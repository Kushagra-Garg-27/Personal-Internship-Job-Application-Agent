"""Generic RSS/Atom feed adapter — stable tier.

Uses ``feedparser`` to read any RSS/Atom feed (many company career
pages expose one).  Config-driven list of feed URLs.

Configuration via ``settings.RSS_FEEDS``:
    [{"url": "https://company.com/careers/rss", "company": "Acme Corp"}, ...]
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import feedparser

from core.config import settings
from core.discovery.base import DiscoverySource, RawOpportunity
from core.status import ReliabilityTier

logger = logging.getLogger(__name__)


class RSSFeedSource(DiscoverySource):
    """Discover jobs from RSS/Atom feeds."""

    name = "rss"
    tier = ReliabilityTier.STABLE

    def __init__(self, feeds: list[dict] | None = None):
        self._feeds = feeds if feeds is not None else settings.RSS_FEEDS

    def discover(self) -> list[RawOpportunity]:
        """Parse all configured RSS feeds and return normalised opportunities."""
        results: list[RawOpportunity] = []
        for feed_cfg in self._feeds:
            url = feed_cfg["url"]
            company = feed_cfg.get("company", "Unknown")
            try:
                entries = self._parse_feed(url, company)
                results.extend(entries)
                logger.info("RSS [%s]: parsed %d entries", url, len(entries))
            except Exception:
                logger.exception("RSS [%s]: parse failed", url)
        return results

    @staticmethod
    def _parse_feed(url: str, company: str) -> list[RawOpportunity]:
        feed = feedparser.parse(url)
        if feed.bozo and not feed.entries:
            logger.warning("RSS [%s]: malformed feed (bozo), no entries", url)
            return []

        results = []
        for entry in feed.entries:
            try:
                results.append(RSSFeedSource._parse_entry(entry, company))
            except Exception:
                logger.warning(
                    "RSS [%s]: skipping malformed entry %s",
                    url, entry.get("title", "?"),
                )
        return results

    @staticmethod
    def _parse_entry(entry: dict, company: str) -> RawOpportunity:
        """Map a feedparser entry to RawOpportunity."""
        posted_at = None
        if entry.get("published"):
            try:
                posted_at = parsedate_to_datetime(entry["published"])
            except (ValueError, TypeError):
                pass
        elif entry.get("updated"):
            try:
                posted_at = parsedate_to_datetime(entry["updated"])
            except (ValueError, TypeError):
                pass

        return RawOpportunity(
            title=entry.get("title", "Untitled"),
            company=company,
            url=entry.get("link"),
            source="rss",
            description=entry.get("summary"),
            posted_at=posted_at,
            metadata={
                "feed_id": entry.get("id"),
                "author": entry.get("author"),
            },
        )
