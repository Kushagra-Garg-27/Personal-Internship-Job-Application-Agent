"""Integration tests for Internshala experimental platform adapter (Phase 9).

Runs against local recorded HTML fixture (tests/worker/fixtures/internshala_form.html).
Verifies:
- Form elements and custom questions are filled
- AI draft markers are retained
- The submit button is NEVER clicked by the worker
- Fails closed on missing session or CAPTCHA presence
"""

from __future__ import annotations

from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

from worker.adapters.base import ApplicationContext
from worker.adapters.internshala import InternshalaAdapter
from worker.security.storage import save_encrypted_storage_state

FIXTURE_HTML = Path("tests/worker/fixtures/internshala_form.html").resolve()


@pytest.fixture(scope="module")
def playwright_instance():
    with sync_playwright() as p:
        yield p


@pytest.fixture
def mock_session_file(tmp_path):
    state = {"cookies": [{"name": "auth_token", "value": "xyz123"}], "origins": []}
    fpath = tmp_path / "mock_internshala.enc"
    save_encrypted_storage_state(state, fpath, key="test-key")
    return fpath


def test_internshala_fill_leaves_browser_open_and_unsubmitted(playwright_instance, mock_session_file):
    adapter = InternshalaAdapter(
        session_file=mock_session_file,
        headless=True,
        playwright_instance=playwright_instance,
    )

    browser = playwright_instance.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    # Load local fixture HTML
    file_url = f"file:///{FIXTURE_HTML.as_posix()}"
    page.goto(file_url)

    app_ctx = ApplicationContext(
        opportunity_id=101,
        listing_url=file_url,
        adapter_name="internshala",
        tier=adapter.tier,
        browser_context=context,
        browser_page=page,
    )

    candidate_data = {
        "full_name": "Alice Candidate",
        "phone": "+91 9876543210",
        "email": "alice@example.com",
    }
    custom_answers = [
        {
            "question_id": "cover_letter",
            "answer": "[AI DRAFT - PENDING APPROVAL]\nI have 3 years of Python experience building APIs.",
        },
        {
            "question_id": "availability",
            "answer": "Yes, available full time.",
        },
    ]

    fill_result = adapter.fill(app_ctx, candidate_data, custom_answers=custom_answers)

    assert fill_result.success is True
    assert fill_result.status == "ready_for_review"

    # Verify form inputs in DOM were filled
    cl_val = page.locator("#cover_letter").input_value()
    assert "[AI DRAFT - PENDING APPROVAL]" in cl_val
    assert "3 years of Python" in cl_val

    avail_val = page.locator("#other_experiences").input_value()
    assert "Yes, available full time" in avail_val

    # CRITICAL CHECK: Submit button was NOT clicked (page URL remained on fixture, form not submitted)
    assert page.url == file_url

    browser.close()


def test_internshala_missing_session_fails_closed(playwright_instance, tmp_path):
    missing_file = tmp_path / "nonexistent.enc"
    adapter = InternshalaAdapter(
        session_file=missing_file,
        headless=True,
        playwright_instance=playwright_instance,
    )

    app_ctx = adapter.open_application("https://internshala.com/internship/detail/test-123")
    assert app_ctx.metadata.get("session_missing") is True

    fill_res = adapter.fill(app_ctx, candidate_data={})
    assert fill_res.success is False
    assert fill_res.status == "manual_required"
    assert "session state missing" in fill_res.error_reason.lower()


def test_internshala_captcha_fails_closed(playwright_instance, mock_session_file):
    adapter = InternshalaAdapter(
        session_file=mock_session_file,
        headless=True,
        playwright_instance=playwright_instance,
    )

    browser = playwright_instance.chromium.launch(headless=True)
    page = browser.new_page()

    # Load page with injected CAPTCHA element
    page.set_content("""
        <html>
          <body>
            <div class="g-recaptcha"></div>
          </body>
        </html>
    """)

    app_ctx = ApplicationContext(
        opportunity_id=202,
        listing_url="https://internshala.com/internship/detail/test-202",
        adapter_name="internshala",
        tier=adapter.tier,
        browser_page=page,
    )

    fill_res = adapter.fill(app_ctx, candidate_data={})
    assert fill_res.success is False
    assert fill_res.status == "manual_required"
    assert "captcha" in fill_res.error_reason.lower()

    browser.close()
