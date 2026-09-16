"""End-to-end integration tests for the M1 approval-to-worker execution handoff.

These tests verify the complete sequence:
    issue_approval_token() -> confirm_and_submit() -> poll_and_submit_queue()
    -> claim_application_for_submission() -> reconstruct_form_state()
    -> execute_browser_submission() -> submitted/applied

All tests use the real service and runner methods.
The only mocked surface is the final browser/platform adapter action
(submit_application) and the form reconstruction (open_application / fill).

ABSOLUTE SAFETY: No live_unstop test is run. No authenticated browser is launched.
No real submission is performed or attempted.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from pathlib import Path

from core.models.opportunity import Application, Opportunity
from core.models.resume import Resume
from core.services.submission_service import (
    compute_approved_input_digest,
    compute_file_sha256,
    compute_profile_sha256,
    confirm_and_submit,
    issue_approval_token,
)
from core.status import ApplicationStatus, OpportunityStatus
from core.tokens import generate_approval_token
from worker.adapters.base import ApplicationContext, ExtractedListing, FillResult
from worker.engine.filler import ApplicationFiller
from worker.runner import WorkerRunner


# == Helpers ===================================================================

def _make_resume_with_content(
    db_session: Session,
    profile_id: int,
    file_path: Path | str,
    content: bytes = b"%PDF-1.4 sample resume content",
) -> Resume:
    p = Path(file_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)
    resume = Resume(
        profile_id=profile_id,
        version=1,
        file_path=str(p),
        original_filename=p.name,
        file_size_bytes=len(content),
        is_active=True,
    )
    db_session.add(resume)
    db_session.commit()
    return resume


def _make_opportunity(db_session: Session, suffix: str = "1") -> Opportunity:
    opp = Opportunity(
        dedup_hash=f"hash_handoff_{suffix}",
        title="Software Engineer Intern",
        company="Acme Corp",
        url="https://unstop.com/hackathon/test",
        source="unstop",
        status=OpportunityStatus.AWAITING_SUBMISSION.value,
    )
    db_session.add(opp)
    db_session.commit()
    return opp


def _make_form_filled_app(db_session: Session, opp: Opportunity) -> Application:
    app = Application(
        opportunity_id=opp.id,
        status=ApplicationStatus.FORM_FILLED.value,
        notes=json.dumps({
            "adapter": "unstop",
            "tier": "experimental",
            "custom_answers": [
                {"question_id": "q1", "question_text": "Why Acme?", "answer": "Stored answer", "is_ai_draft": True}
            ],
            "draft_payload": None,
            "filled_at": datetime.now(timezone.utc).isoformat(),
        }),
    )
    db_session.add(app)
    db_session.commit()
    return app


def _execute_claimed(
    filler: ApplicationFiller, db_session: Session, app: Application
) -> dict:
    """Execute using the exact persisted claim captured by the test worker."""
    db_session.refresh(app)
    assert app.claimed_by is not None
    assert app.submission_claimed_at is not None
    return filler.execute_browser_submission(
        db_session,
        app.id,
        expected_claimed_by=app.claimed_by,
        expected_submission_claimed_at=app.submission_claimed_at,
    )


def _mock_adapter(success: bool = True):
    mock_adapter = MagicMock()
    mock_adapter.adapter_name = "unstop"
    mock_adapter.tier = MagicMock()
    mock_adapter.tier.value = "experimental"

    ctx = ApplicationContext(
        opportunity_id=1,
        listing_url="https://unstop.com/hackathon/test",
        adapter_name="unstop",
        tier=mock_adapter.tier,
        extracted=ExtractedListing(
            company="Acme Corp",
            title="Software Engineer Intern",
            url="https://unstop.com/hackathon/test",
            custom_questions=[],
        ),
    )
    mock_adapter.open_application.return_value = ctx
    mock_adapter.extract.return_value = ctx.extracted
    mock_adapter.fill.return_value = FillResult(
        success=success,
        status="ready_for_review" if success else "manual_required",
        error_reason=None if success else "Fill failed",
        custom_answers=[],
    )
    return mock_adapter, ctx


# == Class 1: Full E2E sequence ================================================

class TestFullE2EHandoff:

    def test_full_two_phase_browser_submission(self, db_session: Session):
        opp = _make_opportunity(db_session, "e2e_full")
        app = _make_form_filled_app(db_session, opp)

        # Step 1: issue token
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        db_session.refresh(app)
        assert app.approval_token is not None
        assert app.approved_at is None

        # Step 2: human confirms via real confirm_and_submit
        result = confirm_and_submit(db_session, app.id, approval_token=token)
        assert result["success"] is True

        db_session.refresh(app)
        assert app.approval_token is None, "approval_token must be consumed (None) after confirmation"
        assert app.approval_token_expires_at is None, "expiry must be cleared after confirmation"
        assert app.approved_at is not None, "approved_at must be set after confirmation"

        # Step 3-6: worker polls and submits
        mock_adapter, ctx = _mock_adapter(success=True)
        mock_adapter.submit_application.return_value = {
            "success": True,
            "confirmation_ref": "UNSTOP-CONFIRMED-123",
            "clicked_selector": "button.submit",
            "url": "https://unstop.com/success",
        }

        filler = ApplicationFiller(adapter_override=mock_adapter)
        runner = WorkerRunner(filler=filler)

        with patch("worker.engine.filler.resolve_adapter", return_value=mock_adapter):
            results = runner.poll_and_submit_queue(db_session, worker_id="worker_e2e")

        assert len(results) == 1
        assert results[0]["success"] is True
        assert results[0]["status"] == "applied"
        assert results[0]["confirmation_ref"] == "UNSTOP-CONFIRMED-123"

        db_session.refresh(app)
        db_session.refresh(opp)
        assert app.status == ApplicationStatus.SUBMITTED.value
        assert opp.status == OpportunityStatus.APPLIED.value
        assert app.submission_claimed_at is not None
        assert app.claimed_by == "worker_e2e"

        # submit_application called WITHOUT approval_token kwarg
        mock_adapter.submit_application.assert_called_once_with(ctx)

    def test_approval_token_none_after_confirmation(self, db_session: Session):
        opp = _make_opportunity(db_session, "token_consumed")
        app = _make_form_filled_app(db_session, opp)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)
        db_session.refresh(app)
        assert app.approval_token is None
        assert app.approval_token_expires_at is None

    def test_approved_at_is_durable_after_confirmation(self, db_session: Session):
        opp = _make_opportunity(db_session, "approved_at_durable")
        app = _make_form_filled_app(db_session, opp)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)
        db_session.refresh(app)
        assert app.approved_at is not None
        assert app.approval_token is None


# == Class 2: Polling discovery ===============================================

class TestPollingDiscovery:

    def test_confirmed_token_consumed_app_discovered(self, db_session: Session):
        opp = _make_opportunity(db_session, "discover_confirmed")
        app = _make_form_filled_app(db_session, opp)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)
        db_session.refresh(app)
        assert app.approval_token is None
        assert app.approved_at is not None

        filler_mock = MagicMock()
        filler_mock.execute_browser_submission.return_value = {"success": True, "status": "applied"}
        runner = WorkerRunner(filler=filler_mock)
        results = runner.poll_and_submit_queue(db_session, worker_id="poller_test")
        assert len(results) == 1
        filler_mock.execute_browser_submission.assert_called_once_with(
            db_session,
            app.id,
            expected_claimed_by="poller_test",
            expected_submission_claimed_at=app.submission_claimed_at,
        )

    def test_unapproved_application_skipped(self, db_session: Session):
        opp = _make_opportunity(db_session, "skip_unapproved")
        app = _make_form_filled_app(db_session, opp)
        filler_mock = MagicMock()
        runner = WorkerRunner(filler=filler_mock)
        results = runner.poll_and_submit_queue(db_session)
        assert len(results) == 0
        filler_mock.execute_browser_submission.assert_not_called()
        db_session.refresh(app)
        assert app.submission_claimed_at is None


# == Class 3: Execute authorization gates =====================================

class TestExecuteAuthorizationGates:

    def test_unclaimed_app_raises(self, db_session: Session):
        opp = _make_opportunity(db_session, "unclaimed_gate")
        app = _make_form_filled_app(db_session, opp)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)

        filler = ApplicationFiller()
        with pytest.raises(RuntimeError, match="must be claimed"):
            filler.execute_browser_submission(db_session, app.id)

    def test_no_approved_at_raises(self, db_session: Session):
        opp = _make_opportunity(db_session, "unapproved_exec")
        app = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.FORM_FILLED.value,
            notes="{}",
            approved_at=None,
            submission_claimed_at=datetime.utcnow(),
            claimed_by="rogue",
        )
        db_session.add(app)
        db_session.commit()

        filler = ApplicationFiller()
        with pytest.raises(RuntimeError, match="no durable approval"):
            _execute_claimed(filler, db_session, app)

    def test_claimed_app_executes_without_approval_token(self, db_session: Session):
        opp = _make_opportunity(db_session, "claimed_no_token")
        app = _make_form_filled_app(db_session, opp)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)

        runner = WorkerRunner()
        claimed = runner.claim_application_for_submission(db_session, app.id, worker_id="w1")
        assert claimed is True

        db_session.refresh(app)
        assert app.approval_token is None
        assert app.approved_at is not None

        mock_adapter, ctx = _mock_adapter(success=True)
        mock_adapter.submit_application.return_value = {"success": True, "confirmation_ref": "REF-OK"}
        filler = ApplicationFiller(adapter_override=mock_adapter)
        with patch("worker.engine.filler.resolve_adapter", return_value=mock_adapter):
            result = _execute_claimed(filler, db_session, app)

        assert result["success"] is True
        # Called WITHOUT approval_token keyword
        mock_adapter.submit_application.assert_called_once_with(ctx)


# == Class 4: Reconstruct form state ==========================================

class TestReconstructFormState:

    def test_does_not_create_duplicate_application_row(self, db_session: Session):
        opp = _make_opportunity(db_session, "recon_no_dup")
        app = _make_form_filled_app(db_session, opp)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)

        count_before = db_session.query(Application).filter(Application.opportunity_id == opp.id).count()

        mock_adapter, ctx = _mock_adapter(success=True)
        filler = ApplicationFiller(adapter_override=mock_adapter)
        with patch("worker.engine.filler.resolve_adapter", return_value=mock_adapter):
            res = filler.reconstruct_form_state(db_session, app.id)

        count_after = db_session.query(Application).filter(Application.opportunity_id == opp.id).count()
        assert res["success"] is True
        assert count_after == count_before, f"Created {count_after - count_before} extra Application row(s)"

    def test_requires_awaiting_submission_not_ready_to_apply(self, db_session: Session):
        opp = Opportunity(
            dedup_hash="hash_recon_wrong_status_handoff",
            title="Test", company="Corp",
            url="https://unstop.com/x", source="unstop",
            status=OpportunityStatus.READY_TO_APPLY.value,
        )
        db_session.add(opp)
        db_session.commit()
        app = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.FORM_FILLED.value,
            notes="{}",
            approved_at=datetime.utcnow(),
        )
        db_session.add(app)
        db_session.commit()

        filler = ApplicationFiller()
        res = filler.reconstruct_form_state(db_session, app.id)
        assert res["success"] is False
        assert "awaiting_submission" in res["error"]

    def test_reuses_stored_custom_answers_not_redrafts(self, db_session: Session):
        stored_answers = [{"question_id": "q1", "answer": "My stored answer", "is_ai_draft": True}]
        opp = _make_opportunity(db_session, "recon_reuse_answers")
        app = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.FORM_FILLED.value,
            approved_at=datetime.utcnow(),
            notes=json.dumps({"custom_answers": stored_answers, "adapter": "unstop", "tier": "experimental"}),
        )
        db_session.add(app)
        db_session.commit()

        mock_adapter, ctx = _mock_adapter(success=True)
        filler = ApplicationFiller(adapter_override=mock_adapter)
        with patch("worker.engine.filler.resolve_adapter", return_value=mock_adapter):
            res = filler.reconstruct_form_state(db_session, app.id)

        assert res["success"] is True
        fill_call = mock_adapter.fill.call_args
        actual = fill_call.kwargs.get("custom_answers", [])
        assert actual == stored_answers

    def test_reconstruction_failure_no_submission(self, db_session: Session):
        opp = _make_opportunity(db_session, "recon_fail")
        app = _make_form_filled_app(db_session, opp)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)

        runner = WorkerRunner()
        runner.claim_application_for_submission(db_session, app.id, worker_id="w1")

        mock_adapter = MagicMock()
        mock_adapter.adapter_name = "unstop"
        filler = ApplicationFiller(adapter_override=mock_adapter)
        with patch.object(filler, "reconstruct_form_state") as mock_recon:
            mock_recon.return_value = {"success": False, "error": "Browser crashed"}
            result = _execute_claimed(filler, db_session, app)

        assert result["success"] is False
        assert result["status"] == "manual_required"
        mock_adapter.submit_application.assert_not_called()

        db_session.refresh(app)
        db_session.refresh(opp)
        assert app.status == ApplicationStatus.FAILED.value
        assert opp.status == OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value


# == Class 5: Concurrency =====================================================

class TestConcurrencyAfterConfirmation:

    def test_two_workers_only_one_claims_confirmed_app(self, db_session: Session):
        opp = _make_opportunity(db_session, "concurrency_confirmed")
        app = _make_form_filled_app(db_session, opp)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)

        runner_a = WorkerRunner()
        runner_b = WorkerRunner()
        claimed_a = runner_a.claim_application_for_submission(db_session, app.id, worker_id="w_a")
        claimed_b = runner_b.claim_application_for_submission(db_session, app.id, worker_id="w_b")

        assert claimed_a is True
        assert claimed_b is False
        db_session.refresh(app)
        assert app.claimed_by == "w_a"

    def test_second_polling_cycle_does_not_resubmit(self, db_session: Session):
        opp = _make_opportunity(db_session, "second_poll")
        app = _make_form_filled_app(db_session, opp)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)

        filler_mock = MagicMock()
        filler_mock.execute_browser_submission.return_value = {"success": True, "status": "applied"}
        runner = WorkerRunner(filler=filler_mock)

        results1 = runner.poll_and_submit_queue(db_session, worker_id="w_first")
        assert len(results1) == 1

        results2 = runner.poll_and_submit_queue(db_session, worker_id="w_second")
        assert len(results2) == 0
        assert filler_mock.execute_browser_submission.call_count == 1


# == Class 6: Failure / ambiguity =============================================

class TestFailureAndAmbiguity:

    def test_ambiguous_adapter_result_transitions_to_manual_review(self, db_session: Session):
        opp = _make_opportunity(db_session, "ambig_confirmed")
        app = _make_form_filled_app(db_session, opp)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)

        runner = WorkerRunner()
        runner.claim_application_for_submission(db_session, app.id, worker_id="w1")

        filler = ApplicationFiller()
        with patch.object(filler, "reconstruct_form_state") as mock_recon:
            mock_recon.return_value = {"success": True, "app_ctx": MagicMock()}
            with patch("worker.engine.filler.resolve_adapter") as mock_resolve:
                mock_adapter = MagicMock()
                mock_adapter.adapter_name = "unstop"
                mock_adapter.submit_application.return_value = {
                    "success": False,
                    "error": "Timeout waiting for Unstop confirmation",
                }
                mock_resolve.return_value = mock_adapter
                result = _execute_claimed(filler, db_session, app)

        assert result["success"] is False
        assert result["status"] == "manual_required"

        db_session.refresh(app)
        db_session.refresh(opp)
        assert app.status == ApplicationStatus.FAILED.value
        assert opp.status == OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value

        # Cannot be re-claimed
        assert not runner.claim_application_for_submission(db_session, app.id)

        # Next poll skips it
        filler_mock2 = MagicMock()
        runner2 = WorkerRunner(filler=filler_mock2)
        assert len(runner2.poll_and_submit_queue(db_session)) == 0
        filler_mock2.execute_browser_submission.assert_not_called()


# == Class 7: Stable HTTP token invariants ====================================

class TestStableHTTPTokenInvariants:

    def _make_greenhouse_app(self, db_session: Session, suffix: str):
        opp = Opportunity(
            dedup_hash=f"hash_greenhouse_handoff_{suffix}",
            title="Backend Engineer", company="Acme GH",
            url="https://boards.greenhouse.io/acme/jobs/123",
            source="greenhouse",
            status=OpportunityStatus.AWAITING_SUBMISSION.value,
        )
        db_session.add(opp)
        db_session.commit()
        draft_payload = {
            "submission_url": "https://boards.greenhouse.io/acme/jobs/123/apply",
            "fields": {"first_name": "Test"},
        }
        app = Application(
            opportunity_id=opp.id,
            status=ApplicationStatus.FORM_FILLED.value,
            notes=json.dumps({"adapter": "greenhouse", "tier": "stable", "draft_payload": draft_payload}),
        )
        db_session.add(app)
        db_session.commit()
        return opp, app

    def test_stable_http_token_consumed_before_network_call(self, db_session: Session):
        import httpx
        opp, app = self._make_greenhouse_app(db_session, "token_consumed_http")
        token = issue_approval_token(db_session, app.id)
        db_session.commit()

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 201
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json.return_value = {"id": "GH-999"}
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = mock_resp

        confirm_and_submit(db_session, app.id, approval_token=token, http_client=mock_client)

        db_session.refresh(app)
        assert app.approval_token is None, "Stable HTTP tier must consume the token"
        assert app.approval_token_expires_at is None

    def test_stable_http_token_cannot_be_replayed(self, db_session: Session):
        import httpx
        from core.status import SubmissionApprovalRequiredError

        opp, app = self._make_greenhouse_app(db_session, "replay_http")
        token = issue_approval_token(db_session, app.id)
        db_session.commit()

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 201
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json.return_value = {"id": "GH-000"}
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = mock_resp

        confirm_and_submit(db_session, app.id, approval_token=token, http_client=mock_client)

        with pytest.raises((SubmissionApprovalRequiredError, ValueError)):
            confirm_and_submit(db_session, app.id, approval_token=token, http_client=mock_client)


# == Class 8: M1 regression invariants ========================================

class TestM1RegressionInvariants:

    def test_expired_token_rejected_before_confirmation(self, db_session: Session):
        from core.status import SubmissionApprovalRequiredError
        opp = _make_opportunity(db_session, "expiry_gate")
        app = _make_form_filled_app(db_session, opp)
        app.approval_token = generate_approval_token()
        app.approval_token_expires_at = datetime.utcnow() - timedelta(seconds=1)
        app.approved_at = None
        db_session.commit()
        with pytest.raises(SubmissionApprovalRequiredError):
            confirm_and_submit(db_session, app.id, approval_token=app.approval_token)

    def test_wrong_token_rejected(self, db_session: Session):
        from core.status import SubmissionApprovalRequiredError
        opp = _make_opportunity(db_session, "wrong_token")
        app = _make_form_filled_app(db_session, opp)
        issue_approval_token(db_session, app.id)
        db_session.commit()
        wrong_token = generate_approval_token()
        with pytest.raises(SubmissionApprovalRequiredError):
            confirm_and_submit(db_session, app.id, approval_token=wrong_token)

    def test_token_single_use_cannot_reconfirm(self, db_session: Session):
        from core.status import SubmissionApprovalRequiredError
        opp = _make_opportunity(db_session, "single_use")
        app = _make_form_filled_app(db_session, opp)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)
        with pytest.raises((SubmissionApprovalRequiredError, ValueError)):
            confirm_and_submit(db_session, app.id, approval_token=token)

    def test_indefinite_post_confirm_approval_no_expiry(self, db_session: Session):
        """approved_at does not expire. Worker can claim indefinitely after confirmation.

        The 30-min TTL bounds token->confirm only. Post-confirm, approved_at
        is a durable record with no automatic expiry. This is the documented design.
        """
        opp = _make_opportunity(db_session, "indefinite_approval")
        app = _make_form_filled_app(db_session, opp)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)
        db_session.refresh(app)
        assert app.approved_at is not None
        assert app.approval_token is None

        runner = WorkerRunner()
        claimed = runner.claim_application_for_submission(db_session, app.id, worker_id="w_late")
        assert claimed is True, (
            "Claim must succeed even when approved_at was set earlier — "
            "durable approval has no automatic expiry."
        )


# == Class 9: Invariant 1 - Structural / Call-path Verification ================

class TestInvariant1CallPath:
    """Invariant 1: No production path can invoke UnstopAdapter.submit_application()
    except through ApplicationFiller.execute_browser_submission() after durable
    approved_at and submission_claimed_at checks.
    """

    def test_ast_no_production_path_calls_submit_application_outside_filler(self):
        """Static AST analysis: scan all python files in worker/, core/, api/.
        Verify that `.submit_application(...)` is NEVER called anywhere except
        inside ApplicationFiller.execute_browser_submission.
        """
        import ast
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        production_dirs = [repo_root / "worker", repo_root / "core", repo_root / "api"]

        call_sites: list[tuple[str, int, str]] = []

        for pdir in production_dirs:
            for py_file in pdir.rglob("*.py"):
                if "__pycache__" in str(py_file):
                    continue
                content = py_file.read_text(encoding="utf-8")
                try:
                    tree = ast.parse(content, filename=str(py_file))
                except SyntaxError:
                    continue

                for node in ast.walk(tree):
                    if isinstance(node, ast.Call):
                        func = node.func
                        attr_name = None
                        if isinstance(func, ast.Attribute):
                            attr_name = func.attr
                        elif isinstance(func, ast.Name):
                            attr_name = func.id

                        if attr_name == "submit_application":
                            rel_path = py_file.relative_to(repo_root).as_posix()
                            call_sites.append((rel_path, node.lineno, attr_name))

        # The ONLY permitted call site in the entire production codebase is inside worker/engine/filler.py
        assert len(call_sites) == 1, f"Unexpected production call sites to submit_application: {call_sites}"
        file_path, _, _ = call_sites[0]
        assert file_path == "worker/engine/filler.py"

        # Verify that call site is within execute_browser_submission
        filler_code = (repo_root / "worker/engine/filler.py").read_text(encoding="utf-8")
        filler_tree = ast.parse(filler_code)
        found_in_exec = False
        for node in ast.walk(filler_tree):
            if isinstance(node, ast.FunctionDef) and node.name == "execute_browser_submission":
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Call) and getattr(inner.func, "attr", None) == "submit_application":
                        found_in_exec = True
                        break
        assert found_in_exec, "submit_application call site must reside inside execute_browser_submission"

    def test_unstop_adapter_has_no_fake_token_gate_parameter(self):
        """UnstopAdapter.submit_application must NOT have approval_token parameter."""
        import inspect
        from worker.adapters.unstop import UnstopAdapter

        sig = inspect.signature(UnstopAdapter.submit_application)
        assert "approval_token" not in sig.parameters, (
            "Fake string-length token gate must not be restored to UnstopAdapter.submit_application"
        )

    def test_execute_browser_submission_fails_closed_without_approved_at(self, db_session: Session):
        """execute_browser_submission must raise RuntimeError if approved_at is None."""
        opp = _make_opportunity(db_session, "inv1_no_appr")
        app = _make_form_filled_app(db_session, opp)
        app.submission_claimed_at = datetime.now(timezone.utc)
        app.claimed_by = "w1"
        app.approved_at = None
        db_session.commit()

        mock_adapter = MagicMock()
        filler = ApplicationFiller(adapter_override=mock_adapter)
        with pytest.raises(RuntimeError, match="no durable approval"):
            _execute_claimed(filler, db_session, app)

        mock_adapter.submit_application.assert_not_called()

    def test_execute_browser_submission_fails_closed_without_claim(self, db_session: Session):
        """execute_browser_submission must raise RuntimeError if submission_claimed_at is None."""
        opp = _make_opportunity(db_session, "inv1_no_claim")
        app = _make_form_filled_app(db_session, opp)
        app.approved_at = datetime.now(timezone.utc)
        app.approved_by = "human"
        app.submission_claimed_at = None
        db_session.commit()

        mock_adapter = MagicMock()
        filler = ApplicationFiller(adapter_override=mock_adapter)
        with pytest.raises(RuntimeError, match="must be claimed"):
            filler.execute_browser_submission(db_session, app.id)

        mock_adapter.submit_application.assert_not_called()


# == Class 10: Invariant 2 - Approved Form Correspondence ======================

class TestInvariant2ApprovedFormCorrespondence:
    """Invariant 2: Reconstructed form must correspond to the application the human approved.
    Any mismatch in:
      - profile data
      - resume selection
      - stored custom answers
      - opportunity URL
      - relevant form structure (questions)
      - approved input digest
    must fail closed to fresh human review (MANUAL_APPLICATION_REQUIRED) and
    MUST NOT call submit_application().
    """

    def test_mismatch_opportunity_url_fails_closed(self, db_session: Session):
        opp = _make_opportunity(db_session, "inv2_url")
        app = _make_form_filled_app(db_session, opp)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)

        # Mutate URL between approval and reconstruction
        opp.url = "https://unstop.com/hacked-url"
        db_session.commit()

        runner = WorkerRunner()
        runner.claim_application_for_submission(db_session, app.id, worker_id="w_inv2")

        mock_adapter, _ = _mock_adapter()
        filler = ApplicationFiller(adapter_override=mock_adapter)
        res = _execute_claimed(filler, db_session, app)

        assert res["success"] is False
        assert res["status"] == "manual_required"
        assert "Opportunity URL mismatch" in res["reason"]
        mock_adapter.submit_application.assert_not_called()

        db_session.refresh(opp)
        assert opp.status == OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value

    def test_mismatch_resume_selection_fails_closed(self, db_session: Session):
        from core.models.profile import Profile
        from core.models.resume import Resume

        profile = Profile(name="Resume Tester", email="res@test.com")
        db_session.add(profile)
        db_session.flush()

        r1 = Resume(profile_id=profile.id, version=1, file_path="res1.pdf", original_filename="res1.pdf", file_size_bytes=1024)
        r2 = Resume(profile_id=profile.id, version=2, file_path="res2.pdf", original_filename="res2.pdf", file_size_bytes=1024)
        db_session.add_all([r1, r2])
        db_session.commit()

        opp = _make_opportunity(db_session, "inv2_resume")
        opp.selected_resume_id = r1.id
        db_session.commit()

        app = _make_form_filled_app(db_session, opp)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)

        # Mutate selected resume ID between approval and reconstruction
        opp.selected_resume_id = r2.id
        db_session.commit()

        runner = WorkerRunner()
        runner.claim_application_for_submission(db_session, app.id, worker_id="w_inv2")

        mock_adapter, _ = _mock_adapter()
        filler = ApplicationFiller(adapter_override=mock_adapter)
        res = _execute_claimed(filler, db_session, app)

        assert res["success"] is False
        assert res["status"] == "manual_required"
        assert "Resume selection mismatch" in res["reason"]
        mock_adapter.submit_application.assert_not_called()

    def test_mismatch_stored_custom_answers_fails_closed(self, db_session: Session):
        opp = _make_opportunity(db_session, "inv2_answers")
        app = _make_form_filled_app(db_session, opp)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)

        # Tamper stored custom answers in notes
        notes_data = json.loads(app.notes)
        notes_data["custom_answers"] = [{"question_id": "q1", "answer": "Tampered!"}]
        app.notes = json.dumps(notes_data)
        db_session.commit()

        runner = WorkerRunner()
        runner.claim_application_for_submission(db_session, app.id, worker_id="w_inv2")

        mock_adapter, _ = _mock_adapter()
        filler = ApplicationFiller(adapter_override=mock_adapter)
        res = _execute_claimed(filler, db_session, app)

        assert res["success"] is False
        assert res["status"] == "manual_required"
        assert "custom answers mismatch" in res["reason"].lower()
        mock_adapter.submit_application.assert_not_called()

    def test_mismatch_form_structure_questions_fails_closed(self, db_session: Session):
        opp = _make_opportunity(db_session, "inv2_struct")
        app = _make_form_filled_app(db_session, opp)
        # Store snapshot with expected questions ["Why Acme?"]
        notes_data = json.loads(app.notes)
        notes_data["form_questions"] = ["Why Acme?"]
        app.notes = json.dumps(notes_data)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)

        runner = WorkerRunner()
        runner.claim_application_for_submission(db_session, app.id, worker_id="w_inv2")

        # Mock adapter returns live page with DIFFERENT questions ["What is your GPA?", "Why Acme?"]
        mock_adapter, ctx = _mock_adapter()
        ctx.extracted = ExtractedListing(
            company="Acme Corp",
            title="Software Engineer Intern",
            url="https://unstop.com/hackathon/test",
            custom_questions=[{"id": "q2", "label": "What is your GPA?"}],
        )
        mock_adapter.open_application.return_value = ctx
        mock_adapter.extract.return_value = ctx.extracted

        filler = ApplicationFiller(adapter_override=mock_adapter)
        res = _execute_claimed(filler, db_session, app)

        assert res["success"] is False
        assert res["status"] == "manual_required"
        assert "Form structure mismatch" in res["reason"]
        mock_adapter.submit_application.assert_not_called()

    def test_mismatch_profile_data_fails_closed(self, db_session: Session):
        from core.models.profile import Profile
        # Create profile and assign to opportunity
        profile = Profile(
            name="Alice Candidate",
            email="alice@example.com",
            phone="1234567890",
        )
        db_session.add(profile)
        db_session.commit()

        opp = _make_opportunity(db_session, "inv2_profile")
        opp.profile_id = profile.id
        db_session.commit()

        app = _make_form_filled_app(db_session, opp)
        # Store approved snapshot with Alice's profile
        from worker.engine.filler import serialize_profile
        alice_data = serialize_profile(profile)
        notes_data = json.loads(app.notes)
        notes_data["approved_input_snapshot"] = {
            "url": opp.url,
            "resume_id": None,
            "candidate_data": alice_data,
            "custom_answers": notes_data.get("custom_answers", []),
            "form_questions": [],
        }
        app.notes = json.dumps(notes_data)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)

        # Mutate profile in DB before worker reconstruction
        profile.name = "Bob Candidate"
        db_session.commit()

        runner = WorkerRunner()
        runner.claim_application_for_submission(db_session, app.id, worker_id="w_inv2")

        mock_adapter, _ = _mock_adapter()
        filler = ApplicationFiller(adapter_override=mock_adapter)
        res = _execute_claimed(filler, db_session, app)

        assert res["success"] is False
        assert res["status"] == "manual_required"
        assert "Candidate profile data mismatch" in res["reason"]
        mock_adapter.submit_application.assert_not_called()

    def test_same_resume_id_changed_file_bytes_fails_closed(
        self, db_session: Session, tmp_path: Path
    ):
        """Same resume ID but changed file bytes fails closed and does not call adapter."""
        from core.models.profile import Profile
        from worker.engine.filler import serialize_profile

        profile = Profile(name="Resume Bytes Tester", email="bytes@test.com")
        db_session.add(profile)
        db_session.commit()

        resume_file = tmp_path / "resume_tamper.pdf"
        resume = _make_resume_with_content(
            db_session, profile.id, resume_file, b"%PDF-1.4 original bytes"
        )
        approved_hash = compute_file_sha256(resume_file)

        opp = _make_opportunity(db_session, "inv2_changed_bytes")
        opp.profile_id = profile.id
        opp.selected_resume_id = resume.id
        db_session.commit()

        app = _make_form_filled_app(db_session, opp)
        notes_data = json.loads(app.notes)
        notes_data["approved_input_snapshot"] = {
            "url": opp.url,
            "resume_id": resume.id,
            "resume_content_sha256": approved_hash,
            "candidate_profile_sha256": compute_profile_sha256(serialize_profile(profile)),
            "custom_answers": notes_data.get("custom_answers", []),
            "form_questions": [],
        }
        app.notes = json.dumps(notes_data)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)

        # Mutate file content on disk AFTER approval
        resume_file.write_bytes(b"%PDF-1.4 TAMPERED bytes")

        runner = WorkerRunner()
        runner.claim_application_for_submission(db_session, app.id, worker_id="w_inv2")

        mock_adapter, _ = _mock_adapter()
        filler = ApplicationFiller(adapter_override=mock_adapter)

        count_before = db_session.query(Application).count()
        res = _execute_claimed(filler, db_session, app)

        assert res["success"] is False
        assert res["status"] == "manual_required"
        assert "Resume content mismatch" in res["reason"]
        mock_adapter.fill.assert_not_called()
        mock_adapter.submit_application.assert_not_called()

        # Invariant: no duplicate Application row created
        assert db_session.query(Application).count() == count_before
        db_session.refresh(opp)
        assert opp.status == OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value

    def test_same_resume_id_unchanged_bytes_succeeds(
        self, db_session: Session, tmp_path: Path
    ):
        """Same resume ID and unchanged file bytes succeeds and calls adapter."""
        from core.models.profile import Profile
        from worker.engine.filler import serialize_profile

        profile = Profile(name="Resume Success Tester", email="success@test.com")
        db_session.add(profile)
        db_session.commit()

        resume_file = tmp_path / "resume_valid.pdf"
        resume = _make_resume_with_content(
            db_session, profile.id, resume_file, b"%PDF-1.4 authentic content"
        )
        approved_hash = compute_file_sha256(resume_file)

        opp = _make_opportunity(db_session, "inv2_unchanged_bytes")
        opp.profile_id = profile.id
        opp.selected_resume_id = resume.id
        db_session.commit()

        app = _make_form_filled_app(db_session, opp)
        notes_data = json.loads(app.notes)
        notes_data["approved_input_snapshot"] = {
            "url": opp.url,
            "resume_id": resume.id,
            "resume_content_sha256": approved_hash,
            "candidate_profile_sha256": compute_profile_sha256(serialize_profile(profile)),
            "custom_answers": notes_data.get("custom_answers", []),
            "form_questions": [],
        }
        app.notes = json.dumps(notes_data)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)

        runner = WorkerRunner()
        runner.claim_application_for_submission(db_session, app.id, worker_id="w_inv2")

        mock_adapter, _ = _mock_adapter()
        mock_adapter.submit_application.return_value = {
            "success": True,
            "confirmation_ref": "CONF-RESUME-OK",
        }
        filler = ApplicationFiller(adapter_override=mock_adapter)

        count_before = db_session.query(Application).count()
        res = _execute_claimed(filler, db_session, app)

        assert res["success"] is True
        assert res["status"] == "applied"
        mock_adapter.fill.assert_called_once()
        mock_adapter.submit_application.assert_called_once()

        # Invariant: no duplicate Application row created
        assert db_session.query(Application).count() == count_before
        db_session.refresh(opp)
        assert opp.status == OpportunityStatus.APPLIED.value

    def test_missing_approved_resume_file_fails_closed(
        self, db_session: Session, tmp_path: Path
    ):
        """Missing approved resume file fails closed and does not call adapter."""
        from core.models.profile import Profile
        from worker.engine.filler import serialize_profile

        profile = Profile(name="Missing Resume Tester", email="missing@test.com")
        db_session.add(profile)
        db_session.commit()

        resume_file = tmp_path / "resume_to_delete.pdf"
        resume = _make_resume_with_content(
            db_session, profile.id, resume_file, b"%PDF-1.4 delete me"
        )
        approved_hash = compute_file_sha256(resume_file)

        opp = _make_opportunity(db_session, "inv2_missing_file")
        opp.profile_id = profile.id
        opp.selected_resume_id = resume.id
        db_session.commit()

        app = _make_form_filled_app(db_session, opp)
        notes_data = json.loads(app.notes)
        notes_data["approved_input_snapshot"] = {
            "url": opp.url,
            "resume_id": resume.id,
            "resume_content_sha256": approved_hash,
            "candidate_profile_sha256": compute_profile_sha256(serialize_profile(profile)),
            "custom_answers": notes_data.get("custom_answers", []),
            "form_questions": [],
        }
        app.notes = json.dumps(notes_data)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)

        # Delete the resume file from disk
        resume_file.unlink()

        runner = WorkerRunner()
        runner.claim_application_for_submission(db_session, app.id, worker_id="w_inv2")

        mock_adapter, _ = _mock_adapter()
        filler = ApplicationFiller(adapter_override=mock_adapter)

        res = _execute_claimed(filler, db_session, app)

        assert res["success"] is False
        assert res["status"] == "manual_required"
        assert "not found" in res["reason"].lower()
        mock_adapter.fill.assert_not_called()
        mock_adapter.submit_application.assert_not_called()

    def test_unreadable_approved_resume_fails_closed(
        self, db_session: Session, tmp_path: Path
    ):
        """Unreadable approved resume fails closed and does not call adapter."""
        from core.models.profile import Profile
        from worker.engine.filler import serialize_profile

        profile = Profile(name="Unreadable Resume Tester", email="unreadable@test.com")
        db_session.add(profile)
        db_session.commit()

        resume_file = tmp_path / "resume_unreadable.pdf"
        resume = _make_resume_with_content(
            db_session, profile.id, resume_file, b"%PDF-1.4 unreadable test"
        )
        approved_hash = compute_file_sha256(resume_file)

        opp = _make_opportunity(db_session, "inv2_unreadable_file")
        opp.profile_id = profile.id
        opp.selected_resume_id = resume.id
        db_session.commit()

        app = _make_form_filled_app(db_session, opp)
        notes_data = json.loads(app.notes)
        notes_data["approved_input_snapshot"] = {
            "url": opp.url,
            "resume_id": resume.id,
            "resume_content_sha256": approved_hash,
            "candidate_profile_sha256": compute_profile_sha256(serialize_profile(profile)),
            "custom_answers": notes_data.get("custom_answers", []),
            "form_questions": [],
        }
        app.notes = json.dumps(notes_data)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)

        runner = WorkerRunner()
        runner.claim_application_for_submission(db_session, app.id, worker_id="w_inv2")

        mock_adapter, _ = _mock_adapter()
        filler = ApplicationFiller(adapter_override=mock_adapter)

        with patch("worker.engine.filler.compute_file_sha256", side_effect=PermissionError("Permission denied")):
            res = _execute_claimed(filler, db_session, app)

        assert res["success"] is False
        assert res["status"] == "manual_required"
        assert "unreadable" in res["reason"].lower() or "permission denied" in res["reason"].lower()
        mock_adapter.fill.assert_not_called()
        mock_adapter.submit_application.assert_not_called()

    def test_changing_resume_path_fails_closed(
        self, db_session: Session, tmp_path: Path
    ):
        """Changing resume.file_path to point to a different file fails closed."""
        from core.models.profile import Profile
        from worker.engine.filler import serialize_profile

        profile = Profile(name="Path Tamper Tester", email="path@test.com")
        db_session.add(profile)
        db_session.commit()

        r1_file = tmp_path / "r1.pdf"
        r1 = _make_resume_with_content(db_session, profile.id, r1_file, b"%PDF-1.4 r1 approved")
        r1_hash = compute_file_sha256(r1_file)

        r2_file = tmp_path / "r2.pdf"
        _make_resume_with_content(db_session, profile.id, r2_file, b"%PDF-1.4 r2 other")

        opp = _make_opportunity(db_session, "inv2_path_tamper")
        opp.profile_id = profile.id
        opp.selected_resume_id = r1.id
        db_session.commit()

        app = _make_form_filled_app(db_session, opp)
        notes_data = json.loads(app.notes)
        notes_data["approved_input_snapshot"] = {
            "url": opp.url,
            "resume_id": r1.id,
            "resume_content_sha256": r1_hash,
            "candidate_profile_sha256": compute_profile_sha256(serialize_profile(profile)),
            "custom_answers": notes_data.get("custom_answers", []),
            "form_questions": [],
        }
        app.notes = json.dumps(notes_data)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)

        # Changing r1's file_path to point to r2's file fails closed
        r1.file_path = str(r2_file)
        db_session.commit()

        runner = WorkerRunner()
        runner.claim_application_for_submission(db_session, app.id, worker_id="w_inv2")

        mock_adapter, _ = _mock_adapter()
        filler = ApplicationFiller(adapter_override=mock_adapter)

        res = _execute_claimed(filler, db_session, app)
        assert res["success"] is False
        assert res["status"] == "manual_required"
        assert "Resume content mismatch" in res["reason"]
        mock_adapter.fill.assert_not_called()
        mock_adapter.submit_application.assert_not_called()

    def test_fallback_resume_selection_cannot_bypass_approved_check(
        self, db_session: Session, tmp_path: Path
    ):
        """Mutating selected resume ID or clearing it to trigger fallback fails closed."""
        from core.models.profile import Profile
        from worker.engine.filler import serialize_profile

        profile = Profile(name="Fallback Tester", email="fallback@test.com")
        db_session.add(profile)
        db_session.commit()

        r1_file = tmp_path / "r1_fb.pdf"
        r1 = _make_resume_with_content(db_session, profile.id, r1_file, b"%PDF-1.4 r1 approved")
        r1_hash = compute_file_sha256(r1_file)

        r2_file = tmp_path / "r2_fb.pdf"
        r2 = _make_resume_with_content(db_session, profile.id, r2_file, b"%PDF-1.4 r2 fallback")

        opp = _make_opportunity(db_session, "inv2_fallback")
        opp.profile_id = profile.id
        opp.selected_resume_id = r1.id
        db_session.commit()

        app = _make_form_filled_app(db_session, opp)
        notes_data = json.loads(app.notes)
        notes_data["approved_input_snapshot"] = {
            "url": opp.url,
            "resume_id": r1.id,
            "resume_content_sha256": r1_hash,
            "candidate_profile_sha256": compute_profile_sha256(serialize_profile(profile)),
            "custom_answers": notes_data.get("custom_answers", []),
            "form_questions": [],
        }
        app.notes = json.dumps(notes_data)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)

        # Mutate selected_resume_id to r2 to attempt fallback bypass
        opp.selected_resume_id = r2.id
        db_session.commit()

        runner = WorkerRunner()
        runner.claim_application_for_submission(db_session, app.id, worker_id="w_inv2")

        mock_adapter, _ = _mock_adapter()
        filler = ApplicationFiller(adapter_override=mock_adapter)

        res = _execute_claimed(filler, db_session, app)
        assert res["success"] is False
        assert res["status"] == "manual_required"
        assert "Resume selection mismatch" in res["reason"]
        mock_adapter.fill.assert_not_called()
        mock_adapter.submit_application.assert_not_called()

    def test_resume_mismatch_results_in_zero_fill_and_submit_calls(
        self, db_session: Session, tmp_path: Path
    ):
        """Resume mismatch strictly results in zero adapter.fill() and submit_application() calls."""
        from core.models.profile import Profile
        from worker.engine.filler import serialize_profile

        profile = Profile(name="Zero Calls Tester", email="zero@test.com")
        db_session.add(profile)
        db_session.commit()

        resume_file = tmp_path / "resume_zero.pdf"
        resume = _make_resume_with_content(
            db_session, profile.id, resume_file, b"%PDF-1.4 initial bytes"
        )
        approved_hash = compute_file_sha256(resume_file)

        opp = _make_opportunity(db_session, "inv2_zero_calls")
        opp.profile_id = profile.id
        opp.selected_resume_id = resume.id
        db_session.commit()

        app = _make_form_filled_app(db_session, opp)
        notes_data = json.loads(app.notes)
        notes_data["approved_input_snapshot"] = {
            "url": opp.url,
            "resume_id": resume.id,
            "resume_content_sha256": approved_hash,
            "candidate_profile_sha256": compute_profile_sha256(serialize_profile(profile)),
            "custom_answers": notes_data.get("custom_answers", []),
            "form_questions": [],
        }
        app.notes = json.dumps(notes_data)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()
        confirm_and_submit(db_session, app.id, approval_token=token)

        # Tamper bytes
        resume_file.write_bytes(b"%PDF-1.4 TAMPERED")

        runner = WorkerRunner()
        runner.claim_application_for_submission(db_session, app.id, worker_id="w_inv2")

        mock_adapter, _ = _mock_adapter()
        filler = ApplicationFiller(adapter_override=mock_adapter)

        _execute_claimed(filler, db_session, app)

        assert mock_adapter.fill.call_count == 0
        assert mock_adapter.submit_application.call_count == 0

    def test_sensitive_candidate_data_not_duplicated_into_notes(
        self, db_session: Session, tmp_path: Path
    ):
        """Sensitive candidate PII is not duplicated into API-visible notes; hash-only satisfies invariant."""
        from core.models.profile import Profile
        from worker.engine.filler import serialize_profile

        profile = Profile(
            name="Privacy Candidate",
            full_name="Secret Candidate Name",
            email="secret.candidate@confidential.com",
            phone="+1-555-867-5309",
            location="Secret City, CA",
        )
        db_session.add(profile)
        db_session.commit()

        resume_file = tmp_path / "privacy_resume.pdf"
        resume = _make_resume_with_content(
            db_session, profile.id, resume_file, b"%PDF-1.4 confidential resume"
        )
        approved_hash = compute_file_sha256(resume_file)

        opp = _make_opportunity(db_session, "inv2_privacy")
        opp.profile_id = profile.id
        opp.selected_resume_id = resume.id
        db_session.commit()

        app = _make_form_filled_app(db_session, opp)

        # Simulate legacy client or drafter that passed raw candidate_data in snapshot
        alice_data = serialize_profile(profile)
        notes_data = json.loads(app.notes)
        notes_data["approved_input_snapshot"] = {
            "url": opp.url,
            "resume_id": resume.id,
            "resume_content_sha256": approved_hash,
            "candidate_data": alice_data,
            "custom_answers": notes_data.get("custom_answers", []),
            "form_questions": [],
        }
        app.notes = json.dumps(notes_data)
        token = issue_approval_token(db_session, app.id)
        db_session.commit()

        # confirm_and_submit must purge candidate_data and replace with candidate_profile_sha256
        confirm_and_submit(db_session, app.id, approval_token=token)

        db_session.refresh(app)
        saved_notes = json.loads(app.notes)
        snapshot = saved_notes["approved_input_snapshot"]

        # 1. candidate_profile_sha256 MUST be present as a 64-character hex string
        assert "candidate_profile_sha256" in snapshot
        assert len(snapshot["candidate_profile_sha256"]) == 64

        # 2. Plaintext candidate_data MUST NOT be present
        assert "candidate_data" not in snapshot

        # 3. Sensitive candidate PII must not appear in the serialized snapshot or notes
        notes_str = app.notes
        assert "secret.candidate@confidential.com" not in notes_str
        assert "+1-555-867-5309" not in notes_str
        assert "Secret Candidate Name" not in notes_str
        assert "Secret City, CA" not in notes_str


