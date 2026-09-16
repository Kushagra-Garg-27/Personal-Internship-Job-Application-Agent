"""Application attempt endpoints."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import get_db
from api.guards import require_submission_secret
from core.config import settings
from core.repositories import application_repo, opportunity_repo
from core.schemas.opportunity import (
    ApplicationCreate,
    ApplicationResponse,
    ApplicationUpdate,
)
from core.services import application_service, opportunity_service
from core.services.application_service import MANUAL_FINAL_ACTION_REASONS
from core.status import (
    InvalidApplicationTransitionError,
    SubmissionApprovalRequiredError,
)

router = APIRouter(tags=["applications"])


class GuardStatusResponse(BaseModel):
    """Submission guard status and CLI guidance."""

    mode: str = Field(description="Submission guard mode ('required' or 'disabled').")
    cli_command: str = Field(description="Local privileged approval command template.")


class PendingQueueItem(BaseModel):
    """A single application in the pending-submission queue (M5)."""

    application_id: int
    opportunity_id: int
    opportunity_title: str | None = None
    opportunity_company: str | None = None
    opportunity_url: str | None = None
    application_status: str
    queue_state: str = Field(description="Derived queue state (APPROVED_PENDING, CLAIMED_IN_PROGRESS, MANUAL_REVIEW, REVOKED, SUBMITTED).")
    approved_at: str | None = None  # ISO-8601 UTC
    approved_by: str | None = None
    # Claim info — non-null when claimed by worker
    claimed_at: str | None = None
    claimed_by: str | None = None
    # Revocation info — non-null when the approval has been revoked
    approval_revoked_at: str | None = None
    approval_revoked_by: str | None = None
    approval_revocation_reason: str | None = None
    # Confirmation ref — non-null when submitted
    confirmation_ref: str | None = None
    # M2C bounded manual-handoff code only; never browser/session diagnostics.
    manual_review_reason: str | None = None


class RevokeApprovalRequest(BaseModel):
    """Input for revoking a pending approval (M5)."""

    revoked_by: str = Field(
        default="human_operator",
        description="Audit label of the operator performing the revocation.",
    )
    reason: str | None = Field(
        default=None,
        description="Human-readable reason for revocation.",
    )

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


@router.get(
    "/applications/guard-status",
    response_model=GuardStatusResponse,
)
def get_submission_guard_status():
    """Return the active submission guard mode and privileged CLI guidance.

    Does NOT expose the configured secret value or length.
    """
    return GuardStatusResponse(
        mode=settings.SUBMISSION_GUARD_MODE,
        cli_command="python scripts/approve_submission.py",
    )


@router.get(
    "/applications/pending-queue",
    response_model=list[PendingQueueItem],
)
def get_pending_queue(
    queue_state: str | None = None,
    db: Session = Depends(get_db),
):
    """Return all applications in the pending-submission queue (M5).

    Returns applications across all operational queue states:
    APPROVED_PENDING, CLAIMED_IN_PROGRESS, MANUAL_REVIEW, REVOKED, SUBMITTED.

    Optional query param ``queue_state`` filters results server-side.
    No submission-secret is required — this endpoint is read-only.
    """
    from sqlalchemy import or_, select
    from core.models.opportunity import Application, Opportunity
    from core.services.submission_service import derive_queue_state

    stmt = (
        select(Application, Opportunity)
        .join(Opportunity, Application.opportunity_id == Opportunity.id)
        .where(
            or_(
                Application.approved_at.is_not(None),
                Application.approval_revoked_at.is_not(None),
                Application.submission_claimed_at.is_not(None),
            )
        )
        .order_by(Application.id.desc())
    )
    rows = db.execute(stmt).all()

    items = []
    for app, opp in rows:
        state = derive_queue_state(app, opp)
        if queue_state and state.upper() != queue_state.upper():
            continue

        confirmation_ref = app.confirmation_ref
        if not confirmation_ref and app.notes:
            try:
                notes_data = json.loads(app.notes) if isinstance(app.notes, str) else app.notes
                if isinstance(notes_data, dict):
                    sub_conf = notes_data.get("submission_confirmation")
                    if isinstance(sub_conf, dict) and sub_conf.get("confirmation_ref"):
                        confirmation_ref = sub_conf.get("confirmation_ref")
                    elif notes_data.get("confirmation_ref"):
                        confirmation_ref = notes_data.get("confirmation_ref")
            except Exception:
                pass

        items.append(
            PendingQueueItem(
                application_id=app.id,
                opportunity_id=opp.id,
                opportunity_title=opp.title,
                opportunity_company=opp.company,
                opportunity_url=opp.url,
                application_status=app.status,
                queue_state=state,
                approved_at=app.approved_at.isoformat() if app.approved_at else None,
                approved_by=app.approved_by,
                claimed_at=app.submission_claimed_at.isoformat() if app.submission_claimed_at else None,
                claimed_by=app.claimed_by,
                approval_revoked_at=app.approval_revoked_at.isoformat() if app.approval_revoked_at else None,
                approval_revoked_by=app.approval_revoked_by,
                approval_revocation_reason=app.approval_revocation_reason,
                confirmation_ref=confirmation_ref,
                manual_review_reason=(
                    app.manual_review_reason
                    if state == "MANUAL_REVIEW"
                    and app.manual_review_reason in MANUAL_FINAL_ACTION_REASONS
                    else None
                ),
            )
        )
    return items


@router.get(
    "/applications/{application_id}",
    response_model=ApplicationResponse,
)
def get_application(
    application_id: int,
    db: Session = Depends(get_db),
):
    """Retrieve an application record by its ID."""
    app = application_repo.get_application(db, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found.")
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



@router.post(
    "/applications/{application_id}/revoke-approval",
    response_model=dict,
)
def revoke_application_approval(
    application_id: int,
    body: RevokeApprovalRequest,
    db: Session = Depends(get_db),
):
    """Atomically revoke a pending approval before the worker claims it (M5).

    Returns 200 on success, 409 if the worker already claimed the application,
    400 for other invalid state (e.g. no pending approval).

    Note: Revocation removes authority rather than granting it. Therefore, this
    endpoint does NOT require the submission secret, allowing dashboard operators
    to cancel a pending approval without holding the submission secret.
    All approval-granting endpoints remain strictly guarded.
    """
    from core.services import submission_service

    try:
        result = submission_service.revoke_approval(
            db,
            application_id,
            revoked_by=body.revoked_by,
            reason=body.reason,
        )
        db.commit()
        return result
    except ValueError as exc:
        msg = str(exc)
        if "claimed" in msg:
            raise HTTPException(status_code=409, detail=msg)
        raise HTTPException(status_code=400, detail=msg)
