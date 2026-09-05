"""Gemini-powered suggested reply drafting for recruiter responses (Phase 10).

Per §5.4 and §7 of Job_Agent_Architecture_v2_Revised.md:
- Reuses Phase 5's Gemini client and quota tracker.
- Drafts responses for reply-bearing categories: interview_invite, screening_question, offer, follow_up.
- Non-reply categories (e.g. rejection) do not warrant a reply.
- HARD RULE: Every draft is clearly marked with:
  ``[AI SUGGESTED REPLY - EDIT BEFORE SENDING]``
- Never presented as anything other than a draft requiring explicit approval.
"""

from __future__ import annotations

import logging
from typing import Any

from core.config import settings
from core.funnel.scam_risk.gemini_client import GeminiQuotaTracker, quota_tracker
from core.models.message import RecruiterMessage
from core.models.opportunity import Application, Opportunity
from core.models.profile import Profile

logger = logging.getLogger(__name__)

AI_REPLY_PREFIX = "[AI SUGGESTED REPLY - EDIT BEFORE SENDING]"

REPLY_BEARING_CATEGORIES: set[str] = {
    "interview_invite",
    "screening_question",
    "offer",
    "follow_up",
}

NON_REPLY_CATEGORIES: set[str] = {
    "rejection",
    "generic",
    "unclassified",
}


def is_reply_bearing(classification: str | None) -> bool:
    """Check whether a message classification plausibly warrants an email reply."""
    if not classification:
        return False
    return classification.lower().strip() in REPLY_BEARING_CATEGORIES


class ReplyDrafter:
    """Drafts contextual email replies to recruiter messages using Gemini."""

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        tracker: GeminiQuotaTracker | None = None,
        client: Any = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.GEMINI_API_KEY
        self.model_name = model_name if model_name is not None else settings.GEMINI_MODEL
        self.tracker = tracker or quota_tracker
        self._client = client

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.api_key:
            return None
        try:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
            return self._client
        except Exception as exc:
            logger.error("Failed to initialize Google GenAI client in ReplyDrafter: %s", exc)
            return None

    def _build_fallback_reply(
        self,
        candidate_name: str,
        company: str,
        role: str,
        classification: str,
        note: str = "",
    ) -> str:
        """Construct a clean, professional template draft when Gemini is unavailable."""
        header = f"{AI_REPLY_PREFIX}{f' ({note})' if note else ''}\n\n"

        if classification == "offer":
            body = (
                f"Dear Hiring Team,\n\n"
                f"Thank you so much for extending an offer for the {role} position at {company}! "
                f"I am thrilled and grateful for this opportunity. I will review the details carefully "
                f"and look forward to discussing the next steps with you soon.\n\n"
                f"Best regards,\n"
                f"{candidate_name}"
            )
        elif classification == "interview_invite":
            body = (
                f"Dear Hiring Team,\n\n"
                f"Thank you for the invitation to interview for the {role} position at {company}. "
                f"I am very excited about this opportunity and look forward to speaking with the team. "
                f"I am generally flexible this week and would be glad to coordinate a time that works best for your schedule.\n\n"
                f"Best regards,\n"
                f"{candidate_name}"
            )
        elif classification == "screening_question":
            body = (
                f"Dear Hiring Team,\n\n"
                f"Thank you for reaching out regarding my application for the {role} role at {company}. "
                f"I am pleased to provide more information and would welcome the opportunity to discuss my background in more detail.\n\n"
                f"Best regards,\n"
                f"{candidate_name}"
            )
        else:
            body = (
                f"Dear Hiring Team,\n\n"
                f"Thank you for reaching out regarding the {role} position at {company}. "
                f"I appreciate the update and remain very interested in the opportunity.\n\n"
                f"Best regards,\n"
                f"{candidate_name}"
            )

        return f"{header}{body}"

    def draft_reply(
        self,
        message: RecruiterMessage,
        opportunity: Opportunity | None = None,
        profile: Profile | None = None,
        application: Application | None = None,
    ) -> str:
        """Draft a reply to a recruiter message.

        Parameters
        ----------
        message : RecruiterMessage
            The received email message.
        opportunity : Opportunity | None
            The linked job opportunity.
        profile : Profile | None
            The candidate's profile.
        application : Application | None
            The submission attempt record.

        Returns
        -------
        str
            The drafted response body prefixed with ``AI_REPLY_PREFIX``.

        Raises
        ------
        ValueError
            If the message classification is not a reply-bearing category.
        """
        classification = (message.classification or "unclassified").lower().strip()
        if not is_reply_bearing(classification):
            raise ValueError(
                f"Message #{message.id} classified as '{classification}' does not warrant a reply draft."
            )

        # Candidate and opportunity details
        candidate_name = profile.full_name if profile and profile.full_name else "Candidate"
        company = opportunity.company if opportunity and opportunity.company else "your company"
        role = opportunity.title if opportunity and opportunity.title else "the open position"

        # Check quota
        if not self.tracker.record_call():
            logger.warning("Gemini daily quota exhausted for reply drafting")
            return self._build_fallback_reply(
                candidate_name, company, role, classification, note="Daily quota exhausted - template draft"
            )

        client = self._get_client()
        if client is None:
            return self._build_fallback_reply(
                candidate_name, company, role, classification, note="Offline template draft"
            )

        # Contextual summary
        skills_summary = ""
        if profile and profile.skills:
            skills_summary = ", ".join(s.skill_name for s in profile.skills[:8])

        resume_info = ""
        if application and application.resume:
            resume_info = f"Resume used: {application.resume.filename}"

        app_notes = application.notes if application and application.notes else ""
        recruiter_text = (message.body_preview or "")[:1500]

        prompt = f"""You are an expert career communications advisor assisting a job candidate with drafting an email reply to a recruiter.

Candidate Information:
- Candidate Name: {candidate_name}
- Core Skills: {skills_summary}
{resume_info}
{f"Application Notes: {app_notes}" if app_notes else ""}

Opportunity Information:
- Company: {company}
- Role: {role}

Recruiter Email Details:
- Sender: {message.sender}
- Subject: {message.subject or "No Subject"}
- Classification: {classification}
- Email Content Preview:
\"\"\"{recruiter_text}\"\"\"

Instructions:
1. Write a professional, authentic, first-person response from {candidate_name} to the sender.
2. Tone: polite, confident, enthusiastic, and respectful. Keep it under 150 words.
3. Guidelines by classification:
   - interview_invite: Express enthusiasm, confirm strong interest in the {role} role at {company}, and indicate availability or readiness to schedule.
   - offer: Express gratitude and excitement, and acknowledge receiving the offer details.
   - screening_question / follow_up: Directly and pleasantly acknowledge the question or inquiry, providing a cooperative response.
4. Do NOT make up specific interview dates/times or personal details not provided.
5. Output ONLY the body of the email. Do not include subject line, headers, markdown fences, or meta commentary.
"""

        try:
            response = client.models.generate_content(
                model=self.model_name,
                contents=prompt,
            )
            raw_text = response.text if hasattr(response, "text") else str(response)
            clean_text = raw_text.strip().strip('"').strip("'")
            return f"{AI_REPLY_PREFIX}\n\n{clean_text}"
        except Exception as exc:
            logger.exception("Gemini API call failed while drafting reply: %s", exc)
            return self._build_fallback_reply(
                candidate_name, company, role, classification, note=f"Generation fallback: {exc}"
            )
