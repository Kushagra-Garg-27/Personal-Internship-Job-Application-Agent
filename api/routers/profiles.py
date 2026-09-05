"""Profile CRUD endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.deps import get_db
from core.repositories import profile_repo
from core.schemas.profile import (
    ProfileCreate,
    ProfileFieldResponse,
    ProfileResponse,
    ProfileUpdate,
)
from core.services import profile_service

router = APIRouter(prefix="/profiles", tags=["profiles"])


@router.post("/", response_model=ProfileResponse, status_code=status.HTTP_201_CREATED)
def create_profile(body: ProfileCreate, db: Session = Depends(get_db)):
    """Create a new named profile with optional education/skills/links."""
    # Check for duplicate name
    if profile_repo.get_profile_by_name(db, body.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Profile with name {body.name!r} already exists.",
        )

    profile_fields = body.model_dump(exclude={"education", "skills", "links"})
    profile = profile_service.create_profile_with_children(
        db,
        education=[e.model_dump() for e in body.education] if body.education else None,
        skills=[s.model_dump() for s in body.skills] if body.skills else None,
        links=[l.model_dump() for l in body.links] if body.links else None,
        **profile_fields,
    )
    db.commit()
    db.refresh(profile)
    return profile


@router.get("/", response_model=list[ProfileResponse])
def list_profiles(db: Session = Depends(get_db)):
    """List all profiles."""
    return profile_repo.list_profiles(db)


@router.get("/{profile_id}", response_model=ProfileResponse)
def get_profile(profile_id: int, db: Session = Depends(get_db)):
    """Get a profile by ID (includes education, skills, links)."""
    profile = profile_repo.get_profile(db, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    return profile


@router.patch("/{profile_id}", response_model=ProfileResponse)
def update_profile(profile_id: int, body: ProfileUpdate, db: Session = Depends(get_db)):
    """Update profile fields. New education/skills/links are appended."""
    update_data = body.model_dump(exclude_unset=True)
    education = update_data.pop("education", None)
    skills = update_data.pop("skills", None)
    links = update_data.pop("links", None)

    profile = profile_service.update_profile_with_children(
        db,
        profile_id,
        education=[e.model_dump() for e in body.education] if education else None,
        skills=[s.model_dump() for s in body.skills] if skills else None,
        links=[l.model_dump() for l in body.links] if links else None,
        **{k: v for k, v in update_data.items() if v is not None},
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="Profile not found.")
    db.commit()
    db.refresh(profile)
    return profile


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_profile(profile_id: int, db: Session = Depends(get_db)):
    """Delete a profile and all associated data."""
    if not profile_repo.delete_profile(db, profile_id):
        raise HTTPException(status_code=404, detail="Profile not found.")
    db.commit()


@router.get("/{profile_id}/field/{field_name}", response_model=ProfileFieldResponse)
def get_profile_field(profile_id: int, field_name: str, db: Session = Depends(get_db)):
    """Query a single profile field by name.

    Supported fields: name, full_name, email, phone, location,
    location_preference, remote_preference, salary_floor, role_types,
    education, skills, links, created_at, updated_at.
    """
    try:
        value = profile_repo.get_profile_field(db, profile_id, field_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if value is None and profile_repo.get_profile(db, profile_id) is None:
        raise HTTPException(status_code=404, detail="Profile not found.")

    # Serialise ORM objects for child collections
    if field_name in ("education", "skills", "links") and value is not None:
        value = [
            {c.name: getattr(item, c.name) for c in item.__table__.columns}
            for item in value
        ]

    return ProfileFieldResponse(field_name=field_name, value=value)
