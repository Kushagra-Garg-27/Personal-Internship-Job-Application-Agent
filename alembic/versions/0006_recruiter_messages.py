"""Phase 7 — RecruiterMessage and IntegrationHealthEvent tables.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-05
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── messages table ────────────────────────────────────────────────
    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("gmail_id", sa.String(100), nullable=False),
        sa.Column("thread_id", sa.String(100), nullable=True),
        sa.Column(
            "application_id",
            sa.Integer(),
            sa.ForeignKey("applications.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("sender", sa.String(500), nullable=False),
        sa.Column("sender_domain", sa.String(200), nullable=True),
        sa.Column("subject", sa.String(1000), nullable=True),
        sa.Column("body_preview", sa.Text(), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "classification",
            sa.String(30),
            nullable=True,
            server_default="unclassified",
        ),
        sa.Column("classification_source", sa.String(20), nullable=True),
        sa.Column("classification_confidence", sa.Float(), nullable=True),
        sa.Column(
            "link_confidence",
            sa.String(20),
            nullable=True,
            server_default="none",
        ),
        sa.Column("raw_headers_json", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_messages_gmail_id", "messages", ["gmail_id"], unique=True)
    op.create_index("ix_messages_thread_id", "messages", ["thread_id"])
    op.create_index("ix_messages_application_id", "messages", ["application_id"])
    op.create_index("ix_messages_sender_domain", "messages", ["sender_domain"])
    op.create_index("ix_messages_classification", "messages", ["classification"])

    # ── integration_health table ──────────────────────────────────────
    op.create_table(
        "integration_health",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("integration_name", sa.String(100), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_integration_health_name", "integration_health", ["integration_name"]
    )


def downgrade() -> None:
    op.drop_index("ix_integration_health_name", table_name="integration_health")
    op.drop_table("integration_health")
    op.drop_index("ix_messages_classification", table_name="messages")
    op.drop_index("ix_messages_sender_domain", table_name="messages")
    op.drop_index("ix_messages_application_id", table_name="messages")
    op.drop_index("ix_messages_thread_id", table_name="messages")
    op.drop_index("ix_messages_gmail_id", table_name="messages")
    op.drop_table("messages")
