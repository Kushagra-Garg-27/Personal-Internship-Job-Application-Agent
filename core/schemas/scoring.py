"""Pydantic schemas for funnel scoring verdicts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class ScoringVerdictResponse(BaseModel):
    """API response model for an opportunity's scoring verdict."""

    id: int
    opportunity_id: int
    profile_id: int | None = None
    eligibility_passed: bool
    eligibility_reason: dict[str, Any] | None = None
    scam_verdict: str | None = None
    scam_reason: dict[str, Any] | None = None
    llm_verdict: str | None = None
    llm_reasoning: str | None = None
    quota_deferred_at: datetime | None = None
    relevance_score: float | None = None
    relevance_explanation: dict[str, Any] | None = None
    funnel_completed_at: datetime | None = None
    model_name: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
