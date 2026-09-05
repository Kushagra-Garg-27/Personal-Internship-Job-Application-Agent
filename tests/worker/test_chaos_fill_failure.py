"""Chaos test: mid-fill network failure handling (Phase 9).

Simulates a network or platform failure occurring mid-fill.
Confirms the system fails closed:
- Opportunity transitions to MANUAL_APPLICATION_REQUIRED
- Application attempt status is set to "failed" with clean error notes
- Core-side watcher notices and alerts the user via NotificationService
- Does not crash or silently leave the opportunity in an unrecoverable state
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from core.models.opportunity import Application, Opportunity
from core.notifications.worker_watcher import poll_worker_events
from core.services import opportunity_service
from core.status import OpportunityStatus
from worker.adapters.base import FillResult
from worker.adapters.greenhouse import GreenhouseAdapter
from worker.engine.filler import ApplicationFiller


def test_chaos_mid_fill_network_failure_fails_closed(db_session):
    opp = opportunity_service.create_opportunity(
        db_session,
        title="Chaos Test Role",
        company="ChaosCorp",
        url="https://boards.greenhouse.io/chaos/jobs/999",
        source="greenhouse",
        reliability_tier="stable",
    )
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.RECOMMENDED)
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.READY_TO_APPLY)

    filler = ApplicationFiller()

    with patch.object(GreenhouseAdapter, "extract") as mock_extract, \
         patch.object(GreenhouseAdapter, "fill") as mock_fill:

        mock_extract.return_value = MagicMock(
            supports_programmatic_submission=True,
            board_token="chaos",
            job_id="999",
            fields_required=["first_name"],
            custom_questions=[],
        )
        # Simulate network failure mid-fill
        mock_fill.return_value = FillResult(
            success=False,
            status="manual_required",
            error_reason="Connection reset by peer during form field mapping",
        )

        res = filler.process_opportunity(db_session, opp.id)

        assert res["status"] == "manual_required"
        assert "Connection reset by peer" in res["reason"]

    # Verify database state
    db_session.refresh(opp)
    assert opp.status == OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value

    app = db_session.query(Application).filter_by(opportunity_id=opp.id).first()
    assert app is not None
    assert app.status == "failed"
    assert "Connection reset by peer" in app.notes

    # Now verify Core-side watcher picks up this event and alerts user
    with patch("core.notifications.worker_watcher.notification_service.dispatch") as mock_dispatch:
        mock_dispatch.return_value = MagicMock(skipped=False)
        stats = poll_worker_events(db_session)
        assert stats["notified"] == 1

        mock_dispatch.assert_called_once()
        event = mock_dispatch.call_args[0][0]
        assert event.event_type == "application_manual_required"
        assert "ChaosCorp" in event.title
        assert "Connection reset by peer" in event.body
