"""Application linker — match recruiter messages to application records.

The linking logic follows the fail-closed principle: a wrong link is
worse than no link.  If the match isn't confident, we leave
``application_id = None`` and set ``link_confidence = 'none'``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from core.models.opportunity import Application, Opportunity
from core.status import OpportunityStatus

logger = logging.getLogger(__name__)

# Statuses where a recruiter reply is plausible
LINKABLE_STATUSES = {
    OpportunityStatus.APPLIED.value,
    OpportunityStatus.SUBMITTED.value,
    OpportunityStatus.INTERVIEW.value,
    OpportunityStatus.INTERVIEW_SCHEDULED.value,
    OpportunityStatus.OFFERED.value,
    OpportunityStatus.OFFER_RECEIVED.value,
}


@dataclass
class LinkResult:
    """Result of the application linking attempt."""

    application_id: int | None
    confidence: str  # "high", "low", "none"
    reason: str


def _normalise_company(name: str) -> str:
    """Normalise a company name for fuzzy comparison."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _extract_domain_base(url: str | None) -> str | None:
    """Extract base domain from a URL (e.g., 'acme' from 'https://www.acme.com/...')."""
    if not url:
        return None
    match = re.search(r"https?://(?:www\.)?([^/]+)", url)
    if match:
        domain = match.group(1).lower()
        parts = domain.split(".")
        # Return the base (e.g., 'acme' from 'acme.com')
        return parts[0] if len(parts) >= 2 else domain
    return None


def link_message_to_application(
    sender_domain: str | None,
    subject: str | None,
    body_preview: str | None,
    thread_id: str | None,
    session: Session,
    existing_thread_links: dict[str, int] | None = None,
) -> LinkResult:
    """Attempt to link a recruiter message to an application record.

    Strategies (in priority order):
    1. Thread-based inheritance — if a previous message in the same
       thread is already linked, inherit that link.
    2. Domain match — sender domain matches opportunity company/URL domain.
    3. Subject/body cross-reference — company name or job title from an
       active application appears in the subject or body.

    If multiple matches are found, we refuse to guess → ``application_id = None``,
    ``confidence = 'low'``.
    """
    # Strategy 1: Thread-based inheritance
    if thread_id and existing_thread_links and thread_id in existing_thread_links:
        app_id = existing_thread_links[thread_id]
        return LinkResult(
            application_id=app_id,
            confidence="high",
            reason=f"Thread {thread_id} previously linked to application {app_id}",
        )

    # Fetch active applications with their opportunity details
    active_apps = (
        session.query(Application, Opportunity)
        .join(Opportunity, Application.opportunity_id == Opportunity.id)
        .filter(Opportunity.status.in_(LINKABLE_STATUSES))
        .all()
    )

    if not active_apps:
        return LinkResult(
            application_id=None,
            confidence="none",
            reason="No active applications found",
        )

    candidates: list[tuple[int, str]] = []  # (application_id, reason)

    search_text = f"{subject or ''} {body_preview or ''}".lower()
    sender_domain_base = sender_domain.split(".")[0] if sender_domain and "." in sender_domain else sender_domain

    for app, opp in active_apps:
        score_reasons: list[str] = []

        # Strategy 2: Domain match
        if sender_domain_base:
            opp_domain_base = _extract_domain_base(opp.url)
            company_normalised = _normalise_company(opp.company)

            if opp_domain_base and sender_domain_base == opp_domain_base:
                score_reasons.append(f"sender domain '{sender_domain}' matches opportunity URL domain")

            if sender_domain_base == company_normalised:
                score_reasons.append(f"sender domain matches company '{opp.company}'")

            # Also try: sender domain *contains* normalised company or vice versa
            if sender_domain_base and company_normalised:
                if company_normalised in sender_domain_base or sender_domain_base in company_normalised:
                    if not score_reasons:  # Don't double-count
                        score_reasons.append(f"sender domain fuzzy-matches company '{opp.company}'")

        # Strategy 3: Subject/body cross-reference
        company_lower = opp.company.lower() if opp.company else ""
        title_lower = opp.title.lower() if opp.title else ""

        if company_lower and len(company_lower) > 2 and company_lower in search_text:
            score_reasons.append(f"company '{opp.company}' found in message text")

        if title_lower and len(title_lower) > 5 and title_lower in search_text:
            score_reasons.append(f"job title '{opp.title}' found in message text")

        if score_reasons:
            candidates.append((app.id, "; ".join(score_reasons)))

    # Evaluate candidates
    if len(candidates) == 0:
        return LinkResult(
            application_id=None,
            confidence="none",
            reason="No matching application found",
        )

    if len(candidates) == 1:
        app_id, reason = candidates[0]
        return LinkResult(
            application_id=app_id,
            confidence="high",
            reason=reason,
        )

    # Multiple matches — refuse to guess (fail-closed)
    candidate_ids = [c[0] for c in candidates]
    return LinkResult(
        application_id=None,
        confidence="low",
        reason=f"Ambiguous match: {len(candidates)} candidates ({candidate_ids}); refusing to guess",
    )
