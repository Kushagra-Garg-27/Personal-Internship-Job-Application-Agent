"""Business logic for Application records."""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from sqlalchemy.orm import Session

from core.models.opportunity import Application
from core.repositories import application_repo
from core.status import (
    APPLICATION_ALLOWED_TRANSITIONS,
    ApplicationStatus,
    InvalidApplicationTransitionError,
)

logger = logging.getLogger(__name__)


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
