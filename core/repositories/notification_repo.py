"""Repository for notification logs and persistent settings (Phase 8)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, func
from sqlalchemy.orm import Session

from core.config import settings
from core.models.notification import NotificationLog, NotificationSetting


def create_log(
    session: Session,
    event_type: str,
    channel: str,
    title: str,
    body: str,
    status: str,
    error_detail: str | None = None,
    retry_count: int = 0,
    payload_json: dict[str, Any] | None = None,
) -> NotificationLog:
    """Create and persist a new notification delivery log."""
    log = NotificationLog(
        event_type=event_type,
        channel=channel,
        title=title,
        body=body,
        status=status,
        error_detail=error_detail,
        retry_count=retry_count,
        payload_json=payload_json,
        created_at=datetime.now(timezone.utc),
    )
    session.add(log)
    session.flush()
    return log


def list_logs(
    session: Session,
    channel: str | None = None,
    status: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[NotificationLog], int]:
    """List notification logs with optional filters and pagination."""
    query = session.query(NotificationLog)

    if channel:
        query = query.filter(NotificationLog.channel == channel)
    if status:
        query = query.filter(NotificationLog.status == status)
    if event_type:
        query = query.filter(NotificationLog.event_type == event_type)

    total = query.count()
    items = (
        query.order_by(desc(NotificationLog.created_at))
        .offset(offset)
        .limit(limit)
        .all()
    )
    return items, total


def get_or_create_settings(session: Session) -> NotificationSetting:
    """Retrieve or initialize singleton notification settings."""
    setting = session.query(NotificationSetting).filter(NotificationSetting.id == 1).first()
    if setting is None:
        setting = NotificationSetting(
            id=1,
            whatsapp_enabled=settings.WHATSAPP_ENABLED,
            enabled_events=dict(settings.NOTIFICATION_DEFAULT_EVENTS),
            updated_at=datetime.now(timezone.utc),
        )
        session.add(setting)
        session.flush()
    return setting


def update_settings(
    session: Session,
    whatsapp_enabled: bool | None = None,
    enabled_events: dict[str, bool] | None = None,
) -> NotificationSetting:
    """Update notification settings."""
    setting = get_or_create_settings(session)
    if whatsapp_enabled is not None:
        setting.whatsapp_enabled = whatsapp_enabled
    if enabled_events is not None:
        # Merge or replace enabled events
        merged = dict(setting.enabled_events or {})
        merged.update(enabled_events)
        setting.enabled_events = merged
    setting.updated_at = datetime.now(timezone.utc)
    session.add(setting)
    session.flush()
    return setting
