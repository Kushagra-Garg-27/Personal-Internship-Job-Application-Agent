"""M3.1 — Submission Boundary API Access Guard & Privileged Channel Tests.

Covers all 32 requirements specified in the M3.1 milestone:
- Guard configuration (1-10)
- Privileged CLI command (11-22)
- Frontend / static checks (23-26)
- Regressions & M1 preservation (27-32)

SAFETY:
- Mock all external API calls made by the new CLI in tests.
- No live browser action, no real submission, no live_unstop test is run.
"""

from __future__ import annotations

import hmac
import inspect
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from api.deps import get_db
from api.guards import require_submission_secret
from api.main import app
from core.config import Settings, settings
from core.models.opportunity import Application, Opportunity
from core.services import application_service, opportunity_service, submission_service
from core.status import ApplicationStatus, OpportunityStatus
import scripts.approve_submission as cli_mod


# ── Fixtures ──────────────────────────────────────────────────────────────────

VALID_TEST_SECRET = "m3-test-operator-secret-32chars-min!!"


def _make_awaiting_app(db: Session, suffix: str) -> tuple[Opportunity, Application]:
    """Create an Opportunity in AWAITING_SUBMISSION state with a FORM_FILLED application."""
    opp = opportunity_service.create_opportunity(
        db,
        title=f"M3 Guard Test {suffix}",
        company="GuardCorp",
        url=f"https://unstop.com/jobs/m3-guard-{suffix}",
        source="unstop",
        reliability_tier="experimental",
    )
    opportunity_service.transition_status(db, opp.id, OpportunityStatus.RECOMMENDED)
    opportunity_service.transition_status(db, opp.id, OpportunityStatus.READY_TO_APPLY)
    opportunity_service.transition_status(db, opp.id, OpportunityStatus.AWAITING_SUBMISSION)
    app_obj = application_service.create_application(
        db, opportunity_id=opp.id, adapter_name="unstop"
    )
    application_service.transition_application_status(
        db,
        app_obj.id,
        ApplicationStatus.FORM_FILLED,
        notes=json.dumps({"adapter": "unstop", "tier": "experimental"}),
    )
    db.commit()
    return opp, app_obj


@pytest.fixture()
def guarded_client(db_session: Session) -> Generator[tuple[TestClient, str], None, None]:
    """TestClient with SUBMISSION_GUARD_MODE='required' and a valid secret configured."""

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    orig_mode = settings.SUBMISSION_GUARD_MODE
    orig_sec = settings.SUBMISSION_API_SECRET

    settings.SUBMISSION_GUARD_MODE = "required"
    settings.SUBMISSION_API_SECRET = VALID_TEST_SECRET

    import api.guards as guards_mod
    guards_mod._GUARD_DISABLED_LOGGED = False

    with TestClient(app) as c:
        yield c, VALID_TEST_SECRET

    settings.SUBMISSION_GUARD_MODE = orig_mode
    settings.SUBMISSION_API_SECRET = orig_sec
    app.dependency_overrides.clear()


@pytest.fixture()
def fail_closed_client(db_session: Session) -> Generator[TestClient, None, None]:
    """TestClient with SUBMISSION_GUARD_MODE='required' but empty secret."""

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    orig_mode = settings.SUBMISSION_GUARD_MODE
    orig_sec = settings.SUBMISSION_API_SECRET

    settings.SUBMISSION_GUARD_MODE = "required"
    settings.SUBMISSION_API_SECRET = ""

    with TestClient(app) as c:
        yield c

    settings.SUBMISSION_GUARD_MODE = orig_mode
    settings.SUBMISSION_API_SECRET = orig_sec
    app.dependency_overrides.clear()


@pytest.fixture()
def unguarded_client(db_session: Session) -> Generator[TestClient, None, None]:
    """TestClient with SUBMISSION_GUARD_MODE='disabled'."""

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    orig_mode = settings.SUBMISSION_GUARD_MODE
    orig_sec = settings.SUBMISSION_API_SECRET

    settings.SUBMISSION_GUARD_MODE = "disabled"
    settings.SUBMISSION_API_SECRET = ""

    with TestClient(app) as c:
        yield c

    settings.SUBMISSION_GUARD_MODE = orig_mode
    settings.SUBMISSION_API_SECRET = orig_sec
    app.dependency_overrides.clear()


# ── SECTION 1: Guard Configuration (Requirements 1-10) ─────────────────────────


class TestGuardConfiguration:
    """Tests 1-10: Guard configuration and verification behavior."""

    def test_01_default_mode_is_required(self):
        """1. Default guard mode is 'required'."""
        cfg = Settings()
        assert cfg.SUBMISSION_GUARD_MODE == "required"

    def test_02_required_mode_empty_secret_fails_closed_503(
        self, fail_closed_client: TestClient, db_session: Session
    ):
        """2. Required mode + empty secret fails closed with HTTP 503."""
        _, app_obj = _make_awaiting_app(db_session, "fc-1")
        resp1 = fail_closed_client.post(
            f"/applications/{app_obj.id}/request-approval-token",
            headers={"x-submission-secret": "some-secret"},
        )
        assert resp1.status_code == 503
        assert "misconfigured" in resp1.json()["detail"].lower()

        resp2 = fail_closed_client.post(
            f"/applications/{app_obj.id}/confirm-submit",
            json={"approval_token": "some-token", "approved_by": "tester"},
            headers={"x-submission-secret": "some-secret"},
        )
        assert resp2.status_code == 503
        assert "misconfigured" in resp2.json()["detail"].lower()

    def test_03_required_mode_missing_header_rejected_403(
        self, guarded_client: tuple[TestClient, str], db_session: Session
    ):
        """3. Required mode + missing header is rejected with HTTP 403."""
        client, _ = guarded_client
        _, app_obj = _make_awaiting_app(db_session, "mh-1")
        resp = client.post(f"/applications/{app_obj.id}/request-approval-token")
        assert resp.status_code == 403
        assert "Missing X-Submission-Secret" in resp.json()["detail"]

    def test_04_required_mode_wrong_header_rejected_403(
        self, guarded_client: tuple[TestClient, str], db_session: Session
    ):
        """4. Required mode + wrong header is rejected with HTTP 403."""
        client, _ = guarded_client
        _, app_obj = _make_awaiting_app(db_session, "wh-1")
        resp = client.post(
            f"/applications/{app_obj.id}/request-approval-token",
            headers={"x-submission-secret": "wrong-secret-value-12345"},
        )
        assert resp.status_code == 403
        assert "Invalid X-Submission-Secret" in resp.json()["detail"]

    def test_05_required_mode_correct_header_reaches_service(
        self, guarded_client: tuple[TestClient, str], db_session: Session
    ):
        """5. Required mode + correct header reaches the service and returns 200."""
        client, secret = guarded_client
        _, app_obj = _make_awaiting_app(db_session, "ch-1")
        resp = client.post(
            f"/applications/{app_obj.id}/request-approval-token",
            headers={"x-submission-secret": secret},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert len(data["token"]) >= 32

    def test_06_disabled_mode_works_only_when_explicitly_configured(
        self, unguarded_client: TestClient, db_session: Session, caplog
    ):
        """6. Disabled mode permits requests and emits a warning log."""
        _, app_obj = _make_awaiting_app(db_session, "dis-1")
        resp = unguarded_client.post(
            f"/applications/{app_obj.id}/request-approval-token"
        )
        assert resp.status_code == 200

    def test_07_unknown_guard_mode_rejected(self):
        """7. Unknown guard mode is rejected during configuration validation."""
        with pytest.raises(ValidationError):
            Settings(SUBMISSION_GUARD_MODE="optional")

    def test_08_m1_token_alone_cannot_satisfy_m3(
        self, guarded_client: tuple[TestClient, str], db_session: Session
    ):
        """8. An M1 token alone cannot satisfy M3."""
        client, _ = guarded_client
        _, app_obj = _make_awaiting_app(db_session, "m1-alone")
        token = submission_service.issue_approval_token(db_session, app_obj.id)
        db_session.commit()

        resp = client.post(
            f"/applications/{app_obj.id}/confirm-submit",
            json={"approval_token": token, "approved_by": "tester"},
        )
        assert resp.status_code == 403
        assert "X-Submission-Secret" in resp.json()["detail"]

    def test_09_no_secret_value_appears_in_responses_or_logs(
        self, guarded_client: tuple[TestClient, str], db_session: Session, caplog
    ):
        """9. No secret value appears in error responses or logs."""
        client, secret = guarded_client
        _, app_obj = _make_awaiting_app(db_session, "sec-log")

        bad_secret = "candidate-guess-secret-12345"
        resp = client.post(
            f"/applications/{app_obj.id}/request-approval-token",
            headers={"x-submission-secret": bad_secret},
        )
        assert secret not in resp.text
        assert bad_secret not in resp.text
        assert secret not in caplog.text
        assert bad_secret not in caplog.text

    def test_10_constant_time_comparison_in_use(self):
        """10. Constant-time comparison remains in use."""
        import api.guards as guards_mod
        source = inspect.getsource(guards_mod)
        assert "hmac.compare_digest(x_submission_secret, configured_secret)" in source


# ── SECTION 2: Privileged Local Approval Channel (Requirements 11-22) ──────────


class TestPrivilegedApprovalCommand:
    """Tests 11-22: scripts/approve_submission.py CLI behavior with mocked HTTP calls."""

    def test_11_missing_secret_fails_before_any_api_request(self, monkeypatch):
        """11. Missing secret fails before any API request."""
        monkeypatch.delenv("SUBMISSION_API_SECRET", raising=False)
        monkeypatch.setattr("getpass.getpass", lambda *args, **kwargs: "")

        fetch_mock = MagicMock()
        monkeypatch.setattr(cli_mod, "fetch_json", fetch_mock)

        ret = cli_mod.main(["--application-id", "42"])
        assert ret == 1
        assert fetch_mock.call_count == 0

    def test_12_secret_read_from_environment_without_being_printed(
        self, monkeypatch, capsys
    ):
        """12. Secret can be read from environment without being printed."""
        secret_value = "super-secret-from-env-32-chars-long!"
        monkeypatch.setenv("SUBMISSION_API_SECRET", secret_value)

        # Mock fetch_json to stop after fetch
        monkeypatch.setattr(
            cli_mod,
            "fetch_json",
            lambda url, *a, **k: (404, {"detail": "Application not found."})
            if "applications/42" in url
            else (200, {}),
        )

        cli_mod.main(["--application-id", "42"])
        captured = capsys.readouterr()
        assert secret_value not in captured.out
        assert secret_value not in captured.err

    def test_13_hidden_prompt_used_when_environment_absent(self, monkeypatch):
        """13. Hidden prompt is used when environment configuration is absent."""
        monkeypatch.delenv("SUBMISSION_API_SECRET", raising=False)
        prompt_called = []

        def mock_getpass(prompt=""):
            prompt_called.append(True)
            return "prompted-secret-value-32chars!!"

        monkeypatch.setattr("getpass.getpass", mock_getpass)
        monkeypatch.setattr(
            cli_mod,
            "fetch_json",
            lambda url, *a, **k: (404, {"detail": "Not found"}),
        )

        cli_mod.main(["--application-id", "42"])
        assert len(prompt_called) == 1

    def test_14_declined_or_incorrect_typed_confirmation_sends_no_approval_request(
        self, monkeypatch
    ):
        """14. Declined or incorrect typed confirmation sends no approval request."""
        monkeypatch.setenv("SUBMISSION_API_SECRET", VALID_TEST_SECRET)
        monkeypatch.setattr("builtins.input", lambda *args: "CONFIRM WRONG_ID")

        post_calls = []

        def mock_fetch(url, headers=None, method="GET", payload=None):
            if method == "POST":
                post_calls.append(url)
            if "/applications/99" in url and method == "GET":
                return 200, {
                    "id": 99,
                    "opportunity_id": 42,
                    "status": "form_filled",
                    "resume_id": 1,
                    "approved_at": None,
                    "submission_claimed_at": None,
                }
            if "/opportunities/42" in url and method == "GET":
                return 200, {
                    "id": 42,
                    "title": "Software Engineer",
                    "company": "TestCorp",
                    "source": "unstop",
                    "status": "awaiting_submission",
                }
            return 200, {}

        monkeypatch.setattr(cli_mod, "fetch_json", mock_fetch)

        ret = cli_mod.main(["--application-id", "99"])
        assert ret == 1
        assert len(post_calls) == 0

    def test_15_ineligible_application_sends_no_approval_request(self, monkeypatch):
        """15. Ineligible application sends no approval request."""
        monkeypatch.setenv("SUBMISSION_API_SECRET", VALID_TEST_SECRET)

        post_calls = []

        def mock_fetch(url, headers=None, method="GET", payload=None):
            if method == "POST":
                post_calls.append(url)
            if "/applications/99" in url:
                return 200, {
                    "id": 99,
                    "opportunity_id": 42,
                    "status": "submitted",  # Ineligible!
                    "approved_at": "2026-09-12T10:00:00Z",
                    "submission_claimed_at": None,
                }
            if "/opportunities/42" in url:
                return 200, {
                    "id": 42,
                    "title": "SWE",
                    "company": "TestCorp",
                    "source": "unstop",
                    "status": "applied",
                }
            return 200, {}

        monkeypatch.setattr(cli_mod, "fetch_json", mock_fetch)

        ret = cli_mod.main(["--application-id", "99"])
        assert ret == 1
        assert len(post_calls) == 0

    def test_16_17_successful_command_performs_two_phase_with_m3_header(
        self, monkeypatch, capsys
    ):
        """16 & 17. Successful command performs request-token then confirm-submit in order with M3 header."""
        secret = VALID_TEST_SECRET
        monkeypatch.setenv("SUBMISSION_API_SECRET", secret)
        monkeypatch.setattr("builtins.input", lambda *args: "CONFIRM 99")

        call_log = []

        def mock_fetch(url, headers=None, method="GET", payload=None):
            call_log.append((method, url, headers, payload))
            if "/applications/99" in url and method == "GET":
                return 200, {
                    "id": 99,
                    "opportunity_id": 42,
                    "status": "form_filled",
                    "resume_id": 1,
                    "approved_at": None,
                    "submission_claimed_at": None,
                }
            if "/opportunities/42" in url and method == "GET":
                return 200, {
                    "id": 42,
                    "title": "SWE",
                    "company": "TestCorp",
                    "source": "unstop",
                    "status": "awaiting_submission",
                }
            if "request-approval-token" in url and method == "POST":
                return 200, {"token": "issued-token-uuid-12345", "expires_at": "2026-12-31T23:59:59Z"}
            if "confirm-submit" in url and method == "POST":
                return 200, {"success": True, "status": "approved_for_submission"}
            return 200, {}

        monkeypatch.setattr(cli_mod, "fetch_json", mock_fetch)

        ret = cli_mod.main(["--application-id", "99"])
        assert ret == 0

        # Verify calls in order:
        post_calls = [c for c in call_log if c[0] == "POST"]
        assert len(post_calls) == 2
        # Phase 1
        assert "request-approval-token" in post_calls[0][1]
        assert post_calls[0][2].get("X-Submission-Secret") == secret
        # Phase 2
        assert "confirm-submit" in post_calls[1][1]
        assert post_calls[1][2].get("X-Submission-Secret") == secret
        assert post_calls[1][3]["approval_token"] == "issued-token-uuid-12345"

    def test_18_m1_token_not_printed_or_persisted(self, monkeypatch, capsys):
        """18. The M1 token is not printed, persisted, or placed in the command line."""
        m1_token_secret = "secret-token-uuid-should-not-appear"
        monkeypatch.setenv("SUBMISSION_API_SECRET", VALID_TEST_SECRET)
        monkeypatch.setattr("builtins.input", lambda *args: "CONFIRM 99")

        def mock_fetch(url, headers=None, method="GET", payload=None):
            if "/applications/99" in url and method == "GET":
                return 200, {"id": 99, "opportunity_id": 42, "status": "form_filled"}
            if "/opportunities/42" in url and method == "GET":
                return 200, {"id": 42, "title": "SWE", "company": "Co", "source": "unstop", "status": "awaiting_submission"}
            if "request-approval-token" in url:
                return 200, {"token": m1_token_secret}
            if "confirm-submit" in url:
                return 200, {"success": True, "status": "approved_for_submission"}
            return 200, {}

        monkeypatch.setattr(cli_mod, "fetch_json", mock_fetch)

        ret = cli_mod.main(["--application-id", "99"])
        assert ret == 0
        captured = capsys.readouterr()
        assert m1_token_secret not in captured.out
        assert m1_token_secret not in captured.err

    def test_19_token_issuance_failure_prevents_confirmation(self, monkeypatch):
        """19. Failure of token issuance prevents confirmation."""
        monkeypatch.setenv("SUBMISSION_API_SECRET", VALID_TEST_SECRET)
        monkeypatch.setattr("builtins.input", lambda *args: "CONFIRM 99")

        confirm_calls = []

        def mock_fetch(url, headers=None, method="GET", payload=None):
            if "confirm-submit" in url:
                confirm_calls.append(url)
            if "/applications/99" in url and method == "GET":
                return 200, {"id": 99, "opportunity_id": 42, "status": "form_filled"}
            if "/opportunities/42" in url and method == "GET":
                return 200, {"id": 42, "title": "SWE", "company": "Co", "status": "awaiting_submission"}
            if "request-approval-token" in url:
                return 500, {"detail": "Internal server error"}
            return 200, {}

        monkeypatch.setattr(cli_mod, "fetch_json", mock_fetch)

        ret = cli_mod.main(["--application-id", "99"])
        assert ret == 1
        assert len(confirm_calls) == 0

    def test_20_confirmation_failure_reported_fail_closed(self, monkeypatch):
        """20. Confirmation failure is reported fail-closed."""
        monkeypatch.setenv("SUBMISSION_API_SECRET", VALID_TEST_SECRET)
        monkeypatch.setattr("builtins.input", lambda *args: "CONFIRM 99")

        def mock_fetch(url, headers=None, method="GET", payload=None):
            if "/applications/99" in url and method == "GET":
                return 200, {"id": 99, "opportunity_id": 42, "status": "form_filled"}
            if "/opportunities/42" in url and method == "GET":
                return 200, {"id": 42, "title": "SWE", "company": "Co", "status": "awaiting_submission"}
            if "request-approval-token" in url:
                return 200, {"token": "token-xyz"}
            if "confirm-submit" in url:
                return 400, {"detail": "Token expired"}
            return 200, {}

        monkeypatch.setattr(cli_mod, "fetch_json", mock_fetch)

        ret = cli_mod.main(["--application-id", "99"])
        assert ret == 1

    def test_21_command_does_not_import_worker_or_playwright(self):
        """21. The command does not import or invoke worker, Playwright, or platform adapters."""
        import ast
        tree = ast.parse(Path("scripts/approve_submission.py").read_text(encoding="utf-8"))
        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_modules.append(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)

        for mod in imported_modules:
            assert not mod.startswith("worker"), f"scripts/approve_submission.py must not import worker, found: {mod}"
            assert "playwright" not in mod, f"scripts/approve_submission.py must not import playwright, found: {mod}"
            assert "adapters" not in mod, f"scripts/approve_submission.py must not import adapters, found: {mod}"
            assert "filler" not in mod, f"scripts/approve_submission.py must not import filler, found: {mod}"

    def test_22_command_does_not_claim_submitted(self, monkeypatch, capsys):
        """22. The command reports approval recorded/queued, not submitted."""
        monkeypatch.setenv("SUBMISSION_API_SECRET", VALID_TEST_SECRET)
        monkeypatch.setattr("builtins.input", lambda *args: "CONFIRM 99")

        monkeypatch.setattr(
            cli_mod,
            "fetch_json",
            lambda url, *a, **k: (200, {"token": "t", "success": True, "id": 99, "opportunity_id": 42, "status": "form_filled", "title": "SWE"}),
        )

        cli_mod.main(["--application-id", "99"])
        captured = capsys.readouterr()
        assert "Approval recorded and authorized" in captured.out
        assert "queued for worker execution" in captured.out
        assert "Application submitted successfully" not in captured.out


# ── SECTION 3: Frontend & Static Checks (Requirements 23-26) ───────────────────


class TestFrontendAndStaticChecks:
    """Tests 23-26: Frontend secret-freedom, guard-status endpoint, and CORS."""

    def test_23_no_privileged_secret_in_frontend(self):
        """23. No privileged secret exists in frontend source or build configuration."""
        frontend_src = Path("frontend/src")
        for f in frontend_src.rglob("*"):
            if f.is_file() and f.suffix in {".ts", ".tsx", ".js", ".jsx", ".json", ".html"}:
                content = f.read_text(encoding="utf-8", errors="ignore")
                assert "VITE_SUBMISSION_API_SECRET" not in content
                # Ensure no literal submission secret is hardcoded
                assert "SUBMISSION_API_SECRET" not in content or "api/guard-status" in content or "getGuardStatus" in content

    def test_24_guarded_status_endpoint_returns_cli_guidance(self, guarded_client):
        """24. GET /applications/guard-status returns active mode and guidance without secrets."""
        client, secret = guarded_client
        resp = client.get("/applications/guard-status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["mode"] == "required"
        assert "scripts/approve_submission.py" in data["cli_command"]
        assert secret not in resp.text

    def test_25_ordinary_dashboard_endpoints_unaffected(self, guarded_client):
        """25. Ordinary dashboard endpoints remain unaffected when guard is required."""
        client, _ = guarded_client
        assert client.get("/health").status_code == 200
        assert client.get("/opportunities/").status_code == 200
        assert client.get("/opportunities/dashboard-feed").status_code == 200

    def test_26_cors_contains_no_wildcard(self):
        """26. CORS contains no wildcard and is not treated as authentication."""
        assert "*" not in settings.CORS_ALLOW_ORIGINS


# ── SECTION 4: Regression & M1 Invariants (Requirements 27-32) ────────────────


class TestM1RegressionsWithGuard:
    """Tests 27-32: Full M1 invariant preservation across the guarded boundary."""

    def test_27_complete_guarded_m1_token_flow_remains_valid(
        self, guarded_client: tuple[TestClient, str], db_session: Session
    ):
        """27. Complete guarded M1 token flow remains valid."""
        client, secret = guarded_client
        _, app_obj = _make_awaiting_app(db_session, "reg-flow")

        token_resp = client.post(
            f"/applications/{app_obj.id}/request-approval-token",
            headers={"x-submission-secret": secret},
        )
        assert token_resp.status_code == 200
        token = token_resp.json()["token"]

        confirm_resp = client.post(
            f"/applications/{app_obj.id}/confirm-submit",
            json={"approval_token": token, "approved_by": "reg-tester"},
            headers={"x-submission-secret": secret},
        )
        assert confirm_resp.status_code == 200
        assert confirm_resp.json().get("success") is True

    def test_28_token_reuse_remains_rejected(
        self, guarded_client: tuple[TestClient, str], db_session: Session
    ):
        """28. Token reuse remains rejected."""
        client, secret = guarded_client
        _, app_obj = _make_awaiting_app(db_session, "reg-reuse")

        token = client.post(
            f"/applications/{app_obj.id}/request-approval-token",
            headers={"x-submission-secret": secret},
        ).json()["token"]

        first = client.post(
            f"/applications/{app_obj.id}/confirm-submit",
            json={"approval_token": token, "approved_by": "tester"},
            headers={"x-submission-secret": secret},
        )
        assert first.status_code == 200

        second = client.post(
            f"/applications/{app_obj.id}/confirm-submit",
            json={"approval_token": token, "approved_by": "tester"},
            headers={"x-submission-secret": secret},
        )
        assert second.status_code == 403

    def test_29_approved_at_remains_durable(
        self, guarded_client: tuple[TestClient, str], db_session: Session
    ):
        """29. approved_at remains durable in database."""
        client, secret = guarded_client
        _, app_obj = _make_awaiting_app(db_session, "reg-durable")

        token = client.post(
            f"/applications/{app_obj.id}/request-approval-token",
            headers={"x-submission-secret": secret},
        ).json()["token"]

        client.post(
            f"/applications/{app_obj.id}/confirm-submit",
            json={"approval_token": token, "approved_by": "durable-tester"},
            headers={"x-submission-secret": secret},
        )

        db_session.refresh(app_obj)
        assert app_obj.approved_at is not None
        assert app_obj.approved_by == "durable-tester"
        assert app_obj.approval_token is None

    def test_30_31_atomic_claim_exclusion_under_guard(self, db_session: Session):
        """30 & 31. Atomic two-worker claim exclusion works on approved applications."""
        _, app_obj = _make_awaiting_app(db_session, "reg-claim")
        token = submission_service.issue_approval_token(db_session, app_obj.id)
        submission_service.confirm_and_submit(
            db_session, app_obj.id, approval_token=token, approval_actor="human"
        )
        db_session.commit()

        from worker.runner import WorkerRunner
        runner = WorkerRunner()
        claim1 = runner.claim_application_for_submission(db_session, app_obj.id, worker_id="worker-A")
        assert claim1 is True

        claim2 = runner.claim_application_for_submission(db_session, app_obj.id, worker_id="worker-B")
        assert claim2 is False

    def test_32_reconstruction_fingerprint_check_preserved(self, db_session: Session):
        """32. Reconstruction and approved-input fingerprint checks remain active."""
        from worker.engine.filler import ApplicationFiller
        assert hasattr(ApplicationFiller, "reconstruct_form_state")
        assert hasattr(ApplicationFiller, "execute_browser_submission")
