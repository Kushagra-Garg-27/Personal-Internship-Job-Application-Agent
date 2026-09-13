"""Application attempt endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import get_db
from api.guards import require_submission_secret
from core.repositories import application_repo, opportunity_repo
from core.schemas.opportunity import (
    ApplicationCreate,
    ApplicationResponse,
    ApplicationUpdate,
)
from core.services import application_service, opportunity_service
from core.status import (
    InvalidApplicationTransitionError,
    SubmissionApprovalRequiredError,
)

router = APIRouter(tags=["applications"])


class ApprovalTokenResponse(BaseModel):
    """Response from the request-approval-token endpoint."""

    token: str = Field(description="Server-issued approval token to echo back in confirm-submit.")
    expires_at: str = Field(description="ISO-8601 UTC expiry time of the token (30-minute TTL).")


class ConfirmSubmitRequest(BaseModel):
    """Explicit human confirmation payload for the irreversible submission boundary.

    ``approval_token`` has NO default. It must be the server-issued UUID returned
    by ``POST /applications/{id}/request-approval-token``. Any other value is
    rejected — the pre-submit state itself is never treated as approval.
    """

    approval_token: str = Field(
        ...,
        description=(
            "Server-issued approval token obtained from request-approval-token. "
            "Must match the token stored for this application and must not be expired."
        ),
    )
    approved_by: str = Field(
        default="human_user",
        description="Audit label of the human who approved this submission.",
    )
    platform_confirmed: bool = Field(
        default=False,
        description="Deprecated/unused for browser tier. Kept for API compatibility.",
    )
    confirmation_ref: str | None = Field(
        default=None,
        description="Platform-reported confirmation reference, when available.",
    )
    confirmation_detail: str | None = Field(
        default=None,
        description="Human-readable description of the observed platform confirmation.",
    )


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
    try:
        app = application_service.update_application_status(
            db,
            application_id,
            status=body.status,
            confirmation_ref=body.confirmation_ref,
            submitted_at=body.submitted_at,
            notes=body.notes,
        )
    except (InvalidApplicationTransitionError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if app is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    db.commit()
    db.refresh(app)
    return app


@router.post(
    "/applications/{application_id}/request-approval-token",
    response_model=ApprovalTokenResponse,
    dependencies=[Depends(require_submission_secret)],
)
def request_approval_token(
    application_id: int,
    db: Session = Depends(get_db),
):
    """Mint a single-use, time-boxed server-side approval token (M1).

    Stores the generated UUID in ``applications.approval_token`` (30-minute TTL).
    The client must echo this token back in ``confirm-submit``.  A new call
    invalidates any previously issued token for the same application.
    """
    from core.services import submission_service

    try:
        token = submission_service.issue_approval_token(db, application_id)
        db.commit()
        app = db.get(submission_service.Application, application_id)
        expires_at = app.approval_token_expires_at.isoformat() if app and app.approval_token_expires_at else ""
        return ApprovalTokenResponse(token=token, expires_at=expires_at)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post(
    "/applications/{application_id}/confirm-submit",
    response_model=dict,
    dependencies=[Depends(require_submission_secret)],
)
def confirm_submit_application(
    application_id: int,
    body: ConfirmSubmitRequest,
    db: Session = Depends(get_db),
):
    """Explicit human confirmation action that finalizes an application submission (M1).

    Requires ``body.approval_token`` to be the server-issued UUID from
    ``request-approval-token``. The token must match the DB record and must
    not be expired. No other signal — status, autofill success, form validity,
    timeouts, defaults, or agent assumptions — is ever treated as approval.

    - Stable HTTP tier (Greenhouse/Lever): Authorizes and executes the pending
      draft HTTP submission to the platform API.
    - Experimental browser tier (Internshala/Unstop): Records the approval in
      dedicated columns; background worker performs the Playwright submission.
    """
    from core.services import submission_service

    try:
        result = submission_service.confirm_and_submit(
            db,
            application_id,
            approval_token=body.approval_token,
            approval_actor=body.approved_by,
            platform_confirmed=body.platform_confirmed,
            confirmation_ref=body.confirmation_ref,
            confirmation_detail=body.confirmation_detail,
        )
        return result
    except SubmissionApprovalRequiredError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
