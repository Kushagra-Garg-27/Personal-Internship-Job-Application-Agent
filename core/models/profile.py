"""Profile model and related child tables (education, skills, links)."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.models.base import Base, TimestampMixin


class Profile(TimestampMixin, Base):
    """A named user profile.

    Multiple profiles are supported (e.g. ``"default"``, ``"staging"``,
    ``"internship-focused"``) so that different configurations can coexist.
    """

    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    # ── Contact / personal ────────────────────────────────────────────
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    location: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # ── Preferences (eligibility filters in Phase 4) ──────────────────
    location_preference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    remote_preference: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # "remote", "onsite", "hybrid", "any"
    salary_floor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    role_types: Mapped[list | None] = mapped_column(
        JSON, nullable=True
    )  # e.g. ["backend", "data-eng", "ML"]

    # ── Relationships ─────────────────────────────────────────────────
    education: Mapped[list[ProfileEducation]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", lazy="selectin"
    )
    skills: Mapped[list[ProfileSkill]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", lazy="selectin"
    )
    links: Mapped[list[ProfileLink]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", lazy="selectin"
    )
    resumes: Mapped[list] = relationship(
        "Resume", back_populates="profile", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Profile id={self.id} name={self.name!r}>"


class ProfileEducation(Base):
    """A single education entry (degree) linked to a profile."""

    __tablename__ = "profile_education"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )

    degree: Mapped[str] = mapped_column(String(100), nullable=False)
    branch: Mapped[str] = mapped_column(String(100), nullable=False)
    institution: Mapped[str] = mapped_column(String(200), nullable=False)
    graduation_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    profile: Mapped[Profile] = relationship(back_populates="education")

    def __repr__(self) -> str:
        return f"<Education {self.degree} in {self.branch} @ {self.institution}>"


class ProfileSkill(Base):
    """A single skill entry linked to a profile."""

    __tablename__ = "profile_skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )

    skill_name: Mapped[str] = mapped_column(String(100), nullable=False)
    proficiency: Mapped[str | None] = mapped_column(
        String(50), nullable=True
    )  # "beginner", "intermediate", "advanced"

    profile: Mapped[Profile] = relationship(back_populates="skills")

    def __repr__(self) -> str:
        return f"<Skill {self.skill_name}>"


class ProfileLink(Base):
    """An external link (LinkedIn, GitHub, etc.) linked to a profile."""

    __tablename__ = "profile_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False
    )

    link_type: Mapped[str] = mapped_column(String(50), nullable=False)
    url: Mapped[str] = mapped_column(String(500), nullable=False)

    profile: Mapped[Profile] = relationship(back_populates="links")

    def __repr__(self) -> str:
        return f"<Link {self.link_type}: {self.url}>"
