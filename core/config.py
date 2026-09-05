"""Application configuration via environment variables and .env files."""

from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Central configuration for the Career Intelligence Core.

    Values can be overridden via environment variables or a `.env` file.
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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
