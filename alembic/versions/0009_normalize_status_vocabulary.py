"""Normalize opportunity status vocabulary to canonical values.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Normalize 'submitted' -> 'applied'
    op.execute(
        sa.text("UPDATE opportunities SET status = 'applied' WHERE status = 'submitted'")
    )
    op.execute(
        sa.text("UPDATE status_history SET old_status = 'applied' WHERE old_status = 'submitted'")
    )
    op.execute(
        sa.text("UPDATE status_history SET new_status = 'applied' WHERE new_status = 'submitted'")
    )

    # 2. Normalize 'interview' -> 'interview_scheduled'
    op.execute(
        sa.text("UPDATE opportunities SET status = 'interview_scheduled' WHERE status = 'interview'")
    )
    op.execute(
        sa.text("UPDATE status_history SET old_status = 'interview_scheduled' WHERE old_status = 'interview'")
    )
    op.execute(
        sa.text("UPDATE status_history SET new_status = 'interview_scheduled' WHERE new_status = 'interview'")
    )

    # 3. Normalize 'offered' -> 'offer_received'
    op.execute(
        sa.text("UPDATE opportunities SET status = 'offer_received' WHERE status = 'offered'")
    )
    op.execute(
        sa.text("UPDATE status_history SET old_status = 'offer_received' WHERE old_status = 'offered'")
    )
    op.execute(
        sa.text("UPDATE status_history SET new_status = 'offer_received' WHERE new_status = 'offered'")
    )


def downgrade() -> None:
    # Downgrade is a no-op as canonical values remain backward-compatible
    pass
