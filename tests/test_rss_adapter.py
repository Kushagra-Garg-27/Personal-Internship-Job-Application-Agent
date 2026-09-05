"""Tests for the RSS/Atom feed adapter."""

from __future__ import annotations

from pathlib import Path

from core.discovery.sources.rss_feed import RSSFeedSource

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestRSSFieldMapping:
    """Verify correct field mapping from RSS entries to RawOpportunity."""

    def test_parses_all_entries(self):
        source = RSSFeedSource(feeds=[{
            "url": str(FIXTURES_DIR / "rss_sample.xml"),
            "company": "AcmeCorp",
        }])
        results = source.discover()
        assert len(results) == 2

    def test_maps_title(self):
        source = RSSFeedSource(feeds=[{
            "url": str(FIXTURES_DIR / "rss_sample.xml"),
            "company": "AcmeCorp",
        }])
        results = source.discover()
        assert results[0].title == "Systems Engineer"
        assert results[1].title == "QA Intern"

    def test_maps_company_from_config(self):
        source = RSSFeedSource(feeds=[{
            "url": str(FIXTURES_DIR / "rss_sample.xml"),
            "company": "AcmeCorp",
        }])
        results = source.discover()
        assert all(r.company == "AcmeCorp" for r in results)

    def test_maps_url(self):
        source = RSSFeedSource(feeds=[{
            "url": str(FIXTURES_DIR / "rss_sample.xml"),
            "company": "AcmeCorp",
        }])
        results = source.discover()
        assert results[0].url == "https://acmecorp.com/careers/systems-engineer"

    def test_maps_description(self):
        source = RSSFeedSource(feeds=[{
            "url": str(FIXTURES_DIR / "rss_sample.xml"),
            "company": "AcmeCorp",
        }])
        results = source.discover()
        assert "cloud infrastructure" in results[0].description

    def test_maps_posted_at(self):
        source = RSSFeedSource(feeds=[{
            "url": str(FIXTURES_DIR / "rss_sample.xml"),
            "company": "AcmeCorp",
        }])
        results = source.discover()
        assert results[0].posted_at is not None

    def test_source_is_rss(self):
        source = RSSFeedSource(feeds=[{
            "url": str(FIXTURES_DIR / "rss_sample.xml"),
            "company": "AcmeCorp",
        }])
        results = source.discover()
        assert all(r.source == "rss" for r in results)

    def test_metadata_has_author(self):
        source = RSSFeedSource(feeds=[{
            "url": str(FIXTURES_DIR / "rss_sample.xml"),
            "company": "AcmeCorp",
        }])
        results = source.discover()
        assert results[1].metadata["author"] == "hr@acmecorp.com"


class TestRSSEdgeCases:
    """Edge cases and error handling."""

    def test_no_feeds_configured(self):
        source = RSSFeedSource(feeds=[])
        assert source.discover() == []

    def test_invalid_feed_url_handled_gracefully(self):
        source = RSSFeedSource(feeds=[{
            "url": "https://nonexistent.invalid/feed.xml",
            "company": "BadCo",
        }])
        # feedparser handles bad URLs gracefully — returns empty
        results = source.discover()
        assert isinstance(results, list)
