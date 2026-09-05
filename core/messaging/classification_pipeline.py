"""Classification pipeline — rules first, LLM for ambiguous cases.

Same architectural pattern as Phase 5's ScamRiskStage: deterministic
rules run first; only the minority of messages that can't be
confidently classified get sent to the LLM.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from core.messaging.classifier_rules import (
    classify_by_rules,
    RULES_CONFIDENCE_THRESHOLD,
)
from core.messaging.classifier_llm import MessageClassifierLLM, LLMClassificationResult

logger = logging.getLogger(__name__)


@dataclass
class ClassificationResult:
    """Final classification output from the pipeline."""

    classification: str
    confidence: float | None
    source: str  # "rules" or "llm"
    reasoning: str | None = None
    matched_patterns: list[str] | None = None


def classify_message(
    subject: str,
    body_preview: str,
    sender: str | None = None,
    llm_client: MessageClassifierLLM | None = None,
) -> ClassificationResult:
    """Run the full classification pipeline for a recruiter message.

    1. Deterministic rules first.
    2. If rules confidence ≥ threshold (0.8), the verdict is final.
    3. Otherwise, escalate to LLM.
    4. If LLM fails (quota, API error), fall back to rules verdict
       or ``unclassified``.
    """
    # Step 1: Rules-based classification
    rules_result = classify_by_rules(subject, body_preview)

    if rules_result.confidence >= RULES_CONFIDENCE_THRESHOLD:
        logger.debug(
            "Message classified by rules: %s (%.2f)",
            rules_result.classification,
            rules_result.confidence,
        )
        return ClassificationResult(
            classification=rules_result.classification,
            confidence=rules_result.confidence,
            source="rules",
            matched_patterns=rules_result.matched_patterns,
        )

    # Step 2: Escalate to LLM
    logger.info(
        "Rules produced low-confidence result (%s, %.2f); escalating to LLM",
        rules_result.classification,
        rules_result.confidence,
    )

    client = llm_client or MessageClassifierLLM()
    llm_result = client.classify(
        subject=subject,
        body_preview=body_preview,
        sender=sender,
    )

    # Step 3: Handle LLM response
    if llm_result.success:
        return ClassificationResult(
            classification=llm_result.classification,
            confidence=llm_result.confidence,
            source="llm",
            reasoning=llm_result.reasoning,
        )

    # Step 4: LLM failed — fail closed
    logger.warning(
        "LLM classification failed (%s); using rules fallback or unclassified",
        llm_result.error,
    )

    # If rules had a reasonable guess (even low confidence), use it
    if rules_result.classification != "unclassified" and rules_result.confidence > 0.5:
        return ClassificationResult(
            classification=rules_result.classification,
            confidence=rules_result.confidence,
            source="rules",
            reasoning=f"LLM unavailable ({llm_result.error}); used rules fallback.",
            matched_patterns=rules_result.matched_patterns,
        )

    return ClassificationResult(
        classification="unclassified",
        confidence=None,
        source="rules",
        reasoning=f"LLM unavailable ({llm_result.error}); rules inconclusive.",
    )
