"""Read-only API endpoints for recruiter messages and integration health (Phase 7)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from api.deps import get_db
from core.repositories import message_repo
from core.schemas.message import (
    AcknowledgeMessageResponse,
    ApproveReplyRequest,
    ApproveReplyResponse,
    IntegrationHealthListResponse,
    IntegrationHealthResponse,
    MessageListResponse,
    MessageResponse,
    MessageStatsResponse,
)

router = APIRouter(prefix="/messages", tags=["messages"])


def _enrich_message(msg) -> dict:
    """Add opportunity info and response loop status from linked application."""
    data = {
        "id": msg.id,
        "gmail_id": msg.gmail_id,
        "thread_id": msg.thread_id,
        "application_id": msg.application_id,
        "sender": msg.sender,
        "sender_domain": msg.sender_domain,
        "subject": msg.subject,
        "body_preview": msg.body_preview,
        "received_at": msg.received_at,
        "classification": msg.classification,
        "classification_source": msg.classification_source,
        "classification_confidence": msg.classification_confidence,
        "link_confidence": msg.link_confidence,
        "created_at": msg.created_at,
        "updated_at": msg.updated_at,
        "suggested_reply": msg.suggested_reply,
        "draft_id": msg.draft_id,
        "action_taken": msg.action_taken,
        "action_taken_at": msg.action_taken_at,
        "opportunity_id": None,
        "opportunity_title": None,
        "opportunity_company": None,
        "opportunity_status": None,
    }

    if msg.application and msg.application.opportunity:
        opp = msg.application.opportunity
        data["opportunity_id"] = opp.id
        data["opportunity_title"] = opp.title
        data["opportunity_company"] = opp.company
        data["opportunity_status"] = opp.status

    return data


@router.get("/stats", response_model=MessageStatsResponse)
def get_message_stats(db: Session = Depends(get_db)):
    """Aggregate statistics for recruiter messages."""
    return message_repo.get_message_stats(db)


@router.get("", response_model=MessageListResponse)
def list_messages(
    classification: str | None = Query(None, description="Filter by classification"),
    application_id: int | None = Query(None, description="Filter by application ID"),
    linked: bool | None = Query(None, description="Filter by linked status"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List recruiter messages with optional filters."""
    items, total = message_repo.list_messages(
        db,
        classification=classification,
        application_id=application_id,
        linked=linked,
        limit=limit,
        offset=offset,
    )

    return MessageListResponse(
        items=[MessageResponse(**_enrich_message(m)) for m in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{message_id}", response_model=MessageResponse)
def get_message(message_id: int, db: Session = Depends(get_db)):
    """Get a single recruiter message by ID."""
    msg = message_repo.get_message(db, message_id)
    if msg is None:
        raise HTTPException(status_code=404, detail="Message not found.")
    return MessageResponse(**_enrich_message(msg))


@router.post("/{message_id}/draft-reply", response_model=MessageResponse)
def generate_draft_reply(message_id: int, db: Session = Depends(get_db)):
    """Generate or retrieve an AI-drafted reply for a reply-bearing recruiter message."""
    from core.services import response_loop_service

    try:
        msg = response_loop_service.generate_reply_draft(db, message_id)
        db.commit()
        return MessageResponse(**_enrich_message(msg))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to generate draft reply: {exc}")


@router.post("/{message_id}/approve-reply", response_model=ApproveReplyResponse)
def approve_reply(
    message_id: int,
    req: ApproveReplyRequest | None = None,
    db: Session = Depends(get_db),
):
    """Approve a reply (with optional human edits), create a draft in Gmail, and update application status."""
    from core.services import response_loop_service

    edited_reply = req.edited_reply if req else None
    try:
        msg, opp = response_loop_service.approve_and_create_draft(
            db, message_id, edited_reply=edited_reply
        )
        db.commit()
        return ApproveReplyResponse(
            message=MessageResponse(**_enrich_message(msg)),
            opportunity_id=opp.id,
            opportunity_status=opp.status,
            draft_id=msg.draft_id or "",
            detail="Draft successfully created in your Gmail Drafts folder. Open Gmail to send.",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to approve reply: {exc}")


@router.post("/{message_id}/acknowledge", response_model=AcknowledgeMessageResponse)
def acknowledge_message(message_id: int, db: Session = Depends(get_db)):
    """Acknowledge a non-reply message (e.g. rejection) and update opportunity status."""
    from core.services import response_loop_service

    try:
        msg, opp = response_loop_service.acknowledge_message(db, message_id)
        db.commit()
        return AcknowledgeMessageResponse(
            message=MessageResponse(**_enrich_message(msg)),
            opportunity_id=opp.id,
            opportunity_status=opp.status,
            detail="Message acknowledged and application status updated.",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to acknowledge message: {exc}")



# ── Integration Health ───────────────────────────────────────────────────

health_router = APIRouter(prefix="/integration-health", tags=["integration-health"])


@health_router.get("", response_model=IntegrationHealthListResponse)
def list_integration_health(
    integration_name: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List recent integration health events."""
    items, total = message_repo.list_health_events(
        db, integration_name=integration_name, limit=limit
    )
    return IntegrationHealthListResponse(
        items=[IntegrationHealthResponse.model_validate(e) for e in items],
        total=total,
    )
