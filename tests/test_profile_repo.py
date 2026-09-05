"""Tests for the profile repository layer."""

from __future__ import annotations

import pytest

from core.repositories import profile_repo
from core.models.profile import Profile


class TestProfileCRUD:
    """Profile create / read / update / delete operations."""

    def test_create_profile(self, db_session):
        profile = profile_repo.create_profile(
            db_session,
            name="default",
            full_name="Jane Doe",
            email="jane@example.com",
            phone="555-0100",
            location="San Francisco, CA",
            location_preference="Bay Area",
            remote_preference="hybrid",
            salary_floor=80000,
            role_types=["backend", "ML"],
        )
        assert profile.id is not None
        assert profile.name == "default"
        assert profile.full_name == "Jane Doe"
        assert profile.salary_floor == 80000
        assert profile.role_types == ["backend", "ML"]

    def test_get_profile(self, db_session):
        created = profile_repo.create_profile(db_session, name="test-get")
        fetched = profile_repo.get_profile(db_session, created.id)
        assert fetched is not None
        assert fetched.id == created.id

    def test_get_profile_not_found(self, db_session):
        assert profile_repo.get_profile(db_session, 99999) is None

    def test_get_profile_by_name(self, db_session):
        profile_repo.create_profile(db_session, name="by-name-test")
        fetched = profile_repo.get_profile_by_name(db_session, "by-name-test")
        assert fetched is not None
        assert fetched.name == "by-name-test"

    def test_list_profiles(self, db_session):
        profile_repo.create_profile(db_session, name="list-a")
        profile_repo.create_profile(db_session, name="list-b")
        profiles = profile_repo.list_profiles(db_session)
        names = [p.name for p in profiles]
        assert "list-a" in names
        assert "list-b" in names

    def test_update_profile_fields(self, db_session):
        profile = profile_repo.create_profile(
            db_session, name="update-me", salary_floor=50000
        )
        updated = profile_repo.update_profile(
            db_session, profile.id, salary_floor=75000, location="NYC"
        )
        assert updated is not None
        assert updated.salary_floor == 75000
        assert updated.location == "NYC"
        assert updated.name == "update-me"  # unchanged

    def test_update_profile_not_found(self, db_session):
        assert profile_repo.update_profile(db_session, 99999, name="x") is None

    def test_delete_profile(self, db_session):
        profile = profile_repo.create_profile(db_session, name="delete-me")
        assert profile_repo.delete_profile(db_session, profile.id) is True
        assert profile_repo.get_profile(db_session, profile.id) is None

    def test_delete_profile_not_found(self, db_session):
        assert profile_repo.delete_profile(db_session, 99999) is False

    def test_multiple_named_profiles_coexist(self, db_session):
        """Verify multi-profile support — different named profiles coexist."""
        p1 = profile_repo.create_profile(
            db_session, name="default", salary_floor=100000
        )
        p2 = profile_repo.create_profile(
            db_session, name="staging", salary_floor=50000
        )
        assert p1.id != p2.id
        assert profile_repo.get_profile(db_session, p1.id).salary_floor == 100000
        assert profile_repo.get_profile(db_session, p2.id).salary_floor == 50000


class TestProfileChildren:
    """Education, skills, and links child entity operations."""

    def test_add_education(self, db_session):
        profile = profile_repo.create_profile(db_session, name="edu-test")
        edu = profile_repo.add_education(
            db_session,
            profile.id,
            degree="B.Tech",
            branch="Computer Science",
            institution="IIT Delhi",
            graduation_year=2025,
        )
        assert edu.id is not None
        assert edu.profile_id == profile.id
        assert edu.branch == "Computer Science"

    def test_add_skill(self, db_session):
        profile = profile_repo.create_profile(db_session, name="skill-test")
        skill = profile_repo.add_skill(
            db_session,
            profile.id,
            skill_name="Python",
            proficiency="advanced",
        )
        assert skill.skill_name == "Python"
        assert skill.proficiency == "advanced"

    def test_add_link(self, db_session):
        profile = profile_repo.create_profile(db_session, name="link-test")
        link = profile_repo.add_link(
            db_session,
            profile.id,
            link_type="github",
            url="https://github.com/janedoe",
        )
        assert link.link_type == "github"

    def test_delete_cascade_removes_children(self, db_session):
        """Deleting a profile should cascade-delete all children."""
        profile = profile_repo.create_profile(db_session, name="cascade-test")
        profile_repo.add_education(
            db_session, profile.id,
            degree="M.S.", branch="AI", institution="Stanford",
        )
        profile_repo.add_skill(db_session, profile.id, skill_name="PyTorch")
        profile_repo.add_link(
            db_session, profile.id, link_type="linkedin", url="https://linkedin.com/in/x"
        )

        profile_repo.delete_profile(db_session, profile.id)
        db_session.flush()

        # Profile and all children should be gone
        assert profile_repo.get_profile(db_session, profile.id) is None


class TestProfileFieldAccess:
    """Field-level query via get_profile_field."""

    def test_get_direct_field(self, db_session):
        profile = profile_repo.create_profile(
            db_session, name="field-test", salary_floor=60000
        )
        assert profile_repo.get_profile_field(db_session, profile.id, "salary_floor") == 60000
        assert profile_repo.get_profile_field(db_session, profile.id, "name") == "field-test"

    def test_get_child_collection(self, db_session):
        profile = profile_repo.create_profile(db_session, name="field-children")
        profile_repo.add_skill(db_session, profile.id, skill_name="FastAPI")
        skills = profile_repo.get_profile_field(db_session, profile.id, "skills")
        assert len(skills) == 1
        assert skills[0].skill_name == "FastAPI"

    def test_get_unknown_field_raises(self, db_session):
        profile = profile_repo.create_profile(db_session, name="field-bad")
        with pytest.raises(ValueError, match="Unknown profile field"):
            profile_repo.get_profile_field(db_session, profile.id, "nonexistent")

    def test_get_field_on_missing_profile(self, db_session):
        assert profile_repo.get_profile_field(db_session, 99999, "name") is None
