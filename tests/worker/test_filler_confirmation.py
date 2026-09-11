"""Unit tests for human confirmation mechanics across both tiers (Phase 9).

Verifies the system invariant:
"Never autonomously submits; explicit human authorization is required."
- Experimental browser tier: Human physically clicks submit in browser, then confirms state.
- Stable HTTP tier: Human authorizes programmatic execution of the pending draft payload.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from core.models.opportunity import Application, Opportunity
from core.services import application_service, opportunity_service
from core.status import ApplicationStatus, OpportunityStatus
from worker.adapters.greenhouse import GreenhouseAdapter
from worker.engine.filler import ApplicationFiller


def test_confirm_submit_stable_http_tier(db_session):
    """Stable HTTP tier: Human confirms, authorizing programmatic HTTP POST submission."""
    opp = opportunity_service.create_opportunity(
        db_session,
        title="Software Engineer",
        company="Stripe",
        url="https://boards.greenhouse.io/stripe/jobs/777",
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
            "submission_url": "https://boards-api.greenhouse.io/v1/boards/stripe/jobs/777",
            "fields": {"first_name": "Dev"},
        },
    }
    application_service.transition_application_status(
        db_session,
        app.id,
        ApplicationStatus.FORM_FILLED,
        notes=json.dumps(draft_notes),
    )
    db_session.commit()

    filler = ApplicationFiller()
    with patch.object(GreenhouseAdapter, "execute_submission") as mock_exec:
        mock_exec.return_value = {
            "success": True,
            "confirmation_ref": "STRIPE-GH-1234",
            "status_code": 200,
        }

        result = filler.confirm_and_submit(db_session, app.id)

        assert result["success"] is True
        assert result["status"] == "applied"
        assert result["confirmation_ref"] == "STRIPE-GH-1234"
        mock_exec.assert_called_once()

    db_session.refresh(opp)
    assert opp.status == OpportunityStatus.APPLIED.value
    db_session.refresh(app)
    assert app.status == "submitted"


def test_confirm_submit_experimental_browser_tier(db_session):
    """Experimental browser tier: Human physically clicked submit in browser, confirms state."""
    opp = opportunity_service.create_opportunity(
        db_session,
        title="Web Developer Intern",
        company="InnoTech",
        url="https://internshala.com/internship/detail/innotech-123",
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

    filler = ApplicationFiller()
    # No HTTP call made; updates and confirms state
    result = filler.confirm_and_submit(db_session, app.id)

    assert result["success"] is True
    assert result["status"] == "applied"
    assert result["mode"] == "browser_confirmed"
    assert "INTERNSHALA" in result["confirmation_ref"]

    db_session.refresh(opp)
    assert opp.status == OpportunityStatus.APPLIED.value
    db_session.refresh(app)
    assert app.status == "submitted"
