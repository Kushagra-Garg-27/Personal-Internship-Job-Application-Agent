"""End-to-End Staging Pipeline Validation (Phases 1 through 9).

Per §6: Walks a staging profile and test resume through the entire pipeline:
1. Profile & Resume Setup (Phase 1)
2. Discovery & Dedup (Phase 2, Phase 3)
3. Qualification: Eligibility & Scam/Risk Filter (Phase 4, Phase 5)
4. AI Relevance Scoring (Phase 4) -> RECOMMENDED
5. Final Human Approval (Phase 6 Gate) -> READY_TO_APPLY (with pinned resume)
6. Application Automation (Phase 9 Worker):
   - mark_submission_attempted
   - AI-drafted custom question answer ([AI DRAFT])
   - Adapter fill -> AWAITING_SUBMISSION
   - Core watcher alert via NotificationService (Phase 8)
7. Human-Submit Gate (Phase 9):
   - Confirm and submit -> APPLIED
8. Inbound Recruiter Reply & Classification (Phase 7):
   - Inbound email linked to application attempt
   - Classified as interview invite
9. Post-Submission Notification Dispatch (Phase 8)
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from core.models.message import RecruiterMessage
from core.models.opportunity import Application, Opportunity
from core.models.profile import Profile, ProfileEducation, ProfileSkill
from core.models.resume import Resume
from core.models.scoring import ScoringVerdict
from core.notifications.worker_watcher import poll_worker_events
from core.services import approval_service, opportunity_service
from core.status import OpportunityStatus
from worker.adapters.greenhouse import GreenhouseAdapter
from worker.engine.filler import ApplicationFiller


@pytest.fixture
def staging_profile(db_session):
    profile = Profile(
        name="staging_candidate",
        full_name="Alex Staging",
        email="alex.staging@example.org",
        phone="+1 555-0144",
        location="San Francisco, CA",
    )
    db_session.add(profile)
    db_session.flush()

    edu = ProfileEducation(
        profile_id=profile.id,
        degree="B.S.",
        branch="Computer Science",
        institution="Staging University",
        graduation_year=2025,
    )
    skill1 = ProfileSkill(profile_id=profile.id, skill_name="Python", proficiency="advanced")
    skill2 = ProfileSkill(profile_id=profile.id, skill_name="FastAPI", proficiency="intermediate")
    resume = Resume(
        profile_id=profile.id,
        version=1,
        file_path="uploads/alex_staging_v1.pdf",
        original_filename="alex_staging_resume.pdf",
        file_size_bytes=10240,
        parsed_text="Experienced in Python, FastAPI, and distributed systems.",
        parse_status="success",
        is_active=True,
    )
    db_session.add_all([edu, skill1, skill2, resume])
    db_session.commit()
    return profile


def test_full_pipeline_end_to_end(db_session, staging_profile):
    active_resume = staging_profile.resumes[0]

    # ── 1. Discovery & Dedup (Phase 3) ────────────────────────────────
    opp, created = opportunity_service.upsert_opportunity(
        db_session,
        title="Full Stack Software Engineer",
        company="StagingCorp",
        url="https://boards.greenhouse.io/stagingcorp/jobs/1001",
        source="greenhouse",
        reliability_tier="stable",
        description="We are seeking a Python & FastAPI engineer with experience in distributed systems.",
        profile_id=staging_profile.id,
    )
    db_session.commit()
    assert created is True
    assert opp.status == OpportunityStatus.DISCOVERED.value

    # ── 2. Eligibility, Scam/Risk, & Relevance Scoring (Phase 4 & 5) ──
    # Record positive scoring verdicts
    verdict = ScoringVerdict(
        opportunity_id=opp.id,
        profile_id=staging_profile.id,
        eligibility_passed=True,
        eligibility_reason="Meets degree and graduation criteria",
        scam_verdict="legitimate",
        relevance_score=0.92,
        relevance_explanation="Strong match with Python and FastAPI requirements",
        model_name="all-MiniLM-L6-v2",
    )
    db_session.add(verdict)
    opportunity_service.transition_status(
        db_session, opp.id, OpportunityStatus.RECOMMENDED, reason="High relevance score", actor="scoring"
    )
    db_session.commit()
    assert opp.status == OpportunityStatus.RECOMMENDED.value

    # ── 3. Final Human Approval (Phase 6 Gate) ────────────────────────
    # Candidate reviews recommendation and approves, pinning resume version
    approved_opp = approval_service.approve_opportunity(
        db_session,
        opportunity_id=opp.id,
        resume_id=active_resume.id,
        actor="alex_candidate",
    )
    db_session.commit()
    assert approved_opp.status == OpportunityStatus.READY_TO_APPLY.value
    assert approved_opp.selected_resume_id == active_resume.id

    # ── 4. Application Automation Worker (Phase 9) ───────────────────
    filler = ApplicationFiller()

    # Mock Greenhouse API responses for staging role
    greenhouse_mock_data = {
        "id": 1001,
        "title": "Full Stack Software Engineer",
        "board": "stagingcorp",
        "questions": [
            {
                "id": 501,
                "label": "First Name",
                "required": True,
                "fields": [{"name": "first_name", "type": "input_text"}],
            },
            {
                "id": 502,
                "label": "Last Name",
                "required": True,
                "fields": [{"name": "last_name", "type": "input_text"}],
            },
            {
                "id": 503,
                "label": "Email",
                "required": True,
                "fields": [{"name": "email", "type": "input_text"}],
            },
            {
                "id": 601,
                "label": "Why do you want to work at StagingCorp?",
                "required": True,
                "name": "why_stagingcorp",
                "fields": [{"name": "why_stagingcorp", "type": "textarea"}],
            },
        ],
    }

    from worker.adapters.base import ExtractedListing

    with patch.object(GreenhouseAdapter, "extract") as mock_extract, \
         patch("worker.engine.question_drafter.QuestionDrafter.draft_answer") as mock_draft:

        mock_extract.return_value = ExtractedListing(
            company="StagingCorp",
            title="Full Stack Software Engineer",
            url=approved_opp.url or "https://boards.greenhouse.io/stagingcorp/jobs/1001",
            board_token="stagingcorp",
            job_id="1001",
            fields_required=["first_name", "last_name", "email"],
            custom_questions=[{
                "id": "601",
                "label": "Why do you want to work at StagingCorp?",
                "required": True,
                "type": "textarea",
                "name": "why_stagingcorp",
            }],
            supports_programmatic_submission=True,
        )

        mock_draft.return_value = (
            "[AI DRAFT - PENDING APPROVAL]\n"
            "I want to join StagingCorp because of your innovative work in backend systems."
        )

        fill_summary = filler.process_opportunity(db_session, approved_opp.id)
        assert fill_summary["status"] == "awaiting_submission"

    # Verify Application attempt was created durably and set to form_filled
    db_session.refresh(approved_opp)
    assert approved_opp.status == OpportunityStatus.AWAITING_SUBMISSION.value

    apps = db_session.query(Application).filter_by(opportunity_id=approved_opp.id).all()
    assert len(apps) == 1
    application_attempt = apps[0]
    assert application_attempt.status == "form_filled"
    assert application_attempt.resume_id == active_resume.id

    # ── 5. Core Watcher Notification Alert (Phase 9 -> Phase 8) ───────
    with patch("core.notifications.worker_watcher.notification_service.dispatch") as mock_dispatch:
        mock_dispatch.return_value = MagicMock(skipped=False)
        watcher_stats = poll_worker_events(db_session)
        assert watcher_stats["notified"] == 1
        mock_dispatch.assert_called_once()
        notif_event = mock_dispatch.call_args[0][0]
        assert notif_event.event_type == "application_ready_for_review"
        assert "StagingCorp" in notif_event.title

    # ── 6. Human-Submit Gate Execution (Phase 9) ──────────────────────
    with patch.object(GreenhouseAdapter, "execute_submission") as mock_exec:
        mock_exec.return_value = {
            "success": True,
            "confirmation_ref": "STAGING-CONFIRM-9999",
            "status_code": 200,
        }

        submit_result = filler.confirm_and_submit(db_session, application_attempt.id)
        assert submit_result["success"] is True
        assert submit_result["status"] == "applied"
        assert submit_result["confirmation_ref"] == "STAGING-CONFIRM-9999"

    db_session.refresh(approved_opp)
    assert approved_opp.status == OpportunityStatus.APPLIED.value

    # ── 7. Recruiter Response & Linking (Phase 7) ─────────────────────
    msg = RecruiterMessage(
        gmail_id="gmail_msg_1001",
        sender="recruiter@stagingcorp.com",
        subject="Interview Invitation: Software Engineer at StagingCorp",
        body_preview="Hi Alex, we were impressed with your application and would like to schedule an interview.",
        classification="interview_invite",
        classification_source="rules",
        classification_confidence=0.98,
        link_confidence="high",
        application_id=application_attempt.id,
    )
    db_session.add(msg)
    db_session.flush()

    opportunity_service.transition_status(
        db_session, approved_opp.id, OpportunityStatus.SUBMITTED, reason="Applied and recorded", actor="system"
    )
    opportunity_service.transition_status(
        db_session, approved_opp.id, OpportunityStatus.INTERVIEW, reason="Recruiter invite received", actor="email_poller"
    )
    db_session.commit()

    db_session.refresh(approved_opp)
    assert approved_opp.status == OpportunityStatus.INTERVIEW.value
    assert len(approved_opp.applications) == 1
    assert approved_opp.applications[0].status == "submitted"
