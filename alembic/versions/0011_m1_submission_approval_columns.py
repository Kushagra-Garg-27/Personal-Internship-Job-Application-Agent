"""Add M1 submission approval and claim columns to the applications table.

Adds six nullable, default-free columns that replace the old practice of
storing approval and claim state inside the free-text ``notes`` JSON blob:

    approval_token              VARCHAR(255)  – server-generated UUID
    approved_by                 VARCHAR(100)  – human actor label
    approved_at                 DATETIME      – confirmation timestamp
    approval_token_expires_at   DATETIME      – TTL boundary
    submission_claimed_at       DATETIME      – worker claim timestamp
    claimed_by                  VARCHAR(100)  – worker-id

All columns are nullable with NO server default.  Existing rows remain valid;
absence of a value is preserved as NULL rather than fabricated.

Revision ID: 0011
Revises:     0010
Create Date: 2026-09-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_COLUMNS = (
    "approval_token",
    "approved_by",
    "approved_at",
    "approval_token_expires_at",
    "submission_claimed_at",
    "claimed_by",
)


def upgrade() -> None:
    with op.batch_alter_table("applications") as batch_op:
        batch_op.add_column(sa.Column("approval_token", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("approved_by", sa.String(100), nullable=True))
        batch_op.add_column(sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(
            sa.Column("approval_token_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("submission_claimed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("claimed_by", sa.String(100), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("applications") as batch_op:
        for column in reversed(_NEW_COLUMNS):
            batch_op.drop_column(column)
