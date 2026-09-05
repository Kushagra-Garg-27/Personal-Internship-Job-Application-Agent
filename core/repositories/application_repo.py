"""Data-access layer for Application records (submission attempts)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from core.models.opportunity import Application


def get_next_attempt_number(session: Session, opportunity_id: int) -> int:
    """Return the next attempt number for an opportunity's applications."""
    stmt = select(func.coalesce(func.max(Application.attempt_number), 0)).where(
        Application.opportunity_id == opportunity_id
    )
    current_max: int = session.scalar(stmt) or 0
    return current_max + 1


def create_application(session: Session, **kwargs: Any) -> Application:
    """Insert a new application record."""
    app = Application(**kwargs)
    session.add(app)
    session.flush()
    return app


def get_application(session: Session, application_id: int) -> Application | None:
    """Fetch an application by primary key."""
    return session.get(Application, application_id)


def list_by_opportunity(session: Session, opportunity_id: int) -> list[Application]:
    """Return all application attempts for an opportunity, ordered by attempt."""
    stmt = (
        select(Application)
        .where(Application.opportunity_id == opportunity_id)
        .order_by(Application.attempt_number.asc())
    )
    return list(session.scalars(stmt).all())


def update_application(
    session: Session, application_id: int, **kwargs: Any
) -> Application | None:
    """Update fields on an existing application record."""
    app = session.get(Application, application_id)
    if app is None:
        return None
    for key, value in kwargs.items():
        if hasattr(app, key):
            setattr(app, key, value)
    session.flush()
    return app
