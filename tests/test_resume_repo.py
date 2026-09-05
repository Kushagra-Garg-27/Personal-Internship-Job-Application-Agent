"""Tests for the resume repository layer."""

from __future__ import annotations

from core.repositories import profile_repo, resume_repo


class TestResumeVersioning:
    """Resume version auto-increment and immutability."""

    def _make_profile(self, db_session, name="resume-test"):
        return profile_repo.create_profile(db_session, name=name)

    def _make_resume(self, db_session, profile_id, **overrides):
        version = resume_repo.get_next_version(db_session, profile_id)
        defaults = dict(
            profile_id=profile_id,
            version=version,
            file_path=f"/fake/path/v{version}.pdf",
            original_filename=f"resume_v{version}.pdf",
            file_size_bytes=1024,
            parsed_text="Some parsed text content for testing purposes.",
            parse_status="success",
            is_active=False,
        )
        defaults.update(overrides)
        return resume_repo.create_resume(db_session, **defaults)

    def test_first_version_is_one(self, db_session):
        profile = self._make_profile(db_session, "ver-1")
        assert resume_repo.get_next_version(db_session, profile.id) == 1

    def test_version_auto_increments(self, db_session):
        profile = self._make_profile(db_session, "ver-inc")
        self._make_resume(db_session, profile.id)
        assert resume_repo.get_next_version(db_session, profile.id) == 2
        self._make_resume(db_session, profile.id)
        assert resume_repo.get_next_version(db_session, profile.id) == 3

    def test_uploading_new_version_preserves_old(self, db_session):
        """Uploading a new version must NOT destroy previous versions."""
        profile = self._make_profile(db_session, "preserve")
        r1 = self._make_resume(db_session, profile.id)
        r2 = self._make_resume(db_session, profile.id)

        versions = resume_repo.list_resume_versions(db_session, profile.id)
        assert len(versions) == 2
        assert {r.id for r in versions} == {r1.id, r2.id}

    def test_get_resume(self, db_session):
        profile = self._make_profile(db_session, "get-resume")
        r = self._make_resume(db_session, profile.id)
        fetched = resume_repo.get_resume(db_session, r.id)
        assert fetched is not None
        assert fetched.version == 1


class TestActiveResume:
    """Active/default resume flag management."""

    def _make_profile(self, db_session, name="active-test"):
        return profile_repo.create_profile(db_session, name=name)

    def _make_resume(self, db_session, profile_id):
        version = resume_repo.get_next_version(db_session, profile_id)
        return resume_repo.create_resume(
            db_session,
            profile_id=profile_id,
            version=version,
            file_path=f"/fake/v{version}.pdf",
            original_filename=f"r{version}.pdf",
            file_size_bytes=512,
            parsed_text="Test content " * 10,
            parse_status="success",
            is_active=False,
        )

    def test_no_active_resume_initially(self, db_session):
        profile = self._make_profile(db_session, "no-active")
        assert resume_repo.get_active_resume(db_session, profile.id) is None

    def test_set_active_resume(self, db_session):
        profile = self._make_profile(db_session, "set-active")
        r1 = self._make_resume(db_session, profile.id)
        activated = resume_repo.set_active_resume(db_session, profile.id, r1.id)
        assert activated is not None
        assert activated.is_active is True

    def test_set_active_deactivates_previous(self, db_session):
        """Setting a new active resume must deactivate the old one."""
        profile = self._make_profile(db_session, "deactivate")
        r1 = self._make_resume(db_session, profile.id)
        r2 = self._make_resume(db_session, profile.id)

        resume_repo.set_active_resume(db_session, profile.id, r1.id)
        resume_repo.set_active_resume(db_session, profile.id, r2.id)

        assert resume_repo.get_active_resume(db_session, profile.id).id == r2.id

        # r1 should no longer be active
        refreshed_r1 = resume_repo.get_resume(db_session, r1.id)
        assert refreshed_r1.is_active is False

    def test_set_active_wrong_profile(self, db_session):
        """Cannot activate a resume that belongs to a different profile."""
        p1 = self._make_profile(db_session, "p1")
        p2 = self._make_profile(db_session, "p2")
        r1 = self._make_resume(db_session, p1.id)

        result = resume_repo.set_active_resume(db_session, p2.id, r1.id)
        assert result is None

    def test_set_active_nonexistent_resume(self, db_session):
        profile = self._make_profile(db_session, "nonexistent-r")
        assert resume_repo.set_active_resume(db_session, profile.id, 99999) is None

    def test_list_versions_ordered_desc(self, db_session):
        profile = self._make_profile(db_session, "ordered")
        self._make_resume(db_session, profile.id)
        self._make_resume(db_session, profile.id)
        self._make_resume(db_session, profile.id)

        versions = resume_repo.list_resume_versions(db_session, profile.id)
        version_numbers = [r.version for r in versions]
        assert version_numbers == [3, 2, 1]  # newest first
