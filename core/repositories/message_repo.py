"""Repository layer for recruiter messages and integration health (Phase 7)."""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.models.message import IntegrationHealthEvent, RecruiterMessage


# ── RecruiterMessage queries ─────────────────────────────────────────────


def get_message(db: Session, message_id: int) -> RecruiterMessage | None:
    """Fetch a single message by primary key."""
    return db.query(RecruiterMessage).filter(RecruiterMessage.id == message_id).first()


def get_message_by_gmail_id(db: Session, gmail_id: str) -> RecruiterMessage | None:
    """Fetch a message by its Gmail ID (idempotency check)."""
    return (
        db.query(RecruiterMessage)
        .filter(RecruiterMessage.gmail_id == gmail_id)
        .first()
    )


def list_messages(
    db: Session,
    *,
    classification: str | None = None,
    application_id: int | None = None,
    linked: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[RecruiterMessage], int]:
    """List messages with optional filters. Returns (items, total_count)."""
    query = db.query(RecruiterMessage)

    if classification:
        query = query.filter(RecruiterMessage.classification == classification)

    if application_id is not None:
        query = query.filter(RecruiterMessage.application_id == application_id)

    if linked is True:
        query = query.filter(RecruiterMessage.application_id.isnot(None))
    elif linked is False:
        query = query.filter(RecruiterMessage.application_id.is_(None))

    total = query.count()

    items = (
        query.order_by(RecruiterMessage.received_at.desc().nullslast())
        .offset(offset)
        .limit(limit)
        .all()
    )

    return items, total


def get_message_stats(db: Session) -> dict:
    """Get aggregate message statistics."""
    total = db.query(func.count(RecruiterMessage.id)).scalar() or 0

    # By classification
    class_counts = (
        db.query(RecruiterMessage.classification, func.count(RecruiterMessage.id))
        .group_by(RecruiterMessage.classification)
        .all()
    )
    by_classification = {c or "unclassified": count for c, count in class_counts}

    linked = (
        db.query(func.count(RecruiterMessage.id))
        .filter(RecruiterMessage.application_id.isnot(None))
        .scalar()
        or 0
    )
    unlinked = total - linked

    return {
        "total": total,
        "by_classification": by_classification,
        "linked": linked,
        "unlinked": unlinked,
    }


# ── IntegrationHealthEvent queries ───────────────────────────────────────


def list_health_events(
    db: Session,
    *,
    integration_name: str | None = None,
    limit: int = 20,
) -> tuple[list[IntegrationHealthEvent], int]:
    """List recent health events, newest first."""
    query = db.query(IntegrationHealthEvent)

    if integration_name:
        query = query.filter(
            IntegrationHealthEvent.integration_name == integration_name
        )

    total = query.count()
    items = (
        query.order_by(IntegrationHealthEvent.occurred_at.desc())
        .limit(limit)
        .all()
    )

    return items, total


def get_latest_health_event(
    db: Session, integration_name: str
) -> IntegrationHealthEvent | None:
    """Get the most recent health event for an integration."""
    return (
        db.query(IntegrationHealthEvent)
        .filter(IntegrationHealthEvent.integration_name == integration_name)
        .order_by(IntegrationHealthEvent.occurred_at.desc())
        .first()
    )
