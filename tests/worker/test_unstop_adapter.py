"""Integration tests for Unstop experimental platform adapter (Phase 9).

Runs against local recorded HTML fixture (tests/worker/fixtures/unstop_form.html).
Verifies:
- Form elements and custom questions are filled
- AI draft markers are retained
- The submit button is NEVER clicked by the worker
- Fails closed on bot detection
"""

from __future__ import annotations

from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

from worker.adapters.base import ApplicationContext
from worker.adapters.unstop import UnstopAdapter
from worker.security.storage import save_encrypted_storage_state

FIXTURE_HTML = Path("tests/worker/fixtures/unstop_form.html").resolve()


@pytest.fixture(scope="module")
def playwright_instance():
    with sync_playwright() as p:
        yield p


@pytest.fixture
def mock_session_file(tmp_path):
    state = {"cookies": [{"name": "auth_token", "value": "xyz123"}], "origins": []}
    fpath = tmp_path / "mock_unstop.enc"
    save_encrypted_storage_state(state, fpath, key="test-key")
    return fpath


def test_unstop_fill_leaves_browser_open_and_unsubmitted(playwright_instance, mock_session_file):
    adapter = UnstopAdapter(
        session_file=mock_session_file,
        headless=True,
        playwright_instance=playwright_instance,
    )

    browser = playwright_instance.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    file_url = f"file:///{FIXTURE_HTML.as_posix()}"
    page.goto(file_url)

    app_ctx = ApplicationContext(
        opportunity_id=201,
        listing_url=file_url,
        adapter_name="unstop",
        tier=adapter.tier,
        browser_context=context,
        browser_page=page,
    )

    candidate_data = {
        "full_name": "Charlie Dev",
        "phone": "+91 9988776655",
        "email": "charlie@example.com",
    }
    custom_answers = [
        {
            "question_id": "statement_of_purpose",
            "answer": "[AI DRAFT - PENDING APPROVAL]\nI want to work with innovative engineering teams.",
        },
    ]

    fill_result = adapter.fill(app_ctx, candidate_data, custom_answers=custom_answers)

    assert fill_result.success is True
    assert fill_result.status == "ready_for_review"

    # Verify textarea filled in DOM
    sop_val = page.locator("textarea[name='statement_of_purpose']").input_value()
    assert "[AI DRAFT - PENDING APPROVAL]" in sop_val
    assert "innovative engineering teams" in sop_val

    # CRITICAL CHECK: Submit button was NOT clicked
    assert page.url == file_url

    browser.close()


def test_unstop_bot_challenge_fails_closed(playwright_instance, mock_session_file):
    adapter = UnstopAdapter(
        session_file=mock_session_file,
        headless=True,
        playwright_instance=playwright_instance,
    )

    browser = playwright_instance.chromium.launch(headless=True)
    page = browser.new_page()

    # Load page with Cloudflare challenge
    page.set_content("""
        <html>
          <body>
            <div id="challenge-stage">Checking if you are human...</div>
          </body>
        </html>
    """)

    app_ctx = ApplicationContext(
        opportunity_id=202,
        listing_url="https://unstop.com/opportunities/test",
        adapter_name="unstop",
        tier=adapter.tier,
        browser_page=page,
    )

    fill_res = adapter.fill(app_ctx, candidate_data={})
    assert fill_res.success is False
    assert fill_res.status == "manual_required"
    assert "bot verification" in fill_res.error_reason.lower()

    browser.close()
