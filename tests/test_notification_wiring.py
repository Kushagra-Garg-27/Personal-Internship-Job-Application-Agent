"""Integration tests verifying real event wiring from Phases 5 and 7 (Phase 8)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from core.funnel.runner import evaluate_opportunity
from core.funnel.scam_risk.gemini_client import LLMScamResult
from core.funnel.scam_risk.stage import ScamRiskStage
from core.messaging.poller import _record_health_event, run_response_poll
from core.models.opportunity import Opportunity
from core.notifications.service import notification_service
from core.status import OpportunityStatus


class TestPhase7MessageWiring:
    @patch("core.messaging.poller.build_gmail_service")
    @patch("core.messaging.poller.load_gmail_state")
    @patch("core.messaging.poller.save_gmail_state")
    @patch("core.messaging.poller.poll_new_messages")
    def test_interview_invite_triggers_notification(
        self,
        mock_poll,
        mock_save,
        mock_load,
        mock_build,
        db_session: Session,
    ):
        mock_build.return_value = MagicMock()
        mock_load.return_value = {}
        mock_poll.return_value = (
            [
                {
                    "id": "gmail_msg_wire_1",
                    "threadId": "thread_wire_1",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Jane Doe <recruiter@techcorp.com>"},
                            {"name": "Subject", "value": "Schedule your interview with TechCorp"},
                            {"name": "Date", "value": "Fri, 05 Sep 2026 12:00:00 +0000"},
                        ],
                        "mimeType": "text/plain",
                        "body": {"data": ""},
                    },
                    "snippet": "We would like to invite you for an interview next week.",
                }
            ],
            "new_history_123",
        )

        with patch.object(notification_service, "dispatch", wraps=notification_service.dispatch) as mock_dispatch:
            stats = run_response_poll(db_session)
            assert stats["classified"] == 1

            # Verify notification_service.dispatch was called with interview_invite
            assert mock_dispatch.called
            dispatched_events = [call[0][0] for call in mock_dispatch.call_args_list]
            msg_events = [e for e in dispatched_events if e.event_type == "interview_invite"]
            assert len(msg_events) == 1
            assert "TechCorp" in msg_events[0].title or "TechCorp" in msg_events[0].body


class TestPhase7HealthWiring:
    def test_token_failure_triggers_health_notification(self, db_session: Session):
        """When OAuth token expires or refresh fails, a health alert is dispatched."""
        with patch.object(notification_service, "dispatch") as mock_dispatch:
            _record_health_event(
                session=db_session,
                event_type="token_refresh_failure",
                detail="invalid_grant: Token has been expired or revoked.",
            )

            mock_dispatch.assert_called_once()
            dispatched_event = mock_dispatch.call_args[0][0]
            assert dispatched_event.event_type == "integration_unhealthy"
            assert "invalid_grant" in dispatched_event.body


class TestPhase5QuotaWiring:
    def test_gemini_quota_exhaustion_triggers_notification(self, db_session: Session):
        """When ambiguous listing cannot be evaluated due to Gemini quota, an alert is sent."""
        opp = Opportunity(
            dedup_hash="wire_quota_test_hash",
            title="Software Engineer",
            company="Ambiguous Ventures",
            description="Contact on telegram or whatsapp for immediate hire earn $5000",
            status=OpportunityStatus.DISCOVERED.value,
        )
        db_session.add(opp)
        db_session.commit()

        # Mock Gemini client to simulate quota exhausted
        mock_gemini = MagicMock()
        mock_gemini.evaluate_ambiguous.return_value = LLMScamResult(
            success=False,
            is_quota_exhausted=True,
            error="Gemini 429 Resource Exhausted: daily quota reached",
        )

        stage = ScamRiskStage(gemini_client=mock_gemini, session=db_session)

        from core.funnel.eligibility import EligibilityStage
        from core.funnel.runner import FunnelRunner
        runner = FunnelRunner(stages=[EligibilityStage(), stage])

        with patch.object(notification_service, "dispatch") as mock_dispatch:
            verdict = evaluate_opportunity(
                session=db_session,
                opportunity=opp,
                runner=runner,
            )

            assert verdict.scam_verdict == "deferred"
            # Verify quota exhausted alert was sent
            assert mock_dispatch.called
            dispatched_event = mock_dispatch.call_args[0][0]
            assert dispatched_event.event_type == "quota_exhausted"
            assert "Ambiguous Ventures" in dispatched_event.body
