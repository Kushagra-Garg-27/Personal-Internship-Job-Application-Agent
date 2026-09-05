"""Tests for the status-transition engine — the core of Phase 2.

Covers:
- Valid transitions succeed and produce exactly one history row
- Invalid transitions raise InvalidTransitionError with no side effects
- Full lifecycle walkthrough with ordered history query
- Terminal states reject all outgoing transitions
- Re-evaluation return paths
- ORM-level validation rejects unknown status values
"""

from __future__ import annotations

import pytest

from core.models.opportunity import Opportunity
from core.repositories import opportunity_repo, status_history_repo
from core.services import opportunity_service
from core.status import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    OpportunityStatus,
)


def _make_opportunity(db_session, **overrides) -> Opportunity:
    """Helper to create a test opportunity via the service layer."""
    defaults = dict(
        title="Backend Engineer Intern",
        company="Acme Corp",
        url="https://careers.acme.com/backend-intern",
        source="greenhouse",
        reliability_tier="stable",
    )
    defaults.update(overrides)
    return opportunity_service.create_opportunity(db_session, **defaults)


class TestValidTransitions:
    """Valid status transitions succeed and log correctly."""

    def test_discovered_to_recommended(self, db_session):
        opp = _make_opportunity(db_session)
        assert opp.status == "discovered"

        updated = opportunity_service.transition_status(
            db_session, opp.id, "recommended",
            reason="high relevance score", actor="scoring_engine",
        )
        assert updated.status == "recommended"

        history = status_history_repo.get_history(db_session, opp.id)
        # Initial creation + one transition = 2 entries
        assert len(history) == 2
        assert history[1].old_status == "discovered"
        assert history[1].new_status == "recommended"
        assert history[1].reason == "high relevance score"
        assert history[1].actor == "scoring_engine"

    def test_recommended_to_ready_to_apply(self, db_session):
        opp = _make_opportunity(db_session)
        opportunity_service.transition_status(
            db_session, opp.id, "recommended", actor="system"
        )
        updated = opportunity_service.transition_status(
            db_session, opp.id, "ready_to_apply",
            reason="user approved", actor="human_approval",
        )
        assert updated.status == "ready_to_apply"

    def test_each_transition_produces_exactly_one_history_row(self, db_session):
        opp = _make_opportunity(db_session)
        history_before = status_history_repo.get_history(db_session, opp.id)
        assert len(history_before) == 1  # just the initial creation

        opportunity_service.transition_status(db_session, opp.id, "recommended")
        history_after = status_history_repo.get_history(db_session, opp.id)
        assert len(history_after) == 2  # exactly one more


class TestInvalidTransitions:
    """Invalid transitions are rejected cleanly."""

    def test_discovered_to_applied_rejected(self, db_session):
        """Can't jump from discovered straight to applied."""
        opp = _make_opportunity(db_session)
        with pytest.raises(InvalidTransitionError) as exc_info:
            opportunity_service.transition_status(db_session, opp.id, "applied")
        assert "discovered" in str(exc_info.value)
        assert "applied" in str(exc_info.value)

        # Status unchanged
        refreshed = opportunity_repo.get_opportunity(db_session, opp.id)
        assert refreshed.status == "discovered"

        # No spurious history row
        history = status_history_repo.get_history(db_session, opp.id)
        assert len(history) == 1  # only the initial creation

    def test_applied_to_discovered_rejected(self, db_session):
        """Can't go backwards from applied to discovered."""
        opp = _make_opportunity(db_session)
        opportunity_service.transition_status(db_session, opp.id, "recommended")
        opportunity_service.transition_status(db_session, opp.id, "ready_to_apply")
        opportunity_service.transition_status(db_session, opp.id, "applied")

        with pytest.raises(InvalidTransitionError):
            opportunity_service.transition_status(db_session, opp.id, "discovered")

    def test_invalid_status_value_rejected_by_orm(self, db_session):
        """ORM validation rejects completely unknown status values."""
        with pytest.raises(ValueError, match="Invalid status"):
            opportunity_repo.create_opportunity(
                db_session,
                dedup_hash="test-invalid-status",
                status="nonexistent_status",
                title="Test",
                company="Test Co",
            )

    def test_invalid_transition_does_not_create_history(self, db_session):
        """A failed transition attempt must NOT leave a history row behind."""
        opp = _make_opportunity(db_session)
        initial_count = len(status_history_repo.get_history(db_session, opp.id))

        with pytest.raises(InvalidTransitionError):
            opportunity_service.transition_status(db_session, opp.id, "accepted")

        final_count = len(status_history_repo.get_history(db_session, opp.id))
        assert final_count == initial_count


class TestTerminalStates:
    """Terminal states have no outgoing transitions."""

    @pytest.mark.parametrize("terminal_status", [
        "accepted",
        "rejected_by_recruiter",
        "withdrawn",
        "expired",
    ])
    def test_terminal_rejects_all_transitions(self, db_session, terminal_status):
        """Every terminal status should reject transitions to any other status."""
        opp = _make_opportunity(db_session, title=f"Terminal test {terminal_status}")

        # Walk the opportunity to the terminal state
        path_to_terminal = {
            "accepted": ["recommended", "ready_to_apply", "applied", "submitted",
                          "interview", "offered", "accepted"],
            "rejected_by_recruiter": ["recommended", "ready_to_apply", "applied",
                                       "submitted", "rejected_by_recruiter"],
            "withdrawn": ["recommended", "ready_to_apply", "withdrawn"],
            "expired": ["expired"],
        }
        for step in path_to_terminal[terminal_status]:
            opportunity_service.transition_status(db_session, opp.id, step)

        assert opp.status == terminal_status

        # Try transitioning to every possible status — all should fail
        for target in OpportunityStatus:
            if target.value == terminal_status:
                continue
            with pytest.raises(InvalidTransitionError):
                opportunity_service.transition_status(db_session, opp.id, target.value)


class TestReEvaluationPaths:
    """Some non-terminal states allow return transitions."""

    def test_ineligible_back_to_discovered(self, db_session):
        opp = _make_opportunity(db_session)
        opportunity_service.transition_status(db_session, opp.id, "ineligible")
        updated = opportunity_service.transition_status(
            db_session, opp.id, "discovered",
            reason="criteria changed", actor="system",
        )
        assert updated.status == "discovered"

    def test_scam_risk_back_to_discovered(self, db_session):
        opp = _make_opportunity(db_session)
        opportunity_service.transition_status(db_session, opp.id, "scam_risk_rejected")
        updated = opportunity_service.transition_status(
            db_session, opp.id, "discovered",
            reason="false positive corrected", actor="human_review",
        )
        assert updated.status == "discovered"

    def test_rejected_by_user_back_to_recommended(self, db_session):
        opp = _make_opportunity(db_session)
        opportunity_service.transition_status(db_session, opp.id, "recommended")
        opportunity_service.transition_status(db_session, opp.id, "rejected_by_user")
        updated = opportunity_service.transition_status(
            db_session, opp.id, "recommended",
            reason="user reconsidered", actor="human_approval",
        )
        assert updated.status == "recommended"


class TestFullLifecycle:
    """End-to-end lifecycle walkthrough with history verification."""

    def test_full_happy_path(self, db_session):
        """discovered → recommended → ready_to_apply → applied → submitted
        → interview → offered → accepted — with full ordered history."""
        opp = _make_opportunity(db_session)
        steps = [
            ("recommended", "high score", "scoring_engine"),
            ("ready_to_apply", "user approved", "human_approval"),
            ("applied", "submission started", "worker"),
            ("submitted", "form submitted OK", "worker_result"),
            ("interview", "recruiter email", "email_monitor"),
            ("offered", "offer received", "email_monitor"),
            ("accepted", "user accepted", "human_action"),
        ]

        for new_status, reason, actor in steps:
            opportunity_service.transition_status(
                db_session, opp.id, new_status, reason=reason, actor=actor,
            )

        assert opp.status == "accepted"

        # Query back the full history
        history = status_history_repo.get_history(db_session, opp.id)
        assert len(history) == 8  # 1 initial + 7 transitions

        # Verify order and completeness
        expected_sequence = [
            (None, "discovered"),
            ("discovered", "recommended"),
            ("recommended", "ready_to_apply"),
            ("ready_to_apply", "applied"),
            ("applied", "submitted"),
            ("submitted", "interview"),
            ("interview", "offered"),
            ("offered", "accepted"),
        ]
        for entry, (exp_old, exp_new) in zip(history, expected_sequence):
            assert entry.old_status == exp_old
            assert entry.new_status == exp_new

    def test_nonexistent_opportunity_raises(self, db_session):
        with pytest.raises(ValueError, match="not found"):
            opportunity_service.transition_status(db_session, 99999, "recommended")
