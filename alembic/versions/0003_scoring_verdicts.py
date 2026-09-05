"""Phase 4 — scoring_verdicts table for funnel evaluation results.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-05
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "scoring_verdicts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "opportunity_id",
            sa.Integer,
            sa.ForeignKey("opportunities.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "profile_id",
            sa.Integer,
            sa.ForeignKey("profiles.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("eligibility_passed", sa.Boolean, nullable=False),
        sa.Column("eligibility_reason", sa.JSON, nullable=True),
        sa.Column("relevance_score", sa.Float, nullable=True),
        sa.Column("relevance_explanation", sa.JSON, nullable=True),
        sa.Column("funnel_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_name", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("scoring_verdicts")
