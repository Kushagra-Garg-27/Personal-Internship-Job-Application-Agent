"""Tests for the Gmail job-alert parser.

Uses mock Gmail API responses — no live API calls, no credentials needed.
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock, patch

import pytest

from core.discovery.sources.gmail_alerts import (
    GmailAlertSource,
    _extract_company_near_link,
    _is_job_link,
)


def _make_gmail_message(
    sender: str,
    subject: str,
    body_html: str,
    msg_id: str = "msg-001",
) -> dict:
    """Build a fake Gmail message in the format returned by messages.get(format='full')."""
    encoded_body = base64.urlsafe_b64encode(body_html.encode()).decode()
    return {
        "id": msg_id,
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
            ],
            "mimeType": "text/html",
            "body": {"data": encoded_body},
        },
    }


INTERNSHALA_HTML = """
<html><body>
<h2>New Internships for You</h2>
<div>
    <span>TechStartup Inc</span> |
    <a href="https://internshala.com/internship/detail/backend-developer-internship-at-techstartup">
        Backend Developer Internship
    </a> |
    <span>Mumbai</span>
</div>
<div>
    <span>DataCo</span> |
    <a href="https://internshala.com/internship/detail/data-analyst-at-dataco">
        Data Analyst Internship
    </a> |
    <span>Remote</span>
</div>
<a href="https://internshala.com/unsubscribe">Unsubscribe</a>
</body></html>
"""

UNSTOP_HTML = """
<html><body>
<h2>Recommended Opportunities</h2>
<div>
    <a href="https://unstop.com/competition/hackathon-2026-techfest">
        TechFest Hackathon 2026
    </a>
</div>
<a href="https://unstop.com/settings">Manage preferences</a>
</body></html>
"""


class TestSenderMatching:
    """Verify sender-domain pattern matching."""

    def test_internshala_noreply_matched(self):
        source = GmailAlertSource()
        result = source._match_sender("Internshala <noreply@internshala.com>")
        assert result is not None
        assert result["name"] == "internshala"

    def test_internshala_alerts_matched(self):
        result = GmailAlertSource._match_sender("alerts@internshala.com")
        assert result is not None

    def test_unstop_matched(self):
        result = GmailAlertSource._match_sender("Unstop <noreply@unstop.com>")
        assert result is not None
        assert result["name"] == "unstop"

    def test_unknown_sender_returns_none(self):
        result = GmailAlertSource._match_sender("spam@random-company.com")
        assert result is None

    def test_gmail_sender_not_matched(self):
        result = GmailAlertSource._match_sender("someone@gmail.com")
        assert result is None


class TestInternshalaAlertParsing:
    """Parse Internshala job-alert emails."""

    def test_extracts_internshala_listings(self):
        msg = _make_gmail_message(
            sender="Internshala <noreply@internshala.com>",
            subject="New internships matching your profile",
            body_html=INTERNSHALA_HTML,
        )
        source = GmailAlertSource()
        opps = source._parse_message(msg)
        assert len(opps) == 2

    def test_maps_title_from_link_text(self):
        msg = _make_gmail_message(
            sender="noreply@internshala.com",
            subject="New internships",
            body_html=INTERNSHALA_HTML,
        )
        source = GmailAlertSource()
        opps = source._parse_message(msg)
        titles = [o.title for o in opps]
        assert "Backend Developer Internship" in titles
        assert "Data Analyst Internship" in titles

    def test_maps_url_from_href(self):
        msg = _make_gmail_message(
            sender="noreply@internshala.com",
            subject="New internships",
            body_html=INTERNSHALA_HTML,
        )
        source = GmailAlertSource()
        opps = source._parse_message(msg)
        assert any("internshala.com/internship/" in o.url for o in opps)

    def test_filters_out_unsubscribe_links(self):
        msg = _make_gmail_message(
            sender="noreply@internshala.com",
            subject="New internships",
            body_html=INTERNSHALA_HTML,
        )
        source = GmailAlertSource()
        opps = source._parse_message(msg)
        # "Unsubscribe" link should NOT appear as an opportunity
        assert not any("unsubscribe" in (o.url or "").lower() for o in opps)

    def test_source_is_gmail_alert(self):
        msg = _make_gmail_message(
            sender="noreply@internshala.com",
            subject="New internships",
            body_html=INTERNSHALA_HTML,
        )
        source = GmailAlertSource()
        opps = source._parse_message(msg)
        assert all(o.source == "gmail_alert" for o in opps)

    def test_metadata_has_alert_sender(self):
        msg = _make_gmail_message(
            sender="noreply@internshala.com",
            subject="New internships",
            body_html=INTERNSHALA_HTML,
        )
        source = GmailAlertSource()
        opps = source._parse_message(msg)
        assert all(o.metadata["alert_sender"] == "internshala" for o in opps)


class TestUnstopAlertParsing:
    """Parse Unstop opportunity-alert emails."""

    def test_extracts_unstop_listings(self):
        msg = _make_gmail_message(
            sender="noreply@unstop.com",
            subject="New opportunities for you",
            body_html=UNSTOP_HTML,
        )
        source = GmailAlertSource()
        opps = source._parse_message(msg)
        assert len(opps) == 1
        assert opps[0].title == "TechFest Hackathon 2026"

    def test_filters_manage_preferences_link(self):
        msg = _make_gmail_message(
            sender="noreply@unstop.com",
            subject="Opportunities",
            body_html=UNSTOP_HTML,
        )
        source = GmailAlertSource()
        opps = source._parse_message(msg)
        assert not any("settings" in (o.url or "").lower() for o in opps)


class TestUnknownSenderSkipped:
    """Unknown senders should produce zero opportunities."""

    def test_random_email_produces_nothing(self):
        msg = _make_gmail_message(
            sender="promotions@randomshop.com",
            subject="50% off everything!",
            body_html="<html><body><a href='https://shop.com/sale'>Shop now</a></body></html>",
        )
        source = GmailAlertSource()
        opps = source._parse_message(msg)
        assert opps == []


class TestSubjectLineFallback:
    """When no job links are found in the body, try the subject line."""

    def test_extracts_from_subject(self):
        msg = _make_gmail_message(
            sender="noreply@internshala.com",
            subject="New Internship: Marketing Analyst at BrandCo",
            body_html="<html><body><p>Check out this internship!</p></body></html>",
        )
        source = GmailAlertSource()
        opps = source._parse_message(msg)
        assert len(opps) == 1
        assert "Marketing Analyst" in opps[0].title


class TestIsJobLink:
    """Test the URL pattern matching helper."""

    def test_internshala_internship_link(self):
        assert _is_job_link("https://internshala.com/internship/detail/swe", "internshala")

    def test_internshala_job_link(self):
        assert _is_job_link("https://internshala.com/job/detail/fullstack", "internshala")

    def test_internshala_non_job_link(self):
        assert not _is_job_link("https://internshala.com/blog/tips", "internshala")

    def test_unstop_competition_link(self):
        assert _is_job_link("https://unstop.com/competition/hack-2026", "unstop")

    def test_unstop_opportunity_link(self):
        assert _is_job_link("https://unstop.com/opportunity/intern-2026", "unstop")
