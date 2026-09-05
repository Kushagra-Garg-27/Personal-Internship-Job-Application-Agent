"""Tests for the Lever discovery adapter.

Uses saved fixture data — no live API calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from core.discovery.sources.lever import LeverSource

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


class TestLeverFieldMapping:
    """Verify correct field mapping from API response to RawOpportunity."""

    def test_parses_all_postings(self):
        source = LeverSource(
            companies=[{"slug": "democorp", "company": "DemoCorp"}],
            http_client=_mock_client("lever_response.json"),
        )
        results = source.discover()
        assert len(results) == 3

    def test_maps_title_from_text_field(self):
        source = LeverSource(
            companies=[{"slug": "democorp", "company": "DemoCorp"}],
            http_client=_mock_client("lever_response.json"),
        )
        results = source.discover()
        assert results[0].title == "Frontend Engineer"
        assert results[1].title == "Product Design Intern"
        assert results[2].title == "ML Engineer"

    def test_maps_company_from_config(self):
        source = LeverSource(
            companies=[{"slug": "democorp", "company": "DemoCorp"}],
            http_client=_mock_client("lever_response.json"),
        )
        results = source.discover()
        assert all(r.company == "DemoCorp" for r in results)

    def test_maps_location_from_categories(self):
        source = LeverSource(
            companies=[{"slug": "democorp", "company": "DemoCorp"}],
            http_client=_mock_client("lever_response.json"),
        )
        results = source.discover()
        assert results[0].location == "Seattle, WA"
        assert results[1].location == "Remote"

    def test_maps_url(self):
        source = LeverSource(
            companies=[{"slug": "democorp", "company": "DemoCorp"}],
            http_client=_mock_client("lever_response.json"),
        )
        results = source.discover()
        assert results[0].url == "https://jobs.lever.co/democorp/abc-def-123"

    def test_maps_salary(self):
        source = LeverSource(
            companies=[{"slug": "democorp", "company": "DemoCorp"}],
            http_client=_mock_client("lever_response.json"),
        )
        results = source.discover()
        assert results[0].salary_min == 80000
        assert results[0].salary_max == 120000
        # Second posting has no salary
        assert results[1].salary_min is None
        assert results[1].salary_max is None

    def test_maps_posted_at_from_epoch(self):
        source = LeverSource(
            companies=[{"slug": "democorp", "company": "DemoCorp"}],
            http_client=_mock_client("lever_response.json"),
        )
        results = source.discover()
        assert results[0].posted_at is not None

    def test_maps_description(self):
        source = LeverSource(
            companies=[{"slug": "democorp", "company": "DemoCorp"}],
            http_client=_mock_client("lever_response.json"),
        )
        results = source.discover()
        assert "frontend engineer" in results[0].description.lower()

    def test_maps_metadata_with_categories(self):
        source = LeverSource(
            companies=[{"slug": "democorp", "company": "DemoCorp"}],
            http_client=_mock_client("lever_response.json"),
        )
        results = source.discover()
        assert results[0].metadata["lever_id"] == "abc-def-123"
        assert results[0].metadata["commitment"] == "Full-time"
        assert results[0].metadata["workplace_type"] == "onSite"

    def test_source_is_lever(self):
        source = LeverSource(
            companies=[{"slug": "democorp", "company": "DemoCorp"}],
            http_client=_mock_client("lever_response.json"),
        )
        results = source.discover()
        assert all(r.source == "lever" for r in results)


class TestLeverEdgeCases:
    """Error handling and edge cases."""

    def test_empty_company_returns_empty(self):
        mock = MagicMock(spec=httpx.Client)
        response = MagicMock(spec=httpx.Response)
        response.json.return_value = []
        response.raise_for_status = MagicMock()
        mock.get.return_value = response

        source = LeverSource(
            companies=[{"slug": "empty", "company": "EmptyCo"}],
            http_client=mock,
        )
        assert source.discover() == []

    def test_no_companies_configured(self):
        source = LeverSource(companies=[])
        assert source.discover() == []

    def test_missing_optional_fields_handled(self):
        """Postings without salary/workplace should still parse."""
        source = LeverSource(
            companies=[{"slug": "democorp", "company": "DemoCorp"}],
            http_client=_mock_client("lever_response.json"),
        )
        results = source.discover()
        # Third posting has no salaryRange or workplaceType
        assert results[2].salary_min is None
        assert results[2].title == "ML Engineer"


class TestLeverContract:
    """Contract tests: verify fixture matches expected API shape."""

    def test_response_is_list(self):
        data = json.loads((FIXTURES_DIR / "lever_response.json").read_text())
        assert isinstance(data, list)

    def test_each_posting_has_required_fields(self):
        data = json.loads((FIXTURES_DIR / "lever_response.json").read_text())
        for posting in data:
            assert "text" in posting, "Lever postings must have 'text' (title)"
            assert "hostedUrl" in posting or "applyUrl" in posting
            assert "categories" in posting
