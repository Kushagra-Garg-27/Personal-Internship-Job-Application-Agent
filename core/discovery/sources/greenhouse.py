"""Greenhouse public job-board API adapter — stable tier.

API: ``GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true``
No authentication required.  Returns JSON with ``jobs[]`` array.

Configuration via ``settings.GREENHOUSE_BOARDS``:
    [{"token": "vaulttec", "company": "Vault-Tec"}, ...]
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import httpx

from core.config import settings
from core.discovery.base import DiscoverySource, RawOpportunity
from core.status import ReliabilityTier

logger = logging.getLogger(__name__)

GREENHOUSE_API_BASE = "https://boards-api.greenhouse.io/v1/boards"


class GreenhouseSource(DiscoverySource):
    """Discover jobs from Greenhouse public board APIs."""

    name = "greenhouse"
    tier = ReliabilityTier.STABLE

    def __init__(
        self,
        boards: list[dict] | None = None,
        *,
        http_client: httpx.Client | None = None,
    ):
        self._boards = boards if boards is not None else settings.GREENHOUSE_BOARDS
        self._client = http_client or httpx.Client(timeout=30)

    def discover(self) -> list[RawOpportunity]:
        """Fetch jobs from all configured Greenhouse boards."""
        results: list[RawOpportunity] = []
        for board in self._boards:
            token = board["token"]
            company = board.get("company", token)
            try:
                jobs = self._fetch_board(token, company)
                results.extend(jobs)
                logger.info("Greenhouse [%s]: fetched %d jobs", token, len(jobs))
            except Exception:
                logger.exception("Greenhouse [%s]: fetch failed", token)
        return results

    def _fetch_board(self, token: str, company: str) -> list[RawOpportunity]:
        url = f"{GREENHOUSE_API_BASE}/{token}/jobs"
        resp = self._client.get(url, params={"content": "true"})
        resp.raise_for_status()
        data = resp.json()

        results = []
        for job in data.get("jobs", []):
            try:
                results.append(self._parse_job(job, company))
            except Exception:
                logger.warning(
                    "Greenhouse [%s]: skipping malformed job id=%s",
                    token, job.get("id", "?"),
                )
        return results

    @staticmethod
    def _parse_job(job: dict, company: str) -> RawOpportunity:
        """Map a Greenhouse job JSON object to RawOpportunity."""
        location_obj = job.get("location") or {}
        location = location_obj.get("name")

        # Parse updated_at as posted_at (Greenhouse uses updated_at)
        posted_at = None
        if job.get("updated_at"):
            try:
                posted_at = datetime.fromisoformat(job["updated_at"])
            except (ValueError, TypeError):
                pass

        return RawOpportunity(
            title=job["title"],
            company=company,
            url=job.get("absolute_url"),
            source="greenhouse",
            description=job.get("content"),  # HTML when content=true
            location=location,
            posted_at=posted_at,
            metadata={
                "greenhouse_id": job.get("id"),
                "internal_job_id": job.get("internal_job_id"),
                "requisition_id": job.get("requisition_id"),
                "departments": [
                    d.get("name") for d in job.get("departments", [])
                ],
            },
        )
