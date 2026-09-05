"""Shared pytest fixtures for the Job Application Agent test suite."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Generator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from fastapi.testclient import TestClient

from core.models.base import Base
from api.main import app
from api.deps import get_db
from core.config import settings


@pytest.fixture(scope="session")
def engine():
    """Create an in-memory SQLite engine for the full test session."""
    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _set_pragmas(dbapi_conn, _rec):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def db_session(engine) -> Generator[Session, None, None]:
    """Yield a transactional session that rolls back after each test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = sessionmaker(bind=connection)()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db_session: Session) -> Generator[TestClient, None, None]:
    """FastAPI TestClient with the DB session overridden."""

    def _override_get_db():
        try:
            yield db_session
        finally:
            pass  # session lifecycle managed by db_session fixture

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def upload_dir(tmp_path: Path) -> Generator[Path, None, None]:
    """Temporary upload directory for resume tests."""
    upload = tmp_path / "uploads"
    upload.mkdir()
    original = settings.UPLOAD_DIR
    settings.UPLOAD_DIR = upload
    yield upload
    settings.UPLOAD_DIR = original


# ── Sample file paths ─────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def sample_pdf_path() -> Path:
    return FIXTURES_DIR / "sample_resume.pdf"


@pytest.fixture()
def sample_docx_path() -> Path:
    return FIXTURES_DIR / "sample_resume.docx"


@pytest.fixture()
def empty_pdf_path() -> Path:
    return FIXTURES_DIR / "empty_scanned.pdf"
