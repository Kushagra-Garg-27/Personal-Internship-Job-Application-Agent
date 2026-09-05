"""Tests for NotificationService core dispatcher (Phase 8).

Includes the critical fallback test verbatim from §6 and the structural
unconfigured-WhatsApp check to guarantee WhatsApp is never load-bearing.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from core.models.notification import NotificationLog
from core.notifications.base import DeliveryResult, NotificationEvent
from core.notifications.service import NotificationService
from core.notifications.telegram import TelegramProvider
from core.notifications.whatsapp import WhatsAppProvider
from core.repositories import notification_repo


@pytest.fixture
def sample_event():
    return NotificationEvent(
        event_type="interview_invite",
        title="Interview Scheduled",
        body="Google invited you to an interview.",
    )


class TestEventFiltering:
    def test_disabled_event_is_skipped(self, db_session: Session, sample_event):
        # Explicitly disable interview_invite in DB settings
        notification_repo.update_settings(
            db_session,
            enabled_events={"interview_invite": False},
        )

        mock_telegram = MagicMock(spec=TelegramProvider)
        service = NotificationService(telegram_provider=mock_telegram)

        summary = service.dispatch(sample_event, session=db_session)
        assert summary.skipped is True
        assert "disabled" in (summary.skip_reason or "").lower()
        mock_telegram.send.assert_not_called()

        # Verify skipped log entry was persisted
        log = db_session.query(NotificationLog).filter(NotificationLog.status == "skipped").first()
        assert log is not None
        assert log.event_type == "interview_invite"

    def test_enabled_event_is_dispatched(self, db_session: Session, sample_event):
        # Enable event
        notification_repo.update_settings(
            db_session,
            enabled_events={"interview_invite": True},
        )

        mock_telegram = MagicMock(spec=TelegramProvider)
        mock_telegram.send.return_value = DeliveryResult(success=True, channel="telegram")

        service = NotificationService(telegram_provider=mock_telegram)
        summary = service.dispatch(sample_event, session=db_session)

        assert summary.skipped is False
        assert summary.telegram_result is not None
        assert summary.telegram_result.success is True
        mock_telegram.send.assert_called_once_with(sample_event)


class TestProviderDispatch:
    def test_whatsapp_disabled_by_default(self, db_session: Session, sample_event):
        """When WhatsApp mirror is disabled, only Telegram receives the event."""
        mock_telegram = MagicMock(spec=TelegramProvider)
        mock_telegram.send.return_value = DeliveryResult(success=True, channel="telegram")

        mock_whatsapp = MagicMock(spec=WhatsAppProvider)

        notification_repo.update_settings(db_session, whatsapp_enabled=False)

        service = NotificationService(
            telegram_provider=mock_telegram,
            whatsapp_provider=mock_whatsapp,
        )
        summary = service.dispatch(sample_event, session=db_session)

        assert summary.telegram_result is not None
        assert summary.telegram_result.success is True
        assert summary.whatsapp_result is None
        mock_whatsapp.send.assert_not_called()

    def test_whatsapp_enabled_mirrors_message(self, db_session: Session, sample_event):
        """When WhatsApp mirror is enabled, both Telegram and WhatsApp receive the event."""
        mock_telegram = MagicMock(spec=TelegramProvider)
        mock_telegram.send.return_value = DeliveryResult(success=True, channel="telegram")

        mock_whatsapp = MagicMock(spec=WhatsAppProvider)
        mock_whatsapp.send.return_value = DeliveryResult(success=True, channel="whatsapp")

        notification_repo.update_settings(db_session, whatsapp_enabled=True)

        service = NotificationService(
            telegram_provider=mock_telegram,
            whatsapp_provider=mock_whatsapp,
        )
        summary = service.dispatch(sample_event, session=db_session)

        assert summary.telegram_result is not None
        assert summary.telegram_result.success is True
        assert summary.whatsapp_result is not None
        assert summary.whatsapp_result.success is True
        mock_telegram.send.assert_called_once_with(sample_event)
        mock_whatsapp.send.assert_called_once_with(sample_event)


class TestFailClosedFallback:
    def test_whatsapp_failure_preserves_telegram_and_triggers_warning(
        self, db_session: Session, sample_event
    ):
        """VERBATIM §6 TEST:

        Simulate a WhatsApp provider failure (e.g. unpaid billing / expired token)
        and confirm Telegram fallback fires along with the degraded-channel warning.
        """
        mock_telegram = MagicMock(spec=TelegramProvider)
        mock_telegram.send.return_value = DeliveryResult(success=True, channel="telegram")

        mock_whatsapp = MagicMock(spec=WhatsAppProvider)
        mock_whatsapp.send.return_value = DeliveryResult(
            success=False,
            channel="whatsapp",
            error="HTTP 402 (Unpaid Billing / Payment Required)",
        )

        notification_repo.update_settings(db_session, whatsapp_enabled=True)

        service = NotificationService(
            telegram_provider=mock_telegram,
            whatsapp_provider=mock_whatsapp,
        )
        summary = service.dispatch(sample_event, session=db_session)

        # 1. Primary event reached Telegram
        assert summary.telegram_result is not None
        assert summary.telegram_result.success is True

        # 2. WhatsApp failed
        assert summary.whatsapp_result is not None
        assert summary.whatsapp_result.success is False

        # 3. Channel degraded alert was dispatched via Telegram
        assert summary.channel_degraded_alert_sent is True

        # Telegram was invoked twice: once for original event, once for degraded alert
        assert mock_telegram.send.call_count == 2
        second_call_event = mock_telegram.send.call_args_list[1][0][0]
        assert second_call_event.event_type == "channel_degraded"
        assert "whatsapp" in second_call_event.title.lower()

        # 4. Durable log records both delivery and failure
        logs = db_session.query(NotificationLog).all()
        channels = [l.channel for l in logs]
        assert "telegram" in channels
        assert "whatsapp" in channels


class TestStructuralUnconfiguredWhatsApp:
    def test_system_runs_with_whatsapp_unconfigured(self, db_session: Session, sample_event):
        """Direct test against the named risk:

        Verify the entire notification system runs correctly with WhatsApp
        completely unconfigured (no credentials, no tokens).
        """
        mock_telegram = MagicMock(spec=TelegramProvider)
        mock_telegram.send.return_value = DeliveryResult(success=True, channel="telegram")

        # Real unconfigured WhatsAppProvider
        real_unconfigured_wa = WhatsAppProvider(
            phone_number_id=None,
            recipient_phone=None,
            access_token=None,
        )
        assert real_unconfigured_wa.is_configured() is False

        # Even if someone turned the mirror toggle ON in settings:
        notification_repo.update_settings(db_session, whatsapp_enabled=True)

        service = NotificationService(
            telegram_provider=mock_telegram,
            whatsapp_provider=real_unconfigured_wa,
        )

        # Dispatch should not raise any exception
        summary = service.dispatch(sample_event, session=db_session)

        # Telegram delivered cleanly
        assert summary.telegram_result is not None
        assert summary.telegram_result.success is True

        # WhatsApp was caught gracefully
        assert summary.whatsapp_result is not None
        assert summary.whatsapp_result.success is False
        assert "not configured" in (summary.whatsapp_result.error or "").lower()

        # Channel degraded alert was sent to Telegram
        assert summary.channel_degraded_alert_sent is True
