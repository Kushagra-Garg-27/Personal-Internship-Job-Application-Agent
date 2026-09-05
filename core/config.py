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

    # ── Scam / Risk (Phase 5) ─────────────────────────────────────────
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_DAILY_QUOTA: int = 1500

    # High-confidence hard rejection phrases
    SCAM_BLOCKLIST_KEYWORDS: list[str] = [
        "registration fee",
        "application fee required",
        "security deposit required",
        "refundable deposit",
        "training fee required",
        "wire transfer required",
        "wire transfer",
        "wire money",
        "western union",
        "moneygram",
        "crypto payment required",
        "crypto payment",
        "crypto transfer",
        "pay for background check",
        "pay upfront",
        "upfront fee",
        "buy equipment from our vendor",
        "buy your own equipment",
        "buy equipment and we will reimburse",
        "multilevel marketing",
        "pyramid scheme",
        "cashier's check reimbursement",
    ]

    # Borderline / weak signals (trigger ambiguity for LLM evaluation)
    SCAM_SUSPICIOUS_KEYWORDS: list[str] = [
        "earn $5000",
        "earn $1000 daily",
        "no experience required earn",
        "whatsapp interview",
        "telegram interview",
        "contact on telegram",
        "contact on whatsapp",
        "investment required",
        "package forwarding",
        "mystery shopper",
        "immediate start no interview",
        "no interview required",
        "urgent hiring",
        "guaranteed income",
    ]

    FREE_EMAIL_DOMAINS: list[str] = [
        "gmail.com",
        "yahoo.com",
        "hotmail.com",
        "outlook.com",
        "aol.com",
        "icloud.com",
        "zoho.com",
        "proton.me",
        "protonmail.com",
        "mail.com",
        "yandex.com",
    ]

    WHOIS_MIN_DOMAIN_AGE_DAYS: int = 30

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
