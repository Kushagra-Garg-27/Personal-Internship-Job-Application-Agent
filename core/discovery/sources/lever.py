"""Lever public postings API adapter — stable tier.

API: ``GET https://api.lever.co/v0/postings/{slug}?mode=json``
No authentication required.  Returns a JSON array of posting objects.

Configuration via ``settings.LEVER_COMPANIES``:
    [{"slug": "netflix", "company": "Netflix"}, ...]
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from core.config import settings
from core.discovery.base import DiscoverySource, RawOpportunity
from core.status import ReliabilityTier

logger = logging.getLogger(__name__)

LEVER_API_BASE = "https://api.lever.co/v0/postings"


class LeverSource(DiscoverySource):
    """Discover jobs from Lever public postings API."""

    name = "lever"
    tier = ReliabilityTier.STABLE

    def __init__(
        self,
        companies: list[dict] | None = None,
        *,
        http_client: httpx.Client | None = None,
    ):
        self._companies = companies if companies is not None else settings.LEVER_COMPANIES
        self._client = http_client or httpx.Client(timeout=30)

    def discover(self) -> list[RawOpportunity]:
        """Fetch postings from all configured Lever companies."""
        results: list[RawOpportunity] = []
        for entry in self._companies:
            slug = entry["slug"]
            company = entry.get("company", slug)
            try:
                postings = self._fetch_company(slug, company)
                results.extend(postings)
                logger.info("Lever [%s]: fetched %d postings", slug, len(postings))
            except Exception:
                logger.exception("Lever [%s]: fetch failed", slug)
        return results

    def _fetch_company(self, slug: str, company: str) -> list[RawOpportunity]:
        url = f"{LEVER_API_BASE}/{slug}"
        resp = self._client.get(url, params={"mode": "json"})
        resp.raise_for_status()
        data = resp.json()

        # Lever returns a JSON array directly (no wrapper object)
        if not isinstance(data, list):
            logger.warning("Lever [%s]: unexpected response type %s", slug, type(data))
            return []

        results = []
        for posting in data:
            try:
                results.append(self._parse_posting(posting, company))
            except Exception:
                logger.warning(
                    "Lever [%s]: skipping malformed posting id=%s",
                    slug, posting.get("id", "?"),
                )
        return results

    @staticmethod
    def _parse_posting(posting: dict, company: str) -> RawOpportunity:
        """Map a Lever posting JSON object to RawOpportunity."""
        categories = posting.get("categories") or {}
        location = categories.get("location")
        commitment = categories.get("commitment")  # e.g. "Full-time", "Intern"

        # Lever provides createdAt as epoch ms
        posted_at = None
        if posting.get("createdAt"):
            try:
                posted_at = datetime.fromtimestamp(
                    posting["createdAt"] / 1000, tz=timezone.utc
                )
            except (ValueError, TypeError, OSError):
                pass

        # Salary info if available
        salary_range = posting.get("salaryRange") or {}
        salary_min = None
        salary_max = None
        if salary_range:
            salary_min = salary_range.get("min")
            salary_max = salary_range.get("max")

        return RawOpportunity(
            title=posting["text"],
            company=company,
            url=posting.get("hostedUrl"),
            source="lever",
            description=posting.get("descriptionPlain") or posting.get("description"),
            location=location,
            salary_min=salary_min,
            salary_max=salary_max,
            posted_at=posted_at,
            metadata={
                "lever_id": posting.get("id"),
                "commitment": commitment,
                "team": categories.get("team"),
                "department": categories.get("department"),
                "workplace_type": posting.get("workplaceType"),
            },
        )
