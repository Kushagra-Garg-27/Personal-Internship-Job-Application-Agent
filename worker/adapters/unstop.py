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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

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
                    name = el.get_attribute("name")
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

                    # Detect semantic field role
                    norm_name = (name or "").lower()
                    norm_id = el_id.lower()
                    norm_lbl = label.lower()

                    field_role = "custom"
                    if "firstname" in norm_name or "first_name" in norm_name or "first name" in norm_lbl:
                        field_role = "first_name"
                    elif "lastname" in norm_name or "name_last" in norm_name or "last_name" in norm_name or "last name" in norm_lbl:
                        field_role = "last_name"
                    elif "full_name" in norm_name or ("name" in norm_name and "org" not in norm_name and "user" not in norm_name and "last" not in norm_name and "first" not in norm_name):
                        field_role = "full_name"
                    elif "email" in norm_name or el_type == "email" or "email" in norm_lbl:
                        field_role = "email"
                    elif "tel" in norm_name or "mobile" in norm_name or "phone" in norm_name or el_type == "tel" or "mobile" in norm_lbl or "phone" in norm_lbl:
                        field_role = "phone"
                    elif "cities_input" in norm_id or "player_location" in norm_name or "location" in norm_lbl:
                        field_role = "location"
                    elif "skill" in norm_lbl or "skill" in placeholder.lower() or "skills" in norm_id:
                        field_role = "skills"
                    elif "institute" in norm_lbl or "college" in norm_lbl or "organisation" in norm_lbl or "organization" in norm_lbl or "organisation" in norm_id or "organisation" in norm_name:
                        field_role = "organization"
                    elif "gender" in norm_name or "gender" in norm_lbl:
                        field_role = "gender"
                    elif "differently_abled" in norm_name or "differently abled" in norm_lbl or "disability" in norm_lbl:
                        field_role = "differently_abled"
                    elif "user_type" in norm_name or ("type" in norm_name and tag == "un-radio-group") or "user type" in norm_lbl:
                        field_role = "user_type"
                    elif "course_pursuing" in norm_name or "course" in norm_lbl:
                        field_role = "course_pursuing"
                    elif "course_stream" in norm_name or "stream" in norm_lbl:
                        field_role = "course_stream"
                    elif "course_specialization" in norm_name or "specialization" in norm_lbl:
                        field_role = "course_specialization"
                    elif "passout_year" in norm_name or "passout" in norm_lbl or "graduation_year" in norm_name or "graduation year" in norm_lbl:
                        field_role = "graduation_year"
                    elif "designation" in norm_name or "designation" in norm_lbl:
                        field_role = "designation"
                    elif "work_experience" in norm_name or "experience" in norm_lbl:
                        field_role = "work_experience"
                    elif "acceptance" in norm_name or "acceptance" in norm_id or "terms" in norm_lbl or "agree" in norm_lbl:
                        field_role = "acceptance"
                    elif el_type == "file" or "resume" in norm_name or "resume" in norm_lbl or "cv" in norm_lbl:
                        field_role = "resume"
                    elif "statement" in norm_name or "why" in norm_lbl or "statement" in norm_lbl or "sop" in norm_lbl:
                        field_role = "statement_of_purpose"

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
    ) -> tuple[QuestionClassification, Any]:
        """Classify field into PROFILE_FACT, SAFE_INFERENCE, REQUIRES_USER, or UNSUPPORTED."""
        role = field_obj.field_role
        answers_map = {
            str(a.get("question_id") or a.get("question_text") or a.get("label")): a.get("answer", "")
            for a in (custom_answers or [])
        }

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
            val = candidate_data.get("agree_terms") or answers_map.get("acceptance") or answers_map.get("agree_terms")
            if val:
                return QuestionClassification.PROFILE_FACT, True
            return QuestionClassification.REQUIRES_USER, None

        if role == "user_type":
            val = candidate_data.get("user_type") or answers_map.get("user_type")
            if val:
                return QuestionClassification.PROFILE_FACT, val
            return (QuestionClassification.REQUIRES_USER, None) if field_obj.required else (QuestionClassification.SAFE_INFERENCE, None)

        if role in {"course_pursuing", "course_stream", "course_specialization", "graduation_year", "designation", "work_experience"}:
            val = candidate_data.get(role) or answers_map.get(role)
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
        sensitive_keywords = ["salary", "ctc", "criminal", "authorization", "visa", "gender", "race", "disability", "notice period"]
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
            apply_btn = page.locator(
                "#un-register-btn, button:has-text('Quick Apply'), div:has-text('Quick Apply'), "
                "button:has-text('Apply Now'), button:has-text('Register')"
            )
            if apply_btn.count() > 0 and apply_btn.first.is_visible():
                try:
                    apply_btn.first.click()
                    page.wait_for_timeout(2500)
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
            extracted_fields = self.extract_form_fields(page)

            # Classify all extracted fields
            classified_fields: list[tuple[FormField, QuestionClassification, Any]] = []
            for f in extracted_fields:
                classification, val = self.classify_field(f, candidate_data, custom_answers)
                f.classification = classification
                classified_fields.append((f, classification, val))

                # Fail-closed safety rule: If any REQUIRED field requires user or is unsupported, STOP
                if f.required and classification in {QuestionClassification.REQUIRES_USER, QuestionClassification.UNSUPPORTED}:
                    return FillResult(
                        success=False,
                        status="manual_required",
                        error_reason=f"Required field '{f.label or f.name}' requires user decision ({classification.value}). Cannot auto-fill.",
                    )

            # Deterministically fill fields
            for f, classification, val in classified_fields:
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
                        org_opt = page.locator(".autocomplete-content li, div[class*='option']").first
                        if org_opt.count() > 0 and org_opt.is_visible():
                            org_opt.click()
                            page.wait_for_timeout(300)
                        else:
                            org_input.press("Enter")

                elif f.field_role == "skills":
                    skills_input = page.locator("input[placeholder*='skills'], input[id*='skills'], app-autocomplete input").last
                    if skills_input.count() > 0 and skills_input.is_visible():
                        skill_list = val if isinstance(val, list) else [str(val)]
                        for sk in skill_list:
                            skills_input.click()
                            skills_input.type(str(sk), delay=30)
                            page.wait_for_timeout(1000)
                            s_opt = page.locator(".autocomplete-content li").first
                            if s_opt.count() > 0 and s_opt.is_visible():
                                s_opt.click()
                                page.wait_for_timeout(300)
                            else:
                                skills_input.press("Enter")

                elif f.tag == "un-radio-group":
                    rg_parts = []
                    if f.id:
                        rg_parts.append(f"un-radio-group#{f.id} label")
                    if f.name:
                        rg_parts.append(f"un-radio-group[name='{f.name}'] label")
                    rg_sel = ", ".join(rg_parts) if rg_parts else "un-radio-group label"
                    rg_lbl = page.locator(rg_sel).filter(has_text=str(val)).first
                    if rg_lbl.count() > 0 and rg_lbl.is_visible():
                        rg_lbl.click()

                elif f.tag == "mat-select":
                    sel = _field_locator("mat-select")
                    if sel.count() > 0 and sel.is_visible():
                        sel.click()
                        page.wait_for_timeout(500)
                        opt = page.locator("mat-option, .mat-mdc-option").filter(has_text=str(val)).first
                        if opt.count() == 0 or not opt.is_visible():
                            opt = page.locator("mat-option, .mat-mdc-option").first
                        if opt.count() > 0 and opt.is_visible():
                            opt.click()
                            page.wait_for_timeout(300)

                elif f.tag == "un-checkbox":
                    if bool(val):
                        chk_parts = []
                        if f.id:
                            chk_parts.append(f"un-checkbox#{f.id} label")
                        if f.name:
                            chk_parts.append(f"un-checkbox[name='{f.name}'] label")
                        chk_sel = ", ".join(chk_parts) if chk_parts else "un-checkbox label"
                        chk = page.locator(chk_sel).first
                        if chk.count() > 0 and chk.is_visible():
                            chk.click()

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

            # Resume upload if present
            if resume_path and Path(resume_path).exists():
                file_input = page.locator("input[type='file']")
                if file_input.count() > 0:
                    file_input.first.set_input_files(resume_path)

            # Check for conditional fields
            new_fields = self.extract_form_fields(page)
            for nf in new_fields:
                if nf.required and not any(existing.id == nf.id and existing.name == nf.name for existing in extracted_fields):
                    nf_class, _ = self.classify_field(nf, candidate_data, custom_answers)
                    if nf_class in {QuestionClassification.REQUIRES_USER, QuestionClassification.UNSUPPORTED}:
                        return FillResult(
                            success=False,
                            status="manual_required",
                            error_reason=f"Conditional field '{nf.label or nf.name}' appeared and requires user input.",
                        )

            # CRITICAL SAFETY BOUNDARY:
            # Verify that final submission controls are NOT clicked.
            # The Worker leaves the browser window open at the completed form.
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
            # 1. Hard confirmation signals rendered by Unstop after a successful
            #    registration (confirmation screen / success URL).
            url = (page.url or "").lower()
            url_confirmed = any(
                marker in url for marker in ("success", "/registered", "thank-you", "confirmation")
            )

            confirmed_selectors = [
                "text='Successfully Registered'",
                "text='Registration Successful'",
                "text='You have successfully registered'",
                "text='Application Submitted'",
                "text='Application Completed'",
                "text='Already Registered'",
                "text='Registered Successfully'",
                "text='Your application has been submitted'",
                "text='Thank you for registering'",
            ]
            matched: str | None = None
            for sel in confirmed_selectors:
                loc = page.locator(sel)
                try:
                    if loc.count() > 0 and loc.first.is_visible():
                        matched = sel
                        break
                except Exception:
                    continue

            if matched or url_confirmed:
                return SubmissionStatus(
                    confirmed=True,
                    status="confirmed",
                    confirmation_ref=f"UNSTOP-CONFIRMED-{app_ctx.opportunity_id}",
                    detail=(
                        f"Unstop confirmed submission via {matched or 'success URL'} "
                        f"(url={page.url})."
                    ),
                )

            # 2. Explicit pre-submit state: form still open and unsubmitted.
            if page.url and "/register" in page.url:
                return SubmissionStatus(
                    confirmed=False,
                    status="not_submitted",
                    detail="Unstop registration form is still open and unsubmitted.",
                )

            # 3. Anything else is genuinely ambiguous — never treated as success.
            return SubmissionStatus(
                confirmed=False,
                status="ambiguous",
                detail=f"No Unstop confirmation signal observed at url={page.url}.",
            )
        except Exception as exc:
            return SubmissionStatus(confirmed=False, status="ambiguous", detail=str(exc))

    # ── Explicit human-approved submission boundary (U4) ──────────────────
    #
    # The default fill() contract leaves the browser open and NEVER submits.
    # submit_application() is the single, explicitly named method that may
    # click the final control, and it is callable ONLY by passing the exact
    # human approval token after the human has reviewed the filled form.
    # Everything else — ready_for_review, a valid form, a successful autofill,
    # a prior instruction, a test run, a timeout, a default, or an agent
    # assumption — fails closed here.

    def submit_application(
        self,
        app_ctx: ApplicationContext,
        *,
        approval_token: str | None = None,
    ) -> dict[str, Any]:
        """Click the final Unstop submit control and verify real platform confirmation.

        Parameters
        ----------
        app_ctx : ApplicationContext
            Live context holding the filled, reviewed page.
        approval_token : str | None
            Must equal ``HUMAN_SUBMISSION_APPROVAL_TOKEN``. Absent/incorrect
            tokens raise before any browser interaction occurs.

        Returns
        -------
        dict[str, Any]
            ``{"success", "confirmed", "confirmation_ref", "detail", ...}``.
            ``success`` is True only when Unstop itself reported confirmation.
        """
        from core.status import HUMAN_SUBMISSION_APPROVAL_TOKEN

        if not (isinstance(approval_token, str) and approval_token == HUMAN_SUBMISSION_APPROVAL_TOKEN):
            raise PermissionError(
                "Unstop submit refused: explicit human approval token required. "
                "ready_for_review is not approval."
            )

        page = app_ctx.browser_page
        if page is None:
            return {
                "success": False,
                "confirmed": False,
                "error": "No active browser page available for Unstop submission.",
            }

        # Fail closed on bot challenges — never attempt to bypass them.
        if page.locator("iframe[src*='challenges.cloudflare'], div#challenge-stage").count() > 0:
            return {
                "success": False,
                "confirmed": False,
                "error": "Cloudflare bot verification present; human interaction required. Not bypassed.",
            }

        # Refuse to submit twice on the same live page.
        pre_status = self.check_status(app_ctx)
        if pre_status.confirmed:
            return {
                "success": True,
                "confirmed": True,
                "already_confirmed": True,
                "confirmation_ref": pre_status.confirmation_ref,
                "detail": pre_status.detail,
            }

        clicked: str | None = None
        last_error: str | None = None
        for sel in self.SUBMISSION_SELECTORS:
            loc = page.locator(sel)
            try:
                if loc.count() == 0:
                    continue
                btn = loc.first
                if not btn.is_visible():
                    continue
                if not btn.is_enabled():
                    continue
                btn.click()
                clicked = sel
                break
            except Exception as exc:
                last_error = f"{sel}: {exc}"
                continue

        if clicked is None:
            return {
                "success": False,
                "confirmed": False,
                "error": f"No enabled Unstop submit control found. Last error: {last_error}",
            }

        # Confirm the result by observing the platform, never by assuming that
        # a successful click means a successful submission.
        confirmation = self.wait_for_confirmation(app_ctx, timeout_ms=45000)
        return {
            "success": bool(confirmation.confirmed),
            "confirmed": bool(confirmation.confirmed),
            "confirmation_ref": confirmation.confirmation_ref,
            "detail": confirmation.detail,
            "clicked_selector": clicked,
            "url": page.url,
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

