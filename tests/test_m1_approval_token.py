"""Dedicated test suite for M1: server-side approval token generation, validation, and DB storage.

Covers:
- core.tokens: generate_approval_token, make_token_expiry, is_token_valid
- submission_service.issue_approval_token: DB persistence, idempotency, reset behavior
- submission_service.confirm_and_submit: token gate (fail-closed), column writes on success
- WorkerRunner.claim_application_for_submission: column-based claim atomicity
- API layer: request-approval-token endpoint returns well-formed response
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from core.models.opportunity import Application, Opportunity
from core.services import application_service, opportunity_service
from core.services.submission_service import (
    confirm_and_submit,
    issue_approval_token,
)
from core.status import (
    ApplicationStatus,
    OpportunityStatus,
    SubmissionApprovalRequiredError,
)
from core.tokens import (
    APPROVAL_TOKEN_MIN_LENGTH,
    APPROVAL_TOKEN_TTL_SECONDS,
    generate_approval_token,
    is_token_valid,
    make_token_expiry,
)
from worker.runner import WorkerRunner


# ── Helper fixtures ───────────────────────────────────────────────────────────

def _make_browser_app(db_session, suffix: str = "m1") -> tuple[Opportunity, Application]:
    """Create an awaiting-submission Internshala app in form_filled status."""
    import json

    opp = opportunity_service.create_opportunity(
        db_session,
        title=f"M1 Test Job {suffix}",
        company="M1Corp",
        url=f"https://internshala.com/job/m1-{suffix}",
        source="internshala",
        reliability_tier="experimental",
    )
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.RECOMMENDED)
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.READY_TO_APPLY)
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.AWAITING_SUBMISSION)

    app = application_service.create_application(
        db_session, opportunity_id=opp.id, adapter_name="internshala"
    )
    application_service.transition_application_status(
        db_session,
        app.id,
        ApplicationStatus.FORM_FILLED,
        notes=json.dumps({"adapter": "internshala", "tier": "experimental"}),
    )
    db_session.commit()
    return opp, app


# ── core.tokens unit tests ────────────────────────────────────────────────────

class TestTokenGeneration:
    def test_generate_returns_string(self):
        t = generate_approval_token()
        assert isinstance(t, str)

    def test_generate_meets_min_length(self):
        t = generate_approval_token()
        assert len(t) >= APPROVAL_TOKEN_MIN_LENGTH

    def test_generate_is_unique_each_call(self):
        tokens = {generate_approval_token() for _ in range(20)}
        assert len(tokens) == 20  # no collisions

    def test_make_token_expiry_is_future(self):
        exp = make_token_expiry()
        assert exp > datetime.now(timezone.utc)

    def test_make_token_expiry_respects_ttl(self):
        before = datetime.now(timezone.utc)
        exp = make_token_expiry(APPROVAL_TOKEN_TTL_SECONDS)
        after = datetime.now(timezone.utc)
        assert before + timedelta(seconds=APPROVAL_TOKEN_TTL_SECONDS - 1) < exp
        assert exp < after + timedelta(seconds=APPROVAL_TOKEN_TTL_SECONDS + 1)


class TestIsTokenValid:
    def setup_method(self):
        self.good = generate_approval_token()
        self.exp_future = make_token_expiry()
        self.exp_past = datetime.now(timezone.utc) - timedelta(seconds=1)

    def test_valid_case_returns_true(self):
        assert is_token_valid(self.good, self.good, self.exp_future) is True

    def test_none_token_returns_false(self):
        assert is_token_valid(None, self.good, self.exp_future) is False

    def test_empty_token_returns_false(self):
        assert is_token_valid("", self.good, self.exp_future) is False

    def test_mismatched_token_returns_false(self):
        assert is_token_valid(generate_approval_token(), self.good, self.exp_future) is False

    def test_expired_token_returns_false(self):
        assert is_token_valid(self.good, self.good, self.exp_past) is False

    def test_none_stored_token_returns_false(self):
        assert is_token_valid(self.good, None, self.exp_future) is False

    def test_none_expiry_returns_false(self):
        assert is_token_valid(self.good, self.good, None) is False

    def test_old_static_constant_rejected(self):
        """The removed HUMAN_CONFIRMED_SUBMIT string never matches a server-issued token."""
        assert is_token_valid("HUMAN_CONFIRMED_SUBMIT", self.good, self.exp_future) is False

    def test_naive_datetime_treated_as_utc(self):
        """Naive datetimes (SQLite may return them) are normalized to UTC."""
        naive_future = datetime.utcnow() + timedelta(hours=1)
        assert naive_future.tzinfo is None
        assert is_token_valid(self.good, self.good, naive_future) is True

    def test_naive_past_datetime_still_rejects(self):
        naive_past = datetime.utcnow() - timedelta(seconds=10)
        assert is_token_valid(self.good, self.good, naive_past) is False


# ── issue_approval_token service tests ───────────────────────────────────────

class TestIssueApprovalToken:
    def test_returns_string_of_sufficient_length(self, db_session):
        _, app = _make_browser_app(db_session, "iat1")
        token = issue_approval_token(db_session, app.id)
        assert isinstance(token, str)
        assert len(token) >= APPROVAL_TOKEN_MIN_LENGTH

    def test_persists_token_to_db_column(self, db_session):
        _, app = _make_browser_app(db_session, "iat2")
        token = issue_approval_token(db_session, app.id)
        db_session.flush()
        db_session.refresh(app)
        assert app.approval_token == token

    def test_persists_expiry_column(self, db_session):
        _, app = _make_browser_app(db_session, "iat3")
        issue_approval_token(db_session, app.id)
        db_session.flush()
        db_session.refresh(app)
        assert app.approval_token_expires_at is not None
        # Must be in the future
        exp = app.approval_token_expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        assert exp > datetime.now(timezone.utc)

    def test_resets_approved_at_on_reissue(self, db_session):
        _, app = _make_browser_app(db_session, "iat4")
        app.approved_at = datetime.now(timezone.utc)
        db_session.flush()

        issue_approval_token(db_session, app.id)
        db_session.flush()
        db_session.refresh(app)
        assert app.approved_at is None  # Reset: fresh confirmation required

    def test_second_call_overwrites_first_token(self, db_session):
        _, app = _make_browser_app(db_session, "iat5")
        t1 = issue_approval_token(db_session, app.id)
        db_session.flush()
        t2 = issue_approval_token(db_session, app.id)
        db_session.flush()
        db_session.refresh(app)
        assert app.approval_token == t2
        assert t1 != t2

    def test_raises_for_nonexistent_application(self, db_session):
        with pytest.raises(ValueError, match="not found"):
            issue_approval_token(db_session, 999999)

    def test_raises_for_already_submitted_application(self, db_session):
        _, app = _make_browser_app(db_session, "iat6")
        app.status = ApplicationStatus.SUBMITTED.value
        db_session.flush()
        with pytest.raises(ValueError):
            issue_approval_token(db_session, app.id)


# ── confirm_and_submit token gate ─────────────────────────────────────────────

class TestConfirmAndSubmitGate:
    def test_valid_token_proceeds(self, db_session):
        opp, app = _make_browser_app(db_session, "cas1")
        token = issue_approval_token(db_session, app.id)
        db_session.commit()

        result = confirm_and_submit(db_session, app.id, approval_token=token)
        assert result["success"] is True

    def test_no_token_fails_closed(self, db_session):
        _, app = _make_browser_app(db_session, "cas2")
        db_session.commit()
        with pytest.raises(SubmissionApprovalRequiredError):
            confirm_and_submit(db_session, app.id)

    def test_wrong_token_fails_closed(self, db_session):
        _, app = _make_browser_app(db_session, "cas3")
        issue_approval_token(db_session, app.id)
        db_session.commit()
        with pytest.raises(SubmissionApprovalRequiredError):
            confirm_and_submit(db_session, app.id, approval_token=generate_approval_token())

    def test_expired_token_fails_closed(self, db_session):
        _, app = _make_browser_app(db_session, "cas4")
        token = issue_approval_token(db_session, app.id)
        # Force expiry into the past
        app.approval_token_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db_session.commit()
        with pytest.raises(SubmissionApprovalRequiredError):
            confirm_and_submit(db_session, app.id, approval_token=token)

    def test_static_old_constant_fails_closed(self, db_session):
        _, app = _make_browser_app(db_session, "cas5")
        issue_approval_token(db_session, app.id)
        db_session.commit()
        with pytest.raises(SubmissionApprovalRequiredError):
            confirm_and_submit(
                db_session, app.id, approval_token="HUMAN_CONFIRMED_SUBMIT"
            )

    def test_approved_at_set_on_success(self, db_session):
        opp, app = _make_browser_app(db_session, "cas6")
        token = issue_approval_token(db_session, app.id)
        db_session.commit()

        before = datetime.now(timezone.utc)
        confirm_and_submit(db_session, app.id, approval_token=token)
        db_session.refresh(app)

        assert app.approved_at is not None
        exp = app.approved_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        assert exp >= before

    def test_approved_by_actor_stored(self, db_session):
        opp, app = _make_browser_app(db_session, "cas7")
        token = issue_approval_token(db_session, app.id)
        db_session.commit()

        confirm_and_submit(
            db_session, app.id, approval_token=token, approval_actor="test_actor"
        )
        db_session.refresh(app)
        assert app.approved_by == "test_actor"


# ── WorkerRunner claim column tests ──────────────────────────────────────────

class TestClaimColumns:
    def _setup_approved_app(self, db_session, suffix):
        from datetime import datetime, timedelta
        from core.tokens import generate_approval_token

        opp, app = _make_browser_app(db_session, suffix)
        app.approval_token = generate_approval_token()
        # Use naive UTC datetimes — SQLite stores naive, and SQLAlchemy's
        # in-memory evaluator must compare like-for-like.
        app.approval_token_expires_at = datetime.utcnow() + timedelta(minutes=30)
        app.approved_at = datetime.utcnow()
        db_session.commit()
        return opp, app

    def test_claim_sets_submission_claimed_at(self, db_session):
        opp, app = self._setup_approved_app(db_session, "clm1")
        runner = WorkerRunner()
        assert runner.claim_application_for_submission(db_session, app.id, "wA") is True
        db_session.refresh(app)
        assert app.submission_claimed_at is not None
        assert app.claimed_by == "wA"

    def test_claim_is_exclusive(self, db_session):
        opp, app = self._setup_approved_app(db_session, "clm2")
        runner = WorkerRunner()
        assert runner.claim_application_for_submission(db_session, app.id, "wA") is True
        assert runner.claim_application_for_submission(db_session, app.id, "wB") is False

    def test_no_approval_cannot_claim(self, db_session):
        opp, app = _make_browser_app(db_session, "clm3")
        # No approved_at set
        runner = WorkerRunner()
        assert runner.claim_application_for_submission(db_session, app.id) is False

    def test_approved_at_is_sole_claim_gate_not_token(self, db_session):
        """M1 handoff repair: approved_at is the only durable claim gate.

        Before this fix, the claim step re-checked the token, which meant a
        confirmed application (token consumed → None) could never be claimed.
        In the corrected design:
        - approved_at IS NOT NULL → claim succeeds (token state is irrelevant)
        - approved_at IS NULL     → claim fails (even if a stale/expired token exists)

        The expired-token state cannot occur in real flow (confirm_and_submit
        clears both the token and expiry when setting approved_at), but even
        if it did, approved_at=set is definitive proof of human confirmation.
        """
        opp, app = _make_browser_app(db_session, "clm4")
        # Simulate a post-confirm state where the token happened to be retained
        # (impossible in real flow, but the gate should not break if it occurs).
        app.approval_token = generate_approval_token()
        app.approval_token_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        app.approved_at = datetime.now(timezone.utc)  # durable approval IS set
        db_session.commit()

        runner = WorkerRunner()
        # approved_at is set → claim MUST succeed regardless of token state
        assert runner.claim_application_for_submission(db_session, app.id) is True

        # Without approved_at the claim must fail (even with a live token)
        opp2, app2 = _make_browser_app(db_session, "clm4b")
        app2.approval_token = generate_approval_token()
        app2.approval_token_expires_at = datetime.now(timezone.utc) + timedelta(minutes=30)
        app2.approved_at = None  # human has NOT confirmed
        db_session.commit()
        assert runner.claim_application_for_submission(db_session, app2.id) is False


    def test_approval_remains_claimable_indefinitely_after_confirm(self, db_session):
        """M1 behavior lock: token TTL bounds the window for human confirmation. 
        Once confirmed (approved_at set, token cleared/consumed), the worker 
        can claim the application indefinitely, even days later, ensuring the 
        submission isn't silently dropped if the worker is down.
        """
        opp, app = _make_browser_app(db_session, "clm5")
        # Simulate a consumed token post-confirm
        app.approval_token = None
        app.approval_token_expires_at = None
        # Approval happened 3 days ago
        app.approved_at = datetime.now(timezone.utc) - timedelta(days=3)
        db_session.commit()
        
        runner = WorkerRunner()
        assert runner.claim_application_for_submission(db_session, app.id) is True



# ── API endpoint test ─────────────────────────────────────────────────────────

def test_request_approval_token_api_returns_token_and_expiry(client, db_session):
    """GET/POST request-approval-token -> returns token + expires_at; token stored in DB."""
    opp, app = _make_browser_app(db_session, "api1")

    resp = client.post(f"/applications/{app.id}/request-approval-token")
    assert resp.status_code == 200
    data = resp.json()

    assert "token" in data
    assert len(data["token"]) >= APPROVAL_TOKEN_MIN_LENGTH
    assert "expires_at" in data

    db_session.refresh(app)
    assert app.approval_token == data["token"]
    assert app.approval_token_expires_at is not None


def test_request_approval_token_then_confirm_submit_succeeds(client, db_session):
    """Full two-phase M1 flow through HTTP layer: request-token -> confirm-submit."""
    opp, app = _make_browser_app(db_session, "api2")

    # Phase 1: request token
    token_resp = client.post(f"/applications/{app.id}/request-approval-token")
    assert token_resp.status_code == 200
    token = token_resp.json()["token"]

    # Phase 2: confirm submit with server-issued token
    confirm_resp = client.post(
        f"/applications/{app.id}/confirm-submit",
        json={"approval_token": token, "approved_by": "test_human"},
    )
    assert confirm_resp.status_code == 200
    data = confirm_resp.json()
    assert data["success"] is True
    assert data["mode"] == "browser_orchestrator"


def test_confirm_submit_without_token_issuance_returns_403(client, db_session):
    """confirm-submit with no prior token issuance returns 403 (M1 fail-closed)."""
    opp, app = _make_browser_app(db_session, "api3")

    resp = client.post(
        f"/applications/{app.id}/confirm-submit",
        json={"approval_token": "HUMAN_CONFIRMED_SUBMIT"},
    )
    assert resp.status_code == 403
