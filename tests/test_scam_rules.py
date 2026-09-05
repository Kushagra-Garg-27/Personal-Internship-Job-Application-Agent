"""Unit tests for deterministic scam and risk detection rules (Phase 5)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from core.funnel.scam_risk.rules import (
    check_duplicate_content,
    check_free_email,
    check_keyword_blocklist,
    check_whois_domain,
    compute_content_hash,
    evaluate_deterministic_scam_rules,
    normalize_content_text,
)
from core.models.opportunity import Opportunity
from core.repositories import scam_signature_repo


class DummyOpp:
    """Lightweight duck-typed opportunity object for testing rules."""

    def __init__(
        self,
        title: str = "Software Engineer",
        company: str = "Acme Corp",
        description: str | None = None,
        url: str | None = "https://acme.com/jobs/1",
        metadata_json: dict | None = None,
    ):
        self.id = 1
        self.title = title
        self.company = company
        self.description = description
        self.url = url
        self.metadata_json = metadata_json or {}


class TestContentHashing:
    def test_normalize_content_text(self):
        t1 = "  Senior Python   Developer\n\nRemote position  "
        t2 = "senior python developer remote position"
        assert normalize_content_text(t1) == t2

    def test_compute_content_hash_identical_for_whitespace_casing_variations(self):
        t1 = "Urgent hiring! Wire money via Western Union."
        t2 = "   urgent HIRING!   wire money via western   union.  "
        assert compute_content_hash(t1) == compute_content_hash(t2)

    def test_compute_content_hash_different_for_different_text(self):
        h1 = compute_content_hash("Looking for a Python dev.")
        h2 = compute_content_hash("Looking for a Go dev.")
        assert h1 != h2


class TestDuplicateContentRule:
    def test_empty_description_is_clear(self, db_session):
        opp = DummyOpp(description=None)
        verdict, reason = check_duplicate_content(opp, session=db_session)
        assert verdict == "clear"
        assert reason is None

    def test_no_match_in_database_is_clear(self, db_session):
        opp = DummyOpp(description="A legitimate software engineering job opening.")
        verdict, reason = check_duplicate_content(opp, session=db_session)
        assert verdict == "clear"
        assert reason is None

    def test_matches_confirmed_scam_signature_rejects(self, db_session):
        desc = "Pay upfront fee of $200 for equipment reimbursement."
        chash = compute_content_hash(desc)
        scam_signature_repo.create_signature(
            db_session,
            content_hash=chash,
            confirmed_scam=True,
            rule_name="test_rule",
            notes="Known fake check scam",
        )

        opp = DummyOpp(description=desc)
        verdict, reason = check_duplicate_content(opp, session=db_session)
        assert verdict == "reject"
        assert reason is not None
        assert reason["rule"] == "duplicate_content_hash"
        assert reason["content_hash"] == chash

    def test_matches_unconfirmed_signature_is_ambiguous(self, db_session):
        desc = "Borderline job listing text needing review."
        chash = compute_content_hash(desc)
        scam_signature_repo.create_signature(
            db_session,
            content_hash=chash,
            confirmed_scam=False,
            rule_name="user_flagged",
        )

        opp = DummyOpp(description=desc)
        verdict, reason = check_duplicate_content(opp, session=db_session)
        assert verdict == "ambiguous"
        assert reason is not None


class TestKeywordBlocklistRule:
    def test_hard_reject_keywords(self):
        opp = DummyOpp(description="Please wire money before receiving your training kit.")
        verdict, reason = check_keyword_blocklist(opp)
        assert verdict == "reject"
        assert "wire money" in reason["matched"]

    def test_crypto_transfer_reject(self):
        opp = DummyOpp(description="Account executive position: requires daily crypto transfer.")
        verdict, reason = check_keyword_blocklist(opp)
        assert verdict == "reject"
        assert "crypto transfer" in reason["matched"]

    def test_buy_equipment_reimburse_reject(self):
        opp = DummyOpp(description="You must buy your own equipment and we will reimburse via check.")
        verdict, reason = check_keyword_blocklist(opp)
        assert verdict == "reject"

    def test_suspicious_borderline_keyword_is_ambiguous(self):
        opp = DummyOpp(description="Urgent hiring! Immediate start no interview needed. Apply now.")
        verdict, reason = check_keyword_blocklist(opp)
        assert verdict == "ambiguous"
        assert any("no interview" in m for m in reason["matched"])

    def test_normal_tech_job_is_clear(self):
        opp = DummyOpp(
            description="We are looking for a Senior Backend Engineer proficient in Python and FastAPI."
        )
        verdict, reason = check_keyword_blocklist(opp)
        assert verdict == "clear"
        assert reason is None


class TestFreeEmailRecruiterRule:
    def test_corporate_company_with_free_email_rejects(self):
        opp = DummyOpp(
            company="Microsoft Corporation",
            description="Send your resume to hr-microsoft-recruitment@gmail.com for fast processing.",
        )
        verdict, reason = check_free_email(opp)
        assert verdict == "reject"
        assert reason["rule"] == "free_email_recruiter"
        assert "hr-microsoft-recruitment@gmail.com" in reason["emails"]

    def test_non_corporate_with_free_email_is_ambiguous(self):
        opp = DummyOpp(
            company="Small Local Bakery",
            description="Send info to localbakery123@yahoo.com.",
        )
        verdict, reason = check_free_email(opp)
        assert verdict == "ambiguous"
        assert reason["rule"] == "free_email_recruiter"

    def test_official_corporate_email_is_clear(self):
        opp = DummyOpp(
            company="Microsoft Corporation",
            description="Contact careers@microsoft.com for inquiries.",
        )
        verdict, reason = check_free_email(opp)
        assert verdict == "clear"
        assert reason is None

    def test_no_email_in_description_is_clear(self):
        opp = DummyOpp(
            company="Google",
            description="Apply via our careers portal.",
        )
        verdict, reason = check_free_email(opp)
        assert verdict == "clear"
        assert reason is None


class TestWhoisDomainAgeRule:
    def test_established_platform_domain_skipped(self):
        mock_whois = Mock()
        opp = DummyOpp(url="https://www.linkedin.com/jobs/view/12345")
        verdict, reason = check_whois_domain(opp, whois_lookup_fn=mock_whois)
        assert verdict == "clear"
        assert reason is None
        mock_whois.assert_not_called()

    def test_newly_registered_domain_is_ambiguous(self):
        mock_whois = Mock()
        mock_record = Mock()
        # Created 5 days ago
        mock_record.creation_date = datetime.now(timezone.utc) - timedelta(days=5)
        mock_whois.return_value = mock_record

        opp = DummyOpp(url="https://brandnewfakejobs-portal.com/job/10")
        verdict, reason = check_whois_domain(opp, min_age_days=30, whois_lookup_fn=mock_whois)
        assert verdict == "ambiguous"
        assert reason["rule"] == "whois_domain_age"
        assert reason["age_days"] == 5

    def test_established_domain_is_clear(self):
        mock_whois = Mock()
        mock_record = Mock()
        # Created 500 days ago
        mock_record.creation_date = datetime.now(timezone.utc) - timedelta(days=500)
        mock_whois.return_value = mock_record

        opp = DummyOpp(url="https://establishedtech.com/careers/backend")
        verdict, reason = check_whois_domain(opp, min_age_days=30, whois_lookup_fn=mock_whois)
        assert verdict == "clear"
        assert reason is None

    def test_whois_lookup_exception_fails_open_gracefully(self):
        mock_whois = Mock(side_effect=Exception("DNS query timeout"))
        opp = DummyOpp(url="https://somerandomsite.xyz/job/1")
        verdict, reason = check_whois_domain(opp, whois_lookup_fn=mock_whois)
        assert verdict == "clear"
        assert reason is None


class TestEvaluateDeterministicRulesCombined:
    def test_duplicate_confirmed_short_circuits_immediately(self, db_session):
        desc = "Normal description but matches confirmed scam signature."
        chash = compute_content_hash(desc)
        scam_signature_repo.create_signature(
            db_session,
            content_hash=chash,
            confirmed_scam=True,
            notes="Confirmed scam",
        )

        opp = DummyOpp(description=desc)
        verdict, primary, signals = evaluate_deterministic_scam_rules(opp, session=db_session)
        assert verdict == "reject"
        assert primary["rule"] == "duplicate_content_hash"

    def test_clean_job_returns_clear_with_empty_signals(self, db_session):
        opp = DummyOpp(
            company="Tech Corp",
            description="Standard software engineer position. Python experience required.",
            url="https://techcorp.com/jobs/1",
        )
        mock_whois = Mock()
        mock_record = Mock()
        mock_record.creation_date = datetime.now(timezone.utc) - timedelta(days=200)
        mock_whois.return_value = mock_record

        verdict, primary, signals = evaluate_deterministic_scam_rules(
            opp, session=db_session, whois_lookup_fn=mock_whois
        )
        assert verdict == "clear"
        assert primary is None
        assert signals == []
