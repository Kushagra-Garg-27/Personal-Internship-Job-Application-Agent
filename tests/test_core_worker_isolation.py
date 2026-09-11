"""Tests ensuring Core has zero import-time or call-time dependency on Worker.

Verifies:
1. Core (api.main, routers, services) can be imported and executed in an environment
   where `worker` is completely absent from the Python path.
2. The `/applications/{id}/confirm-submit` endpoint completes successfully without `worker`.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from core.models.opportunity import Application, Opportunity
from core.services import application_service, opportunity_service
from core.status import (
    HUMAN_SUBMISSION_APPROVAL_TOKEN,
    ApplicationStatus,
    OpportunityStatus,
)

APPROVED_BODY = {
    "approval_token": HUMAN_SUBMISSION_APPROVAL_TOKEN,
    "approved_by": "human_user",
}


def test_core_imports_without_worker(monkeypatch):
    """Confirm api.main and core modules can be imported with worker masked out."""
    # Ensure worker is masked so any import attempt raises ModuleNotFoundError
    monkeypatch.setitem(sys.modules, "worker", None)
    monkeypatch.setitem(sys.modules, "worker.engine", None)
    monkeypatch.setitem(sys.modules, "worker.engine.filler", None)
    monkeypatch.setitem(sys.modules, "worker.adapters", None)

    # Re-import api.main cleanly
    import api.main
    assert api.main.app is not None


def test_confirm_submit_endpoint_without_worker(client: TestClient, db_session, monkeypatch):
    """Test POST /applications/{id}/confirm-submit executes without worker package present."""
    # Setup an application in awaiting_submission
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
        db_session,
        opportunity_id=opp.id,
        adapter_name="internshala",
    )
    application_service.transition_application_status(
        db_session,
        app.id,
        ApplicationStatus.FORM_FILLED,
        notes=json.dumps({"adapter": "internshala", "tier": "experimental"}),
    )
    db_session.commit()

    # Mask worker out completely
    monkeypatch.setitem(sys.modules, "worker", None)
    monkeypatch.setitem(sys.modules, "worker.engine", None)
    monkeypatch.setitem(sys.modules, "worker.engine.filler", None)

    # Call the API endpoint with explicit human approval and platform confirmation
    resp = client.post(
        f"/applications/{app.id}/confirm-submit",
        json={
            **APPROVED_BODY,
            "platform_confirmed": True,
            "confirmation_ref": "BROWSER-INTERNSHALA-CONFIRMED",
            "confirmation_detail": "Internshala showed 'Application submitted'.",
        },
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["success"] is True
    assert data["status"] == "applied"
    assert data["mode"] == "browser_confirmed"
    assert data["confirmed"] is True

    db_session.refresh(opp)
    assert opp.status == OpportunityStatus.APPLIED.value
    db_session.refresh(app)
    assert app.status == "submitted"


def test_confirm_submit_http_tier_without_worker(client: TestClient, db_session, monkeypatch):
    """Test POST /applications/{id}/confirm-submit for stable HTTP tier without worker package."""
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
        db_session,
        opportunity_id=opp.id,
        adapter_name="greenhouse",
    )
    draft_notes = {
        "adapter": "greenhouse",
        "draft_payload": {
            "submission_url": "https://boards-api.greenhouse.io/v1/boards/stripe/jobs/888",
            "fields": {"first_name": "Alex"},
        },
    }
    application_service.transition_application_status(
        db_session,
        app.id,
        ApplicationStatus.FORM_FILLED,
        notes=json.dumps(draft_notes),
    )
    db_session.commit()

    # Mask worker out
    monkeypatch.setitem(sys.modules, "worker", None)
    monkeypatch.setitem(sys.modules, "worker.engine", None)
    monkeypatch.setitem(sys.modules, "worker.engine.filler", None)

    with patch("core.services.submission_service.execute_greenhouse_submission") as mock_exec:
        mock_exec.return_value = {
            "success": True,
            "confirmation_ref": "STRIPE-GH-888-OK",
            "status_code": 200,
        }

        resp = client.post(f"/applications/{app.id}/confirm-submit", json=APPROVED_BODY)
        assert resp.status_code == 200
        data = resp.json()

        assert data["success"] is True
        assert data["status"] == "applied"
        assert data["confirmation_ref"] == "STRIPE-GH-888-OK"

    db_session.refresh(opp)
    assert opp.status == OpportunityStatus.APPLIED.value
    db_session.refresh(app)
    assert app.status == "submitted"
