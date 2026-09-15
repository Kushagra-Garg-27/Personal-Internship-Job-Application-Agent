"""Tests for scripts/inspect_unstop_submission_controls.py (M2A & M2B.1).

Verifies safety invariants and SPA hydration readiness of the diagnostic inspection CLI:
- Validates original URL (HTTPS, unstop.com or *.unstop.com, rejects credentials and lookalikes).
- Preserves URL query parameters for page navigation while redacting from logs and JSON output without netloc/userinfo.
- Refuses to run without the mandatory safety flag.
- Proves statically (via AST) that it cannot invoke mutation/submission methods.
- Instruments runtime page and verifies ZERO mutating calls (click, fill, press, submit, etc.).
- Proves higher-level submission methods (Adapter, Filler, Runner) are never reached.
- Rejects post-navigation redirects to foreign origins, even if containing credentials or fragments.
- Fails closed on missing explicit session files.
- Uses real session loader import path from worker.security.storage.
- Tests A–K for bounded SPA hydration readiness:
  A. Delayed hydration: waits for skeleton removal and control mounting.
  B. Stable snapshot: requires 3 consecutive identical structural polls.
  C. Changing DOM: stability counter resets on structural count changes.
  D. Permanent skeleton: times out with code 8, writing no artifact.
  E. Empty hydrated shell: 0 candidates through timeout aborts with code 8.
  F. Login redirect during polling: aborts with code 4.
  G. Challenge appearing during polling: aborts with code 3.
  H. Foreign redirect during polling: aborts with code 5.
  I. Probe exception: bounded probe-error code 9, no raw exception in logs.
  J. Runtime mutation instrumentation: zero mutation calls throughout wait and inspection.
  K. Timeout argument validation: rejects negative, zero, non-int, > 30000 ms.
"""

from __future__ import annotations

import ast
from decimal import Decimal
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.inspect_unstop_submission_controls import (
    CANDIDATE_CONTROL_SELECTOR,
    DEFAULT_HYDRATION_TIMEOUT_MS,
    DEFAULT_POLL_INTERVAL_MS,
    HydrationStatus,
    MAX_SKELETON_COUNT,
    PLAUSIBLE_APPLICATION_FORM_SELECTOR,
    SKELETON_SELECTOR,
    _APP_FORM_EXCLUSIONS,
    _APP_ROLE_FORM_EXCLUSIONS,
    probe_hydration_state,
    redact_url,
    run_inspection,
    validate_hydration_timeout,
    validate_readiness_details,
    validate_unstop_url,
    wait_for_hydration_readiness,
)


def _make_mock_control_handle(
    text: str = "Complete Registration",
    element_id: str = "unstop_submit",
    form_action: str = "/register",
    tag_name: str = "button",
    role: str = "button",
) -> MagicMock:
    """Create a mock element handle for submission control inspection."""
    h = MagicMock()
    h.evaluate.return_value = {
        "tag_name": tag_name,
        "raw_text": text,
        "control_type": "button" if tag_name == "button" else None,
        "element_id": element_id,
        "name": None,
        "role": role,
        "aria_label": None,
        "title": None,
        "data_testid": None,
        "is_disabled": False,
        "is_visible": True,
        "form_action": form_action,
        "form_method": "POST",
        "is_in_active_form": True,
        "has_form": True,
        "form_id": "reg-form",
        "css_classes": ["submit-btn"],
    }
    h.is_visible.return_value = True
    h.is_enabled.return_value = True
    return h


def _make_hydrated_mock_page(
    url: str = "https://unstop.com/competitions/123/register",
    cand_count: int = 1,
    form_count: int = 1,
    skeleton_count: int = 0,
    skeleton_visible: bool = True,
    handles: list[Any] | None = None,
) -> MagicMock:
    """Create a mock page with configured structural counts conforming to M2B.1."""
    page = MagicMock()
    page.url = url

    if handles is None:
        handles = [_make_mock_control_handle()] if cand_count > 0 else []

    skel_loc = MagicMock()
    skel_loc.count.return_value = skeleton_count
    skel_item = MagicMock()
    skel_item.is_visible.return_value = skeleton_visible
    skel_loc.nth.return_value = skel_item

    cand_loc = MagicMock()
    cand_loc.count.return_value = cand_count

    form_loc = MagicMock()
    form_loc.count.return_value = form_count
    form_loc.locator.return_value = cand_loc

    chal_loc = MagicMock()
    chal_loc.count.return_value = 0

    def dynamic_locator(selector):
        if "challenges.cloudflare" in selector or "challenge-stage" in selector:
            return chal_loc
        elif "skeleton" in selector or "shimmer" in selector or "loading" in selector or "spinner" in selector:
            return skel_loc
        elif "form" in selector:
            return form_loc
        elif "button" in selector:
            return cand_loc
        else:
            default_loc = MagicMock()
            default_loc.count.return_value = 0
            default_loc.locator.return_value = default_loc
            return default_loc

    page.locator.side_effect = dynamic_locator
    page.query_selector_all.return_value = handles
    return page


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
    target_url = "https://unstop.com/competitions/123/register?source=email&secret=hidden"
    mock_page = _make_hydrated_mock_page(url=target_url, cand_count=1)

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page

    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    out_file = tmp_path / "out.json"
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
    assert content["readiness_status"] == "READY_FOR_INSPECTION"
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

    mock_page = _make_hydrated_mock_page(cand_count=1)

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

    mock_page = _make_hydrated_mock_page(
        url="https://unstop.com/competitions/123/register?sensitive_param=top_secret_token",
        cand_count=1,
        handles=[mock_handle],
    )

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


def test_navigation_challenge_inspection_exceptions_produce_bounded_cli_exits(tmp_path):
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
    assert code_chal == 9

    # 3. Inspection failure => code 6
    mock_page_insp_err = _make_hydrated_mock_page(url=target_url, cand_count=1)
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
    mock_page_out = _make_hydrated_mock_page(url=target_url, cand_count=1)
    mock_browser_out = MagicMock()
    mock_browser_out.new_page.return_value = mock_page_out
    mock_pw_out = MagicMock()
    mock_pw_out.chromium.launch.return_value = mock_browser_out

    bad_output_path = tmp_path / "dir_as_file"
    bad_output_path.mkdir()

    code_out = run_inspection(
        url=target_url,
        output_path=bad_output_path,
        acknowledge_flag=True,
        playwright_instance=mock_pw_out,
    )
    assert code_out == 7


# ── 4. Milestone M2B.1 SPA Hydration Readiness Tests (A through K) ──────────

def test_m2b1_a_delayed_hydration(tmp_path):
    """Test A: Synthetic delayed hydration; detector waits for skeleton removal and mounts control."""
    out_file = tmp_path / "out.json"
    target_url = "https://unstop.com/competitions/123/register"

    poll_count = 0
    mock_handle = _make_mock_control_handle(text="Submit Application")

    mock_page = MagicMock()
    mock_page.url = target_url

    chal_loc = MagicMock()
    chal_loc.count.return_value = 0

    skel_loc = MagicMock()
    skel_loc.count.side_effect = lambda: 1 if poll_count < 2 else 0
    skel_item = MagicMock()
    skel_item.is_visible.return_value = True
    skel_loc.nth.return_value = skel_item

    cand_loc = MagicMock()
    cand_loc.count.side_effect = lambda: 0 if poll_count < 2 else 1

    form_loc = MagicMock()
    form_loc.count.side_effect = lambda: 0 if poll_count < 2 else 1
    form_loc.locator.return_value = cand_loc

    def dynamic_locator(selector):
        if "challenges.cloudflare" in selector or "challenge-stage" in selector:
            return chal_loc
        elif "skeleton" in selector or "shimmer" in selector or "loading" in selector or "spinner" in selector:
            return skel_loc
        elif "form" in selector:
            return form_loc
        elif "button" in selector:
            return cand_loc
        else:
            default_loc = MagicMock()
            default_loc.count.return_value = 0
            default_loc.locator.return_value = default_loc
            return default_loc

    mock_page.locator.side_effect = dynamic_locator
    mock_page.query_selector_all.return_value = [mock_handle]

    def on_wait_timeout(ms):
        nonlocal poll_count
        poll_count += 1

    mock_page.wait_for_timeout.side_effect = on_wait_timeout

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    code = run_inspection(
        url=target_url,
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
        hydration_timeout_ms=5000,
        poll_interval_ms=50,
    )
    assert code == 0
    assert out_file.exists()
    content = json.loads(out_file.read_text(encoding="utf-8"))
    assert content["readiness_status"] == "READY_FOR_INSPECTION"
    assert content["candidate_count"] == 1
    assert content["candidates"][0]["classification"] == "final_submit"


def test_m2b1_b_stable_snapshot_requires_three_consecutive_polls():
    """Test B: Verify detector requires exactly 3 consecutive identical snapshots before declaring readiness."""
    mock_page = _make_hydrated_mock_page(cand_count=1, form_count=1, skeleton_count=0)

    # With poll_interval_ms=10 and timeout=500
    status, elapsed, stable_count = wait_for_hydration_readiness(
        mock_page,
        timeout_ms=500,
        poll_interval_ms=10,
        allow_test_hosts=True,
    )
    assert status == HydrationStatus.READY_FOR_INSPECTION
    assert stable_count >= 3


def test_m2b1_c_changing_dom_resets_stability_counter():
    """Test C: Structural count change resets the stability counter."""
    mock_page = MagicMock()
    mock_page.url = "https://unstop.com/competitions/123/register"

    poll_step = 0

    chal_loc = MagicMock()
    chal_loc.count.return_value = 0

    skel_loc = MagicMock()
    skel_loc.count.return_value = 0

    cand_loc = MagicMock()
    cand_loc.count.side_effect = lambda: 1 if poll_step == 0 else 2

    form_loc = MagicMock()
    form_loc.count.return_value = 1
    form_loc.locator.return_value = cand_loc

    def dynamic_locator(selector):
        if "challenges.cloudflare" in selector or "challenge-stage" in selector:
            return chal_loc
        elif "skeleton" in selector or "shimmer" in selector or "loading" in selector or "spinner" in selector:
            return skel_loc
        elif "form" in selector:
            return form_loc
        elif "button" in selector:
            return cand_loc
        else:
            default_loc = MagicMock()
            default_loc.count.return_value = 0
            default_loc.locator.return_value = default_loc
            return default_loc

    def on_wait(ms):
        nonlocal poll_step
        poll_step += 1

    mock_page.locator.side_effect = dynamic_locator
    mock_page.wait_for_timeout.side_effect = on_wait

    status, elapsed, stable_count = wait_for_hydration_readiness(
        mock_page,
        timeout_ms=500,
        poll_interval_ms=10,
        allow_test_hosts=True,
    )
    assert status == HydrationStatus.READY_FOR_INSPECTION
    # Must have taken at least 3 steps of candidate_count=2 after resetting
    assert poll_step >= 3


def test_m2b1_d_permanent_skeleton_aborts_with_timeout_code_8(tmp_path):
    """Test D: Skeleton never clears; returns exit code 8 and writes NO artifact."""
    out_file = tmp_path / "out.json"
    mock_page = _make_hydrated_mock_page(cand_count=1, form_count=1, skeleton_count=1, skeleton_visible=True)

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    code = run_inspection(
        url="https://unstop.com/competitions/123/register",
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
        hydration_timeout_ms=1000,
        poll_interval_ms=100,
    )
    assert code == 8
    assert not out_file.exists()


def test_m2b1_e_empty_hydrated_shell_aborts_with_timeout_code_8(tmp_path):
    """Test E: Skeleton absent but 0 candidate controls through timeout; aborts with code 8 (NOT success)."""
    out_file = tmp_path / "out.json"
    mock_page = _make_hydrated_mock_page(cand_count=0, form_count=1, skeleton_count=0)

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    code = run_inspection(
        url="https://unstop.com/competitions/123/register",
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
        hydration_timeout_ms=1000,
        poll_interval_ms=100,
    )
    assert code == 8
    assert not out_file.exists()


class _DynamicUrlMockPage:
    """Mock page that simulates URL changes across polling intervals."""

    def __init__(self, initial_url: str, shifted_url: str, switch_poll: int = 1):
        self.initial_url = initial_url
        self.shifted_url = shifted_url
        self.switch_poll = switch_poll
        self.poll_count = 0

        self.chal_loc = MagicMock()
        self.chal_loc.count.return_value = 0

        self.skel_loc = MagicMock()
        self.skel_loc.count.return_value = 0

        self.cand_loc = MagicMock()
        self.cand_loc.count.return_value = 1

        self.form_loc = MagicMock()
        self.form_loc.count.return_value = 1
        self.form_loc.locator.return_value = self.cand_loc

        def dynamic_loc(selector):
            if "challenges.cloudflare" in selector or "challenge-stage" in selector:
                return self.chal_loc
            elif "skeleton" in selector or "shimmer" in selector or "loading" in selector or "spinner" in selector:
                return self.skel_loc
            elif "form" in selector:
                return self.form_loc
            elif "button" in selector:
                return self.cand_loc
            else:
                default_loc = MagicMock()
                default_loc.count.return_value = 0
                default_loc.locator.return_value = default_loc
                return default_loc

        self.locator = MagicMock(side_effect=dynamic_loc)
        self.query_selector_all = MagicMock(return_value=[_make_mock_control_handle()])

    @property
    def url(self) -> str:
        if self.poll_count >= self.switch_poll:
            return self.shifted_url
        return self.initial_url

    def wait_for_timeout(self, ms: int):
        self.poll_count += 1

    def goto(self, url: str, **kwargs):
        pass


def test_m2b1_f_login_state_appearing_during_polling(tmp_path):
    """Test F: Page shifts to /login during polling; aborts with code 4 and writes no artifact."""
    out_file = tmp_path / "out.json"
    mock_page = _DynamicUrlMockPage(
        initial_url="https://unstop.com/competitions/123/register",
        shifted_url="https://unstop.com/auth/login",
        switch_poll=1,
    )

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    code = run_inspection(
        url="https://unstop.com/competitions/123/register",
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
        hydration_timeout_ms=2000,
        poll_interval_ms=50,
    )

    assert code == 4
    assert not out_file.exists()


def test_m2b1_g_challenge_appearing_during_polling(tmp_path):
    """Test G: Bot challenge appears during polling; aborts with code 3 and writes no artifact."""
    out_file = tmp_path / "out.json"
    poll_step = 0
    mock_page = MagicMock()
    mock_page.url = "https://unstop.com/competitions/123/register"

    chal_loc = MagicMock()
    chal_loc.count.side_effect = lambda: 1 if poll_step >= 1 else 0

    skel_loc = MagicMock()
    skel_loc.count.return_value = 0

    cand_loc = MagicMock()
    cand_loc.count.return_value = 1

    form_loc = MagicMock()
    form_loc.count.return_value = 1
    form_loc.locator.return_value = cand_loc

    def dynamic_locator(selector):
        if "challenges.cloudflare" in selector or "challenge-stage" in selector:
            return chal_loc
        elif "skeleton" in selector or "shimmer" in selector or "loading" in selector or "spinner" in selector:
            return skel_loc
        elif "form" in selector:
            return form_loc
        elif "button" in selector:
            return cand_loc
        else:
            default_loc = MagicMock()
            default_loc.count.return_value = 0
            default_loc.locator.return_value = default_loc
            return default_loc

    def on_wait(ms):
        nonlocal poll_step
        poll_step += 1

    mock_page.locator.side_effect = dynamic_locator
    mock_page.wait_for_timeout.side_effect = on_wait

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    code = run_inspection(
        url="https://unstop.com/competitions/123/register",
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
        hydration_timeout_ms=2000,
        poll_interval_ms=50,
    )
    assert code == 3
    assert not out_file.exists()


def test_m2b1_h_foreign_redirect_during_polling(tmp_path):
    """Test H: Redirect to foreign origin during polling aborts with code 5 and writes no artifact."""
    out_file = tmp_path / "out.json"
    mock_page = _DynamicUrlMockPage(
        initial_url="https://unstop.com/competitions/123/register",
        shifted_url="https://malicious.example.com/stolen",
        switch_poll=1,
    )

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    code = run_inspection(
        url="https://unstop.com/competitions/123/register",
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
        hydration_timeout_ms=2000,
        poll_interval_ms=50,
    )

    assert code == 5
    assert not out_file.exists()


def test_m2b1_i_probe_exception_aborts_with_code_9(tmp_path, caplog):
    """Test I: Probe exception aborts with bounded code 9 without leaking raw exception in logs or writing artifact."""
    out_file = tmp_path / "out.json"
    mock_page = MagicMock()
    mock_page.url = "https://unstop.com/competitions/123/register"

    chal_loc = MagicMock()
    chal_loc.count.return_value = 0

    def dynamic_locator(selector):
        if "challenges.cloudflare" in selector or "challenge-stage" in selector:
            return chal_loc
        elif "skeleton" in selector or "shimmer" in selector or "loading" in selector or "spinner" in selector:
            # Readiness probe raises exception with sensitive fragment
            raise RuntimeError("DOM_INTERNAL_DB_SECRET_XYZ123")
        elif "form" in selector:
            loc = MagicMock()
            loc.count.return_value = 1
            loc.locator.return_value.count.return_value = 1
            return loc
        elif "button" in selector:
            loc = MagicMock()
            loc.count.return_value = 1
            return loc
        else:
            return MagicMock()

    mock_page.locator.side_effect = dynamic_locator

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    with caplog.at_level(logging.DEBUG):
        code = run_inspection(
            url="https://unstop.com/competitions/123/register",
            output_path=out_file,
            acknowledge_flag=True,
            playwright_instance=mock_pw,
            hydration_timeout_ms=1000,
            poll_interval_ms=50,
        )

    assert code == 9
    assert not out_file.exists()
    assert "DOM_INTERNAL_DB_SECRET_XYZ123" not in caplog.text
    assert "SAFETY ABORT: Readiness probe failed." in caplog.text


def test_m2b1_j_runtime_mutation_instrumentation_throughout_wait(tmp_path):
    """Test J: Zero mutation calls (click, fill, type, press, upload, submit, dispatch) throughout wait and inspection."""
    mutating_methods = [
        "click", "dblclick", "press", "type", "fill", "check",
        "uncheck", "select_option", "set_input_files", "dispatch_event",
        "drag_to", "submit",
    ]

    mock_page = _make_hydrated_mock_page(cand_count=1)

    for m in mutating_methods:
        setattr(mock_page, m, MagicMock(side_effect=AssertionError(f"FORBIDDEN MUTATION: page.{m}() called!")))
        setattr(mock_page.locator.return_value, m, MagicMock(side_effect=AssertionError(f"FORBIDDEN MUTATION: locator.{m}() called!")))

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
        hydration_timeout_ms=1000,
        poll_interval_ms=50,
    )
    assert code == 0
    assert out_file.exists()

    for m in mutating_methods:
        assert getattr(mock_page, m).call_count == 0


def test_m2b1_k_timeout_argument_validation():
    """Test K: Validate --hydration-timeout-ms rejects negative, zero, non-integer, and > 30000 values."""
    # Valid values
    assert validate_hydration_timeout(1000) == 1000
    assert validate_hydration_timeout(15000) == 15000
    assert validate_hydration_timeout(30000) == 30000
    assert validate_hydration_timeout("15000") == 15000

    # Below minimum
    with pytest.raises(ValueError):
        validate_hydration_timeout(999)

    with pytest.raises(ValueError):
        validate_hydration_timeout(0)

    with pytest.raises(ValueError):
        validate_hydration_timeout(-500)

    # Above maximum
    with pytest.raises(ValueError):
        validate_hydration_timeout(30001)

    with pytest.raises(ValueError):
        validate_hydration_timeout(100000)

    # Non-integer / boolean
    with pytest.raises(ValueError):
        validate_hydration_timeout("not_a_number")

    with pytest.raises(ValueError):
        validate_hydration_timeout(True)

    with pytest.raises(ValueError):
        validate_hydration_timeout(False)


def test_m2b1_k_cli_timeout_argument_aborts_before_browser_launch(tmp_path):
    """Test K CLI: Invalid CLI --hydration-timeout-ms returns code 2 before browser launch."""
    out_file = tmp_path / "out.json"
    mock_pw = MagicMock()

    code = run_inspection(
        url="https://unstop.com/competitions/123/register",
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
        hydration_timeout_ms=50000,  # invalid: > 30000
    )
    assert code == 2
    mock_pw.chromium.launch.assert_not_called()
    assert not out_file.exists()


# ── 5. Detailed M2B.1 Correction Tests ───────────────────────────────────────

def test_stable_header_button_while_application_form_mounts_later(tmp_path):
    """Stable header button present from start; application form mounts later; waits safely and succeeds."""
    out_file = tmp_path / "out.json"
    target_url = "https://unstop.com/competitions/123/register"
    poll_step = 0
    mock_handle = _make_mock_control_handle(text="Apply Now")

    mock_page = MagicMock()
    mock_page.url = target_url

    chal_loc = MagicMock()
    chal_loc.count.return_value = 0
    skel_loc = MagicMock()
    skel_loc.count.return_value = 0

    cand_loc = MagicMock()
    cand_loc.count.side_effect = lambda: 0 if poll_step < 2 else 1

    form_loc = MagicMock()
    form_loc.count.side_effect = lambda: 0 if poll_step < 2 else 1
    form_loc.locator.return_value = cand_loc

    def dynamic_locator(selector):
        if "challenges.cloudflare" in selector or "challenge-stage" in selector:
            return chal_loc
        elif "skeleton" in selector or "shimmer" in selector or "loading" in selector or "spinner" in selector:
            return skel_loc
        elif "form" in selector:
            return form_loc
        elif "button" in selector:
            return cand_loc
        return MagicMock()

    def on_wait(ms):
        nonlocal poll_step
        poll_step += 1

    mock_page.locator.side_effect = dynamic_locator
    mock_page.wait_for_timeout.side_effect = on_wait
    mock_page.query_selector_all.return_value = [mock_handle]

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    code = run_inspection(
        url=target_url,
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
        hydration_timeout_ms=5000,
        poll_interval_ms=50,
    )
    assert code == 0
    assert out_file.exists()
    assert poll_step >= 4


def test_permanent_header_button_with_no_application_form_times_out(tmp_path):
    """Permanent header button present but application form never mounts; detector times out with code 8."""
    out_file = tmp_path / "out.json"
    mock_page = MagicMock()
    mock_page.url = "https://unstop.com/competitions/123/register"

    chal_loc = MagicMock()
    chal_loc.count.return_value = 0
    skel_loc = MagicMock()
    skel_loc.count.return_value = 0

    cand_loc = MagicMock()
    cand_loc.count.return_value = 0

    form_loc = MagicMock()
    form_loc.count.return_value = 0
    form_loc.locator.return_value = cand_loc

    def dynamic_locator(selector):
        if "challenges.cloudflare" in selector or "challenge-stage" in selector:
            return chal_loc
        elif "skeleton" in selector or "shimmer" in selector or "loading" in selector or "spinner" in selector:
            return skel_loc
        elif "form" in selector:
            return form_loc
        elif "button" in selector:
            header_btn_loc = MagicMock()
            header_btn_loc.count.return_value = 1
            return header_btn_loc
        return MagicMock()

    mock_page.locator.side_effect = dynamic_locator

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    code = run_inspection(
        url="https://unstop.com/competitions/123/register",
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
        hydration_timeout_ms=1000,
        poll_interval_ms=100,
    )
    assert code == 8
    assert not out_file.exists()


def test_role_button_implemented_as_div_inside_application_form(tmp_path):
    """[role=button] implemented as div inside application form is detected via shared selector."""
    out_file = tmp_path / "out.json"
    div_handle = _make_mock_control_handle(
        text="Submit Application",
        tag_name="div",
        role="button",
    )
    mock_page = _make_hydrated_mock_page(cand_count=1, form_count=1, handles=[div_handle])

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    code = run_inspection(
        url="https://unstop.com/competitions/123/register",
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
        hydration_timeout_ms=1000,
        poll_interval_ms=50,
    )
    assert code == 0
    assert out_file.exists()
    content = json.loads(out_file.read_text(encoding="utf-8"))
    assert content["candidate_count"] == 1
    assert content["candidates"][0]["role"] == "button"
    assert content["candidates"][0]["tag_name"].startswith("tag_")


def test_hidden_persistent_spinner_does_not_block_readiness(tmp_path):
    """Persistent spinner with is_visible() == False does not block readiness detector."""
    out_file = tmp_path / "out.json"
    mock_page = _make_hydrated_mock_page(
        cand_count=1,
        form_count=1,
        skeleton_count=1,
        skeleton_visible=False,
    )

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    code = run_inspection(
        url="https://unstop.com/competitions/123/register",
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
        hydration_timeout_ms=1000,
        poll_interval_ms=50,
    )
    assert code == 0
    assert out_file.exists()


def test_visible_spinner_does_block_readiness(tmp_path):
    """Persistent spinner with is_visible() == True blocks readiness detector and causes timeout."""
    out_file = tmp_path / "out.json"
    mock_page = _make_hydrated_mock_page(
        cand_count=1,
        form_count=1,
        skeleton_count=1,
        skeleton_visible=True,
    )

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    code = run_inspection(
        url="https://unstop.com/competitions/123/register",
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
        hydration_timeout_ms=1000,
        poll_interval_ms=50,
    )
    assert code == 8
    assert not out_file.exists()


def test_missing_skeleton_form_candidate_challenge_count(tmp_path):
    """Missing count method on skeleton, form, candidate, or challenge locators aborts with code 9."""
    out_file = tmp_path / "out.json"

    for missing_target in ("skeleton", "form", "candidate", "challenge"):
        mock_page = _make_hydrated_mock_page(cand_count=1, form_count=1)

        if missing_target == "skeleton":
            skel = MagicMock(spec=[])  # no count method
            mock_page.locator.side_effect = lambda sel: skel if "skeleton" in sel or "loading" in sel or "spinner" in sel else _make_hydrated_mock_page().locator(sel)
        elif missing_target == "form":
            frm = MagicMock(spec=[])
            mock_page.locator.side_effect = lambda sel: frm if "form" in sel else _make_hydrated_mock_page().locator(sel)
        elif missing_target == "candidate":
            cand = MagicMock(spec=[])
            frm = MagicMock()
            frm.count.return_value = 1
            frm.locator.return_value = cand
            mock_page.locator.side_effect = lambda sel: frm if "form" in sel else _make_hydrated_mock_page().locator(sel)
        elif missing_target == "challenge":
            chal = MagicMock(spec=[])
            mock_page.locator.side_effect = lambda sel: chal if "challenges.cloudflare" in sel else _make_hydrated_mock_page().locator(sel)

        mock_browser = MagicMock()
        mock_browser.new_page.return_value = mock_page
        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser

        code = run_inspection(
            url="https://unstop.com/competitions/123/register",
            output_path=out_file,
            acknowledge_flag=True,
            playwright_instance=mock_pw,
            hydration_timeout_ms=1000,
            poll_interval_ms=50,
        )
        assert code == 9, f"Expected code 9 for missing {missing_target} count, got {code}"
        assert not out_file.exists()


def test_missing_nth_and_is_visible_for_skeleton_visibility(tmp_path):
    """Missing nth, missing is_visible, or non-boolean visibility on skeleton aborts with code 9."""
    out_file = tmp_path / "out.json"

    # Case 1: missing nth
    mock_page_1 = _make_hydrated_mock_page(skeleton_count=1)
    skel_1 = MagicMock(spec=["count"])
    skel_1.count.return_value = 1
    mock_page_1.locator.side_effect = lambda sel: skel_1 if "skeleton" in sel or "loading" in sel or "spinner" in sel else _make_hydrated_mock_page().locator(sel)

    mock_pw_1 = MagicMock()
    mock_pw_1.chromium.launch.return_value.new_page.return_value = mock_page_1
    assert run_inspection(url="https://unstop.com/competitions/123/register", output_path=out_file, acknowledge_flag=True, playwright_instance=mock_pw_1, hydration_timeout_ms=1000, poll_interval_ms=50) == 9

    # Case 2: missing is_visible on item
    mock_page_2 = _make_hydrated_mock_page(skeleton_count=1)
    skel_2 = MagicMock()
    skel_2.count.return_value = 1
    skel_2.nth.return_value = MagicMock(spec=[])  # no is_visible
    mock_page_2.locator.side_effect = lambda sel: skel_2 if "skeleton" in sel or "loading" in sel or "spinner" in sel else _make_hydrated_mock_page().locator(sel)

    mock_pw_2 = MagicMock()
    mock_pw_2.chromium.launch.return_value.new_page.return_value = mock_page_2
    assert run_inspection(url="https://unstop.com/competitions/123/register", output_path=out_file, acknowledge_flag=True, playwright_instance=mock_pw_2, hydration_timeout_ms=1000, poll_interval_ms=50) == 9

    # Case 3: non-boolean visibility
    mock_page_3 = _make_hydrated_mock_page(skeleton_count=1)
    skel_3 = MagicMock()
    skel_3.count.return_value = 1
    skel_3.nth.return_value.is_visible.return_value = "true"  # string, not bool
    mock_page_3.locator.side_effect = lambda sel: skel_3 if "skeleton" in sel or "loading" in sel or "spinner" in sel else _make_hydrated_mock_page().locator(sel)

    mock_pw_3 = MagicMock()
    mock_pw_3.chromium.launch.return_value.new_page.return_value = mock_page_3
    assert run_inspection(url="https://unstop.com/competitions/123/register", output_path=out_file, acknowledge_flag=True, playwright_instance=mock_pw_3, hydration_timeout_ms=1000, poll_interval_ms=50) == 9


def test_negative_and_boolean_counts_abort_with_probe_error(tmp_path):
    """Boolean counts (True/False) or negative counts return code 9."""
    out_file = tmp_path / "out.json"

    for bad_val in (True, False, -1, -5):
        mock_page = _make_hydrated_mock_page()
        mock_form = MagicMock()
        mock_form.count.return_value = bad_val
        mock_form.locator.return_value.count.return_value = 1
        mock_page.locator.side_effect = lambda sel: mock_form if "form" in sel else _make_hydrated_mock_page().locator(sel)

        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value.new_page.return_value = mock_page

        code = run_inspection(
            url="https://unstop.com/competitions/123/register",
            output_path=out_file,
            acknowledge_flag=True,
            playwright_instance=mock_pw,
            hydration_timeout_ms=1000,
            poll_interval_ms=50,
        )
        assert code == 9, f"Expected code 9 for count={bad_val}, got {code}"


def test_malformed_or_missing_readiness_detail_fields():
    """wait_for_hydration_readiness aborts with PROBE_ERROR if details dict is missing fields or has bad types."""
    mock_page = MagicMock()
    mock_page.url = "https://unstop.com/competitions/123/register"

    # Missing field: "form_count" missing
    with patch("scripts.inspect_unstop_submission_controls.probe_hydration_state", return_value=(HydrationStatus.READY_FOR_INSPECTION, {"skeleton_count": 0, "candidate_count": 1})):
        status, _, _ = wait_for_hydration_readiness(mock_page, timeout_ms=1000, poll_interval_ms=10, allow_test_hosts=True)
        assert status == HydrationStatus.PROBE_ERROR

    # Boolean in details
    with patch("scripts.inspect_unstop_submission_controls.probe_hydration_state", return_value=(HydrationStatus.READY_FOR_INSPECTION, {"skeleton_count": 0, "form_count": 1, "candidate_count": True})):
        status, _, _ = wait_for_hydration_readiness(mock_page, timeout_ms=1000, poll_interval_ms=10, allow_test_hosts=True)
        assert status == HydrationStatus.PROBE_ERROR

    # Negative count in details
    with patch("scripts.inspect_unstop_submission_controls.probe_hydration_state", return_value=(HydrationStatus.READY_FOR_INSPECTION, {"skeleton_count": -1, "form_count": 1, "candidate_count": 1})):
        status, _, _ = wait_for_hydration_readiness(mock_page, timeout_ms=1000, poll_interval_ms=10, allow_test_hosts=True)
        assert status == HydrationStatus.PROBE_ERROR

    # Non-dict details
    with patch("scripts.inspect_unstop_submission_controls.probe_hydration_state", return_value=(HydrationStatus.READY_FOR_INSPECTION, "not a dict")):
        status, _, _ = wait_for_hydration_readiness(mock_page, timeout_ms=1000, poll_interval_ms=10, allow_test_hosts=True)
        assert status == HydrationStatus.PROBE_ERROR


def test_post_readiness_challenge_count_method_missing_aborts_with_code_9(tmp_path):
    """Post-readiness challenge probe missing count method aborts with code 9 without writing artifact."""
    out_file = tmp_path / "out.json"
    mock_page = _make_hydrated_mock_page(cand_count=1, form_count=1)

    chal_probe_count = 0
    normal_loc = mock_page.locator.side_effect

    def dynamic_loc(sel):
        nonlocal chal_probe_count
        if "challenges.cloudflare" in sel:
            chal_probe_count += 1
            if chal_probe_count >= 4:
                chal = MagicMock(spec=[])  # no count method
                return chal
        return normal_loc(sel)

    mock_page.locator.side_effect = dynamic_loc

    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value.new_page.return_value = mock_page

    code = run_inspection(
        url="https://unstop.com/competitions/123/register",
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
        hydration_timeout_ms=2000,
        poll_interval_ms=10,
    )
    assert code == 9
    assert not out_file.exists()


def test_challenge_count_exception_returns_code_9_probe_error(tmp_path):
    """Challenge count raising an exception returns code 9 (PROBE_ERROR), not code 3."""
    out_file = tmp_path / "out.json"
    mock_page = _make_hydrated_mock_page(cand_count=1, form_count=1)

    chal = MagicMock()
    chal.count.side_effect = RuntimeError("DOM disconnected during challenge probe")

    orig_loc = mock_page.locator.side_effect
    mock_page.locator.side_effect = lambda sel: chal if "challenges.cloudflare" in sel else orig_loc(sel)

    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value.new_page.return_value = mock_page

    code = run_inspection(
        url="https://unstop.com/competitions/123/register",
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
        hydration_timeout_ms=1000,
        poll_interval_ms=50,
    )
    assert code == 9
    assert not out_file.exists()


def test_strict_timeout_values_rejected_before_browser_launch(tmp_path, caplog):
    """Strict timeout validation rejects 1000.5, '1e3', Decimal('1000'), True, and arbitrary objects before browser launch."""
    out_file = tmp_path / "out.json"
    mock_pw = MagicMock()

    class IntLike:
        def __int__(self):
            return 1000

    invalid_values = [
        (1000.5, "INVALID_TIMEOUT_TYPE"),
        ("1e3", "INVALID_TIMEOUT_FORMAT"),
        (Decimal("1000"), "INVALID_TIMEOUT_TYPE"),
        (True, "INVALID_TIMEOUT_TYPE"),
        (False, "INVALID_TIMEOUT_TYPE"),
        ("-1000", "INVALID_TIMEOUT_FORMAT"),
        ("+1000", "INVALID_TIMEOUT_FORMAT"),
        (" 1000 ", "INVALID_TIMEOUT_FORMAT"),
        (IntLike(), "INVALID_TIMEOUT_TYPE"),
        (999, "TIMEOUT_OUT_OF_BOUNDS"),
        (30001, "TIMEOUT_OUT_OF_BOUNDS"),
    ]

    for val, expected_reason in invalid_values:
        with pytest.raises(ValueError) as excinfo:
            validate_hydration_timeout(val)
        assert str(excinfo.value) == expected_reason

        with caplog.at_level(logging.ERROR):
            code = run_inspection(
                url="https://unstop.com/competitions/123/register",
                output_path=out_file,
                acknowledge_flag=True,
                playwright_instance=mock_pw,
                hydration_timeout_ms=val,
            )
        assert code == 2
        mock_pw.chromium.launch.assert_not_called()
        assert not out_file.exists()
        assert f"SAFETY ABORT: Hydration timeout validation failed: {expected_reason}" in caplog.text
        if expected_reason != "TIMEOUT_OUT_OF_BOUNDS" and not isinstance(val, (int, str)):
            assert repr(val) not in caplog.text


# ── 6. New Required M2B.1 Correction Tests ───────────────────────────────────

def test_newsletter_form_present_before_application_form_waits_correctly(tmp_path):
    """Blocker 1: A newsletter form mounted before application form must NOT satisfy readiness.

    The readiness detector must exclude newsletter/subscribe forms from PLAUSIBLE_APPLICATION_FORM_SELECTOR.
    The mock simulates: initially only a newsletter form (form_count=0 from the plausible selector,
    because the newsletter form is excluded), then the application form mounts on poll >= 2.
    """
    out_file = tmp_path / "out.json"
    target_url = "https://unstop.com/competitions/123/register"
    poll_step = 0
    mock_handle = _make_mock_control_handle(text="Submit Application")

    mock_page = MagicMock()
    mock_page.url = target_url

    chal_loc = MagicMock()
    chal_loc.count.return_value = 0
    skel_loc = MagicMock()
    skel_loc.count.return_value = 0

    # Plausible application form: only present after poll >= 2 (newsletter form excluded by selector).
    cand_loc = MagicMock()
    cand_loc.count.side_effect = lambda: 0 if poll_step < 2 else 1

    form_loc = MagicMock()
    form_loc.count.side_effect = lambda: 0 if poll_step < 2 else 1
    form_loc.locator.return_value = cand_loc

    def dynamic_locator(selector):
        if "challenges.cloudflare" in selector or "challenge-stage" in selector:
            return chal_loc
        elif "skeleton" in selector or "shimmer" in selector or "loading" in selector or "spinner" in selector:
            return skel_loc
        elif "form" in selector or "role='form'" in selector:
            # The PLAUSIBLE_APPLICATION_FORM_SELECTOR excludes newsletter/subscribe forms;
            # this mock returns form_count=0 while only newsletter form is present.
            return form_loc
        return MagicMock()

    def on_wait(ms):
        nonlocal poll_step
        poll_step += 1

    mock_page.locator.side_effect = dynamic_locator
    mock_page.wait_for_timeout.side_effect = on_wait
    mock_page.query_selector_all.return_value = [mock_handle]

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    code = run_inspection(
        url=target_url,
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
        hydration_timeout_ms=5000,
        poll_interval_ms=50,
    )
    assert code == 0
    assert out_file.exists()
    # Must have waited beyond poll_step=0 (newsletter-only phase)
    assert poll_step >= 2, f"Expected at least 2 polls to mount application form, got {poll_step}"


def test_newsletter_only_page_times_out_with_code_8(tmp_path):
    """Blocker 1: A page with only newsletter/subscribe forms, never mounting an application form, times out.

    The PLAUSIBLE_APPLICATION_FORM_SELECTOR must exclude these forms;
    form_count must remain 0 throughout, causing a timeout with code 8.
    """
    out_file = tmp_path / "out.json"
    mock_page = MagicMock()
    mock_page.url = "https://unstop.com/competitions/123/register"

    chal_loc = MagicMock()
    chal_loc.count.return_value = 0
    skel_loc = MagicMock()
    skel_loc.count.return_value = 0

    # Newsletter form is excluded by PLAUSIBLE_APPLICATION_FORM_SELECTOR;
    # mock returns form_count=0 for all plausible-form selector probes.
    plausible_form_loc = MagicMock()
    plausible_form_loc.count.return_value = 0
    plausible_form_loc.locator.return_value.count.return_value = 0

    def dynamic_locator(selector):
        if "challenges.cloudflare" in selector or "challenge-stage" in selector:
            return chal_loc
        elif "skeleton" in selector or "shimmer" in selector or "loading" in selector or "spinner" in selector:
            return skel_loc
        elif "form" in selector or "role='form'" in selector:
            return plausible_form_loc
        return MagicMock()

    mock_page.locator.side_effect = dynamic_locator

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    code = run_inspection(
        url="https://unstop.com/competitions/123/register",
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
        hydration_timeout_ms=1000,
        poll_interval_ms=100,
    )
    assert code == 8
    assert not out_file.exists()


def test_form_disappears_after_readiness_aborts_with_no_artifact(tmp_path):
    """Blocker 2: Application form/controls disappear between readiness declaration and pre-inspection re-probe.

    Strategy: patch probe_hydration_state directly to control exactly when the mutation occurs.
    During readiness polling, return READY_FOR_INSPECTION with valid counts to accumulate
    REQUIRED_STABLE_POLLS=3 stable polls. Then on the NEXT call (the pre-inspection re-probe),
    return READY_FOR_INSPECTION but with form_count=0 so the re-probe logic detects lost readiness
    and aborts with a nonzero exit code, writing no artifact.
    """
    from scripts.inspect_unstop_submission_controls import REQUIRED_STABLE_POLLS

    out_file = tmp_path / "out.json"
    target_url = "https://unstop.com/competitions/123/register"
    mock_handle = _make_mock_control_handle(text="Submit Application")

    mock_page = _make_hydrated_mock_page(url=target_url, cand_count=1, form_count=1)
    mock_page.query_selector_all.return_value = [mock_handle]

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    probe_call_count = 0
    # The wait loop needs exactly REQUIRED_STABLE_POLLS=3 consecutive stable probes to declare readiness.
    # The pre-inspection re-probe is the very next (4th) call. Setting PROBES_BEFORE_MUTATION=3
    # means calls 1,2,3 return form_count=1 (readiness loop terminates), and call 4+ returns form_count=0.
    PROBES_BEFORE_MUTATION = REQUIRED_STABLE_POLLS

    def patched_probe(page, allow_test_hosts=False):
        nonlocal probe_call_count
        probe_call_count += 1
        if probe_call_count > PROBES_BEFORE_MUTATION:
            # Simulate form disappearing: ready status but form_count = 0
            return HydrationStatus.READY_FOR_INSPECTION, {
                "skeleton_count": 0,
                "form_count": 0,
                "candidate_count": 0,
            }
        return HydrationStatus.READY_FOR_INSPECTION, {
            "skeleton_count": 0,
            "form_count": 1,
            "candidate_count": 1,
        }

    with patch(
        "scripts.inspect_unstop_submission_controls.probe_hydration_state",
        side_effect=patched_probe,
    ):
        code = run_inspection(
            url=target_url,
            output_path=out_file,
            acknowledge_flag=True,
            playwright_instance=mock_pw,
            hydration_timeout_ms=5000,
            poll_interval_ms=10,
        )

    assert code != 0, f"Expected nonzero exit when form disappears after readiness, got {code}"
    assert not out_file.exists(), "No artifact must be written when readiness is lost before inspection"


def test_empty_inspect_submission_controls_produces_no_success_artifact(tmp_path):
    """Blocker 2 (empty result): inspect_submission_controls() returning [] must not produce a success artifact.

    Structural readiness passes (form + candidate counts are positive), but the
    actual handle-level inspection returns an empty list. The script must abort with
    a nonzero exit code and write no artifact.
    """
    out_file = tmp_path / "out.json"
    target_url = "https://unstop.com/competitions/123/register"

    mock_page = _make_hydrated_mock_page(url=target_url, cand_count=1, form_count=1)
    # Override query_selector_all to return empty list (no element handles found)
    mock_page.query_selector_all.return_value = []

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    code = run_inspection(
        url=target_url,
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
        hydration_timeout_ms=2000,
        poll_interval_ms=50,
    )
    assert code != 0, "Empty inspect_submission_controls() must not produce exit code 0"
    assert not out_file.exists(), "No artifact must be written when inspection returns empty candidates"


def test_skeleton_count_above_cap_returns_probe_error_without_iteration(tmp_path):
    """Blocker 3: A skeleton count above MAX_SKELETON_COUNT returns code 9 without iterating over indicators.

    The mock returns raw_skel_count = MAX_SKELETON_COUNT + 1. The code must detect this,
    return PROBE_ERROR immediately, and must NOT call .nth() (i.e., no iteration).
    """
    out_file = tmp_path / "out.json"
    mock_page = MagicMock()
    mock_page.url = "https://unstop.com/competitions/123/register"

    chal_loc = MagicMock()
    chal_loc.count.return_value = 0

    skel_loc = MagicMock()
    skel_loc.count.return_value = MAX_SKELETON_COUNT + 1  # pathological count above cap
    # .nth() must never be called when count exceeds the cap:
    skel_loc.nth.side_effect = AssertionError("nth() called despite count exceeding cap — unbounded iteration!")

    cand_loc = MagicMock()
    cand_loc.count.return_value = 1

    form_loc = MagicMock()
    form_loc.count.return_value = 1
    form_loc.locator.return_value = cand_loc

    def dynamic_locator(selector):
        if "challenges.cloudflare" in selector or "challenge-stage" in selector:
            return chal_loc
        elif "skeleton" in selector or "shimmer" in selector or "loading" in selector or "spinner" in selector:
            return skel_loc
        elif "form" in selector or "role='form'" in selector:
            return form_loc
        return MagicMock()

    mock_page.locator.side_effect = dynamic_locator

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    code = run_inspection(
        url="https://unstop.com/competitions/123/register",
        output_path=out_file,
        acknowledge_flag=True,
        playwright_instance=mock_pw,
        hydration_timeout_ms=1000,
        poll_interval_ms=50,
    )
    assert code == 9, f"Expected code 9 for skeleton count above cap, got {code}"
    assert not out_file.exists()
    # Confirm .nth() was never called (no unbounded iteration)
    skel_loc.nth.assert_not_called()


def test_validate_readiness_details_truth_table():
    """Unit tests for validate_readiness_details enforcing strict type and value constraints."""
    # Ready state: skeleton == 0, form >= 1, cand >= 1
    ready, counts = validate_readiness_details({"skeleton_count": 0, "form_count": 1, "candidate_count": 1})
    assert ready is True
    assert counts == (0, 1, 1)

    # Not ready: skeleton > 0
    ready, counts = validate_readiness_details({"skeleton_count": 1, "form_count": 1, "candidate_count": 1})
    assert ready is False
    assert counts == (1, 1, 1)

    # Not ready: form == 0
    ready, counts = validate_readiness_details({"skeleton_count": 0, "form_count": 0, "candidate_count": 1})
    assert ready is False
    assert counts == (0, 0, 1)

    # Not ready: cand == 0
    ready, counts = validate_readiness_details({"skeleton_count": 0, "form_count": 1, "candidate_count": 0})
    assert ready is False
    assert counts == (0, 1, 0)

    # Malformed: non-dict
    with pytest.raises(ValueError, match="details_not_a_dict"):
        validate_readiness_details(None)
    with pytest.raises(ValueError, match="details_not_a_dict"):
        validate_readiness_details("ready")

    # Malformed: missing fields
    with pytest.raises(ValueError, match="missing_field_candidate_count"):
        validate_readiness_details({"skeleton_count": 0, "form_count": 1})

    # Malformed: boolean values (bool is a subclass of int in Python)
    with pytest.raises(ValueError, match="invalid_count_skeleton_count"):
        validate_readiness_details({"skeleton_count": False, "form_count": 1, "candidate_count": 1})
    with pytest.raises(ValueError, match="invalid_count_form_count"):
        validate_readiness_details({"skeleton_count": 0, "form_count": True, "candidate_count": 1})

    # Malformed: negative integers
    with pytest.raises(ValueError, match="invalid_count_candidate_count"):
        validate_readiness_details({"skeleton_count": 0, "form_count": 1, "candidate_count": -1})

    # Malformed: string/float counts
    with pytest.raises(ValueError, match="invalid_count_form_count"):
        validate_readiness_details({"skeleton_count": 0, "form_count": "1", "candidate_count": 1})


def test_skeleton_reappears_during_pre_inspection_reprobe_aborts_with_no_artifact(tmp_path):
    """Blocker 1: Visible skeleton reappears between readiness declaration and pre-inspection re-probe.

    Strategy: patch probe_hydration_state so that polls 1..REQUIRED_STABLE_POLLS return
    skeleton_count=0 (stable readiness achieved). On the 4th call (pre-inspection re-probe),
    return skeleton_count=1 (a loading skeleton reappeared).
    The re-probe must invoke validate_readiness_details, detect that is_ready is False,
    abort with exit code 8, and write NO artifact.
    """
    from scripts.inspect_unstop_submission_controls import REQUIRED_STABLE_POLLS

    out_file = tmp_path / "out.json"
    target_url = "https://unstop.com/competitions/123/register"
    mock_handle = _make_mock_control_handle(text="Submit Application")

    mock_page = _make_hydrated_mock_page(url=target_url, cand_count=1, form_count=1)
    mock_page.query_selector_all.return_value = [mock_handle]

    mock_browser = MagicMock()
    mock_browser.new_page.return_value = mock_page
    mock_pw = MagicMock()
    mock_pw.chromium.launch.return_value = mock_browser

    probe_call_count = 0
    PROBES_BEFORE_MUTATION = REQUIRED_STABLE_POLLS

    def patched_probe(page, allow_test_hosts=False):
        nonlocal probe_call_count
        probe_call_count += 1
        if probe_call_count > PROBES_BEFORE_MUTATION:
            # Simulate skeleton reappearing: ready status but skeleton_count = 1
            return HydrationStatus.READY_FOR_INSPECTION, {
                "skeleton_count": 1,
                "form_count": 1,
                "candidate_count": 1,
            }
        return HydrationStatus.READY_FOR_INSPECTION, {
            "skeleton_count": 0,
            "form_count": 1,
            "candidate_count": 1,
        }

    with patch(
        "scripts.inspect_unstop_submission_controls.probe_hydration_state",
        side_effect=patched_probe,
    ):
        code = run_inspection(
            url=target_url,
            output_path=out_file,
            acknowledge_flag=True,
            playwright_instance=mock_pw,
            hydration_timeout_ms=5000,
            poll_interval_ms=10,
        )

    assert code != 0, f"Expected nonzero exit when skeleton reappears before inspection, got {code}"
    assert code == 8, f"Expected exit code 8 when readiness is lost before inspection, got {code}"
    assert not out_file.exists(), "No artifact must be written when skeleton reappears before inspection"


def test_offline_real_playwright_dom_newsletter_exclusion_and_delayed_readiness():
    """Real Playwright DOM test verifying case-insensitive newsletter exclusion and delayed hydration.

    Safety: completely offline; route '**/*' fulfills locally; zero external network requests.
    Verifies:
    1. Lowercase newsletter form is excluded.
    2. Mixed-case newsletter/subscription form is excluded.
    3. Header and search forms are excluded.
    4. Header/newsletter buttons never contribute to form-scoped candidate count.
    5. Newsletter-only page times out (HYDRATION_TIMEOUT).
    6. Delayed real application-form insertion eventually achieves stable readiness.
    """
    from playwright.sync_api import sync_playwright
    from scripts.inspect_unstop_submission_controls import REQUIRED_STABLE_POLLS

    newsletter_html = """<!DOCTYPE html>
<html>
<head><title>Offline Registration</title></head>
<body>
  <header>
    <form id="header-search" role="search" action="/Search">
      <input type="search" name="q" />
      <button type="submit" id="search-btn">Search</button>
    </form>
    <button id="nav-btn">Menu</button>
  </header>
  <nav>
    <form id="nav-menu" role="navigation">
      <button type="button">Home</button>
    </form>
  </nav>
  <!-- Lowercase newsletter form -->
  <form id="newsletter-signup" class="newsletter-box" action="/subscribe">
    <input type="email" name="email" />
    <button type="submit" id="sub-btn-1">Subscribe Lowercase</button>
  </form>
  <!-- Mixed-case newsletter form -->
  <form id="NewsletterForm" class="SubscribeForm" action="/Newsletter-Submit">
    <input type="text" name="mail" />
    <button type="submit" id="sub-btn-2">Sign Up MixedCase</button>
  </form>
  <!-- Mixed-case role=form newsletter -->
  <div role="form" id="RoleNewsletter" class="NewsletterRole" action="/Subscribe-Now">
    <button type="button" id="role-sub-btn">Role Subscribe</button>
  </div>
</body>
</html>
"""

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        # Ensure strictly offline local execution
        page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html", body=newsletter_html))
        page.goto("https://unstop.com/competitions/123/register")

        # 1. Lowercase newsletter form excluded
        assert page.locator("#newsletter-signup").count() == 1
        assert page.locator(f"#newsletter-signup{_APP_FORM_EXCLUSIONS}").count() == 0

        # 2. Mixed-case newsletter forms excluded
        assert page.locator("#NewsletterForm").count() == 1
        assert page.locator(f"#NewsletterForm{_APP_FORM_EXCLUSIONS}").count() == 0
        assert page.locator("#RoleNewsletter").count() == 1
        assert page.locator(f"#RoleNewsletter{_APP_ROLE_FORM_EXCLUSIONS}").count() == 0

        # 3. Overall plausible application forms count is 0
        assert page.locator(PLAUSIBLE_APPLICATION_FORM_SELECTOR).count() == 0

        # 4. Header / newsletter buttons never contribute to form-scoped candidate count
        all_buttons = page.locator("button").count()
        assert all_buttons >= 5
        form_scoped_candidates = page.locator(PLAUSIBLE_APPLICATION_FORM_SELECTOR).locator(CANDIDATE_CONTROL_SELECTOR).count()
        assert form_scoped_candidates == 0

        # 5. probe_hydration_state returns form_count=0, candidate_count=0
        status, details = probe_hydration_state(page)
        assert status == HydrationStatus.READY_FOR_INSPECTION
        assert details["skeleton_count"] == 0
        assert details["form_count"] == 0
        assert details["candidate_count"] == 0

        # 6. Newsletter-only page times out
        status, elapsed, stable = wait_for_hydration_readiness(
            page, timeout_ms=300, poll_interval_ms=50
        )
        assert status == HydrationStatus.HYDRATION_TIMEOUT
        assert stable == 0

        # 7. Delayed real application-form insertion eventually achieves stable readiness
        page.evaluate("""
            const appForm = document.createElement('form');
            appForm.id = 'application-form';
            appForm.setAttribute('action', '/competitions/123/apply');
            appForm.innerHTML = '<input type="text" name="fullname" /><button type="submit" id="submit-application-btn">Submit Application</button>';
            document.body.appendChild(appForm);
        """)

        assert page.locator(PLAUSIBLE_APPLICATION_FORM_SELECTOR).count() == 1
        new_form_candidates = page.locator(PLAUSIBLE_APPLICATION_FORM_SELECTOR).locator(CANDIDATE_CONTROL_SELECTOR).count()
        assert new_form_candidates == 1

        status_after, elapsed_after, stable_after = wait_for_hydration_readiness(
            page, timeout_ms=1000, poll_interval_ms=50
        )
        assert status_after == HydrationStatus.READY_FOR_INSPECTION
        assert stable_after >= REQUIRED_STABLE_POLLS

        browser.close()
