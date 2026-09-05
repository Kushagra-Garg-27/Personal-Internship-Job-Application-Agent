"""ScoringVerdict model for tracking multi-stage funnel evaluation.

Stores pass/fail decisions for eligibility, relevance similarity scores,
explanations, and funnel metadata for each evaluated opportunity.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
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


class ScoringVerdict(TimestampMixin, Base):
    """The outcome of passing an opportunity through the evaluation funnel."""

    __tablename__ = "scoring_verdicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # ── Foreign keys ───────────────────────────────────────────────────
    opportunity_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("opportunities.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    profile_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("profiles.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Eligibility verdict (Stage 1) ──────────────────────────────────
    eligibility_passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    eligibility_reason: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # ── Scam / Risk verdict (Stage 2 - Phase 5) ───────────────────────
    scam_verdict: Mapped[str | None] = mapped_column(String(30), nullable=True)  # "clear", "reject", "ambiguous"
    scam_reason: Mapped[dict | None] = mapped_column(JSON, nullable=True)        # rule name, matched signals
    llm_verdict: Mapped[str | None] = mapped_column(String(30), nullable=True)   # "scam", "legitimate", "suspicious"
    llm_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)       # inspectable stated reason
    quota_deferred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Relevance score (Stage 3 - local embeddings) ──────────────────
    # NULL if eligibility or scam/risk failed — never evaluated on rejected jobs
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    relevance_explanation: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # ── Funnel metadata ────────────────────────────────────────────────
    funnel_completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # ── Relationships ──────────────────────────────────────────────────
    opportunity: Mapped["Opportunity"] = relationship(  # noqa: F821
        back_populates="scoring_verdicts",
        lazy="selectin",
    )
    profile: Mapped["Profile | None"] = relationship(  # noqa: F821
        lazy="selectin",
    )
