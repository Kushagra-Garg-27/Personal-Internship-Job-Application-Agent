"""Re-export all models so Alembic and application code can do:

    from core.models import Base, Profile, Resume, Opportunity, ...
"""

from core.models.base import Base, TimestampMixin
from core.models.profile import Profile, ProfileEducation, ProfileLink, ProfileSkill
from core.models.resume import Resume
from core.models.opportunity import Application, Opportunity, StatusHistory
from core.models.scam_signature import ScamContentSignature
from core.models.scoring import ScoringVerdict
from core.models.message import IntegrationHealthEvent, RecruiterMessage
from core.models.notification import NotificationLog, NotificationSetting

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
    # Phase 4
    "ScoringVerdict",
    # Phase 5
    "ScamContentSignature",
    # Phase 7
    "RecruiterMessage",
    "IntegrationHealthEvent",
    # Phase 8
    "NotificationLog",
    "NotificationSetting",
]
