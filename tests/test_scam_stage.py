"""Tests for ScamRiskStage budgeting, AI invocation economy, and funnel integration (Phase 5)."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from core.funnel.base import FunnelRunner
from core.funnel.eligibility import EligibilityStage
from core.funnel.relevance import RelevanceStage
from core.funnel.runner import build_default_runner, evaluate_opportunity
from core.funnel.scam_risk.gemini_client import GeminiScamClient, LLMScamResult
from core.funnel.scam_risk.stage import ScamRiskStage
from core.models.opportunity import Opportunity
from core.models.profile import Profile
from core.repositories import scam_signature_repo, scoring_repo
from core.services import opportunity_service
from core.status import OpportunityStatus


@pytest.fixture
def test_profile(db_session):
    profile = Profile(
        name="Bob Engineer",
        email="bob@example.com",
        salary_floor=80000,
        remote_preference="remote",
    )
    db_session.add(profile)
    db_session.flush()
    return profile


class TestScamRiskStageBudgeting:
    def test_clean_listing_never_invokes_gemini(self):
        """Zero AI budget consumed when deterministic rules find no scam indicators."""
        mock_gemini = Mock(spec=GeminiScamClient)
        stage = ScamRiskStage(gemini_client=mock_gemini)

        opp = Mock()
        opp.title = "Software Engineer"
        opp.company = "Stripe"
        opp.description = "Looking for a backend engineer with Python and distributed systems experience."
        opp.url = "https://stripe.com/jobs/123"
        opp.metadata_json = {}

        verdict = stage.evaluate(opp, profile=None)

        assert verdict.passed is True
        assert verdict.payload["verdict"] == "clear"
        mock_gemini.evaluate_ambiguous.assert_not_called()

    def test_hard_reject_listing_never_invokes_gemini(self):
        """Zero AI budget consumed on high-confidence scam listings (wire money, etc.)."""
        mock_gemini = Mock(spec=GeminiScamClient)
        stage = ScamRiskStage(gemini_client=mock_gemini)

        opp = Mock()
        opp.title = "Virtual Assistant"
        opp.company = "Global Logistics"
        opp.description = "Must wire money to supplier via Western Union before starting work."
        opp.url = "https://randomjob.com/apply"
        opp.metadata_json = {}

        verdict = stage.evaluate(opp, profile=None)

        assert verdict.passed is False
        assert verdict.payload["verdict"] == "reject"
        assert "wire money" in verdict.reason["matched"]
        mock_gemini.evaluate_ambiguous.assert_not_called()

    def test_ambiguous_listing_invokes_gemini_and_halts_for_review(self):
        """Only ambiguous listings invoke Gemini, and they halt in pending review state."""
        mock_gemini = Mock(spec=GeminiScamClient)
        mock_gemini.evaluate_ambiguous.return_value = LLMScamResult(
            success=True,
            verdict="suspicious",
            reasoning="Unusually aggressive hiring tactics.",
            confidence=0.8,
        )

        stage = ScamRiskStage(gemini_client=mock_gemini)

        opp = Mock()
        opp.title = "Customer Rep"
        opp.company = "Stealth Startup"
        opp.description = "Urgent hiring! Immediate start no interview needed. Apply now."
        opp.url = "https://stealthstartup.co/apply"
        opp.metadata_json = {}

        verdict = stage.evaluate(opp, profile=None)

        # Crucial requirement: never auto-commit, halts for human review
        assert verdict.passed is False
        assert verdict.payload["verdict"] == "ambiguous"
        assert verdict.payload["llm_verdict"] == "suspicious"
        mock_gemini.evaluate_ambiguous.assert_called_once()

    def test_ambiguous_listing_with_quota_exhausted_defers_fail_closed(self):
        """When Gemini quota is exhausted, fail closed into deferred state without guessing."""
        mock_gemini = Mock(spec=GeminiScamClient)
        mock_gemini.evaluate_ambiguous.return_value = LLMScamResult(
            success=False,
            is_quota_exhausted=True,
            error="Daily Gemini quota exhausted (1500/1500 calls used)",
        )

        stage = ScamRiskStage(gemini_client=mock_gemini)

        opp = Mock()
        opp.title = "Customer Rep"
        opp.company = "Stealth Startup"
        opp.description = "Urgent hiring! Immediate start no interview needed."
        opp.url = "https://stealthstartup.co/apply"
        opp.metadata_json = {}

        verdict = stage.evaluate(opp, profile=None)

        assert verdict.passed is False
        assert verdict.payload["verdict"] == "deferred"
        assert verdict.reason["is_quota_exhausted"] is True


class TestFunnelRunnerScamIntegration:
    def test_build_default_runner_stage_order(self):
        runner = build_default_runner()
        assert len(runner.stages) == 3
        assert runner.stages[0].name == "eligibility"
        assert runner.stages[1].name == "scam_risk"
        assert runner.stages[2].name == "relevance"

    def test_hard_scam_rejects_and_stores_hash_and_bypasses_relevance(
        self, db_session, test_profile
    ):
        opp = opportunity_service.create_opportunity(
            db_session,
            title="Account Manager",
            company="Crypto Escrow LLC",
            salary_max=120000,
            location="Remote",
            description="You will handle daily crypto transfer transactions for our clients.",
        )
        assert opp.status == OpportunityStatus.DISCOVERED

        mock_relevance = Mock(spec=RelevanceStage)
        mock_gemini = Mock(spec=GeminiScamClient)
        scam_stage = ScamRiskStage(gemini_client=mock_gemini, session=db_session)

        runner = FunnelRunner(stages=[EligibilityStage(), scam_stage, mock_relevance])

        verdict = evaluate_opportunity(
            db_session, opp, profile=test_profile, runner=runner
        )

        # Relevance stage should never have been touched
        mock_relevance.evaluate.assert_not_called()
        mock_gemini.evaluate_ambiguous.assert_not_called()

        # Check verdict & status
        assert verdict.eligibility_passed is True
        assert verdict.scam_verdict == "reject"
        assert opp.status == OpportunityStatus.SCAM_RISK_REJECTED

        # Check that content hash was automatically stored in scam_content_signatures
        from core.funnel.scam_risk.rules import compute_content_hash
        chash = compute_content_hash(opp.description)
        sig = scam_signature_repo.get_by_hash(db_session, chash)
        assert sig is not None
        assert sig.confirmed_scam is True

    def test_ambiguous_scam_transitions_to_scam_review_pending(
        self, db_session, test_profile
    ):
        opp = opportunity_service.create_opportunity(
            db_session,
            title="Fast Hire",
            company="Unknown Agency",
            salary_max=100000,
            location="Remote",
            description="Immediate start no interview required. High payout guaranteed.",
        )

        mock_relevance = Mock(spec=RelevanceStage)
        mock_gemini = Mock(spec=GeminiScamClient)
        mock_gemini.evaluate_ambiguous.return_value = LLMScamResult(
            success=True,
            verdict="suspicious",
            reasoning="Too good to be true without interview.",
            confidence=0.9,
        )
        scam_stage = ScamRiskStage(gemini_client=mock_gemini, session=db_session)
        runner = FunnelRunner(stages=[EligibilityStage(), scam_stage, mock_relevance])

        verdict = evaluate_opportunity(
            db_session, opp, profile=test_profile, runner=runner
        )

        # Stopped before relevance stage
        mock_relevance.evaluate.assert_not_called()
        assert verdict.scam_verdict == "ambiguous"
        assert verdict.llm_verdict == "suspicious"
        assert opp.status == OpportunityStatus.SCAM_REVIEW_PENDING

    def test_quota_exhausted_fail_closed_stays_in_discovered(
        self, db_session, test_profile
    ):
        opp = opportunity_service.create_opportunity(
            db_session,
            title="Assistant",
            company="Vague LLC",
            salary_max=95000,
            location="Remote",
            description="Immediate start no interview required.",
        )

        mock_relevance = Mock(spec=RelevanceStage)
        mock_gemini = Mock(spec=GeminiScamClient)
        mock_gemini.evaluate_ambiguous.return_value = LLMScamResult(
            success=False,
            is_quota_exhausted=True,
            error="Rate limit exceeded",
        )
        scam_stage = ScamRiskStage(gemini_client=mock_gemini, session=db_session)
        runner = FunnelRunner(stages=[EligibilityStage(), scam_stage, mock_relevance])

        verdict = evaluate_opportunity(
            db_session, opp, profile=test_profile, runner=runner
        )

        assert verdict.scam_verdict == "deferred"
        assert verdict.quota_deferred_at is not None
        # Does NOT transition to recommended or rejected: stays in discovered!
        assert opp.status == OpportunityStatus.DISCOVERED
