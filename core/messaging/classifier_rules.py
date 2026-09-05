"""Deterministic rules-based classifier for recruiter messages.

Runs first in the classification pipeline.  If the rules produce a
high-confidence verdict (≥ 0.8), it is final and the LLM is skipped.
Lower confidence → escalate to LLM.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Confidence threshold — at or above this, the rules verdict is final
RULES_CONFIDENCE_THRESHOLD = 0.8


@dataclass
class RulesClassificationResult:
    """Output of the deterministic classifier."""

    classification: str
    confidence: float
    matched_patterns: list[str]


# ── Pattern definitions ──────────────────────────────────────────────────
#
# Each entry: (classification, confidence, compiled_regex_pattern)
# Order matters: first match wins within a classification.

_INTERVIEW_PATTERNS = [
    (re.compile(r"schedule.*interview", re.I), 0.90),
    (re.compile(r"invite you (?:to|for) (?:an? )?interview", re.I), 0.92),
    (re.compile(r"next round", re.I), 0.85),
    (re.compile(r"calendar invite", re.I), 0.88),
    (re.compile(r"meet with (?:the|our) (?:team|hiring)", re.I), 0.87),
    (re.compile(r"phone (?:screen|call|interview)", re.I), 0.88),
    (re.compile(r"video (?:call|interview)", re.I), 0.88),
    (re.compile(r"on-?site (?:interview|visit)", re.I), 0.90),
    (re.compile(r"technical (?:round|interview)", re.I), 0.88),
    (re.compile(r"panel interview", re.I), 0.90),
]

_REJECTION_PATTERNS = [
    (re.compile(r"unfortunately", re.I), 0.82),
    (re.compile(r"not (?:moving|going) forward", re.I), 0.90),
    (re.compile(r"position has been filled", re.I), 0.92),
    (re.compile(r"decided not to proceed", re.I), 0.90),
    (re.compile(r"we (?:have )?decided to (?:go|move) (?:with|forward with) (?:another|other)", re.I), 0.92),
    (re.compile(r"regret to inform", re.I), 0.90),
    (re.compile(r"not (?:a|the) (?:right )?fit", re.I), 0.85),
    (re.compile(r"will not be (?:moving|proceeding)", re.I), 0.88),
    (re.compile(r"after careful (?:consideration|review).*(?:not|unable)", re.I), 0.85),
]

_OFFER_PATTERNS = [
    (re.compile(r"pleased to offer", re.I), 0.95),
    (re.compile(r"offer letter", re.I), 0.93),
    (re.compile(r"compensation package", re.I), 0.88),
    (re.compile(r"extend (?:an? )?offer", re.I), 0.93),
    (re.compile(r"starting salary", re.I), 0.85),
    (re.compile(r"welcome aboard", re.I), 0.88),
    (re.compile(r"congratulations.*(?:offer|position|selected)", re.I), 0.90),
]

_SCREENING_PATTERNS = [
    (re.compile(r"(?:a )?few questions", re.I), 0.80),
    (re.compile(r"assessment", re.I), 0.78),
    (re.compile(r"coding challenge", re.I), 0.85),
    (re.compile(r"take[- ]home", re.I), 0.85),
    (re.compile(r"screening (?:questions?|call)", re.I), 0.85),
    (re.compile(r"pre[- ]?interview (?:questions?|task)", re.I), 0.85),
    (re.compile(r"skills? (?:test|assessment)", re.I), 0.82),
]

_FOLLOW_UP_PATTERNS = [
    (re.compile(r"following up", re.I), 0.82),
    (re.compile(r"checking in", re.I), 0.78),
    (re.compile(r"update on your application", re.I), 0.85),
    (re.compile(r"status (?:of|update)", re.I), 0.78),
    (re.compile(r"wanted to (?:reach out|touch base)", re.I), 0.75),
]

_GENERIC_PATTERNS = [
    (re.compile(r"we (?:have )?received your application", re.I), 0.90),
    (re.compile(r"thank(?:s| you) for (?:applying|your (?:application|interest))", re.I), 0.85),
    (re.compile(r"application (?:has been |was )?received", re.I), 0.88),
    (re.compile(r"confirm(?:ation|ing) (?:your|receipt)", re.I), 0.85),
    (re.compile(r"auto[- ]?reply", re.I), 0.90),
]

# Classification name → pattern list (order = priority)
CLASSIFICATION_PATTERNS: list[tuple[str, list[tuple[re.Pattern, float]]]] = [
    ("offer", _OFFER_PATTERNS),
    ("interview_invite", _INTERVIEW_PATTERNS),
    ("rejection", _REJECTION_PATTERNS),
    ("screening_question", _SCREENING_PATTERNS),
    ("follow_up", _FOLLOW_UP_PATTERNS),
    ("generic", _GENERIC_PATTERNS),
]


def classify_by_rules(
    subject: str,
    body_preview: str,
) -> RulesClassificationResult:
    """Classify a recruiter message using deterministic pattern matching.

    Scans subject first (higher signal), then body preview.
    Returns the classification with the highest confidence.
    """
    text = f"{subject or ''}\n{body_preview or ''}"

    best_classification = "unclassified"
    best_confidence = 0.0
    matched: list[str] = []

    for classification, patterns in CLASSIFICATION_PATTERNS:
        for pattern, confidence in patterns:
            if pattern.search(text):
                if confidence > best_confidence:
                    best_classification = classification
                    best_confidence = confidence
                    matched = [pattern.pattern]
                elif confidence == best_confidence and classification == best_classification:
                    matched.append(pattern.pattern)

    return RulesClassificationResult(
        classification=best_classification,
        confidence=best_confidence,
        matched_patterns=matched,
    )
