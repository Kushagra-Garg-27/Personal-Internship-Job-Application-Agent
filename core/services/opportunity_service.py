"""Business logic for opportunity management and the status-transition engine.

This module enforces the status machine:  every transition is validated
against ``ALLOWED_TRANSITIONS``, and every change atomically writes to
both ``opportunities.status`` and ``status_history`` in the same flush.

**Never** update ``opportunities.status`` directly — always go through
``transition_status()``.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy.orm import Session

from core.models.opportunity import Application, Opportunity
from core.repositories import application_repo, opportunity_repo, status_history_repo
from core.status import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    OpportunityStatus,
)


# ── Dedup hash computation ────────────────────────────────────────────────


def compute_dedup_hash(company: str, title: str, url: str | None) -> str:
    """Compute a deterministic SHA-256 dedup key from listing identifiers.

    Normalisation: lowercase, stripped whitespace.  URL is included if
    present; if absent, only company+title are hashed.
    """
    parts = [
        company.strip().lower(),
        title.strip().lower(),
    ]
    if url:
        # Normalise: strip trailing slashes and whitespace
        parts.append(url.strip().lower().rstrip("/"))
    raw = "|".join(parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ── Opportunity creation / upsert ─────────────────────────────────────────


def create_opportunity(
    session: Session,
    *,
    title: str,
    company: str,
    url: str | None = None,
    reason: str | None = "discovery",
    actor: str | None = "system",
    **extra_fields: Any,
) -> Opportunity:
    """Create a new opportunity with ``status=discovered`` and log the initial
    transition in ``status_history``.

    The ``dedup_hash`` is computed automatically from company+title+url.
    """
    dedup_hash = compute_dedup_hash(company, title, url)

    opp = opportunity_repo.create_opportunity(
        session,
        dedup_hash=dedup_hash,
        status=OpportunityStatus.DISCOVERED,
        title=title,
        company=company,
        url=url,
        **extra_fields,
    )

    # Initial status-history entry (old_status is None for creation)
    status_history_repo.create_entry(
        session,
        opportunity_id=opp.id,
        old_status=None,
        new_status=OpportunityStatus.DISCOVERED,
        reason=reason,
        actor=actor,
    )

    return opp


def upsert_opportunity(
    session: Session,
    *,
    title: str,
    company: str,
    url: str | None = None,
    **extra_fields: Any,
) -> tuple[Opportunity, bool]:
    """Insert or update an opportunity, keyed on ``dedup_hash``.

    Returns ``(opportunity, created)`` where *created* is ``True`` if a
    new row was inserted, ``False`` if an existing one was updated.
    """
    dedup_hash = compute_dedup_hash(company, title, url)
    existing = opportunity_repo.get_by_dedup_hash(session, dedup_hash)

    if existing is not None:
        # Update mutable fields (never status — that goes through transition)
        for key, value in extra_fields.items():
            if key != "status" and hasattr(existing, key):
                setattr(existing, key, value)
        # Also update title/company/url in case casing changed
        existing.title = title
        existing.company = company
        if url is not None:
            existing.url = url
        session.flush()
        return existing, False

    opp = create_opportunity(
        session, title=title, company=company, url=url, **extra_fields
    )
    return opp, True


# ── Status-transition engine ─────────────────────────────────────────────


def transition_status(
    session: Session,
    opportunity_id: int,
    new_status: str | OpportunityStatus,
    *,
    reason: str | None = None,
    actor: str | None = None,
) -> Opportunity:
    """Transition an opportunity to a new status.

    Validates the transition against ``ALLOWED_TRANSITIONS``, then
    atomically updates ``opportunities.status`` and inserts a
    ``status_history`` row in the same flush.

    Raises
    ------
    ValueError
        If the opportunity does not exist.
    InvalidTransitionError
        If the transition is not allowed by the status machine.
    """
    opp = opportunity_repo.get_opportunity(session, opportunity_id)
    if opp is None:
        raise ValueError(f"Opportunity {opportunity_id} not found.")

    # Normalise to enum
    new_status_enum = OpportunityStatus(new_status)
    current_enum = OpportunityStatus(opp.status)

    # Validate transition
    allowed = ALLOWED_TRANSITIONS.get(current_enum, set())
    if new_status_enum not in allowed:
        raise InvalidTransitionError(opp.status, new_status_enum.value)

    old_status = opp.status

    # Atomic update: status column + history row in one flush
    opp.status = new_status_enum.value
    status_history_repo.create_entry(
        session,
        opportunity_id=opp.id,
        old_status=old_status,
        new_status=new_status_enum.value,
        reason=reason,
        actor=actor,
    )
    session.flush()

    return opp


# ── Pre-write primitive for Phase 9 ──────────────────────────────────────


def mark_submission_attempted(
    session: Session,
    opportunity_id: int,
    resume_id: int | None = None,
    *,
    adapter_name: str | None = None,
) -> Application:
    """Record a submission attempt BEFORE the actual risky action.

    This is the idempotency/fail-closed primitive from §5.7: every
    automated action writes to the DB before attempting the risky step.
    Phase 9's Worker will call this before launching Playwright.

    The application is created with ``status="pending"`` and flushed
    immediately to ensure durability even if a crash follows.
    """
    attempt_number = application_repo.get_next_attempt_number(session, opportunity_id)

    app = application_repo.create_application(
        session,
        opportunity_id=opportunity_id,
        resume_id=resume_id,
        attempt_number=attempt_number,
        status="pending",
        adapter_name=adapter_name,
    )

    # Flush immediately — the whole point is that this write is durable
    # before any risky downstream action.
    session.flush()

    return app
