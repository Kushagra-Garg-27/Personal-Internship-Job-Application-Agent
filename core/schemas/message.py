"""Pydantic schemas for recruiter messages and integration health (Phase 7)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


# ── Message schemas ──────────────────────────────────────────────────────


class MessageResponse(BaseModel):
    """Full recruiter message representation."""

    id: int
    gmail_id: str
    thread_id: str | None = None
    application_id: int | None = None
    sender: str
    sender_domain: str | None = None
    subject: str | None = None
    body_preview: str | None = None
    received_at: datetime | None = None
    classification: str | None = None
    classification_source: str | None = None
    classification_confidence: float | None = None
    link_confidence: str | None = None
    created_at: datetime
    updated_at: datetime

    # Nested opportunity info when available
    opportunity_id: int | None = None
    opportunity_title: str | None = None
    opportunity_company: str | None = None
    opportunity_status: str | None = None

    # Phase 10: Response Loop
    suggested_reply: str | None = None
    draft_id: str | None = None
    action_taken: str | None = None
    action_taken_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ApproveReplyRequest(BaseModel):
    """Payload for approving and creating a Gmail draft for a suggested reply."""

    edited_reply: str | None = None


class ApproveReplyResponse(BaseModel):
    """Result of approving a reply draft."""

    message: MessageResponse
    opportunity_id: int
    opportunity_status: str
    draft_id: str
    detail: str = "Draft created in Gmail. Open Gmail to send."


class AcknowledgeMessageResponse(BaseModel):
    """Result of acknowledging a recruiter message."""

    message: MessageResponse
    opportunity_id: int
    opportunity_status: str
    detail: str = "Message acknowledged and opportunity status updated."



class MessageListResponse(BaseModel):
    """Paginated list of recruiter messages."""

    items: list[MessageResponse]
    total: int
    limit: int
    offset: int


class MessageStatsResponse(BaseModel):
    """Aggregate statistics for recruiter messages."""

    total: int
    by_classification: dict[str, int]
    linked: int
    unlinked: int


# ── Integration Health schemas ───────────────────────────────────────────


class IntegrationHealthResponse(BaseModel):
    """A single integration health event."""

    id: int
    integration_name: str
    event_type: str
    detail: str | None = None
    occurred_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IntegrationHealthListResponse(BaseModel):
    """List of integration health events."""

    items: list[IntegrationHealthResponse]
    total: int
