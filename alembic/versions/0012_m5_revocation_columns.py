"""Add M5 approval revocation audit columns to the applications table.

Adds three nullable, default-free columns that record when and why a pending
approval was revoked before the background worker could claim and execute it:

    approval_revoked_at          DATETIME      – timestamp of revocation
    approval_revoked_by          VARCHAR(100)  – operator actor label
    approval_revocation_reason   VARCHAR(500)  – human-readable reason

Active revocation is defined as:
    approval_revoked_at IS NOT NULL
    AND (approved_at IS NULL OR approved_at <= approval_revoked_at)

A newer approved_at timestamp always supersedes an older revocation without
erasing the revocation record.  All columns are nullable with NO server
default.  Existing rows remain valid; absence of a value is NULL.

Revision ID: 0012
Revises:     0011
Create Date: 2026-09-14
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_COLUMNS = (
    "approval_revoked_at",
    "approval_revoked_by",
    "approval_revocation_reason",
)


def upgrade() -> None:
    with op.batch_alter_table("applications") as batch_op:
        batch_op.add_column(
            sa.Column("approval_revoked_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("approval_revoked_by", sa.String(100), nullable=True)
        )
        batch_op.add_column(
            sa.Column("approval_revocation_reason", sa.String(500), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("applications") as batch_op:
        for column in reversed(_NEW_COLUMNS):
            batch_op.drop_column(column)
