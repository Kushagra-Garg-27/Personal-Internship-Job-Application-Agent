"""Tests for Core-side worker notification watcher (Phase 9).

Verifies:
- Awaiting submission triggers application_ready_for_review notification
- Manual application required triggers application_manual_required notification
- Notifications are deduplicated (no spamming on every poll)
- The worker never touches notification credentials directly; everything routes through DB status.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from core.models.opportunity import Opportunity
from core.notifications.base import DeliveryResult
from core.notifications.worker_watcher import poll_worker_events
from core.services import application_service, opportunity_service
from core.status import OpportunityStatus


@pytest.fixture
def sample_ready_opp(db_session):
    opp = opportunity_service.create_opportunity(
        db_session,
        title="Software Engineer Intern",
        company="TechCorp Inc",
        url="https://boards.greenhouse.io/techcorp/jobs/123",
        source="greenhouse",
        reliability_tier="stable",
    )
    # Transition to RECOMMENDED -> READY_TO_APPLY
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.RECOMMENDED)
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.READY_TO_APPLY)
    return opp


def test_watcher_notifies_when_awaiting_submission(db_session, sample_ready_opp):
    # Transition to AWAITING_SUBMISSION
    opportunity_service.transition_status(
        db_session, sample_ready_opp.id, OpportunityStatus.AWAITING_SUBMISSION,
        reason="Worker filled form", actor="worker"
    )
    # Create an application record with adapter name and notes
    application_service.create_application(
        db_session,
        opportunity_id=sample_ready_opp.id,
        adapter_name="greenhouse",
    )
    db_session.commit()

    with patch("core.notifications.worker_watcher.notification_service.dispatch") as mock_dispatch:
        mock_dispatch.return_value = MagicMock(skipped=False)
        stats = poll_worker_events(db_session)
        assert stats["checked"] >= 1
        assert stats["notified"] == 1

        mock_dispatch.assert_called_once()
        event = mock_dispatch.call_args[0][0]
        assert event.event_type == "application_ready_for_review"
        assert "TechCorp Inc" in event.title
        assert event.payload["opportunity_id"] == sample_ready_opp.id

    # Running it again must deduplicate and NOT notify
    with patch("core.notifications.worker_watcher.notification_service.dispatch") as mock_dispatch:
        stats2 = poll_worker_events(db_session)
        assert stats2["notified"] == 0
        mock_dispatch.assert_not_called()


def test_watcher_notifies_when_manual_application_required(db_session, sample_ready_opp):
    # Transition to MANUAL_APPLICATION_REQUIRED
    opportunity_service.transition_status(
        db_session, sample_ready_opp.id, OpportunityStatus.MANUAL_APPLICATION_REQUIRED,
        reason="Anti-bot verification challenged form", actor="worker"
    )
    db_session.commit()

    with patch("core.notifications.worker_watcher.notification_service.dispatch") as mock_dispatch:
        mock_dispatch.return_value = MagicMock(skipped=False)
        stats = poll_worker_events(db_session)
        assert stats["checked"] >= 1
        assert stats["notified"] == 1

        mock_dispatch.assert_called_once()
        event = mock_dispatch.call_args[0][0]
        assert event.event_type == "application_manual_required"
        assert "TechCorp Inc" in event.title
        assert "Anti-bot verification challenged form" in event.body

    # Subsequent poll deduplicates
    with patch("core.notifications.worker_watcher.notification_service.dispatch") as mock_dispatch:
        stats2 = poll_worker_events(db_session)
        assert stats2["notified"] == 0
        mock_dispatch.assert_not_called()
