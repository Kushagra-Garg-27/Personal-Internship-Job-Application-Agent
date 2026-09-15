"""Tests for scripts/inspect_unstop_submission_controls.py (M2A / Phase 6).

Verifies safety invariants of the diagnostic inspection CLI:
- Validates original URL (HTTPS, unstop.com or *.unstop.com, rejects credentials and lookalikes).
- Preserves URL query parameters for page navigation while redacting from logs and JSON output without netloc/userinfo.
- Refuses to run without the mandatory safety flag.
- Proves statically (via AST) that it cannot invoke mutation/submission methods.
- Instruments runtime page and verifies ZERO mutating calls (click, fill, press, submit, etc.).
- Proves higher-level submission methods (Adapter, Filler, Runner) are never reached.
- Rejects post-navigation redirects to foreign origins, even if containing credentials or fragments.
- Fails closed on missing explicit session files.
- Uses real session loader import path from worker.security.storage.
"""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.inspect_unstop_submission_controls import (
    redact_url,
    run_inspection,
    validate_unstop_url,
)


# ── 1. Target URL Validation & Redaction Tests ──────────────────────────────

def test_validate_unstop_url_preserves_query_params_for_navigation():
    """Verify validate_unstop_url preserves query parameters for page navigation."""
    raw = "https://unstop.com/competitions/12345/register?source=email&token=partner_123#step2"
    valid = validate_unstop_url(raw)
    assert valid == raw, "Original validated URL must be preserved intact for browser navigation"


def test_validate_unstop_url_accepts_subdomains():
    """Verify subdomains like jobs.unstop.com are accepted."""
    url = "https://jobs.unstop.com/apply/123?ref=feed"
    assert validate_unstop_url(url) == url


def test_validate_unstop_url_rejects_non_https():
    """Verify http and file URLs are rejected."""
    with pytest.raises(ValueError, match="only https is permitted"):
        validate_unstop_url("http://unstop.com/competitions/123")

    with pytest.raises(ValueError, match="only https is permitted"):
        validate_unstop_url("file:///etc/passwd")


def test_validate_unstop_url_rejects_embedded_credentials():
    """Verify URLs with username/password are rejected."""
    with pytest.raises(ValueError, match="embedded credentials are strictly rejected"):
        validate_unstop_url("https://user:pass@unstop.com/competitions/123")


def test_validate_unstop_url_rejects_lookalikes():
    """Verify lookalike domains like unstop.com.evil.test are rejected."""
    with pytest.raises(ValueError, match="Unauthorized hostname"):
        validate_unstop_url("https://unstop.com.evil.example/phishing")

    with pytest.raises(ValueError, match="Unauthorized hostname"):
        validate_unstop_url("https://evil.example/?next=https://unstop.com")


def test_redact_url_strips_query_and_fragment():
    """Verify redact_url removes query strings and fragments for logs and JSON."""
    raw = "https://unstop.com/competitions/12345/register?source=email&token=secret_123#step2"
    redacted = redact_url(raw)
    assert redacted == "https://unstop.com/competitions/12345/register"
    assert "source" not in redacted
    assert "secret_123" not in redacted
    assert "#" not in redacted


def test_redact_url_strips_userinfo_credentials():
    """Verify redact_url never preserves netloc userinfo (username:password)."""
    raw = "https://user:password@evil.test:8080/path/to/page?token=secret#fragment"
    redacted = redact_url(raw)
    assert "user" not in redacted
    assert "password" not in redacted
    assert "token" not in redacted
    assert "fragment" not in redacted
    assert redacted == "https://evil.test:8080/path/to/page"


# ── 2. Inspection Tool Execution & Safety Flags ─────────────────────────────

def test_inspection_refuses_without_mandatory_safety_flag(tmp_path):
    """Verify tool returns exit code 2 when safety acknowledgment is missing."""
    out_file = tmp_path / "out.json"
    code = run_inspection(
        url="https://unstop.com/competitions/123/register",
        output_path=out_file,
        acknowledge_flag=False,
    )
    assert code == 2
    assert not out_file.exists()


def test_inspection_navigates_with_params_but_redacts_in_output(tmp_path):
    """Verify tool calls page.goto with original URL and writes redacted URL to JSON."""
    mock_page = MagicMock()
    mock_page.url = "https://unstop.com/competitions/123/register?source=email&secret=hidden"
    mock_page.locator.return_value.count.return_value = 0
    mock_page.query_selector_all.return_value = []

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page

    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    out_file = tmp_path / "out.json"
    target_url = "https://unstop.com/competitions/123/register?source=email&secret=hidden"
    code = run_inspection(
        url=target_url,
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
    )
    assert code == 0
    mock_page.goto.assert_called_once_with(target_url, wait_until="domcontentloaded", timeout=30000)

    assert out_file.exists()
    content = json.loads(out_file.read_text(encoding="utf-8"))
    assert content["inspected_url"] == "https://unstop.com/competitions/123/register"
    assert "secret" not in json.dumps(content)


def test_inspection_fails_closed_on_cloudflare_challenge(tmp_path):
    """Verify Cloudflare detection aborts with code 3 without writing output."""
    mock_page = MagicMock()
    mock_page.url = "https://unstop.com/competitions/123/register"
    mock_page.locator.return_value.count.return_value = 1

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page

    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    out_file = tmp_path / "out.json"
    code = run_inspection(
        url="https://unstop.com/competitions/123/register",
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
    )
    assert code == 3
    assert not out_file.exists()


def test_inspection_fails_closed_on_login_redirect(tmp_path):
    """Verify unexpected redirect to /login aborts with code 4."""
    mock_page = MagicMock()
    mock_page.url = "https://unstop.com/auth/login"
    mock_page.locator.return_value.count.return_value = 0

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page

    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    out_file = tmp_path / "out.json"
    code = run_inspection(
        url="https://unstop.com/competitions/123/register",
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
    )
    assert code == 4
    assert not out_file.exists()


# ── 3. Runtime Immutability & Safety Invariant Tests ─────────────────────────

def test_ast_proves_no_submission_or_mutating_calls():
    """Static AST check proving script contains zero calls to mutation/submission methods."""
    script_path = Path("scripts/inspect_unstop_submission_controls.py")
    tree = ast.parse(script_path.read_text(encoding="utf-8"))

    forbidden_methods = {
        "click",
        "dblclick",
        "press",
        "type",
        "fill",
        "check",
        "uncheck",
        "select_option",
        "set_input_files",
        "dispatch_event",
        "drag_to",
        "submit",
        "submit_application",
        "poll_and_submit_queue",
        "execute_browser_submission",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_methods, (
                    f"CRITICAL SAFETY VIOLATION: '{node.func.attr}' called in inspection script!"
                )


def test_runtime_page_instrumentation_proves_zero_mutations(tmp_path):
    """Dynamic test instrumenting page object: asserts zero mutating/submission methods invoked."""
    mutating_methods = [
        "click",
        "dblclick",
        "press",
        "type",
        "fill",
        "check",
        "uncheck",
        "select_option",
        "set_input_files",
        "dispatch_event",
        "drag_to",
        "submit",
    ]

    mock_handle = MagicMock()
    mock_handle.evaluate.return_value = {
        "tag_name": "button",
        "raw_text": "Complete Registration",
        "control_type": "button",
        "element_id": "unstop_submit",
        "name": None,
        "role": "button",
        "aria_label": None,
        "title": None,
        "data_testid": None,
        "is_disabled": False,
        "is_visible": True,
        "form_action": "/register",
        "form_method": "POST",
        "is_in_active_form": True,
        "has_form": True,
        "form_id": "reg-form",
        "css_classes": ["submit-btn"],
    }
    mock_handle.is_visible.return_value = True
    mock_handle.is_enabled.return_value = True

    mock_page = MagicMock()
    mock_page.url = "https://unstop.com/competitions/123/register"
    mock_page.locator.return_value.count.return_value = 0
    mock_page.query_selector_all.return_value = [mock_handle]

    for m in mutating_methods:
        setattr(mock_page, m, MagicMock(side_effect=AssertionError(f"FORBIDDEN MUTATION: page.{m}() called!")))
        setattr(mock_page.locator.return_value, m, MagicMock(side_effect=AssertionError(f"FORBIDDEN MUTATION: locator.{m}() called!")))

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page

    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    out_file = tmp_path / "out.json"

    with (
        patch("worker.adapters.unstop.UnstopAdapter.fill", side_effect=AssertionError("UnstopAdapter.fill called!")),
        patch("worker.adapters.unstop.UnstopAdapter.submit_application", side_effect=AssertionError("UnstopAdapter.submit_application called!")),
        patch("worker.engine.filler.ApplicationFiller.execute_browser_submission", side_effect=AssertionError("execute_browser_submission called!")),
        patch("worker.runner.WorkerRunner.poll_and_submit_queue", side_effect=AssertionError("poll_and_submit_queue called!")),
    ):
        code = run_inspection(
            url="https://unstop.com/competitions/123/register?tab=details",
            output_path=out_file,
            acknowledge_flag=True,
            playwright_instance=mock_pw,
        )

    assert code == 0
    assert out_file.exists()

    for m in mutating_methods:
        call_count = getattr(mock_page, m).call_count
        assert call_count == 0, f"Expected 0 calls to page.{m}, got {call_count}"


def test_inspection_fails_closed_on_post_navigation_foreign_redirect(tmp_path):
    """Verify tool aborts with code 5 when page.goto redirects to a foreign/unauthorized origin."""
    mock_page = MagicMock()
    mock_page.url = "https://evil.test/phishing"

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page

    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    out_file = tmp_path / "out.json"
    code = run_inspection(
        url="https://unstop.com/competitions/123/register",
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
    )
    assert code == 5
    assert not out_file.exists(), "Output must not be created when post-navigation URL is foreign"


def test_post_navigation_redirect_with_credentials_redacts_cleanly(tmp_path, caplog):
    """Verify redirect URL with embedded credentials logs cleanly without leaking user/pass."""
    mock_page = MagicMock()
    mock_page.url = "https://evil_user:evil_password@evil.test/phishing?token=secret123#fragment"

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page

    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    out_file = tmp_path / "out.json"
    with caplog.at_level(logging.ERROR):
        code = run_inspection(
            url="https://unstop.com/competitions/123/register",
            output_path=out_file,
            acknowledge_flag=True,
            playwright_instance=mock_pw,
        )
    assert code == 5
    assert "evil_user" not in caplog.text
    assert "evil_password" not in caplog.text
    assert "secret123" not in caplog.text


def test_missing_explicit_session_file_aborts_closed(tmp_path):
    """Verify supplying a nonexistent session file aborts with code 2 without opening browser."""
    out_file = tmp_path / "out.json"
    missing_file = tmp_path / "does_not_exist.enc"

    mock_pw = MagicMock()
    code = run_inspection(
        url="https://unstop.com/competitions/123/register",
        output_path=out_file,
        acknowledge_flag=True,
        session_file=missing_file,
        playwright_instance=mock_pw,
    )
    assert code == 2
    assert not out_file.exists()


def test_real_session_loader_import_path(tmp_path):
    """Verify loading real encrypted session via worker.security.storage with mocked key."""
    from worker.security.storage import load_decrypted_storage_state, save_encrypted_storage_state

    session_data = {"cookies": [{"name": "sid", "value": "test_cookie", "domain": ".unstop.com"}]}
    enc_path = tmp_path / "unstop_session.enc"
    test_key = b"A" * 32  # 32-byte key

    save_encrypted_storage_state(session_data, enc_path, key=test_key)
    loaded = load_decrypted_storage_state(enc_path, key=test_key)
    assert loaded == session_data


def test_inspection_metadata_sanitization_redacts_pii_and_credentials(tmp_path):
    """Verify metadata output redacts simulated emails, phones, tokens, credentials, and long strings."""
    sensitive_email = "candidate_john@example.com"
    sensitive_phone = "+1-555-867-5309"
    sensitive_bearer = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    long_raw_text = "A" * 150

    mock_handle = MagicMock()
    mock_handle.evaluate.return_value = {
        "tag_name": "button",
        "raw_text": long_raw_text,
        "control_type": "button",
        "element_id": f"btn_{sensitive_email}",
        "name": f"btn_{sensitive_phone}",
        "role": "button",
        "aria_label": f"auth {sensitive_bearer}",
        "title": f"title_{sensitive_bearer}",
        "data_testid": "data_test_with_query?token=secret123",
        "is_disabled": False,
        "is_visible": True,
        "form_action": "https://user:pass@unstop.com/apply/submit?token=topsecret#section",
        "form_method": "POST",
        "is_in_active_form": True,
        "has_form": True,
        "form_id": "form-1",
        "css_classes": ["btn-primary", "class_with_token_secret"],
    }
    mock_handle.is_visible.return_value = True
    mock_handle.is_enabled.return_value = True

    mock_page = MagicMock()
    mock_page.url = "https://unstop.com/competitions/123/register?sensitive_param=top_secret_token"
    mock_page.locator.return_value.count.return_value = 0
    mock_page.query_selector_all.return_value = [mock_handle]

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page

    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    out_file = tmp_path / "out.json"
    code = run_inspection(
        url="https://unstop.com/competitions/123/register?sensitive_param=top_secret_token",
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
    )
    assert code == 0
    assert out_file.exists()

    raw_json = out_file.read_text(encoding="utf-8")
    parsed_json = json.loads(raw_json)

    # Verify query parameter is redacted from inspected_url
    assert parsed_json["inspected_url"] == "https://unstop.com/competitions/123/register"

    # Verify sensitive patterns are absent from output JSON
    for forbidden in [sensitive_email, sensitive_phone, sensitive_bearer, "top_secret_token", "user:pass", "topsecret", long_raw_text]:
        assert forbidden not in raw_json, f"CRITICAL LEAK: '{forbidden}' found in persisted inspection JSON!"


def test_session_path_absence_from_logs(tmp_path, caplog):
    """Verify session-file path is strictly omitted from log output on missing/invalid session file."""
    secret_dir = tmp_path / "top_secret_dir_9981"
    secret_session = secret_dir / "secret_user_session.enc"
    out_file = tmp_path / "out.json"

    with caplog.at_level(logging.DEBUG):
        code = run_inspection(
            url="https://unstop.com/competitions/123/register",
            output_path=out_file,
            acknowledge_flag=True,
            session_file=secret_session,
            playwright_instance=MagicMock(),
        )

    assert code == 2
    assert not out_file.exists()
    assert "top_secret_dir_9981" not in caplog.text
    assert "secret_user_session.enc" not in caplog.text
    assert "SAFETY ABORT: Explicit session file does not exist." in caplog.text


def test_navigation_challenge_inspection_exceptions_produce_bounded_cli_exits(tmp_path, caplog):
    """Verify navigation, challenge probe, inspection, context, and output write failures return bounded exit codes."""
    out_file = tmp_path / "out.json"
    target_url = "https://unstop.com/competitions/123/register?secret_key=xyz"

    # 1. Navigation failure => code 4
    mock_page_nav_err = MagicMock()
    mock_page_nav_err.url = target_url
    mock_page_nav_err.goto.side_effect = RuntimeError("Navigation network timeout")
    mock_browser_nav = MagicMock()
    mock_browser_nav.new_page.return_value = mock_page_nav_err
    mock_pw_nav = MagicMock()
    mock_pw_nav.chromium.launch.return_value = mock_browser_nav

    code_nav = run_inspection(
        url=target_url,
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw_nav,
    )
    assert code_nav == 4

    # 2. Challenge probe failure => code 3
    mock_page_chal_err = MagicMock()
    mock_page_chal_err.url = target_url
    mock_page_chal_err.locator.side_effect = RuntimeError("Challenge locator error")
    mock_browser_chal = MagicMock()
    mock_browser_chal.new_page.return_value = mock_page_chal_err
    mock_pw_chal = MagicMock()
    mock_pw_chal.chromium.launch.return_value = mock_browser_chal

    code_chal = run_inspection(
        url=target_url,
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw_chal,
    )
    assert code_chal == 3

    # 3. Inspection failure => code 6
    mock_page_insp_err = MagicMock()
    mock_page_insp_err.url = target_url
    mock_page_insp_err.locator.return_value.count.return_value = 0
    mock_page_insp_err.query_selector_all.side_effect = RuntimeError("DOM snapshot failure")
    mock_browser_insp = MagicMock()
    mock_browser_insp.new_page.return_value = mock_page_insp_err
    mock_pw_insp = MagicMock()
    mock_pw_insp.chromium.launch.return_value = mock_browser_insp

    code_insp = run_inspection(
        url=target_url,
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw_insp,
    )
    assert code_insp == 6

    # 4. Context/page creation failure => code 2
    mock_browser_ctx_err = MagicMock()
    mock_browser_ctx_err.new_page.side_effect = RuntimeError("Page allocation failed")
    mock_pw_ctx = MagicMock()
    mock_pw_ctx.chromium.launch.return_value = mock_browser_ctx_err

    code_ctx = run_inspection(
        url=target_url,
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw_ctx,
    )
    assert code_ctx == 2

    # 5. Output write failure => code 7
    mock_page_out = MagicMock()
    mock_page_out.url = target_url
    mock_page_out.locator.return_value.count.return_value = 0
    mock_page_out.query_selector_all.return_value = []
    mock_browser_out = MagicMock()
    mock_browser_out.new_page.return_value = mock_page_out
    mock_pw_out = MagicMock()
    mock_pw_out.chromium.launch.return_value = mock_browser_out

    # Pass an output path that fails writing (e.g. an existing directory)
    bad_output_path = tmp_path / "dir_as_file"
    bad_output_path.mkdir()

    code_out = run_inspection(
        url=target_url,
        output_path=bad_output_path,
        acknowledge_flag=True,
        playwright_instance=mock_pw_out,
    )
    assert code_out == 7
