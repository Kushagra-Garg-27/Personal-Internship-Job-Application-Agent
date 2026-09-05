"""RecruiterMessage and IntegrationHealthEvent models (Phase 7).

``RecruiterMessage`` stores classified recruiter emails linked back to
application records.  Gmail ID serves as the idempotency key — duplicate
ingestion is prevented at the DB level via a unique constraint.

``IntegrationHealthEvent`` tracks poller health (token refresh failures,
successful polls, etc.) so OAuth issues surface as queryable data rather
than silent gaps in coverage.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import Base, TimestampMixin


# Valid classification values
MESSAGE_CLASSIFICATIONS = {
    "interview_invite",
    "rejection",
    "offer",
    "follow_up",
    "screening_question",
    "generic",
    "unclassified",
}

# Valid link confidence levels
LINK_CONFIDENCE_LEVELS = {"high", "low", "none"}


class RecruiterMessage(TimestampMixin, Base):
    """A classified recruiter email linked to an application record.

    The ``gmail_id`` column is the idempotency key — the poller checks
    this before insertion to avoid duplicate processing of the same
    email across poll cycles.
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ── Idempotency & threading ──────────────────────────────────────
    gmail_id: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False, index=True
    )
    thread_id: Mapped[str | None] = mapped_column(
        String(100), nullable=True, index=True
    )

    # ── Application linkage ──────────────────────────────────────────
    application_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("applications.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Sender info ──────────────────────────────────────────────────
    sender: Mapped[str] = mapped_column(String(500), nullable=False)
    sender_domain: Mapped[str | None] = mapped_column(
        String(200), nullable=True, index=True
    )

    # ── Content ──────────────────────────────────────────────────────
    subject: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    body_preview: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Timestamps ───────────────────────────────────────────────────
    received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Classification ───────────────────────────────────────────────
    classification: Mapped[str | None] = mapped_column(
        String(30), nullable=True, default="unclassified", index=True
    )
    classification_source: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # "rules" or "llm"
    classification_confidence: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )

    # ── Link confidence ──────────────────────────────────────────────
    link_confidence: Mapped[str | None] = mapped_column(
        String(20), nullable=True, default="none"
    )  # "high", "low", "none"

    # ── Debug metadata ───────────────────────────────────────────────
    raw_headers_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # ── Relationships ────────────────────────────────────────────────
    application: Mapped["Application | None"] = relationship(  # noqa: F821
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<RecruiterMessage id={self.id} gmail_id={self.gmail_id!r} "
            f"classification={self.classification!r} "
            f"linked={'yes' if self.application_id else 'no'}>"
        )


class IntegrationHealthEvent(Base):
    """A single health check event for an integration (e.g., Gmail poller).

    These are append-only log entries — never updated or deleted.
    The latest event for a given integration_name tells you the current
    health status.
    """

    __tablename__ = "integration_health"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    integration_name: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )
    event_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # "token_refresh_failure", "poll_success", "poll_error", "auth_expired"
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<IntegrationHealthEvent {self.integration_name!r} "
            f"{self.event_type!r} at {self.occurred_at}>"
        )
