"""Data-access layer for ScamContentSignature CRUD and hash lookup."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models.scam_signature import ScamContentSignature


def create_signature(
    session: Session,
    content_hash: str,
    *,
    opportunity_id: int | None = None,
    rule_name: str | None = None,
    confirmed_scam: bool = True,
    notes: str | None = None,
) -> ScamContentSignature:
    """Persist a new fraudulent content signature hash."""
    sig = ScamContentSignature(
        content_hash=content_hash,
        opportunity_id=opportunity_id,
        rule_name=rule_name,
        confirmed_scam=confirmed_scam,
        notes=notes,
    )
    session.add(sig)
    session.flush()
    return sig


def get_by_hash(session: Session, content_hash: str) -> ScamContentSignature | None:
    """Look up a content signature by its SHA-256 hash."""
    stmt = select(ScamContentSignature).where(
        ScamContentSignature.content_hash == content_hash
    )
    return session.scalars(stmt).first()


def is_confirmed_scam(session: Session, content_hash: str) -> bool:
    """Return True if content hash matches a confirmed scam signature."""
    stmt = select(ScamContentSignature).where(
        ScamContentSignature.content_hash == content_hash,
        ScamContentSignature.confirmed_scam.is_(True),
    )
    return session.scalars(stmt).first() is not None


def list_signatures(
    session: Session,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[ScamContentSignature]:
    """List recorded scam content signatures."""
    stmt = (
        select(ScamContentSignature)
        .order_by(ScamContentSignature.first_seen_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(stmt).all())
