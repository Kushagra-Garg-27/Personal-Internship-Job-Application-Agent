"""Offline M2C corrective tests for claim-bound manual final-action handoff."""

from __future__ import annotations

from datetime import datetime, timedelta
import json

import pytest
from sqlalchemy import create_engine, event, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from unittest.mock import MagicMock, patch

from core.models.base import Base
from core.models.opportunity import Application, Opportunity, StatusHistory
from core.models.profile import Profile
from core.models.resume import Resume
from core.services import application_service
from core.status import ApplicationStatus, OpportunityStatus
from worker.engine.filler import ApplicationFiller


@pytest.fixture()
def session_factory(tmp_path):
    """Independent sessions over one file-backed SQLite database for race tests."""
    engine = create_engine(f"sqlite:///{(tmp_path / 'm2c.db').as_posix()}")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _claimed_application(session: Session, *, suffix: str) -> tuple[Opportunity, Application]:
    profile = Profile(name=f"m2c-profile-{suffix}")
    session.add(profile)
    session.flush()
    resume = Resume(
        profile_id=profile.id,
        version=1,
        file_path=f"/offline/{suffix}.pdf",
        original_filename=f"{suffix}.pdf",
        file_size_bytes=42,
        is_active=True,
    )
    session.add(resume)
    session.flush()

    opp = Opportunity(
        dedup_hash=f"m2c-{suffix}",
        status=OpportunityStatus.AWAITING_SUBMISSION.value,
        reliability_tier="experimental",
        title="Offline role",
        company="Offline company",
        url="https://example.invalid/offline",
    )
    session.add(opp)
    session.flush()

    now = datetime.utcnow()
    app = Application(
        opportunity_id=opp.id,
        resume_id=resume.id,
        attempt_number=7,
        status=ApplicationStatus.FORM_FILLED.value,
        adapter_name="unstop",
        notes=json.dumps({
            "approved_input_snapshot": {"resume_sha256": "digest"},
            "approved_input_digest": "digest",
            "approval_audit": ["approved"],
        }),
        approval_token="preserved-token",
        approval_token_expires_at=now + timedelta(minutes=30),
        approved_at=now,
        approved_by="human-reviewer",
        approval_revoked_at=now - timedelta(seconds=1),
        approval_revoked_by="earlier-revocation",
        approval_revocation_reason="superseded by current approval",
        submission_claimed_at=now,
        claimed_by="worker-a",
        confirmation_ref=None,
        submitted_at=None,
        manual_review_reason=None,
    )
    session.add(app)
    session.commit()
    session.refresh(app)
    return opp, app


def _identity(app: Application) -> dict[str, object]:
    assert app.claimed_by is not None
    assert app.submission_claimed_at is not None
    return {
        "expected_claimed_by": app.claimed_by,
        "expected_submission_claimed_at": app.submission_claimed_at,
    }


def _unrelated_snapshot(app: Application) -> dict[str, object]:
    fields = (
        "approved_at", "approved_by", "approval_token", "approval_token_expires_at",
        "approval_revoked_at", "approval_revoked_by", "approval_revocation_reason",
        "notes", "resume_id", "attempt_number", "adapter_name", "created_at",
        "confirmation_ref", "submitted_at",
    )
    return {field: getattr(app, field) for field in fields}


def test_successful_handoff_changes_only_manual_state_and_releases_claim(session_factory):
    session = session_factory()
    opp, app = _claimed_application(session, suffix="success")
    before = _unrelated_snapshot(app)
    claim = _identity(app)
    history_before = session.scalar(
        select(StatusHistory).where(StatusHistory.opportunity_id == opp.id)
    )
    assert history_before is None

    assert application_service.handoff_claimed_application_to_manual_review(
        session, app.id, reason="ambiguous_next_control", **claim
    ) is True
    session.commit()
    session.refresh(app)
    session.refresh(opp)

    assert app.status == ApplicationStatus.FAILED.value
    assert app.manual_review_reason == "ambiguous_next_control"
    assert app.submission_claimed_at is None
    assert app.claimed_by is None
    assert _unrelated_snapshot(app) == before
    assert opp.status == OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value
    assert session.scalars(
        select(StatusHistory).where(StatusHistory.opportunity_id == opp.id)
    ).all().__len__() == 1

    # A terminal manual row cannot be reclaimed or generate a second history row.
    assert application_service.handoff_claimed_application_to_manual_review(
        session, app.id, reason="ambiguous_next_control", **claim
    ) is False
    session.rollback()
    assert len(session.scalars(
        select(StatusHistory).where(StatusHistory.opportunity_id == opp.id)
    ).all()) == 1
    session.close()


@pytest.mark.parametrize(
    "expected_owner, expected_time, reason",
    [
        ("", datetime.utcnow(), "ambiguous_next_control"),
        ("worker-a", None, "ambiguous_next_control"),
        ("worker-a", "not-a-datetime", "ambiguous_next_control"),
        ("worker-a", datetime.utcnow(), "unbounded-diagnostic"),
    ],
)
def test_handoff_rejects_malformed_identity_or_reason_before_mutation(
    session_factory, expected_owner, expected_time, reason
):
    session = session_factory()
    opp, app = _claimed_application(session, suffix=f"reject-{reason}-{expected_owner or 'empty'}")
    before = _unrelated_snapshot(app)

    with pytest.raises(ValueError):
        application_service.handoff_claimed_application_to_manual_review(
            session,
            app.id,
            reason=reason,
            expected_claimed_by=expected_owner,
            expected_submission_claimed_at=expected_time,
        )
    session.rollback()
    session.refresh(app)
    session.refresh(opp)
    assert app.status == ApplicationStatus.FORM_FILLED.value
    assert app.manual_review_reason is None
    assert app.claimed_by == "worker-a"
    assert _unrelated_snapshot(app) == before
    assert opp.status == OpportunityStatus.AWAITING_SUBMISSION.value
    session.close()


def test_reentering_claimable_state_clears_current_manual_reason(session_factory):
    session = session_factory()
    _opp, app = _claimed_application(session, suffix="retry")
    claim = _identity(app)
    assert application_service.handoff_claimed_application_to_manual_review(
        session, app.id, reason="ambiguous_controls", **claim
    ) is True
    session.commit()

    application_service.transition_application_status(
        session, app.id, ApplicationStatus.PENDING
    )
    session.commit()
    session.refresh(app)
    assert app.status == ApplicationStatus.PENDING.value
    assert app.manual_review_reason is None
    session.close()


def test_stale_session_cannot_adopt_replaced_claim_and_new_owner_handoffs_once(session_factory):
    session_a = session_factory()
    opp, app_a = _claimed_application(session_a, suffix="race")
    claim_a = _identity(app_a)
    preserved = _unrelated_snapshot(app_a)

    session_b = session_factory()
    app_b = session_b.get(Application, app_a.id)
    claim_b_at = datetime.utcnow() + timedelta(seconds=2)
    result = session_b.execute(
        update(Application)
        .where(Application.id == app_b.id, Application.claimed_by == "worker-a")
        .values(claimed_by="worker-b", submission_claimed_at=claim_b_at)
    )
    assert result.rowcount == 1
    session_b.commit()

    assert application_service.handoff_claimed_application_to_manual_review(
        session_a, app_a.id, reason="ambiguous_next_control", **claim_a
    ) is False
    session_a.rollback()

    verify = session_factory()
    unchanged = verify.get(Application, app_a.id)
    unchanged_opp = verify.get(Opportunity, opp.id)
    assert unchanged.claimed_by == "worker-b"
    assert unchanged.submission_claimed_at == claim_b_at
    assert unchanged.status == ApplicationStatus.FORM_FILLED.value
    assert unchanged.manual_review_reason is None
    assert _unrelated_snapshot(unchanged) == preserved
    assert unchanged_opp.status == OpportunityStatus.AWAITING_SUBMISSION.value

    app_b = session_b.get(Application, app_a.id, populate_existing=True)
    claim_b = _identity(app_b)
    assert application_service.handoff_claimed_application_to_manual_review(
        session_b, app_b.id, reason="ambiguous_next_control", **claim_b
    ) is True
    session_b.commit()
    assert application_service.handoff_claimed_application_to_manual_review(
        session_b, app_b.id, reason="ambiguous_next_control", **claim_b
    ) is False
    session_b.rollback()
    assert len(verify.scalars(
        select(StatusHistory).where(StatusHistory.opportunity_id == opp.id)
    ).all()) == 1

    session_a.close()
    session_b.close()
    verify.close()


@pytest.mark.parametrize("failure_target", ["transition", "history_flush", "commit"])
def test_orchestrator_rolls_back_every_partial_handoff_failure(session_factory, failure_target):
    session = session_factory()
    opp, app = _claimed_application(session, suffix=failure_target)
    identity = _identity(app)
    before = _unrelated_snapshot(app)

    adapter = MagicMock(adapter_name="unstop")
    adapter.submit_application.return_value = {
        "success": False,
        "confirmed": False,
        "manual_review_required": True,
        "error": "manual_final_action_required",
        "reason": "ambiguous_next_control",
    }
    filler = ApplicationFiller(adapter_override=adapter)
    failure = SQLAlchemyError("offline injected failure")

    patches = []
    if failure_target == "transition":
        patches.append(patch("core.services.opportunity_service.transition_status", side_effect=failure))
    elif failure_target == "history_flush":
        patches.append(patch("core.repositories.status_history_repo.create_entry", side_effect=failure))
    else:
        patches.append(patch.object(session, "commit", side_effect=failure))

    with patch.object(filler, "reconstruct_form_state", return_value={"success": True, "app_ctx": MagicMock()}):
        with patches[0]:
            result = filler.execute_browser_submission(session, app.id, **identity)

    assert result == {
        "success": False,
        "status": "error",
        "reason": "browser_submission_execution_failed",
    }

    verify = session_factory()
    restored = verify.get(Application, app.id)
    restored_opp = verify.get(Opportunity, opp.id)
    assert restored.status == ApplicationStatus.FORM_FILLED.value
    assert restored.manual_review_reason is None
    assert restored.claimed_by == "worker-a"
    assert restored.submission_claimed_at == identity["expected_submission_claimed_at"]
    assert _unrelated_snapshot(restored) == before
    assert restored_opp.status == OpportunityStatus.AWAITING_SUBMISSION.value
    assert verify.scalars(
        select(StatusHistory).where(StatusHistory.opportunity_id == opp.id)
    ).all() == []
    session.close()
    verify.close()
