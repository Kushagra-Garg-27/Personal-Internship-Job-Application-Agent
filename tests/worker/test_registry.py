"""Tests for adapter registry and discovery_only rejection (Phase 9)."""

from __future__ import annotations

import pytest

from core.status import ReliabilityTier
from worker.adapters.greenhouse import GreenhouseAdapter
from worker.adapters.internshala import InternshalaAdapter
from worker.adapters.lever import LeverAdapter
from worker.adapters.unstop import UnstopAdapter
from worker.adapters.registry import (
    DiscoveryOnlyRejectionError,
    UnsupportedPlatformError,
    resolve_adapter,
)


def test_resolve_greenhouse():
    adapter = resolve_adapter({"source": "greenhouse", "url": "https://boards.greenhouse.io/acme/jobs/123"})
    assert isinstance(adapter, GreenhouseAdapter)


def test_resolve_lever():
    adapter = resolve_adapter({"source": "lever", "url": "https://jobs.lever.co/spotify/abc"})
    assert isinstance(adapter, LeverAdapter)


def test_resolve_internshala():
    adapter = resolve_adapter({"source": "internshala", "url": "https://internshala.com/internship/123"})
    assert isinstance(adapter, InternshalaAdapter)


def test_resolve_unstop():
    adapter = resolve_adapter({"source": "unstop", "url": "https://unstop.com/opportunities/456"})
    assert isinstance(adapter, UnstopAdapter)


def test_discovery_only_rejected_strictly():
    """If an opportunity has reliability_tier='discovery_only', the worker MUST refuse it."""
    with pytest.raises(DiscoveryOnlyRejectionError, match="discovery_only"):
        resolve_adapter({
            "source": "gmail_job_alert",
            "url": "https://example.com/job/123",
            "reliability_tier": ReliabilityTier.DISCOVERY_ONLY,
        })


def test_unsupported_platform_raises():
    with pytest.raises(UnsupportedPlatformError, match="No automated adapter implemented"):
        resolve_adapter({
            "source": "unknown_board",
            "url": "https://random-jobs.example.org/listing",
            "reliability_tier": ReliabilityTier.EXPERIMENTAL,
        })
