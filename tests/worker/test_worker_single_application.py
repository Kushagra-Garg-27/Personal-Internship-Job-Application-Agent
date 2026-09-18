"""Unit tests for WorkerRunner.submit_single_application() (single-application targeting mode).

Validates that the --application-id worker mode:
- Targets exactly the specified application
- Does not touch other applications in the queue
- Returns clear errors for non-existent, unapproved, or already-claimed applications
- Reuses all existing guards without weakening them
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from core.models.opportunity import Application, Opportunity
from core.status import ApplicationStatus, OpportunityStatus
from worker.runner import WorkerRunner


@pytest.fixture
def mock_filler():
    filler = MagicMock()
    filler.execute_browser_submission.return_value = {
        "success": True,
        "status": "applied",
        "confirmed": True,
        "confirmation_ref": "TEST-CONFIRMED",
    }
    return filler


@pytest.fixture
def runner(mock_filler):
    return WorkerRunner(poll_interval=60, filler=mock_filler)


def _make_app(
    id: int = 1,
    opportunity_id: int = 10,
    status: str = ApplicationStatus.FORM_FILLED.value,
    approved_at: datetime | None = datetime(2026, 1, 1, tzinfo=timezone.utc),
    submission_claimed_at: datetime | None = None,
    claimed_by: str | None = None,
    approval_revoked_at: datetime | None = None,
    attempt_number: int = 1,
) -> Application:
    app = Application()
    app.id = id
    app.opportunity_id = opportunity_id
    app.status = status
    app.approved_at = approved_at
    app.submission_claimed_at = submission_claimed_at
    app.claimed_by = claimed_by
    app.approval_revoked_at = approval_revoked_at
    app.attempt_number = attempt_number
    app.notes = "{}"
    return app


def _make_opp(
    id: int = 10,
    status: str = OpportunityStatus.AWAITING_SUBMISSION.value,
) -> Opportunity:
    opp = Opportunity()
    opp.id = id
    opp.status = status
    opp.title = "Test Opportunity"
    opp.company = "Test Co"
    opp.url = "https://unstop.com/test/123"
    opp.source = "unstop"
    return opp


class TestSubmitSingleApplication:
    """Tests for WorkerRunner.submit_single_application()."""

    def test_not_found(self, runner):
        """Non-existent application ID returns a clear error."""
        session = MagicMock()
        session.get.return_value = None

        result = runner.submit_single_application(session, 999)
        assert result["success"] is False
        assert "not found" in result["reason"]

    def test_unapproved_application_rejected(self, runner):
        """Application without approved_at is rejected."""
        app = _make_app(approved_at=None)
        opp = _make_opp()
        session = MagicMock()
        session.get.side_effect = lambda cls, id, **kw: app if cls == Application else opp

        result = runner.submit_single_application(session, 1)
        assert result["success"] is False
        assert "no approval" in result["reason"]

    def test_already_claimed_rejected(self, runner):
        """Application that is already claimed is rejected."""
        app = _make_app(
            submission_claimed_at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc),
            claimed_by="other_worker",
        )
        opp = _make_opp()
        session = MagicMock()
        session.get.side_effect = lambda cls, id, **kw: app if cls == Application else opp

        result = runner.submit_single_application(session, 1)
        assert result["success"] is False
        assert "already claimed" in result["reason"]

    def test_wrong_status_rejected(self, runner):
        """Application in submitted status is rejected."""
        app = _make_app(status=ApplicationStatus.SUBMITTED.value)
        opp = _make_opp()
        session = MagicMock()
        session.get.side_effect = lambda cls, id, **kw: app if cls == Application else opp

        result = runner.submit_single_application(session, 1)
        assert result["success"] is False
        assert "form_filled or pending" in result["reason"]

    def test_wrong_opp_status_rejected(self, runner):
        """Opportunity not in awaiting_submission is rejected."""
        app = _make_app()
        opp = _make_opp(status=OpportunityStatus.APPLIED.value)
        session = MagicMock()
        session.get.side_effect = lambda cls, id, **kw: app if cls == Application else opp

        result = runner.submit_single_application(session, 1)
        assert result["success"] is False
        assert "awaiting_submission" in result["reason"]

    def test_claim_failure_rejected(self, runner):
        """Atomic claim failure returns a clear error."""
        app = _make_app()
        opp = _make_opp()
        session = MagicMock()
        session.get.side_effect = lambda cls, id, **kw: app if cls == Application else opp

        with patch.object(runner, "claim_application_for_submission", return_value=False):
            result = runner.submit_single_application(session, 1)
        assert result["success"] is False
        assert "Failed to atomically claim" in result["reason"]

    def test_success_path(self, runner, mock_filler):
        """Happy path: approved, unclaimed application is claimed and submitted."""
        claim_time = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        app = _make_app()
        opp = _make_opp()
        session = MagicMock()
        session.get.side_effect = lambda cls, id, **kw: app if cls == Application else opp

        def fake_refresh(obj):
            obj.submission_claimed_at = claim_time

        session.refresh.side_effect = fake_refresh

        with patch.object(runner, "claim_application_for_submission", return_value=True):
            result = runner.submit_single_application(session, 1, worker_id="test_worker")

        assert result["success"] is True
        assert result["confirmation_ref"] == "TEST-CONFIRMED"
        mock_filler.execute_browser_submission.assert_called_once_with(
            session,
            1,
            expected_claimed_by="test_worker",
            expected_submission_claimed_at=claim_time,
        )

    def test_other_applications_not_touched(self, runner, mock_filler):
        """Only the specified application is processed; others are not queried."""
        app1 = _make_app(id=1)
        opp = _make_opp()
        session = MagicMock()

        # session.get should only be called for app 1 and opp 10
        def get_handler(cls, id, **kw):
            if cls == Application:
                assert id == 1, f"Unexpected Application query for id={id}"
                return app1
            return opp

        session.get.side_effect = get_handler

        def fake_refresh(obj):
            obj.submission_claimed_at = datetime(2026, 1, 1, tzinfo=timezone.utc)

        session.refresh.side_effect = fake_refresh

        with patch.object(runner, "claim_application_for_submission", return_value=True):
            result = runner.submit_single_application(session, 1)

        assert result["success"] is True
        # Verify no scalars/execute calls that would scan the full queue
        session.scalars.assert_not_called()
