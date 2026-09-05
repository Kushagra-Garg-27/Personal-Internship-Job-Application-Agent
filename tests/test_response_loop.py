"""Phase 10: Recruiter Response Loop Test Suite.

Verifies:
1. Hard no-auto-send guarantee (restricted OAuth scopes, mock send assertion).
2. Gemini-backed suggested reply drafting (reply-bearing vs non-reply, quota fallback).
3. Status machine transitions and status_history audit logging on approve / acknowledge.
4. Phase 7 regression guard (message ingestion/classification alone never mutates status).
5. Human editing test (edited text is what gets placed into Gmail draft, not unedited output).
6. API endpoint integration.
"""

from __future__ import annotations

import base64
from email import message_from_bytes, policy
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from core.discovery.gmail_client import SCOPES
from core.funnel.scam_risk.gemini_client import GeminiQuotaTracker
from core.messaging.draft_service import create_gmail_draft
from core.messaging.reply_drafter import (
    AI_REPLY_PREFIX,
    ReplyDrafter,
    is_reply_bearing,
)
from core.models.message import RecruiterMessage
from core.models.opportunity import Application, Opportunity, StatusHistory
from core.models.profile import Profile
from core.services import response_loop_service
from core.status import OpportunityStatus


# ── Helpers & Fixtures ───────────────────────────────────────────────────


def _create_test_pipeline(
    session: Session,
    opp_status: str = "applied",
    classification: str = "interview_invite",
    suggested_reply: str | None = None,
) -> tuple[Opportunity, Application, RecruiterMessage]:
    """Helper to create an opportunity, application, and linked recruiter message."""
    import uuid

    # Unique dedup_hash
    unique_suffix = uuid.uuid4().hex[:8]

    opp = Opportunity(
        title=f"Senior Software Engineer {unique_suffix}",
        company="Acme Corp",
        dedup_hash=f"hash_{unique_suffix}",
        status=opp_status,
        reliability_tier="stable",
    )
    session.add(opp)
    session.flush()

    app = Application(
        opportunity_id=opp.id,
        attempt_number=1,
        status="submitted",
        notes="Applied with v2 resume",
    )
    session.add(app)
    session.flush()

    msg = RecruiterMessage(
        gmail_id=f"msg_{unique_suffix}",
        thread_id=f"thread_{unique_suffix}",
        application_id=app.id,
        sender="recruiter@acme.com",
        sender_domain="acme.com",
        subject="Interview with Acme Corp",
        body_preview="Hi Kushal, we were impressed by your resume and would love to schedule a 30-min call.",
        classification=classification,
        classification_source="rules",
        classification_confidence=0.95,
        link_confidence="high",
        suggested_reply=suggested_reply,
    )
    session.add(msg)
    session.flush()

    return opp, app, msg


def _make_mock_gmail_service():
    """Create a mock Gmail service and track method calls."""
    service = MagicMock()
    draft_create_mock = MagicMock(return_value={"id": "draft_mock_123"})
    service.users().drafts().create.return_value.execute = draft_create_mock

    # Mock send methods to detect if code ever calls them
    service.users().messages().send.side_effect = RuntimeError(
        "CRITICAL ERROR: users().messages().send() was called! Auto-send invariant violated."
    )
    service.users().drafts().send.side_effect = RuntimeError(
        "CRITICAL ERROR: users().drafts().send() was called! Auto-send invariant violated."
    )

    return service, draft_create_mock


# ── 1. The Core No-Auto-Send Guarantee ───────────────────────────────────


def test_oauth_scopes_exclude_send():
    """Assert OAuth scopes include compose/drafts and readonly, strictly excluding gmail.send."""
    assert "https://www.googleapis.com/auth/gmail.compose" in SCOPES
    assert "https://www.googleapis.com/auth/gmail.readonly" in SCOPES

    # Explicitly verify NO send scopes are present
    assert "https://www.googleapis.com/auth/gmail.send" not in SCOPES
    assert "https://mail.google.com/" not in SCOPES
    assert "https://www.googleapis.com/auth/gmail.modify" not in SCOPES


def test_mock_assertion_send_never_invoked(db_session: Session):
    """Verify that approving a reply calls drafts().create() and NEVER calls send()."""
    opp, app, msg = _create_test_pipeline(
        db_session,
        opp_status="applied",
        classification="interview_invite",
        suggested_reply="I am available for an interview.",
    )

    service, draft_create_mock = _make_mock_gmail_service()

    updated_msg, updated_opp = response_loop_service.approve_and_create_draft(
        db=db_session,
        message_id=msg.id,
        gmail_service=service,
    )

    # drafts.create was invoked once
    assert draft_create_mock.called
    assert updated_msg.draft_id == "draft_mock_123"
    assert updated_msg.action_taken == "approved"

    # messages().send was NEVER called
    assert not service.users().messages().send.called
    # drafts().send was NEVER called
    assert not service.users().drafts().send.called


def test_structural_no_send_calls_in_messaging():
    """Structural AST/text check that no call to .send( exists in messaging services."""
    import inspect
    from core.messaging import draft_service
    from core.services import response_loop_service

    draft_code = inspect.getsource(draft_service)
    loop_code = inspect.getsource(response_loop_service)

    assert ".send(" not in draft_code
    assert ".messages().send" not in loop_code
    assert ".drafts().send" not in loop_code


# ── 2. Suggested-Reply Drafting ──────────────────────────────────────────


def test_reply_drafter_interview_invite(db_session: Session):
    """Test reply drafting with mocked Gemini client for an interview invite."""
    opp, app, msg = _create_test_pipeline(
        db_session,
        opp_status="applied",
        classification="interview_invite",
    )

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "Thank you for the interview invitation! I am very excited to speak with the team."
    mock_client.models.generate_content.return_value = mock_response

    drafter = ReplyDrafter(client=mock_client)
    reply = drafter.draft_reply(msg, opp, None, app)

    assert reply.startswith(AI_REPLY_PREFIX)
    assert "Thank you for the interview invitation!" in reply
    assert mock_client.models.generate_content.called


def test_reply_drafter_offer(db_session: Session):
    """Test reply drafting for an offer."""
    opp, app, msg = _create_test_pipeline(
        db_session,
        opp_status="interview",
        classification="offer",
    )

    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "I am thrilled to receive this offer for the Senior Software Engineer role."
    mock_client.models.generate_content.return_value = mock_response

    drafter = ReplyDrafter(client=mock_client)
    reply = drafter.draft_reply(msg, opp, None, app)

    assert reply.startswith(AI_REPLY_PREFIX)
    assert "thrilled to receive this offer" in reply


def test_reply_drafter_non_reply_category(db_session: Session):
    """Test that non-reply categories (rejection, generic) raise ValueError."""
    opp, app, msg = _create_test_pipeline(
        db_session,
        opp_status="applied",
        classification="rejection",
    )

    drafter = ReplyDrafter()
    with pytest.raises(ValueError, match="does not warrant a reply draft"):
        drafter.draft_reply(msg, opp, None, app)


def test_reply_drafter_quota_exhaustion_fallback(db_session: Session):
    """Test that exhausted quota yields a template draft prefixed with AI draft marker."""
    opp, app, msg = _create_test_pipeline(
        db_session,
        opp_status="applied",
        classification="interview_invite",
    )

    exhausted_tracker = GeminiQuotaTracker(daily_limit=1)
    exhausted_tracker.record_call()
    assert exhausted_tracker.is_exhausted()

    drafter = ReplyDrafter(tracker=exhausted_tracker)
    reply = drafter.draft_reply(msg, opp, None, app)

    assert reply.startswith(AI_REPLY_PREFIX)
    assert "Daily quota exhausted" in reply
    assert "interview for the" in reply


# ── 3. Status-Update Tests ───────────────────────────────────────────────


def test_approve_interview_invite_updates_status(db_session: Session):
    """Approving an interview invite reply transitions opportunity to interview_scheduled."""
    opp, app, msg = _create_test_pipeline(
        db_session,
        opp_status="applied",
        classification="interview_invite",
        suggested_reply="Thank you, I'd love to interview.",
    )

    service, _ = _make_mock_gmail_service()

    updated_msg, updated_opp = response_loop_service.approve_and_create_draft(
        db=db_session,
        message_id=msg.id,
        gmail_service=service,
    )

    assert updated_opp.status == OpportunityStatus.INTERVIEW_SCHEDULED.value

    # Verify status_history was written
    history = (
        db_session.query(StatusHistory)
        .filter(StatusHistory.opportunity_id == opp.id)
        .order_by(StatusHistory.id.desc())
        .first()
    )
    assert history is not None
    assert history.old_status == "applied"
    assert history.new_status == OpportunityStatus.INTERVIEW_SCHEDULED.value
    assert history.actor == "human_approved_reply"


def test_approve_offer_updates_status(db_session: Session):
    """Approving an offer reply transitions opportunity to offer_received."""
    opp, app, msg = _create_test_pipeline(
        db_session,
        opp_status="interview",
        classification="offer",
        suggested_reply="Thank you for the wonderful offer.",
    )

    service, _ = _make_mock_gmail_service()

    updated_msg, updated_opp = response_loop_service.approve_and_create_draft(
        db=db_session,
        message_id=msg.id,
        gmail_service=service,
    )

    assert updated_opp.status == OpportunityStatus.OFFER_RECEIVED.value

    history = (
        db_session.query(StatusHistory)
        .filter(StatusHistory.opportunity_id == opp.id)
        .order_by(StatusHistory.id.desc())
        .first()
    )
    assert history is not None
    assert history.new_status == OpportunityStatus.OFFER_RECEIVED.value
    assert history.actor == "human_approved_reply"


def test_acknowledge_rejection_updates_status(db_session: Session):
    """Acknowledging a rejection message transitions opportunity to rejected_by_recruiter."""
    opp, app, msg = _create_test_pipeline(
        db_session,
        opp_status="applied",
        classification="rejection",
    )

    updated_msg, updated_opp = response_loop_service.acknowledge_message(
        db=db_session,
        message_id=msg.id,
    )

    assert updated_opp.status == OpportunityStatus.REJECTED_BY_RECRUITER.value
    assert updated_msg.action_taken == "acknowledged"

    history = (
        db_session.query(StatusHistory)
        .filter(StatusHistory.opportunity_id == opp.id)
        .order_by(StatusHistory.id.desc())
        .first()
    )
    assert history is not None
    assert history.new_status == OpportunityStatus.REJECTED_BY_RECRUITER.value
    assert history.actor == "human_acknowledged_message"


# ── 4. Phase 7 Non-Goal Regression Test ──────────────────────────────────


def test_newly_classified_message_never_changes_status(db_session: Session):
    """REGRESSION TEST: A newly classified message ALONE must NEVER mutate opportunity status.

    Only explicit human action (approve or acknowledge) can trigger status changes.
    """
    import uuid

    unique_suffix = uuid.uuid4().hex[:8]
    opp = Opportunity(
        title=f"Role {unique_suffix}",
        company="Startup Co",
        dedup_hash=f"hash_{unique_suffix}",
        status="applied",
        reliability_tier="stable",
    )
    db_session.add(opp)
    db_session.flush()

    app = Application(opportunity_id=opp.id, status="submitted")
    db_session.add(app)
    db_session.flush()

    # Step 1: Simulate Phase 7 message ingestion & classification
    new_msg = RecruiterMessage(
        gmail_id=f"msg_{unique_suffix}",
        application_id=app.id,
        sender="hr@startup.co",
        subject="Invitation to Interview",
        body_preview="Please let us know your availability.",
        classification="interview_invite",
        classification_source="llm",
        classification_confidence=0.98,
        link_confidence="high",
    )
    db_session.add(new_msg)
    db_session.flush()

    # CRITICAL CHECK: Opportunity status MUST STILL BE 'applied'
    db_session.refresh(opp)
    assert opp.status == "applied"

    # Status history MUST NOT contain any transition
    history_count = (
        db_session.query(StatusHistory)
        .filter(StatusHistory.opportunity_id == opp.id)
        .count()
    )
    assert history_count == 0

    # Step 2: Now human explicitly approves
    service, _ = _make_mock_gmail_service()
    response_loop_service.approve_and_create_draft(
        db=db_session,
        message_id=new_msg.id,
        edited_reply="I am available on Thursday.",
        gmail_service=service,
    )

    db_session.refresh(opp)
    assert opp.status == OpportunityStatus.INTERVIEW_SCHEDULED.value


# ── 5. Human Editing Test ────────────────────────────────────────────────


def test_human_edited_reply_passed_to_draft(db_session: Session):
    """Test that a human-edited reply is what gets sent to Gmail draft creation, not raw LLM output."""
    raw_llm_draft = f"{AI_REPLY_PREFIX}\n\nDear Recruiter, I am interested in interviewing."
    human_edited_reply = "Hi Jane! Thanks for reaching out. I would love to chat. How about Thursday at 3 PM EST?"

    opp, app, msg = _create_test_pipeline(
        db_session,
        opp_status="applied",
        classification="interview_invite",
        suggested_reply=raw_llm_draft,
    )

    service, draft_create_mock = _make_mock_gmail_service()

    updated_msg, updated_opp = response_loop_service.approve_and_create_draft(
        db=db_session,
        message_id=msg.id,
        edited_reply=human_edited_reply,
        gmail_service=service,
    )

    # Retrieve the body passed to service.users().drafts().create
    call_args = service.users().drafts().create.call_args
    assert call_args is not None
    body_passed = call_args.kwargs.get("body", {})

    raw_base64 = body_passed["message"]["raw"]
    decoded_bytes = base64.urlsafe_b64decode(raw_base64)
    email_msg = message_from_bytes(decoded_bytes, policy=policy.default)

    email_body = email_msg.get_content().strip()

    # The email content MUST match the human-edited text
    assert email_body == human_edited_reply
    assert "Thursday at 3 PM EST" in email_body
    # The raw unedited LLM draft MUST NOT be present
    assert "Dear Recruiter, I am interested" not in email_body
    assert AI_REPLY_PREFIX not in email_body

    # Database record should reflect the edited reply
    assert updated_msg.suggested_reply == human_edited_reply


# ── 6. API Layer Tests ───────────────────────────────────────────────────


def test_api_draft_reply_endpoint(client: TestClient, db_session: Session):
    """Test POST /messages/{id}/draft-reply."""
    opp, app, msg = _create_test_pipeline(
        db_session,
        opp_status="applied",
        classification="interview_invite",
    )

    with patch("core.services.response_loop_service.ReplyDrafter") as MockDrafter:
        mock_instance = MockDrafter.return_value
        mock_instance.draft_reply.return_value = f"{AI_REPLY_PREFIX}\n\nMocked API Draft Response"

        resp = client.post(f"/messages/{msg.id}/draft-reply")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == msg.id
        assert data["suggested_reply"] == f"{AI_REPLY_PREFIX}\n\nMocked API Draft Response"


def test_api_approve_reply_endpoint(client: TestClient, db_session: Session):
    """Test POST /messages/{id}/approve-reply creates draft and updates status."""
    opp, app, msg = _create_test_pipeline(
        db_session,
        opp_status="applied",
        classification="interview_invite",
        suggested_reply="Ready to interview.",
    )

    service, _ = _make_mock_gmail_service()

    with patch("core.services.response_loop_service.build_gmail_service", return_value=service):
        payload = {"edited_reply": "Confirmed interview availability for next Monday."}
        resp = client.post(f"/messages/{msg.id}/approve-reply", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        assert data["opportunity_id"] == opp.id
        assert data["opportunity_status"] == OpportunityStatus.INTERVIEW_SCHEDULED.value
        assert data["draft_id"] == "draft_mock_123"
        assert data["message"]["action_taken"] == "approved"


def test_api_acknowledge_endpoint(client: TestClient, db_session: Session):
    """Test POST /messages/{id}/acknowledge updates status to rejected_by_recruiter."""
    opp, app, msg = _create_test_pipeline(
        db_session,
        opp_status="applied",
        classification="rejection",
    )

    resp = client.post(f"/messages/{msg.id}/acknowledge")
    assert resp.status_code == 200
    data = resp.json()

    assert data["opportunity_id"] == opp.id
    assert data["opportunity_status"] == OpportunityStatus.REJECTED_BY_RECRUITER.value
    assert data["message"]["action_taken"] == "acknowledged"


def test_api_unlinked_message_rejected(client: TestClient, db_session: Session):
    """Test that approving or acknowledging an unlinked message returns HTTP 400."""
    import uuid

    unique_suffix = uuid.uuid4().hex[:8]
    unlinked_msg = RecruiterMessage(
        gmail_id=f"unlinked_{unique_suffix}",
        application_id=None,
        sender="stranger@example.com",
        subject="Hello",
        classification="interview_invite",
    )
    db_session.add(unlinked_msg)
    db_session.flush()
    msg_id = unlinked_msg.id

    resp = client.post(f"/messages/{msg_id}/approve-reply", json={"edited_reply": "Hi"})
    assert resp.status_code == 400
    assert "not linked to an application" in resp.json()["detail"]

    resp_ack = client.post(f"/messages/{msg_id}/acknowledge")
    assert resp_ack.status_code == 400
    assert "not linked to an application" in resp_ack.json()["detail"]

