"""Integration tests for the discovery pipeline.

Tests normalisation, deduplication, and upsert through Phase 2's
service layer.  Uses the test DB session from conftest.
"""

from __future__ import annotations

import pytest

from core.discovery.base import DiscoverySource, RawOpportunity
from core.discovery.pipeline import run_discovery_with_session
from core.repositories import opportunity_repo, status_history_repo
from core.status import ReliabilityTier


# ── Fake sources for testing ─────────────────────────────────────────────


class FakeStableSource(DiscoverySource):
    """A test source that returns canned RawOpportunity objects."""

    name = "fake_stable"
    tier = ReliabilityTier.STABLE

    def __init__(self, opportunities: list[RawOpportunity]):
        self._opps = opportunities

    def discover(self) -> list[RawOpportunity]:
        return self._opps


class FakeDiscoveryOnlySource(DiscoverySource):
    name = "fake_discovery_only"
    tier = ReliabilityTier.DISCOVERY_ONLY

    def __init__(self, opportunities: list[RawOpportunity]):
        self._opps = opportunities

    def discover(self) -> list[RawOpportunity]:
        return self._opps


class FakeErrorSource(DiscoverySource):
    name = "fake_error"
    tier = ReliabilityTier.STABLE

    def discover(self) -> list[RawOpportunity]:
        raise RuntimeError("Simulated fetch failure")


# ── Test data ────────────────────────────────────────────────────────────

GREENHOUSE_JOB = RawOpportunity(
    title="Backend Engineer",
    company="TestCorp",
    url="https://boards.greenhouse.io/testcorp/jobs/123",
    source="greenhouse",
    description="A backend role",
    location="San Francisco, CA",
)

LEVER_JOB = RawOpportunity(
    title="Frontend Engineer",
    company="DemoCorp",
    url="https://jobs.lever.co/democorp/abc",
    source="lever",
    description="A frontend role",
    location="Remote",
    salary_min=80000,
    salary_max=120000,
)

RSS_JOB = RawOpportunity(
    title="DevOps Engineer",
    company="AcmeCorp",
    url="https://acmecorp.com/careers/devops",
    source="rss",
    description="Infrastructure role",
)

GMAIL_JOB = RawOpportunity(
    title="Data Analyst Internship",
    company="Internshala",
    url="https://internshala.com/internship/detail/data-analyst",
    source="gmail_alert",
)

# Same listing from two different sources (same company+title+url)
CROSS_SOURCE_JOB_GH = RawOpportunity(
    title="ML Engineer",
    company="SharedCo",
    url="https://sharedco.com/ml-engineer",
    source="greenhouse",
)
CROSS_SOURCE_JOB_RSS = RawOpportunity(
    title="ML Engineer",
    company="SharedCo",
    url="https://sharedco.com/ml-engineer",
    source="rss",
)


class TestPipelineUpsert:
    """Verify that pipeline correctly upserts through the service layer."""

    def test_single_opportunity_lands_in_db(self, db_session):
        source = FakeStableSource([GREENHOUSE_JOB])
        result = run_discovery_with_session(source, db_session)

        assert result.total_fetched == 1
        assert result.new == 1
        assert result.errors == 0

        # Verify it's in the DB
        opps = opportunity_repo.list_opportunities(db_session)
        assert any(o.title == "Backend Engineer" for o in opps)

    def test_opportunity_has_correct_status(self, db_session):
        source = FakeStableSource([GREENHOUSE_JOB])
        run_discovery_with_session(source, db_session)

        opps = opportunity_repo.list_opportunities(db_session, status="discovered")
        assert any(o.title == "Backend Engineer" for o in opps)

    def test_opportunity_has_correct_tier(self, db_session):
        source = FakeStableSource([GREENHOUSE_JOB])
        run_discovery_with_session(source, db_session)

        opps = opportunity_repo.list_opportunities(db_session)
        match = [o for o in opps if o.title == "Backend Engineer"]
        assert match[0].reliability_tier == "stable"

    def test_discovery_only_tier_tagged(self, db_session):
        source = FakeDiscoveryOnlySource([GMAIL_JOB])
        run_discovery_with_session(source, db_session)

        opps = opportunity_repo.list_opportunities(db_session)
        match = [o for o in opps if o.title == "Data Analyst Internship"]
        assert match[0].reliability_tier == "discovery_only"

    def test_multiple_opportunities(self, db_session):
        source = FakeStableSource([GREENHOUSE_JOB, LEVER_JOB, RSS_JOB])
        result = run_discovery_with_session(source, db_session)

        assert result.total_fetched == 3
        assert result.new == 3

    def test_each_new_opp_has_status_history(self, db_session):
        source = FakeStableSource([GREENHOUSE_JOB])
        run_discovery_with_session(source, db_session)

        opps = opportunity_repo.list_opportunities(db_session)
        opp = [o for o in opps if o.title == "Backend Engineer"][0]
        history = status_history_repo.get_history(db_session, opp.id)
        assert len(history) == 1
        assert history[0].old_status is None
        assert history[0].new_status == "discovered"


class TestPipelineDedup:
    """Verify deduplication: same listing from any source = one DB row."""

    def test_duplicate_from_same_source(self, db_session):
        """Running the same source twice doesn't create duplicates."""
        source = FakeStableSource([GREENHOUSE_JOB])
        r1 = run_discovery_with_session(source, db_session)
        r2 = run_discovery_with_session(source, db_session)

        assert r1.new == 1
        assert r2.new == 0
        assert r2.updated == 1

        opps = opportunity_repo.list_opportunities(db_session)
        backend = [o for o in opps if o.title == "Backend Engineer"]
        assert len(backend) == 1

    def test_cross_source_dedup(self, db_session):
        """Same listing from Greenhouse and RSS → exactly one row."""
        gh_source = FakeStableSource([CROSS_SOURCE_JOB_GH])
        rss_source = FakeStableSource([CROSS_SOURCE_JOB_RSS])

        r1 = run_discovery_with_session(gh_source, db_session)
        r2 = run_discovery_with_session(rss_source, db_session)

        assert r1.new == 1
        assert r2.new == 0  # deduped!
        assert r2.updated == 1

        opps = opportunity_repo.list_opportunities(db_session)
        ml = [o for o in opps if o.title == "ML Engineer"]
        assert len(ml) == 1

    def test_dedup_hash_case_insensitive(self, db_session):
        """Company/title casing differences still dedup."""
        job_upper = RawOpportunity(
            title="ML ENGINEER", company="SHAREDCO",
            url="https://sharedco.com/ml-engineer", source="gh",
        )
        job_lower = RawOpportunity(
            title="ml engineer", company="sharedco",
            url="https://sharedco.com/ml-engineer", source="rss",
        )
        s1 = FakeStableSource([job_upper])
        s2 = FakeStableSource([job_lower])

        run_discovery_with_session(s1, db_session)
        run_discovery_with_session(s2, db_session)

        opps = opportunity_repo.list_opportunities(db_session)
        ml = [o for o in opps if "ml engineer" in o.title.lower()]
        assert len(ml) == 1


class TestPipelineErrorHandling:
    """Source errors are contained, not propagated."""

    def test_source_error_returns_error_result(self, db_session):
        source = FakeErrorSource()
        result = run_discovery_with_session(source, db_session)
        assert result.errors == 1
        assert result.total_fetched == 0

    def test_empty_source_returns_zero_counts(self, db_session):
        source = FakeStableSource([])
        result = run_discovery_with_session(source, db_session)
        assert result.total_fetched == 0
        assert result.new == 0
