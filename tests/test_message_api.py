"""Tests for the message API endpoints (Phase 7)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from core.models.message import IntegrationHealthEvent, RecruiterMessage
from core.models.opportunity import Application, Opportunity
from core.status import OpportunityStatus


def _seed_messages(db: Session) -> list[RecruiterMessage]:
    """Create test messages across various classifications."""
    # Create an opportunity + application for linking
    opp = Opportunity(
        dedup_hash="api_test_opp_1",
        title="Backend Engineer",
        company="TestCo",
        status=OpportunityStatus.SUBMITTED.value,
    )
    db.add(opp)
    db.flush()
    app = Application(opportunity_id=opp.id, status="submitted")
    db.add(app)
    db.flush()

    messages = [
        RecruiterMessage(
            gmail_id="api_msg_1",
            thread_id="t1",
            application_id=app.id,
            sender="hr@testco.com",
            sender_domain="testco.com",
            subject="Interview Invitation",
            body_preview="We'd like to schedule an interview.",
            received_at=datetime(2024, 6, 1, 12, 0, tzinfo=timezone.utc),
            classification="interview_invite",
            classification_source="rules",
            classification_confidence=0.90,
            link_confidence="high",
        ),
        RecruiterMessage(
            gmail_id="api_msg_2",
            sender="recruiter@other.com",
            sender_domain="other.com",
            subject="Application Update",
            body_preview="Unfortunately, we have decided not to proceed.",
            received_at=datetime(2024, 6, 2, 10, 0, tzinfo=timezone.utc),
            classification="rejection",
            classification_source="rules",
            classification_confidence=0.88,
            link_confidence="none",
        ),
        RecruiterMessage(
            gmail_id="api_msg_3",
            sender="noreply@auto.com",
            sender_domain="auto.com",
            subject="Application Received",
            body_preview="Thank you for applying.",
            received_at=datetime(2024, 6, 3, 8, 0, tzinfo=timezone.utc),
            classification="generic",
            classification_source="rules",
            classification_confidence=0.85,
            link_confidence="none",
        ),
    ]
    db.add_all(messages)
    db.flush()
    return messages


def _seed_health_events(db: Session) -> list[IntegrationHealthEvent]:
    """Create test health events."""
    events = [
        IntegrationHealthEvent(
            integration_name="gmail_response_poller",
            event_type="poll_success",
            detail="Processed 5 messages",
        ),
        IntegrationHealthEvent(
            integration_name="gmail_response_poller",
            event_type="token_refresh_failure",
            detail="Token expired",
        ),
    ]
    db.add_all(events)
    db.flush()
    return events


class TestListMessages:
    def test_list_all(self, client, db_session):
        _seed_messages(db_session)
        resp = client.get("/messages")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert len(data["items"]) == 3

    def test_filter_by_classification(self, client, db_session):
        _seed_messages(db_session)
        resp = client.get("/messages?classification=rejection")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["classification"] == "rejection"

    def test_filter_linked(self, client, db_session):
        _seed_messages(db_session)
        resp = client.get("/messages?linked=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["items"][0]["application_id"] is not None

    def test_filter_unlinked(self, client, db_session):
        _seed_messages(db_session)
        resp = client.get("/messages?linked=false")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

    def test_pagination(self, client, db_session):
        _seed_messages(db_session)
        resp = client.get("/messages?limit=2&offset=0")
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["total"] == 3


class TestGetMessage:
    def test_get_existing(self, client, db_session):
        msgs = _seed_messages(db_session)
        resp = client.get(f"/messages/{msgs[0].id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["gmail_id"] == "api_msg_1"
        assert data["classification"] == "interview_invite"

    def test_get_nonexistent(self, client, db_session):
        resp = client.get("/messages/99999")
        assert resp.status_code == 404


class TestMessageStats:
    def test_stats(self, client, db_session):
        _seed_messages(db_session)
        resp = client.get("/messages/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 3
        assert data["linked"] == 1
        assert data["unlinked"] == 2
        assert data["by_classification"]["interview_invite"] == 1
        assert data["by_classification"]["rejection"] == 1

    def test_stats_empty(self, client, db_session):
        resp = client.get("/messages/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0


class TestIntegrationHealth:
    def test_list_events(self, client, db_session):
        _seed_health_events(db_session)
        resp = client.get("/integration-health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["items"]) == 2

    def test_filter_by_name(self, client, db_session):
        _seed_health_events(db_session)
        resp = client.get("/integration-health?integration_name=gmail_response_poller")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2

    def test_empty(self, client, db_session):
        resp = client.get("/integration-health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0


class TestEnrichedMessageResponse:
    def test_linked_message_has_opportunity_info(self, client, db_session):
        msgs = _seed_messages(db_session)
        resp = client.get(f"/messages/{msgs[0].id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["opportunity_company"] == "TestCo"
        assert data["opportunity_title"] == "Backend Engineer"

    def test_unlinked_message_has_null_opportunity(self, client, db_session):
        msgs = _seed_messages(db_session)
        resp = client.get(f"/messages/{msgs[1].id}")
        data = resp.json()
        assert data["opportunity_company"] is None
        assert data["opportunity_title"] is None
