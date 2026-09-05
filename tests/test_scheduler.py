"""Tests for the discovery scheduler."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from core.discovery.scheduler import (
    _run_source_job,
    _last_results,
    get_scheduler_status,
    start_scheduler,
    stop_scheduler,
)


class TestSchedulerJobWrapper:
    """Test the _run_source_job error-handling wrapper."""

    def test_successful_run_records_ok(self):
        from core.discovery.base import DiscoverySource, RawOpportunity
        from core.status import ReliabilityTier

        class DummySource(DiscoverySource):
            name = "dummy"
            tier = ReliabilityTier.STABLE
            def discover(self):
                return []

        _last_results.clear()

        with patch("core.discovery.scheduler.run_discovery") as mock_run:
            mock_run.return_value = MagicMock(
                total_fetched=0, new=0, updated=0, errors=0
            )
            _run_source_job(DummySource, "dummy")

        assert "dummy" in _last_results
        assert _last_results["dummy"]["status"] == "ok"

    def test_source_exception_records_error(self):
        def bad_factory():
            raise RuntimeError("boom")

        _last_results.clear()
        _run_source_job(bad_factory, "bad_source")

        assert "bad_source" in _last_results
        assert _last_results["bad_source"]["status"] == "error"


class TestSchedulerLifecycle:
    """Test start/stop and status."""

    def test_status_when_not_started(self):
        stop_scheduler()  # ensure clean state
        status = get_scheduler_status()
        assert status["running"] is False
        assert status["registered_jobs"] == []

    def test_start_with_no_sources_configured(self):
        """Scheduler starts even with no sources — just no jobs registered."""
        with patch("core.discovery.scheduler.settings") as mock_settings:
            mock_settings.GREENHOUSE_BOARDS = []
            mock_settings.LEVER_COMPANIES = []
            mock_settings.RSS_FEEDS = []
            mock_settings.GMAIL_CREDENTIALS_FILE = None
            mock_settings.SCHEDULER_ENABLED = True

            scheduler = start_scheduler()
            try:
                assert scheduler.running
                status = get_scheduler_status()
                assert status["running"] is True
                assert status["registered_jobs"] == []
            finally:
                stop_scheduler()

    def test_start_registers_greenhouse_job(self):
        with patch("core.discovery.scheduler.settings") as mock_settings:
            mock_settings.GREENHOUSE_BOARDS = [{"token": "test", "company": "Test"}]
            mock_settings.LEVER_COMPANIES = []
            mock_settings.RSS_FEEDS = []
            mock_settings.GMAIL_CREDENTIALS_FILE = None
            mock_settings.GREENHOUSE_POLL_INTERVAL = 3600

            scheduler = start_scheduler()
            try:
                status = get_scheduler_status()
                assert "discovery_greenhouse" in status["registered_jobs"]
            finally:
                stop_scheduler()

    def test_start_registers_multiple_sources(self):
        with patch("core.discovery.scheduler.settings") as mock_settings:
            mock_settings.GREENHOUSE_BOARDS = [{"token": "t", "company": "T"}]
            mock_settings.LEVER_COMPANIES = [{"slug": "l", "company": "L"}]
            mock_settings.RSS_FEEDS = [{"url": "http://test.com/rss", "company": "R"}]
            mock_settings.GMAIL_CREDENTIALS_FILE = None
            mock_settings.GREENHOUSE_POLL_INTERVAL = 3600
            mock_settings.LEVER_POLL_INTERVAL = 3600
            mock_settings.RSS_POLL_INTERVAL = 3600

            scheduler = start_scheduler()
            try:
                jobs = get_scheduler_status()["registered_jobs"]
                assert "discovery_greenhouse" in jobs
                assert "discovery_lever" in jobs
                assert "discovery_rss" in jobs
            finally:
                stop_scheduler()

    def test_stop_scheduler(self):
        with patch("core.discovery.scheduler.settings") as mock_settings:
            mock_settings.GREENHOUSE_BOARDS = []
            mock_settings.LEVER_COMPANIES = []
            mock_settings.RSS_FEEDS = []
            mock_settings.GMAIL_CREDENTIALS_FILE = None

            start_scheduler()
            stop_scheduler()
            status = get_scheduler_status()
            assert status["running"] is False
