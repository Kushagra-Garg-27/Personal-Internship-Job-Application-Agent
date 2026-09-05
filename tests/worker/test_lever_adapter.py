"""Unit tests for Lever stable-tier platform adapter (Phase 9)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
import httpx
import pytest

from worker.adapters.lever import LeverAdapter
from worker.adapters.base import ApplicationContext

FIXTURE_PATH = Path("tests/worker/fixtures/lever_posting_api.json")


@pytest.fixture
def lever_json():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_lever_url_extraction():
    adapter = LeverAdapter()
    site, posting_id = adapter.extract_site_and_posting_id(
        "https://jobs.lever.co/spotify/abc-123-def"
    )
    assert site == "spotify"
    assert posting_id == "abc-123-def"

    s_none, p_none = adapter.extract_site_and_posting_id("https://example.com/not-lever")
    assert s_none is None
    assert p_none is None


def test_lever_extract(lever_json):
    mock_client = MagicMock(spec=httpx.Client)
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = lever_json
    mock_client.get.return_value = mock_resp

    adapter = LeverAdapter(http_client=mock_client)
    extracted = adapter.extract({
        "url": "https://jobs.lever.co/spotify/abc-123-def",
        "company": "Spotify",
        "title": "Junior Python Developer",
    })

    assert extracted.supports_programmatic_submission is True
    assert extracted.board_token == "spotify"
    assert extracted.job_id == "abc-123-def"
    assert len(extracted.custom_questions) == 2
    assert extracted.custom_questions[0]["id"] == "cq_1"
    assert "Why do you want to work at Spotify?" in extracted.custom_questions[0]["label"]


def test_lever_fill_prepares_draft(lever_json):
    mock_client = MagicMock(spec=httpx.Client)
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = lever_json
    mock_client.get.return_value = mock_resp

    adapter = LeverAdapter(http_client=mock_client)
    app_ctx = adapter.open_application(
        "https://jobs.lever.co/spotify/abc-123-def", opportunity_id=88
    )

    candidate_data = {
        "full_name": "Bob Smith",
        "email": "bob.smith@example.com",
        "phone": "+1 555-0188",
        "links": [
            {"link_type": "LinkedIn", "url": "https://linkedin.com/in/bobsmith"},
            {"link_type": "GitHub", "url": "https://github.com/bobsmith"},
        ],
    }
    custom_answers = [
        {
            "question_id": "cq_1",
            "answer": "[AI DRAFT - PENDING APPROVAL]\nI love music and high scale streaming architecture.",
        },
    ]

    fill_res = adapter.fill(
        app_ctx=app_ctx,
        candidate_data=candidate_data,
        resume_path="/path/to/bob_resume.pdf",
        custom_answers=custom_answers,
    )

    assert fill_res.success is True
    assert fill_res.status == "ready_for_review"
    assert fill_res.draft_payload is not None
    assert fill_res.draft_payload["fields"]["name"] == "Bob Smith"
    assert fill_res.draft_payload["fields"]["email"] == "bob.smith@example.com"
    assert fill_res.draft_payload["fields"]["urls"]["LinkedIn"] == "https://linkedin.com/in/bobsmith"
    assert fill_res.draft_payload["fields"]["urls"]["GitHub"] == "https://github.com/bobsmith"
    assert "[AI DRAFT" in fill_res.draft_payload["fields"]["comments"]


def test_lever_timeout_handling():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.side_effect = httpx.TimeoutException("Connection timed out")

    adapter = LeverAdapter(http_client=mock_client)
    draft_payload = {
        "submission_url": "https://api.lever.co/v0/postings/spotify/abc-123",
        "fields": {"name": "Bob"},
    }
    result = adapter.execute_submission(draft_payload)
    assert result["success"] is False
    assert result["ambiguous_timeout"] is True
