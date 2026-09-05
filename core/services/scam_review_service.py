"""Service layer for human review of ambiguous scam/risk opportunities.

When deterministic rules flag an opportunity as ambiguous and Gemini evaluates it,
the opportunity halts in `SCAM_REVIEW_PENDING` for human review.
- Approve: runs Stage 3 Relevance, transitions to `RECOMMENDED`.
- Reject: records content hash in `scam_content_signatures`, transitions to `SCAM_RISK_REJECTED`.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.funnel.relevance import RelevanceStage
from core.funnel.scam_risk.rules import compute_content_hash
from core.models.opportunity import Opportunity
from core.models.profile import Profile
from core.repositories import profile_repo, scam_signature_repo, scoring_repo
from core.services import opportunity_service
from core.status import OpportunityStatus

logger = logging.getLogger(__name__)


def list_pending_scam_reviews(
    session: Session,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[Opportunity]:
    """Retrieve all opportunities currently pending human scam/risk review."""
    stmt = (
        select(Opportunity)
        .where(Opportunity.status == OpportunityStatus.SCAM_REVIEW_PENDING)
        .order_by(Opportunity.discovered_at.asc())
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(stmt).all())


def approve_pending_opportunity(
    session: Session,
    opportunity_id: int,
    *,
    actor: str = "human_reviewer",
) -> Opportunity:
    """Approve an opportunity halted in SCAM_REVIEW_PENDING.

    1. Validates the opportunity exists and is in SCAM_REVIEW_PENDING.
    2. Runs Stage 3 (RelevanceStage) to compute profile-job matching.
    3. Updates the ScoringVerdict with clear scam status and relevance score.
    4. Transitions opportunity status from SCAM_REVIEW_PENDING -> RECOMMENDED.
    """
    opp = session.get(Opportunity, opportunity_id)
    if opp is None:
        raise ValueError(f"Opportunity {opportunity_id} not found.")

    if opp.status != OpportunityStatus.SCAM_REVIEW_PENDING:
        raise ValueError(
            f"Opportunity {opportunity_id} is in status '{opp.status}', "
            f"expected '{OpportunityStatus.SCAM_REVIEW_PENDING}'."
        )

    # Resolve profile
    active_profile: Profile | None = None
    if opp.profile_id is not None:
        active_profile = session.get(Profile, opp.profile_id)
    if active_profile is None:
        profiles = profile_repo.list_profiles(session)
        if profiles:
            active_profile = profiles[0]

    # Resolve resume text
    text_to_use: str | None = None
    if active_profile:
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

    # Run Stage 3 Relevance
    rel_stage = RelevanceStage()
    rel_verdict = rel_stage.evaluate(
        opportunity=opp,
        profile=active_profile,
        resume_text=text_to_use,
    )

    rel_score: float | None = None
    rel_explanation: dict[str, Any] | None = None
    model_name: str | None = None

    if rel_verdict and rel_verdict.payload:
        rel_score = rel_verdict.payload.get("score")
        rel_explanation = rel_verdict.payload.get("explanation")
        model_name = rel_verdict.payload.get("model_name")

    completed_at = datetime.now(timezone.utc)

    # Update scoring verdict
    scoring_repo.upsert_verdict(
        session,
        opportunity_id=opp.id,
        profile_id=active_profile.id if active_profile else None,
        eligibility_passed=True,
        scam_verdict="clear",
        scam_reason={"rule": "human_review", "detail": "Approved by human reviewer"},
        relevance_score=rel_score,
        relevance_explanation=rel_explanation,
        funnel_completed_at=completed_at,
        model_name=model_name,
    )

    # Transition status: scam_review_pending -> recommended
    score_str = f"{rel_score:.4f}" if rel_score is not None else "N/A"
    opp = opportunity_service.transition_status(
        session,
        opportunity_id=opp.id,
        new_status=OpportunityStatus.RECOMMENDED,
        reason=f"Human approved from scam review; relevance score: {score_str}",
        actor=actor,
    )

    return opp


def reject_pending_opportunity(
    session: Session,
    opportunity_id: int,
    *,
    reason: str = "Rejected during human review",
    actor: str = "human_reviewer",
) -> Opportunity:
    """Reject an opportunity halted in SCAM_REVIEW_PENDING.

    1. Validates the opportunity exists and is in SCAM_REVIEW_PENDING.
    2. Stores the listing's body text hash in `scam_content_signatures` as confirmed scam.
    3. Updates ScoringVerdict to record rejection.
    4. Transitions opportunity status from SCAM_REVIEW_PENDING -> SCAM_RISK_REJECTED.
    """
    opp = session.get(Opportunity, opportunity_id)
    if opp is None:
        raise ValueError(f"Opportunity {opportunity_id} not found.")

    if opp.status != OpportunityStatus.SCAM_REVIEW_PENDING:
        raise ValueError(
            f"Opportunity {opportunity_id} is in status '{opp.status}', "
            f"expected '{OpportunityStatus.SCAM_REVIEW_PENDING}'."
        )

    # Persist content hash into scam_content_signatures
    if getattr(opp, "description", None):
        chash = compute_content_hash(opp.description)
        existing_sig = scam_signature_repo.get_by_hash(session, chash)
        if not existing_sig:
            scam_signature_repo.create_signature(
                session,
                content_hash=chash,
                opportunity_id=opp.id,
                rule_name="human_review",
                confirmed_scam=True,
                notes=reason,
            )
        else:
            existing_sig.confirmed_scam = True
            session.flush()

    completed_at = datetime.now(timezone.utc)

    # Update scoring verdict
    scoring_repo.upsert_verdict(
        session,
        opportunity_id=opp.id,
        profile_id=opp.profile_id,
        eligibility_passed=True,
        scam_verdict="reject",
        scam_reason={"rule": "human_review", "detail": reason},
        funnel_completed_at=completed_at,
    )

    # Transition status: scam_review_pending -> scam_risk_rejected
    opp = opportunity_service.transition_status(
        session,
        opportunity_id=opp.id,
        new_status=OpportunityStatus.SCAM_RISK_REJECTED,
        reason=f"Human rejected from scam review: {reason}",
        actor=actor,
    )

    return opp
