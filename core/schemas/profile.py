"""Pydantic schemas for Profile and its child entities."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


# ── Child entity schemas ──────────────────────────────────────────────────


class EducationCreate(BaseModel):
    degree: str
    branch: str
    institution: str
    graduation_year: int | None = None


class EducationResponse(EducationCreate):
    id: int
    model_config = ConfigDict(from_attributes=True)


class SkillCreate(BaseModel):
    skill_name: str
    proficiency: str | None = None


class SkillResponse(SkillCreate):
    id: int
    model_config = ConfigDict(from_attributes=True)


class LinkCreate(BaseModel):
    link_type: str
    url: str


class LinkResponse(LinkCreate):
    id: int
    model_config = ConfigDict(from_attributes=True)


# ── Profile schemas ───────────────────────────────────────────────────────


class ProfileCreate(BaseModel):
    """Input schema for creating a new profile."""

    name: str
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    location_preference: str | None = None
    remote_preference: str | None = None
    salary_floor: int | None = None
    role_types: list[str] | None = None

    # Nested child creation (optional)
    education: list[EducationCreate] | None = None
    skills: list[SkillCreate] | None = None
    links: list[LinkCreate] | None = None


class ProfileUpdate(BaseModel):
    """Input schema for partial profile updates.

    All fields are optional — only provided fields are changed.
    """

    name: str | None = None
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    location_preference: str | None = None
    remote_preference: str | None = None
    salary_floor: int | None = None
    role_types: list[str] | None = None

    # Append new children (does not replace existing)
    education: list[EducationCreate] | None = None
    skills: list[SkillCreate] | None = None
    links: list[LinkCreate] | None = None


class ProfileResponse(BaseModel):
    """Full profile representation returned to clients."""

    id: int
    name: str
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    location: str | None = None
    location_preference: str | None = None
    remote_preference: str | None = None
    salary_floor: int | None = None
    role_types: list[str] | None = None

    education: list[EducationResponse] = []
    skills: list[SkillResponse] = []
    links: list[LinkResponse] = []

    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProfileFieldResponse(BaseModel):
    """Response for single-field queries."""

    field_name: str
    value: Any
