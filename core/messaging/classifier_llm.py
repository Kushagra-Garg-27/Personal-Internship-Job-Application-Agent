"""LLM-based classifier for ambiguous recruiter messages.

Reuses the shared Gemini quota tracker from Phase 5's scam_risk module
so there is one global budget across all LLM features.  Fails closed
on quota exhaustion or API errors → classification = ``unclassified``.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from core.config import settings
from core.funnel.scam_risk.gemini_client import quota_tracker, GeminiQuotaTracker

logger = logging.getLogger(__name__)


@dataclass
class LLMClassificationResult:
    """Structured result from LLM-based message classification."""

    success: bool
    classification: str = "unclassified"
    reasoning: str | None = None
    confidence: float | None = None
    is_quota_exhausted: bool = False
    error: str | None = None


VALID_CLASSIFICATIONS = {
    "interview_invite",
    "rejection",
    "offer",
    "follow_up",
    "screening_question",
    "generic",
}


class MessageClassifierLLM:
    """Classify ambiguous recruiter messages using Gemini free tier.

    Shares the global ``quota_tracker`` with the scam-risk evaluator so
    both features draw from the same daily budget.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        tracker: GeminiQuotaTracker | None = None,
        client: Any = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.GEMINI_API_KEY
        self.model_name = model_name if model_name is not None else settings.GEMINI_MODEL
        self.tracker = tracker or quota_tracker
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.api_key:
            return None
        try:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
            return self._client
        except Exception as exc:
            logger.error("Failed to initialize GenAI client: %s", exc)
            return None

    def classify(
        self,
        subject: str,
        body_preview: str,
        sender: str | None = None,
    ) -> LLMClassificationResult:
        """Classify a recruiter message using the LLM.

        Fails closed on quota exhaustion, missing credentials, or API errors.
        """
        # 1. Quota check
        if not self.tracker.record_call():
            logger.warning(
                "Gemini daily quota exhausted (%d calls)", self.tracker.daily_limit
            )
            return LLMClassificationResult(
                success=False,
                is_quota_exhausted=True,
                error="Gemini daily quota limit reached",
            )

        client = self._get_client()
        if client is None:
            return LLMClassificationResult(
                success=False,
                error="Gemini API key not configured or client failed to initialize",
            )

        # 2. Build prompt
        prompt = f"""You are an expert recruiter communication classifier analyzing an email received after a job application was submitted.

Email Details:
- From: {sender or 'Unknown'}
- Subject: {subject or 'No subject'}
- Body preview:
{body_preview or 'No body'}

Classify this email into exactly ONE of these categories:
1. "interview_invite" — scheduling an interview, phone screen, video call, or on-site visit
2. "rejection" — declining the candidate, position filled, not moving forward
3. "offer" — extending a job offer, compensation discussion
4. "screening_question" — assessment, coding challenge, take-home task, pre-interview questions
5. "follow_up" — status update, checking in, general follow-up
6. "generic" — automated confirmation, acknowledgement of receipt

Respond with a JSON object containing exactly these keys:
1. "classification": exactly one of the above category names
2. "reasoning": a clear 1-2 sentence explanation
3. "confidence": a float between 0.0 and 1.0

JSON response only:"""

        # 3. Invoke model
        try:
            response = client.models.generate_content(
                model=self.model_name,
                contents=prompt,
            )
            raw_text = response.text if hasattr(response, "text") else str(response)
            classification, reasoning, confidence = self._parse_response(raw_text)
            return LLMClassificationResult(
                success=True,
                classification=classification,
                reasoning=reasoning,
                confidence=confidence,
            )
        except Exception as exc:
            err_msg = str(exc)
            logger.exception("Gemini message classification failed: %s", err_msg)
            is_429 = (
                "429" in err_msg
                or "RESOURCE_EXHAUSTED" in err_msg
                or "quota" in err_msg.lower()
            )
            return LLMClassificationResult(
                success=False,
                is_quota_exhausted=is_429,
                error=f"Gemini API error: {err_msg}",
            )

    def _parse_response(self, text: str) -> tuple[str, str, float | None]:
        """Extract classification, reasoning, confidence from LLM response."""
        cleaned = text.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

        try:
            data = json.loads(cleaned)
            c = str(data.get("classification", "generic")).lower()
            if c not in VALID_CLASSIFICATIONS:
                c = "generic"
            r = str(data.get("reasoning", "Classified by LLM."))
            conf_raw = data.get("confidence")
            conf = float(conf_raw) if conf_raw is not None else None
            return c, r, conf
        except Exception:
            # Fallback regex
            match_c = re.search(r'"classification"\s*:\s*"([^"]+)"', cleaned, re.I)
            match_r = re.search(r'"reasoning"\s*:\s*"([^"]+)"', cleaned, re.I)
            match_conf = re.search(r'"confidence"\s*:\s*([0-9.]+)', cleaned, re.I)
            c = match_c.group(1).lower() if match_c else "generic"
            if c not in VALID_CLASSIFICATIONS:
                c = "generic"
            r = match_r.group(1) if match_r else "Classified by LLM (fallback parse)."
            conf = float(match_conf.group(1)) if match_conf else None
            return c, r, conf
