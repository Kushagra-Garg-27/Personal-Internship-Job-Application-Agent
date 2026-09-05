"""Tests for ScoringVerdict repository CRUD and query methods."""

from __future__ import annotations

import pytest

from core.models.opportunity import Opportunity
from core.models.profile import Profile
from core.models.scoring import ScoringVerdict
from core.repositories import scoring_repo
from core.services import opportunity_service


class TestScoringRepo:
    @pytest.fixture
    def setup_entities(self, db_session):
        profile = Profile(name="Test User", email="user@example.com")
        db_session.add(profile)
        db_session.flush()

        opp = opportunity_service.create_opportunity(
            db_session,
            title="Backend Engineer",
            company="Acme Corp",
        )
        return profile, opp

    def test_create_and_get_verdict(self, db_session, setup_entities):
        profile, opp = setup_entities
        verdict = scoring_repo.create_verdict(
            db_session,
            opportunity_id=opp.id,
            profile_id=profile.id,
            eligibility_passed=True,
            relevance_score=0.82,
            relevance_explanation={"top_skills": ["Python"]},
            model_name="all-MiniLM-L6-v2",
        )
        assert verdict.id is not None

        fetched = scoring_repo.get_verdict(db_session, verdict.id)
        assert fetched is not None
        assert fetched.opportunity_id == opp.id
        assert fetched.relevance_score == 0.82
        assert fetched.model_name == "all-MiniLM-L6-v2"

    def test_get_verdict_by_opportunity(self, db_session, setup_entities):
        profile, opp = setup_entities
        scoring_repo.create_verdict(
            db_session,
            opportunity_id=opp.id,
            profile_id=profile.id,
            eligibility_passed=False,
            eligibility_reason={"rule": "check_deadline", "detail": "expired"},
            relevance_score=None,
        )

        by_opp = scoring_repo.get_verdict_by_opportunity(db_session, opp.id)
        assert by_opp is not None
        assert by_opp.eligibility_passed is False
        assert by_opp.relevance_score is None

    def test_upsert_verdict(self, db_session, setup_entities):
        profile, opp = setup_entities

        # Initial insert
        v1 = scoring_repo.upsert_verdict(
            db_session,
            opportunity_id=opp.id,
            profile_id=profile.id,
            eligibility_passed=True,
            relevance_score=0.65,
        )
        assert v1.relevance_score == 0.65

        # Update on re-evaluation
        v2 = scoring_repo.upsert_verdict(
            db_session,
            opportunity_id=opp.id,
            profile_id=profile.id,
            eligibility_passed=True,
            relevance_score=0.91,
        )
        assert v2.id == v1.id
        assert v2.relevance_score == 0.91

    def test_list_verdicts_filtered(self, db_session, setup_entities):
        profile, opp1 = setup_entities
        opp2 = opportunity_service.create_opportunity(
            db_session,
            title="Frontend Dev",
            company="Beta Corp",
        )

        scoring_repo.create_verdict(
            db_session,
            opportunity_id=opp1.id,
            profile_id=profile.id,
            eligibility_passed=True,
        )
        scoring_repo.create_verdict(
            db_session,
            opportunity_id=opp2.id,
            profile_id=profile.id,
            eligibility_passed=False,
        )

        all_v = scoring_repo.list_verdicts(db_session)
        assert len(all_v) == 2

        passed_v = scoring_repo.list_verdicts(db_session, passed=True)
        assert len(passed_v) == 1
        assert passed_v[0].opportunity_id == opp1.id

        failed_v = scoring_repo.list_verdicts(db_session, passed=False)
        assert len(failed_v) == 1
        assert failed_v[0].opportunity_id == opp2.id

    def test_cascade_delete(self, db_session, setup_entities):
        profile, opp = setup_entities
        verdict = scoring_repo.create_verdict(
            db_session,
            opportunity_id=opp.id,
            profile_id=profile.id,
            eligibility_passed=True,
        )
        verdict_id = verdict.id

        db_session.delete(opp)
        db_session.flush()

        assert scoring_repo.get_verdict(db_session, verdict_id) is None
