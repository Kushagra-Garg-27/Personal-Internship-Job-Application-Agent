"""Unit tests for Gemini scam analysis client, quota tracker, and fail-closed handling (Phase 5)."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest

from core.funnel.scam_risk.gemini_client import (
    GeminiQuotaTracker,
    GeminiScamClient,
    LLMScamResult,
)


class DummyOpp:
    def __init__(self, title="Dev", company="Corp", description="Job desc", url="http://x.com"):
        self.title = title
        self.company = company
        self.description = description
        self.url = url


class TestGeminiQuotaTracker:
    def test_initial_state(self):
        tracker = GeminiQuotaTracker(daily_limit=10)
        assert tracker.can_make_call() is True
        assert tracker.is_exhausted() is False
        assert tracker.calls_remaining == 10
        assert tracker.calls_used == 0

    def test_record_call_increments_and_exhausts(self):
        tracker = GeminiQuotaTracker(daily_limit=2)
        assert tracker.can_make_call() is True

        tracker.record_call()
        assert tracker.calls_used == 1
        assert tracker.calls_remaining == 1
        assert tracker.is_exhausted() is False

        tracker.record_call()
        assert tracker.calls_used == 2
        assert tracker.calls_remaining == 0
        assert tracker.is_exhausted() is True
        assert tracker.can_make_call() is False

    def test_daily_rollover_resets_counter(self):
        tracker = GeminiQuotaTracker(daily_limit=5)
        tracker.record_call()
        tracker.record_call()
        assert tracker.calls_used == 2

        # Simulate date rolling over to yesterday
        tracker._current_date = date.today() - timedelta(days=1)
        assert tracker.can_make_call() is True
        assert tracker.calls_used == 0
        assert tracker.calls_remaining == 5


class TestGeminiScamClient:
    def test_no_api_key_fails_closed(self):
        client = GeminiScamClient(api_key=None)
        opp = DummyOpp()
        result = client.evaluate_ambiguous(opp)

        assert result.success is False
        assert "not configured" in (result.error or "").lower()
        assert result.is_quota_exhausted is False

    def test_exhausted_quota_fails_closed(self):
        tracker = GeminiQuotaTracker(daily_limit=0)
        client = GeminiScamClient(api_key="fake-key", quota_tracker=tracker)
        opp = DummyOpp()
        result = client.evaluate_ambiguous(opp)

        assert result.success is False
        assert result.is_quota_exhausted is True
        assert "quota" in (result.error or "").lower()

    def test_successful_llm_json_response(self):
        tracker = GeminiQuotaTracker(daily_limit=10)
        client = GeminiScamClient(api_key="test-key", quota_tracker=tracker)

        # Mock the genai client
        mock_response = Mock()
        mock_response.text = '{"verdict": "suspicious", "confidence": 0.85, "reasoning": "Unusually high compensation with vague requirements."}'

        mock_genai_client = MagicMock()
        mock_genai_client.models.generate_content.return_value = mock_response
        client._client = mock_genai_client

        opp = DummyOpp(
            title="Data Entry Assistant",
            company="Unknown LLC",
            description="Earn $100/hr from home with no previous skills.",
        )
        result = client.evaluate_ambiguous(opp, flagged_signals=[{"rule": "whois", "detail": "Domain age 2 days"}])

        assert result.success is True
        assert result.verdict == "suspicious"
        assert result.confidence == 0.85
        assert "Unusually high compensation" in result.reasoning
        assert tracker.calls_used == 1

    def test_llm_markdown_wrapped_json_response(self):
        tracker = GeminiQuotaTracker(daily_limit=10)
        client = GeminiScamClient(api_key="test-key", quota_tracker=tracker)

        mock_response = Mock()
        mock_response.text = '```json\n{"verdict": "legitimate", "confidence": 0.9, "reasoning": "Legitimate early stage startup."}\n```'

        mock_genai_client = MagicMock()
        mock_genai_client.models.generate_content.return_value = mock_response
        client._client = mock_genai_client

        opp = DummyOpp()
        result = client.evaluate_ambiguous(opp)

        assert result.success is True
        assert result.verdict == "legitimate"
        assert result.confidence == 0.9

    def test_api_exception_fails_closed(self):
        tracker = GeminiQuotaTracker(daily_limit=10)
        client = GeminiScamClient(api_key="test-key", quota_tracker=tracker)

        mock_genai_client = MagicMock()
        mock_genai_client.models.generate_content.side_effect = Exception("503 Service Unavailable")
        client._client = mock_genai_client

        opp = DummyOpp()
        result = client.evaluate_ambiguous(opp)

        assert result.success is False
        assert "503 Service Unavailable" in (result.error or "")
        # Should record call attempt for budget accounting
        assert tracker.calls_used == 1

    def test_quota_exhaustion_detected_from_api_error(self):
        tracker = GeminiQuotaTracker(daily_limit=100)
        client = GeminiScamClient(api_key="test-key", quota_tracker=tracker)

        mock_genai_client = MagicMock()
        mock_genai_client.models.generate_content.side_effect = Exception("Resource has been exhausted (e.g. check quota)")
        client._client = mock_genai_client

        opp = DummyOpp()
        result = client.evaluate_ambiguous(opp)

        assert result.success is False
        assert result.is_quota_exhausted is True
