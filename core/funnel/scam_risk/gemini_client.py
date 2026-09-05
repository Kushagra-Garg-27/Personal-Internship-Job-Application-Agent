"""Gemini Developer API client wrapper for scam and risk assessment.

Reserved strictly for the ambiguous minority of job listings (~10–20%) that
cannot be resolved by deterministic rules alone.
Implements quota tracking and fail-closed behavior on quota exhaustion or API errors.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
import logging
import re
from typing import Any

from core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class GeminiScamResult:
    """Structured result of a Gemini scam/risk assessment."""

    success: bool
    verdict: str | None = None          # "scam", "legitimate", "suspicious"
    reasoning: str | None = None        # inspectable explanation
    confidence: float | None = None
    is_quota_exhausted: bool = False
    error: str | None = None
    raw_response: str | None = None


# Alias for readability and testing
LLMScamResult = GeminiScamResult


class GeminiQuotaTracker:
    """Tracks daily API call volume to enforce free-tier quota limits."""

    def __init__(self, daily_limit: int = 1500) -> None:
        self.daily_limit = daily_limit
        self._current_date: date = datetime.now(timezone.utc).date()
        self._call_count: int = 0

    def record_call(self) -> bool:
        """Attempt to record a call. Returns False if daily limit is reached."""
        today = datetime.now(timezone.utc).date()
        if today != self._current_date:
            self._current_date = today
            self._call_count = 0

        if self._call_count >= self.daily_limit:
            return False

        self._call_count += 1
        return True

    @property
    def remaining_today(self) -> int:
        today = datetime.now(timezone.utc).date()
        if today != self._current_date:
            return self.daily_limit
        return max(0, self.daily_limit - self._call_count)

    @property
    def calls_remaining(self) -> int:
        return self.remaining_today

    @property
    def calls_used(self) -> int:
        today = datetime.now(timezone.utc).date()
        if today != self._current_date:
            return 0
        return self._call_count

    def can_make_call(self) -> bool:
        return self.remaining_today > 0

    def is_exhausted(self) -> bool:
        return self.remaining_today <= 0

    def reset(self) -> None:
        """Reset quota counter (useful for testing)."""
        self._current_date = datetime.now(timezone.utc).date()
        self._call_count = 0


# Global singleton quota tracker
quota_tracker = GeminiQuotaTracker(daily_limit=settings.GEMINI_DAILY_QUOTA)


class GeminiScamClient:
    """Client for evaluating ambiguous postings with Gemini free tier."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        tracker: GeminiQuotaTracker | None = None,
        quota_tracker: GeminiQuotaTracker | None = None,
        client: Any = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.GEMINI_API_KEY
        self.model_name = model_name if model_name is not None else settings.GEMINI_MODEL
        self.tracker = tracker or quota_tracker or globals()["quota_tracker"]
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
            logger.error("Failed to initialize Google GenAI client: %s", exc)
            return None

    def evaluate_ambiguous(
        self,
        opportunity: Any,
        flagged_signals: list[dict[str, Any]] | None = None,
    ) -> GeminiScamResult:
        """Evaluate an ambiguous opportunity using Gemini.

        Fails closed on quota exhaustion, missing credentials, or API errors:
        returns `success=False` with error details so the opportunity can be deferred.
        """
        # 1. Enforce quota check
        if not self.tracker.record_call():
            logger.warning("Gemini daily quota exhausted (%d calls)", self.tracker.daily_limit)
            return GeminiScamResult(
                success=False,
                is_quota_exhausted=True,
                error="Gemini daily quota limit reached",
            )

        client = self._get_client()
        if client is None:
            return GeminiScamResult(
                success=False,
                is_quota_exhausted=False,
                error="Gemini API key is not configured or client failed to initialize",
            )

        # 2. Build contextual prompt including ambiguity signals
        title = getattr(opportunity, "title", "Unknown")
        company = getattr(opportunity, "company", "Unknown")
        url = getattr(opportunity, "url", "N/A")
        location = getattr(opportunity, "location", "N/A")
        desc = (getattr(opportunity, "description", "") or "")[:2500]

        signals_summary = json.dumps(flagged_signals or [], indent=2)

        prompt = f"""You are an expert fraud and scam investigator analyzing a job/internship listing that was flagged as AMBIGUOUS by automated deterministic rules.

Listing Details:
- Title: {title}
- Company: {company}
- URL: {url}
- Location: {location}
- Description excerpt:
{desc}

Automated Rules Triggered (Borderline Signals):
{signals_summary}

Instructions:
Evaluate whether this posting exhibits fraudulent patterns (fake checks, phishing, MLM, identity theft, advance-fee scam) or appears to be a legitimate opportunity.
Respond with a JSON object containing exactly these two keys:
1. "verdict": exactly one of "scam", "legitimate", or "suspicious"
2. "reasoning": a clear, 1-2 sentence inspectable explanation justifying your decision.

JSON response only:"""

        # 3. Invoke model
        try:
            response = client.models.generate_content(
                model=self.model_name,
                contents=prompt,
            )
            raw_text = response.text if hasattr(response, "text") else str(response)

            # Parse JSON
            verdict, reasoning, confidence = self._parse_response(raw_text)
            return GeminiScamResult(
                success=True,
                verdict=verdict,
                reasoning=reasoning,
                confidence=confidence,
                raw_response=raw_text,
            )
        except Exception as exc:
            err_msg = str(exc)
            logger.exception("Gemini API call failed: %s", err_msg)
            is_429 = "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg or "quota" in err_msg.lower()
            return GeminiScamResult(
                success=False,
                is_quota_exhausted=is_429,
                error=f"Gemini API error: {err_msg}",
            )

    def _parse_response(self, text: str) -> tuple[str, str, float | None]:
        """Extract verdict, reasoning, and optional confidence from model response text."""
        # Clean markdown codeblocks if any
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
            v = str(data.get("verdict", "suspicious")).lower()
            if v not in {"scam", "legitimate", "suspicious"}:
                v = "suspicious"
            r = str(data.get("reasoning", "Ambiguous listing flagged for review."))
            c_raw = data.get("confidence")
            c = float(c_raw) if c_raw is not None else None
            return v, r, c
        except Exception:
            # Fallback regex search
            match_v = re.search(r'"verdict"\s*:\s*"([^"]+)"', cleaned, re.I)
            match_r = re.search(r'"reasoning"\s*:\s*"([^"]+)"', cleaned, re.I)
            match_c = re.search(r'"confidence"\s*:\s*([0-9.]+)', cleaned, re.I)
            v = match_v.group(1).lower() if match_v else "suspicious"
            if v not in {"scam", "legitimate", "suspicious"}:
                v = "suspicious"
            r = match_r.group(1) if match_r else "Ambiguous signals flagged for human review."
            c = float(match_c.group(1)) if match_c else None
            return v, r, c
