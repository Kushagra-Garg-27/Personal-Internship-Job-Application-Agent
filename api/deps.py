"""FastAPI dependency injection helpers."""

from __future__ import annotations

from typing import Generator

from sqlalchemy.orm import Session

from core.database import SessionLocal


def get_db() -> Generator[Session, None, None]:
    """Yield a database session; close it after the request completes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
