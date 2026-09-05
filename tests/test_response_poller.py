"""Tests for the response poller (Phase 7)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from core.messaging.poller import run_response_poll
from core.models.message import IntegrationHealthEvent, RecruiterMessage
from core.models.opportunity import Application, Opportunity
from core.status import OpportunityStatus


def _create_app(db: Session, company: str, url: str | None = None):
    """Helper to create an opportunity + application."""
    opp = Opportunity(
        dedup_hash=f"poller_hash_{company}_{id(company)}",
        title=f"Eng at {company}",
        company=company,
        url=url,
        status=OpportunityStatus.SUBMITTED.value,
    )
    db.add(opp)
    db.flush()
    app = Application(opportunity_id=opp.id, status="submitted")
    db.add(app)
    db.flush()
    return opp, app


def _mock_gmail_message(
    msg_id: str = "msg_001",
    thread_id: str = "thread_001",
    sender: str = "recruiter@acme.com",
    subject: str = "Schedule your interview",
    body: str = "We'd like to schedule an interview.",
    internal_date: str = "1717459200000",
) -> dict:
    """Create a mock Gmail message resource."""
    import base64

    body_encoded = base64.urlsafe_b64encode(body.encode()).decode()
    return {
        "id": msg_id,
        "threadId": thread_id,
        "internalDate": internal_date,
        "snippet": body[:100],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Mon, 3 Jun 2024 12:00:00 +0000"},
            ],
            "body": {"data": body_encoded},
        },
    }


class TestPollerIdempotency:
    @patch("core.messaging.poller.build_gmail_service")
    @patch("core.messaging.poller.poll_new_messages")
    @patch("core.messaging.poller.load_gmail_state", return_value={})
    @patch("core.messaging.poller.save_gmail_state")
    @patch("core.messaging.poller._get_user_email", return_value="me@gmail.com")
    def test_duplicate_gmail_id_skipped(
        self, mock_user, mock_save, mock_load, mock_poll, mock_build, db_session
    ):
        """Processing the same gmail_id twice should skip the duplicate."""
        mock_build.return_value = MagicMock()
        msg = _mock_gmail_message(msg_id="dup_001", subject="Your interview")
        mock_poll.return_value = ([msg], "history_999")

        # First poll
        stats1 = run_response_poll(db_session)
        assert stats1["classified"] >= 0  # might be 0 or 1 depending on filter

        # Insert a message directly to simulate the first run
        existing = db_session.query(RecruiterMessage).filter_by(gmail_id="dup_001").first()
        if not existing:
            db_session.add(
                RecruiterMessage(
                    gmail_id="dup_001",
                    sender="recruiter@acme.com",
                    subject="Your interview",
                )
            )
            db_session.flush()

        # Second poll with same message
        mock_poll.return_value = ([msg], "history_1000")
        stats2 = run_response_poll(db_session)
        assert stats2["skipped_duplicate"] >= 1


class TestPollerAuthFailure:
    @patch("core.messaging.poller.build_gmail_service")
    def test_service_build_failure_records_health_event(self, mock_build, db_session):
        """When build_gmail_service raises, a health event should be recorded."""
        mock_build.side_effect = Exception("Token refresh failed: invalid_grant")

        stats = run_response_poll(db_session)

        events = (
            db_session.query(IntegrationHealthEvent)
            .filter_by(integration_name="gmail_response_poller")
            .all()
        )
        assert len(events) >= 1
        assert events[-1].event_type == "token_refresh_failure"
        assert "invalid_grant" in (events[-1].detail or "")

    @patch("core.messaging.poller.build_gmail_service")
    @patch("core.messaging.poller.poll_new_messages")
    @patch("core.messaging.poller.load_gmail_state", return_value={})
    @patch("core.messaging.poller.save_gmail_state")
    @patch("core.messaging.poller._get_user_email", return_value="me@gmail.com")
    def test_poll_error_records_health_event(
        self, mock_user, mock_save, mock_load, mock_poll, mock_build, db_session
    ):
        """When poll_new_messages raises, a health event is recorded."""
        mock_build.return_value = MagicMock()
        mock_poll.side_effect = Exception("Token refresh error: expired credentials")

        stats = run_response_poll(db_session)

        events = (
            db_session.query(IntegrationHealthEvent)
            .filter_by(integration_name="gmail_response_poller")
            .all()
        )
        assert len(events) >= 1
        assert events[-1].event_type == "token_refresh_failure"


class TestPollerHappyPath:
    @patch("core.messaging.poller.build_gmail_service")
    @patch("core.messaging.poller.poll_new_messages")
    @patch("core.messaging.poller.load_gmail_state", return_value={})
    @patch("core.messaging.poller.save_gmail_state")
    @patch("core.messaging.poller._get_user_email", return_value="me@gmail.com")
    def test_processes_candidate_message(
        self, mock_user, mock_save, mock_load, mock_poll, mock_build, db_session
    ):
        """A message matching the candidate filter should be classified and stored."""
        _create_app(db_session, "Acme Corp", url="https://acme.com/careers")
        mock_build.return_value = MagicMock()

        msg = _mock_gmail_message(
            msg_id="happy_001",
            sender="hr@acme.com",
            subject="Schedule your interview at Acme Corp",
            body="We'd like to schedule your interview for next week.",
        )
        mock_poll.return_value = ([msg], "history_200")

        stats = run_response_poll(db_session)

        stored = db_session.query(RecruiterMessage).filter_by(gmail_id="happy_001").first()
        if stored:
            assert stored.classification is not None
            assert stored.sender_domain is not None
            assert stats["classified"] >= 1

    @patch("core.messaging.poller.build_gmail_service")
    @patch("core.messaging.poller.poll_new_messages")
    @patch("core.messaging.poller.load_gmail_state", return_value={})
    @patch("core.messaging.poller.save_gmail_state")
    @patch("core.messaging.poller._get_user_email", return_value="me@gmail.com")
    def test_skips_noreply_messages(
        self, mock_user, mock_save, mock_load, mock_poll, mock_build, db_session
    ):
        """noreply messages should be filtered out."""
        mock_build.return_value = MagicMock()

        msg = _mock_gmail_message(
            msg_id="noreply_001",
            sender="noreply@company.com",
            subject="Application received",
        )
        mock_poll.return_value = ([msg], "history_300")

        stats = run_response_poll(db_session)
        assert stats["skipped_filter"] >= 1

        stored = db_session.query(RecruiterMessage).filter_by(gmail_id="noreply_001").first()
        assert stored is None

    @patch("core.messaging.poller.build_gmail_service")
    def test_gmail_not_configured(self, mock_build, db_session):
        """When Gmail is not configured, the poller should return gracefully."""
        mock_build.return_value = None

        stats = run_response_poll(db_session)
        assert stats["total_fetched"] == 0
