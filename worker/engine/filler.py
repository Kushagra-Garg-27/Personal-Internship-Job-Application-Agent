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
from core.services.submission_service import (
    compute_approved_input_digest,
    compute_file_sha256,
    compute_profile_sha256,
)
from core.status import (
    ApplicationStatus,
    OpportunityStatus,
    ReliabilityTier,
    SubmissionApprovalRequiredError,
)
from core.tokens import is_token_valid
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
        questions_list = [
            q.get("label") or q.get("text") or q.get("name") or str(q.get("id"))
            if isinstance(q, dict) else str(q)
            for q in (extracted.custom_questions if extracted else [])
        ]
        # Hash resume file bytes if accessible on disk
        resume_content_sha256 = None
        if resume_path and Path(resume_path).is_file():
            try:
                resume_content_sha256 = compute_file_sha256(resume_path)
            except Exception as exc:
                logger.warning("Could not hash resume at %s: %s", resume_path, exc)

        # Do not duplicate raw candidate PII into notes; store deterministic hash
        candidate_profile_sha256 = compute_profile_sha256(candidate_data)

        input_snapshot = {
            "url": opp.url or "",
            "resume_id": resume_id,
            "resume_content_sha256": resume_content_sha256,
            "candidate_profile_sha256": candidate_profile_sha256,
            "custom_answers": fill_result.custom_answers,
            "form_questions": questions_list,
        }
        input_digest = compute_approved_input_digest(
            url=input_snapshot["url"],
            resume_id=input_snapshot["resume_id"],
            resume_content_sha256=input_snapshot["resume_content_sha256"],
            candidate_profile_sha256=input_snapshot["candidate_profile_sha256"],
            custom_answers=input_snapshot["custom_answers"],
            form_questions=input_snapshot["form_questions"],
        )
        notes_dict = {
            "adapter": adapter.adapter_name,
            "tier": adapter.tier.value,
            "custom_answers": fill_result.custom_answers,
            "draft_payload": fill_result.draft_payload,
            "filled_at": datetime.now(timezone.utc).isoformat(),
            "form_questions": questions_list,
            "approved_input_snapshot": input_snapshot,
            "approved_input_digest": input_digest,
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

    def reconstruct_form_state(
        self,
        session: Session,
        application_id: int,
    ) -> dict[str, Any]:
        """Reopen an already-filled application form to its final pre-submit state.

        This method is used by the worker *after* human approval and atomic claim
        to restore the browser page to the review screen before calling the
        adapter's submit action.

        Key design constraints (M1 handoff repair):
        - Operates on the *existing* Application record — does NOT create a new one.
        - Requires the opportunity to be in ``AWAITING_SUBMISSION`` (not ``READY_TO_APPLY``).
        - Reuses the custom answers already stored in the application notes
          (from the original ``process_opportunity`` fill) rather than re-drafting.
        - Reuses the pinned resume where available.
        - Stops at the final review screen — performs no submission.
        - Fails closed if reconstruction cannot succeed.

        Returns
        -------
        dict[str, Any]
            ``{"success": True, "app_ctx": ApplicationContext, ...}`` on success.
            ``{"success": False, "error": str}`` on failure.
        """
        app = session.get(Application, application_id)
        if app is None:
            return {"success": False, "error": f"Application {application_id} not found."}

        opp = session.get(Opportunity, app.opportunity_id)
        if opp is None:
            return {"success": False, "error": f"Opportunity {app.opportunity_id} not found."}

        if opp.status != OpportunityStatus.AWAITING_SUBMISSION.value:
            return {
                "success": False,
                "error": f"Opportunity #{opp.id} is in {opp.status!r}, expected awaiting_submission.",
            }

        # 1. Parse stored notes to recover the previously reviewed custom answers
        notes_data: dict[str, Any] = {}
        if app.notes:
            try:
                notes_data = json.loads(app.notes)
            except Exception:
                pass

        stored_custom_answers = notes_data.get("custom_answers", [])
        approved_snapshot = notes_data.get("approved_input_snapshot")

        # 2. Resolve candidate profile
        profile = None
        if opp.profile_id is not None:
            profile = session.get(Profile, opp.profile_id)
        if profile is None:
            profiles = profile_repo.list_profiles(session)
            if profiles:
                profile = profiles[0]
        candidate_data = serialize_profile(profile)
        current_profile_hash = compute_profile_sha256(candidate_data)

        # 3. Resolve and verify resume (Requirement 3 & 4)
        resume_id: int | None = None
        resume_path: str | None = None
        current_resume_hash: str | None = None

        if approved_snapshot:
            # When an approved snapshot exists, resolve the EXACT approved pinned resume.
            # Do NOT silently fall back to another active resume after approval.
            approved_resume_id = approved_snapshot.get("resume_id")
            current_opp_resume_id = opp.selected_resume_id or app.resume_id

            if approved_resume_id is not None:
                if str(current_opp_resume_id) != str(approved_resume_id):
                    reason = (
                        f"Resume selection mismatch: approved resume ID {approved_resume_id!r}, "
                        f"current {current_opp_resume_id!r}. Failing closed to fresh human review."
                    )
                    logger.error("App #%d: %s", app.id, reason)
                    return {"success": False, "error": reason}

                res = session.get(Resume, int(approved_resume_id))
                if res is None:
                    reason = (
                        f"Pinned approved resume #{approved_resume_id} not found in database. "
                        "Failing closed to fresh human review."
                    )
                    logger.error("App #%d: %s", app.id, reason)
                    return {"success": False, "error": reason}

                if not res.file_path:
                    reason = (
                        f"Pinned approved resume #{approved_resume_id} has no file path. "
                        "Failing closed to fresh human review."
                    )
                    logger.error("App #%d: %s", app.id, reason)
                    return {"success": False, "error": reason}

                resume_file = Path(res.file_path)
                if not resume_file.is_file():
                    reason = (
                        f"Pinned approved resume file not found at {res.file_path}. "
                        "Failing closed to fresh human review."
                    )
                    logger.error("App #%d: %s", app.id, reason)
                    return {"success": False, "error": reason}

                try:
                    current_resume_hash = compute_file_sha256(resume_file)
                except (PermissionError, OSError) as exc:
                    reason = (
                        f"Pinned approved resume file at {res.file_path} is unreadable: {exc}. "
                        "Failing closed to fresh human review."
                    )
                    logger.error("App #%d: %s", app.id, reason)
                    return {"success": False, "error": reason}

                approved_resume_hash = approved_snapshot.get("resume_content_sha256")
                if not approved_resume_hash:
                    reason = (
                        f"Pinned approved resume #{approved_resume_id} has no approved content hash. "
                        "Failing closed to fresh human review."
                    )
                    logger.error("App #%d: %s", app.id, reason)
                    return {"success": False, "error": reason}

                if current_resume_hash != approved_resume_hash:
                    reason = (
                        f"Resume content mismatch: file bytes changed since approval. "
                        f"Approved hash: {approved_resume_hash}, current hash: {current_resume_hash}. "
                        "Failing closed to fresh human review."
                    )
                    logger.error("App #%d: %s", app.id, reason)
                    return {"success": False, "error": reason}

                resume_id = approved_resume_id
                resume_path = str(resume_file)
            else:
                # Approved without resume
                if current_opp_resume_id is not None:
                    reason = (
                        f"Resume selection mismatch: approved with no resume, "
                        f"current {current_opp_resume_id!r}. Failing closed to fresh human review."
                    )
                    logger.error("App #%d: %s", app.id, reason)
                    return {"success": False, "error": reason}

            # 3b. Invariant 2: Verify remaining pre-browser inputs against approved snapshot
            # 1. Opportunity URL check
            expected_url = approved_snapshot.get("url")
            if expected_url is not None and (opp.url or "") != expected_url:
                reason = (
                    f"Opportunity URL mismatch: approved {expected_url!r}, current {opp.url!r}. "
                    "Failing closed to fresh human review."
                )
                logger.error("App #%d: %s", app.id, reason)
                return {"success": False, "error": reason}

            # 2. Candidate profile data check (hash-based to avoid PII duplication)
            expected_profile_hash = approved_snapshot.get("candidate_profile_sha256")
            if not expected_profile_hash and "candidate_data" in approved_snapshot:
                expected_profile_hash = compute_profile_sha256(approved_snapshot.get("candidate_data"))
            if expected_profile_hash is not None and current_profile_hash != expected_profile_hash:
                reason = (
                    "Candidate profile data mismatch: profile data changed between approval "
                    "and reconstruction. Failing closed to fresh human review."
                )
                logger.error("App #%d: %s", app.id, reason)
                return {"success": False, "error": reason}

            # 3. Stored custom answers check
            expected_answers = approved_snapshot.get("custom_answers")
            if expected_answers is not None and stored_custom_answers != expected_answers:
                reason = (
                    "Stored custom answers mismatch: custom answers changed between approval "
                    "and reconstruction. Failing closed to fresh human review."
                )
                logger.error("App #%d: %s", app.id, reason)
                return {"success": False, "error": reason}
        else:
            # Legacy / mock path when no approved_snapshot is present
            resume_id = opp.selected_resume_id
            if resume_id is not None:
                res = session.get(Resume, resume_id)
                if res:
                    resume_path = res.file_path
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
                return {"success": False, "error": str(exc)}

        # 5. Open the application page (browser) and re-fill to the review screen
        try:
            app_ctx = adapter.open_application(opp.url or "", opportunity_id=opp.id)
            extracted = app_ctx.extracted or adapter.extract(opp)
            app_ctx.extracted = extracted

            # Invariant 2: Form structure and digest check against approved snapshot
            current_questions = [
                q.get("label") or q.get("text") or q.get("name") or str(q.get("id"))
                if isinstance(q, dict) else str(q)
                for q in (extracted.custom_questions if extracted else [])
            ]
            if approved_snapshot:
                expected_questions = approved_snapshot.get("form_questions")
                if expected_questions is not None:
                    norm_expected = sorted([
                        q.get("label") or q.get("text") or q.get("name") or str(q.get("id"))
                        if isinstance(q, dict) else str(q)
                        for q in expected_questions
                    ])
                    norm_current = sorted(current_questions)
                    if norm_current != norm_expected:
                        reason = (
                            "Form structure mismatch: extracted questions on live page do not match "
                            f"approved form questions. Approved: {norm_expected}, live: {norm_current}. "
                            "Failing closed to fresh human review."
                        )
                        logger.error("App #%d: %s", app.id, reason)
                        return {"success": False, "error": reason}

                expected_digest = notes_data.get("approved_input_digest")
                if expected_digest:
                    current_digest = compute_approved_input_digest(
                        url=opp.url or "",
                        resume_id=opp.selected_resume_id or app.resume_id or resume_id,
                        resume_content_sha256=current_resume_hash if approved_snapshot.get("resume_content_sha256") is not None else None,
                        candidate_profile_sha256=current_profile_hash if approved_snapshot.get("candidate_profile_sha256") is not None else None,
                        candidate_data=candidate_data if approved_snapshot.get("candidate_data") is not None else None,
                        custom_answers=stored_custom_answers,
                        form_questions=current_questions if expected_questions is not None else None,
                    )
                    if current_digest != expected_digest:
                        reason = (
                            "Approved input digest mismatch between approval and reconstruction. "
                            "Failing closed to fresh human review."
                        )
                        logger.error("App #%d: %s", app.id, reason)
                        return {"success": False, "error": reason}

            # Re-fill using the *stored* custom answers — do not re-draft
            fill_result: FillResult = adapter.fill(
                app_ctx=app_ctx,
                candidate_data=candidate_data,
                resume_path=resume_path,
                custom_answers=stored_custom_answers,
            )

            if not fill_result.success:
                return {
                    "success": False,
                    "error": fill_result.error_reason or "Failed to reconstruct form state.",
                }

            return {
                "success": True,
                "app_ctx": app_ctx,
                "adapter": adapter,
                "fill_result": fill_result,
            }

        except Exception as exc:
            logger.exception(
                "reconstruct_form_state failed for App #%d: %s", application_id, exc
            )
            return {"success": False, "error": str(exc)}


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

        Requires a valid, non-expired server-issued approval token stored in
        ``applications.approval_token`` (set by ``issue_approval_token()``).
        Every other signal — status, autofill success, form validity, timeouts,
        defaults, or agent assumptions — fails closed.
        """
        app = session.get(Application, application_id)
        if app is None:
            raise ValueError(f"Application {application_id} not found.")

        # ── M1: DB-column token validation (fail closed) ─────────────────
        if not is_token_valid(approval_token, app.approval_token, app.approval_token_expires_at):
            raise SubmissionApprovalRequiredError(
                f"Application {application_id} submission blocked: token invalid, expired, or "
                "not yet issued."
            )

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

    def execute_browser_submission(
        self,
        session: Session,
        application_id: int,
        *,
        expected_claimed_by: str | None = None,
        expected_submission_claimed_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Orchestrate the autonomous browser submission for an explicitly approved application.

        Authorization model (M1 handoff repair):
        - The application must have been atomically claimed (``submission_claimed_at IS NOT NULL``).
        - Durable approval is ``approved_at IS NOT NULL`` — the human approval token is consumed
          and cleared by ``confirm_and_submit`` and must NOT be passed to the adapter as a
          credential. The token was single-use; its consumption is proof of approval, not its
          presence.
        - Form state is reconstructed via ``reconstruct_form_state()`` which operates on the
          existing Application record without creating a duplicate or requiring READY_TO_APPLY.
        """
        # Force a database refresh at the execution boundary.  The expected
        # identity still comes exclusively from the worker that won the claim;
        # it is never reconstructed from this fresh row.
        app = session.get(Application, application_id, populate_existing=True)
        if app is None:
            raise ValueError(f"Application {application_id} not found.")

        # Gate 1: Must be atomically claimed
        if app.submission_claimed_at is None:
            raise RuntimeError(
                f"Application #{app.id} must be claimed before executing browser submission"
            )

        if (
            not isinstance(expected_claimed_by, str)
            or not expected_claimed_by.strip()
            or not isinstance(expected_submission_claimed_at, datetime)
            or app.claimed_by != expected_claimed_by
            or app.submission_claimed_at != expected_submission_claimed_at
        ):
            return {
                "success": False,
                "status": "stale_claim",
                "reason": "manual_final_action_transition_rejected",
            }

        # Gate 2: Durable approval must be present
        if app.approved_at is None:
            raise RuntimeError(
                f"Application #{app.id} has no durable approval (approved_at is None). "
                "confirm_and_submit() must be called before the worker can execute."
            )

        if app.status == ApplicationStatus.SUBMITTED.value:
            return {"success": True, "already_submitted": True}

        if app.status not in {ApplicationStatus.FORM_FILLED.value, ApplicationStatus.PENDING.value}:
            raise ValueError(f"Cannot submit application in status {app.status!r}")

        opp = session.get(Opportunity, app.opportunity_id)
        if opp is None:
            raise ValueError(f"Opportunity {app.opportunity_id} not found.")

        if opp.status != OpportunityStatus.AWAITING_SUBMISSION.value:
            raise ValueError(f"Cannot submit application for opportunity in status {opp.status!r}")

        if self.adapter_override is not None:
            adapter = self.adapter_override
        else:
            adapter = resolve_adapter(opp)
        if not hasattr(adapter, "submit_application"):
            raise TypeError(f"Adapter {type(adapter).__name__} does not implement submit_application")

        logger.info(
            "Executing browser submission for App #%d (Opp #%d) — approved_at=%s, claimed_at=%s",
            app.id, opp.id, app.approved_at, app.submission_claimed_at,
        )

        try:
            # 1. Reconstruct the final review screen using stored fill data.
            #    reconstruct_form_state operates on the existing Application record,
            #    requires AWAITING_SUBMISSION (not READY_TO_APPLY), and reuses stored
            #    custom answers — it does NOT create a duplicate Application row.
            recon_res = self.reconstruct_form_state(session, app.id)
            if not recon_res.get("success"):
                reason = recon_res.get("error") or "Could not reconstruct form state for submission"
                logger.error("Form state reconstruction failed for App #%d: %s", app.id, reason)
                application_service.transition_application_status(
                    session,
                    app.id,
                    ApplicationStatus.FAILED,
                    notes=f"{app.notes}\n[RECONSTRUCT_ERROR]: {reason}",
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

            app_ctx = recon_res.get("app_ctx")
            if not app_ctx:
                return {"success": False, "error": "No ApplicationContext returned from reconstruct_form_state"}

            # 2. Execute the final submit action.
            #    Authorization proof: approved_at IS NOT NULL (durable) +
            #    submission_claimed_at IS NOT NULL (atomic exclusive claim).
            #    The human approval token has been consumed — do NOT pass it here.
            submit_res = adapter.submit_application(app_ctx)

            if not submit_res.get("success"):
                if (
                    submit_res.get("manual_review_required") is True
                    and submit_res.get("error") == "manual_final_action_required"
                    and isinstance(submit_res.get("reason"), str)
                ):
                    manual_reason = submit_res.get("reason")
                    transitioned = application_service.handoff_claimed_application_to_manual_review(
                        session,
                        app.id,
                        reason=manual_reason,
                        expected_claimed_by=expected_claimed_by,
                        expected_submission_claimed_at=expected_submission_claimed_at,
                    )
                    if not transitioned:
                        session.rollback()
                        return {
                            "success": False,
                            "status": "stale_claim",
                            "reason": "manual_final_action_transition_rejected",
                        }
                    session.commit()
                    return {
                        "success": False,
                        "status": "manual_required",
                        "reason": f"manual_final_action_required:{manual_reason}",
                    }

                # AMBIGUOUS TIMEOUT / FAILURE — FAIL CLOSED
                # Do not automatically retry: a submission may have occurred before
                # the process lost confirmation. Transition to manual review.
                reason = submit_res.get("error") or "Submission failed or timed out ambiguously"
                logger.error("Browser submission failed/ambiguous for App #%d: %s", app.id, reason)

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
            conf_ref = (
                submit_res.get("confirmation_ref")
                or f"BROWSER-{adapter.adapter_name.upper()}-CONFIRMED"
            )

            # Evidence capture (best-effort — never fail a verified submission over this)
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
                "status": "verified_success",
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

            # Persist success state
            notes_data: dict[str, Any] = {}
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
                    "approved_by": app.approved_by or notes_data.get("approved_by", "unknown"),
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
                "evidence": str(evidence_file),
            }

        except Exception:
            # A failed application/opportunity transition must never be left
            # half-persisted for a later caller to commit.
            session.rollback()
            logger.exception("Unexpected exception during execute_browser_submission")
            return {
                "success": False,
                "status": "error",
                "reason": "browser_submission_execution_failed",
            }

