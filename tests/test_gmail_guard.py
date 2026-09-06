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
