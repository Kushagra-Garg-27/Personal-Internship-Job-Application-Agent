"""Unit tests for Phase 4 deterministic eligibility filter stage and rules."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from core.funnel.eligibility import (
    DEFAULT_ELIGIBILITY_RULES,
    EligibilityStage,
    check_deadline,
    check_degree,
    check_location,
    check_salary_floor,
)


class DummyOpp:
    def __init__(
        self,
        deadline_at=None,
        salary_min=None,
        salary_max=None,
        location=None,
        description=None,
    ):
        self.deadline_at = deadline_at
        self.salary_min = salary_min
        self.salary_max = salary_max
        self.location = location
        self.description = description


class DummyEdu:
    def __init__(self, degree=None, branch=None):
        self.degree = degree
        self.branch = branch


class DummyProfile:
    def __init__(
        self,
        salary_floor=None,
        remote_preference=None,
        location_preference=None,
        education=None,
    ):
        self.salary_floor = salary_floor
        self.remote_preference = remote_preference
        self.location_preference = location_preference
        self.education = education or []


class TestCheckDeadline:
    def test_deadline_future_passes(self):
        opp = DummyOpp(deadline_at=datetime.now(timezone.utc) + timedelta(days=5))
        passed, reason = check_deadline(opp, None)
        assert passed is True
        assert reason is None

    def test_deadline_past_fails(self):
        opp = DummyOpp(deadline_at=datetime.now(timezone.utc) - timedelta(days=2))
        passed, reason = check_deadline(opp, None)
        assert passed is False
        assert reason is not None
        assert reason["rule"] == "check_deadline"
        assert "has passed" in reason["detail"]

    def test_deadline_none_passes(self):
        opp = DummyOpp(deadline_at=None)
        passed, reason = check_deadline(opp, None)
        assert passed is True
        assert reason is None

    def test_deadline_naive_datetime_handled_correctly(self):
        # Naive datetime in the past
        opp = DummyOpp(deadline_at=datetime(2020, 1, 1))
        passed, reason = check_deadline(opp, None)
        assert passed is False


class TestCheckSalaryFloor:
    def test_salary_meets_floor_passes(self):
        opp = DummyOpp(salary_max=120000)
        profile = DummyProfile(salary_floor=100000)
        passed, reason = check_salary_floor(opp, profile)
        assert passed is True
        assert reason is None

    def test_salary_below_floor_fails(self):
        opp = DummyOpp(salary_max=85000)
        profile = DummyProfile(salary_floor=100000)
        passed, reason = check_salary_floor(opp, profile)
        assert passed is False
        assert reason is not None
        assert reason["rule"] == "check_salary_floor"
        assert "is below candidate floor" in reason["detail"]

    def test_salary_equal_to_floor_passes(self):
        opp = DummyOpp(salary_max=100000)
        profile = DummyProfile(salary_floor=100000)
        passed, reason = check_salary_floor(opp, profile)
        assert passed is True

    def test_missing_salary_floor_passes(self):
        opp = DummyOpp(salary_max=50000)
        profile = DummyProfile(salary_floor=None)
        passed, reason = check_salary_floor(opp, profile)
        assert passed is True

    def test_missing_opportunity_max_passes(self):
        opp = DummyOpp(salary_min=50000, salary_max=None)
        profile = DummyProfile(salary_floor=100000)
        passed, reason = check_salary_floor(opp, profile)
        assert passed is True


class TestCheckLocation:
    def test_remote_preference_matches_remote_opp(self):
        opp = DummyOpp(location="Remote, US")
        profile = DummyProfile(remote_preference="remote")
        passed, reason = check_location(opp, profile)
        assert passed is True

    def test_remote_preference_fails_onsite_opp(self):
        opp = DummyOpp(location="Austin, TX")
        profile = DummyProfile(remote_preference="remote")
        passed, reason = check_location(opp, profile)
        assert passed is False
        assert reason["rule"] == "check_location"
        assert "requires remote" in reason["detail"]

    def test_onsite_preference_fails_pure_remote_opp(self):
        opp = DummyOpp(location="Fully Remote")
        profile = DummyProfile(remote_preference="onsite")
        passed, reason = check_location(opp, profile)
        assert passed is False
        assert reason["rule"] == "check_location"

    def test_location_preference_token_match(self):
        opp = DummyOpp(location="Seattle, WA")
        profile = DummyProfile(location_preference="Seattle")
        passed, reason = check_location(opp, profile)
        assert passed is True

    def test_location_preference_mismatch(self):
        opp = DummyOpp(location="Chicago, IL")
        profile = DummyProfile(location_preference="Seattle")
        passed, reason = check_location(opp, profile)
        assert passed is False

    def test_remote_opp_satisfies_location_preference(self):
        opp = DummyOpp(location="Remote - Worldwide")
        profile = DummyProfile(location_preference="Seattle", remote_preference="any")
        passed, reason = check_location(opp, profile)
        assert passed is True

    def test_missing_opp_location_passes(self):
        opp = DummyOpp(location=None)
        profile = DummyProfile(remote_preference="remote", location_preference="Seattle")
        passed, reason = check_location(opp, profile)
        assert passed is True


class TestCheckDegree:
    def test_phd_required_fails_candidate_without_phd(self):
        opp = DummyOpp(description="Candidates must hold a PhD in Computer Science.")
        profile = DummyProfile(education=[DummyEdu(degree="B.Tech", branch="Computer Science")])
        passed, reason = check_degree(opp, profile)
        assert passed is False
        assert reason["rule"] == "check_degree"
        assert "PhD" in reason["detail"]

    def test_phd_required_passes_candidate_with_phd(self):
        opp = DummyOpp(description="A PhD is required for this research scientist position.")
        profile = DummyProfile(education=[DummyEdu(degree="PhD", branch="Machine Learning")])
        passed, reason = check_degree(opp, profile)
        assert passed is True

    def test_mba_required_fails_candidate_without_mba(self):
        opp = DummyOpp(description="An MBA is required for this product director role.")
        profile = DummyProfile(education=[DummyEdu(degree="B.S.", branch="Business")])
        passed, reason = check_degree(opp, profile)
        assert passed is False
        assert "MBA" in reason["detail"]

    def test_masters_required_passes_with_ms(self):
        opp = DummyOpp(description="Master's degree is required for this role.")
        profile = DummyProfile(education=[DummyEdu(degree="MS", branch="Data Science")])
        passed, reason = check_degree(opp, profile)
        assert passed is True

    def test_cs_degree_required_branch_check(self):
        opp = DummyOpp(description="A degree in Computer Science is required.")
        # Mechanical branch -> fails
        profile_mech = DummyProfile(education=[DummyEdu(degree="B.Tech", branch="Mechanical Engineering")])
        passed, reason = check_degree(opp, profile_mech)
        assert passed is False

        # CS branch -> passes
        profile_cs = DummyProfile(education=[DummyEdu(degree="B.Tech", branch="Computer Science")])
        passed, _ = check_degree(opp, profile_cs)
        assert passed is True

    def test_no_degree_requirements_passes(self):
        opp = DummyOpp(description="Looking for passionate engineers. Python and SQL skills.")
        profile = DummyProfile(education=[])
        passed, reason = check_degree(opp, profile)
        assert passed is True


class TestEligibilityStage:
    def test_all_rules_pass(self):
        stage = EligibilityStage()
        opp = DummyOpp(
            deadline_at=datetime.now(timezone.utc) + timedelta(days=10),
            salary_max=150000,
            location="Remote",
            description="Software Engineer role with Python.",
        )
        profile = DummyProfile(
            salary_floor=100000,
            remote_preference="remote",
            education=[DummyEdu(degree="B.Tech", branch="Computer Science")],
        )
        verdict = stage.evaluate(opp, profile)
        assert verdict.passed is True
        assert verdict.reason is None
        assert verdict.stage_name == "eligibility"
        assert len(verdict.payload["rules_checked"]) == len(DEFAULT_ELIGIBILITY_RULES)

    def test_stage_short_circuits_on_first_failure(self):
        # Create a mock rule that fails, followed by a spy rule
        fail_rule = Mock(return_value=(False, {"rule": "fail_rule", "detail": "Stopped here"}))
        fail_rule.__name__ = "fail_rule"

        spy_rule = Mock(return_value=(True, None))
        spy_rule.__name__ = "spy_rule"

        stage = EligibilityStage(rules=[fail_rule, spy_rule])
        opp = DummyOpp()
        profile = DummyProfile()

        verdict = stage.evaluate(opp, profile)
        assert verdict.passed is False
        assert verdict.reason["rule"] == "fail_rule"
        fail_rule.assert_called_once()
        spy_rule.assert_not_called()
