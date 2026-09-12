"""Tests for the U7 Browser-Tier Submission Orchestrator and Concurrency Protection."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from core.models.opportunity import Application, Opportunity
from core.status import (
    HUMAN_SUBMISSION_APPROVAL_TOKEN,
    ApplicationStatus,
    OpportunityStatus,
)
from worker.engine.filler import ApplicationFiller
from worker.runner import WorkerRunner


def _create_awaiting_app(
    db_session: Session,
    dedup_suffix: str = "1",
    approval_token: str | None = HUMAN_SUBMISSION_APPROVAL_TOKEN,
    status: str = ApplicationStatus.FORM_FILLED.value,
    opp_status: str = OpportunityStatus.AWAITING_SUBMISSION.value,
    submission_claimed: bool = False,
) -> tuple[Opportunity, Application]:
    """Helper to create a test opportunity and application record."""
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

    notes: dict = {"approved_by": "test_user"}
    if approval_token is not None:
        notes["approval_token"] = approval_token
    if submission_claimed:
        notes["submission_claimed"] = True
        notes["claimed_by"] = "test_claim"

    app = Application(
        opportunity_id=opp.id,
        status=status,
        notes=json.dumps(notes),
    )
    db_session.add(app)
    db_session.commit()
    return opp, app


def test_poll_and_submit_queue_finds_and_claims_approved_apps(db_session: Session):
    """WorkerRunner.poll_and_submit_queue claims and executes an approved application."""
    opp, app = _create_awaiting_app(db_session, dedup_suffix="find_approved")

    filler_mock = MagicMock()
    filler_mock.execute_browser_submission.return_value = {"success": True}

    runner = WorkerRunner(filler=filler_mock)
    results = runner.poll_and_submit_queue(db_session, worker_id="worker_test")

    assert len(results) == 1
    assert results[0] == {"success": True}
    filler_mock.execute_browser_submission.assert_called_once_with(
        db_session, app.id, HUMAN_SUBMISSION_APPROVAL_TOKEN
    )

    db_session.refresh(app)
    notes = json.loads(app.notes)
    assert notes["submission_claimed"] is True
    assert notes["claimed_by"] == "worker_test"


def test_poll_and_submit_queue_ignores_unapproved(db_session: Session):
    """Applications without a valid approval token are skipped and never claimed."""
    opp, app = _create_awaiting_app(
        db_session, dedup_suffix="unapproved", approval_token=None
    )

    filler_mock = MagicMock()
    runner = WorkerRunner(filler=filler_mock)

    results = runner.poll_and_submit_queue(db_session)

    assert len(results) == 0
    filler_mock.execute_browser_submission.assert_not_called()

    db_session.refresh(app)
    notes = json.loads(app.notes)
    assert not notes.get("submission_claimed")


def test_concurrency_two_workers_same_app_claim_exclusion(db_session: Session):
    """Part 8.1: Two workers see the same approved application: Worker A claims, Worker B cannot."""
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
    notes = json.loads(app.notes)
    assert notes["submission_claimed"] is True
    assert notes["claimed_by"] == "worker_a"


def test_concurrency_only_claiming_worker_executes_submission(db_session: Session):
    """Part 8.2: Only the worker that successfully claims can call execute_browser_submission()."""
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
    """Calling execute_browser_submission on an unclaimed app raises RuntimeError."""
    opp, app = _create_awaiting_app(
        db_session, dedup_suffix="unclaimed_exec", submission_claimed=False
    )

    filler = ApplicationFiller()
    with pytest.raises(RuntimeError, match="must be claimed"):
        filler.execute_browser_submission(
            db_session, app.id, HUMAN_SUBMISSION_APPROVAL_TOKEN
        )


def test_second_polling_cycle_skips_active_claim(db_session: Session):
    """Part 8.3: A second polling cycle cannot submit while the first claim is active."""
    opp, app = _create_awaiting_app(
        db_session, dedup_suffix="active_claim", submission_claimed=True
    )

    filler_mock = MagicMock()
    runner = WorkerRunner(filler=filler_mock)

    results = runner.poll_and_submit_queue(db_session)
    assert len(results) == 0
    filler_mock.execute_browser_submission.assert_not_called()


def test_already_submitted_application_skipped(db_session: Session):
    """Part 8.4: Already-submitted applications are skipped and cannot be claimed."""
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
    """Part 8.5: Ambiguous submission transitions to manual_required and is NOT re-submitted."""
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
            result = filler.execute_browser_submission(
                db_session, app.id, HUMAN_SUBMISSION_APPROVAL_TOKEN
            )

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


def test_invalid_or_missing_approval_token_fails_closed(db_session: Session):
    """Part 8.6: Invalid/missing approval token results in no claim and no submission."""
    for bad_token in [None, "", "INVALID_TOKEN", "ready_for_review", "autofilled"]:
        opp, app = _create_awaiting_app(
            db_session,
            dedup_suffix=f"bad_tok_{id(bad_token)}",
            approval_token=bad_token,
        )
        runner = WorkerRunner()
        assert runner.claim_application_for_submission(db_session, app.id) is False


@patch("worker.engine.filler.resolve_adapter")
def test_execute_browser_submission_success(mock_resolve_adapter, db_session: Session):
    """execute_browser_submission correctly coordinates a successful submission with claim."""
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

        result = filler.execute_browser_submission(
            db_session, app.id, HUMAN_SUBMISSION_APPROVAL_TOKEN
        )

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
    """Ambiguous failures safely transition to manual resolution without retrying."""
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

        result = filler.execute_browser_submission(
            db_session, app.id, HUMAN_SUBMISSION_APPROVAL_TOKEN
        )

    assert result["success"] is False
    assert result["status"] == "manual_required"

    db_session.refresh(app)
    db_session.refresh(opp)

    assert app.status == ApplicationStatus.FAILED.value
    assert opp.status == OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value
