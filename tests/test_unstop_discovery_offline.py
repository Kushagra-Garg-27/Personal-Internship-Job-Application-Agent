"""Offline unit tests for Unstop discovery adapter and session status.

Uses respx/mocks to test:
- UnstopDiscoverySource API querying and mapping
- UnstopAdapter session verification (missing, corrupted, expired, valid)
- RawOpportunity normalization and missing field safety
- Pipeline ingestion and deduplication
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import pytest
import respx
import httpx

from core.discovery.base import RawOpportunity
from core.discovery.pipeline import run_discovery_with_session
from core.discovery.sources.unstop import UNSTOP_SEARCH_API, UnstopDiscoverySource
from core.models.opportunity import Opportunity
from core.services import opportunity_service
from core.status import OpportunityStatus, ReliabilityTier
from worker.adapters.unstop import SessionStatus, UnstopAdapter
from worker.security.storage import save_encrypted_storage_state


SAMPLE_UNSTOP_API_RESPONSE = {
    "data": {
        "current_page": 1,
        "data": [
            {
                "id": 1752428,
                "title": "Software Engineer Intern",
                "organisation": {
                    "id": 101,
                    "name": "Acme Innovations",
                },
                "seo_url": "https://unstop.com/jobs/software-engineer-intern-acme-1752428",
                "locations": ["Bangalore", "Remote"],
                "regnRequirements": {
                    "start_regn_dt": "2026-09-10T00:00:00+05:30",
                    "end_regn_dt": "2026-09-25T23:59:59+05:30",
                    "remain_days": "15 days left",
                },
                "details": "We are seeking a high-performing backend intern.",
                "required_skills": [{"name": "Python"}, {"name": "FastAPI"}],
                "filters": [{"name": "Students"}],
            },
            {
                "id": 1752429,
                "title": "Data Analyst",
                "organisation": {
                    "id": 102,
                    "name": "DataCorp",
                },
                "seo_url": "/jobs/data-analyst-datacorp-1752429",  # relative URL test
                "locations": ["Delhi"],
                "regnRequirements": {
                    "start_regn_dt": None,
                    "end_regn_dt": None,
                },
                "details": None,
                "required_skills": [],
                "filters": [],
            },
        ],
        "total": 2,
    }
}


class TestUnstopDiscoverySourceOffline:
    """Offline unit tests for core/discovery/sources/unstop.py."""

    @respx.mock
    def test_discover_fetches_and_maps_successfully(self):
        respx.get(UNSTOP_SEARCH_API).respond(
            status_code=200,
            json=SAMPLE_UNSTOP_API_RESPONSE,
        )

        source = UnstopDiscoverySource(limit=5)
        raw_opps = source.discover()

        assert len(raw_opps) == 2
        opp1 = raw_opps[0]
        assert opp1.title == "Software Engineer Intern"
        assert opp1.company == "Acme Innovations"
        assert opp1.url == "https://unstop.com/jobs/software-engineer-intern-acme-1752428"
        assert opp1.source == "unstop"
        assert opp1.location == "Bangalore; Remote"
        assert "Python" in opp1.metadata["skills"]
        assert opp1.metadata["platform"] == "unstop"
        assert opp1.metadata["external_id"] == "1752428"

        opp2 = raw_opps[1]
        assert opp2.title == "Data Analyst"
        assert opp2.company == "DataCorp"
        assert opp2.url == "https://unstop.com/jobs/data-analyst-datacorp-1752429"
        assert opp2.location == "Delhi"

    @respx.mock
    def test_discover_http_error_fails_safely(self):
        respx.get(UNSTOP_SEARCH_API).respond(status_code=500)

        source = UnstopDiscoverySource()
        raw_opps = source.discover()
        assert raw_opps == []

    @respx.mock
    def test_pipeline_ingestion_and_deduplication(self, db_session):
        respx.get(UNSTOP_SEARCH_API).respond(
            status_code=200,
            json=SAMPLE_UNSTOP_API_RESPONSE,
        )

        source = UnstopDiscoverySource(limit=5)

        # 1st run: 2 new opportunities inserted
        result1 = run_discovery_with_session(source, db_session)
        assert result1.total_fetched == 2
        assert result1.new == 2
        assert result1.updated == 0

        # Query database to confirm persisted records
        opps = db_session.query(Opportunity).filter_by(source="unstop").all()
        assert len(opps) == 2
        for o in opps:
            assert o.status == OpportunityStatus.DISCOVERED.value
            assert o.reliability_tier == ReliabilityTier.EXPERIMENTAL.value

        # 2nd run: identical listings -> 0 new, 2 updated (deduplication verified!)
        result2 = run_discovery_with_session(source, db_session)
        assert result2.total_fetched == 2
        assert result2.new == 0
        assert result2.updated == 2

        # Still exactly 2 records in database
        opps_after = db_session.query(Opportunity).filter_by(source="unstop").all()
        assert len(opps_after) == 2


class TestUnstopAdapterSessionStatus:
    """Offline tests for UnstopAdapter session verification and fail-closed rules."""

    def test_session_missing_fails_closed(self, tmp_path):
        non_existent_file = tmp_path / "missing_session.enc"
        adapter = UnstopAdapter(session_file=non_existent_file)

        status = adapter.check_session_status()
        assert status.valid is False
        assert status.status == "session_missing"
        assert "not found" in status.detail.lower()

    def test_session_corrupted_fails_closed(self, tmp_path):
        corrupted_file = tmp_path / "corrupt_session.enc"
        corrupted_file.write_bytes(b"garbage-non-fernet-data")

        adapter = UnstopAdapter(session_file=corrupted_file)
        status = adapter.check_session_status()
        assert status.valid is False
        assert status.status == "session_corrupted"

    def test_session_no_unstop_cookies_fails_closed(self, tmp_path):
        dummy_state = {
            "cookies": [
                {"name": "generic_cookie", "value": "123", "domain": ".other.com"}
            ],
            "origins": [],
        }
        fpath = tmp_path / "other_cookies.enc"
        save_encrypted_storage_state(dummy_state, fpath, key="test-key")

        adapter = UnstopAdapter(session_file=fpath, encryption_key="test-key")
        status = adapter.check_session_status()
        assert status.valid is False
        assert status.status == "session_expired"
        assert "no unstop cookies" in status.detail.lower()

    def test_session_valid_cookies_authenticated(self, tmp_path):
        valid_state = {
            "cookies": [
                {"name": "XSRF-TOKEN", "value": "tok_123", "domain": ".unstop.com"},
                {"name": "unstop_user", "value": "usr_456", "domain": "unstop.com"},
            ],
            "origins": [],
        }
        fpath = tmp_path / "valid_unstop.enc"
        save_encrypted_storage_state(valid_state, fpath, key="test-key")

        adapter = UnstopAdapter(session_file=fpath, encryption_key="test-key")
        status = adapter.check_session_status()
        assert status.valid is True
        assert status.status == "authenticated"
        assert status.cookies_count == 2

    def test_discover_with_require_auth_fails_closed_when_session_missing(self, tmp_path):
        adapter = UnstopAdapter(session_file=tmp_path / "missing.enc")
        with pytest.raises(PermissionError, match=r"(?i)Unstop authentication required: session_missing"):
            adapter.discover(require_auth=True)

    def test_normalize_listing_missing_fields_safe(self):
        adapter = UnstopAdapter()
        raw = adapter.normalize_listing({
            "title": "",
            "company": "",
            "url": None,
        })
        assert raw.title == "Unknown Title"
        assert raw.company == "Unknown Company"
        assert raw.url == ""
        assert raw.salary_min is None
        assert raw.salary_max is None
        assert raw.posted_at is None
        assert raw.deadline_at is None
        assert raw.metadata["platform"] == "unstop"
