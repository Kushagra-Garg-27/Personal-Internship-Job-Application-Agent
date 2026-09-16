"""M5 Migration Tests.

Verifies:
1. Fresh alembic upgrade head creates applications table with revocation columns.
2. Downgrade to 0011 cleanly removes revocation columns.
3. Re-upgrade to 0012 restores revocation columns (round-trip idempotency).
4. ORM Application model columns match the schema created by migration 0012.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import declarative_base

from core.models.opportunity import Application


@pytest.fixture()
def alembic_config(tmp_path: Path) -> tuple[Config, str]:
    db_path = tmp_path / "m5_migration_test.db"
    db_url = f"sqlite:///{db_path.as_posix()}"

    # Locate the project alembic.ini
    repo_root = Path(__file__).resolve().parent.parent
    ini_path = repo_root / "alembic.ini"

    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option("script_location", str(repo_root / "alembic"))

    return cfg, db_url


def test_migration_0012_upgrade_creates_columns(alembic_config):
    cfg, db_url = alembic_config
    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    inspector = inspect(engine)
    columns = {col["name"] for col in inspector.get_columns("applications")}

    assert "approval_revoked_at" in columns
    assert "approval_revoked_by" in columns
    assert "approval_revocation_reason" in columns
    engine.dispose()


def test_migration_0012_downgrade_removes_columns(alembic_config):
    cfg, db_url = alembic_config
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0011")

    engine = create_engine(db_url)
    inspector = inspect(engine)
    columns = {col["name"] for col in inspector.get_columns("applications")}

    assert "approval_revoked_at" not in columns
    assert "approval_revoked_by" not in columns
    assert "approval_revocation_reason" not in columns
    engine.dispose()


def test_migration_0012_round_trip(alembic_config):
    cfg, db_url = alembic_config
    # 0011 -> 0012 -> 0011 -> 0012
    command.upgrade(cfg, "0011")
    command.upgrade(cfg, "0012")

    engine = create_engine(db_url)
    cols_1 = {col["name"] for col in inspect(engine).get_columns("applications")}
    assert "approval_revoked_at" in cols_1

    command.downgrade(cfg, "0011")
    cols_down = {col["name"] for col in inspect(engine).get_columns("applications")}
    assert "approval_revoked_at" not in cols_down

    command.upgrade(cfg, "0012")
    cols_up = {col["name"] for col in inspect(engine).get_columns("applications")}
    assert "approval_revoked_at" in cols_up
    assert "approval_revoked_by" in cols_up
    assert "approval_revocation_reason" in cols_up
    engine.dispose()


def test_orm_mapping_matches_migrated_columns(alembic_config):
    cfg, db_url = alembic_config
    command.upgrade(cfg, "head")

    engine = create_engine(db_url)
    inspector = inspect(engine)
    db_cols = {col["name"]: col for col in inspector.get_columns("applications")}

    # Application ORM attributes
    assert hasattr(Application, "approval_revoked_at")
    assert hasattr(Application, "approval_revoked_by")
    assert hasattr(Application, "approval_revocation_reason")

    assert db_cols["approval_revoked_at"]["nullable"] is True
    assert db_cols["approval_revoked_by"]["nullable"] is True
    assert db_cols["approval_revocation_reason"]["nullable"] is True
    engine.dispose()


def test_migration_0013_manual_review_reason_round_trip(alembic_config):
    """0013 adds one nullable bounded-reason column and cleanly round-trips."""
    cfg, db_url = alembic_config
    command.upgrade(cfg, "0012")
    command.upgrade(cfg, "0013")

    engine = create_engine(db_url)
    column = next(
        col for col in inspect(engine).get_columns("applications")
        if col["name"] == "manual_review_reason"
    )
    assert column["nullable"] is True
    assert "VARCHAR(100)" in str(column["type"]).upper()

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO opportunities
                (dedup_hash, status, reliability_tier, title, company)
            VALUES
                ('m2c-migration-round-trip', 'awaiting_submission', 'experimental', 'Role', 'Company')
        """))
        conn.execute(text("""
            INSERT INTO applications
                (opportunity_id, attempt_number, status, manual_review_reason)
            VALUES
                (1, 1, 'failed', 'ambiguous_next_control')
        """))
        assert conn.scalar(text("SELECT manual_review_reason FROM applications WHERE id = 1")) == (
            "ambiguous_next_control"
        )

    command.downgrade(cfg, "0012")
    assert "manual_review_reason" not in {
        col["name"] for col in inspect(engine).get_columns("applications")
    }

    command.upgrade(cfg, "0013")
    assert "manual_review_reason" in {
        col["name"] for col in inspect(engine).get_columns("applications")
    }
    engine.dispose()
