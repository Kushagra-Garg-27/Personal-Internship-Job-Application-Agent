"""Business logic for profile management.

Orchestrates the profile repository and handles nested child-entity
creation (education, skills, links) as part of profile create/update.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from core.models.profile import Profile
from core.repositories import profile_repo


def create_profile_with_children(
    session: Session,
    *,
    education: list[dict[str, Any]] | None = None,
    skills: list[dict[str, Any]] | None = None,
    links: list[dict[str, Any]] | None = None,
    **profile_fields: Any,
) -> Profile:
    """Create a profile and optionally attach education/skills/links in one call.

    Parameters
    ----------
    session:
        Active SQLAlchemy session (caller manages commit/rollback).
    education:
        List of dicts with keys matching ``ProfileEducation`` columns.
    skills:
        List of dicts with keys matching ``ProfileSkill`` columns.
    links:
        List of dicts with keys matching ``ProfileLink`` columns.
    **profile_fields:
        Top-level ``Profile`` column values (``name``, ``email``, etc.).
    """
    profile = profile_repo.create_profile(session, **profile_fields)

    for edu in education or []:
        profile_repo.add_education(session, profile.id, **edu)
    for skill in skills or []:
        profile_repo.add_skill(session, profile.id, **skill)
    for link in links or []:
        profile_repo.add_link(session, profile.id, **link)

    # Refresh to load children into relationships
    session.refresh(profile)
    return profile


def update_profile_with_children(
    session: Session,
    profile_id: int,
    *,
    education: list[dict[str, Any]] | None = None,
    skills: list[dict[str, Any]] | None = None,
    links: list[dict[str, Any]] | None = None,
    **profile_fields: Any,
) -> Profile | None:
    """Update profile fields and optionally *append* new child entities.

    This does NOT replace existing children — it adds to them.  Deletion
    of individual children can be handled via dedicated endpoints later.
    """
    profile = profile_repo.update_profile(session, profile_id, **profile_fields)
    if profile is None:
        return None

    for edu in education or []:
        profile_repo.add_education(session, profile_id, **edu)
    for skill in skills or []:
        profile_repo.add_skill(session, profile_id, **skill)
    for link in links or []:
        profile_repo.add_link(session, profile_id, **link)

    session.refresh(profile)
    return profile
