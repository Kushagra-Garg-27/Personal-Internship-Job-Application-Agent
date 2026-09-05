"""Initial migration — profiles, education, skills, links, resumes.

Revision ID: 0001
Revises: None
Create Date: 2026-09-05
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── profiles ──────────────────────────────────────────────────────
    op.create_table(
        "profiles",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(100), unique=True, nullable=False),
        sa.Column("full_name", sa.String(200), nullable=True),
        sa.Column("email", sa.String(254), nullable=True),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("location", sa.String(200), nullable=True),
        sa.Column("location_preference", sa.String(200), nullable=True),
        sa.Column("remote_preference", sa.String(50), nullable=True),
        sa.Column("salary_floor", sa.Integer, nullable=True),
        sa.Column("role_types", sa.JSON, nullable=True),
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

    # ── profile_education ─────────────────────────────────────────────
    op.create_table(
        "profile_education",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "profile_id",
            sa.Integer,
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("degree", sa.String(100), nullable=False),
        sa.Column("branch", sa.String(100), nullable=False),
        sa.Column("institution", sa.String(200), nullable=False),
        sa.Column("graduation_year", sa.Integer, nullable=True),
    )

    # ── profile_skills ────────────────────────────────────────────────
    op.create_table(
        "profile_skills",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "profile_id",
            sa.Integer,
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("skill_name", sa.String(100), nullable=False),
        sa.Column("proficiency", sa.String(50), nullable=True),
    )

    # ── profile_links ─────────────────────────────────────────────────
    op.create_table(
        "profile_links",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "profile_id",
            sa.Integer,
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("link_type", sa.String(50), nullable=False),
        sa.Column("url", sa.String(500), nullable=False),
    )

    # ── resumes ───────────────────────────────────────────────────────
    op.create_table(
        "resumes",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "profile_id",
            sa.Integer,
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("file_path", sa.String(500), nullable=False),
        sa.Column("original_filename", sa.String(300), nullable=False),
        sa.Column("file_size_bytes", sa.Integer, nullable=False),
        sa.Column("parsed_text", sa.Text, nullable=True),
        sa.Column("parse_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column(
            "uploaded_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("resumes")
    op.drop_table("profile_links")
    op.drop_table("profile_skills")
    op.drop_table("profile_education")
    op.drop_table("profiles")
