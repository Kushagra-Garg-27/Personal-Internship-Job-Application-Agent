"""Application attempt endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from api.deps import get_db
from core.repositories import application_repo, opportunity_repo
from core.schemas.opportunity import (
    ApplicationCreate,
    ApplicationResponse,
    ApplicationUpdate,
)
from core.services import application_service, opportunity_service

router = APIRouter(tags=["applications"])


def _ensure_opportunity_exists(opportunity_id: int, db: Session) -> None:
    if opportunity_repo.get_opportunity(db, opportunity_id) is None:
        raise HTTPException(status_code=404, detail="Opportunity not found.")


@router.post(
    "/opportunities/{opportunity_id}/applications",
    response_model=ApplicationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_application(
    opportunity_id: int,
    body: ApplicationCreate,
    db: Session = Depends(get_db),
):
    """Create a new application attempt for an opportunity.

    Uses ``mark_submission_attempted`` to write a durable record before
    any hypothetical risky action (Phase 9 will call this).
    """
    _ensure_opportunity_exists(opportunity_id, db)
    app = opportunity_service.mark_submission_attempted(
        db,
        opportunity_id,
        resume_id=body.resume_id,
        adapter_name=body.adapter_name,
    )
    if body.notes:
        app.notes = body.notes
        db.flush()
    db.commit()
    db.refresh(app)
    return app


@router.get(
    "/opportunities/{opportunity_id}/applications",
    response_model=list[ApplicationResponse],
)
def list_applications(opportunity_id: int, db: Session = Depends(get_db)):
    """List all application attempts for an opportunity."""
    _ensure_opportunity_exists(opportunity_id, db)
    return application_repo.list_by_opportunity(db, opportunity_id)


@router.patch(
    "/applications/{application_id}",
    response_model=ApplicationResponse,
)
def update_application(
    application_id: int,
    body: ApplicationUpdate,
    db: Session = Depends(get_db),
):
    """Update an application's status, confirmation ref, etc."""
    app = application_service.update_application_status(
        db,
        application_id,
        status=body.status,
        confirmation_ref=body.confirmation_ref,
        submitted_at=body.submitted_at,
        notes=body.notes,
    )
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    db.commit()
    db.refresh(app)
    return app


@router.post(
    "/applications/{application_id}/confirm-submit",
    response_model=dict,
)
def confirm_submit_application(
    application_id: int,
    db: Session = Depends(get_db),
):
    """Human confirmation action to execute pending draft submission (Phase 9).
    Submits the reviewed draft payload to the platform API (Greenhouse/Lever).
    """
    from worker.engine.filler import ApplicationFiller

    filler = ApplicationFiller()
    try:
        result = filler.confirm_and_submit(db, application_id)
        return result
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

