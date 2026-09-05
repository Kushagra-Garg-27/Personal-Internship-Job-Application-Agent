"""Unstop experimental-tier platform adapter (Phase 9).

Per §5.3: Experimental adapter using Playwright with an encrypted storage_state
from an interactively authenticated Unstop session.
Fills candidate details, attaches resume, fills AI-drafted answers, and
LEAVES THE BROWSER WINDOW OPEN at the completed, unsubmitted form.
The human reviews in the browser and clicks Submit themselves.
The Worker never programmatically clicks submit.
Fails closed on session expiry, CAPTCHA/bot challenges, or UI blocks.
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

DEFAULT_SESSION_FILE = Path("worker/storage/unstop_storage_state.enc")


class UnstopAdapter(BasePlatformAdapter):
    """Experimental Playwright adapter for Unstop."""

    adapter_name = "unstop"
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
        """Extract job metadata and questions for Unstop."""
        url = getattr(opportunity_data, "url", None) or opportunity_data.get("url", "")
        company = getattr(opportunity_data, "company", None) or opportunity_data.get("company", "Unknown")
        title = getattr(opportunity_data, "title", None) or opportunity_data.get("title", "Unknown")

        custom_q = [
            {
                "id": "statement_of_purpose",
                "label": "Why do you want to apply for this opportunity?",
                "required": False,
                "type": "textarea",
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
        """Launch Playwright browser with decrypted session state and open Unstop application."""
        opportunity_id = kwargs.get("opportunity_id", 0)
        extracted = self.extract({"url": url, "opportunity_id": opportunity_id})

        if not self.session_file.exists():
            logger.warning("Unstop session state not found at %s", self.session_file)
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

            if "auth/login" in page.url or "login" in page.url:
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
            logger.exception("Failed to open Unstop application with Playwright: %s", exc)
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
        """Fill the form elements on Unstop page and leave browser open at unsubmitted state."""
        if app_ctx.metadata.get("session_missing"):
            return FillResult(
                success=False,
                status="manual_required",
                error_reason="Unstop session state missing. Run python -m worker.setup_session --platform unstop",
            )
        if app_ctx.metadata.get("session_expired"):
            return FillResult(
                success=False,
                status="manual_required",
                error_reason="Unstop session expired. Please re-authenticate.",
            )
        if app_ctx.metadata.get("error"):
            return FillResult(
                success=False,
                status="manual_required",
                error_reason=f"Playwright error on Unstop: {app_ctx.metadata['error']}",
            )

        page = app_ctx.browser_page
        if page is None:
            return FillResult(
                success=False,
                status="manual_required",
                error_reason="No active browser page available for Unstop.",
            )

        processed_answers: list[dict[str, Any]] = []
        try:
            # Check for Cloudflare / CAPTCHA challenge
            if page.locator("iframe[src*='challenges.cloudflare'], div#challenge-stage").count() > 0:
                return FillResult(
                    success=False,
                    status="manual_required",
                    error_reason="Cloudflare bot verification detected on Unstop. Fail closed per policy.",
                )

            # Click Apply on Unstop if present
            apply_btn = page.locator("button:has-text('Apply Now'), button:has-text('Register')")
            if apply_btn.count() > 0 and apply_btn.first.is_visible():
                apply_btn.first.click()
                page.wait_for_timeout(2000)

            # Map custom questions
            answers_map = {
                str(a.get("question_id") or a.get("question_text") or a.get("label")): a.get("answer", "")
                for a in (custom_answers or [])
            }

            sop_text = answers_map.get("statement_of_purpose") or answers_map.get("Why do you want to apply for this opportunity?", "")
            if not sop_text and custom_answers:
                sop_text = custom_answers[0].get("answer", "")

            if sop_text:
                formatted_text = sop_text
                if "[AI DRAFT" not in formatted_text:
                    formatted_text = f"[AI DRAFT - PENDING APPROVAL]\n\n{formatted_text}"

                textarea = page.locator("textarea[name*='statement'], textarea[placeholder*='Why'], textarea")
                if textarea.count() > 0:
                    textarea.first.fill(formatted_text)

                processed_answers.append({
                    "question_id": "statement_of_purpose",
                    "label": "Why do you want to apply for this opportunity?",
                    "answer": formatted_text,
                    "is_ai_draft": True,
                })

            # Resume upload if present
            if resume_path and Path(resume_path).exists():
                file_input = page.locator("input[type='file']")
                if file_input.count() > 0:
                    file_input.first.set_input_files(resume_path)

            # CRITICAL: Do NOT click submit button. Leave browser window open.
            return FillResult(
                success=True,
                status="ready_for_review",
                message="Unstop form filled and left open in browser. Review and click Submit yourself.",
                custom_answers=processed_answers,
            )
        except Exception as exc:
            logger.exception("Error during Unstop fill: %s", exc)
            return FillResult(
                success=False,
                status="manual_required",
                error_reason=f"Error filling Unstop form: {exc}",
            )

    def check_status(self, app_ctx: ApplicationContext) -> SubmissionStatus:
        """Check if application was already submitted on Unstop."""
        page = app_ctx.browser_page
        if page is None:
            return SubmissionStatus(confirmed=False, status="unknown", detail="No active browser page")

        try:
            applied_el = page.locator("text='Registered', text='Applied', text='Application Completed'")
            if applied_el.count() > 0 and applied_el.first.is_visible():
                return SubmissionStatus(
                    confirmed=True,
                    status="confirmed",
                    detail="Unstop indicates application is already submitted.",
                )
            return SubmissionStatus(confirmed=False, status="not_submitted", detail="Not yet submitted")
        except Exception as exc:
            return SubmissionStatus(confirmed=False, status="ambiguous", detail=str(exc))
