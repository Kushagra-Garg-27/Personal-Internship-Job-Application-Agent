"""Tests for the classification pipeline (Phase 7)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.messaging.classification_pipeline import ClassificationResult, classify_message
from core.messaging.classifier_llm import LLMClassificationResult, MessageClassifierLLM


class TestRulesOnlyPath:
    """When rules produce a high-confidence result, the LLM is never called."""

    def test_high_confidence_interview(self):
        result = classify_message(
            subject="Schedule your interview",
            body_preview="We'd like to invite you to an interview.",
        )
        assert result.classification == "interview_invite"
        assert result.source == "rules"
        assert result.confidence is not None and result.confidence >= 0.8

    def test_high_confidence_rejection(self):
        result = classify_message(
            subject="Application update",
            body_preview="Unfortunately, we have decided not to proceed.",
        )
        assert result.classification == "rejection"
        assert result.source == "rules"


class TestLLMEscalation:
    """When rules are low-confidence, the LLM is called."""

    def test_llm_called_on_ambiguous(self):
        mock_client = MagicMock(spec=MessageClassifierLLM)
        mock_client.classify.return_value = LLMClassificationResult(
            success=True,
            classification="follow_up",
            reasoning="The email appears to be a follow-up.",
            confidence=0.85,
        )

        result = classify_message(
            subject="Hello",
            body_preview="Just wanted to reach out about something.",
            llm_client=mock_client,
        )
        assert result.classification == "follow_up"
        assert result.source == "llm"
        assert result.confidence == 0.85
        mock_client.classify.assert_called_once()


class TestLLMFailureFallback:
    """When the LLM fails, we fall back to rules or unclassified."""

    def test_quota_exhausted_falls_back(self):
        mock_client = MagicMock(spec=MessageClassifierLLM)
        mock_client.classify.return_value = LLMClassificationResult(
            success=False,
            is_quota_exhausted=True,
            error="Gemini daily quota limit reached",
        )

        result = classify_message(
            subject="Hello",
            body_preview="No strong signals here.",
            llm_client=mock_client,
        )
        # Should be unclassified since rules also had no confidence
        assert result.classification == "unclassified"
        assert result.source == "rules"
        assert "quota" in (result.reasoning or "").lower()

    def test_api_error_with_rules_fallback(self):
        """If rules had a moderate guess, it should be used on LLM failure."""
        mock_client = MagicMock(spec=MessageClassifierLLM)
        mock_client.classify.return_value = LLMClassificationResult(
            success=False,
            error="API timeout",
        )

        # "checking in" matches follow_up at ~0.78 confidence (below 0.8 threshold)
        result = classify_message(
            subject="checking in",
            body_preview="Just checking in on the status of my application",
            llm_client=mock_client,
        )
        # Should use rules fallback since confidence > 0.5
        assert result.source == "rules"
        assert result.classification != "unclassified"
