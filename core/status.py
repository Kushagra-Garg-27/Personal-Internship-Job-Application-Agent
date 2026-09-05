"""Opportunity status enum, reliability tiers, and allowed-transition map.

This module is the single source of truth for the status machine.  Every
status change in the system must go through the service layer, which
validates against ``ALLOWED_TRANSITIONS`` before writing.

Adding a new status value:
    1. Add it to ``OpportunityStatus``.
    2. Add its outgoing transitions to ``ALLOWED_TRANSITIONS``.
    3. Add it as an incoming transition on any existing statuses that
       should be able to reach it.
    No migration required — status is stored as a ``String(30)`` column.
"""

from __future__ import annotations

from enum import StrEnum


class OpportunityStatus(StrEnum):
    """All possible statuses in the opportunity lifecycle.

    Ordered roughly by the expected flow from discovery to resolution.
    """

    # ── Discovery & qualification ─────────────────────────────────────
    DISCOVERED = "discovered"
    INELIGIBLE = "ineligible"
    SCAM_RISK_REJECTED = "scam_risk_rejected"
    SCAM_REVIEW_PENDING = "scam_review_pending"

    # ── Scoring & review ──────────────────────────────────────────────
    RECOMMENDED = "recommended"
    REJECTED_BY_USER = "rejected_by_user"
    DISMISSED = "dismissed"

    # ── Application pipeline ─────────────────────────────────────────
    READY_TO_APPLY = "ready_to_apply"
    AWAITING_SUBMISSION = "awaiting_submission"
    MANUAL_APPLICATION_REQUIRED = "manual_application_required"
    APPLIED = "applied"
    SUBMITTED = "submitted"

    # ── Post-submission ───────────────────────────────────────────────
    INTERVIEW = "interview"
    INTERVIEW_SCHEDULED = "interview_scheduled"
    OFFERED = "offered"
    OFFER_RECEIVED = "offer_received"
    ACCEPTED = "accepted"
    REJECTED_BY_RECRUITER = "rejected_by_recruiter"
    WITHDRAWN = "withdrawn"

    # ── Lifecycle ─────────────────────────────────────────────────────
    EXPIRED = "expired"


class ReliabilityTier(StrEnum):
    """How reliable/tested the discovery adapter that found this listing is."""

    STABLE = "stable"
    EXPERIMENTAL = "experimental"
    DISCOVERY_ONLY = "discovery_only"


# ── Allowed-transition map ────────────────────────────────────────────────
#
# Keys   = current status
# Values = set of statuses the opportunity can transition TO.
#
# Terminal states (no outgoing transitions) are simply absent as keys,
# or map to an empty set.
#
# A few "return" transitions are allowed for re-evaluation:
#   ineligible → discovered        (criteria changed)
#   scam_risk_rejected → discovered (false positive corrected)
#   rejected_by_user → recommended  (user reconsidered)
#   scam_review_pending → recommended / scam_risk_rejected (human decision)

S = OpportunityStatus  # short alias for readability

ALLOWED_TRANSITIONS: dict[OpportunityStatus, set[OpportunityStatus]] = {
    S.DISCOVERED: {S.RECOMMENDED, S.INELIGIBLE, S.EXPIRED, S.SCAM_RISK_REJECTED, S.SCAM_REVIEW_PENDING},
    S.INELIGIBLE: {S.DISCOVERED},
    S.SCAM_RISK_REJECTED: {S.DISCOVERED},
    S.SCAM_REVIEW_PENDING: {S.SCAM_RISK_REJECTED, S.RECOMMENDED, S.DISCOVERED, S.EXPIRED},
    S.RECOMMENDED: {S.READY_TO_APPLY, S.REJECTED_BY_USER, S.EXPIRED, S.SCAM_RISK_REJECTED, S.DISMISSED},
    S.REJECTED_BY_USER: {S.RECOMMENDED},
    S.READY_TO_APPLY: {S.AWAITING_SUBMISSION, S.APPLIED, S.MANUAL_APPLICATION_REQUIRED, S.EXPIRED, S.REJECTED_BY_USER, S.WITHDRAWN},
    S.AWAITING_SUBMISSION: {S.APPLIED, S.MANUAL_APPLICATION_REQUIRED, S.EXPIRED, S.REJECTED_BY_USER, S.WITHDRAWN},
    S.MANUAL_APPLICATION_REQUIRED: {S.APPLIED, S.READY_TO_APPLY, S.EXPIRED, S.REJECTED_BY_USER, S.WITHDRAWN},
    S.APPLIED: {S.SUBMITTED, S.INTERVIEW, S.INTERVIEW_SCHEDULED, S.OFFERED, S.OFFER_RECEIVED, S.REJECTED_BY_RECRUITER, S.EXPIRED, S.WITHDRAWN},
    S.SUBMITTED: {S.INTERVIEW, S.INTERVIEW_SCHEDULED, S.OFFERED, S.OFFER_RECEIVED, S.REJECTED_BY_RECRUITER, S.EXPIRED, S.WITHDRAWN},
    S.INTERVIEW: {S.INTERVIEW_SCHEDULED, S.OFFERED, S.OFFER_RECEIVED, S.REJECTED_BY_RECRUITER, S.WITHDRAWN},
    S.INTERVIEW_SCHEDULED: {S.INTERVIEW, S.INTERVIEW_SCHEDULED, S.OFFERED, S.OFFER_RECEIVED, S.REJECTED_BY_RECRUITER, S.WITHDRAWN},
    S.OFFERED: {S.OFFER_RECEIVED, S.ACCEPTED, S.REJECTED_BY_RECRUITER, S.WITHDRAWN},
    S.OFFER_RECEIVED: {S.OFFERED, S.ACCEPTED, S.REJECTED_BY_RECRUITER, S.WITHDRAWN},
    # Terminal states — no outgoing transitions
    S.ACCEPTED: set(),
    S.REJECTED_BY_RECRUITER: set(),
    S.WITHDRAWN: set(),
    S.EXPIRED: set(),
    S.DISMISSED: set(),
}

# All valid status values as a set (for ORM validation)
ALL_STATUSES: set[str] = {s.value for s in OpportunityStatus}
ALL_TIERS: set[str] = {t.value for t in ReliabilityTier}


class InvalidTransitionError(Exception):
    """Raised when a status transition violates the allowed-transition map."""

    def __init__(self, current: str, requested: str):
        self.current = current
        self.requested = requested
        super().__init__(
            f"Invalid status transition: {current!r} → {requested!r}. "
            f"Allowed from {current!r}: {_allowed_from(current)}"
        )


def _allowed_from(status: str) -> set[str]:
    """Return the set of statuses reachable from *status*."""
    try:
        s = OpportunityStatus(status)
    except ValueError:
        return set()
    return {t.value for t in ALLOWED_TRANSITIONS.get(s, set())}
