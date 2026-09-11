"""Unstop discovery source adapter — experimental tier (Phase 3 & Unstop V1).

API: ``GET https://unstop.com/api/public/opportunity/search-result?opportunity={opportunity_type}&page=1&per_page={limit}&oppstatus=open``
Public search endpoint for open job and internship vacancies on Unstop.
Produces canonical RawOpportunity objects for ingestion into the discovery pipeline.
Maintains strict Core ↔ Worker decoupling (no worker imports).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from core.discovery.base import DiscoverySource, RawOpportunity
from core.status import ReliabilityTier

logger = logging.getLogger(__name__)

UNSTOP_SEARCH_API = "https://unstop.com/api/public/opportunity/search-result"


class UnstopDiscoverySource(DiscoverySource):
    """Discover jobs and internships from Unstop public search API."""

    name = "unstop"
    tier = ReliabilityTier.EXPERIMENTAL

    def __init__(
        self,
        limit: int = 18,
        opportunity_type: str = "jobs",
        *,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.limit = limit
        self.opportunity_type = opportunity_type
        self._client = http_client or httpx.Client(
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "application/json",
            },
        )

    def discover(self) -> list[RawOpportunity]:
        """Fetch and normalize opportunities from Unstop search endpoint."""
        try:
            resp = self._client.get(
                UNSTOP_SEARCH_API,
                params={
                    "opportunity": self.opportunity_type,
                    "page": 1,
                    "per_page": self.limit,
                    "oppstatus": "open",
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            logger.exception("Unstop discovery failed during HTTP request")
            return []

        items = data.get("data", {}).get("data", [])
        raw_opps: list[RawOpportunity] = []

        for item in items:
            try:
                opp = self._map_item(item)
                if opp:
                    raw_opps.append(opp)
            except Exception as exc:
                logger.debug("Failed mapping Unstop item %s: %s", item.get("id"), exc)

        logger.info("Unstop discovery: fetched %d opportunities", len(raw_opps))
        return raw_opps

    def _map_item(self, item: dict[str, Any]) -> RawOpportunity | None:
        """Map a single Unstop API JSON item to a RawOpportunity."""
        title = (item.get("title") or "").strip()
        if not title:
            return None

        org = item.get("organisation") or {}
        company = org.get("name") if isinstance(org, dict) else str(org or "")
        company = company.strip() or "Unknown Company"

        url = item.get("seo_url") or ""
        if url and not url.startswith("http"):
            url = f"https://unstop.com{url}" if url.startswith("/") else f"https://unstop.com/{url}"

        regn = item.get("regnRequirements") or {}
        posted_at = self._parse_iso(regn.get("start_regn_dt") or item.get("start_date"))
        deadline_at = self._parse_iso(regn.get("end_regn_dt") or item.get("end_date"))

        locs = item.get("locations") or []
        loc_parts = []
        for loc in locs:
            if isinstance(loc, dict):
                city = loc.get("city") or ""
                state = loc.get("state") or ""
                country = loc.get("country") or ""
                part = ", ".join(p for p in [city, state, country] if p)
                if part:
                    loc_parts.append(part)
            elif isinstance(loc, str) and loc.strip():
                loc_parts.append(loc.strip())
        location = "; ".join(loc_parts) if loc_parts else None

        description = item.get("details")
        if not description and isinstance(item.get("jobDetail"), dict):
            description = item["jobDetail"].get("description")

        skills = [
            s.get("name")
            for s in item.get("required_skills", [])
            if isinstance(s, dict) and "name" in s
        ]

        metadata = {
            "platform": "unstop",
            "external_id": str(item.get("id", "")),
            "skills": skills,
            "filters": item.get("filters", []),
            "opportunity_type": self.opportunity_type,
        }

        return RawOpportunity(
            title=title,
            company=company,
            url=url or None,
            source=self.name,
            description=description,
            location=location,
            salary_min=None,
            salary_max=None,
            posted_at=posted_at,
            deadline_at=deadline_at,
            metadata=metadata,
        )

    @staticmethod
    def _parse_iso(dt_str: str | None) -> datetime | None:
        """Parse ISO-8601 timestamp safely."""
        if not dt_str or not isinstance(dt_str, str):
            return None
        try:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        except Exception:
            return None
