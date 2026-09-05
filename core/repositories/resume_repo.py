"""Data-access layer for Resume versioning and retrieval."""

from __future__ import annotations

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from core.models.resume import Resume


def get_next_version(session: Session, profile_id: int) -> int:
    """Return the next version number for a profile's resumes.

    If the profile has no resumes yet, returns 1.
    """
    stmt = select(func.coalesce(func.max(Resume.version), 0)).where(
        Resume.profile_id == profile_id
    )
    current_max: int = session.scalar(stmt) or 0
    return current_max + 1


def create_resume(session: Session, **kwargs) -> Resume:
    """Insert a new resume record (does NOT handle file I/O or parsing)."""
    resume = Resume(**kwargs)
    session.add(resume)
    session.flush()
    return resume


def get_resume(session: Session, resume_id: int) -> Resume | None:
    """Fetch a single resume by primary key."""
    return session.get(Resume, resume_id)


def get_active_resume(session: Session, profile_id: int) -> Resume | None:
    """Return the currently active resume for a profile, or None."""
    stmt = select(Resume).where(
        Resume.profile_id == profile_id,
        Resume.is_active.is_(True),
    )
    return session.scalars(stmt).first()


def set_active_resume(session: Session, profile_id: int, resume_id: int) -> Resume | None:
    """Mark *resume_id* as active and deactivate all others for the profile.

    Returns the newly activated resume, or ``None`` if *resume_id* doesn't
    exist or doesn't belong to the given profile.
    """
    target = session.get(Resume, resume_id)
    if target is None or target.profile_id != profile_id:
        return None

    # Deactivate all resumes for this profile
    stmt = select(Resume).where(
        Resume.profile_id == profile_id,
        Resume.is_active.is_(True),
    )
    for r in session.scalars(stmt).all():
        r.is_active = False

    target.is_active = True
    session.flush()
    return target


def list_resume_versions(session: Session, profile_id: int) -> list[Resume]:
    """Return all resume versions for a profile, ordered by version desc."""
    stmt = (
        select(Resume)
        .where(Resume.profile_id == profile_id)
        .order_by(Resume.version.desc())
    )
    return list(session.scalars(stmt).all())
