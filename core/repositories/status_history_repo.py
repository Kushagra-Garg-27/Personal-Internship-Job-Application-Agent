"""Data-access layer for the StatusHistory audit trail."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models.opportunity import StatusHistory


def create_entry(
    session: Session,
    *,
    opportunity_id: int,
    old_status: str | None,
    new_status: str,
    reason: str | None = None,
    actor: str | None = None,
) -> StatusHistory:
    """Insert an immutable status-transition record."""
    entry = StatusHistory(
        opportunity_id=opportunity_id,
        old_status=old_status,
        new_status=new_status,
        reason=reason,
        actor=actor,
    )
    session.add(entry)
    session.flush()
    return entry


def get_history(session: Session, opportunity_id: int) -> list[StatusHistory]:
    """Return the full status history for an opportunity, ordered by time."""
    stmt = (
        select(StatusHistory)
        .where(StatusHistory.opportunity_id == opportunity_id)
        .order_by(StatusHistory.changed_at.asc())
    )
    return list(session.scalars(stmt).all())
