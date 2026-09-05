"""Unit tests for WhatsApp Cloud API provider (Phase 8)."""

from __future__ import annotations

import httpx
import pytest
import respx

from core.notifications.base import NotificationEvent
from core.notifications.whatsapp import WhatsAppProvider


@pytest.fixture
def sample_event():
    return NotificationEvent(
        event_type="offer",
        title="Job Offer",
        body="Stripe has extended a formal offer letter.",
    )


class TestWhatsAppProvider:
    def test_unconfigured_provider_fails_safely(self, sample_event):
        provider = WhatsAppProvider(phone_number_id=None, recipient_phone=None, access_token=None)
        assert provider.is_configured() is False

        result = provider.send(sample_event)
        assert result.success is False
        assert result.channel == "whatsapp"
        assert "not configured" in (result.error or "").lower()

    @respx.mock
    def test_successful_send(self, sample_event):
        route = respx.post("https://graph.facebook.com/v18.0/phone-123/messages").respond(
            200, json={"messaging_product": "whatsapp", "messages": [{"id": "wamid.123"}]}
        )

        provider = WhatsAppProvider(
            phone_number_id="phone-123",
            recipient_phone="+1234567890",
            access_token="fake-token",
        )
        assert provider.is_configured() is True

        result = provider.send(sample_event)
        assert result.success is True
        assert result.channel == "whatsapp"
        assert route.called

    @respx.mock
    def test_auth_expired_error(self, sample_event):
        respx.post("https://graph.facebook.com/v18.0/phone-123/messages").respond(
            401, text='{"error": {"message": "Session has expired", "type": "OAuthException", "code": 190}}'
        )

        provider = WhatsAppProvider(
            phone_number_id="phone-123",
            recipient_phone="+1234567890",
            access_token="expired-token",
        )
        result = provider.send(sample_event)

        assert result.success is False
        assert result.channel == "whatsapp"
        assert "Auth / Token Expired" in (result.error or "")

    @respx.mock
    def test_unpaid_billing_error(self, sample_event):
        respx.post("https://graph.facebook.com/v18.0/phone-123/messages").respond(
            402, text='{"error": {"message": "Payment required for messaging", "code": 131042}}'
        )

        provider = WhatsAppProvider(
            phone_number_id="phone-123",
            recipient_phone="+1234567890",
            access_token="fake-token",
        )
        result = provider.send(sample_event)

        assert result.success is False
        assert "Payment Required" in (result.error or "")

    @respx.mock
    def test_network_timeout_error(self, sample_event):
        respx.post("https://graph.facebook.com/v18.0/phone-123/messages").mock(
            side_effect=httpx.ConnectTimeout("Connection timed out")
        )

        provider = WhatsAppProvider(
            phone_number_id="phone-123",
            recipient_phone="+1234567890",
            access_token="fake-token",
        )
        result = provider.send(sample_event)

        assert result.success is False
        assert "Timeout" in (result.error or "")
