"""Gmail Draft Creation Service (Phase 10: Recruiter Response Loop).

Per §5.4 and §7 of Job_Agent_Architecture_v2_Revised.md:
- Hard Architectural Invariant: "Any temptation to let it auto-send — resist it."
- This service ONLY creates drafts in the user's Gmail Drafts folder.
- Under NO circumstance does this service or module call send().
- The user physically opens Gmail and clicks Send themselves ("you send").
"""

from __future__ import annotations

import base64
from email.message import EmailMessage
import logging
from typing import Any

logger = logging.getLogger(__name__)


def create_gmail_draft(
    service: Any,
    to_email: str,
    subject: str | None,
    body_text: str,
    thread_id: str | None = None,
) -> str:
    """Create a draft response in the user's Gmail Drafts folder.

    Parameters
    ----------
    service : googleapiclient.discovery.Resource
        Authenticated Gmail API service instance with ``gmail.compose`` scope.
    to_email : str
        Recipient email address (the recruiter's address).
    subject : str | None
        Email subject. If missing or not prefixed with "Re: ", "Re: " is added.
    body_text : str
        The final (possibly human-edited) response body text.
    thread_id : str | None, optional
        Gmail thread ID to preserve conversation threading.

    Returns
    -------
    str
        The created Gmail draft ID.

    Raises
    ------
    ValueError
        If service, to_email, or body_text is empty.
    RuntimeError
        If draft creation via Gmail API fails.
    """
    if service is None:
        raise ValueError("Cannot create Gmail draft without an active Gmail service instance.")
    if not to_email or not to_email.strip():
        raise ValueError("Cannot create Gmail draft without recipient email.")
    if not body_text or not body_text.strip():
        raise ValueError("Cannot create Gmail draft with empty body text.")

    # Format subject
    clean_subject = (subject or "Job Application Follow-up").strip()
    if not clean_subject.lower().startswith("re:"):
        clean_subject = f"Re: {clean_subject}"

    # Construct standard RFC 2822 email message
    msg = EmailMessage()
    msg.set_content(body_text.strip())
    msg["To"] = to_email.strip()
    msg["Subject"] = clean_subject

    raw_bytes = msg.as_bytes()
    encoded_message = base64.urlsafe_b64encode(raw_bytes).decode("utf-8")

    draft_body: dict[str, Any] = {
        "message": {
            "raw": encoded_message,
        }
    }
    if thread_id:
        draft_body["message"]["threadId"] = thread_id

    try:
        draft = (
            service.users()
            .drafts()
            .create(userId="me", body=draft_body)
            .execute()
        )
        draft_id = draft.get("id")
        logger.info(
            "Created Gmail draft %s for recipient %s (thread: %s)",
            draft_id,
            to_email,
            thread_id,
        )
        return str(draft_id)
    except Exception as exc:
        logger.exception("Failed to create Gmail draft: %s", exc)
        raise RuntimeError(f"Failed to create Gmail draft: {exc}") from exc
