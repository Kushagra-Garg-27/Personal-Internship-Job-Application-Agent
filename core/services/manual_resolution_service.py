"""Service layer for manual resolution and safe resume logic."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models.opportunity import Opportunity, Application, ManualResolutionRequest
from core.status import OpportunityStatus, ApplicationStatus
from core.services import application_service, opportunity_service

# ── MRR lifecycle vocabulary (U11.1) ─────────────────────────────────────────
# The ``status`` column already models the full lifecycle; these constants keep
# every read/write of that column in one place so no caller can silently rely on
# a magic string drifting out of sync with the model's @validates set.
STATUS_PENDING = "pending"
STATUS_RESOLVED = "resolved"
STATUS_INVALIDATED = "invalidated"

# A request is "active" while it still represents the current unresolved state of
# its field: a PENDING request is awaiting a human answer, and a RESOLVED request
# is a live answer.  An INVALIDATED request is historical audit evidence only.
ACTIVE_STATUSES = (STATUS_PENDING, STATUS_RESOLVED)


def get_active_resolutions(session: Session, application_id: int) -> dict[str, str]:
    """Return the currently-active manual resolutions for one application.

    Deterministic by construction (U11.1):

    * Only ``resolved`` requests with a non-empty ``resolved_value`` are
      considered — PENDING requests are questions, not answers, and
      INVALIDATED requests are superseded history that must never be replayed as
      an active answer.
    * Rows are read via an explicit ``ORDER BY id DESC`` and the *first* row per
      logical field wins, so the result never depends on incidental database
      row-return order or on dictionary-iteration order.
    * Because creating a new request invalidates every prior active request for
      the same field (see :func:`invalidate_active_requests`), at most one active
      resolution exists per field in normal operation; the deterministic
      tie-break is a safety net for rows written before that invariant existed.

    Returns a ``{field_name: resolved_value}`` map, which is exactly what the
    Unstop adapter consumes as application-scoped manual answers.
    """
    rows = session.scalars(
        select(ManualResolutionRequest)
        .where(
            ManualResolutionRequest.application_id == application_id,
            ManualResolutionRequest.status == STATUS_RESOLVED,
        )
        .order_by(ManualResolutionRequest.id.desc())
    ).all()

    active: dict[str, str] = {}
    for row in rows:
        # Preserve the previous truthiness semantics: an empty resolution is not
        # an answer.  The first row per field wins (latest resolved request).
        if row.resolved_value and row.field_name not in active:
            active[row.field_name] = row.resolved_value
    return active


def invalidate_active_requests(
    session: Session,
    application_id: int,
    field_name: str,
    *,
    reason: str = "superseded_by_new_request",
    actor: str = "worker",
) -> int:
    """Explicitly retire every active MRR for one application field (U11.1).

    A previously RESOLVED answer that is re-validated against the current live
    form and no longer matches the offered option set is no longer a trustworthy
    answer.  This marks it ``invalidated`` so it can never again be treated as an
    active resolution, while the row itself (and its resolution metadata) is kept
    for audit.  A superseded PENDING request is retired the same way.

    The row is never deleted and its ``resolved_value`` / ``resolved_at`` /
    ``resolved_by`` columns are left untouched — the historical answer stays
    inspectable.  Only ``status`` moves to ``invalidated``.

    Returns the number of requests retired.
    """
    rows = session.scalars(
        select(ManualResolutionRequest)
        .where(
            ManualResolutionRequest.application_id == application_id,
            ManualResolutionRequest.field_name == field_name,
            ManualResolutionRequest.status.in_(ACTIVE_STATUSES),
        )
    ).all()

    count = 0
    for row in rows:
        row.status = STATUS_INVALIDATED
        count += 1

    if count:
        session.flush()
    return count


def resolve_request(
    session: Session,
    resolution_id: int,
    selected_value: str,
    actor: str,
) -> ManualResolutionRequest:
    """Resolve a pending manual resolution request with a user-selected value.

    Only a PENDING request is resolvable.  An INVALIDATED request is superseded
    history and must never be re-resolved — a new PENDING request represents the
    current unresolved state instead.
    """
    req = session.get(ManualResolutionRequest, resolution_id)
    if not req:
        raise ValueError(f"Manual resolution request {resolution_id} not found.")

    if req.status != STATUS_PENDING:
        raise ValueError(f"Request {resolution_id} is {req.status}, not pending.")

    # Validation: the selected value MUST be one of the available options
    if selected_value not in req.available_options:
        raise ValueError(f"Selected value {selected_value!r} is not in the available options.")

    req.status = STATUS_RESOLVED
    req.resolved_value = selected_value
    req.resolved_at = datetime.now(timezone.utc)
    req.resolved_by = actor

    session.commit()
    return req

def resume_application(
    session: Session,
    application_id: int,
    actor: str = "human",
) -> Application:
    """Queue an application for resume after manual resolutions are complete.

    Transitions the application back to PENDING and the opportunity back to
    READY_TO_APPLY.
    """
    app = session.get(Application, application_id)
    if not app:
        raise ValueError(f"Application {application_id} not found.")

    opp = session.get(Opportunity, app.opportunity_id)
    if not opp:
        raise ValueError(f"Opportunity {app.opportunity_id} not found.")

    if opp.status != OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value:
        raise ValueError(f"Opportunity {opp.id} is not in MANUAL_APPLICATION_REQUIRED state.")

    # U11.1: a submitted application is terminal — it must never be resumed, even
    # if a stale manual resolution for it still exists.  FAILED is the only
    # application status that may re-enter the fill pipeline.
    if app.status == ApplicationStatus.SUBMITTED.value:
        raise ValueError(
            f"Application {app.id} is already submitted; it cannot be resumed."
        )

    if app.status != ApplicationStatus.FAILED.value:
        raise ValueError(f"Application {app.id} must be FAILED to be resumed.")

    # Check that every active resolution for this application is resolved.
    # INVALIDATED requests are history and do not block the resume; only an
    # outstanding PENDING request is an unanswered question.
    for req in app.manual_resolutions:
        if req.status == STATUS_PENDING:
            raise ValueError(f"Cannot resume application {app.id}: resolution {req.id} is still pending.")

    # Transition the application to PENDING so the worker will pick it up
    application_service.transition_application_status(
        session,
        app.id,
        ApplicationStatus.PENDING,
        notes="Resumed via manual resolution",
        reason="resume_manual_resolution",
    )

    # Transition the opportunity to READY_TO_APPLY
    opportunity_service.transition_status(
        session,
        opp.id,
        OpportunityStatus.READY_TO_APPLY,
        reason="Manual resolution completed",
        actor=actor,
    )

    session.commit()
    return app
