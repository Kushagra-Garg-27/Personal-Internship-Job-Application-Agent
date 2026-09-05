"""Data-access layer for ScoringVerdict CRUD and queries."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models.scoring import ScoringVerdict


def create_verdict(session: Session, **kwargs: Any) -> ScoringVerdict:
    """Create and persist a new scoring verdict."""
    verdict = ScoringVerdict(**kwargs)
    session.add(verdict)
    session.flush()
    return verdict


def get_verdict(session: Session, verdict_id: int) -> ScoringVerdict | None:
    """Fetch a scoring verdict by primary key."""
    return session.get(ScoringVerdict, verdict_id)


def get_verdict_by_opportunity(
    session: Session, opportunity_id: int
) -> ScoringVerdict | None:
    """Fetch the latest scoring verdict for an opportunity."""
    stmt = (
        select(ScoringVerdict)
        .where(ScoringVerdict.opportunity_id == opportunity_id)
        .order_by(ScoringVerdict.created_at.desc())
    )
    return session.scalars(stmt).first()


def upsert_verdict(
    session: Session,
    *,
    opportunity_id: int,
    profile_id: int | None = None,
    eligibility_passed: bool,
    eligibility_reason: dict | None = None,
    scam_verdict: str | None = None,
    scam_reason: dict | None = None,
    llm_verdict: str | None = None,
    llm_reasoning: str | None = None,
    quota_deferred_at: Any = None,
    relevance_score: float | None = None,
    relevance_explanation: dict | None = None,
    funnel_completed_at: Any = None,
    model_name: str | None = None,
) -> ScoringVerdict:
    """Create or update a verdict for an opportunity."""
    verdict = get_verdict_by_opportunity(session, opportunity_id)
    if verdict is None:
        verdict = ScoringVerdict(
            opportunity_id=opportunity_id,
            profile_id=profile_id,
            eligibility_passed=eligibility_passed,
            eligibility_reason=eligibility_reason,
            scam_verdict=scam_verdict,
            scam_reason=scam_reason,
            llm_verdict=llm_verdict,
            llm_reasoning=llm_reasoning,
            quota_deferred_at=quota_deferred_at,
            relevance_score=relevance_score,
            relevance_explanation=relevance_explanation,
            funnel_completed_at=funnel_completed_at,
            model_name=model_name,
        )
        session.add(verdict)
    else:
        verdict.profile_id = profile_id
        verdict.eligibility_passed = eligibility_passed
        verdict.eligibility_reason = eligibility_reason
        if scam_verdict is not None:
            verdict.scam_verdict = scam_verdict
        if scam_reason is not None:
            verdict.scam_reason = scam_reason
        if llm_verdict is not None:
            verdict.llm_verdict = llm_verdict
        if llm_reasoning is not None:
            verdict.llm_reasoning = llm_reasoning
        if quota_deferred_at is not None:
            verdict.quota_deferred_at = quota_deferred_at
        verdict.relevance_score = relevance_score
        verdict.relevance_explanation = relevance_explanation
        verdict.funnel_completed_at = funnel_completed_at
        verdict.model_name = model_name
    session.flush()
    return verdict


def list_verdicts(
    session: Session,
    *,
    opportunity_id: int | None = None,
    profile_id: int | None = None,
    passed: bool | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[ScoringVerdict]:
    """List verdicts with optional filters."""
    stmt = select(ScoringVerdict)

    if opportunity_id is not None:
        stmt = stmt.where(ScoringVerdict.opportunity_id == opportunity_id)
    if profile_id is not None:
        stmt = stmt.where(ScoringVerdict.profile_id == profile_id)
    if passed is not None:
        stmt = stmt.where(ScoringVerdict.eligibility_passed == passed)

    stmt = stmt.order_by(ScoringVerdict.created_at.desc()).limit(limit).offset(offset)
    return list(session.scalars(stmt).all())
