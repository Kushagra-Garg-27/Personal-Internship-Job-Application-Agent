"""Re-export all models so Alembic and application code can do:

    from core.models import Base, Profile, Resume, ...
"""

from core.models.base import Base, TimestampMixin
from core.models.profile import Profile, ProfileEducation, ProfileLink, ProfileSkill
from core.models.resume import Resume

__all__ = [
    "Base",
    "TimestampMixin",
    "Profile",
    "ProfileEducation",
    "ProfileLink",
    "ProfileSkill",
    "Resume",
]
