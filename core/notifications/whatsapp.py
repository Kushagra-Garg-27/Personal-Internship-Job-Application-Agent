"""WhatsApp Cloud API provider for notifications (Phase 8).

WhatsApp is an OPTIONAL, opt-in secondary mirror provider.
Per the architecture and risk register, treating WhatsApp as anything but
optional is a bug. Unconfigured, expired, or erroring WhatsApp must NEVER
block or fail Telegram delivery.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from core.config import settings
from core.notifications.base import BaseNotificationProvider, DeliveryResult, NotificationEvent

logger = logging.getLogger(__name__)


class WhatsAppProvider(BaseNotificationProvider):
    """Optional mirror provider via WhatsApp Cloud API."""

    def __init__(
        self,
        phone_number_id: str | None = None,
        recipient_phone: str | None = None,
        access_token: str | None = None,
        api_version: str | None = None,
        timeout: float = 10.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.phone_number_id = (
            phone_number_id if phone_number_id is not None else settings.WHATSAPP_PHONE_NUMBER_ID
        )
        self.recipient_phone = (
            recipient_phone if recipient_phone is not None else settings.WHATSAPP_RECIPIENT_PHONE
        )
        self.access_token = (
            access_token if access_token is not None else settings.WHATSAPP_ACCESS_TOKEN
        )
        self.api_version = (
            api_version if api_version is not None else settings.WHATSAPP_API_VERSION
        )
        self.timeout = timeout
        self._client = client

    @property
    def channel_name(self) -> str:
        return "whatsapp"

    def is_configured(self) -> bool:
        return bool(self.phone_number_id and self.recipient_phone and self.access_token)

    def _get_client(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(timeout=self.timeout)

    def send(self, event: NotificationEvent) -> DeliveryResult:
        """Send notification via WhatsApp Cloud API."""
        if not self.is_configured():
            logger.debug("WhatsApp send skipped: credentials not configured")
            return DeliveryResult(
                success=False,
                channel=self.channel_name,
                error="WhatsApp credentials not configured",
            )

        url = f"https://graph.facebook.com/{self.api_version}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self.recipient_phone,
            "type": "text",
            "text": {
                "preview_url": False,
                "body": event.format_plain(),
            },
        }

        client = self._get_client()
        try:
            response = client.post(url, json=payload, headers=headers)
            if response.is_success:
                logger.info("WhatsApp notification mirrored for event: %s", event.event_type)
                return DeliveryResult(
                    success=True,
                    channel=self.channel_name,
                )

            status = response.status_code
            error_body = response.text[:300]
            logger.warning("WhatsApp Cloud API error (%d): %s", status, error_body)

            # Categorize known Meta error codes for clearer diagnostics
            category = "API Error"
            if status == 401:
                category = "Auth / Token Expired"
            elif status == 402 or "payment" in error_body.lower() or "billing" in error_body.lower():
                category = "Unpaid Billing / Payment Required"
            elif status == 429 or "rate limit" in error_body.lower():
                category = "Rate Limit / Quota Reached"

            return DeliveryResult(
                success=False,
                channel=self.channel_name,
                error=f"HTTP {status} ({category}): {error_body}",
            )

        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            error_msg = f"Network/Timeout error: {exc}"
            logger.warning("WhatsApp network failure: %s", error_msg)
            return DeliveryResult(
                success=False,
                channel=self.channel_name,
                error=error_msg,
            )
        except Exception as exc:
            error_msg = f"Unexpected error: {exc}"
            logger.error("WhatsApp unexpected failure: %s", error_msg)
            return DeliveryResult(
                success=False,
                channel=self.channel_name,
                error=error_msg,
            )
