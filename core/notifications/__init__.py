"""Re-export notification service, providers, and event data models (Phase 8)."""

from core.notifications.base import (
    BaseNotificationProvider,
    DeliveryResult,
    NotificationEvent,
)
from core.notifications.events import (
    build_channel_degraded_event,
    build_health_event,
    build_message_event,
    build_quota_event,
    build_test_event,
)
from core.notifications.service import (
    DispatchSummary,
    NotificationService,
    notification_service,
)
from core.notifications.telegram import TelegramProvider
from core.notifications.whatsapp import WhatsAppProvider

__all__ = [
    "BaseNotificationProvider",
    "DeliveryResult",
    "NotificationEvent",
    "TelegramProvider",
    "WhatsAppProvider",
    "NotificationService",
    "DispatchSummary",
    "notification_service",
    "build_message_event",
    "build_health_event",
    "build_quota_event",
    "build_channel_degraded_event",
    "build_test_event",
]
