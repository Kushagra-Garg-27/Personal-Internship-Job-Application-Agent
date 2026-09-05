"""Business logic for Application records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from core.models.opportunity import Application
from core.repositories import application_repo


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
        status="pending",
        adapter_name=adapter_name,
    )


def update_application_status(
    session: Session,
    application_id: int,
    *,
    status: str,
    confirmation_ref: str | None = None,
    submitted_at: datetime | None = None,
    notes: str | None = None,
) -> Application | None:
    """Update the status and optional fields on an application.

    For example, marking it ``"submitted"`` with a confirmation code
    after Phase 9's Worker succeeds.
    """
    update_kwargs: dict[str, Any] = {"status": status}
    if confirmation_ref is not None:
        update_kwargs["confirmation_ref"] = confirmation_ref
    if submitted_at is not None:
        update_kwargs["submitted_at"] = submitted_at
    if notes is not None:
        update_kwargs["notes"] = notes

    return application_repo.update_application(session, application_id, **update_kwargs)
