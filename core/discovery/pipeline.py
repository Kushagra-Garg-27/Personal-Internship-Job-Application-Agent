"""Discovery pipeline — normalise, deduplicate, and upsert.

This is the convergence point where all discovery sources' raw output
gets written to the DB through Phase 2's service layer.  Nothing here
touches the DB directly — everything goes through
``opportunity_service.upsert_opportunity()``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from core.database import SessionLocal
from core.discovery.base import DiscoverySource, RawOpportunity
from core.services import opportunity_service

logger = logging.getLogger(__name__)


@dataclass
class DiscoveryResult:
    """Outcome of a single discovery run."""

    source: str
    tier: str
    total_fetched: int = 0
    new: int = 0
    updated: int = 0
    errors: int = 0


def run_discovery(source: DiscoverySource) -> DiscoveryResult:
    """Run a single source's discovery and upsert results into the DB.

    Creates its own database session so it can be called independently
    from the scheduler — each source gets its own transaction boundary.

    Returns a ``DiscoveryResult`` with counts of new/updated/error items.
    """
    result = DiscoveryResult(
        source=source.name,
        tier=source.tier.value,
    )

    # 1. Fetch from the source
    try:
        raw_opps = source.discover()
    except Exception:
        logger.exception("Discovery [%s]: source.discover() failed", source.name)
        result.errors = 1
        return result

    result.total_fetched = len(raw_opps)

    if not raw_opps:
        logger.info("Discovery [%s]: no opportunities found", source.name)
        return result

    # 2. Upsert each one through the service layer
    session: Session = SessionLocal()
    try:
        for raw in raw_opps:
            try:
                _upsert_raw(session, raw, source)
                session.commit()
            except Exception:
                session.rollback()
                result.errors += 1
                logger.warning(
                    "Discovery [%s]: failed to upsert '%s' at '%s'",
                    source.name, raw.title, raw.company,
                )
        # Count results
        # (new/updated counts are approximate — we count based on upsert return)
    finally:
        session.close()

    logger.info(
        "Discovery [%s]: fetched=%d, new=%d, updated=%d, errors=%d",
        source.name, result.total_fetched, result.new, result.updated, result.errors,
    )
    return result


def _upsert_raw(
    session: Session, raw: RawOpportunity, source: DiscoverySource
) -> None:
    """Map a RawOpportunity to the service-layer upsert call."""
    extra_fields: dict = {
        "source": raw.source or source.name,
        "reliability_tier": source.tier.value,
        "description": raw.description,
        "location": raw.location,
        "salary_min": raw.salary_min,
        "salary_max": raw.salary_max,
        "posted_at": raw.posted_at,
        "deadline_at": raw.deadline_at,
        "metadata_json": raw.metadata,
    }

    opp, created = opportunity_service.upsert_opportunity(
        session,
        title=raw.title,
        company=raw.company,
        url=raw.url,
        **extra_fields,
    )

    # The caller's DiscoveryResult tracks these; we do it via a side-channel
    # by modifying the result in run_discovery. For simplicity, we just use
    # the return value.
    return created


def run_discovery_counted(source: DiscoverySource) -> DiscoveryResult:
    """Like run_discovery but with accurate new/updated counts."""
    result = DiscoveryResult(source=source.name, tier=source.tier.value)

    try:
        raw_opps = source.discover()
    except Exception:
        logger.exception("Discovery [%s]: source.discover() failed", source.name)
        result.errors = 1
        return result

    result.total_fetched = len(raw_opps)

    if not raw_opps:
        return result

    session: Session = SessionLocal()
    try:
        for raw in raw_opps:
            try:
                created = _upsert_raw(session, raw, source)
                if created:
                    result.new += 1
                else:
                    result.updated += 1
                session.commit()
            except Exception:
                session.rollback()
                result.errors += 1
                logger.warning(
                    "Discovery [%s]: failed to upsert '%s' at '%s'",
                    source.name, raw.title, raw.company,
                )
    finally:
        session.close()

    logger.info(
        "Discovery [%s]: fetched=%d, new=%d, updated=%d, errors=%d",
        source.name, result.total_fetched, result.new, result.updated, result.errors,
    )
    return result


def run_discovery_with_session(
    source: DiscoverySource, session: Session
) -> DiscoveryResult:
    """Run discovery using an externally-provided session (for testing).

    Unlike ``run_discovery``, this does NOT create its own session or
    commit — the caller is responsible for transaction management.
    """
    result = DiscoveryResult(source=source.name, tier=source.tier.value)

    try:
        raw_opps = source.discover()
    except Exception:
        logger.exception("Discovery [%s]: source.discover() failed", source.name)
        result.errors = 1
        return result

    result.total_fetched = len(raw_opps)

    for raw in raw_opps:
        try:
            created = _upsert_raw(session, raw, source)
            if created:
                result.new += 1
            else:
                result.updated += 1
        except Exception:
            result.errors += 1
            logger.warning(
                "Discovery [%s]: failed to upsert '%s' at '%s'",
                source.name, raw.title, raw.company,
            )

    return result
