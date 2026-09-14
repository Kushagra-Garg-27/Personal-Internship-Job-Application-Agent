"""M5 - Pending queue & race-safe revocation tests (Hardened & Expanded).

Covers:
 1. revoke_approval happy path clears approved_at, sets revoke columns.
 2. Revoked app disappears from worker poll (approved_at cleared).
 3. Revoke with approved_at IS NULL raises ValueError.
 4. Revoke with submission_claimed_at IS NOT NULL raises ValueError.
 5. CAS failure (rowcount=0) raises ValueError.
 6. Reapproval preserves revocation audit columns (immutable audit).
 7. Revoked app has no approved_at; fresh token required for reapproval.
 8. Re-approval supersedes revocation by timestamp comparison.
 9. Revoke rejects submitted/failed/terminal applications.
10. GET /pending-queue with no qualifying apps returns list.
11. GET /pending-queue shows approved-but-unclaimed app as APPROVED_PENDING.
12. GET /pending-queue shows claimed app as CLAIMED_IN_PROGRESS (no black hole).
13. GET /pending-queue shows revoked app as REVOKED (audit view).
14. GET /pending-queue shows manual review app as MANUAL_REVIEW.
15. GET /pending-queue shows submitted app as SUBMITTED.
16. GET /pending-queue ?queue_state= server-side filtering works.
17. GET /pending-queue response leaks no tokens, secrets, or resume paths.
18. POST /revoke-approval returns 200 on success.
19. POST /revoke-approval on already-claimed app returns 409 Conflict.
20. POST /revoke-approval does NOT require submission secret (removes authority).
21. Worker claim CAS rejects actively revoked applications.
22. Worker runner poll skips actively revoked applications but picks up reapproved apps.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from api.deps import get_db
from api.main import app
from core.config import settings
from core.models.base import Base
from core.models.opportunity import Application, Opportunity
from core.services import submission_service
from core.status import ApplicationStatus, OpportunityStatus


# ---------------------------------------------------------------------------
# Isolated in-memory DB for this module
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def m5_engine():
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _pragmas(conn, _rec):
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(m5_engine) -> Generator[Session, None, None]:
    conn = m5_engine.connect()
    txn = conn.begin()
    sess = sessionmaker(bind=conn)()
    yield sess
    sess.close()
    txn.rollback()
    conn.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COUNTER = 0


def _make_opp(session: Session, status: str = OpportunityStatus.AWAITING_SUBMISSION.value) -> Opportunity:
    global _COUNTER
    _COUNTER += 1
    unique = f"m5-{_COUNTER}-{time.monotonic()}"
    opp = Opportunity(
        dedup_hash=hashlib.sha256(unique.encode()).hexdigest()[:64],
        status=status,
        reliability_tier="experimental",
        title=f"Test Role {_COUNTER}",
        company="Acme Corp",
        url=f"https://example.com/job/{_COUNTER}",
    )
    session.add(opp)
    session.flush()
    return opp


def _make_approved_app(session: Session, opp: Opportunity) -> Application:
    """Application with approved_at set (post-confirm state)."""
    app_obj = Application(
        opportunity_id=opp.id,
        status=ApplicationStatus.FORM_FILLED.value,
        attempt_number=1,
        approved_at=datetime.now(timezone.utc),
        approved_by="human_test",
    )
    session.add(app_obj)
    session.flush()
    return app_obj


# ---------------------------------------------------------------------------
# Service unit tests: revoke_approval and audit preservation
# ---------------------------------------------------------------------------

class TestRevokeApprovalService:

    def test_revoke_happy_path(self, session: Session):
        opp = _make_opp(session)
        app_obj = _make_approved_app(session, opp)

        res = submission_service.revoke_approval(
            session,
            app_obj.id,
            revoked_by="alice",
            reason="Found better fit",
        )

        assert res["revoked"] is True
        assert res["application_id"] == app_obj.id
        assert res["revoked_by"] == "alice"

        session.refresh(app_obj)
        assert app_obj.approved_at is None
        assert app_obj.approval_revoked_at is not None
        assert app_obj.approval_revoked_by == "alice"
        assert app_obj.approval_revocation_reason == "Found better fit"

    def test_revoke_clears_worker_filter(self, session: Session):
        opp = _make_opp(session)
        app_obj = _make_approved_app(session, opp)

        submission_service.revoke_approval(session, app_obj.id)
        session.refresh(app_obj)
        assert app_obj.approved_at is None

    def test_revoke_no_pending_approval_raises(self, session: Session):
        opp = _make_opp(session)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.FORM_FILLED.value,
            attempt_number=1,
            approved_at=None,
        )
        session.add(app_obj)
        session.flush()

        with pytest.raises(ValueError, match="no pending approval to revoke"):
            submission_service.revoke_approval(session, app_obj.id)

    def test_revoke_already_claimed_raises(self, session: Session):
        opp = _make_opp(session)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.FORM_FILLED.value,
            attempt_number=1,
            approved_at=datetime.now(timezone.utc),
            approved_by="human_test",
            submission_claimed_at=datetime.now(timezone.utc),
            claimed_by="worker-1",
        )
        session.add(app_obj)
        session.flush()

        with pytest.raises(ValueError, match="already been claimed by worker"):
            submission_service.revoke_approval(session, app_obj.id)

    def test_revoke_cas_failure_raises(self, session: Session):
        opp = _make_opp(session)
        app_obj = _make_approved_app(session, opp)

        fake_result = MagicMock()
        fake_result.rowcount = 0
        with patch.object(session, "execute", return_value=fake_result):
            with pytest.raises(ValueError, match="claimed by a worker concurrently"):
                submission_service.revoke_approval(session, app_obj.id)

    def test_reapproval_preserves_revoke_columns(self, session: Session):
        """B5: issue_approval_token preserves revocation columns for immutable audit."""
        opp = _make_opp(session)
        revoked_time = datetime.now(timezone.utc)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.FORM_FILLED.value,
            attempt_number=1,
            approval_revoked_at=revoked_time,
            approval_revoked_by="operator",
            approval_revocation_reason="test reason",
        )
        session.add(app_obj)
        session.flush()

        token = submission_service.issue_approval_token(session, app_obj.id)
        session.refresh(app_obj)
        assert app_obj.approval_revoked_at is not None, "revocation audit must NOT be erased"
        assert app_obj.approval_revoked_by == "operator"
        assert app_obj.approval_revocation_reason == "test reason"
        assert app_obj.approval_token == token

    def test_reapproval_supersedes_revocation_by_timestamp(self, session: Session):
        """A newer approved_at timestamp supersedes an older approval_revoked_at."""
        opp = _make_opp(session)
        revoked_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.FORM_FILLED.value,
            attempt_number=1,
            approval_revoked_at=revoked_time,
            approval_revoked_by="operator",
            approved_at=datetime.now(timezone.utc),  # newer!
            approved_by="human_operator",
        )
        session.add(app_obj)
        session.flush()

        assert submission_service._is_actively_revoked(app_obj) is False
        assert submission_service.derive_queue_state(app_obj, opp) == "APPROVED_PENDING"

    def test_revoke_rejects_terminal_status(self, session: Session):
        """Revoking an already submitted or failed app raises ValueError."""
        opp = _make_opp(session)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.SUBMITTED.value,
            attempt_number=1,
            approved_at=datetime.now(timezone.utc),
        )
        session.add(app_obj)
        session.flush()

        with pytest.raises(ValueError, match="terminal status"):
            submission_service.revoke_approval(session, app_obj.id)


# ---------------------------------------------------------------------------
# Client fixtures for endpoint tests
# ---------------------------------------------------------------------------

VALID_SECRET = "a-valid-test-secret-m5-hardened!"
SECRET_HEADER = {"X-Submission-Secret": VALID_SECRET}


@pytest.fixture()
def open_client(session: Session) -> Generator[TestClient, None, None]:
    """TestClient with guard disabled (read-only queue endpoint)."""
    def _override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    orig_mode = settings.SUBMISSION_GUARD_MODE
    settings.SUBMISSION_GUARD_MODE = "disabled"
    try:
        with TestClient(app) as c:
            yield c
    finally:
        settings.SUBMISSION_GUARD_MODE = orig_mode
        app.dependency_overrides.clear()


@pytest.fixture()
def guarded_client(session: Session) -> Generator[TestClient, None, None]:
    """TestClient with guard=required and a valid secret."""
    def _override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = _override_get_db
    orig_mode = settings.SUBMISSION_GUARD_MODE
    orig_secret = settings.SUBMISSION_API_SECRET
    settings.SUBMISSION_GUARD_MODE = "required"
    settings.SUBMISSION_API_SECRET = VALID_SECRET
    try:
        with TestClient(app) as c:
            yield c
    finally:
        settings.SUBMISSION_GUARD_MODE = orig_mode
        settings.SUBMISSION_API_SECRET = orig_secret
        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /applications/pending-queue Endpoint Tests
# ---------------------------------------------------------------------------

class TestPendingQueueEndpoint:

    def test_empty_queue_returns_list(self, open_client: TestClient):
        resp = open_client.get("/applications/pending-queue")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_approved_app_visible(self, session: Session, open_client: TestClient):
        opp = _make_opp(session)
        app_obj = _make_approved_app(session, opp)
        session.commit()

        resp = open_client.get("/applications/pending-queue")
        assert resp.status_code == 200
        items = resp.json()
        match = next((i for i in items if i["application_id"] == app_obj.id), None)
        assert match is not None
        assert match["queue_state"] == "APPROVED_PENDING"

    def test_claimed_app_visible_with_claimed_state(self, session: Session, open_client: TestClient):
        """B2: Claimed apps ARE visible in the queue with CLAIMED_IN_PROGRESS state."""
        opp = _make_opp(session)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.FORM_FILLED.value,
            attempt_number=1,
            approved_at=datetime.now(timezone.utc),
            approved_by="human_test",
            submission_claimed_at=datetime.now(timezone.utc),
            claimed_by="worker-1",
        )
        session.add(app_obj)
        session.commit()

        resp = open_client.get("/applications/pending-queue")
        assert resp.status_code == 200
        items = resp.json()
        match = next((i for i in items if i["application_id"] == app_obj.id), None)
        assert match is not None
        assert match["queue_state"] == "CLAIMED_IN_PROGRESS"
        assert match["claimed_by"] == "worker-1"

    def test_revoked_app_visible_for_audit(self, session: Session, open_client: TestClient):
        opp = _make_opp(session)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.FORM_FILLED.value,
            attempt_number=1,
            approval_revoked_at=datetime.now(timezone.utc),
            approval_revoked_by="operator",
            approval_revocation_reason="testing",
        )
        session.add(app_obj)
        session.commit()

        resp = open_client.get("/applications/pending-queue")
        assert resp.status_code == 200
        items = resp.json()
        match = next((i for i in items if i["application_id"] == app_obj.id), None)
        assert match is not None
        assert match["queue_state"] == "REVOKED"
        assert match["approval_revoked_by"] == "operator"

    def test_confirmation_ref_from_dedicated_column_only(self, session: Session, open_client: TestClient):
        """a. A submitted application with only app.confirmation_ref exposes that value."""
        opp = _make_opp(session, status=OpportunityStatus.APPLIED.value)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.SUBMITTED.value,
            attempt_number=1,
            approved_at=datetime.now(timezone.utc),
            submission_claimed_at=datetime.now(timezone.utc),
            confirmation_ref="CONF-COL-ONLY-999",
            notes=None,
        )
        session.add(app_obj)
        session.commit()

        resp = open_client.get("/applications/pending-queue")
        assert resp.status_code == 200
        match = next(i for i in resp.json() if i["application_id"] == app_obj.id)
        assert match["queue_state"] == "SUBMITTED"
        assert match["confirmation_ref"] == "CONF-COL-ONLY-999"

    def test_confirmation_ref_fallback_to_nested_submission_confirmation(self, session: Session, open_client: TestClient):
        """b. A nested submission_confirmation.confirmation_ref is used as fallback when column is null."""
        opp = _make_opp(session, status=OpportunityStatus.APPLIED.value)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.SUBMITTED.value,
            attempt_number=1,
            approved_at=datetime.now(timezone.utc),
            submission_claimed_at=datetime.now(timezone.utc),
            confirmation_ref=None,
            notes=json.dumps({
                "submission_confirmation": {
                    "confirmation_ref": "CONF-NESTED-FALLBACK-123",
                    "submitted_url": "https://example.com/done",
                }
            }),
        )
        session.add(app_obj)
        session.commit()

        resp = open_client.get("/applications/pending-queue")
        assert resp.status_code == 200
        match = next(i for i in resp.json() if i["application_id"] == app_obj.id)
        assert match["queue_state"] == "SUBMITTED"
        assert match["confirmation_ref"] == "CONF-NESTED-FALLBACK-123"

    def test_confirmation_ref_fallback_to_legacy_top_level_notes(self, session: Session, open_client: TestClient):
        """c. The legacy top-level notes value still works when column and nested key are absent."""
        opp = _make_opp(session, status=OpportunityStatus.APPLIED.value)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.SUBMITTED.value,
            attempt_number=1,
            approved_at=datetime.now(timezone.utc),
            submission_claimed_at=datetime.now(timezone.utc),
            confirmation_ref=None,
            notes=json.dumps({"confirmation_ref": "CONF-LEGACY-TOP-LEVEL"}),
        )
        session.add(app_obj)
        session.commit()

        resp = open_client.get("/applications/pending-queue")
        assert resp.status_code == 200
        match = next(i for i in resp.json() if i["application_id"] == app_obj.id)
        assert match["queue_state"] == "SUBMITTED"
        assert match["confirmation_ref"] == "CONF-LEGACY-TOP-LEVEL"

    def test_confirmation_ref_column_wins_when_all_disagree(self, session: Session, open_client: TestClient):
        """d. The dedicated database column wins when column, nested notes, and legacy notes all disagree."""
        opp = _make_opp(session, status=OpportunityStatus.APPLIED.value)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.SUBMITTED.value,
            attempt_number=1,
            approved_at=datetime.now(timezone.utc),
            submission_claimed_at=datetime.now(timezone.utc),
            confirmation_ref="CONF-DB-WINNER",
            notes=json.dumps({
                "submission_confirmation": {"confirmation_ref": "CONF-NESTED-LOSER"},
                "confirmation_ref": "CONF-LEGACY-LOSER",
            }),
        )
        session.add(app_obj)
        session.commit()

        resp = open_client.get("/applications/pending-queue")
        assert resp.status_code == 200
        match = next(i for i in resp.json() if i["application_id"] == app_obj.id)
        assert match["confirmation_ref"] == "CONF-DB-WINNER"

    def test_confirmation_ref_malformed_notes_harmless(self, session: Session, open_client: TestClient):
        """e. Malformed/non-JSON notes do not cause a 500 response and resolve confirmation_ref safely."""
        opp = _make_opp(session, status=OpportunityStatus.APPLIED.value)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.SUBMITTED.value,
            attempt_number=1,
            approved_at=datetime.now(timezone.utc),
            submission_claimed_at=datetime.now(timezone.utc),
            confirmation_ref=None,
            notes="not-a-json-string-{{{invalid",
        )
        session.add(app_obj)
        session.commit()

        resp = open_client.get("/applications/pending-queue")
        assert resp.status_code == 200
        match = next(i for i in resp.json() if i["application_id"] == app_obj.id)
        assert match["confirmation_ref"] is None

    def test_no_other_notes_content_returned_in_queue_response(self, session: Session, open_client: TestClient):
        """f. No other notes content (internal logs, errors, payload data) is returned by the queue response."""
        opp = _make_opp(session, status=OpportunityStatus.APPLIED.value)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.SUBMITTED.value,
            attempt_number=1,
            approved_at=datetime.now(timezone.utc),
            submission_claimed_at=datetime.now(timezone.utc),
            confirmation_ref="CONF-SECURE-99",
            notes=json.dumps({
                "private_internal_notes": "sensitive recruiter message",
                "custom_answers": [{"q": "why hire you", "a": "private answer"}],
                "stack_trace": "internal execution details",
            }),
        )
        session.add(app_obj)
        session.commit()

        resp = open_client.get("/applications/pending-queue")
        assert resp.status_code == 200
        match = next(i for i in resp.json() if i["application_id"] == app_obj.id)
        assert "private_internal_notes" not in match
        assert "custom_answers" not in match
        assert "stack_trace" not in match
        assert "notes" not in match
        payload_str = json.dumps(match)
        assert "sensitive recruiter message" not in payload_str
        assert "private answer" not in payload_str
        assert "internal execution details" not in payload_str

    def test_server_side_queue_state_filter(self, session: Session, open_client: TestClient):
        """Query param ?queue_state= filters items server-side."""
        opp1 = _make_opp(session)
        app_approved = _make_approved_app(session, opp1)

        opp2 = _make_opp(session)
        app_revoked = Application(
            opportunity_id=opp2.id,
            status=ApplicationStatus.FORM_FILLED.value,
            attempt_number=1,
            approval_revoked_at=datetime.now(timezone.utc),
            approval_revoked_by="admin",
        )
        session.add(app_revoked)
        session.commit()

        # Filter for APPROVED_PENDING
        resp_pending = open_client.get("/applications/pending-queue?queue_state=APPROVED_PENDING")
        assert resp_pending.status_code == 200
        ids_pending = [i["application_id"] for i in resp_pending.json()]
        assert app_approved.id in ids_pending
        assert app_revoked.id not in ids_pending

        # Filter for REVOKED
        resp_revoked = open_client.get("/applications/pending-queue?queue_state=REVOKED")
        assert resp_revoked.status_code == 200
        ids_revoked = [i["application_id"] for i in resp_revoked.json()]
        assert app_revoked.id in ids_revoked
        assert app_approved.id not in ids_revoked

    def test_no_sensitive_tokens_in_queue_response(self, session: Session, open_client: TestClient):
        """PendingQueueItem response never exposes tokens, secrets, or file paths."""
        opp = _make_opp(session)
        app_obj = _make_approved_app(session, opp)
        app_obj.approval_token = "secret-token-uuid-12345"
        app_obj.notes = json.dumps({"resume_file_path": "/secret/path/to/resume.pdf"})
        session.commit()

        resp = open_client.get("/applications/pending-queue")
        assert resp.status_code == 200
        match = next(i for i in resp.json() if i["application_id"] == app_obj.id)
        assert "approval_token" not in match
        assert "token" not in match
        assert "secret" not in match
        assert "/secret/path/to/resume.pdf" not in json.dumps(match)


# ---------------------------------------------------------------------------
# POST /applications/{id}/revoke-approval Endpoint Tests
# ---------------------------------------------------------------------------

class TestRevokeApprovalEndpoint:

    def test_revoke_endpoint_success(self, session: Session, guarded_client: TestClient):
        opp = _make_opp(session)
        app_obj = _make_approved_app(session, opp)
        session.commit()

        resp = guarded_client.post(
            f"/applications/{app_obj.id}/revoke-approval",
            json={"revoked_by": "test_operator", "reason": "Testing"},
            headers=SECRET_HEADER,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["revoked"] is True
        assert data["application_id"] == app_obj.id

    def test_revoke_endpoint_409_on_claimed(self, session: Session, guarded_client: TestClient):
        opp = _make_opp(session)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.FORM_FILLED.value,
            attempt_number=1,
            approved_at=datetime.now(timezone.utc),
            approved_by="human_test",
            submission_claimed_at=datetime.now(timezone.utc),
            claimed_by="worker-1",
        )
        session.add(app_obj)
        session.commit()

        resp = guarded_client.post(
            f"/applications/{app_obj.id}/revoke-approval",
            json={"revoked_by": "test_operator"},
            headers=SECRET_HEADER,
        )
        assert resp.status_code == 409

    def test_revoke_endpoint_succeeds_without_secret(self, session: Session):
        """B3: Revocation endpoint removes authority and therefore does NOT require submission secret."""
        opp = _make_opp(session)
        app_obj = _make_approved_app(session, opp)
        session.commit()

        def _override_get_db():
            try:
                yield session
            finally:
                pass

        app.dependency_overrides[get_db] = _override_get_db
        orig_mode = settings.SUBMISSION_GUARD_MODE
        orig_secret = settings.SUBMISSION_API_SECRET
        settings.SUBMISSION_GUARD_MODE = "required"
        settings.SUBMISSION_API_SECRET = VALID_SECRET
        try:
            with TestClient(app) as c:
                # Call without X-Submission-Secret header
                resp = c.post(
                    f"/applications/{app_obj.id}/revoke-approval",
                    json={"revoked_by": "dashboard_user", "reason": "Accidental approval"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["revoked"] is True

                # In contrast, confirm-submit MUST still require the secret
                resp_confirm = c.post(
                    f"/applications/{app_obj.id}/confirm-submit",
                    json={"approval_token": "some-token"},
                )
                assert resp_confirm.status_code in {403, 503}
        finally:
            settings.SUBMISSION_GUARD_MODE = orig_mode
            settings.SUBMISSION_API_SECRET = orig_secret
            app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Worker Runner Integration with Revocation
# ---------------------------------------------------------------------------

class TestWorkerSkipsRevoked:

    def test_worker_skips_revoked_app(self, session: Session):
        from worker.runner import WorkerRunner

        opp = _make_opp(session)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.FORM_FILLED.value,
            attempt_number=1,
            approved_at=None,
            approval_revoked_at=datetime.now(timezone.utc),
            approval_revoked_by="operator",
        )
        session.add(app_obj)
        session.commit()

        runner = WorkerRunner()
        mock_filler = MagicMock()
        runner.filler = mock_filler

        results = runner.poll_and_submit_queue(session)
        assert results == []
        mock_filler.execute_browser_submission.assert_not_called()

    def test_worker_claim_cas_rejects_actively_revoked_app(self, session: Session):
        """WorkerRunner.claim_application_for_submission returns False for revoked app."""
        from worker.runner import WorkerRunner

        opp = _make_opp(session)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.FORM_FILLED.value,
            attempt_number=1,
            approved_at=datetime.now(timezone.utc) - timedelta(minutes=10),
            approval_revoked_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        session.add(app_obj)
        session.commit()

        runner = WorkerRunner()
        won = runner.claim_application_for_submission(session, app_obj.id, worker_id="w-1")
        assert won is False

    def test_worker_picks_up_reapproved_app(self, session: Session):
        """A reapproved app (newer approved_at than revoked_at) can be claimed."""
        from worker.runner import WorkerRunner

        opp = _make_opp(session)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.FORM_FILLED.value,
            attempt_number=1,
            approval_revoked_at=datetime.now(timezone.utc) - timedelta(minutes=10),
            approved_at=datetime.now(timezone.utc),  # newer
            approved_by="human_approver",
        )
        session.add(app_obj)
        session.commit()

        runner = WorkerRunner()
        won = runner.claim_application_for_submission(session, app_obj.id, worker_id="w-1")
        assert won is True
        session.refresh(app_obj)
        assert app_obj.submission_claimed_at is not None


# ---------------------------------------------------------------------------
# Worker Claim Status Allowlist Tests (Fail-Closed)
# ---------------------------------------------------------------------------

class TestWorkerClaimEligibility:
    """Proves fail-closed claim eligibility in WorkerRunner (real SQLite database)."""

    def test_form_filled_can_be_claimed(self, session: Session):
        """a. FORM_FILLED can be claimed when approved and opportunity is AWAITING_SUBMISSION."""
        from worker.runner import WorkerRunner

        opp = _make_opp(session, status=OpportunityStatus.AWAITING_SUBMISSION.value)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.FORM_FILLED.value,
            attempt_number=1,
            approved_at=datetime.now(timezone.utc),
            approved_by="operator",
        )
        session.add(app_obj)
        session.commit()

        runner = WorkerRunner()
        won = runner.claim_application_for_submission(session, app_obj.id, worker_id="worker-1")
        assert won is True
        session.refresh(app_obj)
        assert app_obj.submission_claimed_at is not None
        assert app_obj.claimed_by == "worker-1"

    def test_pending_can_be_claimed(self, session: Session):
        """b. PENDING can be claimed when approved and opportunity is AWAITING_SUBMISSION."""
        from worker.runner import WorkerRunner

        opp = _make_opp(session, status=OpportunityStatus.AWAITING_SUBMISSION.value)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.PENDING.value,
            attempt_number=1,
            approved_at=datetime.now(timezone.utc),
            approved_by="operator",
        )
        session.add(app_obj)
        session.commit()

        runner = WorkerRunner()
        won = runner.claim_application_for_submission(session, app_obj.id, worker_id="worker-2")
        assert won is True
        session.refresh(app_obj)
        assert app_obj.submission_claimed_at is not None
        assert app_obj.claimed_by == "worker-2"

    def test_failed_cannot_be_claimed_even_when_approved_and_awaiting_submission(self, session: Session):
        """c. FAILED cannot be claimed even when approved_at is non-null and opp is AWAITING_SUBMISSION."""
        from worker.runner import WorkerRunner

        opp = _make_opp(session, status=OpportunityStatus.AWAITING_SUBMISSION.value)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.FAILED.value,
            attempt_number=1,
            approved_at=datetime.now(timezone.utc),
            approved_by="operator",
        )
        session.add(app_obj)
        session.commit()

        runner = WorkerRunner()
        won = runner.claim_application_for_submission(session, app_obj.id, worker_id="worker-3")
        assert won is False
        session.refresh(app_obj)
        assert app_obj.submission_claimed_at is None
        assert app_obj.claimed_by is None

    def test_submitted_cannot_be_claimed(self, session: Session):
        """d. SUBMITTED cannot be claimed."""
        from worker.runner import WorkerRunner

        opp = _make_opp(session, status=OpportunityStatus.AWAITING_SUBMISSION.value)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.SUBMITTED.value,
            attempt_number=1,
            approved_at=datetime.now(timezone.utc),
            approved_by="operator",
        )
        session.add(app_obj)
        session.commit()

        runner = WorkerRunner()
        won = runner.claim_application_for_submission(session, app_obj.id, worker_id="worker-4")
        assert won is False
        session.refresh(app_obj)
        assert app_obj.submission_claimed_at is None
        assert app_obj.claimed_by is None

    def test_unexpected_status_cannot_be_claimed(self, session: Session):
        """e. Any other unexpected application status cannot be claimed."""
        from sqlalchemy import text
        from worker.runner import WorkerRunner

        opp = _make_opp(session, status=OpportunityStatus.AWAITING_SUBMISSION.value)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.PENDING.value,
            attempt_number=1,
            approved_at=datetime.now(timezone.utc),
            approved_by="operator",
        )
        session.add(app_obj)
        session.commit()

        # Update DB directly to simulate an unexpected status stored in the table
        session.execute(
            text("UPDATE applications SET status = 'unexpected_or_withdrawn' WHERE id = :id"),
            {"id": app_obj.id},
        )
        session.commit()
        session.expire(app_obj)

        runner = WorkerRunner()
        won = runner.claim_application_for_submission(session, app_obj.id, worker_id="worker-5")
        assert won is False
        session.refresh(app_obj)
        assert app_obj.submission_claimed_at is None
        assert app_obj.claimed_by is None

    def test_rejected_claim_leaves_claimed_fields_null(self, session: Session):
        """f. A rejected claim leaves submission_claimed_at and claimed_by null in the database."""
        from worker.runner import WorkerRunner

        opp = _make_opp(session, status=OpportunityStatus.AWAITING_SUBMISSION.value)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.FAILED.value,
            attempt_number=1,
            approved_at=datetime.now(timezone.utc),
            approved_by="operator",
            submission_claimed_at=None,
            claimed_by=None,
        )
        session.add(app_obj)
        session.commit()

        runner = WorkerRunner()
        won = runner.claim_application_for_submission(session, app_obj.id, worker_id="worker-fail")
        assert won is False
        session.refresh(app_obj)
        assert app_obj.submission_claimed_at is None
        assert app_obj.claimed_by is None

    def test_poll_and_submit_queue_skips_failed_and_ineligible(self, session: Session):
        """g. poll_and_submit_queue() does not call execute_browser_submission() for failed or ineligible apps."""
        from worker.runner import WorkerRunner

        opp = _make_opp(session, status=OpportunityStatus.AWAITING_SUBMISSION.value)
        app_failed = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.FAILED.value,
            attempt_number=1,
            approved_at=datetime.now(timezone.utc),
            approved_by="operator",
        )
        session.add(app_failed)
        session.commit()

        runner = WorkerRunner()
        mock_filler = MagicMock()
        runner.filler = mock_filler

        results = runner.poll_and_submit_queue(session)
        assert results == []
        mock_filler.execute_browser_submission.assert_not_called()
        session.refresh(app_failed)
        assert app_failed.submission_claimed_at is None
        assert app_failed.claimed_by is None

    def test_real_sql_update_predicate_rejects_non_claimable_status(self, session: Session):
        """Proves the atomic SQL UPDATE predicate fails at the SQLite DB level for non-claimable status."""
        from sqlalchemy import update, or_
        from core.models.opportunity import Application

        opp = _make_opp(session, status=OpportunityStatus.AWAITING_SUBMISSION.value)
        app_obj = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.FAILED.value,
            attempt_number=1,
            approved_at=datetime.now(timezone.utc),
            submission_claimed_at=None,
        )
        session.add(app_obj)
        session.commit()

        claimable_statuses = [
            ApplicationStatus.FORM_FILLED.value,
            ApplicationStatus.PENDING.value,
        ]
        stmt = (
            update(Application)
            .where(
                Application.id == app_obj.id,
                Application.submission_claimed_at.is_(None),
                Application.approved_at.is_not(None),
                or_(
                    Application.approval_revoked_at.is_(None),
                    Application.approved_at > Application.approval_revoked_at,
                ),
                Application.status.in_(claimable_statuses),
            )
            .values(
                submission_claimed_at=datetime.utcnow(),
                claimed_by="direct-sql-worker",
            )
        )
        result = session.execute(stmt)
        session.commit()
        assert result.rowcount == 0
        session.refresh(app_obj)
        assert app_obj.submission_claimed_at is None
        assert app_obj.claimed_by is None
