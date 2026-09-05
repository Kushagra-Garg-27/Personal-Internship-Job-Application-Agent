"""Data-access layer for Opportunity CRUD and filtered queries."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models.opportunity import Opportunity


def create_opportunity(session: Session, **kwargs: Any) -> Opportunity:
    """Create and persist a new opportunity."""
    opp = Opportunity(**kwargs)
    session.add(opp)
    session.flush()
    return opp


def get_opportunity(session: Session, opportunity_id: int) -> Opportunity | None:
    """Fetch an opportunity by primary key."""
    return session.get(Opportunity, opportunity_id)


def get_by_dedup_hash(session: Session, dedup_hash: str) -> Opportunity | None:
    """Fetch an opportunity by its deduplication hash."""
    stmt = select(Opportunity).where(Opportunity.dedup_hash == dedup_hash)
    return session.scalars(stmt).first()


def list_opportunities(
    session: Session,
    *,
    status: str | None = None,
    tier: str | None = None,
    company: str | None = None,
    source: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Opportunity]:
    """List opportunities with optional filters.

    All filters are AND-combined.
    """
    stmt = select(Opportunity)

    if status is not None:
        stmt = stmt.where(Opportunity.status == status)
    if tier is not None:
        stmt = stmt.where(Opportunity.reliability_tier == tier)
    if company is not None:
        stmt = stmt.where(Opportunity.company.ilike(f"%{company}%"))
    if source is not None:
        stmt = stmt.where(Opportunity.source == source)

    stmt = stmt.order_by(Opportunity.discovered_at.desc()).limit(limit).offset(offset)
    return list(session.scalars(stmt).all())


def update_opportunity(
    session: Session, opportunity_id: int, **kwargs: Any
) -> Opportunity | None:
    """Update specific fields on an opportunity.

    Returns the updated opportunity, or ``None`` if not found.
    Does NOT update ``status`` — use the service-layer transition function.
    """
    opp = session.get(Opportunity, opportunity_id)
    if opp is None:
        return None
    for key, value in kwargs.items():
        if key == "status":
            raise ValueError(
                "Cannot update status directly. Use opportunity_service.transition_status()."
            )
        if hasattr(opp, key):
            setattr(opp, key, value)
    session.flush()
    return opp


def delete_opportunity(session: Session, opportunity_id: int) -> bool:
    """Delete an opportunity and all related records (CASCADE)."""
    opp = session.get(Opportunity, opportunity_id)
    if opp is None:
        return False
    session.delete(opp)
    session.flush()
    return True
