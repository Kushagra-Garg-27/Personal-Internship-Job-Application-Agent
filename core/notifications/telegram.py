"""Telegram Bot API provider for notifications (Phase 8).

Telegram is the required, non-negotiable default provider.
Treats failures seriously with retries, exponential backoff, and loud logging.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from core.config import settings
from core.notifications.base import BaseNotificationProvider, DeliveryResult, NotificationEvent

logger = logging.getLogger(__name__)


class TelegramProvider(BaseNotificationProvider):
    """Primary notification provider via Telegram Bot API."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.bot_token = bot_token if bot_token is not None else settings.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id if chat_id is not None else settings.TELEGRAM_CHAT_ID
        self.timeout = timeout if timeout is not None else float(settings.TELEGRAM_TIMEOUT_SECONDS)
        self.max_retries = max_retries if max_retries is not None else settings.TELEGRAM_MAX_RETRIES
        self._client = client

    @property
    def channel_name(self) -> str:
        return "telegram"

    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def _get_client(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(timeout=self.timeout)

    def send(self, event: NotificationEvent) -> DeliveryResult:
        """Send notification via Telegram with retries on transient errors."""
        if not self.is_configured():
            logger.warning("Telegram send skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured")
            return DeliveryResult(
                success=False,
                channel=self.channel_name,
                error="Telegram credentials not configured",
            )

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        text = event.format_telegram()
        payload: dict[str, Any] = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
        }

        client = self._get_client()
        retries = 0
        last_error: str | None = None

        while retries <= self.max_retries:
            try:
                response = client.post(url, json=payload)
                # If markdown parsing fails (HTTP 400 with "can't parse entities"), retry as plain text
                if response.status_code == 400 and "can't parse entities" in response.text.lower():
                    logger.debug("Telegram markdown parsing failed, falling back to plain text")
                    payload["parse_mode"] = None
                    payload["text"] = event.format_plain()
                    response = client.post(url, json=payload)

                if response.is_success:
                    logger.info("Telegram notification delivered for event: %s", event.event_type)
                    return DeliveryResult(
                        success=True,
                        channel=self.channel_name,
                        retries=retries,
                    )

                # Check if error is transient (429 or 5xx)
                status = response.status_code
                error_body = response.text[:300]
                last_error = f"HTTP {status}: {error_body}"

                if status in (429, 500, 502, 503, 504) and retries < self.max_retries:
                    backoff = (2 ** retries) * 0.5
                    logger.warning(
                        "Telegram transient failure (%s); retrying in %.1fs (attempt %d/%d)",
                        last_error,
                        backoff,
                        retries + 1,
                        self.max_retries,
                    )
                    time.sleep(backoff)
                    retries += 1
                    continue

                # Permanent client error (e.g. 401 Unauthorized, 403 Forbidden, 404 Not Found)
                break

            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = f"Network/Timeout error: {exc}"
                if retries < self.max_retries:
                    backoff = (2 ** retries) * 0.5
                    logger.warning(
                        "Telegram network failure (%s); retrying in %.1fs (attempt %d/%d)",
                        last_error,
                        backoff,
                        retries + 1,
                        self.max_retries,
                    )
                    time.sleep(backoff)
                    retries += 1
                    continue
                break
            except Exception as exc:
                last_error = f"Unexpected error: {exc}"
                break

        # Critical alert: Telegram failed and there is no tertiary channel
        logger.critical(
            "TELEGRAM NOTIFICATION FAILED PERMANENTLY: event=%s, error=%s, retries=%d",
            event.event_type,
            last_error,
            retries,
        )
        return DeliveryResult(
            success=False,
            channel=self.channel_name,
            error=last_error,
            retries=retries,
        )
