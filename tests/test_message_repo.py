"""Tests for the message repository (Phase 7)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from core.models.message import IntegrationHealthEvent, RecruiterMessage
from core.models.opportunity import Application, Opportunity
from core.repositories import message_repo
from core.status import OpportunityStatus


def _seed(db: Session) -> list[RecruiterMessage]:
    """Seed test messages."""
    msgs = [
        RecruiterMessage(
            gmail_id=f"repo_{i}",
            sender=f"sender{i}@test.com",
            sender_domain="test.com",
            subject=f"Subject {i}",
            classification=cls,
            classification_source="rules",
            application_id=None,
            received_at=datetime(2024, 6, i + 1, tzinfo=timezone.utc),
        )
        for i, cls in enumerate(
            ["interview_invite", "rejection", "offer", "generic", "unclassified"]
        )
    ]
    db.add_all(msgs)
    db.flush()
    return msgs


class TestGetMessage:
    def test_by_id(self, db_session: Session):
        msgs = _seed(db_session)
        found = message_repo.get_message(db_session, msgs[0].id)
        assert found is not None
        assert found.gmail_id == "repo_0"

    def test_by_gmail_id(self, db_session: Session):
        msgs = _seed(db_session)
        found = message_repo.get_message_by_gmail_id(db_session, "repo_2")
        assert found is not None
        assert found.classification == "offer"

    def test_not_found(self, db_session: Session):
        assert message_repo.get_message(db_session, 99999) is None
        assert message_repo.get_message_by_gmail_id(db_session, "nonexistent") is None


class TestListMessages:
    def test_all(self, db_session: Session):
        _seed(db_session)
        items, total = message_repo.list_messages(db_session)
        assert total == 5
        assert len(items) == 5

    def test_filter_classification(self, db_session: Session):
        _seed(db_session)
        items, total = message_repo.list_messages(
            db_session, classification="rejection"
        )
        assert total == 1
        assert items[0].classification == "rejection"

    def test_filter_linked(self, db_session: Session):
        _seed(db_session)
        items, total = message_repo.list_messages(db_session, linked=True)
        assert total == 0  # none are linked

    def test_filter_unlinked(self, db_session: Session):
        _seed(db_session)
        items, total = message_repo.list_messages(db_session, linked=False)
        assert total == 5

    def test_pagination(self, db_session: Session):
        _seed(db_session)
        items, total = message_repo.list_messages(db_session, limit=2, offset=0)
        assert total == 5
        assert len(items) == 2

    def test_offset(self, db_session: Session):
        _seed(db_session)
        items, total = message_repo.list_messages(db_session, limit=2, offset=3)
        assert total == 5
        assert len(items) == 2


class TestMessageStats:
    def test_stats(self, db_session: Session):
        _seed(db_session)
        stats = message_repo.get_message_stats(db_session)
        assert stats["total"] == 5
        assert stats["linked"] == 0
        assert stats["unlinked"] == 5
        assert stats["by_classification"]["interview_invite"] == 1
        assert stats["by_classification"]["rejection"] == 1

    def test_empty_stats(self, db_session: Session):
        stats = message_repo.get_message_stats(db_session)
        assert stats["total"] == 0
        assert stats["linked"] == 0


class TestHealthEvents:
    def test_list_events(self, db_session: Session):
        db_session.add(
            IntegrationHealthEvent(
                integration_name="gmail_response_poller",
                event_type="poll_success",
                detail="OK",
            )
        )
        db_session.flush()

        items, total = message_repo.list_health_events(db_session)
        assert total == 1
        assert items[0].event_type == "poll_success"

    def test_get_latest(self, db_session: Session):
        db_session.add_all([
            IntegrationHealthEvent(
                integration_name="gmail_response_poller",
                event_type="poll_success",
            ),
            IntegrationHealthEvent(
                integration_name="gmail_response_poller",
                event_type="token_refresh_failure",
            ),
        ])
        db_session.flush()

        latest = message_repo.get_latest_health_event(
            db_session, "gmail_response_poller"
        )
        assert latest is not None
        # Latest should be the one added last (by occurred_at default)

    def test_filter_by_name(self, db_session: Session):
        db_session.add_all([
            IntegrationHealthEvent(
                integration_name="gmail_response_poller",
                event_type="poll_success",
            ),
            IntegrationHealthEvent(
                integration_name="other_service",
                event_type="error",
            ),
        ])
        db_session.flush()

        items, total = message_repo.list_health_events(
            db_session, integration_name="gmail_response_poller"
        )
        assert total == 1
        assert items[0].integration_name == "gmail_response_poller"
