"""Tests for FunnelRunner orchestration, stage ordering, short-circuit, and transitions."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from core.funnel.base import FunnelRunner, FunnelStage, StageVerdict
from core.funnel.eligibility import EligibilityStage
from core.funnel.relevance import RelevanceStage
from core.funnel.runner import (
    build_default_runner,
    evaluate_opportunity,
    run_funnel_batch,
)
from core.models.opportunity import Opportunity
from core.models.profile import Profile, ProfileEducation, ProfileSkill
from core.models.resume import Resume
from core.models.scoring import ScoringVerdict
from core.repositories import scoring_repo
from core.services import opportunity_service
from core.status import OpportunityStatus


class DummyStage(FunnelStage):
    def __init__(self, name: str, should_pass: bool = True, payload: dict | None = None):
        self.name = name
        self.should_pass = should_pass
        self.payload = payload or {}
        self.called = False

    def evaluate(self, opportunity, profile, resume_text=None) -> StageVerdict:
        self.called = True
        return StageVerdict(
            stage_name=self.name,
            passed=self.should_pass,
            reason={"detail": "Dummy failure"} if not self.should_pass else None,
            payload=self.payload,
        )


class TestFunnelRunnerOrdering:
    def test_empty_stages_returns_empty_verdicts(self):
        runner = FunnelRunner(stages=[])
        verdicts = runner.run(None, None)
        assert verdicts == []

    def test_short_circuit_on_stage1_failure(self):
        """If stage 1 fails, stage 2 is strictly NEVER invoked."""
        stage1 = DummyStage("stage1", should_pass=False)
        stage2 = DummyStage("stage2", should_pass=True)

        runner = FunnelRunner(stages=[stage1, stage2])
        verdicts = runner.run(None, None)

        assert len(verdicts) == 1
        assert verdicts[0].stage_name == "stage1"
        assert verdicts[0].passed is False
        assert stage1.called is True
        assert stage2.called is False  # Guaranteed hard stop

    def test_all_stages_invoked_when_all_pass(self):
        stage1 = DummyStage("stage1", should_pass=True)
        stage2 = DummyStage("stage2", should_pass=True)

        runner = FunnelRunner(stages=[stage1, stage2])
        verdicts = runner.run(None, None)

        assert len(verdicts) == 2
        assert stage1.called is True
        assert stage2.called is True

    def test_stage_insertion_phase5_slot(self):
        """Simulate inserting Phase 5's scam/risk filter between stage 1 and stage 3."""
        eligibility = DummyStage("eligibility", should_pass=True)
        scam_risk = DummyStage("scam_risk", should_pass=False)  # flagged as scam!
        relevance = DummyStage("relevance", should_pass=True)

        runner = FunnelRunner(stages=[eligibility, scam_risk, relevance])
        verdicts = runner.run(None, None)

        assert len(verdicts) == 2
        assert eligibility.called is True
        assert scam_risk.called is True
        assert relevance.called is False  # Never evaluated for scams!


class TestEvaluateOpportunityTransitions:
    @pytest.fixture
    def test_profile(self, db_session):
        profile = Profile(
            name="Alice Candidate",
            email="alice@example.com",
            salary_floor=90000,
            remote_preference="remote",
        )
        db_session.add(profile)
        db_session.flush()

        edu = ProfileEducation(
            profile_id=profile.id,
            degree="B.Tech",
            branch="Computer Science",
            institution="Tech University",
        )
        skill = ProfileSkill(profile_id=profile.id, skill_name="Python", proficiency="expert")
        resume = Resume(
            profile_id=profile.id,
            version=1,
            original_filename="alice.pdf",
            file_path="uploads/alice.pdf",
            file_size_bytes=1000,
            is_active=True,
            parsed_text="Experienced Python backend engineer.",
        )
        db_session.add_all([edu, skill, resume])
        db_session.flush()
        return profile

    def test_ineligible_opportunity_transitions_to_ineligible(self, db_session, test_profile):
        # Opportunity pays below salary floor
        opp = opportunity_service.create_opportunity(
            db_session,
            title="Junior Dev",
            company="LowPay Co",
            salary_max=60000,  # Below 90000 floor
            location="Remote",
            description="Python dev role.",
        )
        assert opp.status == OpportunityStatus.DISCOVERED

        # Mock relevance to ensure it is never called
        mock_relevance = Mock(spec=RelevanceStage)
        runner = FunnelRunner(stages=[EligibilityStage(), mock_relevance])

        verdict = evaluate_opportunity(db_session, opp, profile=test_profile, runner=runner)

        assert verdict.eligibility_passed is False
        assert verdict.relevance_score is None
        assert opp.status == OpportunityStatus.INELIGIBLE
        mock_relevance.evaluate.assert_not_called()

        # Check status history
        assert len(opp.status_history) >= 2  # discovered, then ineligible
        assert opp.status_history[-1].new_status == OpportunityStatus.INELIGIBLE

    def test_eligible_opportunity_transitions_to_recommended(self, db_session, test_profile):
        opp = opportunity_service.create_opportunity(
            db_session,
            title="Senior Python Engineer",
            company="Tech Corp",
            salary_max=140000,
            location="Remote",
            description="Looking for a Python developer with backend experience.",
        )
        assert opp.status == OpportunityStatus.DISCOVERED

        # Custom runner with dummy relevance stage to test transition cleanly
        dummy_rel = DummyStage(
            "relevance",
            should_pass=True,
            payload={"score": 0.85, "explanation": {"top_skills": []}, "model_name": "test-model"},
        )
        runner = FunnelRunner(stages=[EligibilityStage(), dummy_rel])

        verdict = evaluate_opportunity(
            db_session, opp, profile=test_profile, runner=runner, score_threshold=0.5
        )

        assert verdict.eligibility_passed is True
        assert verdict.relevance_score == 0.85
        assert verdict.model_name == "test-model"
        assert opp.status == OpportunityStatus.RECOMMENDED
        assert opp.status_history[-1].new_status == OpportunityStatus.RECOMMENDED

    def test_run_funnel_batch(self, db_session, test_profile):
        opp1 = opportunity_service.create_opportunity(
            db_session,
            title="Job 1",
            company="Company 1",
            salary_max=120000,
            location="Remote",
            description="Python engineer role.",
        )
        opp2 = opportunity_service.create_opportunity(
            db_session,
            title="Job 2",
            company="Company 2",
            salary_max=40000,  # Below floor -> ineligible
            location="Remote",
            description="Junior developer role.",
        )

        dummy_rel = DummyStage("relevance", should_pass=True, payload={"score": 0.75})
        runner = FunnelRunner(stages=[EligibilityStage(), dummy_rel])

        verdicts = run_funnel_batch(db_session, limit=10, runner=runner)
        assert len(verdicts) == 2

        v1 = scoring_repo.get_verdict_by_opportunity(db_session, opp1.id)
        v2 = scoring_repo.get_verdict_by_opportunity(db_session, opp2.id)

        assert v1.eligibility_passed is True
        assert opp1.status == OpportunityStatus.RECOMMENDED

        assert v2.eligibility_passed is False
        assert opp2.status == OpportunityStatus.INELIGIBLE
