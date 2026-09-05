"""Application configuration via environment variables and .env files."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Central configuration for the Career Intelligence Core.

    Values can be overridden via environment variables or a ``.env`` file.
    """

    # ── Database ──────────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite:///./job_agent.db"

    # ── File storage ──────────────────────────────────────────────────
    UPLOAD_DIR: Path = Path("uploads")

    # ── Resume parsing ────────────────────────────────────────────────
    # Minimum number of characters extracted from a resume before we
    # consider parsing successful.  Below this threshold we flag the
    # resume as ``parse_failed`` (fail-closed principle).
    MIN_PARSE_CHARS: int = 50

    # ── Discovery: Greenhouse (stable) ────────────────────────────────
    # JSON list of {"token": "board_token", "company": "Display Name"}
    # Example: [{"token": "vaulttec", "company": "Vault-Tec"}]
    GREENHOUSE_BOARDS: list[dict] = []

    # ── Discovery: Lever (stable) ─────────────────────────────────────
    # JSON list of {"slug": "company_slug", "company": "Display Name"}
    # Example: [{"slug": "netflix", "company": "Netflix"}]
    LEVER_COMPANIES: list[dict] = []

    # ── Discovery: RSS feeds (stable) ─────────────────────────────────
    # JSON list of {"url": "https://...", "company": "Display Name"}
    RSS_FEEDS: list[dict] = []

    # ── Discovery: Gmail job alerts (discovery_only) ──────────────────
    GMAIL_CREDENTIALS_FILE: Path | None = None
    GMAIL_TOKEN_FILE: Path = Path("gmail_token.json")
    GMAIL_STATE_FILE: Path = Path("gmail_state.json")

    # ── Polling intervals (seconds) ──────────────────────────────────
    GREENHOUSE_POLL_INTERVAL: int = 21600   # 6 hours
    LEVER_POLL_INTERVAL: int = 21600        # 6 hours
    RSS_POLL_INTERVAL: int = 7200           # 2 hours
    GMAIL_POLL_INTERVAL: int = 300          # 5 minutes

    # ── Scheduler ─────────────────────────────────────────────────────
    SCHEDULER_ENABLED: bool = True

    # ── Funnel (Phase 4) ──────────────────────────────────────────────
    FUNNEL_EVALUATOR_ENABLED: bool = False
    FUNNEL_EVALUATOR_INTERVAL: int = 300    # 5 minutes
    FUNNEL_SCORE_THRESHOLD: float = 0.0

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
