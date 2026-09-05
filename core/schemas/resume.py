"""Pydantic schemas for Resume endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ResumeResponse(BaseModel):
    """Full resume metadata returned to clients."""

    id: int
    profile_id: int
    version: int
    original_filename: str
    file_size_bytes: int
    parse_status: str
    parsed_text: str | None = None
    is_active: bool
    uploaded_at: datetime

    model_config = ConfigDict(from_attributes=True)
