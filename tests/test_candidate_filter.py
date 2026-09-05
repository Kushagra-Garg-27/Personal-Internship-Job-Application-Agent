"""Tests for the candidate filter (Phase 7)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from core.messaging.candidate_filter import (
    CandidateFilterResult,
    evaluate_candidate_filter,
    extract_domain,
    extract_email_from_sender,
    is_noreply,
)
from core.models.opportunity import Application, Opportunity
from core.status import OpportunityStatus


# ── Utility function tests ───────────────────────────────────────────────


class TestExtractEmail:
    def test_name_angle_format(self):
        assert extract_email_from_sender("Jane <jane@acme.com>") == "jane@acme.com"

    def test_bare_email(self):
        assert extract_email_from_sender("jane@acme.com") == "jane@acme.com"

    def test_uppercase(self):
        assert extract_email_from_sender("Jane <JANE@ACME.COM>") == "jane@acme.com"


class TestExtractDomain:
    def test_normal(self):
        assert extract_domain("jane@acme.com") == "acme.com"

    def test_no_at(self):
        assert extract_domain("noemail") == ""


class TestIsNoreply:
    @pytest.mark.parametrize(
        "email",
        [
            "noreply@example.com",
            "no-reply@example.com",
            "donotreply@example.com",
            "mailer-daemon@example.com",
            "NOREPLY@EXAMPLE.COM",
        ],
    )
    def test_noreply_addresses(self, email):
        assert is_noreply(email) is True

    def test_regular_email(self):
        assert is_noreply("jane@acme.com") is False


# ── Filter evaluation tests ─────────────────────────────────────────────


class TestCandidateFilterNoSession:
    """Tests that don't need a DB session."""

    def test_self_sent_exclusion(self):
        result = evaluate_candidate_filter(
            sender="Me <me@gmail.com>",
            subject="Re: Application",
            user_email="me@gmail.com",
        )
        assert result.is_candidate is False
        assert result.matched_rule == "self_sent"

    def test_noreply_exclusion(self):
        result = evaluate_candidate_filter(
            sender="noreply@company.com",
            subject="Application received",
            user_email="me@gmail.com",
        )
        assert result.is_candidate is False
        assert result.matched_rule == "noreply_sender"

    def test_subject_keyword_match(self):
        result = evaluate_candidate_filter(
            sender="recruiter@company.com",
            subject="Re: Your application for Software Engineer",
            user_email="me@gmail.com",
        )
        assert result.is_candidate is True
        assert result.matched_rule == "subject_keyword_match"

    def test_interview_keyword(self):
        result = evaluate_candidate_filter(
            sender="hr@tech.com",
            subject="Interview invitation",
            user_email="me@gmail.com",
        )
        assert result.is_candidate is True
        assert result.matched_rule == "subject_keyword_match"

    def test_no_match(self):
        result = evaluate_candidate_filter(
            sender="random@shop.com",
            subject="50% off sale today!",
            user_email="me@gmail.com",
        )
        assert result.is_candidate is False
        assert result.matched_rule == "no_match"


class TestCandidateFilterWithSession:
    """Tests that use the DB to check active application domains."""

    def _create_active_application(self, db: Session, company: str, url: str | None = None):
        """Helper to create an opportunity + application in 'submitted' status."""
        opp = Opportunity(
            dedup_hash=f"hash_{company}_{id(company)}",
            title=f"Engineer at {company}",
            company=company,
            url=url,
            status=OpportunityStatus.SUBMITTED.value,
        )
        db.add(opp)
        db.flush()
        app = Application(
            opportunity_id=opp.id,
            status="submitted",
        )
        db.add(app)
        db.flush()
        return opp, app

    def test_sender_domain_match(self, db_session: Session):
        self._create_active_application(
            db_session, "Acme Corp", "https://www.acme.com/careers"
        )
        result = evaluate_candidate_filter(
            sender="recruiter@acme.com",
            subject="Hello",
            user_email="me@gmail.com",
            session=db_session,
        )
        assert result.is_candidate is True
        assert result.matched_rule == "sender_domain_match"

    def test_company_name_in_sender(self, db_session: Session):
        self._create_active_application(db_session, "TechFlow")
        result = evaluate_candidate_filter(
            sender="hiring@techflow.io",
            subject="Hello",
            user_email="me@gmail.com",
            session=db_session,
        )
        assert result.is_candidate is True
        assert "company_name" in result.matched_rule or "domain" in result.matched_rule

    def test_no_match_with_session(self, db_session: Session):
        self._create_active_application(db_session, "Acme Corp")
        result = evaluate_candidate_filter(
            sender="sales@random.com",
            subject="Buy our product",
            user_email="me@gmail.com",
            session=db_session,
        )
        assert result.is_candidate is False
