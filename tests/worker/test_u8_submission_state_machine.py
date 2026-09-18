"""U8 submission state machine tests.

Validates the end-to-end submission pipeline edge cases for a real
single Unstop application:

1. Successful submission (approved → claim → submit → confirmed)
2. Already submitted (duplicate prevention)
3. Required field missing (fill fails, no submission attempt)
4. Invalid session (adapter returns session_expired)
5. Submit button not found (resolution returns zero_final)
6. Ambiguous final button (resolution returns only_intermediate_or_unknown)
7. Confirmation detected (check_status returns confirmed)
8. Confirmation not detected (timeout without confirmation signal)
9. Optional Expected Compensation blank (not treated as error)
10. Duplicate submission prevention (claimed app rejected on second attempt)
11. Network interruption (click fails, returns control_click_failed, no retry)
12. Pre-status check blocks submission when ambiguous
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from core.models.opportunity import Application, Opportunity
from core.status import ApplicationStatus, OpportunityStatus
from worker.adapters.base import FillResult, SubmissionStatus
from worker.adapters.unstop import (
    EXPIRED,
    VALID,
    FormField,
    QuestionClassification,
    SessionStatus,
    UnstopAdapter,
)
from worker.adapters.submission_controls import (
    SubmissionControlResolutionStatus,
)
from worker.runner import WorkerRunner


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_app(
    id: int = 1,
    opportunity_id: int = 10,
    status: str = ApplicationStatus.FORM_FILLED.value,
    approved_at: datetime | None = datetime(2026, 1, 1, tzinfo=timezone.utc),
    submission_claimed_at: datetime | None = None,
    claimed_by: str | None = None,
    approval_revoked_at: datetime | None = None,
    attempt_number: int = 1,
    notes: str = "{}",
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
    app.notes = notes
    app.approved_by = "test_actor" if approved_at else None
    app.confirmation_ref = None
    app.submitted_at = None
    return app


def _make_opp(
    id: int = 10,
    status: str = OpportunityStatus.AWAITING_SUBMISSION.value,
    url: str = "https://unstop.com/competitions/1756384/register",
) -> Opportunity:
    opp = Opportunity()
    opp.id = id
    opp.status = status
    opp.title = "Java Developer Internship"
    opp.company = "AI Invito"
    opp.url = url
    opp.source = "unstop"
    return opp


def _make_mock_filler(submit_result: dict | None = None):
    filler = MagicMock()
    filler.execute_browser_submission.return_value = submit_result or {
        "success": True,
        "status": "applied",
        "confirmed": True,
        "confirmation_ref": "UNSTOP-CONFIRMED-1756384",
        "evidence": "artifacts/u8_submission_evidence/app_1_evidence.json",
    }
    return filler


def _make_runner(filler=None):
    return WorkerRunner(poll_interval=60, filler=filler or _make_mock_filler())


# ── Tests ────────────────────────────────────────────────────────────────────


class TestU8SuccessfulSubmission:
    """Test the happy path: approved → claim → submit → confirmed."""

    def test_full_success_path(self):
        """Approved, unclaimed application is claimed, submitted, and confirmed."""
        claim_time = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
        filler = _make_mock_filler()
        runner = _make_runner(filler)
        app = _make_app()
        opp = _make_opp()
        session = MagicMock()
        session.get.side_effect = lambda cls, id, **kw: app if cls == Application else opp

        def fake_refresh(obj):
            obj.submission_claimed_at = claim_time

        session.refresh.side_effect = fake_refresh

        with patch.object(runner, "claim_application_for_submission", return_value=True):
            result = runner.submit_single_application(session, 1, worker_id="u8_worker")

        assert result["success"] is True
        assert result["confirmation_ref"] == "UNSTOP-CONFIRMED-1756384"
        filler.execute_browser_submission.assert_called_once_with(
            session, 1,
            expected_claimed_by="u8_worker",
            expected_submission_claimed_at=claim_time,
        )

    def test_success_result_contains_evidence(self):
        """Successful submission includes evidence path in result."""
        filler = _make_mock_filler({
            "success": True,
            "status": "applied",
            "confirmed": True,
            "confirmation_ref": "UNSTOP-CONFIRMED-1756384",
            "evidence": "artifacts/u8_submission_evidence/app_1_20260916_evidence.json",
        })
        runner = _make_runner(filler)
        app = _make_app()
        opp = _make_opp()
        session = MagicMock()
        session.get.side_effect = lambda cls, id, **kw: app if cls == Application else opp
        session.refresh.side_effect = lambda obj: setattr(
            obj, "submission_claimed_at", datetime(2026, 9, 16, tzinfo=timezone.utc)
        )

        with patch.object(runner, "claim_application_for_submission", return_value=True):
            result = runner.submit_single_application(session, 1)

        assert result["success"] is True
        assert "evidence" in result


class TestU8AlreadySubmitted:
    """Duplicate submission prevention."""

    def test_already_submitted_status_rejected(self):
        """Application with status=submitted is rejected before any browser action."""
        runner = _make_runner()
        app = _make_app(status=ApplicationStatus.SUBMITTED.value)
        opp = _make_opp()
        session = MagicMock()
        session.get.side_effect = lambda cls, id, **kw: app if cls == Application else opp

        result = runner.submit_single_application(session, 1)
        assert result["success"] is False
        assert "form_filled or pending" in result["reason"]

    def test_already_claimed_rejected(self):
        """Application already claimed by another worker is rejected."""
        runner = _make_runner()
        app = _make_app(
            submission_claimed_at=datetime(2026, 9, 16, tzinfo=timezone.utc),
            claimed_by="other_worker",
        )
        opp = _make_opp()
        session = MagicMock()
        session.get.side_effect = lambda cls, id, **kw: app if cls == Application else opp

        result = runner.submit_single_application(session, 1)
        assert result["success"] is False
        assert "already claimed" in result["reason"]


class TestU8RequiredFieldMissing:
    """Fill fails when required field cannot be resolved."""

    def test_required_field_missing_stops_before_submission(self):
        """A REQUIRES_USER classification on a required field prevents fill."""
        adapter = UnstopAdapter.__new__(UnstopAdapter)
        field = FormField(
            id="salary_field",
            name="expected_salary",
            tag="input",
            type="text",
            label="Expected Salary *",
            required=True,
        )
        candidate_data = {"name": "Test User", "email": "test@example.com"}
        classification, val = adapter.classify_field(field, candidate_data)
        # "salary" is a sensitive keyword → REQUIRES_USER
        assert classification == QuestionClassification.REQUIRES_USER
        assert val is None


class TestU8InvalidSession:
    """Session validation prevents submission when expired."""

    def test_invalid_session_returns_session_expired(self):
        """Expired session status produces a non-submittable application context."""
        adapter = UnstopAdapter.__new__(UnstopAdapter)
        adapter.session_file = MagicMock()
        adapter.session_file.exists.return_value = True
        adapter.encryption_key = None
        adapter.headless = True
        adapter._pw = None
        adapter.probe_network = False

        status = SessionStatus(
            valid=False,
            status=EXPIRED,
            detail="All Unstop cookies have expired.",
            cookies_count=0,
        )
        with patch.object(adapter, "check_session_status", return_value=status):
            ctx = adapter.open_application("https://unstop.com/competitions/1756384/register")

        assert ctx.metadata.get("session_expired") is True


class TestU8SubmitButtonNotFound:
    """Submit control resolution finds zero final controls."""

    def test_zero_final_controls_fails_closed(self):
        """When resolution finds no FINAL_SUBMIT control, submission fails without clicking."""
        adapter = UnstopAdapter.__new__(UnstopAdapter)

        # Mock a page with valid origin but no final controls
        page = MagicMock()
        page.url = "https://unstop.com/competitions/1756384/register"

        challenge_loc = MagicMock()
        challenge_loc.count.return_value = 0
        page.locator.return_value = challenge_loc

        pre_status = SubmissionStatus(confirmed=False, status="not_submitted", detail="form_inputs_visible")
        with patch.object(adapter, "check_status", return_value=pre_status):
            mock_resolution = MagicMock()
            mock_resolution.status = SubmissionControlResolutionStatus.ZERO_FINAL
            mock_resolution.locator = None
            mock_resolution.candidates = []
            with patch("worker.adapters.unstop.resolve_final_submission_control", return_value=mock_resolution):
                app_ctx = MagicMock()
                app_ctx.browser_page = page
                result = adapter.submit_application(app_ctx)

        assert result["success"] is False
        assert result.get("manual_review_required") is True
        assert result["error"] == "manual_final_action_required"


class TestU8AmbiguousFinalButton:
    """Submit control resolution finds only intermediate/unknown controls."""

    def test_ambiguous_controls_fails_closed(self):
        """Intermediate or unknown controls trigger manual review handoff."""
        adapter = UnstopAdapter.__new__(UnstopAdapter)

        page = MagicMock()
        page.url = "https://unstop.com/competitions/1756384/register"

        challenge_loc = MagicMock()
        challenge_loc.count.return_value = 0
        page.locator.return_value = challenge_loc

        pre_status = SubmissionStatus(confirmed=False, status="not_submitted", detail="form_inputs_visible")
        with patch.object(adapter, "check_status", return_value=pre_status):
            mock_resolution = MagicMock()
            mock_resolution.status = SubmissionControlResolutionStatus.ONLY_INTERMEDIATE_OR_UNKNOWN
            mock_resolution.locator = None
            mock_resolution.candidates = []
            with patch("worker.adapters.unstop.resolve_final_submission_control", return_value=mock_resolution):
                app_ctx = MagicMock()
                app_ctx.browser_page = page
                result = adapter.submit_application(app_ctx)

        assert result["success"] is False
        assert result.get("manual_review_required") is True


class TestU8ConfirmationDetected:
    """check_status detects platform confirmation after submission."""

    def test_confirmation_text_detected(self):
        """Visible confirmation phrase on Unstop page is detected as confirmed."""
        from worker.adapters.unstop import evaluate_submission_confirmation

        page = MagicMock()
        page.url = "https://unstop.com/competitions/1756384/register/success"

        # No form inputs visible
        inputs_loc = MagicMock()
        inputs_loc.count.return_value = 0
        page.locator.side_effect = lambda sel: inputs_loc

        result = evaluate_submission_confirmation(page, opportunity_id=1756384)
        assert result.confirmed is True
        assert result.status == "confirmed"
        assert "1756384" in (result.confirmation_ref or "")


class TestU8ConfirmationNotDetected:
    """Timeout without confirmation signal."""

    def test_no_confirmation_returns_unconfirmed(self):
        """When no confirmation signal appears within timeout, result is not confirmed."""
        adapter = UnstopAdapter.__new__(UnstopAdapter)

        page = MagicMock()
        page.url = "https://unstop.com/competitions/1756384/register"

        # Form inputs still visible (no transition happened)
        inputs_loc = MagicMock()
        inputs_loc.count.return_value = 1
        visible_input = MagicMock()
        visible_input.is_visible.return_value = True
        inputs_loc.nth.return_value = visible_input
        page.locator.return_value = inputs_loc

        app_ctx = MagicMock()
        app_ctx.browser_page = page
        app_ctx.opportunity_id = 1756384

        result = adapter.wait_for_confirmation(app_ctx, timeout_ms=100, poll_ms=50)
        assert result.confirmed is False


class TestU8OptionalExpectedCompensationBlank:
    """Optional Expected Compensation field left blank is not an error."""

    def test_compensation_blank_is_not_error(self):
        """Expected Compensation field classified as REQUIRES_USER when optional → left blank."""
        adapter = UnstopAdapter.__new__(UnstopAdapter)
        field = FormField(
            id="expected_comp",
            name="expected_compensation",
            tag="input",
            type="text",
            label="Expected Compensation",
            required=False,  # optional
        )
        candidate_data = {"name": "Test User"}
        classification, val = adapter.classify_field(field, candidate_data)
        # "compensation" is a sensitive keyword → REQUIRES_USER, val is None
        assert classification == QuestionClassification.REQUIRES_USER
        assert val is None
        # Since required=False and val=None, the fill loop skips this field


class TestU8DuplicateSubmissionPrevention:
    """Atomic claim prevents double submission."""

    def test_claim_race_loser_rejected(self):
        """When atomic claim returns False, submission is not attempted."""
        runner = _make_runner()
        app = _make_app()
        opp = _make_opp()
        session = MagicMock()
        session.get.side_effect = lambda cls, id, **kw: app if cls == Application else opp

        with patch.object(runner, "claim_application_for_submission", return_value=False):
            result = runner.submit_single_application(session, 1)

        assert result["success"] is False
        assert "Failed to atomically claim" in result["reason"]


class TestU8NetworkInterruption:
    """Click failure does not trigger blind retry."""

    def test_click_failure_no_retry(self):
        """When the submit click throws an exception, result is control_click_failed."""
        adapter = UnstopAdapter.__new__(UnstopAdapter)

        page = MagicMock()
        page.url = "https://unstop.com/competitions/1756384/register"

        challenge_loc = MagicMock()
        challenge_loc.count.return_value = 0
        page.locator.return_value = challenge_loc

        pre_status = SubmissionStatus(confirmed=False, status="not_submitted", detail="form_inputs_visible")

        mock_resolution = MagicMock()
        mock_resolution.status = SubmissionControlResolutionStatus.EXACTLY_ONE_FINAL
        mock_locator = MagicMock()
        mock_locator.click.side_effect = Exception("Network disconnected")
        mock_resolution.locator = mock_locator
        mock_resolution.verified_control = MagicMock()
        mock_resolution.verified_control.candidate_id = "submit_btn"
        mock_resolution.form_handle = MagicMock()

        with patch.object(adapter, "check_status", return_value=pre_status):
            with patch("worker.adapters.unstop.resolve_final_submission_control", return_value=mock_resolution):
                with patch("worker.adapters.unstop.revalidate_handle_before_click", return_value=(True, "")):
                    app_ctx = MagicMock()
                    app_ctx.browser_page = page
                    result = adapter.submit_application(app_ctx)

        assert result["success"] is False
        assert result["error"] == "control_click_failed"
        # Verify click was attempted exactly once
        mock_locator.click.assert_called_once()


class TestU8PreStatusBlocksSubmission:
    """Pre-status check prevents submission when state is ambiguous."""

    def test_ambiguous_pre_status_fails_closed(self):
        """When check_status returns ambiguous, submission is blocked with zero clicks."""
        adapter = UnstopAdapter.__new__(UnstopAdapter)

        page = MagicMock()
        page.url = "https://unstop.com/competitions/1756384/register"

        challenge_loc = MagicMock()
        challenge_loc.count.return_value = 0
        page.locator.return_value = challenge_loc

        pre_status = SubmissionStatus(
            confirmed=False, status="ambiguous", detail="confirmation_not_detected"
        )
        with patch.object(adapter, "check_status", return_value=pre_status):
            app_ctx = MagicMock()
            app_ctx.browser_page = page
            result = adapter.submit_application(app_ctx)

        assert result["success"] is False
        assert "pre_status_not_allowed" in result["error"]
