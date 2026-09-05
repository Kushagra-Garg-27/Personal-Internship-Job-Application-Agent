"""Orchestrator for evaluating opportunities through the funnel.

Wires together the Stage pipeline (Eligibility -> [Phase 5 Scam/Risk] -> Relevance),
stores verdicts in `scoring_verdicts`, and drives status transitions:
- Discovered -> Ineligible (when eligibility fails)
- Discovered -> Recommended (when all stages pass and threshold is met)
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from sqlalchemy.orm import Session

from core.funnel.base import FunnelRunner, FunnelStage
from core.funnel.eligibility import EligibilityStage
from core.funnel.relevance import RelevanceStage
from core.models.opportunity import Opportunity
from core.models.profile import Profile
from core.models.scoring import ScoringVerdict
from core.repositories import profile_repo, scoring_repo
from core.services import opportunity_service
from core.status import OpportunityStatus

logger = logging.getLogger(__name__)


def build_default_runner() -> FunnelRunner:
    """Build the standard 2-stage funnel runner (Phase 4).

    Phase 5's scam/risk filter will be inserted between these two stages.
    """
    stages: list[FunnelStage] = [
        EligibilityStage(),
        # ScamRiskStage will slot in here in Phase 5
        RelevanceStage(),
    ]
    return FunnelRunner(stages=stages)


def evaluate_opportunity(
    session: Session,
    opportunity: Opportunity,
    profile: Profile | None = None,
    resume_text: str | None = None,
    runner: FunnelRunner | None = None,
    score_threshold: float = 0.0,
    actor: str = "funnel",
) -> ScoringVerdict:
    """Evaluate a single opportunity through the configured funnel stages.

    1. Executes stages in order with hard short-circuit on failure.
    2. Persists or updates the resulting `ScoringVerdict`.
    3. Atomically updates opportunity status (DISCOVERED -> INELIGIBLE or RECOMMENDED).

    Returns:
        The persisted `ScoringVerdict`.
    """
    # Resolve profile
    active_profile = profile
    if active_profile is None:
        if opportunity.profile_id is not None:
            active_profile = session.get(Profile, opportunity.profile_id)
        if active_profile is None:
            profiles = profile_repo.list_profiles(session)
            if profiles:
                active_profile = profiles[0]

    # Resolve candidate resume text if not provided
    text_to_use = resume_text
    if text_to_use is None and active_profile:
        raw_resumes = getattr(active_profile, "resumes", [])
        resumes_list = raw_resumes if isinstance(raw_resumes, list) else ([raw_resumes] if raw_resumes else [])
        for r in resumes_list:
            if getattr(r, "is_active", False) and getattr(r, "parsed_text", None):
                text_to_use = r.parsed_text
                break
        if not text_to_use:
            for r in resumes_list:
                if getattr(r, "parsed_text", None):
                    text_to_use = r.parsed_text
                    break

    # Run funnel stages
    active_runner = runner or build_default_runner()
    verdicts = active_runner.run(
        opportunity=opportunity,
        profile=active_profile,
        resume_text=text_to_use,
    )

    completed_at = datetime.now(timezone.utc)

    # Check Stage 1: Eligibility
    eligibility_verdict = next((v for v in verdicts if v.stage_name == "eligibility"), None)
    passed_eligibility = eligibility_verdict.passed if eligibility_verdict else False

    if not passed_eligibility:
        reason = (
            eligibility_verdict.reason
            if eligibility_verdict and eligibility_verdict.reason
            else {"rule": "eligibility", "detail": "Eligibility check failed"}
        )
        verdict = scoring_repo.upsert_verdict(
            session,
            opportunity_id=opportunity.id,
            profile_id=active_profile.id if active_profile else None,
            eligibility_passed=False,
            eligibility_reason=reason,
            relevance_score=None,
            relevance_explanation=None,
            funnel_completed_at=completed_at,
            model_name=None,
        )

        if opportunity.status == OpportunityStatus.DISCOVERED:
            detail = reason.get("detail", "Failed eligibility filter")
            opportunity_service.transition_status(
                session,
                opportunity_id=opportunity.id,
                new_status=OpportunityStatus.INELIGIBLE,
                reason=f"Eligibility rejected: {detail}",
                actor=actor,
            )

        return verdict

    # Eligibility passed — retrieve relevance score
    relevance_verdict = next((v for v in verdicts if v.stage_name == "relevance"), None)
    rel_score: float | None = None
    rel_explanation: dict[str, Any] | None = None
    model_name: str | None = None

    if relevance_verdict and relevance_verdict.payload:
        rel_score = relevance_verdict.payload.get("score")
        rel_explanation = relevance_verdict.payload.get("explanation")
        model_name = relevance_verdict.payload.get("model_name")

    verdict = scoring_repo.upsert_verdict(
        session,
        opportunity_id=opportunity.id,
        profile_id=active_profile.id if active_profile else None,
        eligibility_passed=True,
        eligibility_reason=None,
        relevance_score=rel_score,
        relevance_explanation=rel_explanation,
        funnel_completed_at=completed_at,
        model_name=model_name,
    )

    # Status transition if still in DISCOVERED
    if opportunity.status == OpportunityStatus.DISCOVERED:
        if rel_score is not None and rel_score >= score_threshold:
            opportunity_service.transition_status(
                session,
                opportunity_id=opportunity.id,
                new_status=OpportunityStatus.RECOMMENDED,
                reason=f"Passed eligibility; relevance score: {rel_score:.4f}",
                actor=actor,
            )

    return verdict


def run_funnel_batch(
    session: Session,
    *,
    limit: int = 50,
    runner: FunnelRunner | None = None,
    score_threshold: float = 0.0,
    actor: str = "funnel_batch",
) -> list[ScoringVerdict]:
    """Process a batch of newly discovered opportunities through the funnel."""
    from sqlalchemy import select

    stmt = (
        select(Opportunity)
        .where(Opportunity.status == OpportunityStatus.DISCOVERED)
        .order_by(Opportunity.discovered_at.asc())
        .limit(limit)
    )
    opportunities = list(session.scalars(stmt).all())

    results: list[ScoringVerdict] = []
    for opp in opportunities:
        try:
            verdict = evaluate_opportunity(
                session,
                opportunity=opp,
                runner=runner,
                score_threshold=score_threshold,
                actor=actor,
            )
            results.append(verdict)
        except Exception as exc:
            logger.exception("Failed to evaluate opportunity %s in funnel: %s", opp.id, exc)

    session.flush()
    return results
