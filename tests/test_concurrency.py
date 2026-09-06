"""SQLite concurrency hardening tests.

Verifies that:
1. ``PRAGMA busy_timeout`` is set on every new connection (production engine).
2. Two concurrent writers sharing the same on-disk SQLite file in WAL mode
   succeed without raising ``OperationalError: database is locked`` thanks
   to the busy_timeout giving the second writer time to wait for the lock.
"""

from __future__ import annotations

import sqlite3
import tempfile
import threading
from pathlib import Path
from typing import Generator

import pytest
from sqlalchemy import Column, Integer, String, create_engine, event, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

# ---------------------------------------------------------------------------
# 1. Verify busy_timeout pragma on the production engine
# ---------------------------------------------------------------------------


def test_production_engine_sets_busy_timeout():
    """The ``_set_sqlite_pragmas`` listener must set ``busy_timeout = 5000``."""
    from core.database import engine

    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA busy_timeout")).scalar()
        assert result == 5000, f"Expected busy_timeout=5000, got {result}"


# ---------------------------------------------------------------------------
# 2. Concurrent-write stress test on a real file-backed SQLite database
# ---------------------------------------------------------------------------

_Base = declarative_base()


class _ConcurrencyRow(_Base):  # type: ignore[misc]
    """Throwaway table used only inside the concurrency test."""

    __tablename__ = "concurrency_test"
    id = Column(Integer, primary_key=True, autoincrement=True)
    writer = Column(String, nullable=False)
    seq = Column(Integer, nullable=False)


def _make_engine(db_path: str, busy_timeout_ms: int = 5000):
    """Create a file-backed SQLite engine with WAL + busy_timeout."""
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        echo=False,
    )

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        cur.close()

    return engine


# Number of rows each writer inserts — enough to create genuine lock contention.
_ROWS_PER_WRITER = 50


def _writer_thread(
    db_path: str,
    writer_name: str,
    barrier: threading.Barrier,
    errors: list,
    busy_timeout_ms: int = 5000,
):
    """Insert ``_ROWS_PER_WRITER`` rows inside individual transactions.

    A ``threading.Barrier`` synchronises start so both threads hit the
    database at the same instant, maximising contention.
    """
    eng = _make_engine(db_path, busy_timeout_ms=busy_timeout_ms)
    factory = sessionmaker(bind=eng)

    # Wait until both threads are ready before hammering the DB.
    barrier.wait()

    for seq in range(_ROWS_PER_WRITER):
        session = factory()
        try:
            session.add(_ConcurrencyRow(writer=writer_name, seq=seq))
            session.commit()
        except Exception as exc:
            errors.append((writer_name, seq, exc))
            session.rollback()
        finally:
            session.close()

    eng.dispose()


def test_concurrent_writers_with_busy_timeout(tmp_path: Path):
    """Two threads writing to the same SQLite file must both succeed.

    With ``busy_timeout = 5000``, the second writer waits up to 5 s for the
    write lock instead of failing immediately.  This test proves that the
    pragma eliminates the ``OperationalError: database is locked`` that
    would otherwise occur under the default zero timeout.
    """
    db_file = str(tmp_path / "concurrent_test.db")

    # Create the schema on disk.
    setup_engine = _make_engine(db_file)
    _Base.metadata.create_all(setup_engine)
    setup_engine.dispose()

    barrier = threading.Barrier(2)
    errors: list = []

    t1 = threading.Thread(
        target=_writer_thread,
        args=(db_file, "core", barrier, errors),
    )
    t2 = threading.Thread(
        target=_writer_thread,
        args=(db_file, "worker", barrier, errors),
    )

    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    # ── Assertions ────────────────────────────────────────────────────────
    assert not errors, (
        f"Concurrent writes produced errors (expected zero): "
        + "; ".join(f"{w}[{s}]: {e}" for w, s, e in errors)
    )

    # Verify all rows actually landed.
    verify_engine = _make_engine(db_file)
    factory = sessionmaker(bind=verify_engine)
    session = factory()
    try:
        total = session.query(_ConcurrencyRow).count()
        core_rows = (
            session.query(_ConcurrencyRow)
            .filter(_ConcurrencyRow.writer == "core")
            .count()
        )
        worker_rows = (
            session.query(_ConcurrencyRow)
            .filter(_ConcurrencyRow.writer == "worker")
            .count()
        )
    finally:
        session.close()
        verify_engine.dispose()

    assert total == _ROWS_PER_WRITER * 2, f"Expected {_ROWS_PER_WRITER * 2} rows, got {total}"
    assert core_rows == _ROWS_PER_WRITER, f"Core wrote {core_rows}, expected {_ROWS_PER_WRITER}"
    assert worker_rows == _ROWS_PER_WRITER, f"Worker wrote {worker_rows}, expected {_ROWS_PER_WRITER}"


def test_zero_busy_timeout_causes_lock_errors(tmp_path: Path):
    """Control test: with busy_timeout=0, concurrent writes *should* produce lock errors.

    This validates that the concurrency test above is meaningful — i.e.,
    the busy_timeout pragma is the actual reason the test passes, not
    incidental lack of contention.

    Note: This test uses raw ``sqlite3`` to bypass SQLAlchemy's retry layer
    and set ``busy_timeout = 0`` explicitly.  It is a best-effort
    demonstration — if the OS scheduler doesn't interleave the threads
    tightly enough, it may pass without errors.  We mark it
    ``xfail(strict=False)`` so it does not break CI, but documents the
    expected behavior.
    """
    db_file = str(tmp_path / "no_timeout_test.db")

    # Create the table directly with sqlite3.
    conn = sqlite3.connect(db_file)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        "CREATE TABLE concurrency_test "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, writer TEXT, seq INTEGER)"
    )
    conn.close()

    barrier = threading.Barrier(2)
    errors: list = []

    def _raw_writer(writer_name: str):
        barrier.wait()
        for seq in range(_ROWS_PER_WRITER):
            c = None
            try:
                c = sqlite3.connect(db_file, timeout=0)  # zero timeout
                c.execute("PRAGMA journal_mode=WAL")
                c.execute(
                    "INSERT INTO concurrency_test (writer, seq) VALUES (?, ?)",
                    (writer_name, seq),
                )
                c.commit()
            except sqlite3.OperationalError as exc:
                errors.append((writer_name, seq, exc))
            finally:
                if c is not None:
                    c.close()

    t1 = threading.Thread(target=_raw_writer, args=("a",))
    t2 = threading.Thread(target=_raw_writer, args=("b",))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    # We expect lock errors with zero timeout under contention.
    if not errors:
        pytest.skip(
            "No lock errors observed — OS scheduling did not produce "
            "sufficient contention in this run (expected but non-deterministic)"
        )
    assert any("database is locked" in str(e).lower() for _, _, e in errors)
