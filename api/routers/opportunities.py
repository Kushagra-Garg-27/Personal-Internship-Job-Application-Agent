"""Opportunity CRUD, status transition, and history endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from api.deps import get_db
from core.repositories import opportunity_repo, scoring_repo, status_history_repo
from core.schemas.opportunity import (
    OpportunityCreate,
    OpportunityResponse,
    OpportunityUpdate,
    ScamReviewApproveRequest,
    ScamReviewRejectRequest,
    StatusHistoryResponse,
    StatusTransitionRequest,
)
from core.schemas.scoring import ScoringVerdictResponse
from core.services import opportunity_service, scam_review_service
from core.status import InvalidTransitionError

router = APIRouter(prefix="/opportunities", tags=["opportunities"])


@router.post("/", response_model=OpportunityResponse, status_code=status.HTTP_201_CREATED)
def create_or_upsert_opportunity(body: OpportunityCreate, db: Session = Depends(get_db)):
    """Create a new opportunity or update an existing one (dedup by company+title+url).

    Returns 201 for new, 200 for updated — but we always return 201 here
    to keep the endpoint simple; the ``created`` flag in the response body
    can be used to distinguish if needed.
    """
    extra = body.model_dump(exclude={"title", "company", "url"})
    opp, created = opportunity_service.upsert_opportunity(
        db, title=body.title, company=body.company, url=body.url, **extra
    )
    db.commit()
    db.refresh(opp)
    return opp


@router.get("/", response_model=list[OpportunityResponse])
def list_opportunities(
    status_filter: str | None = Query(None, alias="status"),
    tier: str | None = None,
    company: str | None = None,
    source: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List opportunities with optional filters."""
    return opportunity_repo.list_opportunities(
        db, status=status_filter, tier=tier, company=company, source=source,
        limit=limit, offset=offset,
    )


# ── Scam review endpoints (Phase 5) ──────────────────────────────────────


@router.get("/scam-review/pending", response_model=list[OpportunityResponse])
def list_pending_scam_reviews(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """List all opportunities pending human scam/risk review."""
    return scam_review_service.list_pending_scam_reviews(db, limit=limit, offset=offset)


@router.post("/{opportunity_id}/scam-review/approve", response_model=OpportunityResponse)
def approve_scam_review(
    opportunity_id: int,
    body: ScamReviewApproveRequest = ScamReviewApproveRequest(),
    db: Session = Depends(get_db),
):
    """Approve an opportunity pending scam review.

    Runs Stage 3 Relevance scoring and transitions status to RECOMMENDED.
    """
    try:
        opp = scam_review_service.approve_pending_opportunity(
            db, opportunity_id, actor=body.actor
        )
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    db.refresh(opp)
    return opp


@router.post("/{opportunity_id}/scam-review/reject", response_model=OpportunityResponse)
def reject_scam_review(
    opportunity_id: int,
    body: ScamReviewRejectRequest = ScamReviewRejectRequest(),
    db: Session = Depends(get_db),
):
    """Reject an opportunity pending scam review.

    Stores the content hash into scam_content_signatures and transitions status to SCAM_RISK_REJECTED.
    """
    try:
        opp = scam_review_service.reject_pending_opportunity(
            db, opportunity_id, reason=body.reason, actor=body.actor
        )
    except ValueError as exc:
        msg = str(exc)
        if "not found" in msg.lower():
            raise HTTPException(status_code=404, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    db.refresh(opp)
    return opp


@router.get("/{opportunity_id}", response_model=OpportunityResponse)
def get_opportunity(opportunity_id: int, db: Session = Depends(get_db)):
    """Get a single opportunity by ID."""
    opp = opportunity_repo.get_opportunity(db, opportunity_id)
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found.")
    return opp


@router.patch("/{opportunity_id}", response_model=OpportunityResponse)
def update_opportunity(
    opportunity_id: int, body: OpportunityUpdate, db: Session = Depends(get_db)
):
    """Update opportunity fields (NOT status — use the transition endpoint)."""
    update_data = body.model_dump(exclude_unset=True)
    if not update_data:
        raise HTTPException(status_code=400, detail="No fields to update.")
    try:
        opp = opportunity_repo.update_opportunity(db, opportunity_id, **update_data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found.")
    db.commit()
    db.refresh(opp)
    return opp


@router.delete("/{opportunity_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_opportunity(opportunity_id: int, db: Session = Depends(get_db)):
    """Delete an opportunity and all related records."""
    if not opportunity_repo.delete_opportunity(db, opportunity_id):
        raise HTTPException(status_code=404, detail="Opportunity not found.")
    db.commit()


# ── Status transitions ───────────────────────────────────────────────────


@router.post("/{opportunity_id}/transition", response_model=OpportunityResponse)
def transition_status(
    opportunity_id: int,
    body: StatusTransitionRequest,
    db: Session = Depends(get_db),
):
    """Transition an opportunity's status.

    Validates the transition against the allowed-transition map.  Returns
    409 if the transition is illegal, 404 if the opportunity doesn't exist.
    """
    try:
        opp = opportunity_service.transition_status(
            db,
            opportunity_id,
            body.new_status,
            reason=body.reason,
            actor=body.actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.commit()
    db.refresh(opp)
    return opp


@router.get("/{opportunity_id}/history", response_model=list[StatusHistoryResponse])
def get_status_history(opportunity_id: int, db: Session = Depends(get_db)):
    """Get the full status-transition history for an opportunity."""
    opp = opportunity_repo.get_opportunity(db, opportunity_id)
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found.")
    return status_history_repo.get_history(db, opportunity_id)


# ── Scoring verdicts (Phase 4) ──────────────────────────────────────────


@router.get("/{opportunity_id}/verdict", response_model=ScoringVerdictResponse)
def get_opportunity_verdict(opportunity_id: int, db: Session = Depends(get_db)):
    """Get the latest scoring verdict for an opportunity."""
    opp = opportunity_repo.get_opportunity(db, opportunity_id)
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found.")
    verdict = scoring_repo.get_verdict_by_opportunity(db, opportunity_id)
    if verdict is None:
        raise HTTPException(status_code=404, detail="No verdict found for this opportunity.")
    return verdict


@router.post("/{opportunity_id}/evaluate", response_model=ScoringVerdictResponse)
def evaluate_opportunity_endpoint(opportunity_id: int, db: Session = Depends(get_db)):
    """Trigger on-demand evaluation of an opportunity through the funnel."""
    opp = opportunity_repo.get_opportunity(db, opportunity_id)
    if opp is None:
        raise HTTPException(status_code=404, detail="Opportunity not found.")
    from core.funnel.runner import evaluate_opportunity

    verdict = evaluate_opportunity(db, opp, actor="api")
    db.commit()
    db.refresh(verdict)
    return verdict

