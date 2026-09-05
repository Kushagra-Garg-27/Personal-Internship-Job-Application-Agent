"""Deterministic scam and risk detection rules (Stage 2 - Phase 5).

Evaluates opportunities against pure deterministic rules without AI:
1. Duplicate content hash against confirmed scam signatures.
2. Keyword blocklist (hard reject for explicit scam demands, ambiguous for borderline phrasing).
3. Free-email-domain recruiter pattern (Gmail/Yahoo for corporate recruiters).
4. WHOIS domain-age check (newly registered domains < 30 days old trigger ambiguity).

Each rule returns: "clear", "reject", or "ambiguous".
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
import re
from typing import Any, Callable
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from core.config import settings
from core.repositories import scam_signature_repo

logger = logging.getLogger(__name__)


def normalize_content_text(text: str | None) -> str:
    """Normalize body text for consistent hashing (lowercase, collapsed whitespace)."""
    if not text:
        return ""
    # Strip whitespace, collapse internal spaces/newlines, lowercase
    collapsed = re.sub(r"\s+", " ", text.strip().lower())
    return collapsed


def compute_content_hash(text: str | None) -> str:
    """Compute deterministic SHA-256 hash of normalized text."""
    normalized = normalize_content_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def check_duplicate_content(
    opportunity: Any, session: Session | None = None
) -> tuple[str, dict[str, Any] | None]:
    """Check if opportunity description matches a known scam content signature.

    Returns:
        ("reject", reason) if matches confirmed scam signature.
        ("ambiguous", reason) if matches unconfirmed signature.
        ("clear", None) if no match.
    """
    desc = getattr(opportunity, "description", None)
    if not desc or not desc.strip():
        return "clear", None

    content_hash = compute_content_hash(desc)
    if session is not None:
        sig = scam_signature_repo.get_by_hash(session, content_hash)
        if sig is not None:
            if sig.confirmed_scam:
                return "reject", {
                    "rule": "duplicate_content_hash",
                    "detail": f"Content matches confirmed scam signature ({content_hash[:12]}...)",
                    "content_hash": content_hash,
                }
            return "ambiguous", {
                "rule": "duplicate_content_hash",
                "detail": f"Content matches previously flagged listing signature ({content_hash[:12]}...)",
                "content_hash": content_hash,
            }

    return "clear", None


def check_keyword_blocklist(
    opportunity: Any,
    blocklist: list[str] | None = None,
    suspicious_list: list[str] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Check for fraudulent fee requests, payment language, and get-rich phrasing.

    Returns:
        ("reject", reason) on explicit financial demands (registration fee, wire transfer, etc.)
        ("ambiguous", reason) on suspicious / get-rich / chat-interview phrasing.
        ("clear", None) if clean.
    """
    desc = getattr(opportunity, "description", "") or ""
    title = getattr(opportunity, "title", "") or ""
    full_text = f"{title} {desc}".lower()

    if not full_text.strip():
        return "clear", None

    hard_blocklist = blocklist if blocklist is not None else settings.SCAM_BLOCKLIST_KEYWORDS
    matched_hard = [kw for kw in hard_blocklist if kw.lower() in full_text]
    if matched_hard:
        return "reject", {
            "rule": "keyword_blocklist",
            "detail": f"Listing contains fraudulent blocklist phrases: {', '.join(matched_hard)}",
            "matched": matched_hard,
        }

    borderline_list = suspicious_list if suspicious_list is not None else settings.SCAM_SUSPICIOUS_KEYWORDS
    matched_suspicious = [kw for kw in borderline_list if kw.lower() in full_text]
    if matched_suspicious:
        return "ambiguous", {
            "rule": "suspicious_keywords",
            "detail": f"Listing contains borderline suspicious phrases: {', '.join(matched_suspicious)}",
            "matched": matched_suspicious,
        }

    return "clear", None


def check_free_email(
    opportunity: Any, free_domains: list[str] | None = None
) -> tuple[str, dict[str, Any] | None]:
    """Check if contact email is hosted on a free consumer domain instead of corporate domain.

    Returns:
        ("reject", reason) if well-known corporate brand claims free email recruiter (impersonation).
        ("ambiguous", reason) if generic listing provides only free email.
        ("clear", None) if corporate email or no email in listing.
    """
    desc = getattr(opportunity, "description", "") or ""
    meta = getattr(opportunity, "metadata_json", {}) or {}
    meta_str = str(meta)
    combined = f"{desc} {meta_str}"

    emails = re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", combined)
    if not emails:
        return "clear", None

    domains = set(free_domains if free_domains is not None else settings.FREE_EMAIL_DOMAINS)
    free_emails = [e for e in emails if e.split("@")[-1].lower() in domains]

    if not free_emails:
        return "clear", None

    company = (getattr(opportunity, "company", "") or "").strip()
    company_lower = company.lower()
    url = (getattr(opportunity, "url", "") or "").lower()

    # Brand impersonation check: company has corporate presence or known enterprise name
    corporate_signals = ["inc", "corp", "corporation", "ltd", "technologies", "google", "meta", "amazon", "apple", "microsoft"]
    has_corp_signal = any(sig in company_lower for sig in corporate_signals) or any(
        sig in url for sig in corporate_signals
    )

    if has_corp_signal:
        return "reject", {
            "rule": "free_email_recruiter",
            "detail": f"Corporate entity '{company}' recruiting via free consumer email '{free_emails[0]}'",
            "emails": free_emails,
        }

    return "ambiguous", {
        "rule": "free_email_recruiter",
        "detail": f"Recruiter contact email is on a free consumer domain: '{free_emails[0]}'",
        "emails": free_emails,
    }


# Standard established job boards whose domain age is known safe
_ESTABLISHED_JOB_PLATFORMS = {
    "greenhouse.io",
    "boards.greenhouse.io",
    "lever.co",
    "jobs.lever.co",
    "workday.com",
    "myworkdayjobs.com",
    "linkedin.com",
    "indeed.com",
    "ashbyhq.com",
    "smartrecruiters.com",
    "glassdoor.com",
}


def _extract_domain(url: str | None) -> str | None:
    """Extract root or host domain from URL."""
    if not url:
        return None
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.netloc or parsed.path).lower()
    host = host.split(":")[0]  # strip port if any
    return host if host else None


def check_whois_domain(
    opportunity: Any,
    min_age_days: int | None = None,
    whois_lookup_fn: Callable[[str], Any] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Check posting URL domain creation age via WHOIS.

    Young domains (< 30 days) are a suspicion signal that triggers ambiguity.
    Established job platforms or domains >= 180 days are clear.

    Returns:
        ("ambiguous", reason) if domain is very new (< min_age_days).
        ("clear", None) if domain is established or lookup is skipped/fails.
    """
    url = getattr(opportunity, "url", None)
    domain = _extract_domain(url)
    if not domain:
        return "clear", None

    # Skip known established applicant platforms
    for safe_platform in _ESTABLISHED_JOB_PLATFORMS:
        if domain == safe_platform or domain.endswith(f".{safe_platform}"):
            return "clear", None

    threshold_days = min_age_days if min_age_days is not None else settings.WHOIS_MIN_DOMAIN_AGE_DAYS

    lookup = whois_lookup_fn
    if lookup is None:
        try:
            import whois

            lookup = whois.whois
        except Exception:
            logger.debug("python-whois not available or failed to import")
            return "clear", None

    try:
        w = lookup(domain)
        if not w:
            return "clear", None

        creation_date = getattr(w, "creation_date", None)
        if isinstance(creation_date, list):
            creation_date = creation_date[0]

        if creation_date and isinstance(creation_date, datetime):
            if creation_date.tzinfo is None:
                creation_date = creation_date.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            age_days = (now - creation_date).days

            if age_days < 0:
                age_days = 0

            if age_days < threshold_days:
                return "ambiguous", {
                    "rule": "whois_domain_age",
                    "detail": f"Domain '{domain}' was registered {age_days} days ago (< {threshold_days} days threshold)",
                    "domain": domain,
                    "age_days": age_days,
                }
    except Exception as exc:
        logger.debug("WHOIS lookup failed for domain '%s': %s", domain, exc)
        return "clear", None

    return "clear", None


def evaluate_deterministic_scam_rules(
    opportunity: Any,
    session: Session | None = None,
    whois_lookup_fn: Callable[[str], Any] | None = None,
) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
    """Execute all deterministic rules in sequence.

    Order:
    1. Duplicate content hash (immediate high-confidence reject if confirmed)
    2. Keyword blocklist (hard phrases reject, borderline phrases ambiguous)
    3. Free email recruiter pattern (impersonation reject, generic ambiguous)
    4. WHOIS domain age (< 30 days ambiguous)

    Returns:
        (overall_verdict, primary_reason, all_triggered_signals)
        where overall_verdict is "reject", "ambiguous", or "clear".
    """
    signals: list[dict[str, Any]] = []

    # 1. Duplicate content hash
    v, reason = check_duplicate_content(opportunity, session=session)
    if reason:
        signals.append({"verdict": v, **reason})
    if v == "reject":
        return "reject", reason, signals

    # 2. Keyword blocklist
    v, reason = check_keyword_blocklist(opportunity)
    if reason:
        signals.append({"verdict": v, **reason})
    if v == "reject":
        return "reject", reason, signals

    # 3. Free email pattern
    v, reason = check_free_email(opportunity)
    if reason:
        signals.append({"verdict": v, **reason})
    if v == "reject":
        return "reject", reason, signals

    # 4. WHOIS domain age
    v, reason = check_whois_domain(opportunity, whois_lookup_fn=whois_lookup_fn)
    if reason:
        signals.append({"verdict": v, **reason})
    if v == "reject":
        return "reject", reason, signals

    # If any rule returned ambiguous, overall is ambiguous
    ambiguous_signals = [s for s in signals if s.get("verdict") == "ambiguous"]
    if ambiguous_signals:
        primary = ambiguous_signals[0]
        return "ambiguous", primary, signals

    return "clear", None, signals
