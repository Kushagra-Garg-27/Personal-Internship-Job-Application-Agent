"""Application submission execution service (Phase 9 & 10).

Provides self-contained submission execution and confirmation logic for Core,
ensuring zero import-time or call-time dependency on `worker.*`.

Handles:
- Stable HTTP tier (Greenhouse, Lever): Executes authorized HTTP POST submissions
  and ambiguous-timeout recovery status checks.
- Experimental Browser tier (Internshala, Unstop): Confirms that the candidate has
  physically clicked Submit in the open browser window and transitions database state.
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
from core.status import OpportunityStatus, ReliabilityTier

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
    http_client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Human confirmation action to execute submission or confirm physical browser submission.

    Parameters
    ----------
    session : Session
        SQLAlchemy database session.
    application_id : int
        ID of the application being confirmed.
    http_client : httpx.Client | None
        Optional HTTP client for dependency injection in tests.

    Returns
    -------
    dict[str, Any]
        Submission outcome status dictionary.

    Raises
    ------
    ValueError
        If application or opportunity not found, or opportunity is not awaiting_submission.
    """
    app = session.get(Application, application_id)
    if app is None:
        raise ValueError(f"Application {application_id} not found.")

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
    # Candidate physically clicked submit in the opened browser window;
    # this confirm action updates and persists the confirmed state.
    if is_browser_tier:
        now = datetime.now(timezone.utc)
        conf_ref = f"BROWSER-{adapter_name.upper()}-SUBMITTED"
        application_service.update_application_status(
            session,
            app.id,
            status="submitted",
            submitted_at=now,
            confirmation_ref=conf_ref,
        )
        opportunity_service.transition_status(
            session,
            opp.id,
            OpportunityStatus.APPLIED,
            reason=f"Human confirmed physical submission in {adapter_name} browser window",
            actor="human_submission",
        )
        session.commit()
        return {
            "success": True,
            "status": "applied",
            "confirmation_ref": conf_ref,
            "submitted_at": now.isoformat(),
            "mode": "browser_confirmed",
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
            app.status = "failed"
            app.notes = f"{app.notes}\n[FAILURE]: {reason}"
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
        app.status = "failed"
        app.notes = f"{app.notes}\n[ERROR]: {error_msg}"
        opportunity_service.transition_status(
            session,
            opp.id,
            OpportunityStatus.MANUAL_APPLICATION_REQUIRED,
            reason=error_msg,
            actor="submission_service",
        )
        session.commit()
        return {"success": False, "status": "manual_required", "reason": error_msg}

    # Submission succeeded!
    now = datetime.now(timezone.utc)
    conf_ref = result.get("confirmation_ref") or "SUBMITTED-OK"
    application_service.update_application_status(
        session,
        app.id,
        status="submitted",
        submitted_at=now,
        confirmation_ref=conf_ref,
    )
    opportunity_service.transition_status(
        session,
        opp.id,
        OpportunityStatus.APPLIED,
        reason="Application successfully submitted by user confirmation",
        actor="human_submission",
    )
    session.commit()

    return {
        "success": True,
        "status": "applied",
        "confirmation_ref": conf_ref,
        "submitted_at": now.isoformat(),
    }
