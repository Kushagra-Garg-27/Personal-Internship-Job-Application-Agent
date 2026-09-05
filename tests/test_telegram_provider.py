"""Unit tests for Telegram Bot API provider (Phase 8)."""

from __future__ import annotations

import httpx
import pytest
import respx

from core.notifications.base import NotificationEvent
from core.notifications.telegram import TelegramProvider


@pytest.fixture
def sample_event():
    return NotificationEvent(
        event_type="interview_invite",
        title="Interview Invitation",
        body="Google would like to schedule a technical interview.",
    )


class TestTelegramProvider:
    def test_unconfigured_provider_fails_safely(self, sample_event):
        provider = TelegramProvider(bot_token=None, chat_id=None)
        assert provider.is_configured() is False

        result = provider.send(sample_event)
        assert result.success is False
        assert result.channel == "telegram"
        assert "not configured" in (result.error or "").lower()

    @respx.mock
    def test_successful_send(self, sample_event):
        route = respx.post("https://api.telegram.org/botfake-token/sendMessage").respond(
            200, json={"ok": True, "result": {"message_id": 42}}
        )

        provider = TelegramProvider(bot_token="fake-token", chat_id="12345", max_retries=1)
        assert provider.is_configured() is True

        result = provider.send(sample_event)
        assert result.success is True
        assert result.channel == "telegram"
        assert result.retries == 0
        assert route.called

    @respx.mock
    def test_markdown_fallback_to_plain_text(self, sample_event):
        # First call fails with markdown entity parsing error
        # Second call with plain text succeeds
        route = respx.post("https://api.telegram.org/botfake-token/sendMessage").side_effect = [
            httpx.Response(400, text="Bad Request: can't parse entities in message text"),
            httpx.Response(200, json={"ok": True, "result": {"message_id": 43}}),
        ]

        provider = TelegramProvider(bot_token="fake-token", chat_id="12345", max_retries=1)
        result = provider.send(sample_event)

        assert result.success is True
        assert result.channel == "telegram"

    @respx.mock
    def test_transient_error_retries_and_succeeds(self, sample_event):
        # 500 error followed by 200 success
        respx.post("https://api.telegram.org/botfake-token/sendMessage").side_effect = [
            httpx.Response(500, text="Internal Server Error"),
            httpx.Response(200, json={"ok": True, "result": {"message_id": 44}}),
        ]

        provider = TelegramProvider(bot_token="fake-token", chat_id="12345", max_retries=2)
        result = provider.send(sample_event)

        assert result.success is True
        assert result.retries == 1

    @respx.mock
    def test_permanent_error_fails_without_retries(self, sample_event):
        # 401 Unauthorized should fail permanently without spinning in retry loop
        route = respx.post("https://api.telegram.org/botfake-token/sendMessage").respond(
            401, text="Unauthorized"
        )

        provider = TelegramProvider(bot_token="fake-token", chat_id="12345", max_retries=3)
        result = provider.send(sample_event)

        assert result.success is False
        assert result.retries == 0
        assert "401" in (result.error or "")
        assert route.call_count == 1

    @respx.mock
    def test_exhausted_retries_returns_failure(self, sample_event):
        # Repeated 503 Service Unavailable
        respx.post("https://api.telegram.org/botfake-token/sendMessage").respond(
            503, text="Service Unavailable"
        )

        provider = TelegramProvider(bot_token="fake-token", chat_id="12345", max_retries=2)
        result = provider.send(sample_event)

        assert result.success is False
        assert result.retries == 2
        assert "503" in (result.error or "")
