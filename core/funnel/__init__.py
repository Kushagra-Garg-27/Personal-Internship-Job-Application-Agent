"""Multi-stage evaluation funnel package.

Exports base classes, stage implementations, and execution orchestration.
"""

from core.funnel.base import FunnelRunner, FunnelStage, StageVerdict
from core.funnel.eligibility import (
    DEFAULT_ELIGIBILITY_RULES,
    EligibilityStage,
    check_deadline,
    check_degree,
    check_location,
    check_salary_floor,
)
from core.funnel.relevance import RelevanceScorer, RelevanceStage
from core.funnel.runner import (
    build_default_runner,
    evaluate_opportunity,
    run_funnel_batch,
)
from core.funnel.scam_risk import ScamRiskStage

__all__ = [
    "FunnelRunner",
    "FunnelStage",
    "StageVerdict",
    "EligibilityStage",
    "DEFAULT_ELIGIBILITY_RULES",
    "check_deadline",
    "check_degree",
    "check_location",
    "check_salary_floor",
    "ScamRiskStage",
    "RelevanceStage",
    "RelevanceScorer",
    "build_default_runner",
    "evaluate_opportunity",
    "run_funnel_batch",
]
