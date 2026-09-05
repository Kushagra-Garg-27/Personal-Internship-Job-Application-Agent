"""Base abstractions for the multi-stage evaluation funnel.

Each stage implements `FunnelStage`. Stages are executed in strict sequence
by `FunnelRunner`. If any stage returns `passed=False`, execution halts immediately
and subsequent stages are never invoked.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class StageVerdict:
    """The outcome of a single funnel stage evaluation."""

    stage_name: str
    passed: bool
    reason: dict[str, Any] | None = None
    payload: dict[str, Any] | None = field(default_factory=dict)


class FunnelStage(ABC):
    """Abstract base class for all funnel stages."""

    name: str

    @abstractmethod
    def evaluate(
        self,
        opportunity: Any,
        profile: Any,
        resume_text: str | None = None,
    ) -> StageVerdict:
        """Evaluate an opportunity against a profile.

        Args:
            opportunity: The Opportunity model or duck-typed object.
            profile: The Profile model or duck-typed object.
            resume_text: Extracted text from candidate's resume (if available).

        Returns:
            StageVerdict indicating pass/fail with reason/payload.
        """
        ...


class FunnelRunner:
    """Orchestrates ordered execution of funnel stages with short-circuit enforcement."""

    def __init__(self, stages: list[FunnelStage]) -> None:
        self._stages = list(stages)

    @property
    def stages(self) -> list[FunnelStage]:
        return list(self._stages)

    def run(
        self,
        opportunity: Any,
        profile: Any,
        resume_text: str | None = None,
    ) -> list[StageVerdict]:
        """Run stages in order; short-circuit on the first failure.

        Subsequent stages are guaranteed not to run once any stage returns
        `passed=False`.
        """
        verdicts: list[StageVerdict] = []
        for stage in self._stages:
            verdict = stage.evaluate(
                opportunity=opportunity,
                profile=profile,
                resume_text=resume_text,
            )
            verdicts.append(verdict)
            if not verdict.passed:
                break
        return verdicts
