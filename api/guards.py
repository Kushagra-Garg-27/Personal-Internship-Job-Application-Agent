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

from core.config import settings

logger = logging.getLogger(__name__)

_GUARD_DISABLED_LOGGED = False


def require_submission_secret(
    x_submission_secret: str | None = Header(default=None, alias="x-submission-secret"),
) -> None:
    """FastAPI dependency: enforce the X-Submission-Secret header guard (M3).

    When ``settings.SUBMISSION_API_SECRET`` is empty, this dependency is a
    no-op.  When it is set, the request *must* supply a matching header value.

    Raises
    ------
    HTTPException(403)
        If the secret is configured and the header is missing or incorrect.
    """
    global _GUARD_DISABLED_LOGGED

    configured_secret = settings.SUBMISSION_API_SECRET
    if not configured_secret:
        # Guard is opt-in — log once at startup-equivalent, then be silent.
        if not _GUARD_DISABLED_LOGGED:
            logger.warning(
                "M3: SUBMISSION_API_SECRET is not configured — "
                "submission-boundary endpoints are unguarded. "
                "Set SUBMISSION_API_SECRET in your environment or .env to enable the guard."
            )
            _GUARD_DISABLED_LOGGED = True
        return  # No-op — development / unset mode

    # Guard is active. Require a matching header (constant-time comparison).
    if x_submission_secret is None:
        # Do NOT log the configured secret or the missing value.
        logger.warning("M3 guard: rejected request — X-Submission-Secret header missing.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Missing X-Submission-Secret header. "
                "This endpoint requires the configured submission API secret."
            ),
        )

    # hmac.compare_digest prevents timing-based secret extraction.
    if not hmac.compare_digest(x_submission_secret, configured_secret):
        logger.warning("M3 guard: rejected request — X-Submission-Secret header mismatch.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid X-Submission-Secret header.",
        )

    # Header matched — allow the request to proceed.
