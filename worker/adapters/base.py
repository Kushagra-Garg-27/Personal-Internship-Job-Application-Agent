"""Base platform adapter interface (Phase 9).

Defines the contract all platform adapters (Stable HTTP and Experimental Playwright)
must satisfy:
- extract(): Parses fields, questions, and submission endpoint from listing
- open_application(): Sets up HTTP session or opens browser page
- fill(): Fills deterministic profile info and drafted answers, stopping before submit
- check_status(): Idempotent check to determine if an application was received
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from core.status import ReliabilityTier


@dataclass
class ExtractedListing:
    """Extracted structure of an application form/posting."""

    company: str
    title: str
    url: str
    board_token: str | None = None
    job_id: str | None = None
    fields_required: list[str] = field(default_factory=list)
    custom_questions: list[dict[str, Any]] = field(default_factory=list)
    supports_programmatic_submission: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApplicationContext:
    """Context state holding session, page, or connection data for an active application."""

    opportunity_id: int
    listing_url: str
    adapter_name: str
    tier: ReliabilityTier
    extracted: ExtractedListing | None = None
    browser_context: Any = None
    browser_page: Any = None
    http_client: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FillResult:
    """Result of attempting to fill an application form."""

    success: bool
    status: str  # "ready_for_review", "manual_required", "failed"
    message: str | None = None
    draft_payload: dict[str, Any] | None = None
    custom_answers: list[dict[str, Any]] = field(default_factory=list)
    error_reason: str | None = None


@dataclass
class SubmissionStatus:
    """Status check result for duplicate prevention and outcome verification."""

    confirmed: bool
    status: str  # "confirmed", "submitted", "not_found", "unknown", "ambiguous"
    confirmation_ref: str | None = None
    detail: str | None = None


class BasePlatformAdapter(ABC):
    """Abstract base class for all application automation adapters."""

    adapter_name: str
    tier: ReliabilityTier

    #: When True, a real, on-disk resume file is required before this adapter
    #: may fill an application.  Browser-tier forms (e.g. Unstop) upload the
    #: file directly, so a missing/unreadable resume must fail closed as
    #: ``REAL_RESUME_REQUIRED`` rather than proceeding without one.
    requires_resume: bool = False

    @abstractmethod
    def extract(self, opportunity_data: Any) -> ExtractedListing:
        """Extract job details, required fields, and custom questions."""
        raise NotImplementedError

    @abstractmethod
    def open_application(self, url: str, **kwargs: Any) -> ApplicationContext:
        """Prepare session or browser page for application filling."""
        raise NotImplementedError

    @abstractmethod
    def fill(
        self,
        app_ctx: ApplicationContext,
        candidate_data: dict[str, Any],
        resume_path: str | None = None,
        custom_answers: list[dict[str, Any]] | None = None,
    ) -> FillResult:
        """Fill structured fields and drafted custom answers into form or draft payload.
        System invariant: Never autonomously submits; explicit human authorization is required.
        """
        raise NotImplementedError

    @abstractmethod
    def check_status(self, app_ctx: ApplicationContext) -> SubmissionStatus:
        """Query platform idempotently to check if an application has already been submitted."""
        raise NotImplementedError
