"""Server-side approval token utilities for the submission gate (M1).

Replaces the static ``HUMAN_CONFIRMED_SUBMIT`` constant with cryptographically
secure, single-use, time-boxed tokens minted at confirmation time and stored in
dedicated Application columns.

Public surface
--------------
generate_approval_token()   – mint a new URL-safe random token string
make_token_expiry()         – produce the expiry datetime for a fresh token
is_token_valid()            – validate a client-supplied token against DB state

Constants
---------
APPROVAL_TOKEN_TTL_SECONDS  – lifetime of a freshly issued token (30 min)
APPROVAL_TOKEN_MIN_LENGTH   – minimum string length accepted by the adapter gate
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone


# ── Configuration ──────────────────────────────────────────────────────────

APPROVAL_TOKEN_TTL_SECONDS: int = 1800  # 30 minutes

# secrets.token_urlsafe(32) produces a 43-character URL-safe base-64 string.
# The adapter gate uses this lower bound to reject static or short strings
# (e.g. "ready_for_review" is 16 chars and is always rejected).
APPROVAL_TOKEN_MIN_LENGTH: int = 32


# ── Token generation ────────────────────────────────────────────────────────


def generate_approval_token() -> str:
    """Return a cryptographically secure, URL-safe random token string.

    Output is ~43 URL-safe base-64 characters (256 bits of entropy).
    Never call this function more than once per approval event — the returned
    value is stored in the database and acts as the single-use authorization
    proof; a second call produces a *different* token.
    """
    return secrets.token_urlsafe(32)


def make_token_expiry(ttl_seconds: int = APPROVAL_TOKEN_TTL_SECONDS) -> datetime:
    """Return the UTC expiry datetime for a freshly issued token.

    Parameters
    ----------
    ttl_seconds:
        How long (in seconds) the token should remain valid.
        Defaults to ``APPROVAL_TOKEN_TTL_SECONDS`` (30 minutes).
    """
    return datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)


# ── Token validation ────────────────────────────────────────────────────────


def is_token_valid(
    token: str | None,
    stored_token: str | None,
    expires_at: datetime | None,
) -> bool:
    """Return True only when *token* matches the DB record and has not expired.

    All three inputs must be present and consistent for a True result.  Any
    falsy value — ``None``, an empty string, a mismatched UUID, or a past
    expiry — returns False immediately so the gate fails closed.

    Parameters
    ----------
    token:
        The candidate token supplied by the caller (e.g. from the HTTP request
        body or passed programmatically by the worker).
    stored_token:
        The token previously stored in ``applications.approval_token`` by
        ``issue_approval_token()``.  ``None`` means no token was ever issued.
    expires_at:
        The expiry timestamp stored in ``applications.approval_token_expires_at``.
        ``None`` (no expiry recorded) always returns False.
    """
    if not token or not stored_token:
        return False
    if token != stored_token:
        return False
    if expires_at is None:
        return False

    now = datetime.now(timezone.utc)
    # Normalise naive datetimes (SQLite may return them without tzinfo).
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    return now <= expires_at
