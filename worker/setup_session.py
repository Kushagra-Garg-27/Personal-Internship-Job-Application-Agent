"""Interactive session setup CLI for Experimental platform adapters (Phase 9).

Launches a visible browser window, allows the user to log in manually, captures
Playwright's `storage_state`, and encrypts it at rest using Fernet.
Never asks for or stores the user's raw password.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from worker.security.storage import save_encrypted_storage_state

PLATFORM_LOGIN_URLS = {
    "internshala": "https://internshala.com/login/user",
    "unstop": "https://unstop.com/auth/login",
}

DEFAULT_BRAVE_PROFILE_DIR = Path(__file__).resolve().parent / "storage" / "brave_agent_profile"


def resolve_brave_executable(override: str | Path | None = None) -> Path:
    """Resolve the installed Brave browser executable path on Windows.

    Resolution order:
    1. override parameter or BRAVE_PATH environment variable (if exists).
    2. %LOCALAPPDATA%\\BraveSoftware\\Brave-Browser\\Application\\brave.exe
    3. %ProgramFiles%\\BraveSoftware\\Brave-Browser\\Application\\brave.exe
    4. %ProgramFiles(x86)%\\BraveSoftware\\Brave-Browser\\Application\\brave.exe

    Raises FileNotFoundError if Brave cannot be found. Fails clearly rather than
    silently falling back to anonymous Playwright Chromium.
    """
    candidates: list[Path] = []

    env_path = override or os.environ.get("BRAVE_PATH")
    if env_path:
        candidates.append(Path(env_path))

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            Path(local_app_data) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe"
        )

    prog_files = os.environ.get("ProgramFiles")
    if prog_files:
        candidates.append(
            Path(prog_files) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe"
        )

    prog_files_x86 = os.environ.get("ProgramFiles(x86)")
    if prog_files_x86:
        candidates.append(
            Path(prog_files_x86) / "BraveSoftware" / "Brave-Browser" / "Application" / "brave.exe"
        )

    for candidate in candidates:
        if candidate.is_file() or candidate.exists():
            return candidate.resolve()

    searched_paths = "\n - ".join(str(c) for c in candidates)
    raise FileNotFoundError(
        "Brave Browser executable was not found. Please install Brave or set BRAVE_PATH.\n"
        f"Searched locations:\n - {searched_paths}"
    )


def resolve_brave_profile_dir(override: Path | str | None = None) -> Path:
    """Resolve and validate the dedicated persistent Brave automation profile directory.

    Strict safety invariant: Never reuses or points to the user's everyday
    Brave User Data directory.
    """
    profile_dir = Path(override or DEFAULT_BRAVE_PROFILE_DIR).resolve()
    profile_str = str(profile_dir).lower()

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        everyday_user_data = (
            Path(local_app_data) / "BraveSoftware" / "Brave-Browser" / "User Data"
        ).resolve()
        if profile_dir == everyday_user_data or everyday_user_data in profile_dir.parents:
            raise ValueError(
                f"Safety violation: cannot use everyday Brave User Data directory ({profile_dir}) as agent profile!"
            )

    if "brave-browser\\user data" in profile_str or "brave-browser/user data" in profile_str:
        raise ValueError(
            f"Safety violation: cannot use everyday Brave User Data directory ({profile_dir}) as agent profile!"
        )
    profile_dir.mkdir(parents=True, exist_ok=True)
    return profile_dir


def setup_platform_session(
    platform: str,
    key_override: str | None = None,
    *,
    headless: bool = False,
    input_fn: Any = input,
    playwright_instance: Any = None,
    target_path_override: Path | None = None,
    probe_network: bool = True,
    brave_path_override: str | Path | None = None,
    profile_dir_override: str | Path | None = None,
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

    brave_path = resolve_brave_executable(override=brave_path_override)
    profile_dir = resolve_brave_profile_dir(override=profile_dir_override)

    print("=" * 70)
    print(f"[SESSION SETUP] Interactive Session Setup: {platform.upper()}")
    print(f"Browser: Brave ({brave_path})")
    print(f"Profile: {profile_dir} (Dedicated Agent Profile - NOT everyday profile)")
    print("=" * 70)
    print(f"1. Opening Brave browser to: {url}")
    print("2. Please log into your account manually in the browser window.")
    print("   (DO NOT enter credentials in this terminal; never store passwords!)")
    print("3. Once logged in and viewing your dashboard/feed, return here.")
    print("=" * 70)

    def _execute_session_capture(p):
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            executable_path=str(brave_path),
            headless=headless,
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url)

            # Wait for human interactive login
            input_fn("\n>> Press [ENTER] in this terminal once you have successfully logged in... ")

            # Pre-save verification: check that browser has navigated away from login page.
            # Synchronize with Playwright by actively querying the live URL across all open pages.
            pages_to_check = getattr(context, "pages", None) or [page]
            active_urls: list[str] = []
            for p_item in pages_to_check:
                live_url: str | None = None
                if hasattr(p_item, "evaluate"):
                    try:
                        live_url = p_item.evaluate("() => window.location.href")
                    except Exception:
                        pass
                if not live_url and hasattr(p_item, "url"):
                    live_url = p_item.url
                if live_url:
                    active_urls.append(live_url)

            def _is_login_url(u: str) -> bool:
                return "auth/login" in u or u.rstrip("/").endswith("/login")

            navigated_pages = [u for u in active_urls if not _is_login_url(u)]
            if not navigated_pages:
                raise RuntimeError(
                    f"Authentication verification failed: browser is still on login page ({active_urls}). "
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
                    platform_cookies, timeout=10.0, storage_state=state_data
                )
                if not is_valid:
                    raise RuntimeError(
                        f"Authentication verification probe failed ({probe_status}): {probe_detail}. "
                        "Session was NOT saved."
                    )

            return state_data
        finally:
            context.close()

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
    parser.add_argument(
        "--brave-path",
        required=False,
        default=None,
        help="Explicit path to Brave browser executable (defaults to auto-detection).",
    )
    parser.add_argument(
        "--profile-dir",
        required=False,
        default=None,
        help="Explicit path to dedicated persistent profile directory.",
    )
    args = parser.parse_args()
    setup_platform_session(
        args.platform,
        key_override=args.key,
        brave_path_override=args.brave_path,
        profile_dir_override=args.profile_dir,
    )


if __name__ == "__main__":
    main()
