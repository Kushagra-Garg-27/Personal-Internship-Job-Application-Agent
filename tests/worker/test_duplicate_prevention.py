"""Tests for fail-closed duplicate prevention and ambiguous submit handling (Phase 9).

Per §5.7:
- An ambiguous post-submit timeout MUST trigger a status check against the platform
  before any retry is considered.
- Never blindly retry a submission whose outcome is unknown.
- Retries are only allowed for idempotent read-only actions.
- No duplicate `applications` rows may ever be created.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import httpx
import pytest

from core.models.opportunity import Application, Opportunity
from core.services import opportunity_service
from core.status import HUMAN_SUBMISSION_APPROVAL_TOKEN, OpportunityStatus
from worker.adapters.base import SubmissionStatus
from worker.adapters.greenhouse import GreenhouseAdapter
from worker.engine.filler import ApplicationFiller


@pytest.fixture
def ready_opportunity(db_session):
    opp = opportunity_service.create_opportunity(
        db_session,
        title="Backend Engineer",
        company="Acme Corp",
        url="https://boards.greenhouse.io/acme/jobs/555",
        source="greenhouse",
        reliability_tier="stable",
    )
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.RECOMMENDED)
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.READY_TO_APPLY)
    return opp


def test_ambiguous_timeout_checks_status_before_retry_success(db_session, ready_opportunity):
    filler = ApplicationFiller()

    # Mock Greenhouse extraction and fill
    with patch.object(GreenhouseAdapter, "extract") as mock_extract:
        mock_extract.return_value = MagicMock(
            supports_programmatic_submission=True,
            board_token="acme",
            job_id="555",
            fields_required=["first_name"],
            custom_questions=[],
        )
        res = filler.process_opportunity(db_session, ready_opportunity.id)
        assert res["status"] == "awaiting_submission"

    apps_before = db_session.query(Application).filter_by(opportunity_id=ready_opportunity.id).all()
    assert len(apps_before) == 1
    app_id = apps_before[0].id

    # Simulate submission call encountering an ambiguous timeout
    with patch.object(GreenhouseAdapter, "execute_submission") as mock_sub, \
         patch.object(GreenhouseAdapter, "check_status") as mock_check:

        mock_sub.return_value = {
            "success": False,
            "ambiguous_timeout": True,
            "error": "Timeout during POST request",
        }
        # Platform indicates application WAS received despite client timeout
        mock_check.return_value = SubmissionStatus(
            confirmed=True,
            status="confirmed",
            confirmation_ref="GH-RECV-1234",
            detail="Application found on platform.",
        )

        sub_res = filler.confirm_and_submit(
            db_session, app_id, approval_token=HUMAN_SUBMISSION_APPROVAL_TOKEN
        )

        assert sub_res["success"] is True
        assert sub_res["status"] == "applied"
        mock_check.assert_called_once()  # Status check occurred!

        # Verified opportunity and application state
        db_session.refresh(ready_opportunity)
        assert ready_opportunity.status == OpportunityStatus.APPLIED.value

        apps_after = db_session.query(Application).filter_by(opportunity_id=ready_opportunity.id).all()
        # CRITICAL: Exactly 1 application attempt; NO duplicate rows created!
        assert len(apps_after) == 1
        assert apps_after[0].status == "submitted"
        assert apps_after[0].confirmation_ref == "GH-RECV-1234"


def test_ambiguous_timeout_fails_closed_when_not_confirmed(db_session, ready_opportunity):
    filler = ApplicationFiller()

    with patch.object(GreenhouseAdapter, "extract") as mock_extract:
        mock_extract.return_value = MagicMock(
            supports_programmatic_submission=True,
            board_token="acme",
            job_id="555",
            fields_required=["first_name"],
            custom_questions=[],
        )
        filler.process_opportunity(db_session, ready_opportunity.id)

    app = db_session.query(Application).filter_by(opportunity_id=ready_opportunity.id).first()

    with patch.object(GreenhouseAdapter, "execute_submission") as mock_sub, \
         patch.object(GreenhouseAdapter, "check_status") as mock_check:

        mock_sub.return_value = {
            "success": False,
            "ambiguous_timeout": True,
            "error": "Timeout",
        }
        # Platform does NOT confirm receipt
        mock_check.return_value = SubmissionStatus(
            confirmed=False,
            status="not_submitted",
            detail="No application record found.",
        )

        sub_res = filler.confirm_and_submit(
            db_session, app.id, approval_token=HUMAN_SUBMISSION_APPROVAL_TOKEN
        )

        assert sub_res["success"] is False
        assert sub_res["status"] == "manual_required"

        # Fails closed to manual_application_required rather than blind retrying!
        db_session.refresh(ready_opportunity)
        assert ready_opportunity.status == OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value

        apps_after = db_session.query(Application).filter_by(opportunity_id=ready_opportunity.id).all()
        assert len(apps_after) == 1
        assert apps_after[0].status == "failed"
