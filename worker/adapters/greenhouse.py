"""Greenhouse stable-tier platform adapter (Phase 9).

Per §5.3: Documented-API adapter. No browser needed, uses HTTP calls to public
Greenhouse Board API endpoints.
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

# Patterns for Greenhouse job board URLs:
# e.g. https://boards.greenhouse.io/acme/jobs/12345
# e.g. https://job-boards.greenhouse.io/acme/jobs/12345
GREENHOUSE_URL_REGEX = re.compile(
    r"https?://(?:job-)?boards\.greenhouse\.io/(?P<board>[^/]+)/jobs/(?P<job_id>\d+)",
    re.I,
)


class GreenhouseAdapter(BasePlatformAdapter):
    """Stable HTTP adapter for Greenhouse Job Boards."""

    adapter_name = "greenhouse"
    tier = ReliabilityTier.STABLE

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self._client = http_client or httpx.Client(timeout=15.0)

    def extract_board_and_job_id(self, url: str) -> tuple[str | None, str | None]:
        """Extract board token and job ID from standard Greenhouse URL."""
        match = GREENHOUSE_URL_REGEX.search(url)
        if match:
            return match.group("board"), match.group("job_id")
        return None, None

    def extract(self, opportunity_data: Any) -> ExtractedListing:
        """Fetch job details and required application questions from Greenhouse API."""
        url = getattr(opportunity_data, "url", None) or opportunity_data.get("url", "")
        company = getattr(opportunity_data, "company", None) or opportunity_data.get("company", "Unknown")
        title = getattr(opportunity_data, "title", None) or opportunity_data.get("title", "Unknown")

        board, job_id = self.extract_board_and_job_id(url)
        if not board or not job_id:
            logger.warning("Could not extract Greenhouse board and job_id from URL: %s", url)
            return ExtractedListing(
                company=company,
                title=title,
                url=url,
                supports_programmatic_submission=False,
                metadata={"reason": "Non-standard Greenhouse URL structure"},
            )

        api_url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}?questions=true"
        try:
            resp = self._client.get(api_url)
            if resp.status_code != 200:
                logger.warning("Greenhouse API returned %d for %s", resp.status_code, api_url)
                return ExtractedListing(
                    company=company,
                    title=title,
                    url=url,
                    board_token=board,
                    job_id=job_id,
                    supports_programmatic_submission=False,
                    metadata={"status_code": resp.status_code},
                )

            data = resp.json()
            questions = data.get("questions", [])
            custom_q: list[dict[str, Any]] = []

            for q in questions:
                # Greenhouse standard fields vs custom questions
                fields = q.get("fields", [])
                name = fields[0].get("name") if fields else q.get("name")
                if name in {"first_name", "last_name", "email", "phone", "resume"}:
                    continue

                custom_q.append({
                    "id": str(q.get("id")),
                    "label": q.get("label", ""),
                    "required": bool(q.get("required", False)),
                    "type": fields[0].get("type", "textarea") if fields else "textarea",
                    "name": name,
                })

            return ExtractedListing(
                company=company,
                title=data.get("title", title),
                url=url,
                board_token=board,
                job_id=job_id,
                fields_required=["first_name", "last_name", "email", "phone", "resume"],
                custom_questions=custom_q,
                supports_programmatic_submission=True,
                metadata={"location": data.get("location", {}).get("name")},
            )
        except Exception as exc:
            logger.exception("Error extracting Greenhouse listing %s: %s", url, exc)
            return ExtractedListing(
                company=company,
                title=title,
                url=url,
                board_token=board,
                job_id=job_id,
                supports_programmatic_submission=False,
                metadata={"error": str(exc)},
            )

    def open_application(self, url: str, **kwargs: Any) -> ApplicationContext:
        """Create ApplicationContext for Greenhouse HTTP operations."""
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
        """Assemble structured profile data and AI-drafted answers into a pending draft payload."""
        extracted = app_ctx.extracted or self.extract({"url": app_ctx.listing_url})
        app_ctx.extracted = extracted

        if not extracted.supports_programmatic_submission:
            reason = extracted.metadata.get("reason") or "Greenhouse posting has no programmatic API endpoint."
            return FillResult(
                success=False,
                status="manual_required",
                error_reason=reason,
            )

        full_name = str(candidate_data.get("full_name") or "").strip()
        name_parts = full_name.split(maxsplit=1)
        first_name = name_parts[0] if name_parts else ""
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        payload_fields: dict[str, Any] = {
            "first_name": first_name,
            "last_name": last_name,
            "email": candidate_data.get("email", ""),
            "phone": candidate_data.get("phone", ""),
        }

        # Match custom questions with provided drafted answers
        answers_map = {
            str(a.get("question_id") or a.get("question_text")): a.get("answer", "")
            for a in (custom_answers or [])
        }
        processed_answers: list[dict[str, Any]] = []

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
            if q.get("name"):
                payload_fields[q["name"]] = ans

        draft_payload = {
            "board": extracted.board_token,
            "job_id": extracted.job_id,
            "submission_url": f"https://boards-api.greenhouse.io/v1/boards/{extracted.board_token}/jobs/{extracted.job_id}",
            "fields": payload_fields,
            "resume_path": resume_path,
            "custom_answers": processed_answers,
        }

        return FillResult(
            success=True,
            status="ready_for_review",
            message="Greenhouse submission payload prepared as pending draft. Awaiting human confirmation.",
            draft_payload=draft_payload,
            custom_answers=processed_answers,
        )

    def check_status(self, app_ctx: ApplicationContext) -> SubmissionStatus:
        """Check if posting is still active or application was received."""
        extracted = app_ctx.extracted or self.extract({"url": app_ctx.listing_url})
        if not extracted.board_token or not extracted.job_id:
            return SubmissionStatus(confirmed=False, status="unknown", detail="Invalid board/job_id")

        check_url = f"https://boards-api.greenhouse.io/v1/boards/{extracted.board_token}/jobs/{extracted.job_id}"
        try:
            resp = self._client.get(check_url)
            if resp.status_code == 200:
                return SubmissionStatus(
                    confirmed=False,
                    status="not_submitted",
                    detail="Job posting is active on Greenhouse.",
                )
            elif resp.status_code == 404:
                return SubmissionStatus(
                    confirmed=False,
                    status="closed",
                    detail="Job posting is closed or no longer accepting applications.",
                )
            return SubmissionStatus(confirmed=False, status="unknown", detail=f"HTTP status {resp.status_code}")
        except Exception as exc:
            return SubmissionStatus(confirmed=False, status="ambiguous", detail=str(exc))

    def execute_submission(self, draft_payload: dict[str, Any]) -> dict[str, Any]:
        """Execute the HTTP submission to Greenhouse API upon human confirmation.
        Fails closed on timeouts: performs status check before any retry.
        """
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
                    "confirmation_ref": str(data.get("id") or "GH-CONFIRMED"),
                    "status_code": resp.status_code,
                }
            return {
                "success": False,
                "error": f"Greenhouse API returned HTTP {resp.status_code}: {resp.text[:300]}",
                "status_code": resp.status_code,
            }
        except httpx.TimeoutException as exc:
            logger.error("Ambiguous timeout submitting to Greenhouse (%s): %s", submission_url, exc)
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
