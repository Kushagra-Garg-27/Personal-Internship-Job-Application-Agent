"""Add candidate application attributes required by the Unstop flow.

Adds nullable, default-free columns to ``profiles`` for candidate attributes
that the live Unstop application form requires and that are not otherwise
representable by the existing structured profile data:

    organization, designation, work_experience, user_type, gender,
    differently_abled

All columns are nullable with NO server default.  Existing rows remain valid
and the absence of a value is preserved as ``NULL`` (never fabricated).
Platform/application terms consent is intentionally NOT added here — it is an
application-specific answer, not a permanent profile attribute.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_COLUMNS = (
    "organization",
    "designation",
    "work_experience",
    "user_type",
    "gender",
    "differently_abled",
)


def upgrade() -> None:
    with op.batch_alter_table("profiles") as batch_op:
        batch_op.add_column(sa.Column("organization", sa.String(200), nullable=True))
        batch_op.add_column(sa.Column("designation", sa.String(150), nullable=True))
        batch_op.add_column(sa.Column("work_experience", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("user_type", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("gender", sa.String(50), nullable=True))
        batch_op.add_column(sa.Column("differently_abled", sa.String(50), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("profiles") as batch_op:
        for column in reversed(_NEW_COLUMNS):
            batch_op.drop_column(column)
