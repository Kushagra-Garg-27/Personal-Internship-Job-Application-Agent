"""ScamContentSignature model for storing confirmed fraudulent listing body hashes.

Matches on normalized content hash allow instant, deterministic auto-rejection
of duplicate scam listings without needing LLM inference.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import Base


class ScamContentSignature(Base):
    """SHA-256 signature of confirmed fraudulent job posting content."""

    __tablename__ = "scam_content_signatures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    content_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    opportunity_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("opportunities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    rule_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confirmed_scam: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    opportunity: Mapped["Opportunity | None"] = relationship(  # noqa: F821
        lazy="selectin",
    )
