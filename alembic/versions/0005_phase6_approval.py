"""Phase 6 — selected_resume_id column on opportunities table.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-05
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("opportunities") as batch_op:
        batch_op.add_column(
            sa.Column(
                "selected_resume_id",
                sa.Integer(),
                sa.ForeignKey(
                    "resumes.id",
                    ondelete="SET NULL",
                    name="fk_opportunities_selected_resume_id",
                ),
                nullable=True,
            )
        )
        batch_op.create_index(
            "ix_opportunities_selected_resume_id",
            ["selected_resume_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("opportunities") as batch_op:
        batch_op.drop_index("ix_opportunities_selected_resume_id")
        batch_op.drop_column("selected_resume_id")
