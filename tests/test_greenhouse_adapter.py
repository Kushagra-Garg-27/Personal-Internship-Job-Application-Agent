"""Tests for the Greenhouse discovery adapter.

Uses saved fixture data — no live API calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from core.discovery.sources.greenhouse import GreenhouseSource

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _mock_client(fixture_file: str, status_code: int = 200) -> httpx.Client:
    """Create a mock httpx.Client that returns fixture data."""
    data = json.loads((FIXTURES_DIR / fixture_file).read_text())

    mock = MagicMock(spec=httpx.Client)
    response = MagicMock(spec=httpx.Response)
    response.status_code = status_code
    response.json.return_value = data
    response.raise_for_status = MagicMock()
    if status_code >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=response
        )
    mock.get.return_value = response
    return mock


class TestGreenhouseFieldMapping:
    """Verify correct field mapping from API response to RawOpportunity."""

    def test_parses_all_jobs(self):
        source = GreenhouseSource(
            boards=[{"token": "testcorp", "company": "TestCorp"}],
            http_client=_mock_client("greenhouse_response.json"),
        )
        results = source.discover()
        assert len(results) == 3

    def test_maps_title(self):
        source = GreenhouseSource(
            boards=[{"token": "testcorp", "company": "TestCorp"}],
            http_client=_mock_client("greenhouse_response.json"),
        )
        results = source.discover()
        assert results[0].title == "Software Engineer Intern"
        assert results[1].title == "Data Science Intern"
        assert results[2].title == "Backend Developer"

    def test_maps_company_from_config(self):
        source = GreenhouseSource(
            boards=[{"token": "testcorp", "company": "TestCorp"}],
            http_client=_mock_client("greenhouse_response.json"),
        )
        results = source.discover()
        assert all(r.company == "TestCorp" for r in results)

    def test_maps_location(self):
        source = GreenhouseSource(
            boards=[{"token": "testcorp", "company": "TestCorp"}],
            http_client=_mock_client("greenhouse_response.json"),
        )
        results = source.discover()
        assert results[0].location == "San Francisco, CA"
        assert results[2].location == "Remote"

    def test_maps_url(self):
        source = GreenhouseSource(
            boards=[{"token": "testcorp", "company": "TestCorp"}],
            http_client=_mock_client("greenhouse_response.json"),
        )
        results = source.discover()
        assert results[0].url == "https://boards.greenhouse.io/testcorp/jobs/4012345"

    def test_maps_posted_at(self):
        source = GreenhouseSource(
            boards=[{"token": "testcorp", "company": "TestCorp"}],
            http_client=_mock_client("greenhouse_response.json"),
        )
        results = source.discover()
        assert results[0].posted_at is not None

    def test_maps_description(self):
        source = GreenhouseSource(
            boards=[{"token": "testcorp", "company": "TestCorp"}],
            http_client=_mock_client("greenhouse_response.json"),
        )
        results = source.discover()
        assert "talented intern" in results[0].description

    def test_maps_metadata(self):
        source = GreenhouseSource(
            boards=[{"token": "testcorp", "company": "TestCorp"}],
            http_client=_mock_client("greenhouse_response.json"),
        )
        results = source.discover()
        assert results[0].metadata["greenhouse_id"] == 4012345
        assert "Engineering" in results[0].metadata["departments"]

    def test_source_is_greenhouse(self):
        source = GreenhouseSource(
            boards=[{"token": "testcorp", "company": "TestCorp"}],
            http_client=_mock_client("greenhouse_response.json"),
        )
        results = source.discover()
        assert all(r.source == "greenhouse" for r in results)


class TestGreenhouseEdgeCases:
    """Error handling and edge cases."""

    def test_empty_board_returns_empty_list(self):
        mock = MagicMock(spec=httpx.Client)
        response = MagicMock(spec=httpx.Response)
        response.json.return_value = {"jobs": [], "meta": {"total": 0}}
        response.raise_for_status = MagicMock()
        mock.get.return_value = response

        source = GreenhouseSource(
            boards=[{"token": "empty", "company": "EmptyCo"}],
            http_client=mock,
        )
        results = source.discover()
        assert results == []

    def test_no_boards_configured_returns_empty(self):
        source = GreenhouseSource(boards=[])
        results = source.discover()
        assert results == []

    def test_http_error_handled_gracefully(self):
        source = GreenhouseSource(
            boards=[{"token": "bad", "company": "BadCo"}],
            http_client=_mock_client("greenhouse_response.json", status_code=500),
        )
        # Should not raise — returns empty
        results = source.discover()
        assert results == []

    def test_multiple_boards(self):
        source = GreenhouseSource(
            boards=[
                {"token": "a", "company": "CompanyA"},
                {"token": "b", "company": "CompanyB"},
            ],
            http_client=_mock_client("greenhouse_response.json"),
        )
        results = source.discover()
        # Both boards return 3 jobs each = 6 total
        assert len(results) == 6


class TestGreenhouseContract:
    """Contract tests: verify the fixture matches the expected API shape."""

    def test_response_has_jobs_array(self):
        data = json.loads((FIXTURES_DIR / "greenhouse_response.json").read_text())
        assert "jobs" in data
        assert isinstance(data["jobs"], list)

    def test_each_job_has_required_fields(self):
        data = json.loads((FIXTURES_DIR / "greenhouse_response.json").read_text())
        for job in data["jobs"]:
            assert "title" in job
            assert "absolute_url" in job
            assert "location" in job
            assert isinstance(job["location"], dict)
            assert "name" in job["location"]

    def test_response_has_meta(self):
        data = json.loads((FIXTURES_DIR / "greenhouse_response.json").read_text())
        assert "meta" in data
        assert "total" in data["meta"]
