"""Phase 8 — NotificationLog and NotificationSetting tables.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-06
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── notification_logs table ──────────────────────────────────────
    op.create_table(
        "notification_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(32), nullable=False),
        sa.Column("title", sa.String(256), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_notification_logs_event_type", "notification_logs", ["event_type"])
    op.create_index("ix_notification_logs_channel", "notification_logs", ["channel"])
    op.create_index("ix_notification_logs_status", "notification_logs", ["status"])

    # ── notification_settings table ──────────────────────────────────
    op.create_table(
        "notification_settings",
        sa.Column("id", sa.Integer(), primary_key=True, default=1),
        sa.Column("whatsapp_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("enabled_events", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("notification_settings")
    op.drop_index("ix_notification_logs_status", table_name="notification_logs")
    op.drop_index("ix_notification_logs_channel", table_name="notification_logs")
    op.drop_index("ix_notification_logs_event_type", table_name="notification_logs")
    op.drop_table("notification_logs")
