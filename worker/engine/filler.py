"""Worker application fill orchestrator (Phase 9).

Coordinates deterministic profile data mapping, resume resolution, AI custom question
drafting, adapter dispatch, and fail-closed human-submit gating.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from core.models.opportunity import Application, Opportunity
from core.models.profile import Profile
from core.models.resume import Resume
from core.repositories import profile_repo
from core.services import application_service, opportunity_service
from core.status import (
    ApplicationStatus,
    OpportunityStatus,
    ReliabilityTier,
    SubmissionApprovalRequiredError,
    is_human_approved,
)
from worker.adapters.base import BasePlatformAdapter, FillResult, SubmissionStatus
from worker.adapters.registry import (
    DiscoveryOnlyRejectionError,
    UnsupportedPlatformError,
    resolve_adapter,
)
from worker.engine.question_drafter import QuestionDrafter

logger = logging.getLogger(__name__)


def serialize_profile(profile: Profile | None) -> dict[str, Any]:
    """Serialize profile ORM model into dictionary for adapter consumption."""
    if profile is None:
        return {}
    
    edu_list = [
        {
            "degree": e.degree,
            "branch": e.branch,
            "institution": e.institution,
            "graduation_year": e.graduation_year,
        }
        for e in (profile.education or [])
    ]
    skills_list = [
        {"skill_name": s.skill_name, "proficiency": s.proficiency}
        for s in (profile.skills or [])
    ]
    links_list = [
        {"link_type": l.link_type, "url": l.url}
        for l in (profile.links or [])
    ]

    return {
        "id": profile.id,
        "name": profile.name,
        "full_name": profile.full_name,
        "email": profile.email,
        "phone": profile.phone,
        "location": profile.location,
        "education": edu_list,
        "skills": skills_list,
        "links": links_list,
        # Candidate application attributes required by the Unstop flow.
        # Emitted only when explicitly present so an absent value stays
        # absent and the Unstop adapter classifies it as REQUIRES_USER.
        # Never inferred, never defaulted.
        "organization": profile.organization,
        "designation": profile.designation,
        "work_experience": profile.work_experience,
        "user_type": profile.user_type,
        "gender": profile.gender,
        "differently_abled": profile.differently_abled,
    }


class ApplicationFiller:
    """Orchestrates the filling of approved opportunities."""

    def __init__(
        self,
        question_drafter: QuestionDrafter | None = None,
        adapter_override: BasePlatformAdapter | None = None,
    ) -> None:
        self.question_drafter = question_drafter or QuestionDrafter()
        self.adapter_override = adapter_override

    def process_opportunity(
        self,
        session: Session,
        opportunity_id: int,
        *,
        screenshot_path: Path | str | None = None,
    ) -> dict[str, Any]:
        """Process an approved opportunity in ready_to_apply status."""
        opp = session.get(Opportunity, opportunity_id)
        if opp is None:
            raise ValueError(f"Opportunity {opportunity_id} not found.")

        if opp.status != OpportunityStatus.READY_TO_APPLY.value:
            logger.info("Opportunity #%d status is %s; skipping fill.", opp.id, opp.status)
            return {"status": "skipped", "reason": f"Status is {opp.status}"}

        # 1. Reject discovery_only tier immediately
        if opp.reliability_tier in {ReliabilityTier.DISCOVERY_ONLY, "discovery_only"}:
            reason = "Discovery-only listing cannot be auto-filled per architectural policy."
            opportunity_service.transition_status(
                session,
                opp.id,
                OpportunityStatus.MANUAL_APPLICATION_REQUIRED,
                reason=reason,
                actor="worker",
            )
            session.commit()
            return {"status": "manual_required", "reason": reason}

        # 2. Resolve target resume (pinned version from approval time)
        resume_id = opp.selected_resume_id
        resume_path: str | None = None
        if resume_id is not None:
            res = session.get(Resume, resume_id)
            if res:
                resume_path = res.file_path

        # 3. Resolve candidate profile
        profile = None
        if opp.profile_id is not None:
            profile = session.get(Profile, opp.profile_id)
        if profile is None:
            profiles = profile_repo.list_profiles(session)
            if profiles:
                profile = profiles[0]
        candidate_data = serialize_profile(profile)

        # Fallback for resume if not pinned
        if not resume_path and profile and profile.resumes:
            for r in profile.resumes:
                if r.is_active:
                    resume_path = r.file_path
                    resume_id = r.id
                    break

        # 4. Resolve adapter
        if self.adapter_override is not None:
            adapter = self.adapter_override
        else:
            try:
                adapter = resolve_adapter(opp)
            except (DiscoveryOnlyRejectionError, UnsupportedPlatformError) as exc:
                reason = str(exc)
                opportunity_service.transition_status(
                    session,
                    opp.id,
                    OpportunityStatus.MANUAL_APPLICATION_REQUIRED,
                    reason=reason,
                    actor="worker",
                )
                session.commit()
                return {"status": "manual_required", "reason": reason}

        # 4b. Resume availability gate (browser-tier adapters upload the file
        # directly).  A missing or unreadable resume must fail closed as
        # REAL_RESUME_REQUIRED — never fabricate or substitute a test fixture.
        if getattr(adapter, "requires_resume", False) and not (
            resume_path and Path(resume_path).exists()
        ):
            reason = (
                "REAL_RESUME_REQUIRED: no active, on-disk resume is available for "
                f"'{adapter.adapter_name}'. Upload the real candidate resume before "
                "filling this application."
            )
            opportunity_service.transition_status(
                session,
                opp.id,
                OpportunityStatus.MANUAL_APPLICATION_REQUIRED,
                reason=reason,
                actor="worker",
            )
            session.commit()
            return {"status": "manual_required", "reason": reason}

        # 5. Idempotency pre-write primitive from Phase 2
        app_record = opportunity_service.mark_submission_attempted(
            session,
            opportunity_id=opp.id,
            resume_id=resume_id,
            adapter_name=adapter.adapter_name,
        )
        session.commit()

        # 6. Open application and extract questions
        app_ctx = adapter.open_application(opp.url or "", opportunity_id=opp.id)
        extracted = app_ctx.extracted or adapter.extract(opp)
        app_ctx.extracted = extracted

        # 7. Draft custom questions with Gemini (clearly marked AI drafts)
        custom_answers: list[dict[str, Any]] = []
        for q in extracted.custom_questions:
            q_text = q.get("label") or q.get("text") or q.get("name") or "Question"
            drafted_text = self.question_drafter.draft_answer(
                question_text=q_text,
                opportunity_title=opp.title,
                company=opp.company,
                candidate_profile=candidate_data,
                job_description=opp.description,
            )
            custom_answers.append({
                "question_id": q.get("id"),
                "question_text": q_text,
                "label": q_text,
                "answer": drafted_text,
                "is_ai_draft": True,
            })

        # 8. Perform form fill (stops before submit)
        fill_result: FillResult = adapter.fill(
            app_ctx=app_ctx,
            candidate_data=candidate_data,
            resume_path=resume_path,
            custom_answers=custom_answers,
        )

        # Post-fill DOM and screenshot diagnostics
        captured_screenshot: str | None = None
        remaining_invalids: int = 0
        final_url: str | None = None
        page = getattr(app_ctx, "browser_page", None)
        if page is not None:
            try:
                final_url = getattr(page, "url", None)
                if hasattr(page, "locator"):
                    remaining_invalids = page.locator(".ng-invalid:not(form)").count()
            except Exception:
                pass
            if screenshot_path:
                try:
                    p = Path(screenshot_path)
                    p.parent.mkdir(parents=True, exist_ok=True)
                    if hasattr(page, "screenshot"):
                        page.screenshot(path=str(p), full_page=True)
                        captured_screenshot = str(p)
                except Exception as s_exc:
                    logger.warning("Failed to capture screenshot: %s", s_exc)

        # 9. Handle outcome fail-closed
        if not fill_result.success or fill_result.status == "manual_required":
            reason = fill_result.error_reason or "Automated form fill failed."
            application_service.transition_application_status(
                session,
                app_record.id,
                ApplicationStatus.FAILED,
                notes=reason,
                reason=reason,
            )
            opportunity_service.transition_status(
                session,
                opp.id,
                OpportunityStatus.MANUAL_APPLICATION_REQUIRED,
                reason=reason,
                actor="worker",
            )
            session.commit()
            return {
                "status": "manual_required",
                "reason": reason,
                "screenshot_path": captured_screenshot,
                "remaining_invalids": remaining_invalids,
                "final_url": final_url,
                "app_ctx": app_ctx,
            }

        # Success: form filled, awaiting human submission
        notes_dict = {
            "adapter": adapter.adapter_name,
            "tier": adapter.tier.value,
            "custom_answers": fill_result.custom_answers,
            "draft_payload": fill_result.draft_payload,
            "filled_at": datetime.now(timezone.utc).isoformat(),
        }
        application_service.transition_application_status(
            session,
            app_record.id,
            ApplicationStatus.FORM_FILLED,
            notes=json.dumps(notes_dict, default=str),
            reason="Application filled by worker; awaiting human review",
        )

        opportunity_service.transition_status(
            session,
            opp.id,
            OpportunityStatus.AWAITING_SUBMISSION,
            reason=fill_result.message or "Application filled. Awaiting human submission click.",
            actor="worker",
        )
        session.commit()

        return {
            "status": "awaiting_submission",
            "opportunity_id": opp.id,
            "application_id": app_record.id,
            "adapter": adapter.adapter_name,
            "tier": adapter.tier.value,
            "custom_answers_count": len(fill_result.custom_answers),
            "fill_result": fill_result,
            "screenshot_path": captured_screenshot,
            "remaining_invalids": remaining_invalids,
            "final_url": final_url,
            "app_ctx": app_ctx,
        }

    def confirm_and_submit(
        self,
        session: Session,
        application_id: int,
        *,
        approval_token: str | None = None,
        approval_actor: str = "human_submission",
        platform_confirmed: bool = False,
        confirmation_ref: str | None = None,
        confirmation_detail: str | None = None,
    ) -> dict[str, Any]:
        """Human confirmation action to execute submission for stable HTTP adapters.
        Handles ambiguous timeouts by verifying status before retry.

        Requires an explicit human approval token
        (``HUMAN_SUBMISSION_APPROVAL_TOKEN``). Every other signal — awaiting_submission,
        a valid form, a successful fill, prior instructions, test execution, timeouts,
        defaults, or agent assumptions — fails closed.
        """
        # ── Human approval gate (fail closed) ─────────────────────────
        if not is_human_approved(approval_token):
            raise SubmissionApprovalRequiredError(
                f"Application {application_id} submission blocked: no explicit human approval token. "
                "Form-filled/awaiting-submission state is NOT approval."
            )

        app = session.get(Application, application_id)
        if app is None:
            raise ValueError(f"Application {application_id} not found.")

        # ── Duplicate submission protection ───────────────────────────
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

        notes_data = {}
        if app.notes:
            try:
                notes_data = json.loads(app.notes)
            except Exception:
                pass

        adapter = resolve_adapter(opp)

        # ── Experimental Browser Tier ─────────────────────────────────
        # The candidate physically clicked submit in the opened browser window.
        # We only record success when the platform itself confirmed receipt;
        # anything else stays in the pre-submit review state (never a false
        # `submitted`).
        if not hasattr(adapter, "execute_submission"):
            if not platform_confirmed:
                unconfirmed_reason = (
                    confirmation_detail
                    or "Platform confirmation not observed after human-approved submission."
                )
                logger.error(
                    "Browser-tier submission for application #%d was approved but NOT confirmed: %s",
                    app.id,
                    unconfirmed_reason,
                )
                return {
                    "success": False,
                    "status": "unconfirmed",
                    "confirmed": False,
                    "mode": "browser_confirmed",
                    "reason": unconfirmed_reason,
                    "opportunity_status": opp.status,
                    "application_status": app.status,
                }

            now = datetime.now(timezone.utc)
            conf_ref = confirmation_ref or f"BROWSER-{adapter.adapter_name.upper()}-CONFIRMED"
            details = {
                **(notes_data or {}),
                "submission_confirmation": {
                    "source": "platform",
                    "confirmed": True,
                    "confirmation_ref": conf_ref,
                    "detail": confirmation_detail,
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
                reason="Browser application physically confirmed submitted by platform after explicit human approval",
                actor=approval_actor,
            )
            session.commit()
            return {
                "success": True,
                "status": "applied",
                "confirmed": True,
                "confirmation_ref": conf_ref,
                "confirmation_detail": confirmation_detail,
                "submitted_at": now.isoformat(),
                "mode": "browser_confirmed",
            }


        # ── Stable HTTP API Tier ──────────────────────────────────────
        # Candidate reviews pending draft payload and explicitly authorizes
        # the programmatic HTTP POST submission call.
        draft_payload = notes_data.get("draft_payload")
        if not draft_payload:
            raise ValueError("No draft payload found on application record to submit.")

        # Execute authorized HTTP submission
        result = adapter.execute_submission(draft_payload)

        # Ambiguous timeout duplicate prevention check (§5.7)
        if result.get("ambiguous_timeout"):
            logger.warning("Ambiguous timeout submitting app #%d; querying status before retry", app.id)
            app_ctx = adapter.open_application(opp.url or "", opportunity_id=opp.id)
            status_check = adapter.check_status(app_ctx)
            if status_check.confirmed:
                # Successfully received on platform
                now = datetime.now(timezone.utc)
                application_service.update_application_status(
                    session,
                    app.id,
                    status="submitted",
                    submitted_at=now,
                    confirmation_ref=status_check.confirmation_ref or "TIMEOUT-CONFIRMED",
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
                # Not confirmed: fail closed to manual application, do NOT blindly retry
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
                    actor="worker",
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
                actor="worker",
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

    def execute_browser_submission(self, session: Session, application_id: int, approval_token: str) -> dict[str, Any]:
        """Orchestrate the autonomous browser submission for an explicitly approved application.
        
        This securely bridges the human approval gate to the actual Playwright browser execution.
        """
        import json
        from pathlib import Path
        from core.status import HUMAN_SUBMISSION_APPROVAL_TOKEN, ApplicationStatus, OpportunityStatus
        from core.models.opportunity import Opportunity, Application
        from datetime import datetime, timezone

        if not isinstance(approval_token, str) or approval_token != HUMAN_SUBMISSION_APPROVAL_TOKEN:
            raise PermissionError("execute_browser_submission requires valid human approval token")

        app = session.get(Application, application_id)
        if app is None:
            raise ValueError(f"Application {application_id} not found.")

        if app.status == ApplicationStatus.SUBMITTED.value:
            return {"success": True, "already_submitted": True}

        if app.status not in {ApplicationStatus.FORM_FILLED.value, ApplicationStatus.PENDING.value}:
            raise ValueError(f"Cannot submit application in status {app.status!r}")

        opp = session.get(Opportunity, app.opportunity_id)
        if opp is None:
            raise ValueError(f"Opportunity {app.opportunity_id} not found.")

        if opp.status != OpportunityStatus.AWAITING_SUBMISSION.value:
            raise ValueError(f"Cannot submit application for opportunity in status {opp.status!r}")

        notes_data = {}
        if app.notes:
            try:
                notes_data = json.loads(app.notes)
            except Exception:
                pass

        if not notes_data.get("submission_claimed"):
            raise RuntimeError(f"Application #{app.id} must be claimed before executing browser submission")

        if self.adapter_override is not None:
            adapter = self.adapter_override
        else:
            adapter = resolve_adapter(opp)
        if not hasattr(adapter, "submit_application"):
            raise TypeError(f"Adapter {type(adapter).__name__} does not implement submit_application")

        logger.info("Executing browser submission for App #%d (Opp #%d)", app.id, opp.id)

        try:
            # 1. Reach the final review screen using the safe fill logic
            fill_res = self.process_opportunity(session, opp.id)
            if not fill_res.get("success"):
                logger.error("Failed to reach final review screen: %s", fill_res)
                return {"success": False, "error": "Could not recreate form state for submission"}
            
            app_ctx = fill_res.get("app_ctx")
            if not app_ctx:
                 return {"success": False, "error": "No ApplicationContext available from fill"}

            # 2. Execute the actual click and verify confirmation
            submit_res = adapter.submit_application(app_ctx, approval_token=approval_token)
            
            if not submit_res.get("success"):
                # AMBIGUOUS TIMEOUT / FAILURE - FAIL CLOSED
                reason = submit_res.get("error") or "Submission failed or timed out ambiguously"
                logger.error("Browser submission failed or ambiguous: %s", reason)
                
                application_service.transition_application_status(
                    session,
                    app.id,
                    ApplicationStatus.FAILED,
                    notes=f"{app.notes}\n[SUBMIT_ERROR]: {reason}",
                    reason=reason,
                )
                opportunity_service.transition_status(
                    session,
                    opp.id,
                    OpportunityStatus.MANUAL_APPLICATION_REQUIRED,
                    reason=reason,
                    actor="worker_submission",
                )
                session.commit()
                return {"success": False, "status": "manual_required", "reason": reason}

            # VERIFIED SUCCESS
            now = datetime.now(timezone.utc)
            conf_ref = submit_res.get("confirmation_ref") or f"BROWSER-{adapter.adapter_name.upper()}-CONFIRMED"
            
            # Evidence Capture
            artifacts_dir = Path("artifacts/u7_submission_evidence")
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            timestamp = now.strftime("%Y%m%d_%H%M%S")
            evidence_file = artifacts_dir / f"app_{app.id}_{timestamp}_evidence.json"
            screenshot_file = artifacts_dir / f"app_{app.id}_{timestamp}_success.png"
            
            evidence_data = {
                "application_id": app.id,
                "opportunity_id": opp.id,
                "adapter": adapter.adapter_name,
                "timestamp": now.isoformat(),
                "confirmation_ref": conf_ref,
                "clicked_selector": submit_res.get("clicked_selector"),
                "final_url": submit_res.get("url"),
                "status": "verified_success"
            }
            
            try:
                page = app_ctx.browser_page
                if page:
                    page.screenshot(path=str(screenshot_file), full_page=True)
                    evidence_data["screenshot_path"] = str(screenshot_file)
            except Exception as e:
                logger.warning("Failed to capture submission screenshot: %s", e)

            with open(evidence_file, "w") as f:
                json.dump(evidence_data, f, indent=2)

            # Persist state
            notes_data = {}
            if app.notes:
                try:
                    notes_data = json.loads(app.notes)
                except Exception:
                    pass

            details = {
                **(notes_data or {}),
                "submission_confirmation": {
                    "source": "platform_browser",
                    "confirmed": True,
                    "confirmation_ref": conf_ref,
                    "evidence_file": str(evidence_file),
                    "confirmed_at": now.isoformat(),
                    "approved_by": notes_data.get("approved_by", "unknown"),
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
                reason=f"Worker successfully executed and verified browser submission ({conf_ref})",
                actor="worker_submission",
            )
            session.commit()
            
            return {
                "success": True,
                "status": "applied",
                "confirmed": True,
                "confirmation_ref": conf_ref,
                "evidence": str(evidence_file)
            }

        except Exception as e:
            logger.exception("Unexpected exception during execute_browser_submission: %s", e)
            return {"success": False, "error": str(e)}
