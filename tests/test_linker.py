"""Tests for the application linker (Phase 7)."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from core.messaging.linker import LinkResult, link_message_to_application
from core.models.opportunity import Application, Opportunity
from core.status import OpportunityStatus


def _create_app(
    db: Session,
    company: str,
    title: str = "Software Engineer",
    url: str | None = None,
    status: str = OpportunityStatus.SUBMITTED.value,
) -> tuple[Opportunity, Application]:
    """Helper to create an opportunity + application."""
    opp = Opportunity(
        dedup_hash=f"link_hash_{company}_{title}_{id(company)}",
        title=title,
        company=company,
        url=url,
        status=status,
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


class TestThreadBasedLinking:
    def test_inherits_from_previous_thread(self, db_session: Session):
        _, app = _create_app(db_session, "Acme Corp")
        thread_links = {"thread_abc": app.id}

        result = link_message_to_application(
            sender_domain="acme.com",
            subject="Re: Your application",
            body_preview="Follow up",
            thread_id="thread_abc",
            session=db_session,
            existing_thread_links=thread_links,
        )
        assert result.application_id == app.id
        assert result.confidence == "high"

    def test_no_thread_match(self, db_session: Session):
        _create_app(db_session, "Acme Corp")

        result = link_message_to_application(
            sender_domain="unknown.com",
            subject="Hello",
            body_preview="",
            thread_id="thread_xyz",
            session=db_session,
            existing_thread_links={"thread_abc": 999},
        )
        assert result.confidence in ("none", "low")


class TestDomainBasedLinking:
    def test_single_domain_match(self, db_session: Session):
        _, app = _create_app(
            db_session, "Acme Corp", url="https://www.acme.com/careers/123"
        )

        result = link_message_to_application(
            sender_domain="acme.com",
            subject="Interview",
            body_preview="",
            thread_id=None,
            session=db_session,
        )
        assert result.application_id == app.id
        assert result.confidence == "high"

    def test_no_domain_match(self, db_session: Session):
        _create_app(db_session, "Acme Corp")

        result = link_message_to_application(
            sender_domain="random.com",
            subject="Sale today!",
            body_preview="",
            thread_id=None,
            session=db_session,
        )
        assert result.application_id is None
        assert result.confidence == "none"


class TestSubjectBodyCrossReference:
    def test_company_name_in_subject(self, db_session: Session):
        _, app = _create_app(db_session, "Google", title="SRE Intern")

        result = link_message_to_application(
            sender_domain="google.com",
            subject="Re: Your Google application",
            body_preview="",
            thread_id=None,
            session=db_session,
        )
        assert result.application_id == app.id
        assert result.confidence == "high"

    def test_job_title_in_body(self, db_session: Session):
        _, app = _create_app(db_session, "StartupXYZ", title="Frontend Developer")

        result = link_message_to_application(
            sender_domain="startupxyz.com",
            subject="Update",
            body_preview="Regarding the Frontend Developer position you applied for",
            thread_id=None,
            session=db_session,
        )
        assert result.application_id == app.id
        assert result.confidence == "high"


class TestMultipleMatchFailClosed:
    def test_multiple_matches_refuses_to_guess(self, db_session: Session):
        """When multiple applications match, don't guess — fail closed."""
        _create_app(db_session, "Acme Corp", title="Backend Dev", url="https://acme.com/1")
        _create_app(db_session, "Acme Corp", title="Frontend Dev", url="https://acme.com/2")

        result = link_message_to_application(
            sender_domain="acme.com",
            subject="Re: Your Acme Corp application",
            body_preview="",
            thread_id=None,
            session=db_session,
        )
        assert result.application_id is None
        assert result.confidence == "low"
        assert "Ambiguous" in result.reason


class TestNoActiveApplications:
    def test_no_apps_returns_none(self, db_session: Session):
        result = link_message_to_application(
            sender_domain="acme.com",
            subject="Interview",
            body_preview="",
            thread_id=None,
            session=db_session,
        )
        assert result.application_id is None
        assert result.confidence == "none"
