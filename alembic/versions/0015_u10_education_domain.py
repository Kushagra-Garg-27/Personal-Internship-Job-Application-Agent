"""Add explicit candidate education domain to profile education (U10).

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-17

The ``domain`` column is deliberately nullable with no default. Absence
stays absence: the Unstop adapter treats a missing domain as REQUIRES_USER
and never infers it from degree, branch, institution, or the options offered
by a platform dropdown form.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("profile_education") as batch_op:
        batch_op.add_column(sa.Column("domain", sa.String(100), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("profile_education") as batch_op:
        batch_op.drop_column("domain")
