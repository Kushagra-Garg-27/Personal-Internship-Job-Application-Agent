"""API router for notification settings, delivery logs, and test alerts (Phase 8)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from api.deps import get_db
from core.notifications import (
    NotificationEvent,
    build_test_event,
    notification_service,
)
from core.repositories import notification_repo
from core.schemas.notification import (
    NotificationLogListResponse,
    NotificationLogResponse,
    NotificationSettingResponse,
    NotificationSettingUpdate,
    TestNotificationRequest,
    TestNotificationResponse,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/settings", response_model=NotificationSettingResponse)
def get_settings(db: Session = Depends(get_db)):
    """Retrieve current notification settings and provider readiness (no secrets)."""
    db_setting = notification_repo.get_or_create_settings(db)
    return NotificationSettingResponse(
        whatsapp_enabled=db_setting.whatsapp_enabled,
        enabled_events=db_setting.enabled_events,
        telegram_configured=notification_service.telegram.is_configured(),
        whatsapp_configured=notification_service.whatsapp.is_configured(),
        updated_at=db_setting.updated_at,
    )


@router.patch("/settings", response_model=NotificationSettingResponse)
def update_settings(
    payload: NotificationSettingUpdate,
    db: Session = Depends(get_db),
):
    """Update event filters and/or WhatsApp mirror enable toggle."""
    db_setting = notification_repo.update_settings(
        session=db,
        whatsapp_enabled=payload.whatsapp_enabled,
        enabled_events=payload.enabled_events,
    )
    db.commit()
    return NotificationSettingResponse(
        whatsapp_enabled=db_setting.whatsapp_enabled,
        enabled_events=db_setting.enabled_events,
        telegram_configured=notification_service.telegram.is_configured(),
        whatsapp_configured=notification_service.whatsapp.is_configured(),
        updated_at=db_setting.updated_at,
    )


@router.get("/logs", response_model=NotificationLogListResponse)
def list_logs(
    channel: str | None = Query(None, description="Filter by channel: telegram, whatsapp"),
    status: str | None = Query(None, description="Filter by status: delivered, failed, skipped"),
    event_type: str | None = Query(None, description="Filter by event type"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List recent notification delivery records."""
    items, total = notification_repo.list_logs(
        session=db,
        channel=channel,
        status=status,
        event_type=event_type,
        limit=limit,
        offset=offset,
    )
    return NotificationLogListResponse(
        items=[NotificationLogResponse.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/test", response_model=TestNotificationResponse)
def send_test_notification(
    payload: TestNotificationRequest = TestNotificationRequest(),
    db: Session = Depends(get_db),
):
    """Dispatch a test notification to verify provider connectivity."""
    if payload.event_type == "test_event" and not payload.custom_message:
        event = build_test_event()
    else:
        event = NotificationEvent(
            event_type=payload.event_type,
            title=f"Test Alert: {payload.event_type.replace('_', ' ').title()}",
            body=payload.custom_message or "This is a manually triggered test alert from the dashboard.",
            payload={"is_test": True},
        )

    summary = notification_service.dispatch(event, session=db)
    db.commit()

    results_data = [
        {
            "channel": r.channel,
            "success": r.success,
            "error": r.error,
            "retries": r.retries,
        }
        for r in summary.results
    ]

    overall_success = any(r.success for r in summary.results) if summary.results else False

    return TestNotificationResponse(
        success=overall_success,
        event_type=summary.event_type,
        skipped=summary.skipped,
        skip_reason=summary.skip_reason,
        results=results_data,
        channel_degraded_alert_sent=summary.channel_degraded_alert_sent,
    )
