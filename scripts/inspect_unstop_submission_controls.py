#!/usr/bin/env python3
"""Safe read-only inspection tool for Unstop submission controls (M2A / Phase 6).

SAFETY INVARIANTS:
- Inspection-only: never calls click(), fill(), press(), set_input_files(), or submit_application().
- Never mutates DOM or starts worker runners.
- Fails closed without the mandatory safety flag: --acknowledge-read-only-live-inspection.
- Validates pre-navigation URL (HTTPS, unstop.com or *.unstop.com, default port 443, no embedded credentials).
- Re-validates post-navigation URL to fail closed on redirects to foreign origins.
- Preserves URL query parameters for browser routing but redacts them from logs, errors, and JSON output without netloc/userinfo.
- Fails closed on Cloudflare bot verification, CAPTCHAs, or unexpected navigation.
- Waits safely for bounded SPA hydration readiness before inspecting controls.
- Re-probes structural readiness immediately before final inspection; fails closed if readiness is lost.
- Rejects empty inspect_submission_controls() result without writing a success artifact.
- Outputs only sanitized structural control metadata (zero PII, form values, cookies, or tokens).
- Supports optional encrypted session state for authenticated inspection without persisting tokens/cookies.
- Does not run against live Unstop autonomously.
- Enforces a fixed maximum skeleton/loading indicator count to prevent unbounded enumeration.
"""

from __future__ import annotations

import argparse
from enum import Enum
import json
import logging
from pathlib import Path
import sys
import time
from typing import Any
from urllib.parse import urlsplit

# Ensure project root is in sys.path when script is executed directly
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from worker.adapters.submission_controls import (
    CANDIDATE_CONTROL_SELECTOR,
    classify_submission_control,
    inspect_submission_controls,
    redact_url,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("inspect_unstop")


class HydrationStatus(str, Enum):
    READY_FOR_INSPECTION = "READY_FOR_INSPECTION"
    LOGIN_DETECTED = "LOGIN_DETECTED"
    CHALLENGE_DETECTED = "CHALLENGE_DETECTED"
    FOREIGN_ORIGIN = "FOREIGN_ORIGIN"
    HYDRATION_TIMEOUT = "HYDRATION_TIMEOUT"
    PROBE_ERROR = "PROBE_ERROR"


# Bounded selectors for structural readiness
# Newsletter/subscription and search/navigation forms are excluded, mirroring the M2A JS inspector's
# plausible-application-form heuristic (excludes id/class/action containing 'newsletter' or 'subscribe' case-insensitively).
_APP_FORM_EXCLUSIONS = (
    ":not(header form):not(nav form)"
    ":not([role='search' i]):not([role='navigation' i])"
    ":not([id*='newsletter' i]):not([class*='newsletter' i]):not([action*='newsletter' i])"
    ":not([id*='subscribe' i]):not([class*='subscribe' i]):not([action*='subscribe' i])"
)
_APP_ROLE_FORM_EXCLUSIONS = (
    ":not(header [role='form']):not(nav [role='form'])"
    ":not([role='search' i]):not([role='navigation' i])"
    ":not([id*='newsletter' i]):not([class*='newsletter' i]):not([action*='newsletter' i])"
    ":not([id*='subscribe' i]):not([class*='subscribe' i]):not([action*='subscribe' i])"
)
PLAUSIBLE_APPLICATION_FORM_SELECTOR = (
    f"form{_APP_FORM_EXCLUSIONS}, [role='form']{_APP_ROLE_FORM_EXCLUSIONS}"
)
SKELETON_SELECTOR = (
    "[class*='skeleton'], [class*='shimmer'], [class*='loading'], "
    "[class*='spinner'], [aria-busy='true'], div#loading-stage, .loading-screen"
)

# Bounds
MIN_HYDRATION_TIMEOUT_MS = 1000
MAX_HYDRATION_TIMEOUT_MS = 30000
DEFAULT_HYDRATION_TIMEOUT_MS = 15000
REQUIRED_STABLE_POLLS = 3
DEFAULT_POLL_INTERVAL_MS = 250
# Maximum skeleton/loading indicators to enumerate; pathological counts above this cap return PROBE_ERROR.
MAX_SKELETON_COUNT = 100

# Fixed reason codes for timeout validation
TIMEOUT_REASON_INVALID_TYPE = "INVALID_TIMEOUT_TYPE"
TIMEOUT_REASON_INVALID_FORMAT = "INVALID_TIMEOUT_FORMAT"
TIMEOUT_REASON_OUT_OF_BOUNDS = "TIMEOUT_OUT_OF_BOUNDS"


def validate_hydration_timeout(val: Any) -> int:
    """Validate hydration timeout in milliseconds.

    Strict rules:
    - accept Python int excluding bool (type(val) is int)
    - optionally accept str if and only if matching only ASCII decimal digits
    - reject bool, float, Decimal, scientific notation ('1e3'), signs ('+1000', '-1000'), whitespace, None, and arbitrary coercible objects
    - enforce 1000 <= timeout_ms <= 30000
    """
    if isinstance(val, bool):
        raise ValueError(TIMEOUT_REASON_INVALID_TYPE)

    if type(val) is int:
        timeout_ms = val
    elif isinstance(val, str):
        if not val or not val.isascii() or not val.isdigit():
            raise ValueError(TIMEOUT_REASON_INVALID_FORMAT)
        try:
            timeout_ms = int(val)
        except Exception:
            raise ValueError(TIMEOUT_REASON_INVALID_FORMAT)
    else:
        raise ValueError(TIMEOUT_REASON_INVALID_TYPE)

    if not (MIN_HYDRATION_TIMEOUT_MS <= timeout_ms <= MAX_HYDRATION_TIMEOUT_MS):
        raise ValueError(TIMEOUT_REASON_OUT_OF_BOUNDS)

    return timeout_ms


def validate_unstop_url(raw_url: str, allow_test_hosts: bool = False) -> str:
    """Validate target URL for safe Unstop inspection without mutating query parameters.

    Requirements:
    - scheme must be 'https' (or 'http' if allow_test_hosts for local testing)
    - hostname must be exactly 'unstop.com' or end with '.unstop.com' (or localhost if allow_test_hosts)
    - production ports restricted to default/omitted 443
    - reject embedded credentials (user:pass@)
    - reject lookalikes such as 'unstop.com.evil.example'
    - return original URL intact for browser navigation
    """
    url = raw_url.strip()
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if not scheme:
        raise ValueError(f"Invalid URL: missing scheme in '{redact_url(url)}'.")

    if allow_test_hosts and scheme in ("http", "https"):
        pass
    elif scheme != "https":
        raise ValueError(f"Invalid URL scheme '{scheme}': only https is permitted.")

    if parts.username or parts.password:
        raise ValueError("Invalid URL: embedded credentials are strictly rejected.")

    hostname = (parts.hostname or "").lower()
    if not hostname:
        raise ValueError("Invalid URL: missing hostname.")

    # Restrict production ports to default/omitted 443
    if not allow_test_hosts and parts.port is not None and parts.port != 443:
        raise ValueError(f"Invalid port '{parts.port}': only standard HTTPS port 443 is permitted.")

    if allow_test_hosts and (hostname == "localhost" or hostname == "127.0.0.1"):
        return url

    if hostname == "unstop.com" or hostname.endswith(".unstop.com"):
        return url

    raise ValueError(f"Unauthorized hostname '{hostname}': must be 'unstop.com' or a '.unstop.com' subdomain.")


def probe_hydration_state(
    page: Any,
    allow_test_hosts: bool = False,
) -> tuple[HydrationStatus, dict[str, Any]]:
    """Inspect page readiness in a strictly read-only manner.

    Checks origin, login state, challenge presence, and structural element counts.
    Never reads input, textarea, select, or file values.
    Never persists raw text or attributes.
    """
    # 1. Re-validate origin
    curr_url = getattr(page, "url", None) or ""
    try:
        validate_unstop_url(curr_url, allow_test_hosts=allow_test_hosts)
    except Exception:
        return HydrationStatus.FOREIGN_ORIGIN, {}

    # 2. Check login/auth redirect
    curr_url_lower = curr_url.lower()
    if "/login" in curr_url_lower or "/auth" in curr_url_lower:
        return HydrationStatus.LOGIN_DETECTED, {}

    # 3. Check challenge presence
    if not hasattr(page, "locator") or not callable(getattr(page, "locator", None)):
        return HydrationStatus.PROBE_ERROR, {"reason": "missing_locator"}
    try:
        challenge_loc = page.locator("iframe[src*='challenges.cloudflare'], div#challenge-stage")
    except Exception:
        return HydrationStatus.PROBE_ERROR, {"reason": "challenge_locator_failed"}

    if not hasattr(challenge_loc, "count") or not callable(getattr(challenge_loc, "count", None)):
        return HydrationStatus.PROBE_ERROR, {"reason": "missing_challenge_count"}

    try:
        chal_count = challenge_loc.count()
    except Exception:
        return HydrationStatus.PROBE_ERROR, {"reason": "challenge_count_failed"}

    if isinstance(chal_count, bool) or not isinstance(chal_count, int) or chal_count < 0:
        return HydrationStatus.PROBE_ERROR, {"reason": "invalid_challenge_count"}

    if chal_count > 0:
        return HydrationStatus.CHALLENGE_DETECTED, {}

    # 4. Structural counts: Skeleton (visible only), Plausible Application Forms, Form-Scoped Candidate Controls
    # 4a. Skeleton presence — count only visible indicators
    try:
        skeleton_loc = page.locator(SKELETON_SELECTOR)
    except Exception:
        return HydrationStatus.PROBE_ERROR, {"reason": "skeleton_locator_failed"}

    if not hasattr(skeleton_loc, "count") or not callable(getattr(skeleton_loc, "count", None)):
        return HydrationStatus.PROBE_ERROR, {"reason": "missing_skeleton_count"}

    try:
        raw_skel_count = skeleton_loc.count()
    except Exception:
        return HydrationStatus.PROBE_ERROR, {"reason": "skeleton_count_failed"}

    if isinstance(raw_skel_count, bool) or not isinstance(raw_skel_count, int) or raw_skel_count < 0:
        return HydrationStatus.PROBE_ERROR, {"reason": "invalid_skeleton_count"}

    # Enforce a fixed cap on skeleton count to prevent unbounded enumeration on pathological pages.
    if raw_skel_count > MAX_SKELETON_COUNT:
        return HydrationStatus.PROBE_ERROR, {"reason": "skeleton_count_exceeds_cap"}

    visible_skeleton_count = 0
    if raw_skel_count > 0:
        if not hasattr(skeleton_loc, "nth") or not callable(getattr(skeleton_loc, "nth", None)):
            return HydrationStatus.PROBE_ERROR, {"reason": "missing_skeleton_nth"}
        for i in range(raw_skel_count):
            try:
                item = skeleton_loc.nth(i)
            except Exception:
                return HydrationStatus.PROBE_ERROR, {"reason": "skeleton_nth_failed"}
            if not hasattr(item, "is_visible") or not callable(getattr(item, "is_visible", None)):
                return HydrationStatus.PROBE_ERROR, {"reason": "missing_skeleton_is_visible"}
            try:
                vis = item.is_visible()
            except Exception:
                return HydrationStatus.PROBE_ERROR, {"reason": "skeleton_is_visible_failed"}
            if not isinstance(vis, bool):
                return HydrationStatus.PROBE_ERROR, {"reason": "invalid_skeleton_visibility_type"}
            if vis is True:
                visible_skeleton_count += 1

    # 4b. Plausible application forms
    try:
        form_loc = page.locator(PLAUSIBLE_APPLICATION_FORM_SELECTOR)
    except Exception:
        return HydrationStatus.PROBE_ERROR, {"reason": "form_locator_failed"}

    if not hasattr(form_loc, "count") or not callable(getattr(form_loc, "count", None)):
        return HydrationStatus.PROBE_ERROR, {"reason": "missing_form_count"}

    try:
        form_count = form_loc.count()
    except Exception:
        return HydrationStatus.PROBE_ERROR, {"reason": "form_count_failed"}

    if isinstance(form_count, bool) or not isinstance(form_count, int) or form_count < 0:
        return HydrationStatus.PROBE_ERROR, {"reason": "invalid_form_count"}

    # 4c. Form-scoped candidate controls inside plausible application forms
    if not hasattr(form_loc, "locator") or not callable(getattr(form_loc, "locator", None)):
        return HydrationStatus.PROBE_ERROR, {"reason": "missing_form_scoped_locator"}

    try:
        cand_loc = form_loc.locator(CANDIDATE_CONTROL_SELECTOR)
    except Exception:
        return HydrationStatus.PROBE_ERROR, {"reason": "cand_locator_failed"}

    if not hasattr(cand_loc, "count") or not callable(getattr(cand_loc, "count", None)):
        return HydrationStatus.PROBE_ERROR, {"reason": "missing_cand_count"}

    try:
        cand_count = cand_loc.count()
    except Exception:
        return HydrationStatus.PROBE_ERROR, {"reason": "cand_count_failed"}

    if isinstance(cand_count, bool) or not isinstance(cand_count, int) or cand_count < 0:
        return HydrationStatus.PROBE_ERROR, {"reason": "invalid_cand_count"}

    return HydrationStatus.READY_FOR_INSPECTION, {
        "skeleton_count": visible_skeleton_count,
        "form_count": form_count,
        "candidate_count": cand_count,
    }


def validate_readiness_details(details: Any) -> tuple[bool, tuple[int, int, int]]:
    """Strictly validate structural readiness details dictionary.

    Requirements:
    - details must be a dict.
    - fields 'skeleton_count', 'form_count', 'candidate_count' must all be present.
    - each field value must be an explicit nonnegative integer (isinstance(v, int), not bool, >= 0).
    - returns (is_ready, (skeleton_count, form_count, candidate_count)).
      is_ready is True if and only if skeleton_count == 0 and form_count >= 1 and candidate_count >= 1.
    - raises ValueError on any malformed structure (not dict, missing fields, bool/non-int, negative).
    """
    if not isinstance(details, dict):
        raise ValueError("details_not_a_dict")

    for field_name in ("skeleton_count", "form_count", "candidate_count"):
        if field_name not in details:
            raise ValueError(f"missing_field_{field_name}")
        val = details[field_name]
        if isinstance(val, bool) or not isinstance(val, int) or val < 0:
            raise ValueError(f"invalid_count_{field_name}")

    skel_count = details["skeleton_count"]
    form_count = details["form_count"]
    cand_count = details["candidate_count"]

    is_ready = (skel_count == 0 and form_count >= 1 and cand_count >= 1)
    return is_ready, (skel_count, form_count, cand_count)


def wait_for_hydration_readiness(
    page: Any,
    timeout_ms: int,
    poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS,
    allow_test_hosts: bool = False,
) -> tuple[HydrationStatus, int, int]:
    """Poll page safely until structural signature is stable for at least REQUIRED_STABLE_POLLS.

    Returns: (final_status, elapsed_ms, stable_poll_count)
    """
    elapsed_ms = 0
    stable_poll_count = 0
    last_signature = None

    while elapsed_ms <= timeout_ms:
        try:
            status, details = probe_hydration_state(page, allow_test_hosts=allow_test_hosts)
        except Exception:
            return HydrationStatus.PROBE_ERROR, elapsed_ms, stable_poll_count

        if status != HydrationStatus.READY_FOR_INSPECTION:
            # Fatal state detected (FOREIGN_ORIGIN, LOGIN_DETECTED, CHALLENGE_DETECTED, PROBE_ERROR)
            return status, elapsed_ms, stable_poll_count

        try:
            is_ready, counts = validate_readiness_details(details)
        except ValueError:
            return HydrationStatus.PROBE_ERROR, elapsed_ms, stable_poll_count

        if is_ready:
            _skel_count, form_count, cand_count = counts
            current_signature = (form_count, cand_count)
            if current_signature == last_signature:
                stable_poll_count += 1
            else:
                last_signature = current_signature
                stable_poll_count = 1

            if stable_poll_count >= REQUIRED_STABLE_POLLS:
                return HydrationStatus.READY_FOR_INSPECTION, elapsed_ms, stable_poll_count
        else:
            # Skeletons visible or forms/candidate controls not yet mounted: reset stability counter
            stable_poll_count = 0
            last_signature = None

        if elapsed_ms + poll_interval_ms > timeout_ms:
            break

        # Bounded read-only wait
        if hasattr(page, "wait_for_timeout") and callable(getattr(page, "wait_for_timeout")):
            try:
                page.wait_for_timeout(poll_interval_ms)
            except Exception:
                time.sleep(poll_interval_ms / 1000.0)
        else:
            time.sleep(poll_interval_ms / 1000.0)

        elapsed_ms += poll_interval_ms

    return HydrationStatus.HYDRATION_TIMEOUT, elapsed_ms, stable_poll_count


def run_inspection(
    url: str,
    output_path: Path,
    acknowledge_flag: bool,
    headless: bool = True,
    playwright_instance: Any = None,
    allow_test_hosts: bool = False,
    session_file: Path | str | None = None,
    hydration_timeout_ms: int = DEFAULT_HYDRATION_TIMEOUT_MS,
    poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS,
) -> int:
    """Inspect submission controls on an Unstop page in read-only mode."""
    if not acknowledge_flag:
        logger.error(
            "SAFETY ABORT: Missing mandatory flag --acknowledge-read-only-live-inspection. "
            "Refusing to execute."
        )
        return 2

    try:
        valid_target_url = validate_unstop_url(url, allow_test_hosts=allow_test_hosts)
    except Exception as exc:
        logger.error("SAFETY ABORT: Target URL validation failed: %s", type(exc).__name__)
        return 2

    try:
        valid_timeout_ms = validate_hydration_timeout(hydration_timeout_ms)
    except ValueError as exc:
        code = str(exc) if str(exc) in (TIMEOUT_REASON_INVALID_TYPE, TIMEOUT_REASON_INVALID_FORMAT, TIMEOUT_REASON_OUT_OF_BOUNDS) else "INVALID_TIMEOUT"
        logger.error("SAFETY ABORT: Hydration timeout validation failed: %s", code)
        return 2
    except Exception:
        logger.error("SAFETY ABORT: Hydration timeout validation failed: INVALID_TIMEOUT")
        return 2

    redacted_target = redact_url(valid_target_url)
    logger.info("Validated inspection target: %s", redacted_target)

    from playwright.sync_api import sync_playwright

    def _execute_with_playwright(p):
        browser = p.chromium.launch(headless=headless)
        context = None

        if session_file is not None:
            session_path = Path(session_file)
            if not session_path.is_file():
                logger.error("SAFETY ABORT: Explicit session file does not exist.")
                browser.close()
                return 2

            from worker.security.storage import load_decrypted_storage_state

            try:
                storage_state = load_decrypted_storage_state(session_path)
            except Exception as exc:
                logger.error("SAFETY ABORT: Failed to decrypt session state: %s", type(exc).__name__)
                browser.close()
                return 2

            try:
                context = browser.new_context(storage_state=storage_state)
                page = context.new_page()
            except Exception as exc:
                logger.error("SAFETY ABORT: Failed to create browser context: %s", type(exc).__name__)
                browser.close()
                return 2
        else:
            try:
                page = browser.new_page()
            except Exception as exc:
                logger.error("SAFETY ABORT: Failed to create browser page: %s", type(exc).__name__)
                browser.close()
                return 2

        try:
            logger.info("Opening page in read-only mode: %s", redacted_target)
            try:
                page.goto(valid_target_url, wait_until="domcontentloaded", timeout=30000)
            except Exception as exc:
                logger.error("SAFETY ABORT: Page navigation failed: %s", type(exc).__name__)
                return 4

            # Bounded SPA Hydration Readiness Polling
            readiness_status, readiness_elapsed, stable_polls = wait_for_hydration_readiness(
                page,
                timeout_ms=valid_timeout_ms,
                poll_interval_ms=poll_interval_ms,
                allow_test_hosts=allow_test_hosts,
            )

            if readiness_status == HydrationStatus.FOREIGN_ORIGIN:
                logger.error("SAFETY ABORT: Post-navigation URL redirected to unauthorized origin.")
                return 5
            elif readiness_status == HydrationStatus.LOGIN_DETECTED:
                curr_url = (getattr(page, "url", "") or "").lower()
                logger.error("Page redirected to login (%s); unauthenticated session. Failing closed.", redact_url(curr_url))
                return 4
            elif readiness_status == HydrationStatus.CHALLENGE_DETECTED:
                logger.error("Cloudflare challenge or bot verification detected; failing closed.")
                return 3
            elif readiness_status == HydrationStatus.PROBE_ERROR:
                logger.error("SAFETY ABORT: Readiness probe failed.")
                return 9
            elif readiness_status == HydrationStatus.HYDRATION_TIMEOUT:
                logger.error("SAFETY ABORT: Hydration readiness timeout after %d ms.", valid_timeout_ms)
                return 8

            # Re-verify origin, login, and challenge immediately after readiness before inspection
            post_nav_url = getattr(page, "url", "")
            try:
                validate_unstop_url(post_nav_url, allow_test_hosts=allow_test_hosts)
            except Exception as exc:
                logger.error("SAFETY ABORT: Post-readiness URL redirected to unauthorized origin: %s", type(exc).__name__)
                return 5

            curr_url = (getattr(page, "url", "") or "").lower()
            if "/login" in curr_url or "/auth" in curr_url:
                logger.error("Page redirected to login (%s); unauthenticated session. Failing closed.", redact_url(curr_url))
                return 4

            # Post-readiness challenge probe: missing count or invalid result must abort with code 9
            if not hasattr(page, "locator") or not callable(getattr(page, "locator", None)):
                logger.error("SAFETY ABORT: Post-readiness challenge probe missing locator.")
                return 9
            try:
                challenge_loc = page.locator("iframe[src*='challenges.cloudflare'], div#challenge-stage")
            except Exception:
                logger.error("SAFETY ABORT: Post-readiness challenge locator acquisition failed.")
                return 9

            if not hasattr(challenge_loc, "count") or not callable(getattr(challenge_loc, "count", None)):
                logger.error("SAFETY ABORT: Post-readiness challenge probe missing count method.")
                return 9

            try:
                challenge_count = challenge_loc.count()
            except Exception:
                logger.error("SAFETY ABORT: Post-readiness challenge probe failed.")
                return 9

            if isinstance(challenge_count, bool) or not isinstance(challenge_count, int) or challenge_count < 0:
                logger.error("SAFETY ABORT: Post-readiness challenge probe returned invalid count.")
                return 9

            if challenge_count > 0:
                logger.error("Cloudflare challenge or bot verification detected post-readiness; failing closed.")
                return 3

            # Pre-inspection structural re-probe: verify form-scoped readiness is still valid immediately
            # before calling inspect_submission_controls(). If the application form or controls
            # have disappeared since readiness was declared, fail closed without writing an artifact.
            pre_insp_status, pre_insp_details = probe_hydration_state(page, allow_test_hosts=allow_test_hosts)
            if pre_insp_status == HydrationStatus.PROBE_ERROR:
                logger.error("SAFETY ABORT: Pre-inspection structural re-probe failed.")
                return 9
            if pre_insp_status != HydrationStatus.READY_FOR_INSPECTION:
                logger.error(
                    "SAFETY ABORT: Pre-inspection re-probe detected unsafe state: %s",
                    pre_insp_status.value,
                )
                if pre_insp_status == HydrationStatus.FOREIGN_ORIGIN:
                    return 5
                if pre_insp_status == HydrationStatus.LOGIN_DETECTED:
                    return 4
                if pre_insp_status == HydrationStatus.CHALLENGE_DETECTED:
                    return 3
                return 9
            try:
                is_ready, counts = validate_readiness_details(pre_insp_details)
            except ValueError as exc:
                logger.error("SAFETY ABORT: Pre-inspection re-probe details malformed: %s", exc)
                return 9

            if not is_ready:
                skel_c, form_c, cand_c = counts
                logger.error(
                    "SAFETY ABORT: Pre-inspection re-probe found readiness lost "
                    "(skeletons=%s, forms=%s, candidates=%s). Writing no artifact.",
                    skel_c,
                    form_c,
                    cand_c,
                )
                return 8

            # Inspect controls using strictly read-only inspection
            try:
                inspected = inspect_submission_controls(page)
            except Exception as exc:
                logger.error("SAFETY ABORT: Submission control inspection failed: %s", type(exc).__name__)
                return 6

            # Reject empty inspection result — do not write a successful zero-candidate artifact.
            if len(inspected) == 0:
                logger.error(
                    "SAFETY ABORT: inspect_submission_controls() returned no candidates "
                    "despite structural readiness; writing no artifact."
                )
                return 8

            results = []
            for cand, _handle in inspected:
                cls = classify_submission_control(cand)
                data = cand.to_dict()
                data["classification"] = cls.value
                results.append(data)

            output_data = {
                "inspected_url": redact_url(getattr(page, "url", "") or valid_target_url),
                "readiness_status": readiness_status.value,
                "readiness_elapsed_ms": readiness_elapsed,
                "stable_poll_count": stable_polls,
                "candidate_count": len(results),
                "candidates": results,
            }

            try:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                with open(output_path, "w", encoding="utf-8") as f:
                    json.dump(output_data, f, indent=2)
            except Exception as exc:
                logger.error("SAFETY ABORT: Output write failed: %s", type(exc).__name__)
                return 7

            logger.info("Captured %d candidate controls to %s", len(results), output_path)
            return 0
        finally:
            if context is not None:
                context.close()
            browser.close()

    if playwright_instance is not None:
        return _execute_with_playwright(playwright_instance)
    else:
        with sync_playwright() as p:
            return _execute_with_playwright(p)


def main() -> int:
    try:
        parser = argparse.ArgumentParser(
            description="Safe read-only inspection tool for Unstop submission controls (M2A)."
        )
        parser.add_argument(
            "--url",
            required=True,
            help="Unstop URL to inspect (query params will be preserved for navigation but redacted from output)",
        )
        parser.add_argument("--output", required=True, help="Path to write JSON output")
        parser.add_argument(
            "--acknowledge-read-only-live-inspection",
            action="store_true",
            default=False,
            help="Mandatory explicit acknowledgment that this tool performs read-only inspection only.",
        )
        parser.add_argument(
            "--no-headless",
            action="store_false",
            dest="headless",
            default=True,
            help="Run browser with visible UI for debugging (defaults to headless).",
        )
        parser.add_argument(
            "--session-file",
            default=None,
            help="Optional path to encrypted Unstop session file for authenticated inspection (M2B readiness).",
        )
        parser.add_argument(
            "--hydration-timeout-ms",
            default=DEFAULT_HYDRATION_TIMEOUT_MS,
            help=f"Maximum time in milliseconds to wait for SPA hydration readiness (range: {MIN_HYDRATION_TIMEOUT_MS}-{MAX_HYDRATION_TIMEOUT_MS}, default: {DEFAULT_HYDRATION_TIMEOUT_MS}).",
        )

        args = parser.parse_args()

        try:
            timeout_ms = validate_hydration_timeout(args.hydration_timeout_ms)
        except ValueError as exc:
            code = str(exc) if str(exc) in (TIMEOUT_REASON_INVALID_TYPE, TIMEOUT_REASON_INVALID_FORMAT, TIMEOUT_REASON_OUT_OF_BOUNDS) else "INVALID_TIMEOUT"
            logger.error("SAFETY ABORT: Hydration timeout validation failed: %s", code)
            return 2
        except Exception:
            logger.error("SAFETY ABORT: Hydration timeout validation failed: INVALID_TIMEOUT")
            return 2

        return run_inspection(
            url=args.url,
            output_path=Path(args.output),
            acknowledge_flag=args.acknowledge_read_only_live_inspection,
            headless=args.headless,
            session_file=args.session_file,
            hydration_timeout_ms=timeout_ms,
        )
    except Exception as exc:
        logger.error("SAFETY ABORT: Unexpected error: %s", type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
