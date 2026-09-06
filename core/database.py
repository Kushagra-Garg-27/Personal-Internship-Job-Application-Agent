"""SQLAlchemy engine, session factory, and WAL-mode setup for SQLite."""

from contextlib import contextmanager

from sqlalchemy import event, create_engine
from sqlalchemy.orm import sessionmaker, Session

from core.config import settings


engine = create_engine(
    settings.DATABASE_URL,
    # SQLite does not support pool_size/max_overflow in the same way as
    # server-based DBs, but we keep connect_args for thread-safety.
    connect_args={"check_same_thread": False},
    echo=False,
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record):
    """Enable WAL mode, foreign-key enforcement, and busy_timeout on every new connection.

    busy_timeout = 5 000 ms lets concurrent writers (Core FastAPI + Worker
    process sharing the same SQLite file) wait up to 5 s for the write lock
    instead of raising an immediate ``OperationalError: database is locked``.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Session:  # type: ignore[misc]
    """FastAPI dependency — yields a scoped session, closes on teardown."""
    db = SessionLocal()
    try:
        yield db  # type: ignore[misc]
    finally:
        db.close()


@contextmanager
def get_session() -> Session:  # type: ignore[misc]
    """Context manager for non-FastAPI code (scheduler, background jobs).

    Usage::

        with get_session() as session:
            session.query(...)
            session.commit()
    """
    session = SessionLocal()
    try:
        yield session  # type: ignore[misc]
    finally:
        session.close()

