"""Pydantic schemas for notification endpoints (Phase 8).

Strict security rule: Provider tokens/credentials are never exposed or
handled via these schemas or the UI.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class NotificationSettingResponse(BaseModel):
    """Notification settings and provider readiness status (no secrets)."""

    model_config = ConfigDict(from_attributes=True)

    whatsapp_enabled: bool
    enabled_events: dict[str, bool]
    telegram_configured: bool
    whatsapp_configured: bool
    updated_at: datetime | None = None


class NotificationSettingUpdate(BaseModel):
    """Payload to update notification settings."""

    whatsapp_enabled: bool | None = None
    enabled_events: dict[str, bool] | None = None


class NotificationLogResponse(BaseModel):
    """Single notification delivery log record."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    channel: str
    title: str
    body: str
    status: str
    error_detail: str | None = None
    retry_count: int = 0
    payload_json: dict[str, Any] | None = None
    created_at: datetime


class NotificationLogListResponse(BaseModel):
    """Paginated list of notification logs."""

    items: list[NotificationLogResponse]
    total: int
    limit: int
    offset: int


class TestNotificationRequest(BaseModel):
    """Optional request parameters for test notification."""

    event_type: str = Field(default="test_event")
    custom_message: str | None = None


class TestNotificationResponse(BaseModel):
    """Result of triggering a test notification."""

    success: bool
    event_type: str
    skipped: bool = False
    skip_reason: str | None = None
    results: list[dict[str, Any]]
    channel_degraded_alert_sent: bool = False
