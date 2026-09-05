"""Phase 5 — scam_content_signatures table and scam/risk columns on scoring_verdicts.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-05
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── scam_content_signatures ─────────────────────────────────────────
    op.create_table(
        "scam_content_signatures",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("content_hash", sa.String(64), unique=True, nullable=False, index=True),
        sa.Column(
            "opportunity_id",
            sa.Integer,
            sa.ForeignKey("opportunities.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("rule_name", sa.String(100), nullable=True),
        sa.Column(
            "confirmed_scam",
            sa.Boolean,
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # ── extend scoring_verdicts with scam/risk fields ───────────────────
    with op.batch_alter_table("scoring_verdicts") as batch_op:
        batch_op.add_column(sa.Column("scam_verdict", sa.String(30), nullable=True))
        batch_op.add_column(sa.Column("scam_reason", sa.JSON, nullable=True))
        batch_op.add_column(sa.Column("llm_verdict", sa.String(30), nullable=True))
        batch_op.add_column(sa.Column("llm_reasoning", sa.Text, nullable=True))
        batch_op.add_column(
            sa.Column("quota_deferred_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("scoring_verdicts") as batch_op:
        batch_op.drop_column("quota_deferred_at")
        batch_op.drop_column("llm_reasoning")
        batch_op.drop_column("llm_verdict")
        batch_op.drop_column("scam_reason")
        batch_op.drop_column("scam_verdict")

    op.drop_table("scam_content_signatures")
