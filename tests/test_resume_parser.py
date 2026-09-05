"""Tests for the resume parser — deterministic text extraction with fail-closed logic."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.parsing.resume_parser import extract_text


FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestResumeParser:
    """Resume text extraction from PDF and DOCX files."""

    def test_parse_valid_pdf(self, sample_pdf_path):
        """A valid PDF should extract meaningful text."""
        if not sample_pdf_path.exists():
            pytest.skip("Fixture not generated yet — run tests/generate_fixtures.py")

        text, status = extract_text(sample_pdf_path)
        assert status == "success"
        assert text is not None
        assert len(text) >= 50
        # Check for known content
        assert "John Doe" in text or "Software Engineer" in text

    def test_parse_valid_docx(self, sample_docx_path):
        """A valid DOCX should extract meaningful text."""
        if not sample_docx_path.exists():
            pytest.skip("Fixture not generated yet — run tests/generate_fixtures.py")

        text, status = extract_text(sample_docx_path)
        assert status == "success"
        assert text is not None
        assert len(text) >= 50
        assert "John Doe" in text

    def test_parse_empty_pdf_fails_closed(self, empty_pdf_path):
        """An empty/scanned PDF should be flagged as parse_failed."""
        if not empty_pdf_path.exists():
            pytest.skip("Fixture not generated yet — run tests/generate_fixtures.py")

        text, status = extract_text(empty_pdf_path)
        assert status == "parse_failed"
        assert text is None

    def test_parse_nonexistent_file(self):
        """A non-existent file should fail gracefully."""
        text, status = extract_text("/nonexistent/resume.pdf")
        assert status == "parse_failed"
        assert text is None

    def test_parse_unsupported_extension(self, tmp_path):
        """An unsupported file type should be rejected."""
        fake_file = tmp_path / "resume.txt"
        fake_file.write_text("Just some text")
        text, status = extract_text(fake_file)
        assert status == "parse_failed"
        assert text is None
