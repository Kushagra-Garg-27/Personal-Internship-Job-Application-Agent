"""Pydantic schemas for Opportunity, StatusHistory, and Application endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


# ── Opportunity schemas ───────────────────────────────────────────────────


class OpportunityCreate(BaseModel):
    """Input for creating/upserting an opportunity."""

    title: str
    company: str
    url: str | None = None
    source: str | None = None
    reliability_tier: str = "experimental"
    description: str | None = None
    location: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    posted_at: datetime | None = None
    deadline_at: datetime | None = None
    profile_id: int | None = None
    metadata_json: dict[str, Any] | None = None


class OpportunityUpdate(BaseModel):
    """Partial update for an opportunity (status excluded — use transition)."""

    title: str | None = None
    company: str | None = None
    url: str | None = None
    source: str | None = None
    reliability_tier: str | None = None
    description: str | None = None
    location: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    posted_at: datetime | None = None
    deadline_at: datetime | None = None
    profile_id: int | None = None
    metadata_json: dict[str, Any] | None = None


class OpportunityResponse(BaseModel):
    """Full opportunity representation."""

    id: int
    dedup_hash: str
    status: str
    reliability_tier: str
    source: str | None = None
    title: str
    company: str
    description: str | None = None
    url: str | None = None
    location: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    posted_at: datetime | None = None
    deadline_at: datetime | None = None
    discovered_at: datetime
    profile_id: int | None = None
    selected_resume_id: int | None = None
    metadata_json: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# ── Status transition schemas ─────────────────────────────────────────────


class StatusTransitionRequest(BaseModel):
    """Input for transitioning an opportunity's status."""

    new_status: str
    reason: str | None = None
    actor: str | None = None


class StatusHistoryResponse(BaseModel):
    """A single status-transition record."""

    id: int
    opportunity_id: int
    old_status: str | None = None
    new_status: str
    reason: str | None = None
    actor: str | None = None
    changed_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ScamReviewApproveRequest(BaseModel):
    """Input for human review approval."""

    actor: str = "human_reviewer"


class ScamReviewRejectRequest(BaseModel):
    """Input for human review rejection."""

    reason: str = "Rejected during human review"
    actor: str = "human_reviewer"


# ── Phase 6 Dashboard & Approval schemas ──────────────────────────────────


class FinalApprovalRequest(BaseModel):
    """Input for final human approval to apply for an opportunity."""

    resume_id: int | None = None
    actor: str = "human_user"


class DismissRequest(BaseModel):
    """Input for dismissing a recommended opportunity."""

    reason: str = "Declined by user during review"
    actor: str = "human_user"


class ResumeOption(BaseModel):
    """Compact representation of candidate resume version for selection."""

    id: int
    version: int
    original_filename: str
    is_active: bool
    uploaded_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class DashboardOpportunityItem(BaseModel):
    """Consolidated opportunity representation for the frontend dashboard."""

    id: int
    dedup_hash: str
    status: str
    reliability_tier: str
    source: str | None = None
    title: str
    company: str
    description: str | None = None
    url: str | None = None
    location: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    posted_at: datetime | None = None
    deadline_at: datetime | None = None
    discovered_at: datetime
    profile_id: int | None = None
    selected_resume_id: int | None = None

    # Relevance & scoring
    relevance_score: float | None = None
    relevance_explanation: dict[str, Any] | None = None
    model_name: str | None = None

    # Eligibility
    eligibility_passed: bool | None = None
    eligibility_reason: dict[str, Any] | None = None

    # Scam & risk
    scam_verdict: str | None = None
    scam_reason: dict[str, Any] | None = None
    llm_verdict: str | None = None
    llm_reasoning: str | None = None
    quota_deferred_at: datetime | None = None
    funnel_completed_at: datetime | None = None

    # Resumes
    available_resumes: list[ResumeOption] = []

    model_config = ConfigDict(from_attributes=True)


class DashboardFeedResponse(BaseModel):
    """Dashboard feed response with items and counts."""

    items: list[DashboardOpportunityItem]
    total: int
    limit: int
    offset: int


# ── Application schemas ──────────────────────────────────────────────────


class ApplicationCreate(BaseModel):
    """Input for creating an application attempt."""

    resume_id: int | None = None
    adapter_name: str | None = None
    notes: str | None = None


class ApplicationUpdate(BaseModel):
    """Input for updating an application's status."""

    status: str
    confirmation_ref: str | None = None
    submitted_at: datetime | None = None
    notes: str | None = None


class ApplicationResponse(BaseModel):
    """Full application representation."""

    id: int
    opportunity_id: int
    resume_id: int | None = None
    attempt_number: int
    status: str
    submitted_at: datetime | None = None
    confirmation_ref: str | None = None
    adapter_name: str | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
