"""Unit tests for Greenhouse stable-tier platform adapter (Phase 9)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
import httpx
import pytest

from worker.adapters.greenhouse import GreenhouseAdapter
from worker.adapters.base import ApplicationContext

FIXTURE_PATH = Path("tests/worker/fixtures/greenhouse_job_api.json")


@pytest.fixture
def greenhouse_json():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_greenhouse_url_extraction():
    adapter = GreenhouseAdapter()
    board, job_id = adapter.extract_board_and_job_id("https://boards.greenhouse.io/acmecorp/jobs/98765")
    assert board == "acmecorp"
    assert job_id == "98765"

    board2, job_id2 = adapter.extract_board_and_job_id("https://job-boards.greenhouse.io/stripe/jobs/112233")
    assert board2 == "stripe"
    assert job_id2 == "112233"

    b_none, j_none = adapter.extract_board_and_job_id("https://example.com/other")
    assert b_none is None
    assert j_none is None


def test_greenhouse_extract(greenhouse_json):
    mock_client = MagicMock(spec=httpx.Client)
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = greenhouse_json
    mock_client.get.return_value = mock_resp

    adapter = GreenhouseAdapter(http_client=mock_client)
    extracted = adapter.extract({
        "url": "https://boards.greenhouse.io/acme/jobs/12345",
        "company": "Acme Corp",
        "title": "Software Engineer Intern",
    })

    assert extracted.supports_programmatic_submission is True
    assert extracted.board_token == "acme"
    assert extracted.job_id == "12345"
    assert "first_name" in extracted.fields_required
    assert len(extracted.custom_questions) == 2
    assert extracted.custom_questions[0]["id"] == "201"
    assert extracted.custom_questions[0]["label"] == "Why do you want to join Acme Corp?"


def test_greenhouse_fill_prepares_draft(greenhouse_json):
    mock_client = MagicMock(spec=httpx.Client)
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = greenhouse_json
    mock_client.get.return_value = mock_resp

    adapter = GreenhouseAdapter(http_client=mock_client)
    app_ctx = adapter.open_application("https://boards.greenhouse.io/acme/jobs/12345", opportunity_id=42)

    candidate_data = {
        "full_name": "Jane Doe",
        "email": "jane.doe@example.com",
        "phone": "+1 555-0199",
    }
    custom_answers = [
        {
            "question_id": "201",
            "answer": "[AI DRAFT - PENDING APPROVAL]\nI have long admired Acme Corp's open platform.",
        },
        {
            "question_id": "202",
            "answer": "[AI DRAFT - PENDING APPROVAL]\nI built a distributed cache in Rust.",
        },
    ]

    fill_res = adapter.fill(
        app_ctx=app_ctx,
        candidate_data=candidate_data,
        resume_path="/path/to/resume.pdf",
        custom_answers=custom_answers,
    )

    assert fill_res.success is True
    assert fill_res.status == "ready_for_review"
    assert fill_res.draft_payload is not None
    assert fill_res.draft_payload["fields"]["first_name"] == "Jane"
    assert fill_res.draft_payload["fields"]["last_name"] == "Doe"
    assert fill_res.draft_payload["fields"]["email"] == "jane.doe@example.com"
    assert fill_res.draft_payload["fields"]["question_why_acme"].startswith("[AI DRAFT")
    assert fill_res.draft_payload["resume_path"] == "/path/to/resume.pdf"


def test_greenhouse_extract_404_fails_closed():
    mock_client = MagicMock(spec=httpx.Client)
    mock_resp = MagicMock(status_code=404)
    mock_client.get.return_value = mock_resp

    adapter = GreenhouseAdapter(http_client=mock_client)
    extracted = adapter.extract({"url": "https://boards.greenhouse.io/acme/jobs/99999"})
    assert extracted.supports_programmatic_submission is False

    app_ctx = ApplicationContext(
        opportunity_id=1,
        listing_url="https://boards.greenhouse.io/acme/jobs/99999",
        adapter_name="greenhouse",
        tier=adapter.tier,
        extracted=extracted,
    )
    fill_res = adapter.fill(app_ctx, candidate_data={})
    assert fill_res.success is False
    assert fill_res.status == "manual_required"


def test_greenhouse_execute_submission_timeout_handling():
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.side_effect = httpx.TimeoutException("Read timed out")

    adapter = GreenhouseAdapter(http_client=mock_client)
    draft_payload = {
        "submission_url": "https://boards-api.greenhouse.io/v1/boards/acme/jobs/12345",
        "fields": {"first_name": "Jane"},
    }
    result = adapter.execute_submission(draft_payload)
    assert result["success"] is False
    assert result["ambiguous_timeout"] is True
