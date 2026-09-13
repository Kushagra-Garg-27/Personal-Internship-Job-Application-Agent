#!/usr/bin/env python3
"""M3.1 — Privileged local approval CLI command for human submission authorization.

This script provides an isolated, privileged local approval channel for human
operators to authorize application submissions protected by the M3 submission guard.

Security invariants:
- Reads SUBMISSION_API_SECRET from the environment or a hidden interactive getpass prompt.
- Never accepts the secret in the command line or URL.
- Never prints, logs, or persists the secret or the M1 approval token.
- Keeps the server-issued M1 token strictly in memory.
- Performs two-phase approval via the guarded API endpoints.
- Displays application metadata and requires typed confirmation of the application ID.
- Never invokes workers, Playwright, form reconstruction, or browser adapters.
- Reports only that approval was recorded/queued, never claiming submission completed.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


def fetch_json(
    url: str,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Execute an HTTP request and return (status_code, response_json)."""
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)

    data_bytes = None
    if payload is not None:
        req_headers["Content-Type"] = "application/json"
        data_bytes = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        url=url,
        data=data_bytes,
        headers=req_headers,
        method=method,
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            status_code = resp.getcode()
            body = resp.read().decode("utf-8")
            return status_code, json.loads(body) if body else {}
    except urllib.error.HTTPError as err:
        body = err.read().decode("utf-8")
        try:
            err_json = json.loads(body)
        except Exception:
            err_json = {"detail": body or str(err)}
        return err.code, err_json
    except urllib.error.URLError as err:
        return 0, {"detail": f"Network error connecting to API: {err.reason}"}


def resolve_submission_secret() -> str:
    """Resolve the secret from environment or via a hidden interactive prompt."""
    secret = os.environ.get("SUBMISSION_API_SECRET", "").strip()
    if secret:
        return secret

    # If stdin is interactive or piped, prompt securely
    try:
        secret = getpass.getpass("Enter SUBMISSION_API_SECRET: ").strip()
    except Exception:
        secret = ""
    return secret


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Privileged human approval channel for M3-guarded job applications."
    )
    parser.add_argument(
        "--application-id",
        type=int,
        required=True,
        help="ID of the Application to approve for submission.",
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default="http://127.0.0.1:8000",
        help="Base URL of the Career Intelligence API (default: http://127.0.0.1:8000).",
    )
    parser.add_argument(
        "--actor",
        type=str,
        default="human_operator",
        help="Operator audit identifier (default: human_operator).",
    )

    parsed = parser.parse_args(argv)
    api_url = parsed.api_url.rstrip("/")
    app_id = parsed.application_id
    actor = parsed.actor

    # 1. Resolve secret before doing any API calls
    secret = resolve_submission_secret()
    if not secret:
        sys.stderr.write("Error: SUBMISSION_API_SECRET is required to authorize submission.\n")
        return 1

    # 2. Fetch application details
    status_code, app_data = fetch_json(f"{api_url}/applications/{app_id}")
    if status_code == 404:
        sys.stderr.write(f"Error: Application {app_id} not found.\n")
        return 1
    if status_code != 200:
        detail = app_data.get("detail", f"HTTP {status_code}")
        sys.stderr.write(f"Error fetching application {app_id}: {detail}\n")
        return 1

    opp_id = app_data.get("opportunity_id")
    if not opp_id:
        sys.stderr.write(f"Error: Application {app_id} has no associated opportunity ID.\n")
        return 1

    # 3. Fetch opportunity details
    status_code, opp_data = fetch_json(f"{api_url}/opportunities/{opp_id}")
    if status_code != 200:
        detail = opp_data.get("detail", f"HTTP {status_code}")
        sys.stderr.write(f"Error fetching opportunity {opp_id}: {detail}\n")
        return 1

    # 4. Display review summary to operator
    app_status = app_data.get("status", "unknown")
    opp_status = opp_data.get("status", "unknown")
    approved_at = app_data.get("approved_at")
    approved_by = app_data.get("approved_by")
    claimed_at = app_data.get("submission_claimed_at")
    claimed_by = app_data.get("claimed_by")
    resume_id = app_data.get("resume_id")

    approval_status_str = (
        f"Approved at {approved_at} by {approved_by}" if approved_at else "Not approved"
    )
    claim_status_str = (
        f"Claimed at {claimed_at} by {claimed_by}" if claimed_at else "Unclaimed"
    )

    print("=" * 60)
    print(f"APPLICATION SUBMISSION APPROVAL REVIEW (M3.1)")
    print("=" * 60)
    print(f"Application ID:      {app_id}")
    print(f"Opportunity Title:   {opp_data.get('title')}")
    print(f"Company:             {opp_data.get('company')}")
    print(f"Platform / Source:   {opp_data.get('source') or opp_data.get('platform')}")
    print(f"Application Status:  {app_status}")
    print(f"Opportunity Status:  {opp_status}")
    print(f"Selected Resume ID:  {resume_id if resume_id is not None else 'None'}")
    print(f"Approval State:      {approval_status_str}")
    print(f"Claim State:         {claim_status_str}")
    print("=" * 60)

    # 5. Check eligibility
    if app_status == "submitted":
        sys.stderr.write(f"Error: Application {app_id} has already been submitted.\n")
        return 1

    if approved_at:
        sys.stderr.write(f"Error: Application {app_id} has already been approved.\n")
        return 1

    if claimed_at:
        sys.stderr.write(f"Error: Application {app_id} has already been claimed by a worker.\n")
        return 1

    if app_status not in ("form_filled", "pending"):
        sys.stderr.write(
            f"Error: Application {app_id} is in status '{app_status}'. "
            "Only 'form_filled' or 'pending' applications can be approved.\n"
        )
        return 1

    # 6. Require typed confirmation with exact application ID
    confirm_phrase = f"CONFIRM {app_id}"
    try:
        user_input = input(
            f"\nTo authorize irreversible submission, type '{confirm_phrase}': "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        sys.stderr.write("\nApproval aborted by operator.\n")
        return 1

    if user_input != confirm_phrase:
        sys.stderr.write("Error: Confirmation input mismatched or cancelled. No approval request was sent.\n")
        return 1

    # 7. Phase 1: Request approval token
    headers = {"X-Submission-Secret": secret}
    status_code, token_resp = fetch_json(
        f"{api_url}/applications/{app_id}/request-approval-token",
        headers=headers,
        method="POST",
    )

    if status_code != 200:
        detail = token_resp.get("detail", f"HTTP {status_code}")
        sys.stderr.write(f"Error: Failed to issue approval token: {detail}\n")
        return 1

    token = token_resp.get("token")
    if not token or not isinstance(token, str):
        sys.stderr.write("Error: Malformed token response from server.\n")
        return 1

    # 8. Phase 2: Confirm submission with server token
    payload = {
        "approval_token": token,
        "approved_by": actor,
        "platform_confirmed": True,
        "confirmation_detail": f"Privileged CLI human authorization by {actor}",
    }

    status_code, confirm_resp = fetch_json(
        f"{api_url}/applications/{app_id}/confirm-submit",
        headers=headers,
        method="POST",
        payload=payload,
    )

    if status_code != 200:
        detail = confirm_resp.get("detail", f"HTTP {status_code}")
        sys.stderr.write(f"Error: Confirmation request failed: {detail}\n")
        return 1

    if not confirm_resp.get("success"):
        reason = confirm_resp.get("reason") or confirm_resp.get("error") or "Rejection by server"
        sys.stderr.write(f"Error: Submission confirmation rejected: {reason}\n")
        return 1

    # 9. Final success report
    print("\n[SUCCESS] Approval recorded and authorized.")
    print("Application has been transitioned to approved status and queued for worker execution.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
