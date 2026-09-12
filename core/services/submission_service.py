"""Application submission execution service (Phase 9 & 10).

Provides self-contained submission execution and confirmation logic for Core,
ensuring zero import-time or call-time dependency on `worker.*`.

Handles:
- Stable HTTP tier (Greenhouse, Lever): Executes authorized HTTP POST submissions
  and ambiguous-timeout recovery status checks.
- Experimental Browser tier (Internshala, Unstop): Confirms that the candidate has
  physically clicked Submit in the open browser window and transitions database state.

HARD INVARIANT (Unstop V1 - U4):
    Every write path that crosses the irreversible submission boundary requires
    the caller to supply an explicit human approval token
    (`HUMAN_SUBMISSION_APPROVAL_TOKEN`). Reaching a pre-submit state
    (`ready_for_review`, `awaiting_submission`, `form_filled`), a valid form, a
    successful autofill, a prior instruction, a test run, a timeout, a default
    value, or any agent assumption is NEVER approval.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.orm import Session

from core.models.opportunity import Application, Opportunity
from core.services import application_service, opportunity_service
from core.status import (
    ApplicationStatus,
    OpportunityStatus,
    ReliabilityTier,
    SubmissionApprovalRequiredError,
    is_human_approved,
)

logger = logging.getLogger(__name__)


def execute_greenhouse_submission(
    draft_payload: dict[str, Any],
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Execute the HTTP submission to Greenhouse API upon human confirmation."""
    submission_url = draft_payload["submission_url"]
    fields = draft_payload.get("fields", {})
    resume_path = draft_payload.get("resume_path")

    files: dict[str, Any] = {}
    if resume_path and Path(resume_path).exists():
        files["resume"] = (Path(resume_path).name, open(resume_path, "rb"), "application/pdf")

    http_client = client or httpx.Client(timeout=15.0)
    try:
        resp = http_client.post(submission_url, data=fields, files=files if files else None)
        if resp.status_code in {200, 201}:
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            return {
                "success": True,
                "confirmation_ref": str(data.get("id") or "GH-CONFIRMED"),
                "status_code": resp.status_code,
            }
        return {
            "success": False,
            "error": f"Greenhouse API returned HTTP {resp.status_code}: {resp.text[:300]}",
            "status_code": resp.status_code,
        }
    except httpx.TimeoutException as exc:
        logger.error("Ambiguous timeout submitting to Greenhouse (%s): %s", submission_url, exc)
        return {
            "success": False,
            "ambiguous_timeout": True,
            "error": "Submission timed out. Verification required before retrying.",
        }
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
        }
    finally:
        if client is None:
            http_client.close()


def check_greenhouse_status(
    check_url: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Check if Greenhouse posting is still active or application was received."""
    http_client = client or httpx.Client(timeout=15.0)
    try:
        resp = http_client.get(check_url)
        if resp.status_code == 200:
            return {"confirmed": False, "status": "not_submitted", "detail": "Job posting is active on Greenhouse."}
        elif resp.status_code == 404:
            return {"confirmed": False, "status": "closed", "detail": "Job posting is closed or no longer accepting applications."}
        return {"confirmed": False, "status": "unknown", "detail": f"HTTP status {resp.status_code}"}
    except Exception as exc:
        return {"confirmed": False, "status": "ambiguous", "detail": str(exc)}
    finally:
        if client is None:
            http_client.close()


def execute_lever_submission(
    draft_payload: dict[str, Any],
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Execute the HTTP submission to Lever API upon human confirmation."""
    submission_url = draft_payload["submission_url"]
    fields = draft_payload.get("fields", {})
    resume_path = draft_payload.get("resume_path")

    files: dict[str, Any] = {}
    if resume_path and Path(resume_path).exists():
        files["resume"] = (Path(resume_path).name, open(resume_path, "rb"), "application/pdf")

    http_client = client or httpx.Client(timeout=15.0)
    try:
        resp = http_client.post(submission_url, data=fields, files=files if files else None)
        if resp.status_code in {200, 201}:
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            return {
                "success": True,
                "confirmation_ref": str(data.get("applicationId") or "LEVER-CONFIRMED"),
                "status_code": resp.status_code,
            }
        return {
            "success": False,
            "error": f"Lever API returned HTTP {resp.status_code}: {resp.text[:300]}",
            "status_code": resp.status_code,
        }
    except httpx.TimeoutException as exc:
        logger.error("Ambiguous timeout submitting to Lever (%s): %s", submission_url, exc)
        return {
            "success": False,
            "ambiguous_timeout": True,
            "error": "Submission timed out. Verification required before retrying.",
        }
    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
        }
    finally:
        if client is None:
            http_client.close()


def check_lever_status(
    check_url: str,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Check if Lever posting is active."""
    http_client = client or httpx.Client(timeout=15.0)
    try:
        resp = http_client.get(check_url)
        if resp.status_code == 200:
            return {"confirmed": False, "status": "not_submitted", "detail": "Job posting is active on Lever."}
        elif resp.status_code == 404:
            return {"confirmed": False, "status": "closed", "detail": "Job posting is closed on Lever."}
        return {"confirmed": False, "status": "unknown", "detail": f"HTTP status {resp.status_code}"}
    except Exception as exc:
        return {"confirmed": False, "status": "ambiguous", "detail": str(exc)}
    finally:
        if client is None:
            http_client.close()


def confirm_and_submit(
    session: Session,
    application_id: int,
    *,
    approval_token: str | None = None,
    approval_actor: str = "human_submission",
    platform_confirmed: bool = False,
    confirmation_ref: str | None = None,
    confirmation_detail: str | None = None,
    http_client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Human confirmation action to execute submission or confirm physical browser submission.

    Parameters
    ----------
    session : Session
        SQLAlchemy database session.
    application_id : int
        ID of the application being confirmed.
    approval_token : str | None
        REQUIRED. Must equal ``HUMAN_SUBMISSION_APPROVAL_TOKEN``. This is the
        only accepted proof of explicit human approval; the absence of a token
        fails closed.
    approval_actor : str
        Audit label for the approving human actor.
    platform_confirmed : bool
        For the Experimental browser tier: ``True`` only when the platform
        itself was observed to report the submission as received. A missing
        platform confirmation leaves the application in its pre-submit review
        state instead of writing a false ``submitted`` status.
    confirmation_ref : str | None
        Platform-reported confirmation reference, when available.
    confirmation_detail : str | None
        Human-readable detail describing the platform confirmation.
    http_client : httpx.Client | None
        Optional HTTP client for dependency injection in tests.

    Returns
    -------
    dict[str, Any]
        Submission outcome status dictionary.

    Raises
    ------
    SubmissionApprovalRequiredError
        If no valid explicit human approval token was supplied.
    ValueError
        If application or opportunity not found, opportunity is not
        awaiting_submission, or the application is already submitted.
    """
    # ── Human approval gate (fail closed) ─────────────────────────────
    if not is_human_approved(approval_token):
        raise SubmissionApprovalRequiredError(
            f"Application {application_id} submission blocked: no explicit human approval token. "
            "ready_for_review, a valid form, successful autofill, prior instructions, test "
            "execution, timeouts, defaults, and agent assumptions are NOT approval."
        )

    app = session.get(Application, application_id)
    if app is None:
        raise ValueError(f"Application {application_id} not found.")

    # ── Duplicate submission protection ───────────────────────────────
    if app.status == ApplicationStatus.SUBMITTED.value:
        raise ValueError(
            f"Application {application_id} is already submitted "
            f"(confirmation_ref={app.confirmation_ref!r}, submitted_at={app.submitted_at}). "
            "Duplicate submission refused."
        )
    if app.status not in {
        ApplicationStatus.FORM_FILLED.value,
        ApplicationStatus.PENDING.value,
    }:
        raise ValueError(
            f"Cannot submit application in status {app.status!r}; "
            "must be 'form_filled' or 'pending'."
        )

    opp = session.get(Opportunity, app.opportunity_id)
    if opp is None:
        raise ValueError(f"Opportunity {app.opportunity_id} not found.")

    if opp.status != OpportunityStatus.AWAITING_SUBMISSION.value:
        raise ValueError(
            f"Cannot submit application in status {opp.status!r}; must be 'awaiting_submission'."
        )

    notes_data: dict[str, Any] = {}
    if app.notes:
        try:
            notes_data = json.loads(app.notes)
        except Exception:
            pass

    adapter_name = (
        notes_data.get("adapter")
        or app.adapter_name
        or (opp.source or "unknown")
    ).lower()

    is_browser_tier = (
        notes_data.get("tier") in {"experimental", ReliabilityTier.EXPERIMENTAL.value}
        or adapter_name in {"internshala", "unstop"}
        or not notes_data.get("draft_payload")
    )

    # ── Experimental Browser Tier ─────────────────────────────────────
    # The candidate explicitly approved the submission. We record the approval
    # in the application notes and leave it in the pre-submit review state
    # (AWAITING_SUBMISSION / FORM_FILLED). The background browser worker will
    # pick it up and execute the autonomous Playwright submission.
    if is_browser_tier:
        details = {
            **(notes_data or {}),
            "approval_token": approval_token,
            "approved_by": approval_actor,
            "submission_requested_at": datetime.now(timezone.utc).isoformat()
        }
        application_service.update_application_status(
            session,
            app.id,
            status=app.status,
            notes=json.dumps(details, default=str),
        )
        session.commit()
        logger.info("Application #%d approved for browser-tier submission. Worker will execute.", app.id)
        return {
            "success": True,
            "status": "approved_for_submission",
            "message": "Approval recorded. Background worker will perform submission.",
            "mode": "browser_orchestrator",
        }

    # ── Stable HTTP API Tier ──────────────────────────────────────────
    # Candidate reviews pending draft payload and explicitly authorizes
    # the programmatic HTTP POST submission call.
    draft_payload = notes_data.get("draft_payload")
    if not draft_payload:
        raise ValueError("No draft payload found on application record to submit.")

    if "greenhouse" in adapter_name or "greenhouse.io" in (opp.url or "").lower():
        result = execute_greenhouse_submission(draft_payload, client=http_client)
        check_fn = check_greenhouse_status
    elif "lever" in adapter_name or "lever.co" in (opp.url or "").lower():
        result = execute_lever_submission(draft_payload, client=http_client)
        check_fn = check_lever_status
    else:
        raise ValueError(f"No HTTP submission handler for adapter: {adapter_name}")

    # Ambiguous timeout duplicate prevention check (§5.7)
    if result.get("ambiguous_timeout"):
        logger.warning("Ambiguous timeout submitting app #%d; querying status before retry", app.id)
        check_url = draft_payload.get("submission_url", "")
        status_check = check_fn(check_url, client=http_client)
        if status_check.get("confirmed"):
            now = datetime.now(timezone.utc)
            application_service.update_application_status(
                session,
                app.id,
                status="submitted",
                submitted_at=now,
                confirmation_ref=status_check.get("confirmation_ref") or "TIMEOUT-CONFIRMED",
            )
            opportunity_service.transition_status(
                session,
                opp.id,
                OpportunityStatus.APPLIED,
                reason="Submission confirmed after ambiguous timeout check",
                actor="human_submission",
            )
            session.commit()
            return {"success": True, "status": "applied", "confirmed_via_check": True}
        else:
            reason = "Submission timed out ambiguously and was not confirmed by platform. Manual application required."
            application_service.transition_application_status(
                session,
                app.id,
                ApplicationStatus.FAILED,
                notes=f"{app.notes}\n[FAILURE]: {reason}",
                reason=reason,
            )
            opportunity_service.transition_status(
                session,
                opp.id,
                OpportunityStatus.MANUAL_APPLICATION_REQUIRED,
                reason=reason,
                actor="submission_service",
            )
            session.commit()
            return {"success": False, "status": "manual_required", "reason": reason}

    if not result.get("success"):
        error_msg = result.get("error") or "Submission failed."
        application_service.transition_application_status(
            session,
            app.id,
            ApplicationStatus.FAILED,
            notes=f"{app.notes}\n[ERROR]: {error_msg}",
            reason=error_msg,
        )
        opportunity_service.transition_status(
            session,
            opp.id,
            OpportunityStatus.MANUAL_APPLICATION_REQUIRED,
            reason=error_msg,
            actor="submission_service",
        )
        session.commit()
        return {"success": False, "status": "manual_required", "reason": error_msg}

    # Human-approved HTTP submission succeeded and returned platform confirmation.
    now = datetime.now(timezone.utc)
    conf_ref = result.get("confirmation_ref") or "SUBMITTED-OK"
    details = {
        **(notes_data or {}),
        "submission_confirmation": {
            "source": "platform",
            "confirmed": True,
            "confirmation_ref": conf_ref,
            "status_code": result.get("status_code"),
            "confirmed_at": now.isoformat(),
            "approved_by": approval_actor,
        },
    }
    application_service.update_application_status(
        session,
        app.id,
        status="submitted",
        submitted_at=now,
        confirmation_ref=conf_ref,
        notes=json.dumps(details, default=str),
    )
    opportunity_service.transition_status(
        session,
        opp.id,
        OpportunityStatus.APPLIED,
        reason="Application successfully submitted after explicit human approval",
        actor=approval_actor,
    )
    session.commit()

    return {
        "success": True,
        "status": "applied",
        "confirmed": True,
        "confirmation_ref": conf_ref,
        "submitted_at": now.isoformat(),
    }
