"""Base classes, data models, and provider interfaces for notifications (Phase 8)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class DeliveryResult:
    """Result of attempting to deliver a notification through a provider."""

    success: bool
    channel: str  # "telegram", "whatsapp"
    error: str | None = None
    retries: int = 0

    def __repr__(self) -> str:
        status = "OK" if self.success else f"FAIL: {self.error}"
        return f"<DeliveryResult channel={self.channel!r} status={status} retries={self.retries}>"


@dataclass
class NotificationEvent:
    """High-level domain notification event to be dispatched."""

    event_type: str
    title: str
    body: str
    payload: dict[str, Any] | None = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def format_telegram(self) -> str:
        """Format the event message for Telegram (using Markdown)."""
        return f"*{self.title}*\n\n{self.body}"

    def format_plain(self) -> str:
        """Format the event message as plain text."""
        return f"[{self.title}]\n\n{self.body}"


class BaseNotificationProvider(ABC):
    """Abstract interface for a notification delivery provider."""

    @property
    @abstractmethod
    def channel_name(self) -> str:
        """Name of the channel (e.g. 'telegram', 'whatsapp')."""
        ...

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if required provider credentials are present."""
        ...

    @abstractmethod
    def send(self, event: NotificationEvent) -> DeliveryResult:
        """Attempt to deliver the notification event."""
        ...
