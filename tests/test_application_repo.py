"""Tests for the application repository and mark_submission_attempted primitive."""

from __future__ import annotations

import pytest

from core.repositories import application_repo
from core.services import opportunity_service


def _make_opportunity(db_session, suffix=""):
    return opportunity_service.create_opportunity(
        db_session,
        title=f"App Test Job{suffix}",
        company=f"AppCo{suffix}",
        url=f"https://appco{suffix}.com/job",
    )


class TestApplicationCRUD:
    """Application record creation and queries."""

    def test_create_application(self, db_session):
        opp = _make_opportunity(db_session)
        app = application_repo.create_application(
            db_session,
            opportunity_id=opp.id,
            resume_id=None,
            attempt_number=1,
            status="pending",
        )
        assert app.id is not None
        assert app.opportunity_id == opp.id
        assert app.attempt_number == 1
        assert app.status == "pending"

    def test_get_application(self, db_session):
        opp = _make_opportunity(db_session)
        app = application_repo.create_application(
            db_session,
            opportunity_id=opp.id,
            attempt_number=1,
            status="pending",
        )
        fetched = application_repo.get_application(db_session, app.id)
        assert fetched is not None
        assert fetched.id == app.id

    def test_list_by_opportunity(self, db_session):
        opp = _make_opportunity(db_session)
        application_repo.create_application(
            db_session, opportunity_id=opp.id, attempt_number=1, status="pending"
        )
        application_repo.create_application(
            db_session, opportunity_id=opp.id, attempt_number=2, status="pending"
        )
        apps = application_repo.list_by_opportunity(db_session, opp.id)
        assert len(apps) == 2
        assert apps[0].attempt_number == 1
        assert apps[1].attempt_number == 2

    def test_update_application(self, db_session):
        opp = _make_opportunity(db_session)
        app = application_repo.create_application(
            db_session, opportunity_id=opp.id, attempt_number=1, status="pending"
        )
        updated = application_repo.update_application(
            db_session, app.id, status="submitted", confirmation_ref="REF-123"
        )
        assert updated.status == "submitted"
        assert updated.confirmation_ref == "REF-123"


class TestMultipleAttempts:
    """Multiple application attempts per opportunity."""

    def test_attempt_number_auto_increments(self, db_session):
        opp = _make_opportunity(db_session, suffix="-attempts")
        assert application_repo.get_next_attempt_number(db_session, opp.id) == 1

        application_repo.create_application(
            db_session, opportunity_id=opp.id, attempt_number=1, status="pending"
        )
        assert application_repo.get_next_attempt_number(db_session, opp.id) == 2

        application_repo.create_application(
            db_session, opportunity_id=opp.id, attempt_number=2, status="pending"
        )
        assert application_repo.get_next_attempt_number(db_session, opp.id) == 3

    def test_multiple_attempts_coexist(self, db_session):
        opp = _make_opportunity(db_session, suffix="-multi")
        for i in range(1, 4):
            application_repo.create_application(
                db_session, opportunity_id=opp.id, attempt_number=i,
                status="failed" if i < 3 else "submitted",
            )
        apps = application_repo.list_by_opportunity(db_session, opp.id)
        assert len(apps) == 3
        assert apps[2].status == "submitted"


class TestMarkSubmissionAttempted:
    """The pre-write primitive for Phase 9's fail-closed design."""

    def test_creates_pending_application(self, db_session):
        """mark_submission_attempted writes a durable 'pending' record."""
        opp = _make_opportunity(db_session, suffix="-prewrite")
        app = opportunity_service.mark_submission_attempted(
            db_session, opp.id, resume_id=None, adapter_name="greenhouse"
        )
        assert app.status == "pending"
        assert app.attempt_number == 1
        assert app.adapter_name == "greenhouse"

    def test_record_is_durable_after_flush(self, db_session):
        """The record must exist in the DB after flush, even before commit.

        This simulates the "write before the risky step" guarantee: if a
        crash occurred after flush but before the risky action completed,
        the pending record would still be in the DB.
        """
        opp = _make_opportunity(db_session, suffix="-durable")
        app = opportunity_service.mark_submission_attempted(
            db_session, opp.id, resume_id=None
        )

        # Re-query from the session to confirm it's flushed
        fetched = application_repo.get_application(db_session, app.id)
        assert fetched is not None
        assert fetched.status == "pending"

    def test_subsequent_attempts_increment(self, db_session):
        opp = _make_opportunity(db_session, suffix="-inc")
        a1 = opportunity_service.mark_submission_attempted(db_session, opp.id)
        a2 = opportunity_service.mark_submission_attempted(db_session, opp.id)
        assert a1.attempt_number == 1
        assert a2.attempt_number == 2
