# ── Opportunity & Application status enums, reliability tiers, and allowed-transition maps.
#
# This module is the single source of truth for the status machine. Every
# status change in the system must go through the service layer, which
# validates against allowed transitions before writing.

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
    # Legacy alias for APPLIED; retained for backward compatibility:
    SUBMITTED = "submitted"

    # ── Post-submission ───────────────────────────────────────────────
    # Legacy alias for INTERVIEW_SCHEDULED; retained for backward compatibility:
    INTERVIEW = "interview"
    INTERVIEW_SCHEDULED = "interview_scheduled"
    # Legacy alias for OFFER_RECEIVED; retained for backward compatibility:
    OFFERED = "offered"
    OFFER_RECEIVED = "offer_received"
    ACCEPTED = "accepted"
    REJECTED_BY_RECRUITER = "rejected_by_recruiter"
    WITHDRAWN = "withdrawn"

    # ── Lifecycle ─────────────────────────────────────────────────────
    EXPIRED = "expired"


class ApplicationStatus(StrEnum):
    """Lifecycle statuses for an individual Application attempt.

    Tracks the discrete state of a specific submission attempt.
    """

    PENDING = "pending"
    FORM_FILLED = "form_filled"
    SUBMITTED = "submitted"
    FAILED = "failed"


class ReliabilityTier(StrEnum):
    """How reliable/tested the discovery adapter that found this listing is."""

    STABLE = "stable"
    EXPERIMENTAL = "experimental"
    DISCOVERY_ONLY = "discovery_only"


# ── Canonical vocabulary mapping & normalizer ────────────────────────────

LEGACY_OPPORTUNITY_STATUS_MAP: dict[str, OpportunityStatus] = {
    "submitted": OpportunityStatus.APPLIED,
    "interview": OpportunityStatus.INTERVIEW_SCHEDULED,
    "offered": OpportunityStatus.OFFER_RECEIVED,
}


def normalize_opportunity_status(status: str | OpportunityStatus) -> OpportunityStatus:
    """Normalize legacy status names to their canonical OpportunityStatus equivalents."""
    raw = status.value if isinstance(status, OpportunityStatus) else str(status).lower()
    if raw in LEGACY_OPPORTUNITY_STATUS_MAP:
        return LEGACY_OPPORTUNITY_STATUS_MAP[raw]
    return OpportunityStatus(raw)


# ── Allowed-transition maps ──────────────────────────────────────────────

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
    S.APPLIED: {S.SUBMITTED, S.INTERVIEW, S.INTERVIEW_SCHEDULED, S.OFFERED, S.OFFER_RECEIVED, S.REJECTED_BY_RECRUITER, S.EXPIRED, S.WITHDRAWN},
    # Backwards-compatibility for legacy statuses
    S.SUBMITTED: {S.APPLIED, S.INTERVIEW, S.INTERVIEW_SCHEDULED, S.OFFERED, S.OFFER_RECEIVED, S.REJECTED_BY_RECRUITER, S.EXPIRED, S.WITHDRAWN},
    S.INTERVIEW: {S.INTERVIEW, S.INTERVIEW_SCHEDULED, S.OFFERED, S.OFFER_RECEIVED, S.REJECTED_BY_RECRUITER, S.WITHDRAWN},
    S.INTERVIEW_SCHEDULED: {S.INTERVIEW, S.INTERVIEW_SCHEDULED, S.OFFERED, S.OFFER_RECEIVED, S.REJECTED_BY_RECRUITER, S.WITHDRAWN},
    S.OFFERED: {S.OFFERED, S.OFFER_RECEIVED, S.ACCEPTED, S.REJECTED_BY_RECRUITER, S.WITHDRAWN},
    S.OFFER_RECEIVED: {S.OFFERED, S.ACCEPTED, S.REJECTED_BY_RECRUITER, S.WITHDRAWN},
    # Terminal states — no outgoing transitions
    S.ACCEPTED: set(),
    S.REJECTED_BY_RECRUITER: set(),
    S.WITHDRAWN: set(),
    S.EXPIRED: set(),
    S.DISMISSED: set(),
}

AS = ApplicationStatus

APPLICATION_ALLOWED_TRANSITIONS: dict[ApplicationStatus, set[ApplicationStatus]] = {
    AS.PENDING: {AS.FORM_FILLED, AS.SUBMITTED, AS.FAILED},
    AS.FORM_FILLED: {AS.SUBMITTED, AS.FAILED},
    AS.FAILED: {AS.PENDING},  # Allows retry attempts
    AS.SUBMITTED: set(),      # Terminal state for this attempt
}

# All valid status values as a set (for ORM validation)
ALL_STATUSES: set[str] = {s.value for s in OpportunityStatus}
ALL_APPLICATION_STATUSES: set[str] = {s.value for s in ApplicationStatus}
ALL_TIERS: set[str] = {t.value for t in ReliabilityTier}


class InvalidTransitionError(Exception):
    """Raised when an opportunity status transition violates the allowed-transition map."""

    def __init__(self, current: str, requested: str):
        self.current = current
        self.requested = requested
        super().__init__(
            f"Invalid status transition: {current!r} → {requested!r}. "
            f"Allowed from {current!r}: {_allowed_from(current)}"
        )


class InvalidApplicationTransitionError(Exception):
    """Raised when an application status transition violates the allowed-transition map."""

    def __init__(self, current: str, requested: str):
        self.current = current
        self.requested = requested
        super().__init__(
            f"Invalid application status transition: {current!r} → {requested!r}. "
            f"Allowed from {current!r}: {_allowed_application_from(current)}"
        )


def _allowed_from(status: str) -> set[str]:
    """Return the set of statuses reachable from opportunity *status*."""
    try:
        s = normalize_opportunity_status(status)
    except ValueError:
        return set()
    return {t.value for t in ALLOWED_TRANSITIONS.get(s, set())}


def _allowed_application_from(status: str) -> set[str]:
    """Return the set of application statuses reachable from *status*."""
    try:
        s = ApplicationStatus(status)
    except ValueError:
        return set()
    return {t.value for t in APPLICATION_ALLOWED_TRANSITIONS.get(s, set())}
