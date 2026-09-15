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
- Outputs only sanitized structural control metadata (zero PII, form values, cookies, or tokens).
- Supports optional encrypted session state for authenticated inspection without persisting tokens/cookies.
- Does not run against live Unstop autonomously.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlsplit

from worker.adapters.submission_controls import (
    classify_submission_control,
    inspect_submission_controls,
    redact_url,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("inspect_unstop")


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


def run_inspection(
    url: str,
    output_path: Path,
    acknowledge_flag: bool,
    headless: bool = True,
    playwright_instance: Any = None,
    allow_test_hosts: bool = False,
    session_file: Path | str | None = None,
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

            # Re-validate post-navigation URL to fail closed on unauthorized redirects
            post_nav_url = getattr(page, "url", "")
            try:
                validate_unstop_url(post_nav_url, allow_test_hosts=allow_test_hosts)
            except Exception as exc:
                logger.error(
                    "SAFETY ABORT: Post-navigation URL redirected to unauthorized origin: %s",
                    type(exc).__name__,
                )
                return 5

            # Check for Cloudflare / bot verification
            try:
                challenge_count = page.locator("iframe[src*='challenges.cloudflare'], div#challenge-stage").count()
                if challenge_count > 0:
                    logger.error("Cloudflare challenge or bot verification detected; failing closed.")
                    return 3
            except Exception as exc:
                logger.error("SAFETY ABORT: Challenge probe failed: %s", type(exc).__name__)
                return 3

            # Check for login redirect
            curr_url = (getattr(page, "url", "") or "").lower()
            if "/login" in curr_url or "/auth" in curr_url:
                logger.error("Page redirected to login (%s); unauthenticated session. Failing closed.", redact_url(curr_url))
                return 4

            # Inspect controls using strictly read-only inspection
            try:
                inspected = inspect_submission_controls(page)
            except Exception as exc:
                logger.error("SAFETY ABORT: Submission control inspection failed: %s", type(exc).__name__)
                return 6

            results = []
            for cand, _handle in inspected:
                cls = classify_submission_control(cand)
                data = cand.to_dict()
                data["classification"] = cls.value
                results.append(data)

            output_data = {
                "inspected_url": redact_url(getattr(page, "url", "") or valid_target_url),
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
        parser.add_argument("--url", required=True, help="Unstop URL to inspect (query params will be preserved for navigation but redacted from output)")
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

        args = parser.parse_args()
        return run_inspection(
            url=args.url,
            output_path=Path(args.output),
            acknowledge_flag=args.acknowledge_read_only_live_inspection,
            headless=args.headless,
            session_file=args.session_file,
        )
    except Exception as exc:
        logger.error("SAFETY ABORT: Unexpected error: %s", type(exc).__name__)
        return 1


if __name__ == "__main__":
    sys.exit(main())
