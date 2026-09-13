"""M3 — Submission-boundary API access guard.

Provides a FastAPI dependency that enforces the ``X-Submission-Secret`` header
on submission-boundary endpoints when ``SUBMISSION_API_SECRET`` is configured.

Design
------
* When ``settings.SUBMISSION_API_SECRET`` is empty (the default), the guard is a
  no-op — all requests pass through unchanged.  Development and existing tests
  require no configuration changes.
* When ``settings.SUBMISSION_API_SECRET`` is set to a non-empty string, every
  request to a guarded endpoint *must* supply the header::

      X-Submission-Secret: <configured value>

  Comparison is constant-time (``hmac.compare_digest``) to prevent timing
  attacks on the secret value.
* The secret value is **never** logged at any level.
* The guard is an *additional* boundary on top of M1 controls.  It does NOT
  replace token validation, single-use enforcement, ``approved_at``, or the
  atomic worker claim.

Threat model (what M3 stops)
-----------------------------
A local script or process that knows the API is on localhost:8000 and simply
calls ``POST /applications/{id}/request-approval-token`` or
``POST /applications/{id}/confirm-submit`` without the human being in the loop.

Threat model (what M3 does NOT stop)
--------------------------------------
* A process that can read the machine's environment variables (they already have
  full machine access and can hit the DB directly).
* The human sitting at the machine (intentional — this is a personal agent).
* Browser-based CSRF (CORS tightening in main.py handles that separately).
* An unauthenticated request that supplies a valid M1 approval token but no
  secret header — that request is rejected *at the guard layer* before token
  validation even runs.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import Header, HTTPException, status

from core.config import WEAK_SECRETS, settings

logger = logging.getLogger(__name__)

_GUARD_DISABLED_LOGGED = False


def is_secret_adequate(secret: str | None) -> bool:
    """Return True if the secret meets length (>= 32 chars) and entropy standards."""
    if not secret or len(secret) < 32:
        return False
    if secret.lower() in WEAK_SECRETS:
        return False
    if len(set(secret)) < 4:
        return False
    return True


def require_submission_secret(
    x_submission_secret: str | None = Header(default=None, alias="x-submission-secret"),
) -> None:
    """FastAPI dependency: enforce the X-Submission-Secret header guard (M3.1).

    In 'required' mode (default):
      - If SUBMISSION_API_SECRET is missing, empty, or inadequate: fail closed with 503.
      - If X-Submission-Secret header is missing or incorrect: 403 Forbidden.
      - If header matches configured secret: proceeds.

    In 'disabled' mode:
      - Explicit opt-out; logs a prominent warning and permits the request.
    """
    global _GUARD_DISABLED_LOGGED

    guard_mode = getattr(settings, "SUBMISSION_GUARD_MODE", "required")

    if guard_mode == "disabled":
        if not _GUARD_DISABLED_LOGGED:
            logger.warning(
                "M3: SUBMISSION_GUARD_MODE is explicitly 'disabled' — "
                "submission-boundary endpoints are unguarded. "
                "Never use disabled mode in production."
            )
            _GUARD_DISABLED_LOGGED = True
        return

    if guard_mode != "required":
        logger.error("M3 guard: unrecognized SUBMISSION_GUARD_MODE '%s'. Failing closed.", guard_mode)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Submission guard configuration is invalid.",
        )

    # Required mode: fail closed if secret is missing or inadequate
    configured_secret = getattr(settings, "SUBMISSION_API_SECRET", "")
    if not is_secret_adequate(configured_secret):
        logger.error(
            "M3 guard: SUBMISSION_GUARD_MODE is 'required' but SUBMISSION_API_SECRET is not properly configured. "
            "Failing closed with HTTP 503."
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Submission guard is misconfigured. Submission operations are disabled.",
        )

    # Required mode: check request header
    if x_submission_secret is None:
        logger.warning("M3 guard: rejected request — X-Submission-Secret header missing.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Missing X-Submission-Secret header. "
                "This endpoint requires the configured submission API secret."
            ),
        )

    # Constant-time comparison
    if not hmac.compare_digest(x_submission_secret, configured_secret):
        logger.warning("M3 guard: rejected request — X-Submission-Secret header mismatch.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid X-Submission-Secret header.",
        )
