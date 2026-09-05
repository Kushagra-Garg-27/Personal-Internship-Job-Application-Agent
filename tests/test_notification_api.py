"""Tests for notification API endpoints (Phase 8)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from core.notifications.base import DeliveryResult
from core.notifications.service import notification_service
from core.repositories import notification_repo


class TestNotificationSettingsAPI:
    def test_get_settings(self, client: TestClient):
        response = client.get("/notifications/settings")
        assert response.status_code == 200
        data = response.json()
        assert "whatsapp_enabled" in data
        assert "enabled_events" in data
        assert "telegram_configured" in data
        assert "whatsapp_configured" in data
        # Ensure credentials are NOT leaked in response
        assert "token" not in str(data).lower()
        assert "secret" not in str(data).lower()

    def test_update_settings(self, client: TestClient):
        # Enable WhatsApp and toggle generic events
        payload = {
            "whatsapp_enabled": True,
            "enabled_events": {"generic": True, "rejection": False},
        }
        response = client.patch("/notifications/settings", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["whatsapp_enabled"] is True
        assert data["enabled_events"]["generic"] is True
        assert data["enabled_events"]["rejection"] is False

        # Verify persistent in subsequent GET
        get_res = client.get("/notifications/settings")
        assert get_res.json()["whatsapp_enabled"] is True
        assert get_res.json()["enabled_events"]["generic"] is True


class TestNotificationLogsAPI:
    def test_list_logs_empty(self, client: TestClient):
        response = client.get("/notifications/logs")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data

    def test_list_logs_with_filter(self, client: TestClient, db_session):
        # Create some logs directly via repo
        notification_repo.create_log(
            db_session,
            event_type="offer",
            channel="telegram",
            title="Offer 1",
            body="You have an offer",
            status="delivered",
        )
        notification_repo.create_log(
            db_session,
            event_type="offer",
            channel="whatsapp",
            title="Offer 1",
            body="You have an offer",
            status="failed",
            error_detail="HTTP 402",
        )
        db_session.commit()

        # Filter by channel=whatsapp
        res_wa = client.get("/notifications/logs?channel=whatsapp")
        assert res_wa.status_code == 200
        data_wa = res_wa.json()
        assert data_wa["total"] == 1
        assert data_wa["items"][0]["channel"] == "whatsapp"
        assert data_wa["items"][0]["status"] == "failed"

        # Filter by status=delivered
        res_del = client.get("/notifications/logs?status=delivered")
        assert res_del.status_code == 200
        assert res_del.json()["total"] == 1
        assert res_del.json()["items"][0]["channel"] == "telegram"


class TestTestNotificationEndpoint:
    @patch.object(notification_service.telegram, "send")
    def test_trigger_test_notification_success(self, mock_tg_send, client: TestClient):
        mock_tg_send.return_value = DeliveryResult(success=True, channel="telegram")

        response = client.post("/notifications/test", json={"event_type": "test_event"})
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["event_type"] == "test_event"
        assert len(data["results"]) >= 1
        assert data["results"][0]["channel"] == "telegram"
        assert data["results"][0]["success"] is True
