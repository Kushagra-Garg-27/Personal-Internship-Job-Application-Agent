"""Gemini-powered custom question answer drafting engine (Phase 9).

Per §5.2 and §5.6:
- Free-text application questions ("Why do you want to work here?", etc.) receive
  a tailored answer drafted by Gemini, reusing Phase 5's client & quota budget.
- HARD RULE: Every generated answer is prefixed with `[AI DRAFT - PENDING APPROVAL]`
  and requires explicit human review and approval before submission.
- Never auto-submits generated text.
"""

from __future__ import annotations

import logging
from typing import Any

from core.config import settings
from core.funnel.scam_risk.gemini_client import GeminiQuotaTracker, quota_tracker

logger = logging.getLogger(__name__)

AI_DRAFT_PREFIX = "[AI DRAFT - PENDING APPROVAL]"


class QuestionDrafter:
    """Generates tailored drafts for custom free-text job application questions."""

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
            logger.error("Failed to initialize Google GenAI client in QuestionDrafter: %s", exc)
            return None

    def draft_answer(
        self,
        question_text: str,
        opportunity_title: str,
        company: str,
        candidate_profile: dict[str, Any],
        job_description: str | None = None,
    ) -> str:
        """Draft an answer for a specific question using candidate background and role context."""
        # 1. Quota check
        if not self.tracker.record_call():
            logger.warning("Gemini daily quota exhausted for question drafting")
            return (
                f"{AI_DRAFT_PREFIX} (Daily quota exhausted - manual draft needed)\n\n"
                f"I am excited to apply for the {opportunity_title} role at {company}. "
                f"My experience in software engineering and problem-solving makes me a strong fit."
            )

        client = self._get_client()
        if client is None:
            return (
                f"{AI_DRAFT_PREFIX}\n\n"
                f"I am eager to contribute to {company} in the {opportunity_title} position. "
                f"My technical background aligns with your engineering goals."
            )

        # 2. Candidate background summary
        full_name = candidate_profile.get("full_name", "the applicant")
        skills = candidate_profile.get("skills", [])
        skills_str = ", ".join(
            s.get("skill_name", str(s)) if isinstance(s, dict) else str(s)
            for s in skills[:10]
        )
        education = candidate_profile.get("education", [])
        edu_summary = ""
        if education:
            first_edu = education[0]
            if isinstance(first_edu, dict):
                edu_summary = f"{first_edu.get('degree', '')} in {first_edu.get('branch', '')} from {first_edu.get('institution', '')}"

        desc_snippet = (job_description or "")[:1000]

        prompt = f"""You are an expert career advisor drafting a job application question response for a candidate.

Candidate Information:
- Name: {full_name}
- Education: {edu_summary}
- Core Skills: {skills_str}

Target Opportunity:
- Role: {opportunity_title}
- Company: {company}
- Job Overview: {desc_snippet}

Question to Answer:
"{question_text}"

Instructions:
1. Write a professional, authentic, first-person response (1-2 paragraphs max, under 150 words).
2. Connect the candidate's skills and interests specifically to the company and role.
3. Be direct, humble, and persuasive without generic fluff.
4. Output ONLY the drafted answer text without quotation marks, conversational intros, or notes.
"""

        try:
            response = client.models.generate_content(
                model=self.model_name,
                contents=prompt,
            )
            raw_text = response.text if hasattr(response, "text") else str(response)
            clean_text = raw_text.strip().strip('"')
            return f"{AI_DRAFT_PREFIX}\n\n{clean_text}"
        except Exception as exc:
            logger.exception("Gemini API call failed while drafting question answer: %s", exc)
            return (
                f"{AI_DRAFT_PREFIX} (Draft generation error: {exc})\n\n"
                f"I am eager to apply for the {opportunity_title} role at {company}."
            )
