"""Tests for the U7 Browser-Tier Submission Orchestrator and Concurrency Protection (M1)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from core.models.opportunity import Application, Opportunity
from core.status import (
    ApplicationStatus,
    OpportunityStatus,
)
from core.tokens import generate_approval_token
from worker.engine.filler import ApplicationFiller
from worker.runner import WorkerRunner


def _create_awaiting_app(
    db_session: Session,
    dedup_suffix: str = "1",
    with_approval: bool = True,
    status: str = ApplicationStatus.FORM_FILLED.value,
    opp_status: str = OpportunityStatus.AWAITING_SUBMISSION.value,
    submission_claimed: bool = False,
) -> tuple[Opportunity, Application]:
    """Helper: create an opportunity + application with M1 column-based approval state."""
    opp = Opportunity(
        dedup_hash=f"hash_browser_test_{dedup_suffix}",
        title="Software Engineer",
        company="Test Corp",
        url="https://unstop.com/test",
        source="unstop",
        status=opp_status,
    )
    db_session.add(opp)
    db_session.commit()

    token = generate_approval_token() if with_approval else None
    # Use naive UTC datetimes — SQLite stores naive, and SQLAlchemy's
    # in-memory evaluator must compare like-for-like.
    expires = (datetime.utcnow() + timedelta(minutes=30)) if with_approval else None
    approved_at = datetime.utcnow() if with_approval else None
    claimed_at = datetime.utcnow() if submission_claimed else None


    app = Application(
        opportunity_id=opp.id,
        status=status,
        notes=json.dumps({"approved_by": "test_user"}),
        # M1 approval columns
        approval_token=token,
        approval_token_expires_at=expires,
        approved_at=approved_at,
        approved_by="test_user" if with_approval else None,
        # M1 claim columns
        submission_claimed_at=claimed_at,
        claimed_by="test_claim" if submission_claimed else None,
    )
    db_session.add(app)
    db_session.commit()
    return opp, app


def test_poll_and_submit_queue_finds_and_claims_approved_apps(db_session: Session):
    """WorkerRunner.poll_and_submit_queue claims and executes an approved application (M1)."""
    opp, app = _create_awaiting_app(db_session, dedup_suffix="find_approved")

    filler_mock = MagicMock()
    filler_mock.execute_browser_submission.return_value = {"success": True}

    runner = WorkerRunner(filler=filler_mock)
    results = runner.poll_and_submit_queue(db_session, worker_id="worker_test")

    assert len(results) == 1
    assert results[0] == {"success": True}
    # M1: no approval_token positional arg — worker reads from DB column
    filler_mock.execute_browser_submission.assert_called_once_with(db_session, app.id)

    db_session.refresh(app)
    # M1: claim stored in column, not notes JSON
    assert app.submission_claimed_at is not None
    assert app.claimed_by == "worker_test"


def test_poll_and_submit_queue_ignores_unapproved(db_session: Session):
    """Applications without a valid approval token are skipped and never claimed (M1)."""
    opp, app = _create_awaiting_app(
        db_session, dedup_suffix="unapproved", with_approval=False
    )

    filler_mock = MagicMock()
    runner = WorkerRunner(filler=filler_mock)

    results = runner.poll_and_submit_queue(db_session)

    assert len(results) == 0
    filler_mock.execute_browser_submission.assert_not_called()

    db_session.refresh(app)
    assert app.submission_claimed_at is None


def test_concurrency_two_workers_same_app_claim_exclusion(db_session: Session):
    """Two workers see the same approved application: Worker A claims, Worker B cannot (M1)."""
    opp, app = _create_awaiting_app(db_session, dedup_suffix="concurrency_claim")

    runner_a = WorkerRunner()
    runner_b = WorkerRunner()

    claimed_a = runner_a.claim_application_for_submission(
        db_session, app.id, worker_id="worker_a"
    )
    claimed_b = runner_b.claim_application_for_submission(
        db_session, app.id, worker_id="worker_b"
    )

    assert claimed_a is True
    assert claimed_b is False

    db_session.refresh(app)
    # M1: claim state in column
    assert app.submission_claimed_at is not None
    assert app.claimed_by == "worker_a"


def test_concurrency_only_claiming_worker_executes_submission(db_session: Session):
    """Only the worker that successfully claims can call execute_browser_submission() (M1)."""
    opp, app = _create_awaiting_app(db_session, dedup_suffix="concurrency_exec")

    filler_mock_a = MagicMock()
    filler_mock_a.execute_browser_submission.return_value = {"success": True}
    filler_mock_b = MagicMock()
    filler_mock_b.execute_browser_submission.return_value = {"success": True}

    runner_a = WorkerRunner(filler=filler_mock_a)
    runner_b = WorkerRunner(filler=filler_mock_b)

    results_a = runner_a.poll_and_submit_queue(db_session, worker_id="worker_a")
    results_b = runner_b.poll_and_submit_queue(db_session, worker_id="worker_b")

    assert len(results_a) == 1
    assert len(results_b) == 0
    filler_mock_a.execute_browser_submission.assert_called_once()
    filler_mock_b.execute_browser_submission.assert_not_called()


def test_execute_browser_submission_requires_active_claim(db_session: Session):
    """Calling execute_browser_submission on an unclaimed app raises RuntimeError (M1)."""
    opp, app = _create_awaiting_app(
        db_session, dedup_suffix="unclaimed_exec", submission_claimed=False
    )

    filler = ApplicationFiller()
    with pytest.raises(RuntimeError, match="must be claimed"):
        filler.execute_browser_submission(db_session, app.id)


def test_second_polling_cycle_skips_active_claim(db_session: Session):
    """A second polling cycle cannot submit while the first claim is active (M1)."""
    opp, app = _create_awaiting_app(
        db_session, dedup_suffix="active_claim", submission_claimed=True
    )

    filler_mock = MagicMock()
    runner = WorkerRunner(filler=filler_mock)

    results = runner.poll_and_submit_queue(db_session)
    assert len(results) == 0
    filler_mock.execute_browser_submission.assert_not_called()


def test_already_submitted_application_skipped(db_session: Session):
    """Already-submitted applications are skipped and cannot be claimed (M1)."""
    opp, app = _create_awaiting_app(
        db_session,
        dedup_suffix="already_sub",
        status=ApplicationStatus.SUBMITTED.value,
        opp_status=OpportunityStatus.APPLIED.value,
    )

    runner = WorkerRunner()
    can_claim = runner.claim_application_for_submission(db_session, app.id)
    assert can_claim is False

    results = runner.poll_and_submit_queue(db_session)
    assert len(results) == 0


def test_ambiguous_submission_not_automatically_eligible(db_session: Session):
    """Ambiguous submission transitions to manual_required and is NOT re-submitted (M1)."""
    opp, app = _create_awaiting_app(db_session, dedup_suffix="ambig_fail")

    runner = WorkerRunner()
    claimed = runner.claim_application_for_submission(db_session, app.id, worker_id="worker_1")
    assert claimed is True

    mock_adapter = MagicMock()
    mock_adapter.adapter_name = "unstop"
    mock_adapter.submit_application.return_value = {
        "success": False,
        "error": "Timeout waiting for confirmation",
    }

    filler = ApplicationFiller()
    with patch("worker.engine.filler.resolve_adapter", return_value=mock_adapter):
        with patch.object(filler, "process_opportunity") as mock_process:
            mock_process.return_value = {"success": True, "app_ctx": MagicMock()}
            result = filler.execute_browser_submission(db_session, app.id)

    assert result["success"] is False
    assert result["status"] == "manual_required"

    db_session.refresh(opp)
    db_session.refresh(app)
    assert opp.status == OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value
    assert app.status == ApplicationStatus.FAILED.value

    # Next cycle must skip it
    results = runner.poll_and_submit_queue(db_session)
    assert len(results) == 0

    # Cannot be claimed again
    assert runner.claim_application_for_submission(db_session, app.id) is False


def test_invalid_or_missing_approval_results_in_no_claim(db_session: Session):
    """M1: Applications with no DB-column approval (no approved_at) are never claimed."""
    for suffix in ["no_token_1", "no_token_2", "no_token_3"]:
        opp, app = _create_awaiting_app(
            db_session,
            dedup_suffix=suffix,
            with_approval=False,
        )
        runner = WorkerRunner()
        assert runner.claim_application_for_submission(db_session, app.id) is False


@patch("worker.engine.filler.resolve_adapter")
def test_execute_browser_submission_success(mock_resolve_adapter, db_session: Session):
    """execute_browser_submission correctly coordinates a successful submission (M1)."""
    opp, app = _create_awaiting_app(
        db_session, dedup_suffix="success_flow", submission_claimed=True
    )

    mock_adapter = MagicMock()
    mock_adapter.adapter_name = "unstop"
    mock_adapter.submit_application.return_value = {
        "success": True,
        "confirmation_ref": "REF-123",
        "clicked_selector": "button.submit",
        "url": "https://unstop.com/success",
    }
    mock_resolve_adapter.return_value = mock_adapter

    filler = ApplicationFiller()
    with patch.object(filler, "process_opportunity") as mock_process:
        mock_process.return_value = {"success": True, "app_ctx": MagicMock()}

        result = filler.execute_browser_submission(db_session, app.id)

    assert result["success"] is True
    assert result["status"] == "applied"

    db_session.refresh(app)
    db_session.refresh(opp)

    assert app.status == ApplicationStatus.SUBMITTED.value
    assert opp.status == OpportunityStatus.APPLIED.value

    notes = json.loads(app.notes)
    assert notes["submission_confirmation"]["confirmed"] is True
    assert notes["submission_confirmation"]["confirmation_ref"] == "REF-123"


@patch("worker.engine.filler.resolve_adapter")
def test_execute_browser_submission_ambiguous_failure(
    mock_resolve_adapter, db_session: Session
):
    """Ambiguous failures safely transition to manual resolution without retrying (M1)."""
    opp, app = _create_awaiting_app(
        db_session, dedup_suffix="ambig_flow", submission_claimed=True
    )

    mock_adapter = MagicMock()
    mock_adapter.adapter_name = "unstop"
    mock_adapter.submit_application.return_value = {
        "success": False,
        "error": "Timeout waiting for confirmation",
    }
    mock_resolve_adapter.return_value = mock_adapter

    filler = ApplicationFiller()
    with patch.object(filler, "process_opportunity") as mock_process:
        mock_process.return_value = {"success": True, "app_ctx": MagicMock()}

        result = filler.execute_browser_submission(db_session, app.id)

    assert result["success"] is False
    assert result["status"] == "manual_required"

    db_session.refresh(app)
    db_session.refresh(opp)

    assert app.status == ApplicationStatus.FAILED.value
    assert opp.status == OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value
