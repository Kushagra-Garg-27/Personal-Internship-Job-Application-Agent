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


def setup_platform_session(platform: str, key_override: str | None = None) -> Path:
    """Interactively log into a platform and save encrypted session state."""
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
    target_path = Path(f"worker/storage/{platform}_storage_state.enc")

    print("=" * 70)
    print(f"[SESSION SETUP] Interactive Session Setup: {platform.upper()}")
    print("=" * 70)
    print(f"1. Opening browser to: {url}")
    print("2. Please log into your account manually in the browser window.")
    print("   (DO NOT enter credentials in this terminal; never store passwords!)")
    print("3. Once logged in and viewing your dashboard/feed, return here.")
    print("=" * 70)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(url)

        input("\n>> Press [ENTER] in this terminal once you have successfully logged in... ")

        # Capture storage state
        state_data = context.storage_state()
        browser.close()

    saved_path = save_encrypted_storage_state(state_data, target_path, key=key_override)
    print(f"\n[OK] Session captured successfully and encrypted at rest: {saved_path}")
    print("   The worker can now use this encrypted session state for autofill.")
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
