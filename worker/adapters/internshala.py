"""Internshala experimental-tier platform adapter (Phase 9).

Per §5.3: Experimental adapter using Playwright with an encrypted storage_state
from an interactively authenticated session.
Fills the form, attaches resume, fills drafted answers (clearly marked as AI drafts),
and LEAVES THE BROWSER WINDOW OPEN at the completed, unsubmitted form.
The human reviews in the browser and clicks Submit themselves.
The Worker never programmatically clicks submit.
Fails closed if session is expired or CAPTCHA/bot-check is encountered.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.config import settings
from core.status import ReliabilityTier
from worker.adapters.base import (
    ApplicationContext,
    BasePlatformAdapter,
    ExtractedListing,
    FillResult,
    SubmissionStatus,
)
from worker.security.storage import load_decrypted_storage_state

logger = logging.getLogger(__name__)

DEFAULT_SESSION_FILE = Path("worker/storage/internshala_storage_state.enc")


class InternshalaAdapter(BasePlatformAdapter):
    """Experimental Playwright adapter for Internshala."""

    adapter_name = "internshala"
    tier = ReliabilityTier.EXPERIMENTAL

    def __init__(
        self,
        session_file: Path | str | None = None,
        headless: bool | None = None,
        playwright_instance: Any = None,
    ) -> None:
        self.session_file = Path(session_file or DEFAULT_SESSION_FILE)
        self.headless = headless if headless is not None else settings.WORKER_HEADLESS
        self._pw = playwright_instance

    def extract(self, opportunity_data: Any) -> ExtractedListing:
        """Extract job metadata and standard questions for Internshala."""
        url = getattr(opportunity_data, "url", None) or opportunity_data.get("url", "")
        company = getattr(opportunity_data, "company", None) or opportunity_data.get("company", "Unknown")
        title = getattr(opportunity_data, "title", None) or opportunity_data.get("title", "Unknown")

        # Standard Internshala questions commonly present on applications
        custom_q = [
            {
                "id": "cover_letter",
                "label": "Why should you be hired for this role?",
                "required": True,
                "type": "textarea",
            },
            {
                "id": "availability",
                "label": "Are you available to join immediately for the required duration?",
                "required": True,
                "type": "text",
            },
        ]

        return ExtractedListing(
            company=company,
            title=title,
            url=url,
            fields_required=["phone", "resume"],
            custom_questions=custom_q,
            supports_programmatic_submission=True,
        )

    def open_application(self, url: str, **kwargs: Any) -> ApplicationContext:
        """Launch Playwright browser with decrypted session state and open application."""
        opportunity_id = kwargs.get("opportunity_id", 0)
        extracted = self.extract({"url": url, "opportunity_id": opportunity_id})

        if not self.session_file.exists():
            logger.warning("Internshala session state not found at %s", self.session_file)
            return ApplicationContext(
                opportunity_id=opportunity_id,
                listing_url=url,
                adapter_name=self.adapter_name,
                tier=self.tier,
                extracted=extracted,
                metadata={"session_missing": True},
            )

        try:
            from playwright.sync_api import sync_playwright

            p = self._pw or sync_playwright().start()
            storage_state = load_decrypted_storage_state(self.session_file)

            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(storage_state=storage_state)
            page = context.new_page()

            page.goto(url, wait_until="domcontentloaded", timeout=30000)

            # Check for bot challenge or login redirect
            current_url = page.url
            if "login" in current_url:
                return ApplicationContext(
                    opportunity_id=opportunity_id,
                    listing_url=url,
                    adapter_name=self.adapter_name,
                    tier=self.tier,
                    extracted=extracted,
                    browser_context=context,
                    browser_page=page,
                    metadata={"session_expired": True},
                )

            return ApplicationContext(
                opportunity_id=opportunity_id,
                listing_url=url,
                adapter_name=self.adapter_name,
                tier=self.tier,
                extracted=extracted,
                browser_context=context,
                browser_page=page,
            )
        except Exception as exc:
            logger.exception("Failed to open Internshala application with Playwright: %s", exc)
            return ApplicationContext(
                opportunity_id=opportunity_id,
                listing_url=url,
                adapter_name=self.adapter_name,
                tier=self.tier,
                extracted=extracted,
                metadata={"error": str(exc)},
            )

    def fill(
        self,
        app_ctx: ApplicationContext,
        candidate_data: dict[str, Any],
        resume_path: str | None = None,
        custom_answers: list[dict[str, Any]] | None = None,
    ) -> FillResult:
        """Fill the form elements on the live page and leave browser open at unsubmitted state."""
        if app_ctx.metadata.get("session_missing"):
            return FillResult(
                success=False,
                status="manual_required",
                error_reason="Internshala session state missing. Run python -m worker.setup_session --platform internshala",
            )
        if app_ctx.metadata.get("session_expired"):
            return FillResult(
                success=False,
                status="manual_required",
                error_reason="Internshala session expired (redirected to login). Please re-authenticate.",
            )
        if app_ctx.metadata.get("error"):
            return FillResult(
                success=False,
                status="manual_required",
                error_reason=f"Playwright browser initialization error: {app_ctx.metadata['error']}",
            )

        page = app_ctx.browser_page
        if page is None:
            return FillResult(
                success=False,
                status="manual_required",
                error_reason="No active browser page available for Internshala.",
            )

        processed_answers: list[dict[str, Any]] = []
        try:
            # Check for CAPTCHA / bot challenge
            if page.locator("iframe[src*='recaptcha'], iframe[src*='hcaptcha'], div.g-recaptcha").count() > 0:
                return FillResult(
                    success=False,
                    status="manual_required",
                    error_reason="CAPTCHA or bot-verification detected on Internshala page. Fail closed per policy.",
                )

            # Click Apply Now button if on listing overview page
            apply_btn = page.locator("#apply_now_button, button:has-text('Apply now'), a:has-text('Apply now')")
            if apply_btn.count() > 0 and apply_btn.first.is_visible():
                apply_btn.first.click()
                page.wait_for_timeout(2000)

            # Map and fill custom questions
            answers_map = {
                str(a.get("question_id") or a.get("question_text") or a.get("label")): a.get("answer", "")
                for a in (custom_answers or [])
            }

            # Cover letter / "Why should you be hired"
            cover_letter_text = answers_map.get("cover_letter") or answers_map.get("Why should you be hired for this role?", "")
            if not cover_letter_text and custom_answers:
                cover_letter_text = custom_answers[0].get("answer", "")

            if cover_letter_text:
                # Always ensure AI-draft marker is prominently displayed
                formatted_text = cover_letter_text
                if "[AI DRAFT" not in formatted_text:
                    formatted_text = f"[AI DRAFT - PENDING APPROVAL]\n\n{formatted_text}"

                textarea = page.locator("textarea#cover_letter, textarea[name*='cover_letter'], textarea.cover_letter")
                if textarea.count() > 0:
                    textarea.first.fill(formatted_text)

                processed_answers.append({
                    "question_id": "cover_letter",
                    "label": "Why should you be hired for this role?",
                    "answer": formatted_text,
                    "is_ai_draft": True,
                })

            # Availability
            avail_ans = answers_map.get("availability") or "Yes, I am available to join immediately."
            avail_input = page.locator("input#other_experiences, textarea#other_experiences, input[name*='availability']")
            if avail_input.count() > 0:
                avail_input.first.fill(avail_ans)
                processed_answers.append({
                    "question_id": "availability",
                    "label": "Availability",
                    "answer": avail_ans,
                    "is_ai_draft": False,
                })

            # Resume upload if requested
            if resume_path and Path(resume_path).exists():
                file_input = page.locator("input[type='file']")
                if file_input.count() > 0:
                    file_input.first.set_input_files(resume_path)

            # CRITICAL RULE: Leave browser open at unsubmitted form. Never click submit!
            return FillResult(
                success=True,
                status="ready_for_review",
                message="Internshala form filled and left open in browser. Review and click Submit yourself.",
                custom_answers=processed_answers,
            )
        except Exception as exc:
            logger.exception("Error during Internshala fill: %s", exc)
            return FillResult(
                success=False,
                status="manual_required",
                error_reason=f"Error filling Internshala form: {exc}",
            )

    def check_status(self, app_ctx: ApplicationContext) -> SubmissionStatus:
        """Check if application was already submitted on Internshala."""
        page = app_ctx.browser_page
        if page is None:
            return SubmissionStatus(confirmed=False, status="unknown", detail="No active browser page")

        try:
            # Check for already applied banner or confirmation message
            applied_badge = page.locator("text='Already applied', text='Application submitted'")
            if applied_badge.count() > 0 and applied_badge.first.is_visible():
                return SubmissionStatus(
                    confirmed=True,
                    status="confirmed",
                    detail="Internshala indicates application is already submitted.",
                )
            return SubmissionStatus(confirmed=False, status="not_submitted", detail="Not yet submitted")
        except Exception as exc:
            return SubmissionStatus(confirmed=False, status="ambiguous", detail=str(exc))
