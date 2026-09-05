"""Opportunity, StatusHistory, and Application models.

The ``opportunities`` table is the central entity — one row per discovered
job/internship listing.  It doubles as the ``ready_to_apply`` job queue
(per §5.1): an opportunity reaching that status IS the handoff signal
to the future Browser Automation Worker.

Status transitions must go through the service layer, never via direct
column writes, to ensure the ``status_history`` audit trail stays honest.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from core.models.base import Base, TimestampMixin
from core.status import ALL_STATUSES, ALL_TIERS


class Opportunity(TimestampMixin, Base):
    """A discovered job/internship listing tracked through its full lifecycle."""

    __tablename__ = "opportunities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ── Deduplication ─────────────────────────────────────────────────
    dedup_hash: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )

    # ── Status & tier ─────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="discovered", index=True
    )
    reliability_tier: Mapped[str] = mapped_column(
        String(20), nullable=False, default="experimental"
    )

    # ── Listing content ───────────────────────────────────────────────
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    company: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)
    salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    salary_max: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ── Dates ─────────────────────────────────────────────────────────
    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deadline_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ── Scoring link (Phase 4) ────────────────────────────────────────
    profile_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True
    )

    # ── Target Resume link (Phase 6) ──────────────────────────────────
    selected_resume_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("resumes.id", ondelete="SET NULL"), nullable=True
    )

    # ── Extensible metadata ───────────────────────────────────────────
    metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # ── Relationships ─────────────────────────────────────────────────
    profile: Mapped["Profile"] = relationship(  # noqa: F821
        lazy="selectin",
    )
    selected_resume: Mapped["Resume | None"] = relationship(  # noqa: F821
        foreign_keys=[selected_resume_id],
        lazy="selectin",
    )
    status_history: Mapped[list[StatusHistory]] = relationship(
        back_populates="opportunity",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="StatusHistory.changed_at",
    )
    applications: Mapped[list[Application]] = relationship(
        back_populates="opportunity",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    scoring_verdicts: Mapped[list["ScoringVerdict"]] = relationship(  # noqa: F821
        back_populates="opportunity",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    # ── ORM-level validation ──────────────────────────────────────────
    @validates("status")
    def _validate_status(self, _key: str, value: str) -> str:
        if value not in ALL_STATUSES:
            raise ValueError(
                f"Invalid status {value!r}. Must be one of: {sorted(ALL_STATUSES)}"
            )
        return value

    @validates("reliability_tier")
    def _validate_tier(self, _key: str, value: str) -> str:
        if value not in ALL_TIERS:
            raise ValueError(
                f"Invalid reliability_tier {value!r}. Must be one of: {sorted(ALL_TIERS)}"
            )
        return value

    def __repr__(self) -> str:
        return (
            f"<Opportunity id={self.id} status={self.status!r} "
            f"company={self.company!r} title={self.title!r}>"
        )


class StatusHistory(Base):
    """Immutable audit record for every status transition on an opportunity.

    Written atomically alongside the ``opportunities.status`` update by
    the service layer — never inserted independently.
    """

    __tablename__ = "status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    old_status: Mapped[str | None] = mapped_column(
        String(30), nullable=True
    )  # None for initial insert
    new_status: Mapped[str] = mapped_column(String(30), nullable=False)

    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    actor: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )  # e.g. "discovery", "human_approval", "worker_result", "system_expiry"

    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # ── Relationship ──────────────────────────────────────────────────
    opportunity: Mapped[Opportunity] = relationship(back_populates="status_history")

    def __repr__(self) -> str:
        return (
            f"<StatusHistory {self.old_status!r} → {self.new_status!r} "
            f"at {self.changed_at}>"
        )


class Application(TimestampMixin, Base):
    """A submission attempt for an opportunity.

    Multiple attempts per opportunity are supported (e.g., a failed
    submission retried later).  ``attempt_number`` tracks the sequence.
    """

    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    opportunity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    resume_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("resumes.id", ondelete="SET NULL"),
        nullable=True,
    )

    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # ── Submission state ──────────────────────────────────────────────
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending"
    )  # "pending", "submitted", "failed", "confirmed"
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    confirmation_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    adapter_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Relationships ─────────────────────────────────────────────────
    opportunity: Mapped[Opportunity] = relationship(back_populates="applications")
    resume: Mapped["Resume"] = relationship(lazy="selectin")  # noqa: F821

    def __repr__(self) -> str:
        return (
            f"<Application id={self.id} opp={self.opportunity_id} "
            f"attempt={self.attempt_number} status={self.status!r}>"
        )
