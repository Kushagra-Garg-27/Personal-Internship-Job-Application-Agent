"""Gmail response poller — background job for recruiter message ingestion.

Polls Gmail for new messages, runs the candidate filter → classification
pipeline → application linker, and persists results.  Uses a separate
state key from the job-alert poller to avoid conflicts.

OAuth token refresh failures are caught and recorded as
``IntegrationHealthEvent`` entries rather than crashing the scheduler.
"""

from __future__ import annotations

import base64
import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from core.config import settings
from core.discovery.gmail_client import (
    build_gmail_service,
    load_gmail_state,
    poll_new_messages,
    save_gmail_state,
)
from core.messaging.candidate_filter import (
    evaluate_candidate_filter,
    extract_domain,
    extract_email_from_sender,
)
from core.messaging.classification_pipeline import classify_message
from core.messaging.linker import link_message_to_application
from core.models.message import IntegrationHealthEvent, RecruiterMessage
from core.notifications import build_health_event, build_message_event, notification_service

logger = logging.getLogger(__name__)

# State key for response poller (separate from alert poller)
STATE_KEY = "response_poller_history_id"


def _extract_headers(msg: dict) -> dict[str, str]:
    """Extract headers as a lowercase-keyed dict from a Gmail message."""
    return {
        h["name"].lower(): h["value"]
        for h in msg.get("payload", {}).get("headers", [])
    }


def _extract_body_preview(msg: dict, max_length: int = 500) -> str:
    """Extract a plain-text body preview from a Gmail message."""
    payload = msg.get("payload", {})

    def _find_text(part: dict) -> str | None:
        mime = part.get("mimeType", "")
        if mime == "text/plain":
            data = part.get("body", {}).get("data", "")
            if data:
                return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        for sub in part.get("parts", []):
            result = _find_text(sub)
            if result:
                return result
        return None

    text = _find_text(payload)
    if text:
        return text[:max_length]

    # Fallback: try snippet
    snippet = msg.get("snippet", "")
    return snippet[:max_length] if snippet else ""


def _get_received_at(msg: dict) -> datetime | None:
    """Parse internalDate from Gmail message to timezone-aware datetime."""
    internal_date = msg.get("internalDate")
    if internal_date:
        try:
            ts = int(internal_date) / 1000  # ms → seconds
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except (ValueError, OSError):
            pass
    return None


def _get_user_email(service: Any) -> str | None:
    """Fetch the authenticated user's email address."""
    try:
        profile = service.users().getProfile(userId="me").execute()
        return profile.get("emailAddress", "").lower()
    except Exception:
        return None


def _get_existing_thread_links(session: Session) -> dict[str, int]:
    """Build a thread_id → application_id lookup from existing linked messages."""
    results = (
        session.query(RecruiterMessage.thread_id, RecruiterMessage.application_id)
        .filter(
            RecruiterMessage.thread_id.isnot(None),
            RecruiterMessage.application_id.isnot(None),
        )
        .all()
    )
    return {row.thread_id: row.application_id for row in results}


def _record_health_event(
    session: Session,
    event_type: str,
    detail: str | None = None,
) -> None:
    """Write an integration health event."""
    event = IntegrationHealthEvent(
        integration_name="gmail_response_poller",
        event_type=event_type,
        detail=detail,
    )
    session.add(event)
    session.flush()

    # Wire Phase 8: alert candidate if integration is degraded / auth failed
    if event_type in ("token_refresh_failure", "auth_expired", "poll_error"):
        try:
            health_notif = build_health_event(event_type=event_type, detail=detail)
            notification_service.dispatch(health_notif, session=session)
        except Exception as exc:
            logger.warning("Failed to dispatch integration health notification: %s", exc)


def run_response_poll(session: Session) -> dict[str, int]:
    """Execute one poll cycle of the recruiter response poller.

    Returns a summary dict with counts of processed, classified, linked, skipped.
    """
    stats = {
        "total_fetched": 0,
        "candidates_passed": 0,
        "classified": 0,
        "linked": 0,
        "skipped_duplicate": 0,
        "skipped_filter": 0,
    }

    # Build service — catch auth failures
    try:
        service = build_gmail_service()
    except Exception as exc:
        error_detail = str(exc)
        logger.error("Response poller: Gmail service build failed: %s", error_detail)
        _record_health_event(
            session,
            "token_refresh_failure",
            f"Failed to build Gmail service: {error_detail}",
        )
        session.commit()
        return stats

    if service is None:
        logger.info("Response poller: Gmail not configured, skipping")
        return stats

    # Get user email for self-send filter
    user_email = _get_user_email(service)

    # Load poller state
    state = load_gmail_state()
    last_id = state.get(STATE_KEY)

    # Poll for new messages
    try:
        messages, new_id = poll_new_messages(service, last_id)
    except Exception as exc:
        error_detail = str(exc)
        error_lower = error_detail.lower()

        # Detect auth/token failures specifically
        if any(kw in error_lower for kw in ["refresh", "token", "auth", "credentials", "invalid_grant"]):
            event_type = "token_refresh_failure"
        else:
            event_type = "poll_error"

        logger.error("Response poller: poll failed: %s", error_detail)
        _record_health_event(session, event_type, error_detail)
        session.commit()
        return stats

    # Persist new history ID
    state[STATE_KEY] = new_id
    save_gmail_state(state)

    stats["total_fetched"] = len(messages)

    if not messages:
        _record_health_event(session, "poll_success", f"No new messages (historyId: {new_id})")
        session.commit()
        return stats

    # Build thread link lookup once
    thread_links = _get_existing_thread_links(session)
    preview_length = settings.RESPONSE_POLLER_BODY_PREVIEW_LENGTH

    for msg in messages:
        gmail_id = msg.get("id", "")

        # Idempotency check
        existing = (
            session.query(RecruiterMessage.id)
            .filter(RecruiterMessage.gmail_id == gmail_id)
            .first()
        )
        if existing:
            stats["skipped_duplicate"] += 1
            continue

        headers = _extract_headers(msg)
        sender = headers.get("from", "")
        subject = headers.get("subject", "")
        thread_id = msg.get("threadId")

        # Candidate filter
        filter_result = evaluate_candidate_filter(
            sender=sender,
            subject=subject,
            user_email=user_email,
            session=session,
        )

        if not filter_result.is_candidate:
            stats["skipped_filter"] += 1
            continue

        stats["candidates_passed"] += 1

        # Extract content
        body_preview = _extract_body_preview(msg, max_length=preview_length)
        email_addr = extract_email_from_sender(sender)
        sender_domain = extract_domain(email_addr)
        received_at = _get_received_at(msg)

        # Classify
        class_result = classify_message(
            subject=subject,
            body_preview=body_preview,
            sender=sender,
        )
        stats["classified"] += 1

        # Link to application
        link_result = link_message_to_application(
            sender_domain=sender_domain,
            subject=subject,
            body_preview=body_preview,
            thread_id=thread_id,
            session=session,
            existing_thread_links=thread_links,
        )

        if link_result.application_id:
            stats["linked"] += 1
            # Update thread link cache for subsequent messages in same batch
            if thread_id:
                thread_links[thread_id] = link_result.application_id

        # Persist
        message = RecruiterMessage(
            gmail_id=gmail_id,
            thread_id=thread_id,
            application_id=link_result.application_id,
            sender=sender,
            sender_domain=sender_domain,
            subject=subject,
            body_preview=body_preview,
            received_at=received_at,
            classification=class_result.classification,
            classification_source=class_result.source,
            classification_confidence=class_result.confidence,
            link_confidence=link_result.confidence,
            raw_headers_json={
                "from": headers.get("from"),
                "to": headers.get("to"),
                "date": headers.get("date"),
                "message-id": headers.get("message-id"),
            },
        )
        session.add(message)
        session.flush()

        # Wire Phase 8: dispatch notification event for classified recruiter reply
        try:
            opp_title = None
            company = None
            if link_result.application_id:
                from core.models.opportunity import Application
                app = session.get(Application, link_result.application_id)
                if app and app.opportunity:
                    opp_title = app.opportunity.title
                    company = app.opportunity.company

            msg_event = build_message_event(
                classification=class_result.classification,
                sender=sender,
                subject=subject,
                body_preview=body_preview,
                opportunity_title=opp_title,
                company=company,
                confidence=class_result.confidence,
                message_id=message.id,
            )
            notification_service.dispatch(msg_event, session=session)
        except Exception as notif_exc:
            logger.warning("Failed to dispatch message notification: %s", notif_exc)

    _record_health_event(
        session,
        "poll_success",
        (
            f"Processed {stats['total_fetched']} messages: "
            f"{stats['candidates_passed']} passed filter, "
            f"{stats['classified']} classified, "
            f"{stats['linked']} linked"
        ),
    )
    session.commit()

    logger.info(
        "Response poller complete: %d fetched, %d classified, %d linked, "
        "%d filtered, %d duplicates",
        stats["total_fetched"],
        stats["classified"],
        stats["linked"],
        stats["skipped_filter"],
        stats["skipped_duplicate"],
    )

    return stats
