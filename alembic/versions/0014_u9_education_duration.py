"""Add explicit candidate course duration to profile education (U9).

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-17

The ``duration`` column is deliberately nullable with no default.  Absence
stays absence: the Unstop adapter treats a missing duration as REQUIRES_USER
and never infers it from degree, graduation year, institution, or the options
offered by a platform form.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("profile_education") as batch_op:
        batch_op.add_column(sa.Column("duration", sa.String(50), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("profile_education") as batch_op:
        batch_op.drop_column("duration")
