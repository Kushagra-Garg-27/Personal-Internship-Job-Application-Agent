"""Tests for Milestone U6: Controlled Live Unstop Dry-Run Implementation.

Validates the dry-run runner, evidence generation, failure handling, secret scrubbing,
and the strict human-controlled submission boundary.
Uses mocks/doubles for fast, isolated, zero-network execution in CI.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.models.opportunity import Application, Opportunity
from core.models.profile import Profile
from core.models.resume import Resume
from core.services import opportunity_service
from core.services.submission_service import confirm_and_submit
from core.status import (
    HUMAN_SUBMISSION_APPROVAL_TOKEN,
    ApplicationStatus,
    OpportunityStatus,
    ReliabilityTier,
    SubmissionApprovalRequiredError,
)
from worker.adapters.base import ApplicationContext, FillResult
from worker.adapters.unstop import EXPIRED, MISSING, VALID, SessionStatus, UnstopAdapter
from worker.dry_run import DryRunResult, run_dry_run, sanitize_evidence_dict

SAMPLE_KEY = "test-encryption-key-for-u6"


# ── Test Doubles & Fixtures ──────────────────────────────────────────────────

class _FakePage:
    def __init__(self, url: str = "https://unstop.com/competitions/test/register"):
        self.url = url

    def screenshot(self, path: str, full_page: bool = True):
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDRfake_png_header")

    def locator(self, sel: str):
        mock_loc = MagicMock()
        mock_loc.count.return_value = 0
        return mock_loc


class _FakeContext:
    def __init__(self, page: _FakePage):
        self._page = page

    def new_page(self):
        return self._page

    def close(self):
        pass


@pytest.fixture
def dry_run_db_setup(db_session):
    """Seed profile, resume, and opportunity for dry run testing."""
    profile = Profile(
        name="Morgan Engineer",
        full_name="Morgan Engineer",
        email="morgan@example.com",
        phone="+91 9988776655",
        location="Bengaluru",
        gender="Female",
        differently_abled="No",
        user_type="Professional",
        organization="Tech Corp",
        designation="Software Engineer",
        work_experience="2 years",
    )
    db_session.add(profile)
    db_session.flush()

    resume = Resume(
        profile_id=profile.id,
        version=1,
        file_path="tests/fixtures/sample_resume.pdf",
        original_filename="sample_resume.pdf",
        file_size_bytes=2048,
        parse_status="success",
        is_active=True,
    )
    db_session.add(resume)
    db_session.flush()

    opp = opportunity_service.create_opportunity(
        db_session,
        title="Software Engineer Intern",
        company="Global Labs",
        url="https://unstop.com/jobs/swe-intern-999",
        source="unstop",
        reliability_tier=ReliabilityTier.EXPERIMENTAL.value,
        reason="test_setup",
        actor="test",
    )
    opp.profile_id = profile.id
    opp.selected_resume_id = resume.id
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.RECOMMENDED, actor="test")
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.READY_TO_APPLY, actor="test")
    db_session.commit()

    return {"profile": profile, "resume": resume, "opportunity": opp}


class MockSuccessAdapter(UnstopAdapter):
    """Mock adapter that simulates a valid session and successful form autofill."""

    def __init__(self):
        super().__init__()
        self.submit_called = False

    def check_session_status(self, probe_network=False, timeout=10.0):
        return SessionStatus(valid=True, status=VALID, detail="Mock valid session", cookies_count=3)

    def open_application(self, url, **kwargs):
        extracted = self.extract({"url": url, "opportunity_id": kwargs.get("opportunity_id", 0)})
        fake_page = _FakePage(url)
        return ApplicationContext(
            opportunity_id=kwargs.get("opportunity_id", 0),
            listing_url=url,
            adapter_name="unstop",
            tier=self.tier,
            extracted=extracted,
            browser_context=_FakeContext(fake_page),
            browser_page=fake_page,
        )

    def fill(self, app_ctx, candidate_data, resume_path=None, custom_answers=None):
        return FillResult(
            success=True,
            status="ready_for_review",
            message="Unstop form filled and left open. Review and click Submit yourself.",
            custom_answers=custom_answers or [],
        )

    def submit_application(self, app_ctx, approval_token=None):
        self.submit_called = True
        return super().submit_application(app_ctx, approval_token=approval_token)


# ── 1. Successful Dry-Run Flow ───────────────────────────────────────────────

def test_dry_run_success_flow(db_session, dry_run_db_setup, tmp_path):
    opp = dry_run_db_setup["opportunity"]
    artifacts_dir = tmp_path / "u6_evidence"
    mock_adapter = MockSuccessAdapter()

    result = run_dry_run(
        db_session,
        opportunity_id=opp.id,
        adapter_override=mock_adapter,
        artifacts_dir=artifacts_dir,
        headless=True,
    )

    # 1. Verify result object
    assert isinstance(result, DryRunResult)
    assert result.success is True
    assert result.submission_attempted is False
    assert result.confirmation_detected is False
    assert result.final_opportunity_status == OpportunityStatus.AWAITING_SUBMISSION.value
    assert result.final_application_status == ApplicationStatus.FORM_FILLED.value
    assert result.session_valid is True

    # 2. Verify evidence JSON file
    evidence_file = artifacts_dir / "evidence.json"
    assert evidence_file.exists()
    data = json.loads(evidence_file.read_text(encoding="utf-8"))
    assert data["submission_attempted"] is False
    assert data["confirmation_detected"] is False
    assert data["final_opportunity_status"] == "awaiting_submission"
    assert data["final_application_status"] == "form_filled"
    assert data["opportunity_id"] == opp.id

    # 3. Verify screenshot file
    screenshot_file = artifacts_dir / "screenshot.png"
    assert screenshot_file.exists()
    assert screenshot_file.stat().st_size > 0


# ── 2. Submission Code is NEVER Invoked ───────────────────────────────────────

def test_dry_run_never_invokes_submission_code(db_session, dry_run_db_setup, tmp_path):
    opp = dry_run_db_setup["opportunity"]
    mock_adapter = MockSuccessAdapter()

    with patch.object(mock_adapter, "submit_application") as mock_adapter_submit, \
         patch("core.services.submission_service.confirm_and_submit") as mock_core_submit:

        result = run_dry_run(
            db_session,
            opportunity_id=opp.id,
            adapter_override=mock_adapter,
            artifacts_dir=tmp_path,
            headless=True,
        )

        assert result.success is True
        # Critical verification: neither submit function was called
        mock_adapter_submit.assert_not_called()
        mock_core_submit.assert_not_called()


# ── 3. Evidence File Never Leaks Secrets ─────────────────────────────────────

def test_dry_run_evidence_never_leaks_secrets(db_session, dry_run_db_setup, tmp_path):
    opp = dry_run_db_setup["opportunity"]
    mock_adapter = MockSuccessAdapter()

    artifacts_dir = tmp_path / "clean_evidence"
    run_dry_run(
        db_session,
        opportunity_id=opp.id,
        adapter_override=mock_adapter,
        artifacts_dir=artifacts_dir,
        headless=True,
    )

    evidence_text = (artifacts_dir / "evidence.json").read_text(encoding="utf-8")
    lower_text = evidence_text.lower()

    # Confirm zero secret keywords exist as keys
    for secret_word in ["cookies", "unstop_session", "xsrf-token", "storage_state", "password"]:
        assert f'"{secret_word}"' not in lower_text
        assert f'"{secret_word}:"' not in lower_text


# ── 4. Database Safety Invariants ────────────────────────────────────────────

def test_dry_run_database_safety_invariants(db_session, dry_run_db_setup, tmp_path):
    opp = dry_run_db_setup["opportunity"]
    mock_adapter = MockSuccessAdapter()

    run_dry_run(
        db_session,
        opportunity_id=opp.id,
        adapter_override=mock_adapter,
        artifacts_dir=tmp_path,
        headless=True,
    )

    db_session.refresh(opp)
    app = db_session.query(Application).filter_by(opportunity_id=opp.id).first()

    assert opp.status == OpportunityStatus.AWAITING_SUBMISSION.value
    assert opp.status != OpportunityStatus.APPLIED.value
    assert app.status == ApplicationStatus.FORM_FILLED.value
    assert app.status != ApplicationStatus.SUBMITTED.value
    assert app.submitted_at is None
    assert app.confirmation_ref is None


# ── 5. Session Missing Fails Closed ──────────────────────────────────────────

def test_dry_run_session_missing_fails_closed(db_session, dry_run_db_setup, tmp_path):
    opp = dry_run_db_setup["opportunity"]
    missing_session_file = tmp_path / "non_existent.enc"
    adapter = UnstopAdapter(session_file=missing_session_file)

    artifacts_dir = tmp_path / "missing_evidence"
    result = run_dry_run(
        db_session,
        opportunity_id=opp.id,
        adapter_override=adapter,
        artifacts_dir=artifacts_dir,
        headless=True,
        probe_network=False,
    )

    assert result.success is False
    assert result.session_valid is False
    assert result.submission_attempted is False
    assert result.confirmation_detected is False
    assert result.session_health_status == "MISSING"
    assert "not found" in (result.error_reason or "").lower()

    # Evidence file reflects fail-closed status
    data = json.loads((artifacts_dir / "evidence.json").read_text(encoding="utf-8"))
    assert data["submission_attempted"] is False
    assert data["session_valid"] is False


# ── 6. Session Expired Fails Closed ──────────────────────────────────────────

def test_dry_run_session_expired_fails_closed(db_session, dry_run_db_setup, tmp_path):
    opp = dry_run_db_setup["opportunity"]
    expired_adapter = MockSuccessAdapter()
    expired_adapter.check_session_status = MagicMock(
        return_value=SessionStatus(valid=False, status=EXPIRED, detail="Session expired on server.")
    )

    artifacts_dir = tmp_path / "expired_evidence"
    result = run_dry_run(
        db_session,
        opportunity_id=opp.id,
        adapter_override=expired_adapter,
        artifacts_dir=artifacts_dir,
        headless=True,
    )

    assert result.success is False
    assert result.session_valid is False
    assert result.submission_attempted is False
    assert result.session_health_status == "EXPIRED"


# ── 7. Cloudflare / Bot Challenge Fails Closed ───────────────────────────────

def test_dry_run_bot_challenge_fails_closed(db_session, dry_run_db_setup, tmp_path):
    opp = dry_run_db_setup["opportunity"]
    challenge_adapter = MockSuccessAdapter()
    challenge_adapter.fill = MagicMock(
        return_value=FillResult(
            success=False,
            status="manual_required",
            error_reason="Cloudflare bot verification detected on Unstop. Fail closed per policy.",
        )
    )

    result = run_dry_run(
        db_session,
        opportunity_id=opp.id,
        adapter_override=challenge_adapter,
        artifacts_dir=tmp_path,
        headless=True,
    )

    assert result.success is False
    assert result.submission_attempted is False
    assert result.final_opportunity_status == OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value
    assert "cloudflare" in (result.error_reason or "").lower()


# ── 8. Form Fill Failure Fails Closed ────────────────────────────────────────

def test_dry_run_form_fill_failure_fails_closed(db_session, dry_run_db_setup, tmp_path):
    opp = dry_run_db_setup["opportunity"]
    fail_adapter = MockSuccessAdapter()
    fail_adapter.fill = MagicMock(
        return_value=FillResult(
            success=False,
            status="manual_required",
            error_reason="Required field 'statement_of_purpose' could not be filled.",
        )
    )

    result = run_dry_run(
        db_session,
        opportunity_id=opp.id,
        adapter_override=fail_adapter,
        artifacts_dir=tmp_path,
        headless=True,
    )

    assert result.success is False
    assert result.submission_attempted is False
    assert result.final_opportunity_status == OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value

    db_session.refresh(opp)
    app = db_session.query(Application).filter_by(opportunity_id=opp.id).first()
    assert app.status == ApplicationStatus.FAILED.value


# ── 9. Missing Resume Fails Closed ───────────────────────────────────────────

def test_dry_run_resume_missing_fails_closed(db_session, dry_run_db_setup, tmp_path):
    opp = dry_run_db_setup["opportunity"]
    # Point resume to non-existent file
    res = db_session.get(Resume, opp.selected_resume_id)
    res.file_path = "non/existent/resume.pdf"
    db_session.commit()

    mock_adapter = MockSuccessAdapter()
    mock_adapter.requires_resume = True

    result = run_dry_run(
        db_session,
        opportunity_id=opp.id,
        adapter_override=mock_adapter,
        artifacts_dir=tmp_path,
        headless=True,
    )

    assert result.success is False
    assert result.submission_attempted is False
    assert "real_resume_required" in (result.error_reason or "").lower()
    assert result.final_opportunity_status == OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value


# ── 10. Existing U4 Submission Gate Remains Intact ───────────────────────────

def test_u4_submission_gate_remains_intact(db_session, dry_run_db_setup):
    opp = dry_run_db_setup["opportunity"]
    app = Application(
        opportunity_id=opp.id,
        status=ApplicationStatus.FORM_FILLED.value,
        attempt_number=1,
    )
    db_session.add(app)
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.AWAITING_SUBMISSION, actor="test")
    db_session.commit()

    # Missing approval token MUST fail closed
    with pytest.raises(SubmissionApprovalRequiredError):
        confirm_and_submit(db_session, app.id, approval_token=None)

    with pytest.raises(SubmissionApprovalRequiredError):
        confirm_and_submit(db_session, app.id, approval_token="invalid_token")

    # Adapter-level submission call without approval token MUST raise PermissionError
    adapter = UnstopAdapter()
    ctx = ApplicationContext(
        opportunity_id=opp.id,
        listing_url=opp.url,
        adapter_name="unstop",
        tier=adapter.tier,
    )
    with pytest.raises(PermissionError):
        adapter.submit_application(ctx, approval_token=None)
