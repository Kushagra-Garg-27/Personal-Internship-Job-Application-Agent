"""Contract tests for Greenhouse and Lever stable-tier platform adapters (Phase 9).

Pins adapter input/output schemas against official public API documentation:
- Greenhouse: https://developers.greenhouse.io/job-board.html
- Lever: https://github.com/lever/postings-api
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock
import httpx

from worker.adapters.greenhouse import GreenhouseAdapter
from worker.adapters.lever import LeverAdapter

FIXTURES_DIR = Path("tests/worker/fixtures")


def test_greenhouse_contract_schema():
    """Verify Greenhouse adapter extracts fields matching the public Board API schema."""
    data = json.loads((FIXTURES_DIR / "greenhouse_job_api.json").read_text(encoding="utf-8"))

    # Contract assertions on raw API shape
    assert "id" in data
    assert "title" in data
    assert "questions" in data
    assert isinstance(data["questions"], list)

    mock_client = MagicMock(spec=httpx.Client)
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = data
    mock_client.get.return_value = mock_resp

    adapter = GreenhouseAdapter(http_client=mock_client)
    extracted = adapter.extract({"url": "https://boards.greenhouse.io/testboard/jobs/12345"})

    # Contract assertions on extracted entity
    assert extracted.board_token == "testboard"
    assert extracted.job_id == "12345"
    assert set(extracted.fields_required) == {"first_name", "last_name", "email", "phone", "resume"}

    # Custom questions must contain id, label, required, name
    for q in extracted.custom_questions:
        assert "id" in q
        assert "label" in q
        assert "required" in q


def test_lever_contract_schema():
    """Verify Lever adapter extracts fields matching the public Postings API schema."""
    data = json.loads((FIXTURES_DIR / "lever_posting_api.json").read_text(encoding="utf-8"))

    # Contract assertions on raw API shape
    assert "id" in data
    assert "text" in data
    assert "categories" in data
    assert "customQuestions" in data
    assert isinstance(data["customQuestions"], list)

    mock_client = MagicMock(spec=httpx.Client)
    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = data
    mock_client.get.return_value = mock_resp

    adapter = LeverAdapter(http_client=mock_client)
    extracted = adapter.extract({"url": "https://jobs.lever.co/testorg/posting-123"})

    assert extracted.board_token == "testorg"
    assert extracted.job_id == "posting-123"
    assert "name" in extracted.fields_required
    assert "email" in extracted.fields_required

    for cq in extracted.custom_questions:
        assert "id" in cq
        assert "label" in cq
        assert "required" in cq
