"""Gmail job-alert parser — discovery_only tier.

Parses structured listing data from known job-alert senders
(Internshala, Unstop) using deterministic rules-based extraction.
No LLM, no ML — just sender-domain matching and regex patterns.

Every opportunity from this source is tagged ``discovery_only`` — a hard
signal to every later phase that this listing must never be auto-filled
or auto-submitted.
"""

from __future__ import annotations

import base64
import logging
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from core.config import settings
from core.discovery.base import DiscoverySource, RawOpportunity
from core.discovery.gmail_client import (
    build_gmail_service,
    load_gmail_state,
    poll_new_messages,
    save_gmail_state,
)
from core.status import ReliabilityTier

logger = logging.getLogger(__name__)

# ── Known alert senders ──────────────────────────────────────────────────

ALERT_SENDER_PATTERNS: list[dict] = [
    {
        "name": "internshala",
        "domain_pattern": r"@internshala\.com$",
        "company_default": "Internshala",
    },
    {
        "name": "unstop",
        "domain_pattern": r"@unstop\.com$",
        "company_default": "Unstop",
    },
]


class GmailAlertSource(DiscoverySource):
    """Parse job-alert emails from Gmail for known senders.

    This source only handles job-alert parsing.  Recruiter-response
    classification is a Phase 7 concern — kept in a separate module.
    """

    name = "gmail_alert"
    tier = ReliabilityTier.DISCOVERY_ONLY

    def __init__(self, *, gmail_service=None):
        self._service = gmail_service

    def discover(self) -> list[RawOpportunity]:
        """Poll Gmail for new messages, parse known alert emails."""
        service = self._service or build_gmail_service()
        if service is None:
            logger.info("Gmail not configured, skipping alert source")
            return []

        state = load_gmail_state()
        last_id = state.get("history_id")

        messages, new_id = poll_new_messages(service, last_id)

        # Persist the new history ID immediately
        state["history_id"] = new_id
        save_gmail_state(state)

        results: list[RawOpportunity] = []
        for msg in messages:
            try:
                opps = self._parse_message(msg)
                results.extend(opps)
            except Exception:
                logger.warning(
                    "Gmail alert: failed to parse message %s",
                    msg.get("id", "?"),
                )

        logger.info("Gmail alerts: parsed %d opportunities from %d messages",
                     len(results), len(messages))
        return results

    def _parse_message(self, msg: dict) -> list[RawOpportunity]:
        """Extract opportunities from a single Gmail message."""
        headers = {
            h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }
        sender = headers.get("from", "")
        subject = headers.get("subject", "")

        # Check if this is from a known alert sender
        sender_config = self._match_sender(sender)
        if sender_config is None:
            return []  # Not a known alert email — skip silently

        # Extract body text
        body_html = self._extract_body(msg)
        if not body_html:
            return []

        # Parse opportunities from the email body
        return self._extract_opportunities(
            body_html, subject, sender_config
        )

    @staticmethod
    def _match_sender(sender: str) -> dict | None:
        """Check if the sender matches any known alert pattern."""
        # Extract email from "Name <email>" format
        email_match = re.search(r"<([^>]+)>", sender)
        email = email_match.group(1) if email_match else sender

        for config in ALERT_SENDER_PATTERNS:
            if re.search(config["domain_pattern"], email, re.IGNORECASE):
                return config
        return None

    @staticmethod
    def _extract_body(msg: dict) -> str:
        """Extract the HTML body from a Gmail message."""
        payload = msg.get("payload", {})

        def _find_html(part: dict) -> str | None:
            mime = part.get("mimeType", "")
            if mime == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            for sub in part.get("parts", []):
                result = _find_html(sub)
                if result:
                    return result
            return None

        return _find_html(payload) or ""

    @staticmethod
    def _extract_opportunities(
        body_html: str, subject: str, sender_config: dict
    ) -> list[RawOpportunity]:
        """Extract structured listings from an alert email body.

        Uses BeautifulSoup to parse HTML and regex to find listing
        patterns.  This is deterministic, rules-based extraction.
        """
        soup = BeautifulSoup(body_html, "html.parser")
        text = soup.get_text(separator="\n", strip=True)

        opportunities: list[RawOpportunity] = []

        # Strategy 1: Find links with job-listing-like text
        for link in soup.find_all("a", href=True):
            href = link["href"]
            link_text = link.get_text(strip=True)

            # Skip generic/navigation links
            if not link_text or len(link_text) < 5:
                continue
            if any(skip in link_text.lower() for skip in [
                "unsubscribe", "view in browser", "manage", "privacy",
                "settings", "click here", "view all",
            ]):
                continue

            # Check if this looks like a job listing link
            if _is_job_link(href, sender_config["name"]):
                # Try to extract company from surrounding context
                company = _extract_company_near_link(link, sender_config)
                opportunities.append(RawOpportunity(
                    title=link_text,
                    company=company,
                    url=href,
                    source="gmail_alert",
                    metadata={
                        "alert_sender": sender_config["name"],
                        "email_subject": subject,
                    },
                ))

        # Strategy 2: Parse subject line for a single listing
        if not opportunities:
            title_match = re.search(
                r"(?:new (?:internship|job)|apply now[:\s]+)(.+)",
                subject, re.IGNORECASE,
            )
            if title_match:
                opportunities.append(RawOpportunity(
                    title=title_match.group(1).strip(),
                    company=sender_config["company_default"],
                    source="gmail_alert",
                    metadata={
                        "alert_sender": sender_config["name"],
                        "email_subject": subject,
                        "parsed_from": "subject_line",
                    },
                ))

        return opportunities


def _is_job_link(href: str, sender_name: str) -> bool:
    """Check if a URL looks like a job listing link for the given sender."""
    href_lower = href.lower()
    if sender_name == "internshala":
        return "internshala.com" in href_lower and (
            "/internship/" in href_lower or "/job/" in href_lower
        )
    if sender_name == "unstop":
        return "unstop.com" in href_lower and (
            "/competition/" in href_lower
            or "/opportunity/" in href_lower
            or "/hackathon/" in href_lower
        )
    return False


def _extract_company_near_link(link_tag, sender_config: dict) -> str:
    """Try to find the company name near a job link in the HTML."""
    # Look at parent and sibling elements for company info
    parent = link_tag.parent
    if parent:
        text = parent.get_text(separator=" | ", strip=True)
        # Common pattern: "Company Name | Job Title | Location"
        parts = [p.strip() for p in text.split("|") if p.strip()]
        if len(parts) >= 2:
            # The company is often a different segment than the link text
            link_text = link_tag.get_text(strip=True)
            for part in parts:
                if part != link_text and len(part) > 2:
                    return part

    return sender_config["company_default"]
