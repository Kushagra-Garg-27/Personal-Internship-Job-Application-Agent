"""Adapter registry and resolution logic (Phase 9).

Dispatches opportunities to appropriate platform adapters based on URL or source name.
Enforces the strict discovery_only boundary: any opportunity tagged `discovery_only`
is refused and handed back for manual application.
"""

from __future__ import annotations

import logging
from typing import Any

from core.status import ReliabilityTier
from worker.adapters.base import BasePlatformAdapter
from worker.adapters.greenhouse import GreenhouseAdapter
from worker.adapters.internshala import InternshalaAdapter
from worker.adapters.lever import LeverAdapter
from worker.adapters.unstop import UnstopAdapter

logger = logging.getLogger(__name__)


class DiscoveryOnlyRejectionError(Exception):
    """Raised when an opportunity tagged discovery_only reaches the Worker queue."""


class UnsupportedPlatformError(Exception):
    """Raised when an opportunity has no matching adapter implementation."""


def resolve_adapter(opportunity_or_dict: Any) -> BasePlatformAdapter:
    """Resolve the appropriate platform adapter for an opportunity.
    
    Raises
    ------
    DiscoveryOnlyRejectionError
        If the opportunity is tagged discovery_only.
    UnsupportedPlatformError
        If no adapter is implemented for the listing.
    """
    tier = getattr(opportunity_or_dict, "reliability_tier", None)
    if tier is None and isinstance(opportunity_or_dict, dict):
        tier = opportunity_or_dict.get("reliability_tier")

    if tier in {ReliabilityTier.DISCOVERY_ONLY, "discovery_only"}:
        raise DiscoveryOnlyRejectionError(
            "Opportunity is tagged discovery_only. Automated application is strictly "
            "prohibited per architectural policy; manual application required."
        )

    source = (
        getattr(opportunity_or_dict, "source", "") or ""
        if not isinstance(opportunity_or_dict, dict)
        else (opportunity_or_dict.get("source") or "")
    ).lower()

    url = (
        getattr(opportunity_or_dict, "url", "") or ""
        if not isinstance(opportunity_or_dict, dict)
        else (opportunity_or_dict.get("url") or "")
    ).lower()

    if "greenhouse" in source or "greenhouse.io" in url:
        return GreenhouseAdapter()
    elif "lever" in source or "lever.co" in url:
        return LeverAdapter()
    elif "internshala" in source or "internshala.com" in url:
        return InternshalaAdapter()
    elif "unstop" in source or "unstop.com" in url:
        return UnstopAdapter()

    raise UnsupportedPlatformError(
        f"No automated adapter implemented for source={source!r}, url={url!r}. "
        "Manual application required."
    )
