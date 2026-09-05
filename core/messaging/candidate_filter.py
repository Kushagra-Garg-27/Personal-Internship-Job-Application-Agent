"""Deterministic candidate filter — triage emails before classification.

Messages that fail this filter are discarded silently and never reach
the LLM or database.  This keeps cost and storage down — only emails
that plausibly come from recruiters for active applications pass through.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from core.models.opportunity import Application, Opportunity
from core.status import OpportunityStatus

logger = logging.getLogger(__name__)

# Statuses that indicate an active application (recruiter might reply)
ACTIVE_APPLICATION_STATUSES = {
    OpportunityStatus.APPLIED.value,
    OpportunityStatus.SUBMITTED.value,
    OpportunityStatus.INTERVIEW.value,
    OpportunityStatus.OFFERED.value,
}

# No-reply addresses to always skip
NOREPLY_PATTERNS = re.compile(
    r"^(noreply|no-reply|no_reply|donotreply|do-not-reply|mailer-daemon|postmaster)@",
    re.IGNORECASE,
)

# Subject keywords that suggest a recruiter response
RECRUITER_SUBJECT_KEYWORDS = [
    "application",
    "position",
    "interview",
    "offer",
    "candidate",
    "hiring",
    "role",
    "opportunity",
    "regarding your",
    "next steps",
    "follow up",
    "following up",
    "screening",
    "assessment",
    "coding challenge",
]


@dataclass
class CandidateFilterResult:
    """Result of the candidate filter evaluation."""

    is_candidate: bool
    matched_rule: str
    matched_company: str | None = None


def extract_email_from_sender(sender: str) -> str:
    """Extract the bare email address from a 'Name <email>' header."""
    match = re.search(r"<([^>]+)>", sender)
    return match.group(1).lower() if match else sender.strip().lower()


def extract_domain(email: str) -> str:
    """Extract the domain portion of an email address."""
    parts = email.split("@")
    return parts[1] if len(parts) == 2 else ""


def is_noreply(email: str) -> bool:
    """Check if the email matches a known no-reply pattern."""
    return bool(NOREPLY_PATTERNS.match(email))


def _get_active_company_domains(session: Session) -> set[str]:
    """Fetch company domains from active application opportunities."""
    results = (
        session.query(Opportunity.company, Opportunity.url)
        .join(Application, Application.opportunity_id == Opportunity.id)
        .filter(Opportunity.status.in_(ACTIVE_APPLICATION_STATUSES))
        .distinct()
        .all()
    )

    domains: set[str] = set()
    for company, url in results:
        # Normalise company name as a pseudo-domain
        if company:
            normalised = company.lower().replace(" ", "").replace(",", "").replace(".", "")
            domains.add(normalised)

        # Extract actual domain from URL
        if url:
            domain_match = re.search(r"https?://(?:www\.)?([^/]+)", url)
            if domain_match:
                domain = domain_match.group(1).lower()
                # Strip common suffixes for fuzzy matching
                base = domain.split(".")[0] if "." in domain else domain
                domains.add(base)
                domains.add(domain)

    return domains


def _get_active_companies(session: Session) -> set[str]:
    """Fetch distinct company names from active applications."""
    results = (
        session.query(Opportunity.company)
        .join(Application, Application.opportunity_id == Opportunity.id)
        .filter(Opportunity.status.in_(ACTIVE_APPLICATION_STATUSES))
        .distinct()
        .all()
    )
    return {row[0].lower() for row in results if row[0]}


def evaluate_candidate_filter(
    sender: str,
    subject: str,
    user_email: str | None,
    session: Session | None = None,
) -> CandidateFilterResult:
    """Evaluate whether an email is a candidate recruiter response.

    Parameters
    ----------
    sender : str
        Full ``From`` header (e.g. ``"Jane <jane@acme.com>"``).
    subject : str
        Email subject line.
    user_email : str | None
        The user's own email address (to filter self-sent).
    session : Session | None
        DB session for domain/company lookups.  If ``None``, only
        subject-keyword matching is used.
    """
    email = extract_email_from_sender(sender)
    domain = extract_domain(email)

    # Rule 1: Self-sent exclusion
    if user_email and email == user_email.lower():
        return CandidateFilterResult(
            is_candidate=False,
            matched_rule="self_sent",
        )

    # Rule 2: No-reply exclusion
    if is_noreply(email):
        return CandidateFilterResult(
            is_candidate=False,
            matched_rule="noreply_sender",
        )

    # Rule 3: Sender domain matches active application company
    if session and domain:
        active_domains = _get_active_company_domains(session)
        domain_base = domain.split(".")[0] if "." in domain else domain
        if domain_base in active_domains or domain in active_domains:
            return CandidateFilterResult(
                is_candidate=True,
                matched_rule="sender_domain_match",
                matched_company=domain,
            )

    # Rule 4: Company name appears in sender
    if session:
        active_companies = _get_active_companies(session)
        sender_lower = sender.lower()
        for company in active_companies:
            if company in sender_lower or company in domain:
                return CandidateFilterResult(
                    is_candidate=True,
                    matched_rule="company_name_in_sender",
                    matched_company=company,
                )

    # Rule 5: Subject-line keyword matching
    subject_lower = subject.lower() if subject else ""
    for keyword in RECRUITER_SUBJECT_KEYWORDS:
        if keyword in subject_lower:
            return CandidateFilterResult(
                is_candidate=True,
                matched_rule="subject_keyword_match",
                matched_company=None,
            )

    # No match — discard
    return CandidateFilterResult(
        is_candidate=False,
        matched_rule="no_match",
    )
