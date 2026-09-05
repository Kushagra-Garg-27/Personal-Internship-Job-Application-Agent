"""Resume model — versioned, with parsed text and fail-closed status."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import Base


class Resume(Base):
    """A single resume version linked to a profile.

    Resumes are append-only: uploading a new version never overwrites or
    deletes a previous one.  Exactly one resume per profile is marked
    ``is_active = True`` at any time.
    """

    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(300), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    # ── Parsed content ────────────────────────────────────────────────
    parsed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parse_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # "success", "parse_failed", "pending"

    # ── Active flag ───────────────────────────────────────────────────
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ── Timestamp ─────────────────────────────────────────────────────
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # ── Relationship ──────────────────────────────────────────────────
    profile: Mapped["Profile"] = relationship(  # noqa: F821 — resolved at runtime
        back_populates="resumes"
    )

    def __repr__(self) -> str:
        return (
            f"<Resume id={self.id} profile={self.profile_id} "
            f"v{self.version} active={self.is_active} status={self.parse_status}>"
        )
