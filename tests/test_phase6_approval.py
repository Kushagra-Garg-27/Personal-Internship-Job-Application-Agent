"""Tests for Phase 6 Final Approval, Dismissal, and Dashboard Feed endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.models.opportunity import Opportunity
from core.models.profile import Profile
from core.models.resume import Resume
from core.models.scoring import ScoringVerdict
from core.repositories import scoring_repo
from core.services import opportunity_service
from core.status import OpportunityStatus


@pytest.fixture
def candidate_profile(db_session):
    profile = Profile(
        name="Elena Rostova",
        email="elena@example.com",
        salary_floor=90000,
        remote_preference="remote",
    )
    db_session.add(profile)
    db_session.flush()

    r1 = Resume(
        profile_id=profile.id,
        version=1,
        original_filename="elena_v1.pdf",
        file_path="uploads/elena_v1.pdf",
        file_size_bytes=1024,
        is_active=False,
        parsed_text="Full-stack engineer with React and Python experience.",
    )
    r2 = Resume(
        profile_id=profile.id,
        version=2,
        original_filename="elena_v2_active.pdf",
        file_path="uploads/elena_v2.pdf",
        file_size_bytes=2048,
        is_active=True,
        parsed_text="Senior backend specialist with Python, FastAPI, and Postgres.",
    )
    db_session.add_all([r1, r2])
    db_session.flush()
    return profile


class TestFinalApprovalEndpoint:
    def test_approve_transitions_to_ready_to_apply_with_active_resume(
        self, client: TestClient, db_session, candidate_profile
    ):
        opp = opportunity_service.create_opportunity(
            db_session,
            title="Senior Python Architect",
            company="HyperScale Inc",
            location="Remote",
            profile_id=candidate_profile.id,
        )
        # Move to RECOMMENDED
        opportunity_service.transition_status(
            db_session, opp.id, OpportunityStatus.RECOMMENDED, reason="Passed funnel", actor="funnel"
        )
        assert opp.status == OpportunityStatus.RECOMMENDED

        # Final human approval without explicit resume_id (defaults to active v2)
        resp = client.post(
            f"/opportunities/{opp.id}/approve",
            json={"actor": "elena_human"},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["status"] == OpportunityStatus.READY_TO_APPLY
        active_resume = next(r for r in candidate_profile.resumes if r.is_active)
        assert data["selected_resume_id"] == active_resume.id

        # Verify status history
        db_session.refresh(opp)
        assert opp.status == OpportunityStatus.READY_TO_APPLY
        assert opp.selected_resume_id == active_resume.id
        latest_history = opp.status_history[-1]
        assert latest_history.new_status == OpportunityStatus.READY_TO_APPLY
        assert "elena_human" in (latest_history.actor or "")

    def test_approve_with_explicit_resume_selection(
        self, client: TestClient, db_session, candidate_profile
    ):
        opp = opportunity_service.create_opportunity(
            db_session,
            title="Full Stack Engineer",
            company="Frontend Heavy Labs",
            profile_id=candidate_profile.id,
        )
        opportunity_service.transition_status(
            db_session, opp.id, OpportunityStatus.RECOMMENDED, reason="Passed funnel", actor="funnel"
        )

        # Explicitly choose resume v1 (inactive)
        resume_v1 = next(r for r in candidate_profile.resumes if r.version == 1)
        resp = client.post(
            f"/opportunities/{opp.id}/approve",
            json={"resume_id": resume_v1.id, "actor": "reviewer"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == OpportunityStatus.READY_TO_APPLY
        assert data["selected_resume_id"] == resume_v1.id

    def test_approve_from_invalid_status_fails(
        self, client: TestClient, db_session, candidate_profile
    ):
        opp = opportunity_service.create_opportunity(
            db_session,
            title="Junior Dev",
            company="LowPay Co",
            profile_id=candidate_profile.id,
        )
        opportunity_service.transition_status(
            db_session, opp.id, OpportunityStatus.INELIGIBLE, reason="Below salary floor", actor="funnel"
        )

        resp = client.post(f"/opportunities/{opp.id}/approve", json={})
        assert resp.status_code == 409
        assert "Invalid status transition" in resp.json()["detail"]


class TestDismissEndpoint:
    def test_dismiss_recommended_opportunity(
        self, client: TestClient, db_session, candidate_profile
    ):
        opp = opportunity_service.create_opportunity(
            db_session,
            title="Database Admin",
            company="Legacy Systems Corp",
            profile_id=candidate_profile.id,
        )
        opportunity_service.transition_status(
            db_session, opp.id, OpportunityStatus.RECOMMENDED, reason="Passed funnel", actor="funnel"
        )

        resp = client.post(
            f"/opportunities/{opp.id}/dismiss",
            json={"reason": "Not interested in on-call rotations", "actor": "candidate"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == OpportunityStatus.DISMISSED

        db_session.refresh(opp)
        assert opp.status == OpportunityStatus.DISMISSED
        assert opp.status_history[-1].new_status == OpportunityStatus.DISMISSED
        assert "on-call rotations" in opp.status_history[-1].reason

    def test_dismissed_is_terminal(
        self, client: TestClient, db_session, candidate_profile
    ):
        opp = opportunity_service.create_opportunity(
            db_session,
            title="Lead QA",
            company="TestCo",
            profile_id=candidate_profile.id,
        )
        opportunity_service.transition_status(
            db_session, opp.id, OpportunityStatus.RECOMMENDED, reason="Passed funnel", actor="funnel"
        )
        opportunity_service.transition_status(
            db_session, opp.id, OpportunityStatus.DISMISSED, reason="Dismissed", actor="user"
        )

        # Attempting to approve a dismissed item should fail
        resp = client.post(f"/opportunities/{opp.id}/approve", json={})
        assert resp.status_code == 409


class TestDashboardFeedEndpoint:
    def test_dashboard_feed_returns_consolidated_structure(
        self, client: TestClient, db_session, candidate_profile
    ):
        # Opportunity 1: High relevance score
        opp1 = opportunity_service.create_opportunity(
            db_session,
            title="Staff Python Engineer",
            company="Cloud Native Inc",
            location="Remote",
            salary_min=140000,
            salary_max=180000,
            reliability_tier="stable",
            profile_id=candidate_profile.id,
        )
        opportunity_service.transition_status(
            db_session, opp1.id, OpportunityStatus.RECOMMENDED, reason="Passed funnel", actor="funnel"
        )
        scoring_repo.upsert_verdict(
            db_session,
            opportunity_id=opp1.id,
            profile_id=candidate_profile.id,
            eligibility_passed=True,
            scam_verdict="clear",
            relevance_score=0.92,
            relevance_explanation={"top_skills": ["Python", "FastAPI"], "match_summary": "Excellent fit"},
            model_name="all-MiniLM-L6-v2",
        )

        # Opportunity 2: Moderate relevance score
        opp2 = opportunity_service.create_opportunity(
            db_session,
            title="Backend Engineer",
            company="Agency Co",
            location="Remote",
            reliability_tier="discovery_only",
            profile_id=candidate_profile.id,
        )
        opportunity_service.transition_status(
            db_session, opp2.id, OpportunityStatus.RECOMMENDED, reason="Passed funnel", actor="funnel"
        )
        scoring_repo.upsert_verdict(
            db_session,
            opportunity_id=opp2.id,
            profile_id=candidate_profile.id,
            eligibility_passed=True,
            scam_verdict="clear",
            relevance_score=0.74,
            relevance_explanation={"top_skills": ["Python"], "match_summary": "Good fit"},
        )

        resp = client.get("/opportunities/dashboard-feed?status=recommended")
        assert resp.status_code == 200
        feed = resp.json()

        assert "items" in feed
        assert feed["total"] >= 2
        items = feed["items"]

        # Verifies sorting by relevance_score desc
        scores = [item["relevance_score"] for item in items if item["relevance_score"] is not None]
        assert scores == sorted(scores, reverse=True)

        # Check fields of top item
        top_item = next(i for i in items if i["id"] == opp1.id)
        assert top_item["title"] == "Staff Python Engineer"
        assert top_item["reliability_tier"] == "stable"
        assert top_item["relevance_score"] == 0.92
        assert "Python" in top_item["relevance_explanation"]["top_skills"]
        assert top_item["eligibility_passed"] is True
        assert top_item["scam_verdict"] == "clear"

        # Check available resumes listed
        assert len(top_item["available_resumes"]) == 2
        versions = [r["version"] for r in top_item["available_resumes"]]
        assert 1 in versions
        assert 2 in versions
