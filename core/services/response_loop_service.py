"""Recruiter response loop service layer (Phase 10).

Per §5.4 and §7 of Job_Agent_Architecture_v2_Revised.md:
- Closes the loop: Classify → Notify → Suggested Reply (Gemini) → Human Review/Edit → [YOU SEND] → Automatic Status Update
- Guarantees "you send": creates a draft in Gmail, never sends programmatically.
- Guarantees human approval gate: status updates NEVER happen autonomously upon message receipt;
  only upon explicit approve or acknowledge action.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from core.discovery.gmail_client import build_gmail_service
from core.messaging.draft_service import create_gmail_draft
from core.messaging.reply_drafter import ReplyDrafter, is_reply_bearing
from core.models.message import RecruiterMessage
from core.models.opportunity import Application, Opportunity
from core.models.profile import Profile
from core.repositories import message_repo
from core.services import opportunity_service
from core.status import OpportunityStatus

logger = logging.getLogger(__name__)


def generate_reply_draft(
    db: Session,
    message_id: int,
    *,
    drafter: ReplyDrafter | None = None,
) -> RecruiterMessage:
    """Generate and store an AI-suggested draft reply for a recruiter message.

    Parameters
    ----------
    db : Session
        Database session.
    message_id : int
        Primary key of the RecruiterMessage.
    drafter : ReplyDrafter | None, optional
        Custom reply drafter instance (useful for dependency injection in tests).

    Returns
    -------
    RecruiterMessage
        The updated message record with ``suggested_reply`` populated.

    Raises
    ------
    ValueError
        If the message is not found or its classification is not reply-bearing.
    """
    msg = message_repo.get_message(db, message_id)
    if msg is None:
        raise ValueError(f"Recruiter message {message_id} not found.")

    classification = (msg.classification or "unclassified").lower().strip()
    if not is_reply_bearing(classification):
        raise ValueError(
            f"Message #{message_id} classified as '{classification}' does not warrant a reply draft."
        )

    # Resolve linked opportunity and application context
    opp: Opportunity | None = None
    app: Application | None = None
    profile: Profile | None = None

    if msg.application_id:
        app = db.query(Application).filter(Application.id == msg.application_id).first()
        if app and app.opportunity:
            opp = app.opportunity
            if opp.profile_id:
                profile = db.query(Profile).filter(Profile.id == opp.profile_id).first()

    if profile is None:
        # Fallback to default/first profile
        profile = db.query(Profile).first()

    drafter_instance = drafter or ReplyDrafter()
    draft_text = drafter_instance.draft_reply(
        message=msg,
        opportunity=opp,
        profile=profile,
        application=app,
    )

    message_repo.update_suggested_reply(db, msg.id, draft_text)
    return msg


def approve_and_create_draft(
    db: Session,
    message_id: int,
    *,
    edited_reply: str | None = None,
    gmail_service: Any = None,
) -> tuple[RecruiterMessage, Opportunity]:
    """Approve a suggested reply, create a draft in Gmail, and update application status.

    Parameters
    ----------
    db : Session
        Database session.
    message_id : int
        ID of the recruiter message being approved.
    edited_reply : str | None, optional
        Human-edited reply text. If None, uses ``msg.suggested_reply``.
    gmail_service : Any, optional
        Active Gmail API client. If None, built via ``build_gmail_service()``.

    Returns
    -------
    tuple[RecruiterMessage, Opportunity]
        The updated message and the linked opportunity with its new status.

    Raises
    ------
    ValueError
        If message is not found, not linked to an application, reply text is empty,
        or Gmail service is unavailable.
    """
    msg = message_repo.get_message(db, message_id)
    if msg is None:
        raise ValueError(f"Recruiter message {message_id} not found.")

    if not msg.application_id:
        raise ValueError(
            f"Message #{message_id} is not linked to an application. Cannot approve reply."
        )

    app = db.query(Application).filter(Application.id == msg.application_id).first()
    if not app or not app.opportunity:
        raise ValueError(
            f"Linked application #{msg.application_id} has no valid opportunity."
        )
    opp = app.opportunity

    final_reply = (edited_reply or msg.suggested_reply or "").strip()
    if not final_reply:
        raise ValueError(
            f"Cannot approve empty reply text for message #{message_id}."
        )

    # 1. Create Gmail draft (NEVER calls send — structural invariant)
    service = gmail_service or build_gmail_service()
    if service is None:
        raise ValueError(
            "Gmail integration is not configured or authenticated. "
            "Please configure GMAIL_CREDENTIALS_FILE to create drafts."
        )

    draft_id = create_gmail_draft(
        service=service,
        to_email=msg.sender,
        subject=msg.subject,
        body_text=final_reply,
        thread_id=msg.thread_id,
    )

    # 2. Determine and execute status transition based on message classification
    classification = (msg.classification or "").lower().strip()
    target_status: OpportunityStatus | None = None

    if classification == "interview_invite":
        target_status = OpportunityStatus.INTERVIEW_SCHEDULED
    elif classification == "offer":
        target_status = OpportunityStatus.OFFER_RECEIVED
    elif classification in ("screening_question", "follow_up"):
        # If in early applied/submitted state, advance to interview_scheduled
        if opp.status in (OpportunityStatus.APPLIED.value, OpportunityStatus.SUBMITTED.value):
            target_status = OpportunityStatus.INTERVIEW_SCHEDULED

    if target_status and opp.status != target_status.value:
        opportunity_service.transition_status(
            session=db,
            opportunity_id=opp.id,
            new_status=target_status,
            reason=f"Human approved reply to recruiter message #{msg.id} ({classification})",
            actor="human_approved_reply",
        )

    # 3. Record message action
    message_repo.record_message_action(
        db=db,
        message_id=msg.id,
        action_taken="approved",
        draft_id=draft_id,
        suggested_reply=final_reply,
    )

    db.flush()
    return msg, opp


def acknowledge_message(
    db: Session,
    message_id: int,
) -> tuple[RecruiterMessage, Opportunity]:
    """Acknowledge a non-reply message (e.g. rejection) and update application status.

    Parameters
    ----------
    db : Session
        Database session.
    message_id : int
        ID of the recruiter message.

    Returns
    -------
    tuple[RecruiterMessage, Opportunity]
        The updated message and the linked opportunity with its updated status.

    Raises
    ------
    ValueError
        If message is not found or not linked to an application.
    """
    msg = message_repo.get_message(db, message_id)
    if msg is None:
        raise ValueError(f"Recruiter message {message_id} not found.")

    if not msg.application_id:
        raise ValueError(
            f"Message #{message_id} is not linked to an application. Cannot acknowledge."
        )

    app = db.query(Application).filter(Application.id == msg.application_id).first()
    if not app or not app.opportunity:
        raise ValueError(
            f"Linked application #{msg.application_id} has no valid opportunity."
        )
    opp = app.opportunity

    classification = (msg.classification or "").lower().strip()
    target_status: OpportunityStatus | None = None

    if classification == "rejection":
        target_status = OpportunityStatus.REJECTED_BY_RECRUITER

    if target_status and opp.status != target_status.value:
        opportunity_service.transition_status(
            session=db,
            opportunity_id=opp.id,
            new_status=target_status,
            reason=f"Human acknowledged recruiter message #{msg.id} ({classification})",
            actor="human_acknowledged_message",
        )

    message_repo.record_message_action(
        db=db,
        message_id=msg.id,
        action_taken="acknowledged",
    )

    db.flush()
    return msg, opp
