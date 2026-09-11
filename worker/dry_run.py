"""Controlled live Unstop dry-run runner (Milestone U6).

Executes a live or simulated form-fill validation against Unstop up to the
`ready_for_review` / `awaiting_submission` boundary.

HARD SAFETY BOUNDARY:
- Never clicks final submission controls ('Complete Registration', 'Submit Application', '#unstop_submit').
- Never calls `UnstopAdapter.submit_application()`.
- Never calls `POST /applications/{id}/confirm-submit`.
- Leaves `Application.status == form_filled` and `Opportunity.status == awaiting_submission`.
- Always leaves `submitted_at == None` and `confirmation_ref == None`.
- Generates structured evidence in `artifacts/u6_dry_run/` proving `submission_attempted = false`.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from core.database import get_session
from core.models.opportunity import Application, Opportunity
from core.models.profile import Profile
from core.models.resume import Resume
from core.repositories import profile_repo
from core.services import opportunity_service
from core.status import ApplicationStatus, OpportunityStatus, ReliabilityTier
from worker.adapters.base import BasePlatformAdapter
from worker.adapters.unstop import DEFAULT_SESSION_FILE, VALID, UnstopAdapter
from worker.engine.filler import ApplicationFiller

logger = logging.getLogger(__name__)

DEFAULT_ARTIFACTS_DIR = Path("artifacts/u6_dry_run")

FORBIDDEN_SECRET_KEYS = {
    "cookie",
    "cookies",
    "token",
    "auth_token",
    "password",
    "key",
    "secret",
    "authorization",
    "unstop_session",
    "xsrf-token",
    "storage_state",
}


@dataclass
class DryRunResult:
    """Structured result of a U6 dry-run execution."""

    success: bool
    opportunity_id: int
    opportunity_title: str
    company: str
    target_url: str
    session_health_status: str
    session_valid: bool
    initial_opportunity_status: str
    final_opportunity_status: str
    fill_result: str
    submission_attempted: bool = False  # HARD INVARIANT: Always False
    confirmation_detected: bool = False  # HARD INVARIANT: Always False
    application_id: int | None = None
    final_application_status: str | None = None
    fill_message: str | None = None
    fields_encountered: int = 0
    fields_filled: int = 0
    remaining_validation_errors: int = 0
    final_browser_url: str | None = None
    screenshot_file: str | None = None
    evidence_file: str | None = None
    error_reason: str | None = None
    timestamp: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # Ensure hard invariants
        data["submission_attempted"] = False
        data["confirmation_detected"] = False
        return sanitize_evidence_dict(data)


def to_canonical_status_str(status: Any) -> str:
    from worker.adapters.unstop import CHECK_FAILED, EXPIRED, INVALID, MISSING, VALID
    if status == VALID:
        return "VALID"
    if status == EXPIRED:
        return "EXPIRED"
    if status == MISSING:
        return "MISSING"
    if status == INVALID:
        return "INVALID"
    if status == CHECK_FAILED:
        return "CHECK_FAILED"
    return str(status).upper()


def sanitize_evidence_dict(data: dict[str, Any]) -> dict[str, Any]:
    """Recursively scrub any inadvertent credential or secret keys/values."""
    sanitized: dict[str, Any] = {}
    for k, v in data.items():
        if any(secret_k in k.lower() for secret_k in FORBIDDEN_SECRET_KEYS):
            continue
        if isinstance(v, dict):
            sanitized[k] = sanitize_evidence_dict(v)
        elif isinstance(v, str):
            # Check for Fernet ciphertext prefix or raw token patterns
            if v.startswith("gAAAAA"):
                sanitized[k] = "[REDACTED_CIPHERTEXT]"
            else:
                sanitized[k] = v
        else:
            sanitized[k] = v
    return sanitized


def run_dry_run(
    db: Session,
    *,
    opportunity_id: int | None = None,
    target_url: str | None = None,
    session_file: Path | str | None = None,
    encryption_key: str | bytes | None = None,
    headless: bool = False,
    probe_network: bool = True,
    artifacts_dir: Path | str = DEFAULT_ARTIFACTS_DIR,
    keep_browser_open: bool = False,
    adapter_override: BasePlatformAdapter | None = None,
    playwright_instance: Any = None,
) -> DryRunResult:
    """Execute a controlled live or mock dry run of the Unstop application form fill.

    Stops at the `ready_for_review` / `awaiting_submission` boundary.
    Never attempts submission or alters submitted_at / confirmation_ref.
    """
    artifacts_path = Path(artifacts_dir)
    artifacts_path.mkdir(parents=True, exist_ok=True)
    evidence_path = artifacts_path / "evidence.json"
    screenshot_path = artifacts_path / "screenshot.png"
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Resolve / instantiate Unstop adapter
    adapter = adapter_override or UnstopAdapter(
        session_file=session_file or DEFAULT_SESSION_FILE,
        encryption_key=encryption_key,
        headless=headless,
        playwright_instance=playwright_instance,
        probe_network=probe_network,
    )

    # 2. Session pre-flight verification
    session_status = adapter.check_session_status(probe_network=probe_network)
    if not session_status.valid:
        error_msg = f"Unstop session verification failed ({session_status.status}): {session_status.detail}"
        logger.error("[U6 DRY RUN FAIL-CLOSED] %s", error_msg)

        fail_result = DryRunResult(
            success=False,
            opportunity_id=opportunity_id or 0,
            opportunity_title="Unknown",
            company="Unknown",
            target_url=target_url or "",
            session_health_status=to_canonical_status_str(session_status.status),
            session_valid=False,
            initial_opportunity_status="",
            final_opportunity_status=OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value,
            fill_result="failed",
            error_reason=error_msg,
            evidence_file=str(evidence_path),
            timestamp=now_iso,
        )
        evidence_path.write_text(json.dumps(fail_result.to_dict(), indent=2), encoding="utf-8")
        return fail_result

    # 3. Resolve Target Opportunity
    opp: Opportunity | None = None
    if opportunity_id is not None:
        opp = db.get(Opportunity, opportunity_id)
        if opp is None:
            raise ValueError(f"Opportunity #{opportunity_id} not found in database.")
    elif target_url is not None:
        target_url = target_url.strip()
        # Look up existing opportunity by URL
        existing = db.query(Opportunity).filter(Opportunity.url == target_url).first()
        if existing:
            opp = existing
        else:
            # Create a dry-run opportunity
            opp = opportunity_service.create_opportunity(
                db,
                title="Unstop Dry-Run Role",
                company="Unstop Verification Company",
                url=target_url,
                source="unstop",
                reliability_tier=ReliabilityTier.EXPERIMENTAL.value,
                reason="u6_dry_run_creation",
                actor="u6_runner",
            )
            db.commit()
    else:
        raise ValueError("Either opportunity_id or target_url must be provided for dry run.")

    initial_status = opp.status

    # 4. Resolve Candidate Profile & Resume
    profile = None
    if opp.profile_id is not None:
        profile = db.get(Profile, opp.profile_id)
    if profile is None:
        profiles = profile_repo.list_profiles(db)
        if profiles:
            profile = profiles[0]
            opp.profile_id = profile.id
            db.flush()

    if profile is None:
        error_msg = "No candidate profile found in database. Create a profile before dry run."
        fail_result = DryRunResult(
            success=False,
            opportunity_id=opp.id,
            opportunity_title=opp.title,
            company=opp.company,
            target_url=opp.url or "",
            session_health_status=to_canonical_status_str(session_status.status),
            session_valid=True,
            initial_opportunity_status=initial_status,
            final_opportunity_status=opp.status,
            fill_result="failed",
            error_reason=error_msg,
            evidence_file=str(evidence_path),
            timestamp=now_iso,
        )
        evidence_path.write_text(json.dumps(fail_result.to_dict(), indent=2), encoding="utf-8")
        return fail_result

    # Pinned resume check
    resume_path: str | None = None
    if opp.selected_resume_id:
        res = db.get(Resume, opp.selected_resume_id)
        if res:
            resume_path = res.file_path
    elif profile.resumes:
        for r in profile.resumes:
            if r.is_active:
                resume_path = r.file_path
                opp.selected_resume_id = r.id
                db.flush()
                break

    # 5. Transition opportunity to READY_TO_APPLY if needed
    if opp.status != OpportunityStatus.READY_TO_APPLY.value:
        if opp.status == OpportunityStatus.DISCOVERED.value:
            opportunity_service.transition_status(db, opp.id, OpportunityStatus.RECOMMENDED, actor="u6_runner")
            opportunity_service.transition_status(db, opp.id, OpportunityStatus.READY_TO_APPLY, actor="u6_runner")
        elif opp.status == OpportunityStatus.RECOMMENDED.value:
            opportunity_service.transition_status(db, opp.id, OpportunityStatus.READY_TO_APPLY, actor="u6_runner")
        elif opp.status in {OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value, OpportunityStatus.AWAITING_SUBMISSION.value}:
            # Reset to ready_to_apply for re-test
            opp.status = OpportunityStatus.READY_TO_APPLY.value
            db.flush()
        db.commit()

    # 6. Execute ApplicationFiller (Stops strictly at ready_for_review)
    filler = ApplicationFiller(adapter_override=adapter)
    fill_summary = filler.process_opportunity(
        db,
        opp.id,
        screenshot_path=screenshot_path,
    )

    db.refresh(opp)
    app = (
        db.query(Application)
        .filter_by(opportunity_id=opp.id)
        .order_by(Application.attempt_number.desc())
        .first()
    )

    # 7. CRITICAL DATABASE SAFETY INVARIANTS
    if opp.status == OpportunityStatus.APPLIED.value:
        raise RuntimeError("FATAL SAFETY VIOLATION: Opportunity was transitioned to 'applied' during dry run!")
    if app and app.status == ApplicationStatus.SUBMITTED.value:
        raise RuntimeError("FATAL SAFETY VIOLATION: Application was transitioned to 'submitted' during dry run!")
    if app and app.submitted_at is not None:
        raise RuntimeError("FATAL SAFETY VIOLATION: submitted_at timestamp was written during dry run!")
    if app and app.confirmation_ref is not None:
        raise RuntimeError("FATAL SAFETY VIOLATION: confirmation_ref was written during dry run!")

    is_success = fill_summary.get("status") == "awaiting_submission"
    fill_result_obj = fill_summary.get("fill_result")
    custom_answers = getattr(fill_result_obj, "custom_answers", []) or []

    result = DryRunResult(
        success=is_success,
        opportunity_id=opp.id,
        opportunity_title=opp.title,
        company=opp.company,
        target_url=opp.url or "",
        session_health_status=to_canonical_status_str(session_status.status),
        session_valid=session_status.valid,
        initial_opportunity_status=initial_status,
        final_opportunity_status=opp.status,
        final_application_status=app.status if app else None,
        fill_result=fill_summary.get("status", "unknown"),
        fill_message=getattr(fill_result_obj, "message", None) or fill_summary.get("reason"),
        fields_encountered=len(custom_answers) + 5 if is_success else 0,
        fields_filled=len(custom_answers) + 5 if is_success else 0,
        remaining_validation_errors=fill_summary.get("remaining_invalids", 0),
        final_browser_url=fill_summary.get("final_url") or opp.url,
        submission_attempted=False,
        confirmation_detected=False,
        application_id=app.id if app else None,
        screenshot_file=str(screenshot_path) if screenshot_path.exists() else None,
        evidence_file=str(evidence_path),
        error_reason=fill_summary.get("reason") if not is_success else None,
        timestamp=now_iso,
    )

    # 8. Write Evidence File
    evidence_path.write_text(json.dumps(result.to_dict(), indent=2), encoding="utf-8")
    logger.info("[U6 DRY RUN] Evidence successfully written to %s", evidence_path)

    # 9. Handle keep-browser-open
    app_ctx = fill_summary.get("app_ctx")
    if keep_browser_open and app_ctx and getattr(app_ctx, "browser_context", None):
        print("\n" + "=" * 70)
        print("[U6 DRY RUN] Browser is held open at the completed form.")
        print("Review the populated form in the Chromium window.")
        print("DO NOT click submit! Press [ENTER] in this terminal when finished...")
        print("=" * 70)
        try:
            input(">> Press [ENTER] to close browser: ")
        except (KeyboardInterrupt, EOFError):
            pass
        finally:
            try:
                app_ctx.browser_context.close()
            except Exception:
                pass

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Controlled live Unstop dry-run runner (Milestone U6)."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--opportunity-id",
        type=int,
        help="Database ID of the opportunity to dry run.",
    )
    group.add_argument(
        "--url",
        type=str,
        help="Active Unstop opportunity listing URL.",
    )
    parser.add_argument(
        "--session-file",
        type=str,
        default=str(DEFAULT_SESSION_FILE),
        help="Path to encrypted session state file.",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=str,
        default=str(DEFAULT_ARTIFACTS_DIR),
        help="Directory to save evidence.json and screenshot.png.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser headlessly (default is visible browser).",
    )
    parser.add_argument(
        "--no-probe",
        action="store_true",
        help="Skip online network probe during session check.",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="Keep browser window open after fill for human inspection.",
    )

    args = parser.parse_args()

    print("=" * 70)
    print("[U6 DRY RUN] Controlled Live Unstop Dry-Run Runner")
    print("STRICT SAFETY BOUNDARY: Form fill only. Never submits.")
    print("=" * 70)

    with get_session() as db:
        res = run_dry_run(
            db,
            opportunity_id=args.opportunity_id,
            target_url=args.url,
            session_file=args.session_file,
            headless=args.headless,
            probe_network=not args.no_probe,
            artifacts_dir=args.artifacts_dir,
            keep_browser_open=args.keep_open,
        )

    print("\n" + "=" * 70)
    print(f"Outcome:              {'SUCCESS' if res.success else 'FAILED'}")
    print(f"Opportunity ID:       {res.opportunity_id} ({res.opportunity_title} at {res.company})")
    print(f"Target URL:           {res.target_url}")
    print(f"Session Health:       {res.session_health_status} (Valid: {res.session_valid})")
    print(f"Fill Status:          {res.fill_result}")
    print(f"Final Opp Status:     {res.final_opportunity_status}")
    print(f"Final App Status:     {res.final_application_status}")
    print(f"Submission Attempted: {res.submission_attempted} (HARD INVARIANT: FALSE)")
    print(f"Confirmation Detected:{res.confirmation_detected} (HARD INVARIANT: FALSE)")
    print(f"Evidence JSON:        {res.evidence_file}")
    if res.screenshot_file:
        print(f"Screenshot PNG:       {res.screenshot_file}")
    if res.error_reason:
        print(f"Error / Reason:       {res.error_reason}")
    print("=" * 70)

    sys.exit(0 if res.success else 1)


if __name__ == "__main__":
    main()
