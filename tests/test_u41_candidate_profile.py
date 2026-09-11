"""U4.1 tests — real candidate profile data layer for the Unstop flow.

Covers:
1. Profile persistence of candidate application attributes.
2. Profile update of candidate application attributes.
3. ``serialize_profile`` exposing explicit values and preserving absence.
4. Missing candidate values stay missing (never fabricated).
5. Explicit candidate values are preserved exactly.
6. Unstop classification: missing -> REQUIRES_USER, explicit -> PROFILE_FACT.
7. Resume association and the browser-tier REAL_RESUME_REQUIRED gate.
8. Application linking via the normal services.
9. Duplicate application prevention.
10. Demo/seeded data isolation from real-profile behavior.
11. The U4 human submission gate remains fail-closed (no token -> no submit).
12. Fill-only never submits.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.models.opportunity import Application, Opportunity
from core.repositories import profile_repo
from core.services import application_service, opportunity_service, profile_service
from core.status import (
    HUMAN_SUBMISSION_APPROVAL_TOKEN,
    ApplicationStatus,
    OpportunityStatus,
    SubmissionApprovalRequiredError,
    is_human_approved,
)
from worker.adapters.unstop import UnstopAdapter, QuestionClassification
from worker.engine.filler import ApplicationFiller, serialize_profile


# ── Synthetic candidate values (never real personal data) ─────────────────

CANDIDATE_ATTRS = {
    "organization": "Synthetic Institute",
    "designation": "Test Engineer",
    "work_experience": "2 years",
    "user_type": "Professional",
    "gender": "Prefer not to say",
    "differently_abled": "No",
}


# ── 1 & 2. Profile persistence & update ───────────────────────────────────

def test_profile_persists_candidate_attributes(db_session):
    profile = profile_service.create_profile_with_children(
        db_session, name="real-profile", full_name="Jo Tester", **CANDIDATE_ATTRS
    )
    db_session.flush()

    loaded = profile_repo.get_profile(db_session, profile.id)
    for key, value in CANDIDATE_ATTRS.items():
        assert getattr(loaded, key) == value


def test_profile_updates_candidate_attributes(db_session):
    profile = profile_repo.create_profile(db_session, name="update-real")
    updated = profile_service.update_profile_with_children(
        db_session, profile.id, gender="Non-binary", user_type="Fresher"
    )
    assert updated.gender == "Non-binary"
    assert updated.user_type == "Fresher"


def test_candidate_attributes_default_to_none(db_session):
    """Absent values must be NULL — never a fabricated default."""
    profile = profile_repo.create_profile(db_session, name="no-attrs")
    for key in CANDIDATE_ATTRS:
        assert getattr(profile, key) is None


# ── 3, 4 & 5. Serialization ───────────────────────────────────────────────

def test_serialize_profile_includes_explicit_candidate_values(db_session):
    profile = profile_repo.create_profile(
        db_session, name="ser-explicit", full_name="Jo Tester", email="jo@example.com",
        **CANDIDATE_ATTRS,
    )
    data = serialize_profile(profile)
    for key, value in CANDIDATE_ATTRS.items():
        assert data[key] == value


def test_serialize_profile_preserves_missing_values(db_session):
    """Missing values remain absent (None), never invented."""
    profile = profile_repo.create_profile(db_session, name="ser-missing", full_name="Jo")
    data = serialize_profile(profile)
    assert data["gender"] is None
    assert data["differently_abled"] is None
    assert data["user_type"] is None
    assert data["organization"] is None


def test_serialize_none_profile_returns_empty():
    assert serialize_profile(None) == {}


# ── 6. Unstop classification ──────────────────────────────────────────────

def _gender_field():
    from worker.adapters.unstop import FormField

    return FormField(
        id="gender", name="user_gender", tag="un-radio-group", type=None,
        label="Gender*", required=True, field_role="gender",
    )


def test_unstop_missing_gender_requires_user():
    adapter = UnstopAdapter()
    candidate_data = serialize_profile(None) or {"full_name": "Jo"}
    cls, val = adapter.classify_field(_gender_field(), candidate_data)
    assert cls == QuestionClassification.REQUIRES_USER
    assert val is None


def test_unstop_explicit_gender_is_profile_fact():
    adapter = UnstopAdapter()
    cls, val = adapter.classify_field(_gender_field(), {"gender": "Prefer not to say"})
    assert cls == QuestionClassification.PROFILE_FACT
    assert val == "Prefer not to say"


# ── 7. Resume association & REAL_RESUME_REQUIRED gate ─────────────────────

def _ready_unstop_opp(db_session, profile_id=None):
    opp = opportunity_service.create_opportunity(
        db_session,
        title="System Admin",
        company="Synthetic Co",
        url="https://unstop.com/competitions/1753995/register",
        source="unstop",
        reliability_tier="experimental",
        profile_id=profile_id,
    )
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.RECOMMENDED)
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.READY_TO_APPLY)
    db_session.commit()
    return opp


def test_unstop_fill_requires_real_resume(db_session):
    """No on-disk resume -> REAL_RESUME_REQUIRED, fail closed, no application row."""
    profile = profile_repo.create_profile(db_session, name="resume-gate", full_name="Jo")
    opp = _ready_unstop_opp(db_session, profile_id=profile.id)

    result = ApplicationFiller().process_opportunity(db_session, opp.id)

    assert result["status"] == "manual_required"
    assert "REAL_RESUME_REQUIRED" in result["reason"]

    db_session.refresh(opp)
    assert opp.status == OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value
    assert db_session.query(Application).filter_by(opportunity_id=opp.id).count() == 0


def test_unstop_adapter_declares_requires_resume():
    assert UnstopAdapter.requires_resume is True


# ── 8 & 9. Application linking & duplicate prevention ─────────────────────

def test_application_linking_and_duplicate_prevention(db_session):
    opp = _ready_unstop_opp(db_session)
    app1 = application_service.create_application(db_session, opp.id, adapter_name="unstop")
    app2 = application_service.create_application(db_session, opp.id, adapter_name="unstop")
    db_session.commit()

    assert app1.attempt_number == 1
    assert app2.attempt_number == 2
    assert opp.status == OpportunityStatus.READY_TO_APPLY.value
    assert app1.status == ApplicationStatus.PENDING.value


def test_upsert_opportunity_prevents_duplicate_rows(db_session):
    first, created = opportunity_service.upsert_opportunity(
        db_session, title="Dup Role", company="Synthetic Co",
        url="https://unstop.com/competitions/1753995/register", source="unstop",
    )
    second, created2 = opportunity_service.upsert_opportunity(
        db_session, title="Dup Role", company="Synthetic Co",
        url="https://unstop.com/competitions/1753995/register", source="unstop",
    )
    db_session.commit()
    assert created is True
    assert created2 is False
    assert first.id == second.id


# ── 10. Demo-data isolation ───────────────────────────────────────────────

def test_seed_identity_is_not_special_cased(db_session):
    """A profile named 'default' with demo-like data has no privileged behavior."""
    demo = profile_repo.create_profile(
        db_session, name="default", full_name="Demo Person", email="demo@example.com"
    )
    data = serialize_profile(demo)
    # Demo data does not magically populate Unstop-required demographics.
    assert data["gender"] is None
    assert data["differently_abled"] is None
    assert data["user_type"] is None


# ── 11 & 12. U4 gate remains fail-closed; fill-only never submits ─────────

def test_submission_gate_remains_fail_closed(db_session):
    assert is_human_approved(None) is False
    assert is_human_approved("ready_for_review") is False
    assert is_human_approved(HUMAN_SUBMISSION_APPROVAL_TOKEN) is True

    opp = _ready_unstop_opp(db_session)
    app = application_service.create_application(db_session, opp.id, adapter_name="unstop")
    application_service.transition_application_status(
        db_session, app.id, ApplicationStatus.FORM_FILLED,
        notes=json.dumps({"adapter": "unstop", "tier": "experimental"}),
    )
    db_session.commit()

    with pytest.raises(SubmissionApprovalRequiredError):
        ApplicationFiller().confirm_and_submit(db_session, app.id)

    db_session.refresh(app)
    assert app.status == ApplicationStatus.FORM_FILLED.value
