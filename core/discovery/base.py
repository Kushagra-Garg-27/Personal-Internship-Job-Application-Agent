"""Discovery source interface and the RawOpportunity data type.

Every discovery adapter implements ``DiscoverySource`` and returns a list
of ``RawOpportunity`` objects.  The pipeline in ``pipeline.py`` then
normalises and upserts them into the DB via Phase 2's service layer.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.status import ReliabilityTier

logger = logging.getLogger(__name__)


@dataclass
class RawOpportunity:
    """Normalised output from any discovery source.

    This is the common shape that every adapter maps its raw API response
    into, before the pipeline upserts it into the DB.
    """

    title: str
    company: str
    url: str | None = None
    source: str = ""  # adapter name, e.g. "greenhouse", "lever", "rss", "gmail_alert"
    description: str | None = None
    location: str | None = None
    salary_min: int | None = None
    salary_max: int | None = None
    posted_at: datetime | None = None
    deadline_at: datetime | None = None
    metadata: dict[str, Any] | None = field(default=None)


class DiscoverySource(ABC):
    """Interface that all discovery adapters implement.

    Attributes
    ----------
    name : str
        Human-readable source identifier (e.g. ``"greenhouse"``).
    tier : ReliabilityTier
        ``stable`` or ``discovery_only`` — set as a class attribute.
    """

    name: str
    tier: ReliabilityTier

    @abstractmethod
    def discover(self) -> list[RawOpportunity]:
        """Fetch and return normalised opportunities from this source.

        Implementations should handle their own errors gracefully —
        returning a partial list rather than raising on a single
        malformed entry.
        """
        ...
