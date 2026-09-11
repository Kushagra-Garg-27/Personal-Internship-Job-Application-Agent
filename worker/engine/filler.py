"""Worker application fill orchestrator (Phase 9).

Coordinates deterministic profile data mapping, resume resolution, AI custom question
drafting, adapter dispatch, and fail-closed human-submit gating.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from core.models.opportunity import Application, Opportunity
from core.models.profile import Profile
from core.models.resume import Resume
from core.repositories import profile_repo
from core.services import application_service, opportunity_service
from core.status import ApplicationStatus, OpportunityStatus, ReliabilityTier
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
    }


class ApplicationFiller:
    """Orchestrates the filling of approved opportunities."""

    def __init__(self, question_drafter: QuestionDrafter | None = None) -> None:
        self.question_drafter = question_drafter or QuestionDrafter()

    def process_opportunity(
        self,
        session: Session,
        opportunity_id: int,
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
            return {"status": "manual_required", "reason": reason}

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
        }

    def confirm_and_submit(
        self,
        session: Session,
        application_id: int,
    ) -> dict[str, Any]:
        """Human confirmation action to execute submission for stable HTTP adapters.
        Handles ambiguous timeouts by verifying status before retry.
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

        notes_data = {}
        if app.notes:
            try:
                notes_data = json.loads(app.notes)
            except Exception:
                pass

        adapter = resolve_adapter(opp)

        # ── Experimental Browser Tier ─────────────────────────────────
        # Candidate physically clicked submit in the opened browser window;
        # this confirm action updates and persists the confirmed state.
        if not hasattr(adapter, "execute_submission"):
            now = datetime.now(timezone.utc)
            conf_ref = f"BROWSER-{adapter.adapter_name.upper()}-SUBMITTED"
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
                reason=f"Human confirmed physical submission in {adapter.adapter_name} browser window",
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
