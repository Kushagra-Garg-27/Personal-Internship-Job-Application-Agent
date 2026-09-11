"""Tests for Milestone U5: Unstop authenticated-session setup and health management.

Covers all 10 required test scenarios:
1. Missing session state.
2. Valid session (offline & online probe).
3. Expired/unauthenticated session (timestamps, domain, probe redirect).
4. Invalid/corrupt encrypted session state.
5. Session-check network failure.
6. Successful interactive session capture (pre-save verification).
7. Encrypted storage is used at rest.
8. Session contents are never exposed in logs or errors.
9. Worker fails closed when session is unavailable.
10. Existing U4 submission and fill boundaries remain unchanged.

All tests use mocks/fakes — zero live network dependencies, zero real credentials.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from core.models.opportunity import Application, Opportunity
from core.models.profile import Profile
from core.models.resume import Resume
from core.services import application_service, opportunity_service
from core.status import (
    HUMAN_SUBMISSION_APPROVAL_TOKEN,
    ApplicationStatus,
    OpportunityStatus,
    ReliabilityTier,
    is_human_approved,
)
from worker.adapters.base import ApplicationContext
from worker.adapters.unstop import (
    CHECK_FAILED,
    EXPIRED,
    INVALID,
    MISSING,
    VALID,
    AuthState,
    SessionStatus,
    UnstopAdapter,
    probe_authenticated_endpoint,
)
from worker.engine.filler import ApplicationFiller
from worker.security.storage import (
    derive_fernet_key,
    load_decrypted_storage_state,
    save_encrypted_storage_state,
)
from worker.setup_session import setup_platform_session

SAMPLE_KEY = "test-encryption-key-for-u5"


# ── Fixtures & Test Doubles ───────────────────────────────────────────────

@pytest.fixture
def valid_unstop_cookies() -> list[dict[str, Any]]:
    future_ts = datetime.now(timezone.utc).timestamp() + 86400
    return [
        {"name": "XSRF-TOKEN", "value": "mock_xsrf_token_abc", "domain": ".unstop.com", "expires": future_ts},
        {"name": "unstop_session", "value": "mock_session_val_xyz", "domain": "unstop.com", "expires": future_ts},
    ]


@pytest.fixture
def valid_session_file(tmp_path: Path, valid_unstop_cookies) -> Path:
    state = {"cookies": valid_unstop_cookies, "origins": []}
    fpath = tmp_path / "valid_unstop.enc"
    save_encrypted_storage_state(state, fpath, key=SAMPLE_KEY)
    return fpath


class _FakePage:
    def __init__(self, url: str = "https://unstop.com/dashboard"):
        self.url = url
        self.closed = False

    def goto(self, url: str):
        self.url = url

    def close(self):
        self.closed = True


class _FakeContext:
    def __init__(self, cookies: list[dict[str, Any]], url: str = "https://unstop.com/dashboard"):
        self._cookies = cookies
        self._page = _FakePage(url)
        self.closed = False

    def new_page(self):
        return self._page

    def storage_state(self):
        return {"cookies": self._cookies, "origins": []}

    def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self, context: _FakeContext):
        self._context = context
        self.closed = False

    def new_context(self, **kwargs):
        return self._context

    def close(self):
        self.closed = True


class _FakePlaywright:
    def __init__(self, context: _FakeContext):
        self.chromium = MagicMock()
        self.chromium.launch = MagicMock(return_value=_FakeBrowser(context))


# ── 1. Missing Session State ───────────────────────────────────────────────

def test_missing_session_state(tmp_path: Path):
    non_existent = tmp_path / "non_existent_unstop.enc"
    adapter = UnstopAdapter(session_file=non_existent)

    status = adapter.check_session_status()
    assert isinstance(status, SessionStatus)
    assert status.valid is False
    assert status.status == MISSING
    assert status.status == "missing"
    assert status.status == "session_missing"
    assert "not found" in status.detail.lower()
    assert "setup_session" in status.detail.lower()


# ── 2. Valid Session (Offline & Online Probe) ───────────────────────────────

def test_valid_session_offline(valid_session_file: Path):
    adapter = UnstopAdapter(session_file=valid_session_file, encryption_key=SAMPLE_KEY)
    status = adapter.check_session_status(probe_network=False)

    assert status.valid is True
    assert status.status == VALID
    assert status.status == "valid"
    assert status.status == "authenticated"
    assert status.cookies_count == 2
    assert "valid unstop cookies" in status.detail.lower()


def test_valid_session_online_probe(valid_session_file: Path):
    adapter = UnstopAdapter(session_file=valid_session_file, encryption_key=SAMPLE_KEY)

    with patch("worker.adapters.unstop.probe_authenticated_endpoint") as mock_probe:
        mock_probe.return_value = (True, VALID, "Authenticated session verified by platform endpoint.")
        status = adapter.check_session_status(probe_network=True)

        assert status.valid is True
        assert status.status == VALID
        assert "verified by platform" in status.detail.lower()
        mock_probe.assert_called_once()


# ── 3. Expired / Unauthenticated Session ───────────────────────────────────

def test_session_expired_no_unstop_domain_cookies(tmp_path: Path):
    state = {
        "cookies": [{"name": "foo", "value": "bar", "domain": ".other.com"}],
        "origins": [],
    }
    fpath = tmp_path / "no_unstop.enc"
    save_encrypted_storage_state(state, fpath, key=SAMPLE_KEY)

    adapter = UnstopAdapter(session_file=fpath, encryption_key=SAMPLE_KEY)
    status = adapter.check_session_status()

    assert status.valid is False
    assert status.status == EXPIRED
    assert status.status == "expired"
    assert status.status == "session_expired"
    assert "no unstop cookies" in status.detail.lower()


def test_session_expired_past_expiration_timestamps(tmp_path: Path):
    past_ts = datetime.now(timezone.utc).timestamp() - 3600
    state = {
        "cookies": [
            {"name": "XSRF-TOKEN", "value": "old_token", "domain": ".unstop.com", "expires": past_ts}
        ],
        "origins": [],
    }
    fpath = tmp_path / "expired_ts.enc"
    save_encrypted_storage_state(state, fpath, key=SAMPLE_KEY)

    adapter = UnstopAdapter(session_file=fpath, encryption_key=SAMPLE_KEY)
    status = adapter.check_session_status()

    assert status.valid is False
    assert status.status == EXPIRED
    assert "all unstop cookies in session state have expired" in status.detail.lower()


def test_session_expired_probe_redirects_to_login(valid_session_file: Path):
    adapter = UnstopAdapter(session_file=valid_session_file, encryption_key=SAMPLE_KEY)

    with patch("worker.adapters.unstop.probe_authenticated_endpoint") as mock_probe:
        mock_probe.return_value = (
            False,
            EXPIRED,
            "Platform redirected probe to login (session unauthenticated/expired).",
        )
        status = adapter.check_session_status(probe_network=True)

        assert status.valid is False
        assert status.status == EXPIRED
        assert "login" in status.detail.lower()


# ── 4. Invalid / Corrupt Encrypted Session State ───────────────────────────

def test_session_corrupt_ciphertext(tmp_path: Path):
    corrupt_file = tmp_path / "corrupt.enc"
    corrupt_file.write_bytes(b"non-fernet-ciphertext-random-bytes")

    adapter = UnstopAdapter(session_file=corrupt_file, encryption_key=SAMPLE_KEY)
    status = adapter.check_session_status()

    assert status.valid is False
    assert status.status == INVALID
    assert status.status == "invalid"
    assert status.status == "session_corrupted"
    assert "failed to decrypt" in status.detail.lower()


def test_session_wrong_encryption_key(valid_session_file: Path):
    adapter = UnstopAdapter(session_file=valid_session_file, encryption_key="completely-wrong-key")
    status = adapter.check_session_status()

    assert status.valid is False
    assert status.status == INVALID
    assert "failed to decrypt" in status.detail.lower()


def test_session_valid_ciphertext_but_not_dictionary(tmp_path: Path):
    bad_json_file = tmp_path / "non_dict.enc"
    # Encrypt a raw JSON array instead of a dictionary
    from worker.security.storage import encrypt_storage_state
    ciphertext = encrypt_storage_state('["item1", "item2"]', key=SAMPLE_KEY)
    bad_json_file.write_bytes(ciphertext)

    adapter = UnstopAdapter(session_file=bad_json_file, encryption_key=SAMPLE_KEY)
    status = adapter.check_session_status()

    assert status.valid is False
    assert status.status == INVALID


# ── 5. Session-Check Network Failure ───────────────────────────────────────

def test_session_check_network_failure(valid_session_file: Path):
    adapter = UnstopAdapter(session_file=valid_session_file, encryption_key=SAMPLE_KEY)

    with patch("worker.adapters.unstop.probe_authenticated_endpoint") as mock_probe:
        mock_probe.return_value = (
            False,
            CHECK_FAILED,
            "Network communication failure during session probe: URLError",
        )
        status = adapter.check_session_status(probe_network=True)

        assert status.valid is False
        assert status.status == CHECK_FAILED
        assert status.status == "check_failed"
        assert "network" in status.detail.lower() or "failure" in status.detail.lower()


# ── 6. Successful Interactive Session Capture ──────────────────────────────

def test_interactive_session_capture_success(tmp_path: Path, valid_unstop_cookies):
    target_file = tmp_path / "captured_unstop.enc"
    fake_ctx = _FakeContext(cookies=valid_unstop_cookies, url="https://unstop.com/auth/login")
    fake_pw = _FakePlaywright(fake_ctx)

    def _complete_login(_):
        # Simulate browser navigating to dashboard after user logs in
        fake_ctx._page.url = "https://unstop.com/dashboard"

    saved_path = setup_platform_session(
        "unstop",
        key_override=SAMPLE_KEY,
        headless=True,
        input_fn=_complete_login,
        playwright_instance=fake_pw,
        target_path_override=target_file,
        probe_network=False,
    )

    assert saved_path == target_file
    assert target_file.exists()

    # Verify captured state is valid
    loaded = load_decrypted_storage_state(target_file, key=SAMPLE_KEY)
    assert len(loaded["cookies"]) == 2


def test_interactive_session_capture_aborts_if_still_on_login_page(tmp_path: Path, valid_unstop_cookies):
    target_file = tmp_path / "should_not_exist.enc"
    fake_ctx = _FakeContext(cookies=valid_unstop_cookies, url="https://unstop.com/auth/login")
    fake_pw = _FakePlaywright(fake_ctx)

    with pytest.raises(RuntimeError, match="login page"):
        setup_platform_session(
            "unstop",
            key_override=SAMPLE_KEY,
            headless=True,
            input_fn=lambda _: "",  # Still on login page
            playwright_instance=fake_pw,
            target_path_override=target_file,
            probe_network=False,
        )

    assert not target_file.exists()


def test_interactive_session_capture_aborts_if_no_platform_cookies(tmp_path: Path):
    target_file = tmp_path / "should_not_exist2.enc"
    other_cookies = [{"name": "other_token", "value": "123", "domain": ".other.com"}]
    fake_ctx = _FakeContext(cookies=other_cookies, url="https://unstop.com/auth/login")
    fake_pw = _FakePlaywright(fake_ctx)

    def _complete_login(_):
        fake_ctx._page.url = "https://unstop.com/dashboard"

    with pytest.raises(RuntimeError, match="no unstop cookies"):
        setup_platform_session(
            "unstop",
            key_override=SAMPLE_KEY,
            headless=True,
            input_fn=_complete_login,
            playwright_instance=fake_pw,
            target_path_override=target_file,
            probe_network=False,
        )

    assert not target_file.exists()


# ── 7. Encrypted Storage is Used at Rest ───────────────────────────────────

def test_encrypted_storage_at_rest(tmp_path: Path):
    secret_value = "SUPER_SECRET_ACTIVE_SESSION_TOKEN_12345"
    state = {
        "cookies": [
            {"name": "unstop_auth", "value": secret_value, "domain": ".unstop.com"}
        ],
        "origins": [],
    }
    fpath = tmp_path / "test_at_rest.enc"
    save_encrypted_storage_state(state, fpath, key=SAMPLE_KEY)

    raw_bytes = fpath.read_bytes()
    # Fernet ciphertext must start with gAAAAA
    assert raw_bytes.startswith(b"gAAAAA")
    # Plaintext token must NEVER appear in raw bytes
    assert secret_value.encode("utf-8") not in raw_bytes
    assert b"unstop_auth" not in raw_bytes

    # Decrypts cleanly with correct key
    decrypted = load_decrypted_storage_state(fpath, key=SAMPLE_KEY)
    assert decrypted["cookies"][0]["value"] == secret_value


# ── 8. Session Contents Never Exposed in Logs or Errors ────────────────────

def test_session_contents_never_exposed_in_logs_or_errors(tmp_path: Path, caplog):
    secret_token = "TOP_SECRET_SESSION_TOKEN_MUST_NEVER_LEAK_99999"
    past_ts = datetime.now(timezone.utc).timestamp() - 100
    state = {
        "cookies": [
            {"name": "session_tok", "value": secret_token, "domain": ".unstop.com", "expires": past_ts}
        ],
        "origins": [],
    }
    fpath = tmp_path / "leak_test.enc"
    save_encrypted_storage_state(state, fpath, key=SAMPLE_KEY)

    adapter = UnstopAdapter(session_file=fpath, encryption_key=SAMPLE_KEY)

    with caplog.at_level(logging.DEBUG):
        # 1. Session status check
        status = adapter.check_session_status()
        assert secret_token not in (status.detail or "")

        # 2. Open application context
        app_ctx = adapter.open_application("https://unstop.com/jobs/test", opportunity_id=999)
        assert secret_token not in str(app_ctx.metadata)

        # 3. Form fill attempt
        fill_res = adapter.fill(app_ctx, candidate_data={})
        assert secret_token not in (fill_res.error_reason or "")
        assert secret_token not in (fill_res.message or "")

    # Assert secret never appeared anywhere in logged text
    assert secret_token not in caplog.text


# ── 9. Worker Fails Closed When Session Unavailable ───────────────────────

def test_worker_fails_closed_when_session_missing(db_session, tmp_path: Path):
    profile = Profile(name="Alex", email="alex@example.com")
    db_session.add(profile)
    db_session.flush()

    resume = Resume(
        profile_id=profile.id,
        version=1,
        file_path="tests/fixtures/sample_resume.pdf",
        original_filename="sample_resume.pdf",
        file_size_bytes=1024,
        parse_status="success",
        is_active=True,
    )
    db_session.add(resume)
    db_session.flush()

    opp = opportunity_service.create_opportunity(
        db_session,
        title="Software Intern",
        company="Tech Labs",
        url="https://unstop.com/jobs/123",
        source="unstop",
        reliability_tier="experimental",
    )
    opp.selected_resume_id = resume.id
    opp.profile_id = profile.id
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.RECOMMENDED)
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.READY_TO_APPLY)
    db_session.commit()

    # Point Unstop adapter at missing session
    missing_session = tmp_path / "missing_session.enc"
    filler = ApplicationFiller()

    with patch("worker.engine.filler.resolve_adapter") as mock_resolve:
        mock_resolve.return_value = UnstopAdapter(session_file=missing_session)
        result = filler.process_opportunity(db_session, opp.id)

    assert result["status"] == "manual_required"
    assert "session" in result["reason"].lower()

    db_session.refresh(opp)
    assert opp.status == OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value

    # Application record marked failed
    apps = db_session.query(Application).filter_by(opportunity_id=opp.id).all()
    assert len(apps) == 1
    assert apps[0].status == ApplicationStatus.FAILED.value


def test_worker_fails_closed_when_session_corrupted(db_session, tmp_path: Path):
    profile = Profile(name="Alex", email="alex@example.com")
    db_session.add(profile)
    db_session.flush()

    resume = Resume(
        profile_id=profile.id,
        version=1,
        file_path="tests/fixtures/sample_resume.pdf",
        original_filename="sample_resume.pdf",
        file_size_bytes=1024,
        parse_status="success",
        is_active=True,
    )
    db_session.add(resume)
    db_session.flush()

    opp = opportunity_service.create_opportunity(
        db_session,
        title="ML Intern",
        company="AI Labs",
        url="https://unstop.com/jobs/456",
        source="unstop",
        reliability_tier="experimental",
    )
    opp.selected_resume_id = resume.id
    opp.profile_id = profile.id
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.RECOMMENDED)
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.READY_TO_APPLY)
    db_session.commit()

    corrupt_file = tmp_path / "corrupt_session.enc"
    corrupt_file.write_bytes(b"corrupt-data-not-valid-fernet")

    filler = ApplicationFiller()
    with patch("worker.engine.filler.resolve_adapter") as mock_resolve:
        mock_resolve.return_value = UnstopAdapter(session_file=corrupt_file)
        result = filler.process_opportunity(db_session, opp.id)

    assert result["status"] == "manual_required"
    db_session.refresh(opp)
    assert opp.status == OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value


# ── 10. Existing U4 Submission / Fill Boundaries Remain Unchanged ─────────

def test_u4_submission_boundary_remains_fail_closed():
    adapter = UnstopAdapter()
    ctx = ApplicationContext(
        opportunity_id=101,
        listing_url="https://unstop.com/competitions/test/register",
        adapter_name="unstop",
        tier=adapter.tier,
    )

    # Any signal other than HUMAN_SUBMISSION_APPROVAL_TOKEN fails closed
    for probe in [None, "", "ready_for_review", "valid", True, False, "HUMAN_CONFIRMED"]:
        with pytest.raises(PermissionError):
            adapter.submit_application(ctx, approval_token=probe)  # type: ignore[arg-type]

    # Exactly HUMAN_SUBMISSION_APPROVAL_TOKEN is recognized by approval check
    assert is_human_approved(HUMAN_SUBMISSION_APPROVAL_TOKEN) is True
    assert is_human_approved("ready_for_review") is False
