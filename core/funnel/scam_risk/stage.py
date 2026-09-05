"""ScamRiskStage implementation (Stage 2 - Phase 5).

Runs deterministic scam/risk rules first.
Only if deterministic rules produce an ambiguous verdict is Gemini invoked.
Ambiguous results halt in a pending state for mandatory human approval.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from core.funnel.base import FunnelStage, StageVerdict
from core.funnel.scam_risk.gemini_client import GeminiScamClient
from core.funnel.scam_risk.rules import evaluate_deterministic_scam_rules

logger = logging.getLogger(__name__)


class ScamRiskStage(FunnelStage):
    """Stage 2: Scam and fraud risk classification."""

    name = "scam_risk"

    def __init__(
        self,
        gemini_client: GeminiScamClient | None = None,
        session: Session | None = None,
        whois_lookup_fn: Any = None,
    ) -> None:
        self.gemini_client = gemini_client or GeminiScamClient()
        self.session = session
        self.whois_lookup_fn = whois_lookup_fn

    def evaluate(
        self,
        opportunity: Any,
        profile: Any,
        resume_text: str | None = None,
    ) -> StageVerdict:
        """Evaluate an opportunity for scam and fraud indicators.

        Returns:
            - passed=True: Listing is clear, proceeds to relevance stage.
            - passed=False (reject): High-confidence scam detected.
            - passed=False (ambiguous): Requires human review via `scam_review_pending`.
            - passed=False (deferred): Gemini quota exhausted or API error, fail-closed.
        """
        # Step 1: Run deterministic rules
        verdict, reason, signals = evaluate_deterministic_scam_rules(
            opportunity,
            session=self.session,
            whois_lookup_fn=self.whois_lookup_fn,
        )

        # High-confidence reject: Hard stop, no AI call
        if verdict == "reject":
            return StageVerdict(
                stage_name=self.name,
                passed=False,
                reason=reason or {"rule": "deterministic_scam_filter", "detail": "Listing matched scam rules"},
                payload={"verdict": "reject", "signals": signals},
            )

        # Clear: Proceed to next funnel stage
        if verdict == "clear":
            return StageVerdict(
                stage_name=self.name,
                passed=True,
                reason=None,
                payload={"verdict": "clear", "signals": signals},
            )

        # Ambiguous: Escalate to Gemini free tier
        logger.info(
            "Opportunity %s marked ambiguous by deterministic rules; escalating to Gemini",
            getattr(opportunity, "id", "unknown"),
        )
        llm_result = self.gemini_client.evaluate_ambiguous(
            opportunity=opportunity,
            flagged_signals=signals,
        )

        # Fail-closed handling on quota exhaustion or API failure
        if not llm_result.success:
            return StageVerdict(
                stage_name=self.name,
                passed=False,
                reason={
                    "rule": "gemini_eval_deferred",
                    "detail": llm_result.error or "LLM evaluation deferred",
                    "is_quota_exhausted": llm_result.is_quota_exhausted,
                },
                payload={
                    "verdict": "deferred",
                    "is_quota_exhausted": llm_result.is_quota_exhausted,
                    "error": llm_result.error,
                    "signals": signals,
                },
            )

        # Ambiguous case evaluated by Gemini -> HALT for human review (never auto-commit)
        return StageVerdict(
            stage_name=self.name,
            passed=False,
            reason={
                "rule": "human_review_required",
                "detail": f"Listing marked ambiguous; LLM evaluated as '{llm_result.verdict}'. Awaiting human review.",
                "llm_verdict": llm_result.verdict,
                "llm_reasoning": llm_result.reasoning,
            },
            payload={
                "verdict": "ambiguous",
                "llm_verdict": llm_result.verdict,
                "llm_reasoning": llm_result.reasoning,
                "signals": signals,
            },
        )
