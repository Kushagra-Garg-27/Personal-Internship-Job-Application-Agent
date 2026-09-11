"""Tests for the structural Gmail send-denial runtime guard (Phase 10 Hardening).

Verifies:
1. Calling .send() on drafts(), messages(), users(), or the service itself
   immediately raises SendOperationBlockedError.
2. Legitimate draft creation and message reading pass through cleanly.
3. Defense-in-depth: create_gmail_draft() automatically wraps un-guarded services.
"""

from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from core.discovery.gmail_client import (
    GuardedGmailService,
    SendOperationBlockedError,
    guard_gmail_service,
)
from core.messaging.draft_service import create_gmail_draft
from core.status import ApplicationStatus, OpportunityStatus


def test_guard_intercepts_drafts_send():
    """Verify that calling users().drafts().send() raises SendOperationBlockedError."""
    mock_service = MagicMock()
    guarded = guard_gmail_service(mock_service)

    with pytest.raises(SendOperationBlockedError) as exc_info:
        guarded.users().drafts().send(userId="me", body={"id": "draft_123"})

    assert "drafts().send() is structurally blocked" in str(exc_info.value)
    mock_service.users().drafts().send.assert_not_called()


def test_guard_intercepts_messages_send():
    """Verify that calling users().messages().send() raises SendOperationBlockedError."""
    mock_service = MagicMock()
    guarded = guard_gmail_service(mock_service)

    with pytest.raises(SendOperationBlockedError) as exc_info:
        guarded.users().messages().send(userId="me", body={"raw": "abc"})

    assert "messages().send() is structurally blocked" in str(exc_info.value)
    mock_service.users().messages().send.assert_not_called()


def test_guard_intercepts_users_send():
    """Verify that calling users().send() raises SendOperationBlockedError."""
    mock_service = MagicMock()
    guarded = guard_gmail_service(mock_service)

    with pytest.raises(SendOperationBlockedError) as exc_info:
        guarded.users().send()

    assert "users().send() is structurally blocked" in str(exc_info.value)


def test_guard_intercepts_service_send():
    """Verify that calling .send() directly on the service raises SendOperationBlockedError."""
    mock_service = MagicMock()
    guarded = guard_gmail_service(mock_service)

    with pytest.raises(SendOperationBlockedError) as exc_info:
        guarded.send()

    assert "Gmail service is structurally blocked" in str(exc_info.value)


def test_guard_allows_draft_create_passthrough():
    """Verify that drafts().create() passes through cleanly to the underlying resource."""
    mock_service = MagicMock()
    mock_create = MagicMock()
    mock_create.execute.return_value = {"id": "draft_abc", "message": {"id": "msg_xyz"}}
    mock_service.users().drafts().create.return_value = mock_create

    guarded = guard_gmail_service(mock_service)
    result = guarded.users().drafts().create(userId="me", body={"test": "data"}).execute()

    assert result["id"] == "draft_abc"
    mock_service.users().drafts().create.assert_called_once_with(userId="me", body={"test": "data"})


def test_guard_allows_messages_get_and_list_passthrough():
    """Verify that read operations (get, list) pass through cleanly."""
    mock_service = MagicMock()
    mock_get = MagicMock()
    mock_get.execute.return_value = {"id": "msg_999", "snippet": "Recruiter message"}
    mock_service.users().messages().get.return_value = mock_get

    guarded = guard_gmail_service(mock_service)
    result = guarded.users().messages().get(userId="me", id="msg_999").execute()

    assert result["snippet"] == "Recruiter message"
    mock_service.users().messages().get.assert_called_once_with(userId="me", id="msg_999")


def test_create_gmail_draft_auto_guards_raw_service():
    """Verify that create_gmail_draft automatically guards any passed raw service."""
    mock_service = MagicMock()
    mock_create = MagicMock()
    mock_create.execute.return_value = {"id": "draft_new_123"}
    mock_service.users().drafts().create.return_value = mock_create

    draft_id = create_gmail_draft(
        service=mock_service,
        to_email="recruiter@example.com",
        subject="Interview follow-up",
        body_text="Thank you for reaching out.",
    )

    assert draft_id == "draft_new_123"
    mock_service.users().drafts().create.assert_called_once()
    # Confirm no send was called
    mock_service.users().drafts().send.assert_not_called()
    mock_service.users().messages().send.assert_not_called()


def test_recruiter_response_handling_only_creates_drafts(db_session):
    """Verify approve_reply in response_loop_service creates a draft and never calls send."""
    from core.models.message import RecruiterMessage
    from core.services import application_service, opportunity_service, response_loop_service

    opp = opportunity_service.create_opportunity(
        db_session,
        title="Software Engineer",
        company="Acme Corp",
        url="https://unstop.com/jobs/acme",
    )
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.RECOMMENDED)
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.READY_TO_APPLY)
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.AWAITING_SUBMISSION)
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.APPLIED)

    app = application_service.create_application(
        db_session,
        opportunity_id=opp.id,
        adapter_name="unstop",
    )
    application_service.transition_application_status(
        db_session,
        app.id,
        ApplicationStatus.SUBMITTED,
    )
    db_session.commit()

    msg = RecruiterMessage(
        application_id=app.id,
        gmail_id="gmail_msg_456",
        sender="recruiter@acme.com",
        sender_domain="acme.com",
        subject="Interview Invitation",
        body_preview="We'd like to interview you",
        classification="interview_invite",
        suggested_reply="I would be delighted to interview.",
    )
    db_session.add(msg)
    db_session.commit()

    mock_service = MagicMock()
    mock_create = MagicMock()
    mock_create.execute.return_value = {"id": "draft_resp_789"}
    mock_service.users().drafts().create.return_value = mock_create

    updated_msg, updated_opp = response_loop_service.approve_and_create_draft(
        db_session,
        message_id=msg.id,
        edited_reply="I am excited to confirm my availability.",
        gmail_service=mock_service,
    )

    assert updated_msg.draft_id == "draft_resp_789"
    # Invariant: only drafts().create() is called
    mock_service.users().drafts().create.assert_called_once()
    # Invariant: drafts().send() and messages().send() are never called
    mock_service.users().drafts().send.assert_not_called()
    mock_service.users().messages().send.assert_not_called()


def test_draft_service_interface_has_no_send_functions():
    """Verify that draft_service exposes only draft creation and no send functions."""
    import core.messaging.draft_service as ds

    # Check all public callables in draft_service module
    public_callables = [
        name for name, val in ds.__dict__.items()
        if callable(val) and not name.startswith("_")
    ]
    assert "create_gmail_draft" in public_callables
    for name in public_callables:
        assert "send" not in name.lower(), f"Unexpected send function exposed: {name}"


def test_scheduler_and_worker_have_zero_send_calls():
    """Static analysis test verifying scheduler, poller, and worker never reference send()."""
    import inspect
    import core.discovery.scheduler as sched
    import core.messaging.poller as poller
    import worker.runner as wrunner
    import worker.engine.filler as wfiller

    for module in (sched, poller, wrunner, wfiller):
        source = inspect.getsource(module)
        assert ".send(" not in source, f"Forbidden .send() found in {module.__name__}"
        assert ".drafts().send" not in source, f"Forbidden drafts().send in {module.__name__}"

