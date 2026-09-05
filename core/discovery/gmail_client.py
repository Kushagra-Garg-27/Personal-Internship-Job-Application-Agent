"""Gmail API OAuth2 client and History-API polling.

This module handles authentication and the low-level ``history.list``
polling loop.  It is designed for reuse: Phase 3 uses it for job-alert
parsing, Phase 7 will extend it for recruiter-response classification.

Setup:
    1. Create a Google Cloud project and enable the Gmail API
    2. Create OAuth2 Desktop App credentials → download ``credentials.json``
    3. Set ``GMAIL_CREDENTIALS_FILE=credentials.json`` in ``.env``
    4. Run ``python -m core.discovery.gmail_client`` to complete the
       initial OAuth consent flow (opens a browser once)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from core.config import settings

logger = logging.getLogger(__name__)

# Gmail OAuth Scopes
# Hard architectural invariant (Phase 10):
# Strictly limited to readonly and compose (drafts). "gmail.send" is DELIBERATELY
# EXCLUDED so that the system credentials are structurally incapable of sending emails.
# The system only creates drafts; the human physically opens Gmail and hits Send.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
]



def _load_credentials(
    credentials_file: Path | None = None,
    token_file: Path | None = None,
):
    """Load or refresh OAuth2 credentials.

    Returns a ``google.oauth2.credentials.Credentials`` object, or
    ``None`` if Gmail is not configured.
    """
    creds_path = credentials_file or settings.GMAIL_CREDENTIALS_FILE
    tok_path = token_file or settings.GMAIL_TOKEN_FILE

    if creds_path is None or not creds_path.exists():
        logger.info("Gmail not configured (no credentials file)")
        return None

    # Lazy imports — only needed when Gmail is actually configured
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if tok_path.exists():
        creds = Credentials.from_authorized_user_file(str(tok_path), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            tok_path.write_text(creds.to_json())
            return creds
        except Exception:
            logger.warning("Gmail token refresh failed, re-authenticating")

    # Full OAuth consent flow (requires user interaction once)
    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
    creds = flow.run_local_server(port=0)
    tok_path.write_text(creds.to_json())
    logger.info("Gmail OAuth completed, token saved to %s", tok_path)
    return creds


def build_gmail_service(
    credentials_file: Path | None = None,
    token_file: Path | None = None,
):
    """Build and return a Gmail API service, or ``None`` if not configured."""
    creds = _load_credentials(credentials_file, token_file)
    if creds is None:
        return None

    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ── State persistence (history ID tracking) ──────────────────────────────


def load_gmail_state(state_file: Path | None = None) -> dict[str, Any]:
    """Load the last-known historyId from the state file."""
    path = state_file or settings.GMAIL_STATE_FILE
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("Corrupt Gmail state file, starting fresh")
    return {}


def save_gmail_state(state: dict[str, Any], state_file: Path | None = None):
    """Persist the historyId to the state file."""
    path = state_file or settings.GMAIL_STATE_FILE
    path.write_text(json.dumps(state, indent=2))


# ── History polling ──────────────────────────────────────────────────────


def poll_new_messages(
    service,
    last_history_id: str | None,
) -> tuple[list[dict], str]:
    """Poll Gmail for new messages since ``last_history_id``.

    Returns ``(messages, new_history_id)`` where *messages* is a list of
    full message resources.

    If ``last_history_id`` is ``None``, initialises from the current
    profile and returns an empty message list (first run baseline).
    """
    if service is None:
        return [], last_history_id or ""

    # First run: get the current historyId as a baseline
    if last_history_id is None:
        profile = service.users().getProfile(userId="me").execute()
        return [], profile["historyId"]

    try:
        response = (
            service.users()
            .history()
            .list(userId="me", startHistoryId=last_history_id, historyTypes=["messageAdded"])
            .execute()
        )
    except Exception as exc:
        # historyId too old → full reset
        error_str = str(exc)
        if "404" in error_str or "notFound" in error_str:
            logger.warning("Gmail historyId expired, resetting baseline")
            profile = service.users().getProfile(userId="me").execute()
            return [], profile["historyId"]
        raise

    new_history_id = response.get("historyId", last_history_id)
    history_records = response.get("history", [])

    # Collect unique message IDs
    message_ids: set[str] = set()
    for record in history_records:
        for added in record.get("messagesAdded", []):
            msg_id = added["message"]["id"]
            message_ids.add(msg_id)

    # Fetch full message content for each new message
    messages = []
    for msg_id in message_ids:
        try:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=msg_id, format="full")
                .execute()
            )
            messages.append(msg)
        except Exception:
            logger.warning("Gmail: failed to fetch message %s", msg_id)

    return messages, new_history_id


# ── CLI setup helper ─────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    print("Gmail OAuth2 Setup")
    print("=" * 40)
    svc = build_gmail_service()
    if svc is None:
        print("ERROR: Set GMAIL_CREDENTIALS_FILE in .env first")
        sys.exit(1)

    profile = svc.users().getProfile(userId="me").execute()
    print(f"Authenticated as: {profile['emailAddress']}")
    print(f"Current historyId: {profile['historyId']}")

    # Save initial state
    save_gmail_state({"history_id": profile["historyId"]})
    print(f"State saved to {settings.GMAIL_STATE_FILE}")
