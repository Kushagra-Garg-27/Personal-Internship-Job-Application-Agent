"""Business logic for Application records."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from core.models.opportunity import Application, Opportunity
from core.repositories import application_repo
from core.status import (
    APPLICATION_ALLOWED_TRANSITIONS,
    ApplicationStatus,
    InvalidApplicationTransitionError,
)

logger = logging.getLogger(__name__)


# These are deliberately bounded values.  They may be exposed through the
# manual-review queue and must never contain browser, DOM, session, or user data.
MANUAL_FINAL_ACTION_REASONS = frozenset({
    "ambiguous_next_control",
    "ambiguous_controls",
    "no_final_control",
    "multiple_final_controls",
    "final_control_disabled",
    "final_control_hidden",
})


def create_application(
    session: Session,
    opportunity_id: int,
    resume_id: int | None = None,
    adapter_name: str | None = None,
) -> Application:
    """Create a new application attempt for an opportunity."""
    attempt_number = application_repo.get_next_attempt_number(session, opportunity_id)
    return application_repo.create_application(
        session,
        opportunity_id=opportunity_id,
        resume_id=resume_id,
        attempt_number=attempt_number,
        status=ApplicationStatus.PENDING.value,
        adapter_name=adapter_name,
    )


def transition_application_status(
    session: Session,
    application_id: int,
    new_status: str | ApplicationStatus,
    *,
    confirmation_ref: str | None = None,
    submitted_at: datetime | None = None,
    notes: str | None = None,
    reason: str | None = None,
) -> Application:
    """Centralized transition mechanism for Application records.

    Validates transition against APPLICATION_ALLOWED_TRANSITIONS, updates
    status and audit metadata atomically, and preserves transactional integrity.

    Raises
    ------
    ValueError
        If the application does not exist or status is unrecognized.
    InvalidApplicationTransitionError
        If the transition violates the application status machine.
    """
    app = session.get(Application, application_id)
    if app is None:
        raise ValueError(f"Application {application_id} not found.")

    try:
        current_enum = ApplicationStatus(app.status)
    except ValueError:
        raise ValueError(f"Application {application_id} has invalid current status {app.status!r}")

    target_status_str = new_status.value if isinstance(new_status, ApplicationStatus) else str(new_status)
    try:
        target_enum = ApplicationStatus(target_status_str)
    except ValueError:
        raise InvalidApplicationTransitionError(app.status, target_status_str)

    # Allow idempotency / same-status field updates, but validate real transitions
    if current_enum != target_enum:
        allowed = APPLICATION_ALLOWED_TRANSITIONS.get(current_enum, set())
        if target_enum not in allowed:
            raise InvalidApplicationTransitionError(current_enum.value, target_enum.value)

    # Perform mutations atomically
    old_status = app.status
    app.status = target_enum.value

    # A current handoff reason is a queue projection, not immutable history.
    # Do not carry it into a later legitimate claimable state.
    if target_enum in {ApplicationStatus.PENDING, ApplicationStatus.FORM_FILLED}:
        app.manual_review_reason = None

    if confirmation_ref is not None:
        app.confirmation_ref = confirmation_ref
    if submitted_at is not None:
        app.submitted_at = submitted_at
    if notes is not None:
        app.notes = notes
    elif reason is not None and not app.notes:
        app.notes = reason

    session.flush()
    return app


def update_application_status(
    session: Session,
    application_id: int,
    *,
    status: str | ApplicationStatus,
    confirmation_ref: str | None = None,
    submitted_at: datetime | None = None,
    notes: str | None = None,
    reason: str | None = None,
) -> Application | None:
    """Update the status and optional fields on an application using the transition validator."""
    app = session.get(Application, application_id)
    if app is None:
        return None
    return transition_application_status(
        session,
        application_id,
        status,
        confirmation_ref=confirmation_ref,
        submitted_at=submitted_at,
        notes=notes,
        reason=reason,
    )


def handoff_claimed_application_to_manual_review(
    session: Session,
    application_id: int,
    *,
    reason: str,
    expected_claimed_by: str,
    expected_submission_claimed_at: datetime,
    actor: str = "worker_submission",
) -> bool:
    """Atomically release the current worker claim into manual review.

    The caller supplies the exact claim identity captured after it won the
    worker claim.  The service never derives that identity from a newly loaded
    row, so a stale worker cannot adopt or release a newer worker's claim.
    Approval/revocation fields, notes, input correspondence evidence, resume
    linkage, and attempt metadata are intentionally untouched.
    """
    if reason not in MANUAL_FINAL_ACTION_REASONS:
        raise ValueError("manual review reason must be a recognized bounded code")
    if not isinstance(expected_claimed_by, str) or not expected_claimed_by.strip():
        raise ValueError("expected claim owner is required")
    if not isinstance(expected_submission_claimed_at, datetime):
        raise ValueError("expected claim timestamp is required")

    claimable_statuses = (
        ApplicationStatus.FORM_FILLED.value,
        ApplicationStatus.PENDING.value,
    )
    result = session.execute(
        update(Application)
        .where(
            Application.id == application_id,
            Application.claimed_by == expected_claimed_by,
            Application.submission_claimed_at == expected_submission_claimed_at,
            Application.submission_claimed_at.is_not(None),
            Application.status.in_(claimable_statuses),
            Application.approved_at.is_not(None),
            or_(
                Application.approval_revoked_at.is_(None),
                Application.approved_at > Application.approval_revoked_at,
            ),
            Application.opportunity.has(
                Opportunity.status == "awaiting_submission"
            ),
        )
        .values(
            status=ApplicationStatus.FAILED.value,
            manual_review_reason=reason,
            submission_claimed_at=None,
            claimed_by=None,
        )
    )
    if result.rowcount != 1:
        return False

    # Both writes are part of the caller's single transaction.  This lookup is
    # only for the opportunity primary key; it never supplies claim identity.
    opportunity_id = session.scalar(
        select(Application.opportunity_id).where(Application.id == application_id)
    )
    if opportunity_id is None:
        raise ValueError(f"Application {application_id} not found.")

    # The queue's existing MANUAL_REVIEW projection derives from these
    # canonical states.  Any exception below is rolled back by the caller.
    from core.services import opportunity_service
    opportunity_service.transition_status(
        session,
        opportunity_id,
        "manual_application_required",
        reason=f"manual_final_action_required:{reason}",
        actor=actor,
    )
    session.flush()
    return True
