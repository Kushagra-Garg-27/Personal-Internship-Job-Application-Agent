"""NotificationService core dispatcher (Phase 8).

Handles:
- Event-type filtering (permissive default)
- Mandatory Telegram delivery (with retries and loud logging)
- Optional WhatsApp mirror delivery (if enabled)
- Fail-closed fallback: if WhatsApp fails, Telegram continues uninterrupted
  and an additional "WhatsApp channel degraded" warning is sent via Telegram.
- Delivery audit logging in the database
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from core.config import settings
from core.database import get_session
from core.notifications.base import BaseNotificationProvider, DeliveryResult, NotificationEvent
from core.notifications.events import build_channel_degraded_event
from core.notifications.telegram import TelegramProvider
from core.notifications.whatsapp import WhatsAppProvider
from core.repositories import notification_repo

logger = logging.getLogger(__name__)


@dataclass
class DispatchSummary:
    """Summary of dispatching a notification across configured channels."""

    event_type: str
    skipped: bool = False
    skip_reason: str | None = None
    results: list[DeliveryResult] = field(default_factory=list)
    channel_degraded_alert_sent: bool = False

    @property
    def telegram_result(self) -> DeliveryResult | None:
        return next((r for r in self.results if r.channel == "telegram"), None)

    @property
    def whatsapp_result(self) -> DeliveryResult | None:
        return next((r for r in self.results if r.channel == "whatsapp"), None)


class NotificationService:
    """Central notification dispatcher for all application events."""

    def __init__(
        self,
        telegram_provider: BaseNotificationProvider | None = None,
        whatsapp_provider: BaseNotificationProvider | None = None,
    ) -> None:
        self.telegram = telegram_provider or TelegramProvider()
        self.whatsapp = whatsapp_provider or WhatsAppProvider()

    def is_event_enabled(
        self,
        event_type: str,
        session: Session | None = None,
    ) -> bool:
        """Check if an event type is enabled for notification dispatch."""
        if session is not None:
            try:
                db_settings = notification_repo.get_or_create_settings(session)
                if event_type in db_settings.enabled_events:
                    return bool(db_settings.enabled_events[event_type])
            except Exception as exc:
                logger.warning("Failed to query notification settings from DB: %s", exc)

        # Fallback to config defaults
        return settings.NOTIFICATION_DEFAULT_EVENTS.get(event_type, True)

    def is_whatsapp_enabled(self, session: Session | None = None) -> bool:
        """Check if WhatsApp mirroring is enabled."""
        if session is not None:
            try:
                db_settings = notification_repo.get_or_create_settings(session)
                return bool(db_settings.whatsapp_enabled)
            except Exception as exc:
                logger.warning("Failed to query whatsapp setting from DB: %s", exc)

        return bool(settings.WHATSAPP_ENABLED)

    def dispatch(
        self,
        event: NotificationEvent,
        session: Session | None = None,
    ) -> DispatchSummary:
        """Dispatch a notification event according to configuration and rules.

        1. Check event-type filter. If disabled, skip.
        2. Send via Telegram (required default). Log status.
        3. If WhatsApp enabled: send via WhatsApp mirror. Log status.
        4. If WhatsApp failed: send "WhatsApp channel degraded" notice via Telegram!
        """
        # Step 1: Event-type filtering
        if not self.is_event_enabled(event.event_type, session=session):
            logger.info("Notification skipped for event '%s' (disabled in settings)", event.event_type)
            if session is not None:
                try:
                    notification_repo.create_log(
                        session,
                        event_type=event.event_type,
                        channel="all",
                        title=event.title,
                        body=event.body,
                        status="skipped",
                        error_detail="Disabled in event filter settings",
                        payload_json=event.payload,
                    )
                    session.commit()
                except Exception as log_err:
                    logger.warning("Failed to record skipped notification log: %s", log_err)
            return DispatchSummary(
                event_type=event.event_type,
                skipped=True,
                skip_reason=f"Event type '{event.event_type}' disabled in settings",
            )

        summary = DispatchSummary(event_type=event.event_type)

        # Step 2: Telegram dispatch (Primary, required)
        tg_result = self.telegram.send(event)
        summary.results.append(tg_result)
        self._record_log(
            session=session,
            event=event,
            channel="telegram",
            status="delivered" if tg_result.success else "failed",
            error_detail=tg_result.error,
            retry_count=tg_result.retries,
        )

        # Step 3: WhatsApp mirror dispatch (Optional, feature-flagged)
        if self.is_whatsapp_enabled(session=session):
            wa_result = self.whatsapp.send(event)
            summary.results.append(wa_result)
            self._record_log(
                session=session,
                event=event,
                channel="whatsapp",
                status="delivered" if wa_result.success else "failed",
                error_detail=wa_result.error,
            )

            # Step 4: Fail-closed fallback check
            # If WhatsApp failed, send a "WhatsApp channel degraded" warning via Telegram!
            if not wa_result.success:
                logger.warning(
                    "WhatsApp mirror failed (%s); triggering degraded channel alert via Telegram",
                    wa_result.error,
                )
                degraded_event = build_channel_degraded_event("whatsapp", wa_result.error)
                degraded_tg_result = self.telegram.send(degraded_event)
                summary.channel_degraded_alert_sent = degraded_tg_result.success
                self._record_log(
                    session=session,
                    event=degraded_event,
                    channel="telegram",
                    status="delivered" if degraded_tg_result.success else "failed",
                    error_detail=degraded_tg_result.error,
                    retry_count=degraded_tg_result.retries,
                )

        return summary

    def _record_log(
        self,
        session: Session | None,
        event: NotificationEvent,
        channel: str,
        status: str,
        error_detail: str | None = None,
        retry_count: int = 0,
    ) -> None:
        """Safely record a delivery log entry in the database."""
        if session is not None:
            try:
                notification_repo.create_log(
                    session=session,
                    event_type=event.event_type,
                    channel=channel,
                    title=event.title,
                    body=event.body,
                    status=status,
                    error_detail=error_detail,
                    retry_count=retry_count,
                    payload_json=event.payload,
                )
                session.commit()
            except Exception as exc:
                logger.error("Failed to write notification log to session: %s", exc)
        else:
            # Fallback to standalone session
            try:
                with get_session() as standalone_session:
                    notification_repo.create_log(
                        session=standalone_session,
                        event_type=event.event_type,
                        channel=channel,
                        title=event.title,
                        body=event.body,
                        status=status,
                        error_detail=error_detail,
                        retry_count=retry_count,
                        payload_json=event.payload,
                    )
            except Exception as exc:
                logger.debug("No active DB session available for notification logging: %s", exc)


# Global default service singleton
notification_service = NotificationService()
