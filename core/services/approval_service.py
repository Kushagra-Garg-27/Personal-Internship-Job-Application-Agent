"""Service layer for final human approval, dismissal, and consolidated dashboard feed (Phase 6).

Implements §2's [HUMAN APPROVES] gate:
- Final approval: confirms resume version, sets `selected_resume_id`, transitions to `ready_to_apply`.
- Dismiss: declines recommended opportunity into terminal `dismissed` state.
- Consolidated dashboard feed: gathers opportunity, scoring verdicts, and available resumes server-side.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from core.models.opportunity import Opportunity
from core.models.profile import Profile
from core.models.resume import Resume
from core.models.scoring import ScoringVerdict
from core.repositories import profile_repo, scoring_repo
from core.services import opportunity_service
from core.status import OpportunityStatus

logger = logging.getLogger(__name__)


def approve_opportunity(
    session: Session,
    opportunity_id: int,
    *,
    resume_id: int | None = None,
    actor: str = "human_user",
) -> Opportunity:
    """Final human approval to prepare an opportunity for application.

    1. Validates opportunity exists and can transition to READY_TO_APPLY.
    2. Resolves target resume: uses provided `resume_id` or defaults to the profile's active resume.
    3. Persists `selected_resume_id` on the opportunity for the Worker (Phase 9) to consume.
    4. Transitions status: RECOMMENDED -> READY_TO_APPLY.
    """
    opp = session.get(Opportunity, opportunity_id)
    if opp is None:
        raise ValueError(f"Opportunity {opportunity_id} not found.")

    # 1. Resolve target resume
    chosen_resume_id: int | None = None

    if resume_id is not None:
        res = session.get(Resume, resume_id)
        if res is None:
            raise ValueError(f"Resume {resume_id} not found.")
        chosen_resume_id = res.id
    else:
        # Default to the active resume on the candidate profile
        active_profile: Profile | None = None
        if opp.profile_id is not None:
            active_profile = session.get(Profile, opp.profile_id)
        if active_profile is None:
            profiles = profile_repo.list_profiles(session)
            if profiles:
                active_profile = profiles[0]

        if active_profile:
            raw_resumes = getattr(active_profile, "resumes", [])
            resumes_list = raw_resumes if isinstance(raw_resumes, list) else ([raw_resumes] if raw_resumes else [])
            for r in resumes_list:
                if getattr(r, "is_active", False):
                    chosen_resume_id = r.id
                    break
            if chosen_resume_id is None and resumes_list:
                chosen_resume_id = resumes_list[0].id

    # 2. Persist chosen resume ID
    opp.selected_resume_id = chosen_resume_id
    session.flush()

    # 3. Transition status to READY_TO_APPLY
    resume_info = f"resume_id={chosen_resume_id}" if chosen_resume_id else "no resume linked"
    opp = opportunity_service.transition_status(
        session,
        opportunity_id=opp.id,
        new_status=OpportunityStatus.READY_TO_APPLY,
        reason=f"Final human approval by {actor} ({resume_info})",
        actor=actor,
    )

    return opp


def dismiss_opportunity(
    session: Session,
    opportunity_id: int,
    *,
    reason: str = "Declined by user during review",
    actor: str = "human_user",
) -> Opportunity:
    """Human dismissal of a recommended opportunity into terminal DISMISSED status."""
    opp = session.get(Opportunity, opportunity_id)
    if opp is None:
        raise ValueError(f"Opportunity {opportunity_id} not found.")

    opp = opportunity_service.transition_status(
        session,
        opportunity_id=opp.id,
        new_status=OpportunityStatus.DISMISSED,
        reason=reason,
        actor=actor,
    )
    return opp


def get_dashboard_feed(
    session: Session,
    *,
    status_filter: str | None = None,
    tier: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Retrieve consolidated opportunity items for dashboard views.

    Assembles:
    - Opportunity fields
    - Scoring verdict (relevance score, matched skills, explanations)
    - Eligibility verdict
    - Scam & risk classification
    - Available candidate resumes for selection
    """
    # Base query
    stmt = select(Opportunity)
    count_stmt = select(func.count(Opportunity.id))

    if status_filter:
        stmt = stmt.where(Opportunity.status == status_filter)
        count_stmt = count_stmt.where(Opportunity.status == status_filter)

    if tier:
        stmt = stmt.where(Opportunity.reliability_tier == tier)
        count_stmt = count_stmt.where(Opportunity.reliability_tier == tier)

    total = session.scalar(count_stmt) or 0

    # Order by discovered_at desc
    stmt = stmt.order_by(Opportunity.discovered_at.desc()).limit(limit).offset(offset)
    opportunities = list(session.scalars(stmt).all())

    # Pre-fetch profiles and resumes to avoid N+1
    profiles = profile_repo.list_profiles(session)
    default_resumes: list[dict[str, Any]] = []
    profile_map: dict[int, list[dict[str, Any]]] = {}

    for p in profiles:
        raw_res = getattr(p, "resumes", [])
        r_list = raw_res if isinstance(raw_res, list) else ([raw_res] if raw_res else [])
        serialized_res = [
            {
                "id": r.id,
                "version": r.version,
                "original_filename": r.original_filename,
                "is_active": r.is_active,
                "uploaded_at": getattr(r, "uploaded_at", None),
            }
            for r in r_list
        ]
        profile_map[p.id] = serialized_res
        if not default_resumes and serialized_res:
            default_resumes = serialized_res

    # Assemble feed items
    items: list[dict[str, Any]] = []
    for opp in opportunities:
        verdict: ScoringVerdict | None = None
        if opp.scoring_verdicts:
            verdict = sorted(opp.scoring_verdicts, key=lambda v: v.created_at, reverse=True)[0]

        resumes = profile_map.get(opp.profile_id, default_resumes)

        item = {
            "id": opp.id,
            "dedup_hash": opp.dedup_hash,
            "status": opp.status,
            "reliability_tier": opp.reliability_tier,
            "source": opp.source,
            "title": opp.title,
            "company": opp.company,
            "description": opp.description,
            "url": opp.url,
            "location": opp.location,
            "salary_min": opp.salary_min,
            "salary_max": opp.salary_max,
            "posted_at": opp.posted_at,
            "deadline_at": opp.deadline_at,
            "discovered_at": opp.discovered_at,
            "profile_id": opp.profile_id,
            "selected_resume_id": opp.selected_resume_id,
            # Scoring
            "relevance_score": verdict.relevance_score if verdict else None,
            "relevance_explanation": verdict.relevance_explanation if verdict else None,
            "model_name": verdict.model_name if verdict else None,
            # Eligibility
            "eligibility_passed": verdict.eligibility_passed if verdict else None,
            "eligibility_reason": verdict.eligibility_reason if verdict else None,
            # Scam & risk
            "scam_verdict": verdict.scam_verdict if verdict else None,
            "scam_reason": verdict.scam_reason if verdict else None,
            "llm_verdict": verdict.llm_verdict if verdict else None,
            "llm_reasoning": verdict.llm_reasoning if verdict else None,
            "quota_deferred_at": verdict.quota_deferred_at if verdict else None,
            "funnel_completed_at": verdict.funnel_completed_at if verdict else None,
            # Resumes
            "available_resumes": resumes,
        }
        items.append(item)

    # If status is 'recommended', sort in Python by relevance_score desc (nulls last)
    if status_filter == OpportunityStatus.RECOMMENDED:
        items.sort(
            key=lambda x: (x["relevance_score"] is not None, x["relevance_score"] or 0.0),
            reverse=True,
        )

    return items, total
