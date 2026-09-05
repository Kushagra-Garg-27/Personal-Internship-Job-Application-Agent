"""Phase 10 — Recruiter Response Loop columns on messages table.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("messages", sa.Column("suggested_reply", sa.Text(), nullable=True))
    op.add_column("messages", sa.Column("draft_id", sa.String(100), nullable=True))
    op.add_column("messages", sa.Column("action_taken", sa.String(50), nullable=True))
    op.add_column(
        "messages",
        sa.Column("action_taken_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_messages_action_taken", "messages", ["action_taken"])


def downgrade() -> None:
    op.drop_index("ix_messages_action_taken", table_name="messages")
    op.drop_column("messages", "action_taken_at")
    op.drop_column("messages", "action_taken")
    op.drop_column("messages", "draft_id")
    op.drop_column("messages", "suggested_reply")
