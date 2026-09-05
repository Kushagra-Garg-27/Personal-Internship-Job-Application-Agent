"""Data-access layer for Profile and its child entities."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models.profile import Profile, ProfileEducation, ProfileLink, ProfileSkill


# ── Profile CRUD ──────────────────────────────────────────────────────────


def create_profile(session: Session, **kwargs: Any) -> Profile:
    """Create and persist a new profile.

    Accepts top-level profile fields as keyword arguments.  Child entities
    (education, skills, links) should be added separately via their own
    helpers.
    """
    profile = Profile(**kwargs)
    session.add(profile)
    session.flush()
    return profile


def get_profile(session: Session, profile_id: int) -> Profile | None:
    """Fetch a profile by primary key (eager-loads children via selectin)."""
    return session.get(Profile, profile_id)


def get_profile_by_name(session: Session, name: str) -> Profile | None:
    """Fetch a profile by its unique name."""
    stmt = select(Profile).where(Profile.name == name)
    return session.scalars(stmt).first()


def list_profiles(session: Session) -> list[Profile]:
    """Return all profiles, ordered by creation date."""
    stmt = select(Profile).order_by(Profile.created_at)
    return list(session.scalars(stmt).all())


def update_profile(session: Session, profile_id: int, **kwargs: Any) -> Profile | None:
    """Update specific fields on an existing profile.

    Returns the updated profile, or ``None`` if the profile does not exist.
    """
    profile = session.get(Profile, profile_id)
    if profile is None:
        return None
    for key, value in kwargs.items():
        if hasattr(profile, key):
            setattr(profile, key, value)
    session.flush()
    return profile


def delete_profile(session: Session, profile_id: int) -> bool:
    """Delete a profile and all its children (CASCADE). Returns success."""
    profile = session.get(Profile, profile_id)
    if profile is None:
        return False
    session.delete(profile)
    session.flush()
    return True


# ── Field-level access ────────────────────────────────────────────────────

# Fields that live directly on the Profile table.
_PROFILE_DIRECT_FIELDS = {
    "name", "full_name", "email", "phone", "location",
    "location_preference", "remote_preference", "salary_floor",
    "role_types", "created_at", "updated_at",
}


def get_profile_field(session: Session, profile_id: int, field_name: str) -> Any:
    """Return a single field's value from a profile.

    For child collections (``education``, ``skills``, ``links``), returns the
    full list of ORM objects.

    Raises ``ValueError`` if the field name is not recognised.
    """
    profile = session.get(Profile, profile_id)
    if profile is None:
        return None

    if field_name in _PROFILE_DIRECT_FIELDS:
        return getattr(profile, field_name)

    if field_name == "education":
        return profile.education
    if field_name == "skills":
        return profile.skills
    if field_name == "links":
        return profile.links

    raise ValueError(f"Unknown profile field: {field_name!r}")


# ── Child entity helpers ──────────────────────────────────────────────────


def add_education(session: Session, profile_id: int, **kwargs: Any) -> ProfileEducation:
    """Add an education entry to a profile."""
    edu = ProfileEducation(profile_id=profile_id, **kwargs)
    session.add(edu)
    session.flush()
    return edu


def add_skill(session: Session, profile_id: int, **kwargs: Any) -> ProfileSkill:
    """Add a skill entry to a profile."""
    skill = ProfileSkill(profile_id=profile_id, **kwargs)
    session.add(skill)
    session.flush()
    return skill


def add_link(session: Session, profile_id: int, **kwargs: Any) -> ProfileLink:
    """Add a link entry to a profile."""
    link = ProfileLink(profile_id=profile_id, **kwargs)
    session.add(link)
    session.flush()
    return link
