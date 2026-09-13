"""
M1 verification checklist â€” run this against your LOCAL, M1-modified repo.

Copy this file into your repo's tests/ directory (e.g. tests/test_m1_verification.py)
and run:

    python -m pytest tests/test_m1_verification.py -v

ADJUST the import paths / function signatures below to match your actual
implementation â€” the names used here are based on your own summary
(core.tokens, core.services.submission_service.issue_approval_token, etc.)
and may not match exactly.

This file is intentionally independent of your existing test suite â€” it does
not assume your 36 M1 tests already cover these exact scenarios, it re-derives
them from first principles so a false-positive in your own tests doesn't
mask a real gap.

SAFETY: none of these tests perform any real browser action or network call
to Unstop/Internshala/Greenhouse/Lever. They operate entirely against the
DB layer and service functions with mocked/fake adapters where a browser
would otherwise be involved.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# â”€â”€ Adjust these imports to match your actual module layout â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
from core.models.opportunity import Application, Opportunity
from core.services import application_service, opportunity_service, submission_service
from core.status import ApplicationStatus, OpportunityStatus, SubmissionApprovalRequiredError

try:
    from core.tokens import is_token_valid, APPROVAL_TOKEN_MIN_LENGTH
except ImportError as exc:  # pragma: no cover
    pytest.skip(f"Adjust import path for core.tokens: {exc}", allow_module_level=True)


# â”€â”€ Fixtures â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _make_awaiting_application(db_session, dedup_suffix: str = "m1check"):
    opp = opportunity_service.create_opportunity(
        db_session,
        title="M1 Verification Role",
        company="Verification Corp",
        url=f"https://unstop.com/jobs/m1-check-{dedup_suffix}",
        source="unstop",
        reliability_tier="experimental",
    )
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.RECOMMENDED)
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.READY_TO_APPLY)
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.AWAITING_SUBMISSION)
    app = application_service.create_application(
        db_session, opportunity_id=opp.id, adapter_name="unstop"
    )
    application_service.transition_application_status(
        db_session, app.id, ApplicationStatus.FORM_FILLED,
        notes=json.dumps({"adapter": "unstop", "tier": "experimental"}),
    )
    db_session.commit()
    return opp, app


# â”€â”€ 1. Static/legacy token must never validate â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_old_static_token_never_validates():
    """The retired hardcoded constant must be rejected, not silently accepted."""
    assert is_token_valid("HUMAN_CONFIRMED_SUBMIT", "x" * 43, (datetime.now(timezone.utc) + timedelta(hours=1))) is False
    # Also check it can't accidentally satisfy the length gate used in unstop.py
    assert len("HUMAN_CONFIRMED_SUBMIT") < 32 or is_token_valid("HUMAN_CONFIRMED_SUBMIT", "x" * 43, (datetime.now(timezone.utc) + timedelta(hours=1))) is False


# â”€â”€ 2. Expiry must be enforced with correct timezone handling â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_expired_token_is_rejected(db_session):
    """An expired token must fail is_token_valid AND confirm_and_submit,
    with no TypeError from naive/aware datetime comparison."""
    opp, app = _make_awaiting_application(db_session, "expiry")

    # Issue a token, then force it into the past directly at the DB layer,
    # bypassing whatever TTL default issue_approval_token uses.
    token = submission_service.issue_approval_token(db_session, app.id)
    db_session.refresh(app)

    # Force expiry â€” use the SAME datetime "awareness" style your code uses.
    # Try aware first; if your column is naive, this line should raise or
    # need adjustment â€” that itself is useful signal.
    try:
        app.approval_token_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    except Exception:
        app.approval_token_expires_at = datetime.utcnow() - timedelta(minutes=1)
    db_session.commit()

    # This must not raise TypeError (naive vs aware comparison bug)
    try:
        valid = is_token_valid(token, app.approval_token, app.approval_token_expires_at)
    except TypeError as exc:
        pytest.fail(
            f"is_token_valid raised TypeError on expiry comparison â€” likely a "
            f"naive/aware datetime mismatch: {exc}"
        )
    assert valid is False

    # And the service-level path must fail closed too
    with pytest.raises(SubmissionApprovalRequiredError):
        submission_service.confirm_and_submit(
            db_session, app.id, approval_token=token, approval_actor="tester",
        )


# â”€â”€ 3. Token must be single-use â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_token_cannot_be_reused_after_confirmation(db_session):
    """Once a token has been consumed by confirm_and_submit, presenting the
    same token a second time must be rejected â€” not silently accepted."""
    opp, app = _make_awaiting_application(db_session, "singleuse")

    token = submission_service.issue_approval_token(db_session, app.id)

    # First use should succeed (records approval / hands off to worker)
    result_1 = submission_service.confirm_and_submit(
        db_session, app.id, approval_token=token, approval_actor="tester",
    )
    assert result_1.get("success") is True

    # Second use of the SAME token must be rejected
    with pytest.raises((SubmissionApprovalRequiredError, ValueError)):
        submission_service.confirm_and_submit(
            db_session, app.id, approval_token=token, approval_actor="tester",
        )


# â”€â”€ 4. Re-issuing a token must invalidate the previous one â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_reissuing_token_invalidates_previous(db_session):
    """Calling issue_approval_token a second time must make the first token
    unusable (approved_at reset, old token no longer valid)."""
    opp, app = _make_awaiting_application(db_session, "reissue")

    token_1 = submission_service.issue_approval_token(db_session, app.id)
    token_2 = submission_service.issue_approval_token(db_session, app.id)

    assert token_1 != token_2

    with pytest.raises((SubmissionApprovalRequiredError, ValueError)):
        submission_service.confirm_and_submit(
            db_session, app.id, approval_token=token_1, approval_actor="tester",
        )


# â”€â”€ 5. Claim state must not depend on the `notes` blob anymore â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_claim_survives_missing_or_malformed_notes(db_session):
    """If the M1 migration truly moved claim state to real columns, an
    application with notes=None or malformed JSON should still support
    the atomic claim path without special-casing."""
    from worker.runner import WorkerRunner

    opp, app = _make_awaiting_application(db_session, "malformed_notes")
    token = submission_service.issue_approval_token(db_session, app.id)
    submission_service.confirm_and_submit(
        db_session, app.id, approval_token=token, approval_actor="tester",
    )

    # Deliberately corrupt/clear notes to prove claim logic doesn't need it
    app.notes = None
    db_session.commit()

    runner = WorkerRunner()
    claimed = runner.claim_application_for_submission(db_session, app.id, worker_id="w1")
    assert claimed is True, (
        "Claim failed when notes was None â€” claim state may still be "
        "reading from the notes JSON blob instead of dedicated columns."
    )


# â”€â”€ 6. Migration file actually exists for the new columns â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_migration_file_exists_for_new_columns():
    versions_dir = Path("alembic/versions")
    assert versions_dir.exists(), "alembic/versions directory not found â€” run from repo root."

    needle_columns = {
        "approval_token", "approval_token_expires_at", "approved_at",
        "approved_by", "submission_claimed_at", "claimed_by",
    }
    found = set()
    for f in versions_dir.glob("*.py"):
        text = f.read_text()
        for col in needle_columns:
            if col in text:
                found.add(col)

    missing = needle_columns - found
    assert not missing, (
        f"No migration file references these columns: {missing}. "
        "They may exist only at the ORM level, not as an applied schema change."
    )


# â”€â”€ 7. Frontend no longer references the retired static token â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_frontend_has_no_static_token_reference():
    frontend_src = Path("frontend/src")
    if not frontend_src.exists():
        pytest.skip("frontend/src not found â€” adjust path or run from repo root.")

    hits = []
    for f in frontend_src.rglob("*.ts*"):
        text = f.read_text(errors="ignore")
        if "HUMAN_CONFIRMED_SUBMIT" in text or "HUMAN_SUBMISSION_APPROVAL_TOKEN" in text:
            hits.append(str(f))

    assert not hits, f"Old static token constant still referenced in: {hits}"


# â”€â”€ 8. Frontend no longer hardcodes platform_confirmed: true â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_frontend_does_not_hardcode_platform_confirmed_true():
    modal_path = Path(
        "frontend/src/components/feed/SubmissionReviewModal.tsx"
    )
    if not modal_path.exists():
        pytest.skip(f"{modal_path} not found â€” adjust path.")

    text = modal_path.read_text()
    # Looks for the literal pattern `platform_confirmed: true` as a hardcoded
    # value passed directly in a call (vs. a variable/derived value).
    hardcoded = re.search(r"platform_confirmed:\s*true\b", text)
    assert hardcoded is None, (
        "SubmissionReviewModal.tsx still hardcodes `platform_confirmed: true` "
        "â€” this was flagged as dead/misleading weight from the pre-M1 flow."
    )


# â”€â”€ 9. No-approval path still fails closed (regression) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_no_token_still_fails_closed(db_session):
    """Baseline regression: omitting the token entirely must still be
    rejected, exactly as before M1."""
    opp, app = _make_awaiting_application(db_session, "noapproval")

    with pytest.raises((SubmissionApprovalRequiredError, TypeError, ValueError)):
        submission_service.confirm_and_submit(
            db_session, app.id, approval_token=None, approval_actor="tester",
        )
