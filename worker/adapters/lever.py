"""Lever stable-tier platform adapter (Phase 9).

Per §5.3: Documented-API adapter. Uses Lever Postings API (https://api.lever.co/v0/postings/...).
Assembles the submission payload into a reviewable draft and requires explicit
human confirmation before submitting.
Fails closed to manual application if no programmatic path is available.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import httpx

from core.status import ReliabilityTier
from worker.adapters.base import (
    ApplicationContext,
    BasePlatformAdapter,
    ExtractedListing,
    FillResult,
    SubmissionStatus,
)

logger = logging.getLogger(__name__)

# Pattern for Lever posting URLs:
# e.g. https://jobs.lever.co/spotify/1234-abcd-5678
LEVER_URL_REGEX = re.compile(
    r"https?://jobs\.lever\.co/(?P<site>[^/]+)/(?P<posting_id>[^/?#]+)",
    re.I,
)


class LeverAdapter(BasePlatformAdapter):
    """Stable HTTP adapter for Lever Job Postings."""

    adapter_name = "lever"
    tier = ReliabilityTier.STABLE

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self._client = http_client or httpx.Client(timeout=15.0)

    def extract_site_and_posting_id(self, url: str) -> tuple[str | None, str | None]:
        """Extract site slug and posting ID from standard Lever URL."""
        match = LEVER_URL_REGEX.search(url)
        if match:
            return match.group("site"), match.group("posting_id")
        return None, None

    def extract(self, opportunity_data: Any) -> ExtractedListing:
        """Fetch posting details and questions from Lever API."""
        url = getattr(opportunity_data, "url", None) or opportunity_data.get("url", "")
        company = getattr(opportunity_data, "company", None) or opportunity_data.get("company", "Unknown")
        title = getattr(opportunity_data, "title", None) or opportunity_data.get("title", "Unknown")

        site, posting_id = self.extract_site_and_posting_id(url)
        if not site or not posting_id:
            logger.warning("Could not extract Lever site and posting_id from URL: %s", url)
            return ExtractedListing(
                company=company,
                title=title,
                url=url,
                supports_programmatic_submission=False,
                metadata={"reason": "Non-standard Lever URL structure"},
            )

        api_url = f"https://api.lever.co/v0/postings/{site}/{posting_id}"
        try:
            resp = self._client.get(api_url)
            if resp.status_code != 200:
                logger.warning("Lever API returned %d for %s", resp.status_code, api_url)
                return ExtractedListing(
                    company=company,
                    title=title,
                    url=url,
                    board_token=site,
                    job_id=posting_id,
                    supports_programmatic_submission=False,
                    metadata={"status_code": resp.status_code},
                )

            data = resp.json()
            custom_q: list[dict[str, Any]] = []

            # Lever custom questions / custom questions cards
            for cq in data.get("customQuestions", []):
                custom_q.append({
                    "id": str(cq.get("id")),
                    "label": cq.get("text", ""),
                    "required": bool(cq.get("required", False)),
                    "type": cq.get("type", "textarea"),
                })

            return ExtractedListing(
                company=company,
                title=data.get("text", title),
                url=url,
                board_token=site,
                job_id=posting_id,
                fields_required=["name", "email", "phone", "resume"],
                custom_questions=custom_q,
                supports_programmatic_submission=True,
                metadata={
                    "location": data.get("categories", {}).get("location"),
                    "team": data.get("categories", {}).get("team"),
                },
            )
        except Exception as exc:
            logger.exception("Error extracting Lever listing %s: %s", url, exc)
            return ExtractedListing(
                company=company,
                title=title,
                url=url,
                board_token=site,
                job_id=posting_id,
                supports_programmatic_submission=False,
                metadata={"error": str(exc)},
            )

    def open_application(self, url: str, **kwargs: Any) -> ApplicationContext:
        """Create ApplicationContext for Lever HTTP operations."""
        opportunity_id = kwargs.get("opportunity_id", 0)
        extracted = self.extract({"url": url, "opportunity_id": opportunity_id})
        return ApplicationContext(
            opportunity_id=opportunity_id,
            listing_url=url,
            adapter_name=self.adapter_name,
            tier=self.tier,
            extracted=extracted,
            http_client=self._client,
        )

    def fill(
        self,
        app_ctx: ApplicationContext,
        candidate_data: dict[str, Any],
        resume_path: str | None = None,
        custom_answers: list[dict[str, Any]] | None = None,
    ) -> FillResult:
        """Assemble structured profile data and AI-drafted answers into Lever draft payload."""
        extracted = app_ctx.extracted or self.extract({"url": app_ctx.listing_url})
        app_ctx.extracted = extracted

        if not extracted.supports_programmatic_submission:
            reason = extracted.metadata.get("reason") or "Lever posting has no programmatic API endpoint."
            return FillResult(
                success=False,
                status="manual_required",
                error_reason=reason,
            )

        payload_fields: dict[str, Any] = {
            "name": str(candidate_data.get("full_name") or "").strip(),
            "email": candidate_data.get("email", ""),
            "phone": candidate_data.get("phone", ""),
        }

        # Links (e.g. LinkedIn, GitHub)
        links = candidate_data.get("links", [])
        urls: dict[str, str] = {}
        for link in links:
            l_type = link.get("link_type", "").lower()
            l_url = link.get("url", "")
            if "linkedin" in l_type:
                urls["LinkedIn"] = l_url
            elif "github" in l_type:
                urls["GitHub"] = l_url
            elif "portfolio" in l_type or "website" in l_type:
                urls["Portfolio"] = l_url
            elif l_type:
                urls[l_type] = l_url

        if urls:
            payload_fields["urls"] = urls

        # Custom questions matching
        answers_map = {
            str(a.get("question_id") or a.get("question_text")): a.get("answer", "")
            for a in (custom_answers or [])
        }
        processed_answers: list[dict[str, Any]] = []

        comments: list[str] = []
        for q in extracted.custom_questions:
            q_id = q["id"]
            q_label = q["label"]
            ans = answers_map.get(q_id) or answers_map.get(q_label, "")
            processed_answers.append({
                "question_id": q_id,
                "label": q_label,
                "answer": ans,
                "required": q["required"],
                "is_ai_draft": "[AI DRAFT" in ans,
            })
            if ans:
                comments.append(f"{q_label}:\n{ans}")

        if comments:
            payload_fields["comments"] = "\n\n".join(comments)

        draft_payload = {
            "site": extracted.board_token,
            "posting_id": extracted.job_id,
            "submission_url": f"https://api.lever.co/v0/postings/{extracted.board_token}/{extracted.job_id}",
            "fields": payload_fields,
            "resume_path": resume_path,
            "custom_answers": processed_answers,
        }

        return FillResult(
            success=True,
            status="ready_for_review",
            message="Lever submission payload prepared as pending draft. Awaiting human confirmation.",
            draft_payload=draft_payload,
            custom_answers=processed_answers,
        )

    def check_status(self, app_ctx: ApplicationContext) -> SubmissionStatus:
        """Check if posting is still active."""
        extracted = app_ctx.extracted or self.extract({"url": app_ctx.listing_url})
        if not extracted.board_token or not extracted.job_id:
            return SubmissionStatus(confirmed=False, status="unknown", detail="Invalid site/posting_id")

        check_url = f"https://api.lever.co/v0/postings/{extracted.board_token}/{extracted.job_id}"
        try:
            resp = self._client.get(check_url)
            if resp.status_code == 200:
                return SubmissionStatus(
                    confirmed=False,
                    status="not_submitted",
                    detail="Job posting is active on Lever.",
                )
            elif resp.status_code == 404:
                return SubmissionStatus(
                    confirmed=False,
                    status="closed",
                    detail="Job posting is closed on Lever.",
                )
            return SubmissionStatus(confirmed=False, status="unknown", detail=f"HTTP status {resp.status_code}")
        except Exception as exc:
            return SubmissionStatus(confirmed=False, status="ambiguous", detail=str(exc))

    def execute_submission(self, draft_payload: dict[str, Any]) -> dict[str, Any]:
        """Execute the HTTP submission to Lever API upon human confirmation."""
        submission_url = draft_payload["submission_url"]
        fields = draft_payload.get("fields", {})
        resume_path = draft_payload.get("resume_path")

        files: dict[str, Any] = {}
        if resume_path and Path(resume_path).exists():
            files["resume"] = (Path(resume_path).name, open(resume_path, "rb"), "application/pdf")

        try:
            resp = self._client.post(submission_url, data=fields, files=files if files else None)
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
