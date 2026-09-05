"""Re-export all models so Alembic and application code can do:

    from core.models import Base, Profile, Resume, Opportunity, ...
"""

from core.models.base import Base, TimestampMixin
from core.models.profile import Profile, ProfileEducation, ProfileLink, ProfileSkill
from core.models.resume import Resume
from core.models.opportunity import Application, Opportunity, StatusHistory

__all__ = [
    "Base",
    "TimestampMixin",
    # Phase 1
    "Profile",
    "ProfileEducation",
    "ProfileLink",
    "ProfileSkill",
    "Resume",
    # Phase 2
    "Opportunity",
    "StatusHistory",
    "Application",
]
