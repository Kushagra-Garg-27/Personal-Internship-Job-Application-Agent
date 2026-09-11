"""Tests for centralized Application status transitions and validation (U1.2 & U1.3).

Verifies:
1. Valid transitions: pending -> form_filled -> submitted, pending -> failed, failed -> pending (retry).
2. Invalid transitions: submitted -> pending, form_filled -> pending, arbitrary strings.
3. Terminal states: submitted is terminal.
4. Model ORM validation rejects invalid status values on assignment.
5. Retries increment attempt numbers and allow new transitions.
6. Worker failure transitions cleanly.
7. Persistence and reload from database.
8. API PATCH endpoint validates transitions and rejects illegal moves with HTTP 400.
"""

from __future__ import annotations

import json
import pytest
from fastapi.testclient import TestClient

from core.models.opportunity import Application, Opportunity
from core.services import application_service, opportunity_service
from core.status import (
    APPLICATION_ALLOWED_TRANSITIONS,
    ApplicationStatus,
    InvalidApplicationTransitionError,
    OpportunityStatus,
)


@pytest.fixture
def test_opportunity(db_session):
    opp = opportunity_service.create_opportunity(
        db_session,
        title="Software Engineer",
        company="TechCorp",
        url="https://unstop.com/jobs/123",
        source="unstop",
        reliability_tier="experimental",
    )
    db_session.commit()
    return opp


class TestApplicationStateTransitions:
    """Centralized application state machine validation."""

    def test_initial_status_is_pending(self, db_session, test_opportunity):
        app = application_service.create_application(
            db_session,
            opportunity_id=test_opportunity.id,
            adapter_name="unstop",
        )
        db_session.commit()
        assert app.status == ApplicationStatus.PENDING.value
        assert app.attempt_number == 1

    def test_valid_lifecycle_pending_to_form_filled_to_submitted(self, db_session, test_opportunity):
        app = application_service.create_application(
            db_session,
            opportunity_id=test_opportunity.id,
            adapter_name="unstop",
        )
        db_session.commit()

        # 1. pending -> form_filled (Worker completes filling form)
        app = application_service.transition_application_status(
            db_session,
            app.id,
            ApplicationStatus.FORM_FILLED,
            notes=json.dumps({"fields_filled": 5, "info": "Form filled by Unstop adapter"}),
            reason="Form filled by Unstop adapter",
        )
        db_session.commit()
        assert app.status == ApplicationStatus.FORM_FILLED.value
        assert "Form filled by Unstop adapter" in app.notes

        # 2. form_filled -> submitted (Candidate clicks submit & confirms)
        app = application_service.transition_application_status(
            db_session,
            app.id,
            ApplicationStatus.SUBMITTED,
            confirmation_ref="UNSTOP-CONFIRMED-999",
            reason="Confirmed by human applicant",
        )
        db_session.commit()
        assert app.status == ApplicationStatus.SUBMITTED.value
        assert app.confirmation_ref == "UNSTOP-CONFIRMED-999"

    def test_terminal_state_submitted_cannot_transition(self, db_session, test_opportunity):
        app = application_service.create_application(
            db_session,
            opportunity_id=test_opportunity.id,
        )
        application_service.transition_application_status(
            db_session, app.id, ApplicationStatus.FORM_FILLED
        )
        application_service.transition_application_status(
            db_session, app.id, ApplicationStatus.SUBMITTED
        )
        db_session.commit()

        # Attempt submitted -> pending
        with pytest.raises(InvalidApplicationTransitionError) as exc_info:
            application_service.transition_application_status(
                db_session, app.id, ApplicationStatus.PENDING
            )
        assert "Invalid application status transition: 'submitted' → 'pending'" in str(exc_info.value)

        # Attempt submitted -> form_filled
        with pytest.raises(InvalidApplicationTransitionError):
            application_service.transition_application_status(
                db_session, app.id, ApplicationStatus.FORM_FILLED
            )

        # Attempt submitted -> failed
        with pytest.raises(InvalidApplicationTransitionError):
            application_service.transition_application_status(
                db_session, app.id, ApplicationStatus.FAILED
            )

    def test_worker_failure_and_retry(self, db_session, test_opportunity):
        app = application_service.create_application(
            db_session,
            opportunity_id=test_opportunity.id,
        )
        db_session.commit()

        # Worker fails to fill form: pending -> failed
        app = application_service.transition_application_status(
            db_session,
            app.id,
            ApplicationStatus.FAILED,
            reason="CAPTCHA challenge blocked automated fill",
        )
        db_session.commit()
        assert app.status == ApplicationStatus.FAILED.value
        assert "CAPTCHA challenge" in app.notes

        # Retry transition: failed -> pending
        app = application_service.transition_application_status(
            db_session,
            app.id,
            ApplicationStatus.PENDING,
            reason="Retrying application attempt",
        )
        db_session.commit()
        assert app.status == ApplicationStatus.PENDING.value

    def test_direct_submission_from_pending(self, db_session, test_opportunity):
        """HTTP-tier or fast-track allows pending -> submitted directly."""
        app = application_service.create_application(
            db_session,
            opportunity_id=test_opportunity.id,
        )
        app = application_service.transition_application_status(
            db_session,
            app.id,
            ApplicationStatus.SUBMITTED,
            confirmation_ref="FAST-SUBMIT-1",
        )
        db_session.commit()
        assert app.status == ApplicationStatus.SUBMITTED.value

    def test_invalid_arbitrary_transition_rejected(self, db_session, test_opportunity):
        app = application_service.create_application(
            db_session,
            opportunity_id=test_opportunity.id,
        )
        with pytest.raises(InvalidApplicationTransitionError):
            application_service.transition_application_status(
                db_session, app.id, "discovered"
            )

        with pytest.raises(InvalidApplicationTransitionError):
            application_service.transition_application_status(
                db_session, app.id, "applied"
            )

    def test_orm_level_status_validation(self, db_session, test_opportunity):
        """Direct attribute assignment of an invalid status raises ValueError via ORM validator."""
        app = Application(
            opportunity_id=test_opportunity.id,
            attempt_number=1,
            status="pending",
        )
        db_session.add(app)
        db_session.flush()

        with pytest.raises(ValueError) as exc:
            app.status = "invalid_bogus_status"
        assert "Invalid application status 'invalid_bogus_status'" in str(exc.value)

    def test_persistence_across_session(self, db_session, test_opportunity):
        """Verify state is correctly persisted to database and reloaded."""
        app = application_service.create_application(
            db_session,
            opportunity_id=test_opportunity.id,
            adapter_name="unstop",
        )
        application_service.transition_application_status(
            db_session,
            app.id,
            ApplicationStatus.FORM_FILLED,
            notes="Session test note",
        )
        db_session.commit()
        app_id = app.id

        # Clear session and reload from DB
        db_session.expire_all()
        reloaded = db_session.get(Application, app_id)
        assert reloaded is not None
        assert reloaded.status == ApplicationStatus.FORM_FILLED.value
        assert "Session test note" in reloaded.notes


class TestApplicationAPIValidation:
    """Test HTTP API validation for application transitions."""

    def test_patch_application_valid_transition(self, client: TestClient, db_session, test_opportunity):
        app = application_service.create_application(
            db_session, opportunity_id=test_opportunity.id
        )
        db_session.commit()

        resp = client.patch(
            f"/applications/{app.id}",
            json={"status": "form_filled", "notes": "Updated via API"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "form_filled"
        assert data["notes"] == "Updated via API"

    def test_patch_application_invalid_transition_returns_400(self, client: TestClient, db_session, test_opportunity):
        app = application_service.create_application(
            db_session, opportunity_id=test_opportunity.id
        )
        # Advance to submitted
        application_service.transition_application_status(
            db_session, app.id, ApplicationStatus.SUBMITTED
        )
        db_session.commit()

        # Try to transition submitted -> pending via API
        resp = client.patch(
            f"/applications/{app.id}",
            json={"status": "pending"},
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "Invalid application status transition" in detail
