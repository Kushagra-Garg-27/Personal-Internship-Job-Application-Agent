"""Interactive session setup CLI for Experimental platform adapters (Phase 9).

Launches a visible browser window, allows the user to log in manually, captures
Playwright's `storage_state`, and encrypts it at rest using Fernet.
Never asks for or stores the user's raw password.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from worker.security.storage import save_encrypted_storage_state

PLATFORM_LOGIN_URLS = {
    "internshala": "https://internshala.com/login/user",
    "unstop": "https://unstop.com/auth/login",
}


def setup_platform_session(
    platform: str,
    key_override: str | None = None,
    *,
    headless: bool = False,
    input_fn: Any = input,
    playwright_instance: Any = None,
    target_path_override: Path | None = None,
    probe_network: bool = True,
) -> Path:
    """Interactively log into a platform, verify authenticated state, and save encrypted session state."""
    platform = platform.lower().strip()
    if platform not in PLATFORM_LOGIN_URLS:
        raise ValueError(
            f"Unsupported platform {platform!r}. Supported: {list(PLATFORM_LOGIN_URLS.keys())}"
        )

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed in the worker environment.", file=sys.stderr)
        print("Run: pip install -r worker/requirements.txt && playwright install chromium", file=sys.stderr)
        sys.exit(1)

    url = PLATFORM_LOGIN_URLS[platform]
    target_path = target_path_override or Path(f"worker/storage/{platform}_storage_state.enc")

    print("=" * 70)
    print(f"[SESSION SETUP] Interactive Session Setup: {platform.upper()}")
    print("=" * 70)
    print(f"1. Opening browser to: {url}")
    print("2. Please log into your account manually in the browser window.")
    print("   (DO NOT enter credentials in this terminal; never store passwords!)")
    print("3. Once logged in and viewing your dashboard/feed, return here.")
    print("=" * 70)

    def _execute_session_capture(p):
        browser = p.chromium.launch(headless=headless)
        try:
            context = browser.new_context()
            page = context.new_page()
            page.goto(url)

            # Wait for human interactive login
            input_fn("\n>> Press [ENTER] in this terminal once you have successfully logged in... ")

            # Pre-save verification: check that browser has navigated away from login page
            current_url = page.url or ""
            if "auth/login" in current_url or current_url.rstrip("/").endswith("/login"):
                raise RuntimeError(
                    f"Authentication verification failed: browser is still on login page ({current_url}). "
                    "Session was NOT saved. Please re-run setup and complete login."
                )

            # Extract storage state
            state_data = context.storage_state()
            cookies = state_data.get("cookies", [])

            # Domain check
            expected_domain = "unstop.com" if platform == "unstop" else f"{platform}.com"
            platform_cookies = [c for c in cookies if expected_domain in c.get("domain", "")]
            if not platform_cookies:
                raise RuntimeError(
                    f"Authentication verification failed: no {platform} cookies captured. "
                    "Session was NOT saved. Please ensure you are fully logged in before continuing."
                )

            # Optional live probe for Unstop
            if platform == "unstop" and probe_network:
                from worker.adapters.unstop import probe_authenticated_endpoint
                is_valid, probe_status, probe_detail = probe_authenticated_endpoint(
                    platform_cookies, timeout=10.0
                )
                if not is_valid:
                    raise RuntimeError(
                        f"Authentication verification probe failed ({probe_status}): {probe_detail}. "
                        "Session was NOT saved."
                    )

            return state_data
        finally:
            browser.close()

    if playwright_instance is not None:
        state_data = _execute_session_capture(playwright_instance)
    else:
        with sync_playwright() as p:
            state_data = _execute_session_capture(p)

    saved_path = save_encrypted_storage_state(state_data, target_path, key=key_override)
    print(f"\n[OK] Session verified and encrypted at rest: {saved_path}")
    print(f"     Target platform: {platform.upper()}")
    print("     The worker can now use this encrypted session state for autofill.")
    return saved_path


def main():
    parser = argparse.ArgumentParser(
        description="Interactive session login setup for Experimental platform adapters."
    )
    parser.add_argument(
        "--platform",
        required=True,
        choices=["internshala", "unstop"],
        help="Target platform to capture session state for.",
    )
    parser.add_argument(
        "--key",
        required=False,
        default=None,
        help="Optional encryption key override (defaults to WORKER_STORAGE_KEY or local key file).",
    )
    args = parser.parse_args()
    setup_platform_session(args.platform, key_override=args.key)


if __name__ == "__main__":
    main()
