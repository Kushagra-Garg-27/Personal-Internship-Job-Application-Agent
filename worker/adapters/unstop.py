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

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from core.config import settings
from core.discovery.base import RawOpportunity
from core.status import ReliabilityTier
from worker.adapters.base import (
    ApplicationContext,
    BasePlatformAdapter,
    ExtractedListing,
    FillResult,
    SubmissionStatus,
)
from worker.adapters.submission_controls import (
    SubmissionControlResolutionStatus,
    TerminalStepContext,
    redact_url,
    revalidate_handle_before_click,
    resolve_final_submission_control,
)
from worker.security.storage import load_decrypted_storage_state

logger = logging.getLogger(__name__)

DEFAULT_SESSION_FILE = Path("worker/storage/unstop_storage_state.enc")


class AuthState(str):
    """Case-insensitive auth state string with backward-compatibility aliases."""

    _ALIASES: dict[str, set[str]] = {
        "VALID": {"VALID", "AUTHENTICATED"},
        "AUTHENTICATED": {"VALID", "AUTHENTICATED"},
        "EXPIRED": {"EXPIRED", "SESSION_EXPIRED"},
        "SESSION_EXPIRED": {"EXPIRED", "SESSION_EXPIRED"},
        "MISSING": {"MISSING", "SESSION_MISSING"},
        "SESSION_MISSING": {"MISSING", "SESSION_MISSING"},
        "INVALID": {"INVALID", "SESSION_CORRUPTED", "CORRUPTED"},
        "SESSION_CORRUPTED": {"INVALID", "SESSION_CORRUPTED", "CORRUPTED"},
        "CHECK_FAILED": {"CHECK_FAILED", "NETWORK_ERROR", "FAILED"},
        "UNKNOWN_AUTH_STATE": {"UNKNOWN_AUTH_STATE", "UNKNOWN"},
    }

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            my_upper = self.upper()
            other_upper = other.upper()
            if my_upper == other_upper:
                return True
            aliases = self._ALIASES.get(my_upper, {my_upper})
            return other_upper in aliases
        return super().__eq__(other)

    def __hash__(self) -> int:
        return hash(self.upper())


# Explicit outcome constants (U5)
VALID = AuthState("AUTHENTICATED")
EXPIRED = AuthState("SESSION_EXPIRED")
MISSING = AuthState("SESSION_MISSING")
INVALID = AuthState("SESSION_CORRUPTED")
CHECK_FAILED = AuthState("CHECK_FAILED")
UNKNOWN_AUTH_STATE = AuthState("UNKNOWN_AUTH_STATE")

# Backward-compatibility aliases
AUTHENTICATED = VALID
SESSION_MISSING = MISSING
SESSION_EXPIRED = EXPIRED
SESSION_CORRUPTED = INVALID

SESSION_REMEDIATION_GUIDE: dict[str, str] = {
    "MISSING": "Unstop session state file not found. Run 'python -m worker.setup_session --platform unstop' to authenticate.",
    "EXPIRED": "Unstop session has expired or was revoked. Run 'python -m worker.setup_session --platform unstop' to re-authenticate.",
    "INVALID": "Unstop session state file is corrupted or decryption failed. Run 'python -m worker.setup_session --platform unstop' to generate a fresh session.",
    "CHECK_FAILED": "Unstop session verification check could not reach or verify with the platform. Check network connectivity or re-authenticate.",
}


def probe_authenticated_endpoint(
    cookies_or_state: list[dict[str, Any]] | dict[str, Any],
    endpoint_url: str = "https://unstop.com/api/profile",
    timeout: float = 10.0,
    storage_state: dict[str, Any] | None = None,
) -> tuple[bool, AuthState, str]:
    """Perform a read-only probe against an authenticated Unstop endpoint.

    Accepts either a list of cookies or the full Playwright storage_state dict.
    Extracts accessToken from localStorage origins when present.
    Never exposes cookies, tokens, or credentials in returned detail or logs.
    """
    import urllib.error
    import urllib.request

    if isinstance(cookies_or_state, dict):
        state = cookies_or_state
        cookies = state.get("cookies", [])
    else:
        cookies = cookies_or_state
        state = storage_state

    unstop_cookies = [
        f"{c['name']}={c['value']}"
        for c in (cookies or [])
        if "unstop.com" in c.get("domain", "") and c.get("name") and c.get("value")
    ]
    if not unstop_cookies:
        return False, EXPIRED, "No valid Unstop cookies available to probe."

    cookie_header = "; ".join(unstop_cookies)

    # Extract optional accessToken from storage state localStorage origins
    access_token: str | None = None
    if isinstance(state, dict):
        origins = state.get("origins", [])
        for orig in origins:
            if "unstop.com" in orig.get("origin", ""):
                for item in orig.get("localStorage", []):
                    if item.get("name") in ("accessToken", "access_token", "token") and item.get("value"):
                        val = str(item["value"]).strip()
                        if val:
                            access_token = val
                            break
            if access_token:
                break

    class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://unstop.com/",
        "Origin": "https://unstop.com",
        "X-Requested-With": "XMLHttpRequest",
        "Cookie": cookie_header,
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    opener = urllib.request.build_opener(_NoRedirectHandler)
    req = urllib.request.Request(
        endpoint_url,
        headers=headers,
    )

    try:
        with opener.open(req, timeout=timeout) as resp:
            final_url = resp.geturl() or ""
            if "auth/login" in final_url or "/login" in final_url:
                return False, EXPIRED, "Platform redirected probe to login (session unauthenticated/expired)."
            if resp.status == 200:
                return True, VALID, "Authenticated session verified by platform endpoint."
            return False, EXPIRED, f"Platform returned status {resp.status} for session probe."

    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            loc = exc.headers.get("Location") or ""
            if "login" in loc or "auth" in loc:
                return False, EXPIRED, "Platform redirected probe to login page (session expired)."
            return False, EXPIRED, "Session redirected to unauthenticated destination."

        if exc.code in (401, 403):
            body_preview = ""
            try:
                body_preview = exc.read().decode("utf-8", errors="ignore")[:300].lower()
            except Exception:
                pass
            if "challenge" in body_preview or "cloudflare" in body_preview:
                return False, CHECK_FAILED, "Platform presented bot verification challenge during session probe."
            return False, EXPIRED, f"Platform rejected session credentials (HTTP {exc.code})."

        if exc.code >= 500:
            return False, CHECK_FAILED, f"Platform server error (HTTP {exc.code}) during session probe."

        return False, CHECK_FAILED, f"Unexpected HTTP response {exc.code} during session probe."

    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return False, CHECK_FAILED, f"Network communication failure during session probe: {type(exc).__name__}"
    except Exception as exc:
        return False, CHECK_FAILED, f"Session check failed unexpectedly: {type(exc).__name__}"


class QuestionClassification(str, Enum):
    PROFILE_FACT = "PROFILE_FACT"
    SAFE_INFERENCE = "SAFE_INFERENCE"
    REQUIRES_USER = "REQUIRES_USER"
    UNSUPPORTED = "UNSUPPORTED"
    # U9: platform/application consent (terms acceptance).  Consent is
    # application-scoped and can only be resolved by an explicit answer tied to
    # that application.  It is never a static profile fact, never implied by the
    # existence of a checkbox, and never defaulted to True.
    CONSENT = "CONSENT"


# Explicit affirmative answers that resolve a consent question.  Anything else
# (including absence) leaves the consent unresolved.
_EXPLICIT_CONSENT_ANSWERS = {
    "true", "yes", "i accept", "accepted", "accept", "agree",
    "i agree", "consent", "i consent", "on",
}


@dataclass
class FormField:
    id: str
    name: str | None
    tag: str
    type: str | None
    label: str
    required: bool = False
    placeholder: str = ""
    options: list[str] = field(default_factory=list)
    current_value: str | None = None
    source: str | None = None
    confidence: float = 1.0
    field_role: str | None = None
    classification: QuestionClassification | None = None


@dataclass
class SessionStatus:
    """Authentication and session state for Unstop adapter."""

    valid: bool
    status: AuthState | str  # VALID, EXPIRED, MISSING, INVALID, CHECK_FAILED
    detail: str | None = None
    username: str | None = None
    cookies_count: int = 0


class UnstopAdapter(BasePlatformAdapter):
    """Experimental Playwright adapter for Unstop."""

    adapter_name = "unstop"
    tier = ReliabilityTier.EXPERIMENTAL
    # Unstop uploads the resume file directly into the form, so a real,
    # on-disk resume is mandatory; a missing file fails closed.
    requires_resume = True

    def __init__(
        self,
        session_file: Path | str | None = None,
        encryption_key: str | bytes | None = None,
        headless: bool | None = None,
        playwright_instance: Any = None,
        probe_network: bool = False,
    ) -> None:
        self.session_file = Path(session_file or DEFAULT_SESSION_FILE)
        self.encryption_key = encryption_key
        self.headless = headless if headless is not None else settings.WORKER_HEADLESS
        self._pw = playwright_instance
        self.probe_network = probe_network

    def check_session_status(
        self,
        probe_network: bool | None = None,
        timeout: float = 10.0,
    ) -> SessionStatus:
        """Check whether local encrypted session state is available and contains valid cookies."""
        should_probe = self.probe_network if probe_network is None else probe_network

        if not self.session_file.exists():
            return SessionStatus(
                valid=False,
                status=MISSING,
                detail=f"{SESSION_REMEDIATION_GUIDE['MISSING']} (path={self.session_file})",
            )
        try:
            storage_state = load_decrypted_storage_state(self.session_file, key=self.encryption_key)
        except Exception as exc:
            return SessionStatus(
                valid=False,
                status=INVALID,
                detail=f"Failed to decrypt or load session state: {exc}. {SESSION_REMEDIATION_GUIDE['INVALID']}",
            )

        if not isinstance(storage_state, dict):
            return SessionStatus(
                valid=False,
                status=INVALID,
                detail=f"Decrypted storage state is not a valid JSON dictionary. {SESSION_REMEDIATION_GUIDE['INVALID']}",
            )

        cookies = storage_state.get("cookies", []) if isinstance(storage_state, dict) else []
        unstop_cookies = [c for c in cookies if "unstop.com" in c.get("domain", "")]
        if not unstop_cookies:
            return SessionStatus(
                valid=False,
                status=EXPIRED,
                detail=f"No Unstop cookies found in session state. {SESSION_REMEDIATION_GUIDE['EXPIRED']}",
                cookies_count=0,
            )

        # Check for expired cookies if expiration timestamps are set
        now_ts = datetime.now(timezone.utc).timestamp()
        valid_unstop_cookies = [
            c for c in unstop_cookies
            if c.get("expires", -1) == -1 or c.get("expires", 0) > now_ts
        ]
        if not valid_unstop_cookies:
            return SessionStatus(
                valid=False,
                status=EXPIRED,
                detail=f"All Unstop cookies in session state have expired. {SESSION_REMEDIATION_GUIDE['EXPIRED']}",
                cookies_count=len(unstop_cookies),
            )

        # Optional live endpoint verification probe
        if should_probe:
            is_valid, probe_status, probe_detail = probe_authenticated_endpoint(
                unstop_cookies, timeout=timeout, storage_state=storage_state
            )
            return SessionStatus(
                valid=is_valid,
                status=probe_status,
                detail=probe_detail,
                cookies_count=len(valid_unstop_cookies),
            )

        return SessionStatus(
            valid=True,
            status=VALID,
            detail=f"Found {len(valid_unstop_cookies)} valid Unstop cookies in encrypted session.",
            cookies_count=len(valid_unstop_cookies),
        )

    def normalize_listing(self, item: dict[str, Any]) -> RawOpportunity:
        """Map raw listing data (from DOM or JSON API) to canonical RawOpportunity."""
        title = (item.get("title") or "Unknown Title").strip()
        company = (item.get("company") or item.get("organisation_name") or "Unknown Company").strip()
        url = item.get("url") or item.get("seo_url") or ""
        if url and not url.startswith("http"):
            url = f"https://unstop.com{url}" if url.startswith("/") else f"https://unstop.com/{url}"

        posted_at = None
        start_dt = item.get("posted_at") or item.get("start_date")
        if isinstance(start_dt, datetime):
            posted_at = start_dt
        elif isinstance(start_dt, str):
            try:
                posted_at = datetime.fromisoformat(start_dt.replace("Z", "+00:00"))
            except Exception:
                pass

        deadline_at = None
        end_dt = item.get("deadline_at") or item.get("end_date")
        if isinstance(end_dt, datetime):
            deadline_at = end_dt
        elif isinstance(end_dt, str):
            try:
                deadline_at = datetime.fromisoformat(end_dt.replace("Z", "+00:00"))
            except Exception:
                pass

        salary_min = item.get("salary_min")
        salary_max = item.get("salary_max")
        if not isinstance(salary_min, int):
            salary_min = None
        if not isinstance(salary_max, int):
            salary_max = None

        metadata = {
            "platform": "unstop",
            "external_id": str(item.get("external_id") or item.get("id") or ""),
            "skills": item.get("skills", []),
            "filters": item.get("filters", []),
        }

        return RawOpportunity(
            title=title,
            company=company,
            url=url,
            source=self.adapter_name,
            description=item.get("description"),
            location=item.get("location"),
            salary_min=salary_min,
            salary_max=salary_max,
            posted_at=posted_at,
            deadline_at=deadline_at,
            metadata=metadata,
        )

    def discover_via_api(
        self,
        limit: int = 18,
        opportunity_type: str = "jobs",
    ) -> list[RawOpportunity]:
        """Discover live opportunities using Unstop public JSON search API."""
        import urllib.request

        endpoint = f"https://unstop.com/api/public/opportunity/search-result?opportunity={opportunity_type}&page=1&per_page={limit}&oppstatus=open"
        req = urllib.request.Request(
            endpoint,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.warning("Unstop API discovery request failed: %s", exc)
            return []

        items = data.get("data", {}).get("data", [])
        raw_opps: list[RawOpportunity] = []
        for it in items:
            org = it.get("organisation") or {}
            org_name = org.get("name") if isinstance(org, dict) else str(org or "Unknown")
            locs = it.get("locations") or []
            loc_parts = []
            for loc in locs:
                if isinstance(loc, dict):
                    city = loc.get("city") or ""
                    state = loc.get("state") or ""
                    country = loc.get("country") or ""
                    part = ", ".join(p for p in [city, state, country] if p)
                    if part:
                        loc_parts.append(part)
                elif isinstance(loc, str) and loc.strip():
                    loc_parts.append(loc.strip())
            location_str = "; ".join(loc_parts) if loc_parts else None

            regn = it.get("regnRequirements") or it.get("regn_requirements") or {}
            raw_item = {
                "id": str(it.get("id", "")),
                "external_id": str(it.get("id", "")),
                "title": it.get("title", ""),
                "company": org_name,
                "url": it.get("seo_url") or "",
                "location": location_str,
                "posted_at": regn.get("start_regn_dt"),
                "deadline_at": regn.get("end_regn_dt") or it.get("end_date"),
                "description": it.get("details"),
                "skills": [s.get("name") for s in it.get("required_skills", []) if isinstance(s, dict) and "name" in s],
                "filters": it.get("filters", []),
            }
            raw_opps.append(self.normalize_listing(raw_item))
        return raw_opps

    def discover_via_browser(
        self,
        limit: int = 10,
        opportunity_type: str = "jobs",
        browser_context: Any = None,
    ) -> list[RawOpportunity]:
        """Discover live opportunities using Playwright browser DOM parsing."""
        from playwright.sync_api import sync_playwright

        p = self._pw or sync_playwright().start()
        context = browser_context
        owns_context = False
        if context is None:
            browser = p.chromium.launch(headless=self.headless)
            if self.session_file.exists():
                try:
                    storage_state = load_decrypted_storage_state(self.session_file, key=self.encryption_key)
                    context = browser.new_context(storage_state=storage_state)
                except Exception:
                    context = browser.new_context()
            else:
                context = browser.new_context()
            owns_context = True

        page = context.new_page()
        try:
            url = f"https://unstop.com/{opportunity_type}"
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            # Check for Cloudflare / bot challenge
            if page.locator("iframe[src*='challenges.cloudflare'], div#challenge-stage").count() > 0:
                logger.warning("Bot challenge detected on Unstop during discovery.")
                return []

            # Find job card links
            card_locators = page.locator("a[href*='/jobs/'], a[href*='/opportunity/'], a[href*='/internships/']").all()
            raw_opps: list[RawOpportunity] = []
            seen_urls: set[str] = set()

            for card in card_locators[:limit]:
                try:
                    href = card.get_attribute("href") or ""
                    full_url = f"https://unstop.com{href}" if href.startswith("/") else href
                    if not full_url or full_url in seen_urls:
                        continue
                    seen_urls.add(full_url)

                    # Extract title
                    title_el = card.locator("h3[itemprop='name'], h3, .item-title, strong").first
                    title = title_el.inner_text().strip() if title_el.count() > 0 else "Unknown Title"

                    # Extract company
                    company_el = card.locator("p.single-wrap, p.company-name, .organisation-name").first
                    company = company_el.inner_text().strip() if company_el.count() > 0 else "Unknown Company"

                    card_text = card.inner_text()
                    lines = [ln.strip() for ln in card_text.split("\n") if ln.strip()]

                    location = None
                    for line in lines:
                        if "in office" in line.lower() or "remote" in line.lower() or "hybrid" in line.lower():
                            location = line
                            break

                    raw_item = {
                        "title": title,
                        "company": company,
                        "url": full_url,
                        "location": location,
                        "description": None,
                    }
                    raw_opps.append(self.normalize_listing(raw_item))
                except Exception as exc:
                    logger.debug("Failed extracting card: %s", exc)

            return raw_opps
        finally:
            page.close()
            if owns_context:
                context.close()

    def discover(
        self,
        limit: int = 10,
        opportunity_type: str = "jobs",
        require_auth: bool = False,
        mode: str = "browser",
    ) -> list[RawOpportunity]:
        """Discover live opportunities from Unstop.

        Fails closed if require_auth=True and session state is invalid.
        """
        if require_auth:
            status = self.check_session_status()
            if not status.valid:
                raise PermissionError(f"Unstop authentication required: {status.status} ({status.detail})")

        if mode == "browser":
            try:
                opps = self.discover_via_browser(limit=limit, opportunity_type=opportunity_type)
                if opps:
                    return opps
            except Exception as exc:
                logger.warning("Browser discovery failed, falling back to API: %s", exc)

        return self.discover_via_api(limit=limit, opportunity_type=opportunity_type)

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

        session_stat = self.check_session_status(probe_network=False)
        if not session_stat.valid:
            meta: dict[str, Any] = {
                "session_valid": False,
                "session_status": str(session_stat.status),
                "session_detail": session_stat.detail,
            }
            if session_stat.status == MISSING:
                meta["session_missing"] = True
            elif session_stat.status == EXPIRED:
                meta["session_expired"] = True
            elif session_stat.status == INVALID:
                meta["session_invalid"] = True
            elif session_stat.status == CHECK_FAILED:
                meta["session_check_failed"] = True

            logger.warning("Cannot open Unstop application: %s", session_stat.detail)
            return ApplicationContext(
                opportunity_id=opportunity_id,
                listing_url=url,
                adapter_name=self.adapter_name,
                tier=self.tier,
                extracted=extracted,
                metadata=meta,
            )

        try:
            from playwright.sync_api import sync_playwright

            p = self._pw or sync_playwright().start()
            storage_state = load_decrypted_storage_state(self.session_file, key=self.encryption_key)

            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(
                storage_state=storage_state,
                permissions=["geolocation"],
                geolocation={"latitude": 19.0760, "longitude": 72.8777},
            )
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
                    metadata={"session_expired": True, "session_status": str(EXPIRED)},
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

    SUBMISSION_SELECTORS = [
        "#unstop_submit",
        "button:has-text('Complete Registration')",
        "button:has-text('Submit Application')",
        "button:has-text('Submit')",
        "input[type='submit']",
        ".submit-btn",
    ]

    @staticmethod
    def _match_option_by_text(options_locator: Any, expected: str) -> Any:
        """Return the first visible option whose text matches ``expected`` exactly.

        U9 dropdown safety: never selects an arbitrary option.  Returns None when
        no option matches the candidate value, leaving the field unresolved.
        """
        if options_locator is None or not callable(getattr(options_locator, "count", None)):
            return None
        try:
            count = options_locator.count()
        except Exception:
            return None
        if not isinstance(count, int) or count == 0:
            return None
        expected_norm = str(expected).strip().lower()
        for i in range(count):
            try:
                opt = options_locator.nth(i)
                text = (opt.text_content() or "").strip().lower()
            except Exception:
                continue
            if text == expected_norm:
                try:
                    if opt.is_visible():
                        return opt
                except Exception:
                    continue
        return None

    @staticmethod
    def _detect_field_role(
        name: str | None,
        el_id: str | None,
        label: str,
        placeholder: str = "",
        aria_label: str = "",
        el_type: str = "",
        tag: str = "",
    ) -> str:
        """Detect the semantic role of an extracted form field.

        Enforces a deterministic two-tier resolution strategy:
        1. Tier 1 (Explicit Control Identifiers): Developer-assigned names, IDs,
           and formcontrolnames in the DOM carry highest precedence.
        2. Tier 2 (Semantic Label & Text Context): Evaluated only when no explicit
           control identifier resolves the field. Strictly distinguishes domain
           ('course_pursuing') from degree/course ('course_stream'), duration,
           and specialization.
        """
        norm_name = (name or "").lower().strip()
        norm_id = (el_id or "").lower().strip()
        norm_lbl = (label or "").lower().strip()
        norm_placeholder = (placeholder or "").lower().strip()
        norm_aria = (aria_label or "").lower().strip()

        # =====================================================================
        # TIER 1: Explicit Control Identifiers (Highest Precedence)
        # =====================================================================
        # Contact and Identity Identifiers
        if "firstname" in norm_name or "first_name" in norm_name or "first_name" in norm_id:
            return "first_name"
        if "lastname" in norm_name or "name_last" in norm_name or "last_name" in norm_name or "last_name" in norm_id:
            return "last_name"
        if "full_name" in norm_name or "full_name" in norm_id or (
            "name" in norm_name and not any(x in norm_name for x in ("org", "user", "last", "first", "course", "degree", "stream", "file"))
        ):
            return "full_name"
        if "email" in norm_name or "email" in norm_id or el_type == "email":
            return "email"
        if any(x in norm_name or x in norm_id for x in ("tel", "mobile", "phone")) or el_type == "tel":
            return "phone"
        if "gender" in norm_name or "gender" in norm_id:
            return "gender"
        if any(x in norm_name or x in norm_id for x in ("differently_abled", "disability", "handicap")):
            return "differently_abled"
        if "user_type" in norm_name or ("type" in norm_name and tag == "un-radio-group"):
            return "user_type"
        if "cities_input" in norm_id or "player_location" in norm_name or "location" in norm_name:
            return "location"
        if "skills" in norm_id or "skills" in norm_name:
            return "skills"
        if any(x in norm_name or x in norm_id for x in ("organization", "organisation", "institute", "college")):
            return "organization"
        if "designation" in norm_name or "designation" in norm_id:
            return "designation"
        if "work_experience" in norm_name or "experience" in norm_name:
            return "work_experience"
        if any(x in norm_name or x in norm_id for x in ("acceptance", "agree_terms")):
            return "acceptance"
        if el_type == "file" or any(x in norm_name for x in ("resume", "cv")):
            return "resume"
        if any(x in norm_name for x in ("statement", "sop", "why_apply")):
            return "statement_of_purpose"

        # Education Control Identifiers (Unambiguous DOM Tokens)
        if "course_duration" in norm_name or "course_duration" in norm_id or norm_name == "duration" or norm_id == "duration":
            return "course_duration"
        if "course_specialization" in norm_name or "course_specialization" in norm_id or any(x in norm_name or x in norm_id for x in ("specialization", "branch")):
            return "course_specialization"
        if "course_stream" in norm_name or "course_stream" in norm_id or any(x in norm_name or x in norm_id for x in ("stream", "degree", "course_name")):
            return "course_stream"
        if "course_pursuing" in norm_name or "course_pursuing" in norm_id or any(x in norm_name or x in norm_id for x in ("domain", "education_domain")):
            return "course_pursuing"
        if any(x in norm_name or x in norm_id for x in ("passout_year", "graduation_year", "passing_year", "batch")):
            return "graduation_year"
        if any(x in norm_name or x in norm_id for x in ("programme", "program")):
            return "programme"

        # =====================================================================
        # TIER 2: Semantic Label & Text Context (Fallback when no identifier matched)
        # =====================================================================
        # Education Fields by Semantic Label
        if "duration" in norm_lbl:
            return "course_duration"

        if any(x in norm_lbl for x in ("specialization", "branch", "major")):
            return "course_specialization"

        # Domain: represents high-level domain/discipline (Engineering, Management, etc.)
        # Never match bare "course" here — "Course*" on Unstop denotes degree/stream.
        if any(x in norm_lbl for x in ("domain", "course pursuing", "pursuing course", "discipline")):
            return "course_pursuing"

        # Degree / Stream: represents degree (B.Tech, MBA, etc.)
        # Matches "stream", "degree", or "course" when not duration/specialization/pursuing.
        if any(x in norm_lbl for x in ("stream", "degree")) or (
            "course" in norm_lbl
            and not any(x in norm_lbl for x in ("duration", "specialization", "pursuing", "domain", "discipline"))
        ):
            return "course_stream"

        if any(x in norm_lbl for x in ("passout", "graduation year", "passing year", "year of graduation", "batch")):
            return "graduation_year"

        if "programme" in norm_lbl or "program" in norm_lbl:
            return "programme"

        # Other fields by Semantic Label
        if "first name" in norm_lbl:
            return "first_name"
        if "last name" in norm_lbl:
            return "last_name"
        if "email" in norm_lbl:
            return "email"
        if "mobile" in norm_lbl or "phone" in norm_lbl:
            return "phone"
        if "location" in norm_lbl or "city" in norm_lbl:
            return "location"
        if "skill" in norm_lbl or "skill" in norm_placeholder or "skills" in norm_id:
            return "skills"
        if any(x in norm_lbl for x in ("institute", "college", "organisation", "organization")):
            return "organization"
        if "gender" in norm_lbl:
            return "gender"
        if "differently abled" in norm_lbl or "disability" in norm_lbl:
            return "differently_abled"
        if "user type" in norm_lbl:
            return "user_type"
        if "designation" in norm_lbl:
            return "designation"
        if "experience" in norm_lbl:
            return "work_experience"
        if "terms" in norm_lbl or "agree" in norm_lbl:
            return "acceptance"
        if "resume" in norm_lbl or "cv" in norm_lbl:
            return "resume"
        if any(x in norm_lbl for x in ("statement", "why", "sop")):
            return "statement_of_purpose"

        return "custom"

    def extract_form_fields(self, page: Any) -> list[FormField]:
        """Extract interactive form fields from the current Unstop page/modal."""
        fields: list[FormField] = []
        if page is None:
            return fields

        try:
            selector = (
                "input:not([type='hidden']), textarea, select, "
                "un-radio-group, mat-select, un-checkbox"
            )
            elements = page.locator(selector).all()
            for el in elements:
                try:
                    if not el.is_visible():
                        continue
                    tag = el.evaluate("e => e.tagName").lower()
                    name = (
                        el.get_attribute("name")
                        or el.get_attribute("formcontrolname")
                        or el.get_attribute("ng-reflect-name")
                    )
                    el_id = el.get_attribute("id") or el.get_attribute("data-test") or ""
                    placeholder = el.get_attribute("placeholder") or ""
                    aria_label = el.get_attribute("aria-label") or ""

                    if tag == "input":
                        el_type = (el.get_attribute("type") or "text").lower()
                    elif tag == "un-radio-group":
                        el_type = "radio_group"
                    elif tag == "mat-select":
                        el_type = "select"
                    elif tag == "un-checkbox":
                        el_type = "checkbox"
                    else:
                        el_type = tag

                    # Extract options if radio group or select
                    options: list[str] = []
                    if tag == "un-radio-group":
                        raw_opts = el.locator("label, un-radio").all()
                        for o in raw_opts:
                            txt = o.inner_text().strip()
                            if txt and txt not in options:
                                options.append(txt)
                    elif tag == "select":
                        raw_opts = el.locator("option").all()
                        for o in raw_opts:
                            txt = o.inner_text().strip()
                            if txt and txt not in options:
                                options.append(txt)

                    # Extract label semantically
                    label = ""
                    if el_id:
                        lbl_el = page.locator(f"label[for='{el_id}']").first
                        if lbl_el.count() > 0:
                            label = lbl_el.inner_text().strip()
                    if not label and tag == "un-checkbox":
                        chk_lbl = el.locator("label").first
                        if chk_lbl.count() > 0:
                            label = chk_lbl.inner_text().strip()
                    if not label:
                        parent_lbl = el.locator("xpath=ancestor::label").first
                        if parent_lbl.count() > 0:
                            label = parent_lbl.inner_text().strip()
                    if not label:
                        prec_lbl = el.locator("xpath=preceding::label[1]").first
                        if prec_lbl.count() > 0:
                            label = prec_lbl.inner_text().strip()
                    if not label:
                        label = aria_label or placeholder or name or el_id

                    # Check required
                    is_req = False
                    if el.get_attribute("required") is not None:
                        is_req = True
                    elif el.get_attribute("aria-required") == "true":
                        is_req = True
                    elif "*" in label:
                        is_req = True
                    elif tag == "un-checkbox" and ("acceptance" in (name or "") or "acceptance" in el_id):
                        is_req = True

                    # Detect semantic field role deterministically
                    field_role = self._detect_field_role(
                        name=name,
                        el_id=el_id,
                        label=label,
                        placeholder=placeholder,
                        aria_label=aria_label,
                        el_type=el_type,
                        tag=tag,
                    )

                    fields.append(
                        FormField(
                            id=el_id,
                            name=name,
                            tag=tag,
                            type=el_type,
                            label=label,
                            required=is_req,
                            placeholder=placeholder,
                            options=options,
                            field_role=field_role,
                        )
                    )
                except Exception as exc:
                    logger.debug("Failed to extract field details: %s", exc)
        except Exception as exc:
            logger.warning("Error scanning form fields: %s", exc)

        return fields

    def classify_field(
        self,
        field_obj: FormField,
        candidate_data: dict[str, Any],
        custom_answers: list[dict[str, Any]] | None = None,
        manual_resolutions: dict[str, str] | None = None,
    ) -> tuple[QuestionClassification, Any]:
        """Classify field into PROFILE_FACT, SAFE_INFERENCE, REQUIRES_USER, or UNSUPPORTED."""
        role = field_obj.field_role
        answers_map = {
            str(a.get("question_id") or a.get("question_text") or a.get("label")): a.get("answer", "")
            for a in (custom_answers or [])
        }

        # U11: Manual resolutions take highest precedence for this specific application attempt.
        if manual_resolutions and field_obj.name in manual_resolutions:
            return QuestionClassification.PROFILE_FACT, manual_resolutions[field_obj.name]

        # SENSITIVE DEMOGRAPHIC & CONSENT FIELDS:
        # Rule #5: NEVER invent demographic data (gender, disability), experience, preferences, or consent.
        if role == "gender":
            val = candidate_data.get("gender") or answers_map.get("gender") or answers_map.get("user_gender")
            if val:
                return QuestionClassification.PROFILE_FACT, val
            return QuestionClassification.REQUIRES_USER, None

        if role == "differently_abled":
            val = (
                candidate_data.get("differently_abled")
                or candidate_data.get("disability")
                or answers_map.get("differently_abled")
                or answers_map.get("user_differently_abled")
            )
            if val:
                return QuestionClassification.PROFILE_FACT, val
            return QuestionClassification.REQUIRES_USER, None

        if role == "acceptance":
            # U9: consent is application-scoped.  Only an explicit answer tied to
            # THIS application can resolve it — never the candidate profile,
            # never the mere existence of the checkbox, never a default.
            explicit = (
                answers_map.get("acceptance")
                or answers_map.get("agree_terms")
                or answers_map.get("consent")
                or answers_map.get("terms")
            )
            if isinstance(explicit, bool):
                return (QuestionClassification.CONSENT, True) if explicit else (QuestionClassification.CONSENT, None)
            if isinstance(explicit, str) and explicit.strip().lower() in _EXPLICIT_CONSENT_ANSWERS:
                return QuestionClassification.CONSENT, True
            # Unresolved consent is returned as CONSENT with no value: the fill
            # loop must not check the terms box, and a required consent field
            # fails closed to human review.
            return QuestionClassification.CONSENT, None

        if role == "user_type":
            val = candidate_data.get("user_type") or answers_map.get("user_type")
            if val:
                return QuestionClassification.PROFILE_FACT, val
            return (QuestionClassification.REQUIRES_USER, None) if field_obj.required else (QuestionClassification.SAFE_INFERENCE, None)

        if role in {"course_pursuing", "course_stream", "course_specialization", "graduation_year", "course_duration", "designation", "work_experience", "programme"}:
            val = candidate_data.get(role) or answers_map.get(role)
            if not val and candidate_data.get("education"):
                edus = candidate_data["education"]
                if role == "course_pursuing":
                    val = edus[0].get("domain")
                elif role == "course_stream":
                    val = edus[0].get("degree") or edus[0].get("stream")
                elif role == "course_specialization":
                    val = edus[0].get("specialization") or edus[0].get("branch")
                elif role == "graduation_year":
                    val = str(edus[0].get("graduation_year")) if edus[0].get("graduation_year") else None
                elif role == "course_duration":
                    val = edus[0].get("duration")
                elif role == "programme":
                    val = edus[0].get("programme")
            if val:
                return QuestionClassification.PROFILE_FACT, val
            return (QuestionClassification.REQUIRES_USER, None) if field_obj.required else (QuestionClassification.SAFE_INFERENCE, None)

        # 1. Profile facts
        if role == "first_name":
            name = candidate_data.get("full_name") or candidate_data.get("name") or ""
            parts = name.split()
            val = parts[0] if parts else None
            if val:
                return QuestionClassification.PROFILE_FACT, val
            return (QuestionClassification.REQUIRES_USER, None) if field_obj.required else (QuestionClassification.PROFILE_FACT, None)

        if role == "last_name":
            name = candidate_data.get("full_name") or candidate_data.get("name") or ""
            parts = name.split()
            val = " ".join(parts[1:]) if len(parts) > 1 else None
            return QuestionClassification.PROFILE_FACT, val

        if role == "full_name":
            val = candidate_data.get("full_name") or candidate_data.get("name")
            if val:
                return QuestionClassification.PROFILE_FACT, val
            return (QuestionClassification.REQUIRES_USER, None) if field_obj.required else (QuestionClassification.PROFILE_FACT, None)

        if role == "email":
            val = candidate_data.get("email")
            if val:
                return QuestionClassification.PROFILE_FACT, val
            return (QuestionClassification.REQUIRES_USER, None) if field_obj.required else (QuestionClassification.PROFILE_FACT, None)

        if role == "phone":
            val = candidate_data.get("phone")
            if val:
                # Clean phone number (strip +91 prefix if country selector is separate)
                clean_phone = val.replace("+91", "").replace("-", "").replace(" ", "").strip()
                return QuestionClassification.PROFILE_FACT, clean_phone
            return (QuestionClassification.REQUIRES_USER, None) if field_obj.required else (QuestionClassification.PROFILE_FACT, None)

        if role == "location":
            val = candidate_data.get("location")
            if val:
                return QuestionClassification.PROFILE_FACT, val
            return (QuestionClassification.REQUIRES_USER, None) if field_obj.required else (QuestionClassification.PROFILE_FACT, None)

        if role == "organization":
            edus = candidate_data.get("education", [])
            val = (
                candidate_data.get("organization")
                or (edus[0].get("institution") if edus else None)
                or answers_map.get("organization")
                or answers_map.get("institution")
            )
            if val:
                return QuestionClassification.PROFILE_FACT, val
            return (QuestionClassification.REQUIRES_USER, None) if field_obj.required else (QuestionClassification.PROFILE_FACT, None)

        if role == "skills":
            skills = candidate_data.get("skills", [])
            val = [
                s.get("skill_name") if isinstance(s, dict) else str(s)
                for s in skills
                if (isinstance(s, dict) and s.get("skill_name")) or isinstance(s, str)
            ]
            if not val and (answers_map.get("skills") or answers_map.get("skill")):
                raw_sk = answers_map.get("skills") or answers_map.get("skill")
                val = raw_sk if isinstance(raw_sk, list) else [str(raw_sk)]
            if val and field_obj.options:
                options_lower = {opt.lower(): opt for opt in field_obj.options}
                val = [options_lower[v.lower()] for v in val if v.lower() in options_lower]
            if val:
                return QuestionClassification.PROFILE_FACT, val
            return (QuestionClassification.REQUIRES_USER, None) if field_obj.required else (QuestionClassification.PROFILE_FACT, None)

        if role == "resume":
            return QuestionClassification.PROFILE_FACT, "RESUME_ATTACHMENT"

        # 2. Statement of purpose / cover question
        if role == "statement_of_purpose":
            sop_text = (
                answers_map.get("statement_of_purpose")
                or answers_map.get("Why do you want to apply for this opportunity?")
                or (custom_answers[0].get("answer") if custom_answers else None)
            )
            if sop_text:
                return QuestionClassification.SAFE_INFERENCE, sop_text
            if field_obj.required:
                return QuestionClassification.REQUIRES_USER, None
            return QuestionClassification.SAFE_INFERENCE, None

        # 3. Custom questions
        for key, ans in answers_map.items():
            if key.lower() in field_obj.label.lower() or (field_obj.name and key.lower() in field_obj.name.lower()):
                return QuestionClassification.SAFE_INFERENCE, ans

        # Check for sensitive / personal decision questions
        sensitive_keywords = ["salary", "ctc", "compensation", "criminal", "authorization", "visa", "gender", "race", "disability", "notice period"]
        if any(k in field_obj.label.lower() for k in sensitive_keywords):
            return QuestionClassification.REQUIRES_USER, None

        if field_obj.required:
            return QuestionClassification.REQUIRES_USER, None

        return QuestionClassification.SAFE_INFERENCE, None

    def fill(
        self,
        app_ctx: ApplicationContext,
        candidate_data: dict[str, Any],
        resume_path: str | None = None,
        custom_answers: list[dict[str, Any]] | None = None,
        manual_resolutions: dict[str, str] | None = None,
    ) -> FillResult:
        """Fill form elements on Unstop and leave browser open at unsubmitted state.

        Strict pre-submit boundary: NEVER clicks final submission controls.
        """
        if app_ctx.metadata.get("session_missing"):
            return FillResult(
                success=False,
                status="manual_required",
                error_reason=app_ctx.metadata.get("session_detail") or "Unstop session state missing. Run python -m worker.setup_session --platform unstop",
            )
        if app_ctx.metadata.get("session_expired"):
            return FillResult(
                success=False,
                status="manual_required",
                error_reason=app_ctx.metadata.get("session_detail") or "Unstop session expired. Please re-authenticate.",
            )
        if app_ctx.metadata.get("session_invalid"):
            return FillResult(
                success=False,
                status="manual_required",
                error_reason=app_ctx.metadata.get("session_detail") or SESSION_REMEDIATION_GUIDE["INVALID"],
            )
        if app_ctx.metadata.get("session_check_failed"):
            return FillResult(
                success=False,
                status="manual_required",
                error_reason=app_ctx.metadata.get("session_detail") or SESSION_REMEDIATION_GUIDE["CHECK_FAILED"],
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

            # Dismiss / remove cookie consent banner so clicks are never intercepted
            try:
                cookie_btn = page.locator("button.GTM_ACCEPT_COOKIE, button:has-text('Accept')").first
                if cookie_btn.count() > 0 and cookie_btn.is_visible():
                    cookie_btn.click()
                    page.wait_for_timeout(300)
                page.evaluate("() => document.querySelectorAll('app-notification').forEach(e => e.remove())")
            except Exception as c_exc:
                logger.debug("Cookie banner handling notice: %s", c_exc)
            # Ensure dynamic form elements have rendered
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            try:
                page.wait_for_selector("input, un-radio-group, form, #un-register-btn", timeout=10000)
                page.wait_for_timeout(1000)
            except Exception:
                pass

            # Check for Quick Apply / Register button on opportunity overview page
            try:
                page.wait_for_selector("#un-register-btn, button:has-text('Quick Apply'), button:has-text('Apply Now'), button:has-text('Register')", timeout=10000, state="attached")
                page.wait_for_timeout(2000)
            except Exception:
                pass

            apply_btn = page.locator(
                "#un-register-btn, button:has-text('Quick Apply'), div:has-text('Quick Apply'), "
                "button:has-text('Apply Now'), button:has-text('Register')"
            )
            if apply_btn.count() > 0 and apply_btn.first.is_visible():
                try:
                    apply_btn.first.click()
                    page.wait_for_timeout(3500)
                    page.wait_for_selector("un-radio-group, input, mat-select", timeout=5000)
                except Exception as exc:
                    logger.debug("Apply button click notice: %s", exc)

            # Settle root conditional control (user_type) first if present in candidate profile
            user_type_val = candidate_data.get("user_type") or next(
                (a.get("answer") for a in (custom_answers or []) if "user_type" in str(a.get("question_id") or a.get("label") or "")),
                None,
            )
            if user_type_val:
                ut_radio = page.locator("un-radio-group[name='user_type'] label, un-radio-group#user_type label").filter(has_text=str(user_type_val)).first
                if ut_radio.count() > 0 and ut_radio.is_visible():
                    ut_radio.click()
                    page.wait_for_timeout(1000)

            # Extract form fields from live page
            page.wait_for_timeout(4000)
            try:
                page.wait_for_selector("un-radio-group, input, mat-select", timeout=15000, state="attached")
            except Exception as e:
                logger.warning("wait_for_selector timed out waiting for form controls: %s", e)
            extracted_fields = self.extract_form_fields(page)

            # Classify all extracted fields
            classified_fields: list[tuple[FormField, QuestionClassification, Any]] = []
            for f in extracted_fields:
                classification, val = self.classify_field(f, candidate_data, custom_answers, manual_resolutions)
                f.classification = classification
                classified_fields.append((f, classification, val))
                logger.debug(
                    "unstop field classified: name=%s role=%s required=%s class=%s",
                    f.name, f.field_role, f.required, classification.value,
                )

            # We do NOT early exit here anymore, it's done inside _fill_all_fields

            def _fill_all_fields(fields_list: list[tuple[FormField, QuestionClassification, Any]]) -> FillResult | None:
                """Fill classified fields.

                Returns FillResult if a field fails closed, else None (success).
                """
                for f, classification, val in fields_list:
                    # U9/U11: Check for unresolved required fields here so we process fields in DOM order.
                    if f.required and classification in {
                        QuestionClassification.REQUIRES_USER,
                        QuestionClassification.UNSUPPORTED,
                        QuestionClassification.CONSENT,
                    }:
                        if val is None:
                            logger.warning(
                                "Stopping fill: required field %s is unresolved (%s).",
                                f.name, classification.value,
                            )
                            return FillResult(
                                success=False,
                                status="manual_required",
                                error_reason=f"Required field '{f.label or f.name}' requires user decision ({classification.value}). Cannot auto-fill.",
                            )

                    if val is None:
                        continue

                    def _field_locator(tag_name: str, suffix: str = ""):
                        parts = []
                        if f.id:
                            parts.append(f"{tag_name}#{f.id}{suffix}")
                        if f.name:
                            parts.append(f"{tag_name}[name='{f.name}']{suffix}")
                        sel_str = ", ".join(parts) if parts else f"{tag_name}{suffix}"
                        return page.locator(sel_str).first

                    if f.field_role == "first_name":
                        loc = _field_locator("input")
                        if loc.count() > 0 and loc.is_editable():
                            loc.fill(str(val))

                    elif f.field_role == "last_name":
                        loc = _field_locator("input")
                        if loc.count() > 0 and loc.is_editable():
                            loc.fill(str(val))

                    elif f.field_role == "full_name":
                        loc = _field_locator("input")
                        if loc.count() > 0 and loc.is_editable():
                            loc.fill(str(val))

                    elif f.field_role == "email":
                        loc = _field_locator("input")
                        if loc.count() > 0 and loc.is_editable():
                            loc.fill(str(val))

                    elif f.field_role == "phone":
                        loc = _field_locator("input")
                        if loc.count() > 0 and loc.is_editable():
                            loc.fill(str(val))

                    elif f.field_role == "location":
                        loc = _field_locator("input")
                        if loc.count() > 0:
                            geo_btn = page.locator("un-icon.geo_location").first
                            if geo_btn.count() > 0 and geo_btn.is_visible():
                                geo_btn.click()
                                page.wait_for_timeout(1000)

                            if not loc.input_value():
                                page.evaluate("""(locationVal) => {
                                    const el = document.getElementById('cities_input') || document.querySelector('input[name="player_location"]');
                                    if (el) {
                                        el.removeAttribute('readonly');
                                        el.value = locationVal;
                                        el.dispatchEvent(new Event('input', {bubbles: true}));
                                        el.dispatchEvent(new Event('change', {bubbles: true}));
                                    }
                                }""", str(val))
                                page.wait_for_timeout(300)

                    elif f.field_role == "organization":
                        org_input = page.locator("input[id*='organisation'], app-autocomplete input").first
                        if org_input.count() > 0 and org_input.is_visible():
                            org_input.click()
                            org_input.type(str(val), delay=30)
                            page.wait_for_timeout(1000)
                            # U9: only an autocomplete suggestion that matches the
                            # typed candidate value may be selected.  An arbitrary
                            # first suggestion is never clicked.
                            org_opt = self._match_option_by_text(
                                page.locator(".autocomplete-content li, div[class*='option']"),
                                str(val),
                            )
                            if org_opt is not None:
                                org_opt.click()
                                page.wait_for_timeout(300)
                            else:
                                page.keyboard.press("Escape")
                                page.wait_for_timeout(300)
                                logger.warning(
                                    "No autocomplete suggestion matches the candidate "
                                    "value for %s; leaving unresolved.", f.name,
                                )
                                if f.required:
                                    return FillResult(
                                        success=False,
                                        status="manual_required",
                                        error_reason=(
                                            f"Field '{f.label or f.name}' has no matching "
                                            "autocomplete option for the candidate value. "
                                            "Cannot auto-fill."
                                        ),
                                    )

                    elif f.field_role == "skills":
                        skills_input = page.locator("input[placeholder*='skills'], input[id*='skills'], app-autocomplete input").last
                        if skills_input.count() > 0 and skills_input.is_visible():
                            skill_list = val if isinstance(val, list) else [str(val)]
                            for sk in skill_list:
                                skills_input.click()
                                skills_input.type(str(sk), delay=30)
                                page.wait_for_timeout(1000)
                                # U9: select only a suggestion matching the typed
                                # candidate skill; never the first option.
                                s_opt = self._match_option_by_text(
                                    page.locator(".autocomplete-content li"),
                                    str(sk),
                                )
                                if s_opt is not None:
                                    s_opt.click()
                                    page.wait_for_timeout(300)
                                else:
                                    page.keyboard.press("Escape")
                                    page.wait_for_timeout(300)
                                    logger.warning(
                                        "No autocomplete suggestion matches the candidate "
                                        "skill value for %s; leaving unresolved.", f.name,
                                    )
                                    if f.required:
                                        return FillResult(
                                            success=False,
                                            status="manual_required",
                                            error_reason=(
                                                f"Field '{f.label or f.name}' has no matching "
                                                "autocomplete option for the candidate value. "
                                                "Cannot auto-fill."
                                            ),
                                        )

                    elif f.tag == "un-radio-group":
                        rg_parts = []
                        if f.id:
                            rg_parts.append(f"un-radio-group[id='{f.id}' i] label")
                        if f.name:
                            rg_parts.append(f"un-radio-group[name='{f.name}' i] label")
                            rg_parts.append(f"un-radio-group[formcontrolname='{f.name}' i] label")
                            rg_parts.append(f"un-radio-group[ng-reflect-name='{f.name}' i] label")
                        rg_sel = ", ".join(rg_parts) if rg_parts else "un-radio-group label"
                        rg_lbl = page.locator(rg_sel).filter(has_text=str(val)).first
                        if rg_lbl.count() == 0 and f.name:
                            rg_lbl = page.locator(f"un-radio-group[name='{f.name}' i] label:has-text('{val}')").first
                        if rg_lbl.count() == 0 and f.name:
                            # Fallback: match a label inside the group by exact
                            # normalized text. A label that merely contains the
                            # candidate value is not an evidence match.
                            labels = page.locator(f"un-radio-group[name='{f.name}' i] un-radio label, un-radio-group[formcontrolname='{f.name}' i] un-radio label, un-radio-group[ng-reflect-name='{f.name}' i] un-radio label").all()
                            for lbl in labels:
                                text = (lbl.text_content() or "").strip().lower()
                                if text == str(val).strip().lower():
                                    rg_lbl = lbl
                                    break

                        if rg_lbl.count() > 0 and rg_lbl.is_visible():
                            rg_lbl.click(force=True)
                            logger.debug("Selected radio option for %s", f.name)
                        else:
                            # U9 RADIO SAFETY: no option matches the candidate
                            # value exactly, so nothing is selected on the
                            # candidate's behalf.  An arbitrary option is never
                            # chosen; a required field fails closed.
                            logger.warning(
                                "No radio option matches the candidate value for %s; "
                                "left unresolved instead of selecting an arbitrary option.",
                                f.name,
                            )
                            if f.required:
                                available_options = []
                                # Try to get all labels in the radio group
                                for lbl in page.locator(f"un-radio-group[name='{f.name}' i] un-radio label, un-radio-group[formcontrolname='{f.name}' i] un-radio label, un-radio-group[ng-reflect-name='{f.name}' i] un-radio label").all():
                                    txt = (lbl.text_content() or "").strip()
                                    if txt and txt not in available_options:
                                        available_options.append(txt)

                                return FillResult(
                                    success=False,
                                    status="manual_required",
                                    resolution_request={
                                        "field_name": f.name,
                                        "field_label": f.label,
                                        "field_role": f.field_role,
                                        "candidate_value": str(val),
                                        "available_options": available_options,
                                        "reason": "NO_EXACT_MATCH"
                                    },
                                    error_reason=(
                                        f"Field '{f.label or f.name}' has no radio option "
                                        "matching the candidate value. Cannot auto-fill."
                                    ),
                                )

                    elif f.tag == "mat-select":
                        sel = _field_locator("mat-select")
                        if sel.count() > 0 and sel.is_visible():
                            # U10.6: If the currently selected value exactly matches the
                            # candidate's value, we consider it successfully resolved.
                            # We bypass opening the dropdown completely. This safely avoids
                            # trying to click a hidden already-selected option while preserving
                            # fail-closed behavior for mismatches.
                            current_val = (sel.text_content() or "").strip().lower()
                            expected_norm = str(val).strip().lower()
                            if current_val == expected_norm:
                                logger.debug("mat-select for %s already has the correct value '%s'", f.name, current_val)
                                continue

                            sel.click(force=True)
                            page.wait_for_timeout(500)
                            # U9/U10 DROPDOWN SAFETY: select only the option that
                            # matches the candidate value exactly (with case and
                            # whitespace normalization). There is NO fallback
                            # to the first option -- a value with no matching
                            # option is unresolved and fails closed when the
                            # field is required.
                            opt = self._match_option_by_text(
                                page.locator("mat-option, .mat-mdc-option"), str(val)
                            )
                            if opt is not None and opt.is_visible():
                                opt.click(force=True)
                                page.wait_for_timeout(300)
                                logger.debug("Selected matching mat-option for %s", f.name)
                            else:
                                page.keyboard.press("Escape")
                                page.wait_for_timeout(300)
                                logger.warning(
                                    "No mat-option matches the candidate value for %s; "
                                    "left unresolved instead of selecting an arbitrary option.",
                                    f.name,
                                )
                                if f.required:
                                    available_options = []
                                    for opt_el in page.locator("mat-option, .mat-mdc-option").all():
                                        txt = (opt_el.text_content() or "").strip()
                                        if txt and txt not in available_options:
                                            available_options.append(txt)

                                    return FillResult(
                                        success=False,
                                        status="manual_required",
                                        resolution_request={
                                            "field_name": f.name,
                                            "field_label": f.label,
                                            "field_role": f.field_role,
                                            "candidate_value": str(val),
                                            "available_options": available_options,
                                            "reason": "NO_EXACT_MATCH"
                                        },
                                        error_reason=(
                                            f"Field '{f.label or f.name}' has no dropdown option "
                                            "matching the candidate value. Cannot auto-fill."
                                        ),
                                    )

                    elif f.tag == "un-checkbox":
                        # U9: a checkbox is only ever checked on an explicit
                        # affirmative value resolved for THIS application.  An
                        # unresolved consent checkbox (CONSENT with no value)
                        # never reaches here because val is None and the loop
                        # skips it — the box is left untouched.
                        if bool(val):
                            chk_parts = []
                            if f.id:
                                chk_parts.append(f"un-checkbox[id='{f.id}' i] label")
                            if f.name:
                                chk_parts.append(f"un-checkbox[name='{f.name}' i] label")
                                chk_parts.append(f"un-checkbox[formcontrolname='{f.name}' i] label")
                                chk_parts.append(f"un-checkbox[ng-reflect-name='{f.name}' i] label")
                            chk_sel = ", ".join(chk_parts) if chk_parts else "un-checkbox label"
                            chk = page.locator(chk_sel).first
                            if chk.count() > 0 and chk.is_visible():
                                chk_input = page.locator(f"input[type='checkbox'][name='{f.name}' i], input[type='checkbox'][formcontrolname='{f.name}' i]").first
                                if chk_input.count() > 0 and not chk_input.is_checked():
                                    chk.click(force=True)
                                    logger.debug("Checked %s per explicit consent", f.name)
                                elif chk_input.count() == 0:
                                    # Angular custom component (<un-checkbox>) wraps
                                    # no native input, so its label is the only
                                    # interaction surface.  The click here is
                                    # authorized by the explicit consent value
                                    # resolved for this application — never by the
                                    # mere existence of the checkbox.
                                    chk.click(force=True)
                                    logger.debug(
                                        "Checked custom component %s per explicit consent",
                                        f.name,
                                    )
                            else:
                                logger.warning("Failed to find a visible checkbox for %s", f.name)

                    elif f.field_role == "statement_of_purpose":
                        formatted_text = str(val)
                        if "[AI DRAFT" not in formatted_text:
                            formatted_text = f"[AI DRAFT - PENDING APPROVAL]\n\n{formatted_text}"
                        textarea = page.locator("textarea[name*='statement'], textarea[placeholder*='Why'], textarea").first
                        if textarea.count() > 0 and textarea.is_editable():
                            textarea.fill(formatted_text)
                        processed_answers.append({
                            "question_id": f.id or "statement_of_purpose",
                            "label": f.label or "Why do you want to apply for this opportunity?",
                            "answer": formatted_text,
                            "is_ai_draft": True,
                        })

                    elif classification == QuestionClassification.SAFE_INFERENCE and f.tag == "textarea":
                        formatted_text = str(val)
                        if "[AI DRAFT" not in formatted_text:
                            formatted_text = f"[AI DRAFT - PENDING APPROVAL]\n\n{formatted_text}"
                        loc = _field_locator("textarea")
                        if loc.count() > 0 and loc.is_editable():
                            loc.fill(formatted_text)
                        processed_answers.append({
                            "question_id": f.id or f.name,
                            "label": f.label,
                            "answer": formatted_text,
                            "is_ai_draft": True,
                        })

            fill_field_result = _fill_all_fields(classified_fields)
            if fill_field_result is not None:
                return fill_field_result

            # Resume upload if present
            if resume_path and Path(resume_path).exists():
                file_input = page.locator("input[type='file']")
                if file_input.count() > 0:
                    file_input.first.set_input_files(resume_path)

            MAX_STEPS = 10
            reached_terminal_boundary = False
            for step_idx in range(MAX_STEPS):
                next_btn = page.locator(
                    "button:not(#unstop_submit):has-text('Next'), "
                    "button:not(#unstop_submit):has-text('Save & Continue'), "
                    "button:not(#unstop_submit):has-text('Save and Continue')"
                ).first
                final_btn = page.locator(
                    "button#unstop_submit, "
                    "button:has-text('Complete Registration'), "
                    "button:has-text('Submit Application'), "
                    "button:has-text('Submit'), "
                    "button:has-text('Complete')"
                ).first

                if final_btn.count() > 0 and final_btn.is_visible():
                    logger.info("Final submit control visible — stopping at review page (step %d).", step_idx + 1)
                    break

                if next_btn.count() == 0 or not next_btn.is_visible():
                    logger.info("No navigation control visible — assuming final page (step %d).", step_idx + 1)
                    break

                # U9 PROGRESSIVE-FORM SAFETY — the highest-priority invariant.
                #
                # A progressive Unstop form labels its terminal action "Next", so
                # a naive fill loop submits the application by clicking it.  Before
                # clicking ANY navigation control the fill phase must decide, from
                # deterministic stepper state, whether the current step is the
                # terminal one.  A terminal navigation control is NEVER clicked by
                # fill(); it is handed to the submission state machine, which owns
                # the single final click.
                step_context = self.detect_terminal_step_context(page)
                if step_context.is_terminal:
                    reached_terminal_boundary = True
                    terminal_evidence = step_context.evidence
                    logger.info(
                        "Terminal step reached (step %s of %s, evidence=%s) — halting "
                        "before the terminal navigation control; the submission state "
                        "machine owns the final action.",
                        step_context.current_step,
                        step_context.total_steps,
                        step_context.evidence,
                    )
                    break

                logger.info("Advancing progressive form (step %d)...", step_idx + 1)
                next_btn.click()
                page.wait_for_timeout(2500)

                step_fields = self.extract_form_fields(page)
                step_classified = []
                for sf in step_fields:
                    sf_class, sf_val = self.classify_field(sf, candidate_data, custom_answers, manual_resolutions)
                    sf.classification = sf_class
                    step_classified.append((sf, sf_class, sf_val))
                    logger.debug(
                        "unstop step field classified: name=%s role=%s required=%s class=%s",
                        sf.name, sf.field_role, sf.required, sf_class.value,
                    )
                    if sf.required and sf_class in {
                        QuestionClassification.REQUIRES_USER,
                        QuestionClassification.UNSUPPORTED,
                        QuestionClassification.CONSENT,
                    } and sf_val is None:
                        logger.warning(
                            "Stopping fill: conditional field %s is unresolved (%s).",
                            sf.name, sf_class.value,
                        )
                        return FillResult(
                            success=False,
                            status="manual_required",
                            error_reason=f"Conditional field '{sf.label or sf.name}' requires user input.",
                        )
                step_fill_result = _fill_all_fields(step_classified)
                if step_fill_result is not None:
                    return step_fill_result

            # CRITICAL SAFETY BOUNDARY:
            # The fill phase never clicks a final submission control.  When the
            # terminal boundary of a progressive form was reached, the browser is
            # left open at the completed-but-unsubmitted form in state
            # ``form_filled``; the terminal control is resolved and clicked once
            # by the submission state machine.
            if reached_terminal_boundary:
                return FillResult(
                    success=True,
                    status="form_filled",
                    message=(
                        "Progressive form filled; halted before the terminal step "
                        "control. The submission state machine owns the final action."
                    ),
                    custom_answers=processed_answers,
                    is_terminal_reached=True,
                )

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
        """Check if application was already submitted on Unstop.

        Read-only, idempotent confirmation probe. It never clicks anything and
        never mutates platform state, so it is safe to call before and after a
        human-approved submission.
        """
        page = app_ctx.browser_page
        if page is None:
            return SubmissionStatus(confirmed=False, status="unknown", detail="No active browser page")

        try:
            return evaluate_submission_confirmation(page, opportunity_id=app_ctx.opportunity_id)
        except Exception:
            return SubmissionStatus(confirmed=False, status="ambiguous", detail="confirmation_probe_failed")

    def detect_terminal_step_context(self, page: Any) -> TerminalStepContext:
        """Provide explicit terminal-step evidence for submission control resolution.

        The adapter owns stepper/DOM interpretation so that
        ``submission_controls`` never has to scrape unrelated page state.
        """
        return detect_terminal_step_context(page)

    # ── Explicit human-approved submission boundary (U4 / M2A) ─────────────

    def submit_application(
        self,
        app_ctx: ApplicationContext,
    ) -> dict[str, Any]:
        """Click the final Unstop submit control and verify real platform confirmation.

        Authorization model (M1 handoff repair):
        Authorization is verified by the orchestrator (``execute_browser_submission``)
        before this method is called, via two durable DB checks:
          - ``approved_at IS NOT NULL``: human explicitly confirmed submission
          - ``submission_claimed_at IS NOT NULL``: atomic exclusive worker claim

        Control resolution model (M2A):
        Uses ``resolve_final_submission_control`` to find exactly one verified,
        visible, enabled final submit control. Fails closed without clicking if
        zero, multiple, disabled, hidden, intermediate, or ambiguous controls are found.

        Safety gates (in order):
        1. Origin gate: page.url must be a valid Unstop HTTPS origin.
        2. Challenge gate: no Cloudflare/bot challenges.
        3. check_status() pre-gate: confirmed→already_confirmed, not_submitted→continue,
           all other states fail closed with zero clicks.
        4. Control resolution and revalidation before single click.
        """
        page = app_ctx.browser_page

        if page is None:
            return {
                "success": False,
                "confirmed": False,
                "error": "no_browser_page",
            }

        # ── Gate 1: Explicit origin check ─────────────────────────────────────
        # Must happen first, before any control resolution or click path.
        # Invalid origin returns submission_origin_invalid with zero clicks.
        try:
            current_url = getattr(page, "url", None) or ""
        except Exception:
            current_url = ""

        if not is_valid_unstop_origin(current_url):
            return {
                "success": False,
                "confirmed": False,
                "error": "submission_origin_invalid",
            }

        # ── Gate 2: Fail closed on bot challenges ──────────────────────────────
        if not callable(getattr(page, "locator", None)):
            return {
                "success": False,
                "confirmed": False,
                "error": "challenge_probe_failed",
            }
        try:
            challenge_loc = page.locator("iframe[src*='challenges.cloudflare'], div#challenge-stage")
            if not callable(getattr(challenge_loc, "count", None)):
                return {
                    "success": False,
                    "confirmed": False,
                    "error": "challenge_probe_failed",
                }
            count = challenge_loc.count()
            if not isinstance(count, int):
                return {
                    "success": False,
                    "confirmed": False,
                    "error": "challenge_probe_failed",
                }
            if count > 0:
                return {
                    "success": False,
                    "confirmed": False,
                    "error": "challenge_detected",
                }
        except Exception:
            return {
                "success": False,
                "confirmed": False,
                "error": "challenge_probe_failed",
            }

        # ── Gate 3: Mandatory check_status() pre-submission safety gate ────────
        # confirmed=True  → return already_confirmed, zero clicks.
        # status exactly "not_submitted" → may continue to control resolution.
        # ambiguous / unknown / unauthenticated / exceptions / probe failures /
        # any other unexpected status → fail closed, zero clicks.
        try:
            pre_status = self.check_status(app_ctx)
        except Exception:
            return {
                "success": False,
                "confirmed": False,
                "error": "pre_status_probe_failed",
            }

        # Structural & type verification:
        # pre_status must have expected structure, confirmed must be exactly a bool, status a string
        if (
            not hasattr(pre_status, "confirmed")
            or not hasattr(pre_status, "status")
            or type(pre_status.confirmed) is not bool
            or not isinstance(pre_status.status, str)
        ):
            return {
                "success": False,
                "confirmed": False,
                "error": "pre_status_probe_failed",
            }

        if pre_status.confirmed is True:
            return {
                "success": True,
                "confirmed": True,
                "already_confirmed": True,
                "confirmation_ref": getattr(pre_status, "confirmation_ref", None),
                "detail": getattr(pre_status, "detail", "already_confirmed"),
            }

        # Proceeding requires confirmed is False and status == "not_submitted"
        if not (pre_status.confirmed is False and pre_status.status == "not_submitted"):
            allowed_status_codes = {
                "ambiguous", "unauthenticated", "unknown",
                "not_submitted", "confirmed",
            }
            safe_status = pre_status.status if pre_status.status in allowed_status_codes else "pre_status_unexpected"
            return {
                "success": False,
                "confirmed": False,
                "error": f"pre_status_not_allowed:{safe_status}",
            }

        # ── Gate 4: Resolve the unique, verified final submission control ──────
        # U9: supply explicit terminal-step evidence so a progressive form whose
        # terminal action is labelled "Next" resolves to exactly one final
        # control.  Without this evidence, "Next" stays intermediate and the
        # gate fails closed rather than clicking a navigation control.
        terminal_context = self.detect_terminal_step_context(page)
        resolution = resolve_final_submission_control(page, terminal_context=terminal_context)
        if resolution.status != SubmissionControlResolutionStatus.EXACTLY_ONE_FINAL or resolution.locator is None:
            # M2C: An unresolved final boundary is not a retryable browser
            # failure.  This is deliberately limited to resolver outcomes; the
            # origin, challenge, status, and inspection-probe gates above retain
            # their own bounded safety codes.
            manual_reason_by_status = {
                SubmissionControlResolutionStatus.ONLY_INTERMEDIATE_OR_UNKNOWN: "ambiguous_controls",
                SubmissionControlResolutionStatus.ZERO_FINAL: "no_final_control",
                SubmissionControlResolutionStatus.MULTIPLE_FINAL: "multiple_final_controls",
                SubmissionControlResolutionStatus.FINAL_DISABLED: "final_control_disabled",
                SubmissionControlResolutionStatus.FINAL_HIDDEN: "final_control_hidden",
            }
            manual_reason = manual_reason_by_status.get(resolution.status)
            if manual_reason is not None:
                # A visible enabled generic Next is ambiguous even when it is
                # outside the active form.  It is never clicked or promoted to
                # FINAL_SUBMIT; this check only selects the bounded handoff code.
                has_visible_enabled_next = any(
                    candidate.text == "next"
                    and candidate.is_visible is True
                    and candidate.is_disabled is False
                    for candidate in resolution.candidates
                )
                if (
                    has_visible_enabled_next
                    and resolution.status in {
                        SubmissionControlResolutionStatus.ONLY_INTERMEDIATE_OR_UNKNOWN,
                        SubmissionControlResolutionStatus.ZERO_FINAL,
                    }
                ):
                    manual_reason = "ambiguous_next_control"
                return {
                    "success": False,
                    "confirmed": False,
                    "manual_review_required": True,
                    "error": "manual_final_action_required",
                    "reason": manual_reason,
                    "resolution_status": resolution.status.value,
                }
            return {
                "success": False,
                "confirmed": False,
                "error": f"resolution_failed:{resolution.status.value}",
                "resolution_status": resolution.status.value,
            }

        # M2A Revalidation: strictly revalidate the live element handle immediately before click
        is_reval_valid, reval_reason = revalidate_handle_before_click(
            page,
            resolution.locator,
            resolution.verified_control,
            expected_form_handle=resolution.form_handle,
            terminal_context=terminal_context,
        )
        if not is_reval_valid:
            return {
                "success": False,
                "confirmed": False,
                "error": f"revalidation_failed:{reval_reason}",
                "resolution_status": f"revalidation_failed:{reval_reason}",
            }

        # ── Final Pre-Click Safety Envelope ──────────────────────────────────
        # Re-verify origin, bot challenges, and platform status immediately before click.
        # Zero clicks on any violation.
        try:
            pre_click_url = getattr(page, "url", None) or ""
        except Exception:
            pre_click_url = ""

        if not is_valid_unstop_origin(pre_click_url):
            return {
                "success": False,
                "confirmed": False,
                "error": "pre_click_origin_invalid",
            }

        if not callable(getattr(page, "locator", None)):
            return {
                "success": False,
                "confirmed": False,
                "error": "pre_click_challenge_probe_failed",
            }
        try:
            pre_click_challenge_loc = page.locator("iframe[src*='challenges.cloudflare'], div#challenge-stage")
            if not callable(getattr(pre_click_challenge_loc, "count", None)):
                return {
                    "success": False,
                    "confirmed": False,
                    "error": "pre_click_challenge_probe_failed",
                }
            pre_click_count = pre_click_challenge_loc.count()
            if not isinstance(pre_click_count, int):
                return {
                    "success": False,
                    "confirmed": False,
                    "error": "pre_click_challenge_probe_failed",
                }
            if pre_click_count > 0:
                return {
                    "success": False,
                    "confirmed": False,
                    "error": "pre_click_challenge_detected",
                }
        except Exception:
            return {
                "success": False,
                "confirmed": False,
                "error": "pre_click_challenge_probe_failed",
            }

        try:
            pre_click_status = self.check_status(app_ctx)
        except Exception:
            return {
                "success": False,
                "confirmed": False,
                "error": "pre_click_status_probe_failed",
            }

        if (
            not hasattr(pre_click_status, "confirmed")
            or not hasattr(pre_click_status, "status")
            or type(pre_click_status.confirmed) is not bool
            or not isinstance(pre_click_status.status, str)
        ):
            return {
                "success": False,
                "confirmed": False,
                "error": "pre_click_status_probe_failed",
            }

        if pre_click_status.confirmed is True:
            return {
                "success": True,
                "confirmed": True,
                "already_confirmed": True,
                "confirmation_ref": getattr(pre_click_status, "confirmation_ref", None),
                "detail": getattr(pre_click_status, "detail", "already_confirmed"),
            }

        if not (pre_click_status.confirmed is False and pre_click_status.status == "not_submitted"):
            allowed_status_codes = {
                "ambiguous", "unauthenticated", "unknown",
                "not_submitted", "confirmed",
            }
            safe_status = pre_click_status.status if pre_click_status.status in allowed_status_codes else "pre_status_unexpected"
            return {
                "success": False,
                "confirmed": False,
                "error": "pre_click_status_not_allowed",
                "detail": f"pre_click_status_not_allowed:{safe_status}",
            }

        # Perform exactly one click on the unique verified and revalidated handle
        clicked_identifier: str = (
            resolution.verified_control.candidate_id
            if resolution.verified_control
            else "resolved_control"
        )
        try:
            resolution.locator.click()
        except Exception:
            return {
                "success": False,
                "confirmed": False,
                "error": "control_click_failed",
            }

        # Confirm the result by observing the platform, never by assuming that
        # a successful click means a successful submission.
        confirmation = self.wait_for_confirmation(app_ctx, timeout_ms=45000)
        return {
            "success": bool(confirmation.confirmed),
            "confirmed": bool(confirmation.confirmed),
            "confirmation_ref": confirmation.confirmation_ref,
            "detail": confirmation.detail,
            "clicked_selector": clicked_identifier,
            "url": redact_url(getattr(page, "url", "")),
        }

    def wait_for_confirmation(
        self,
        app_ctx: ApplicationContext,
        *,
        timeout_ms: int = 45000,
        poll_ms: int = 1500,
    ) -> SubmissionStatus:
        """Poll read-only for an actual Unstop confirmation signal after submission."""
        page = app_ctx.browser_page
        if page is None:
            return SubmissionStatus(confirmed=False, status="unknown", detail="No active browser page")

        elapsed = 0
        last = SubmissionStatus(confirmed=False, status="unknown", detail="No signal yet")
        while elapsed <= timeout_ms:
            last = self.check_status(app_ctx)
            if last.confirmed:
                return last
            try:
                if hasattr(page, "wait_for_timeout"):
                    page.wait_for_timeout(poll_ms)
            except Exception:
                break
            elapsed += poll_ms

        return SubmissionStatus(
            confirmed=False,
            status=last.status,
            detail=(
                f"Unstop did not report confirmation within {timeout_ms}ms "
                f"(last observed state: {last.status} — {last.detail}). "
                "Result is UNCONFIRMED and must not be recorded as submitted."
            ),
        )


# U9: read-only inspection of the Angular Material stepper state.
#
# A progressive Unstop form may label its terminal action "Next".  Terminality
# is derived ONLY from the stepper's own selected index — never from button
# counts, URL shape, or page layout guesses.  The adapter turns this into
# explicit TerminalStepContext for submission_controls.
_STEPPER_STATE_JS = """
() => {
    const stepper = document.querySelector("mat-horizontal-stepper, mat-vertical-stepper");
    if (!stepper) return {"found": false, "total": 0, "selected": null, "reason": "stepper_not_found"};
    const headers = Array.from(stepper.querySelectorAll("mat-step-header, [role='tab']"));
    if (!headers.length) return {"found": false, "total": 0, "selected": null, "reason": "stepper_headers_not_found"};
    let selected = null;
    for (let i = 0; i < headers.length; i++) {
        if (headers[i].getAttribute("aria-selected") === "true") { selected = i; }
    }
    if (selected === null) {
        return {"found": true, "total": headers.length, "selected": null,
                "reason": "selected_step_indeterminate"};
    }
    return {"found": true, "total": headers.length, "selected": selected,
            "reason": "angular_stepper_selected_index"};
}
"""


def detect_terminal_step_context(page: Any) -> TerminalStepContext:
    """Derive explicit terminal-step evidence from the page (read-only).

    Never guesses: without a trustworthy Angular stepper state the context is
    explicitly non-terminal, so navigation controls stay INTERMEDIATE.
    """
    if page is None or not callable(getattr(page, "evaluate", None)):
        return TerminalStepContext(is_terminal=False, evidence="stepper_probe_unavailable")
    try:
        state = page.evaluate(_STEPPER_STATE_JS)
    except Exception:
        return TerminalStepContext(is_terminal=False, evidence="stepper_probe_failed")
    if not isinstance(state, dict):
        return TerminalStepContext(is_terminal=False, evidence="stepper_probe_malformed")

    if not state.get("found"):
        return TerminalStepContext(
            is_terminal=False,
            evidence=str(state.get("reason") or "stepper_not_found"),
        )

    total = state.get("total")
    selected = state.get("selected")
    if not isinstance(total, int) or total <= 0:
        return TerminalStepContext(is_terminal=False, evidence="stepper_step_count_unavailable")

    if not isinstance(selected, int):
        return TerminalStepContext(
            is_terminal=False,
            evidence="selected_step_indeterminate",
            total_steps=total,
        )

    is_terminal = selected == total - 1
    return TerminalStepContext(
        is_terminal=is_terminal,
        evidence=(
            "angular_stepper_last_step_selected"
            if is_terminal
            else "angular_stepper_intermediate_step"
        ),
        current_step=selected,
        total_steps=total,
    )


def is_valid_unstop_origin(url: str | None) -> bool:
    """Validate that a URL belongs strictly to an authorized HTTPS Unstop origin.

    Requirements:
    - scheme must be exactly 'https'
    - hostname must be 'unstop.com' or end with '.unstop.com'
    - hostname is None or empty fails closed
    - embedded credentials (user:pass@) are rejected
    - lookalikes like 'unstop.com.evil.test' are rejected
    - port must be None or 443; malformed or alternative ports fail closed
    """
    if not url:
        return False
    try:
        parsed = urlsplit(str(url).strip())
        scheme = (parsed.scheme or "").lower()
        if scheme != "https":
            return False
        if parsed.username or parsed.password:
            return False
        hostname = (parsed.hostname or "").lower()
        if not hostname:
            return False
        if hostname != "unstop.com" and not hostname.endswith(".unstop.com"):
            return False
        if parsed.port is not None and parsed.port != 443:
            return False
        return True
    except Exception:
        return False


def evaluate_submission_confirmation(
    page: Any,
    opportunity_id: int | None = None,
) -> SubmissionStatus:
    """Evaluate whether an active browser page exhibits verifiable Unstop confirmation evidence.

    Hardenings (M2A):
    - Origin verification: scheme must be HTTPS and hostname must be unstop.com or *.unstop.com.
    - Rejects foreign origins, http, file://, lookalike domains, or missing URLs.
    - Rejects login / auth redirects on real Unstop origin as unconfirmed.
    - Rejects generic substring matches in query parameters (e.g. ?source=success or ?ref=confirmation).
    - Requires known sanitized confirmation route pattern or visible, specific confirmation message.
    - Rejects if registration form input fields remain visible on the page.
    - Rejects hidden confirmation elements.
    - Mandatory probes: missing probe methods fail closed with fixed reason codes.
    - Fails closed as 'not_submitted' or 'ambiguous' on any uncertainty or probe exception.
    """
    if page is None:
        return SubmissionStatus(confirmed=False, status="unknown", detail="No active browser page")

    raw_url = getattr(page, "url", None) or ""
    if not is_valid_unstop_origin(raw_url):
        return SubmissionStatus(
            confirmed=False,
            status="ambiguous",
            detail="unauthorized_origin",
        )

    try:
        parsed = urlsplit(raw_url)
        path = parsed.path.lower().rstrip("/")
    except Exception:
        path = ""

    # Check for login / auth redirect on the Unstop origin
    if "/login" in path or "/auth" in path:
        return SubmissionStatus(
            confirmed=False,
            status="unauthenticated",
            detail="unauthenticated_redirect",
        )

    # U9: /register/edit is platform evidence that a registration already exists
    # for this opportunity — Unstop only exposes an edit view of an existing
    # registration.  Classified as confirmed so local state is never mistaken
    # for an unsubmitted form after an already-completed external registration.
    # This is path-only evidence (never query/fragment) and is checked before
    # the active-form-input probe, which would otherwise see the editable form.
    if re.search(r"/register(?:/[^/]+)?/edit$", path):
        return SubmissionStatus(
            confirmed=True,
            status="confirmed",
            confirmation_ref=(
                f"UNSTOP-CONFIRMED-{opportunity_id}" if opportunity_id else "UNSTOP-CONFIRMED"
            ),
            detail="registration_edit_page",
        )

    # 1. Reject if active registration inputs are still visible (fail closed on probe errors)
    has_unsubmitted_inputs = False
    try:
        if not hasattr(page, "locator") or not callable(getattr(page, "locator", None)):
            return SubmissionStatus(
                confirmed=False,
                status="ambiguous",
                detail="input_probe_failed",
            )
        inputs = page.locator("form input:not([type='hidden']), form textarea")
        if not hasattr(inputs, "count") or not callable(getattr(inputs, "count", None)):
            return SubmissionStatus(
                confirmed=False,
                status="ambiguous",
                detail="input_probe_failed",
            )
        count = inputs.count()
        if not isinstance(count, int):
            return SubmissionStatus(
                confirmed=False,
                status="ambiguous",
                detail="input_probe_failed",
            )
        if count > 0:
            if not hasattr(inputs, "nth") or not callable(getattr(inputs, "nth", None)):
                return SubmissionStatus(
                    confirmed=False,
                    status="ambiguous",
                    detail="input_probe_failed",
                )
            for i in range(count):
                el = inputs.nth(i)
                if not hasattr(el, "is_visible") or not callable(getattr(el, "is_visible", None)):
                    return SubmissionStatus(
                        confirmed=False,
                        status="ambiguous",
                        detail="input_probe_failed",
                    )
                if el.is_visible():
                    has_unsubmitted_inputs = True
                    break
    except Exception:
        return SubmissionStatus(
            confirmed=False,
            status="ambiguous",
            detail="input_probe_failed",
        )

    if has_unsubmitted_inputs:
        return SubmissionStatus(
            confirmed=False,
            status="not_submitted",
            detail="form_inputs_visible",
        )

    # 2. Check path-only URL evidence (NEVER query string or fragment)
    url_confirmed = False
    recognized_endings = (
        "/register/success",
        "/application/success",
        "/application/submitted",
        "/registration/success",
    )
    if any(path.endswith(ending) for ending in recognized_endings):
        url_confirmed = True
    elif re.search(r"/(competitions|jobs|internships)/[^/]+/(register|application)/(success|submitted)$", path):
        url_confirmed = True

    # 3. Check visible confirmation text component supported by platform (fail closed on probe errors)
    CONFIRMATION_PHRASES = (
        "Successfully Registered",
        "Registration Successful",
        "You have successfully registered",
        "Your application has been submitted",
        "Application Submitted Successfully",
        "Application Received",
        "Thank you for registering",
    )
    text_confirmed = False

    if not hasattr(page, "locator") or not callable(getattr(page, "locator", None)):
        return SubmissionStatus(
            confirmed=False,
            status="ambiguous",
            detail="visibility_probe_failed",
        )

    for phrase in CONFIRMATION_PHRASES:
        try:
            loc = page.locator(f"text='{phrase}'")
            if not hasattr(loc, "count") or not callable(getattr(loc, "count", None)):
                return SubmissionStatus(
                    confirmed=False,
                    status="ambiguous",
                    detail="visibility_probe_failed",
                )
            count = loc.count()
            if not isinstance(count, int):
                return SubmissionStatus(
                    confirmed=False,
                    status="ambiguous",
                    detail="visibility_probe_failed",
                )
            if count > 0:
                if not hasattr(loc, "nth") or not callable(getattr(loc, "nth", None)):
                    return SubmissionStatus(
                        confirmed=False,
                        status="ambiguous",
                        detail="visibility_probe_failed",
                    )
                for i in range(count):
                    el = loc.nth(i)
                    if not hasattr(el, "is_visible") or not callable(getattr(el, "is_visible", None)):
                        return SubmissionStatus(
                            confirmed=False,
                            status="ambiguous",
                            detail="visibility_probe_failed",
                        )
                    if el.is_visible():
                        text_confirmed = True
                        break
            if text_confirmed:
                break
        except Exception:
            return SubmissionStatus(
                confirmed=False,
                status="ambiguous",
                detail="visibility_probe_failed",
            )

    if url_confirmed or text_confirmed:
        ref = f"UNSTOP-CONFIRMED-{opportunity_id}" if opportunity_id else "UNSTOP-CONFIRMED"
        return SubmissionStatus(
            confirmed=True,
            status="confirmed",
            confirmation_ref=ref,
            detail="submission_confirmed_success",
        )

    # 4. Explicit pre-submit route
    if path.endswith("/register") or "/register/" in path or "/apply" in path:
        return SubmissionStatus(
            confirmed=False,
            status="not_submitted",
            detail="form_open_unsubmitted",
        )

    # 5. Ambiguous: no verified signal
    return SubmissionStatus(
        confirmed=False,
        status="ambiguous",
        detail="confirmation_not_detected",
    )
