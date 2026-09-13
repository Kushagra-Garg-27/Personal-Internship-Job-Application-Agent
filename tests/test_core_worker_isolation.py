"""Tests ensuring Core has zero import-time or call-time dependency on Worker (M1).

Verifies:
1. Core (api.main, routers, services) can be imported and executed in an environment
   where `worker` is completely absent from the Python path.
2. The `/applications/{id}/confirm-submit` endpoint completes successfully without `worker`.
3. The M1 approval flow (request-approval-token → confirm-submit) works end-to-end
   through the HTTP layer.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from core.models.opportunity import Application, Opportunity
from core.services import application_service, opportunity_service
from core.services.submission_service import issue_approval_token
from core.status import (
    ApplicationStatus,
    OpportunityStatus,
)


def _make_approval_body(token: str) -> dict:
    """Build confirm-submit payload for M1 tests."""
    return {
        "approval_token": token,
        "approved_by": "human_user",
    }


def test_core_imports_without_worker(monkeypatch):
    """Confirm api.main and core modules can be imported with worker masked out."""
    monkeypatch.setitem(sys.modules, "worker", None)
    monkeypatch.setitem(sys.modules, "worker.engine", None)
    monkeypatch.setitem(sys.modules, "worker.engine.filler", None)
    monkeypatch.setitem(sys.modules, "worker.adapters", None)

    import api.main
    assert api.main.app is not None


def test_request_approval_token_endpoint(client: TestClient, db_session):
    """POST request-approval-token mints a server-issued UUID (M1)."""
    opp = opportunity_service.create_opportunity(
        db_session,
        title="Token Test Internship",
        company="TokenCo",
        url="https://internshala.com/job/tok1",
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
        db_session, app.id, ApplicationStatus.FORM_FILLED
    )
    db_session.commit()

    resp = client.post(f"/applications/{app.id}/request-approval-token")
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    assert len(data["token"]) >= 32
    assert "expires_at" in data

    # Token must be persisted in DB
    db_session.refresh(app)
    assert app.approval_token == data["token"]
    assert app.approval_token_expires_at is not None


def test_confirm_submit_endpoint_without_worker(client: TestClient, db_session, monkeypatch):
    """POST confirm-submit executes without worker package present (M1 token flow)."""
    opp = opportunity_service.create_opportunity(
        db_session,
        title="Software Engineer",
        company="TechCorp",
        url="https://internshala.com/job/123",
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

    # M1: issue a server-side token first
    token = issue_approval_token(db_session, app.id)
    db_session.commit()

    # Mask worker out completely
    monkeypatch.setitem(sys.modules, "worker", None)
    monkeypatch.setitem(sys.modules, "worker.engine", None)
    monkeypatch.setitem(sys.modules, "worker.engine.filler", None)

    resp = client.post(
        f"/applications/{app.id}/confirm-submit",
        json={
            **_make_approval_body(token),
            "platform_confirmed": True,
            "confirmation_ref": "BROWSER-INTERNSHALA-CONFIRMED",
            "confirmation_detail": "Internshala showed 'Application submitted'.",
        },
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["success"] is True
    assert data["status"] == "approved_for_submission"
    assert data["mode"] == "browser_orchestrator"
    assert "Background worker will perform submission" in data["message"]

    # M1: approval stored in columns, not notes JSON
    db_session.refresh(opp)
    assert opp.status == OpportunityStatus.AWAITING_SUBMISSION.value
    assert opp.status != OpportunityStatus.APPLIED.value

    db_session.refresh(app)
    assert app.status == ApplicationStatus.FORM_FILLED.value
    assert app.status != ApplicationStatus.SUBMITTED.value
    assert app.submitted_at is None
    assert app.confirmation_ref is None
    # M1: check dedicated columns instead of notes JSON
    assert app.approved_at is not None
    assert app.approved_by == "human_user"


def test_confirm_submit_rejected_without_valid_token(client: TestClient, db_session):
    """Confirm-submit with no prior token issuance returns 403 (fail closed, M1)."""
    opp = opportunity_service.create_opportunity(
        db_session,
        title="Gated Job",
        company="GatedCo",
        url="https://internshala.com/job/gated",
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
        db_session, app.id, ApplicationStatus.FORM_FILLED
    )
    db_session.commit()

    # Submit without requesting a token — must fail closed
    resp = client.post(
        f"/applications/{app.id}/confirm-submit",
        json={"approval_token": "HUMAN_CONFIRMED_SUBMIT", "approved_by": "human_user"},
    )
    assert resp.status_code == 403


def test_confirm_submit_http_tier_without_worker(client: TestClient, db_session, monkeypatch):
    """POST confirm-submit for stable HTTP tier without worker package (M1 token flow)."""
    opp = opportunity_service.create_opportunity(
        db_session,
        title="Backend Engineer",
        company="Stripe",
        url="https://boards.greenhouse.io/stripe/jobs/888",
        source="greenhouse",
        reliability_tier="stable",
    )
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.RECOMMENDED)
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.READY_TO_APPLY)
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.AWAITING_SUBMISSION)

    app = application_service.create_application(
        db_session, opportunity_id=opp.id, adapter_name="greenhouse"
    )
    draft_notes = {
        "adapter": "greenhouse",
        "draft_payload": {
            "submission_url": "https://boards-api.greenhouse.io/v1/boards/stripe/jobs/888",
            "fields": {"first_name": "Alex"},
        },
    }
    application_service.transition_application_status(
        db_session, app.id, ApplicationStatus.FORM_FILLED, notes=json.dumps(draft_notes)
    )
    db_session.commit()

    # M1: mint server token
    token = issue_approval_token(db_session, app.id)
    db_session.commit()

    monkeypatch.setitem(sys.modules, "worker", None)
    monkeypatch.setitem(sys.modules, "worker.engine", None)
    monkeypatch.setitem(sys.modules, "worker.engine.filler", None)

    with patch("core.services.submission_service.execute_greenhouse_submission") as mock_exec:
        mock_exec.return_value = {
            "success": True,
            "confirmation_ref": "STRIPE-GH-888-OK",
            "status_code": 200,
        }

        resp = client.post(
            f"/applications/{app.id}/confirm-submit",
            json=_make_approval_body(token),
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["success"] is True
        assert data["status"] == "applied"
        assert data["confirmation_ref"] == "STRIPE-GH-888-OK"

    db_session.refresh(opp)
    assert opp.status == OpportunityStatus.APPLIED.value
    db_session.refresh(app)
    assert app.status == "submitted"
