"""Deterministic eligibility filter stage (Phase 4 Stage 1).

Evaluates candidate profile constraints against job opportunity details:
- Application deadline (not expired)
- Salary floor (opportunity max meets or exceeds candidate floor)
- Location & remote preference match
- Degree requirements

All checks are pure deterministic Python logic with zero AI/LLM or network calls.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any, Callable

from core.funnel.base import FunnelStage, StageVerdict


def _to_utc(dt: datetime) -> datetime:
    """Normalize a datetime to UTC timezone-aware."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def check_deadline(
    opportunity: Any, profile: Any, current_time: datetime | None = None
) -> tuple[bool, dict[str, Any] | None]:
    """Check if opportunity deadline has not yet passed.

    If no deadline is specified, passes (benefit of the doubt).
    """
    deadline_at = getattr(opportunity, "deadline_at", None)
    if deadline_at is None:
        return True, None

    now = current_time or datetime.now(timezone.utc)
    deadline_utc = _to_utc(deadline_at)
    now_utc = _to_utc(now)

    if deadline_utc < now_utc:
        return False, {
            "rule": "check_deadline",
            "detail": f"Application deadline {deadline_utc.isoformat()} has passed (now: {now_utc.isoformat()})",
        }
    return True, None


def check_salary_floor(
    opportunity: Any, profile: Any
) -> tuple[bool, dict[str, Any] | None]:
    """Check if opportunity salary meets candidate salary floor.

    If either profile salary_floor or opportunity salary_max is not set,
    passes (benefit of the doubt).
    Fails only if opportunity salary_max is strictly below candidate floor.
    """
    salary_floor = getattr(profile, "salary_floor", None) if profile else None
    salary_max = getattr(opportunity, "salary_max", None)

    if salary_floor is None or salary_max is None:
        return True, None

    if salary_max < salary_floor:
        return False, {
            "rule": "check_salary_floor",
            "detail": f"Max salary {salary_max} is below candidate floor {salary_floor}",
        }
    return True, None


def check_location(
    opportunity: Any, profile: Any
) -> tuple[bool, dict[str, Any] | None]:
    """Check location and remote work preferences against opportunity location.

    Rules:
    - Missing opportunity location passes (benefit of the doubt).
    - If remote_preference is 'remote': opportunity must include 'remote'.
    - If remote_preference is 'onsite': strictly remote opportunities fail.
    - If location_preference is set and opportunity is not remote:
      location must match candidate's preferred location (case-insensitive token overlap).
    """
    if not profile:
        return True, None

    opp_location = getattr(opportunity, "location", None)
    if not opp_location or not opp_location.strip():
        return True, None

    opp_loc_lower = opp_location.lower()
    is_remote = "remote" in opp_loc_lower

    remote_pref = (getattr(profile, "remote_preference", None) or "").lower().strip()
    if remote_pref == "remote" and not is_remote:
        return False, {
            "rule": "check_location",
            "detail": f"Candidate requires remote work, but opportunity location is '{opp_location}'",
        }
    if remote_pref == "onsite" and is_remote and "hybrid" not in opp_loc_lower:
        return False, {
            "rule": "check_location",
            "detail": f"Candidate requires onsite work, but opportunity is remote: '{opp_location}'",
        }

    loc_pref = (getattr(profile, "location_preference", None) or "").lower().strip()
    if loc_pref and loc_pref != "any" and not is_remote:
        pref_tokens = set(re.findall(r"\b[a-zA-Z0-9]+\b", loc_pref))
        opp_tokens = set(re.findall(r"\b[a-zA-Z0-9]+\b", opp_loc_lower))
        has_overlap = bool(pref_tokens & opp_tokens) or (loc_pref in opp_loc_lower) or (opp_loc_lower in loc_pref)
        if not has_overlap:
            return False, {
                "rule": "check_location",
                "detail": f"Location '{opp_location}' does not match preference '{profile.location_preference}'",
            }

    return True, None


def check_degree(
    opportunity: Any, profile: Any
) -> tuple[bool, dict[str, Any] | None]:
    """Check educational degree requirements mentioned in opportunity description.

    If description does not specify required degrees, passes.
    If a required degree (e.g. PhD, MBA, Master's) is explicitly demanded,
    verifies that candidate's education contains the requisite degree.
    """
    desc = getattr(opportunity, "description", None)
    if not desc or not desc.strip():
        return True, None

    desc_lower = desc.lower()

    # Collect candidate education degrees and branches
    edu_list = getattr(profile, "education", []) if profile else []
    degrees = [getattr(e, "degree", "").lower() for e in edu_list if getattr(e, "degree", None)]
    branches = [getattr(e, "branch", "").lower() for e in edu_list if getattr(e, "branch", None)]

    has_phd = any("phd" in d or "ph.d" in d or "doctor" in d for d in degrees)
    has_mba = any("mba" in d for d in degrees)
    has_masters = has_phd or has_mba or any("master" in d or "ms" in d or "m.tech" in d or "msc" in d for d in degrees)
    has_bachelors = has_masters or any("bachelor" in d or "bs" in d or "b.tech" in d or "b.e" in d or "bsc" in d or "ba" in d for d in degrees)

    # PhD check
    if re.search(r"\b(ph\.?d\.?|doctorate)\s+(is\s+)?(required|mandatory|must\s+have)\b", desc_lower) or \
       re.search(r"\b(requires|must\s+hold|must\s+have)\s+(a\s+)?(ph\.?d\.?|doctorate)\b", desc_lower):
        if not has_phd:
            return False, {
                "rule": "check_degree",
                "detail": "PhD or Doctorate required by job description, but not found in profile",
            }

    # MBA check
    if re.search(r"\bmba\s+(is\s+)?(required|mandatory|must\s+have)\b", desc_lower) or \
       re.search(r"\b(requires|must\s+have|must\s+hold)\s+(an\s+)?mba\b", desc_lower):
        if not has_mba:
            return False, {
                "rule": "check_degree",
                "detail": "MBA required by job description, but not found in profile",
            }

    # Master's check
    if re.search(r"\b(master['’]?s|ms|m\.tech)\s+degree\s+(is\s+)?(required|mandatory)\b", desc_lower) or \
       re.search(r"\b(requires|must\s+have)\s+(a\s+)?(master['’]?s|ms)\s+degree\b", desc_lower):
        if not has_masters:
            return False, {
                "rule": "check_degree",
                "detail": "Master's degree required by job description, but candidate holds lower or no degree",
            }

    # Branch / major check if explicitly required, e.g. "degree in computer science required"
    if re.search(r"degree\s+in\s+computer\s+science\s+(is\s+)?(required|mandatory)", desc_lower):
        cs_terms = {"computer science", "cs", "software", "computing", "informatics"}
        has_cs = any(any(term in b for term in cs_terms) for b in branches) or \
                 any(any(term in d for term in cs_terms) for d in degrees)
        if not has_cs and edu_list:
            return False, {
                "rule": "check_degree",
                "detail": "Degree in Computer Science required, but not found in candidate education branch",
            }

    return True, None


# Ordered default rules
DEFAULT_ELIGIBILITY_RULES: list[Callable[[Any, Any], tuple[bool, dict[str, Any] | None]]] = [
    check_deadline,
    check_salary_floor,
    check_location,
    check_degree,
]


class EligibilityStage(FunnelStage):
    """Stage 1: Deterministic eligibility filtering."""

    name = "eligibility"

    def __init__(
        self,
        rules: list[Callable[[Any, Any], tuple[bool, dict[str, Any] | None]]] | None = None,
    ) -> None:
        self._rules = rules or list(DEFAULT_ELIGIBILITY_RULES)

    @property
    def rules(self) -> list[Callable[[Any, Any], tuple[bool, dict[str, Any] | None]]]:
        return list(self._rules)

    def evaluate(
        self,
        opportunity: Any,
        profile: Any,
        resume_text: str | None = None,
    ) -> StageVerdict:
        """Run all eligibility rules sequentially.

        Returns pass immediately if all rules pass, or fails immediately on
        the first rule failure with rule name and detail.
        """
        rules_checked = []
        for rule_fn in self._rules:
            rule_name = getattr(rule_fn, "__name__", str(rule_fn))
            rules_checked.append(rule_name)
            passed, failure_reason = rule_fn(opportunity, profile)
            if not passed:
                return StageVerdict(
                    stage_name=self.name,
                    passed=False,
                    reason=failure_reason or {"rule": rule_name, "detail": "Rule failed"},
                    payload={"rules_checked": rules_checked, "failed_rule": rule_name},
                )

        return StageVerdict(
            stage_name=self.name,
            passed=True,
            reason=None,
            payload={"rules_checked": rules_checked},
        )
