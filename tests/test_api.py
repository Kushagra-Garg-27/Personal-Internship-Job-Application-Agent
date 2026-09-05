"""Integration tests for the FastAPI endpoints."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from core.config import settings


FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestHealthEndpoint:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestProfileEndpoints:
    """Profile CRUD via the HTTP API."""

    def test_create_profile_minimal(self, client):
        resp = client.post("/profiles/", json={"name": "api-test"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "api-test"
        assert data["id"] is not None
        assert data["education"] == []
        assert data["skills"] == []

    def test_create_profile_with_children(self, client):
        resp = client.post(
            "/profiles/",
            json={
                "name": "api-full",
                "full_name": "Jane Doe",
                "email": "jane@test.com",
                "salary_floor": 90000,
                "role_types": ["backend", "infra"],
                "education": [
                    {
                        "degree": "B.Tech",
                        "branch": "Computer Science",
                        "institution": "IIT Bombay",
                        "graduation_year": 2025,
                    }
                ],
                "skills": [
                    {"skill_name": "Python", "proficiency": "advanced"},
                    {"skill_name": "Docker"},
                ],
                "links": [
                    {"link_type": "github", "url": "https://github.com/jane"},
                ],
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["full_name"] == "Jane Doe"
        assert data["salary_floor"] == 90000
        assert len(data["education"]) == 1
        assert data["education"][0]["branch"] == "Computer Science"
        assert len(data["skills"]) == 2
        assert len(data["links"]) == 1

    def test_create_profile_duplicate_name(self, client):
        client.post("/profiles/", json={"name": "dup"})
        resp = client.post("/profiles/", json={"name": "dup"})
        assert resp.status_code == 409

    def test_get_profile(self, client):
        create_resp = client.post("/profiles/", json={"name": "get-test"})
        pid = create_resp.json()["id"]
        resp = client.get(f"/profiles/{pid}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "get-test"

    def test_get_profile_not_found(self, client):
        resp = client.get("/profiles/99999")
        assert resp.status_code == 404

    def test_list_profiles(self, client):
        client.post("/profiles/", json={"name": "list-1"})
        client.post("/profiles/", json={"name": "list-2"})
        resp = client.get("/profiles/")
        assert resp.status_code == 200
        names = [p["name"] for p in resp.json()]
        assert "list-1" in names
        assert "list-2" in names

    def test_update_profile(self, client):
        create_resp = client.post(
            "/profiles/", json={"name": "update-test", "salary_floor": 50000}
        )
        pid = create_resp.json()["id"]

        resp = client.patch(f"/profiles/{pid}", json={"salary_floor": 75000})
        assert resp.status_code == 200
        assert resp.json()["salary_floor"] == 75000
        assert resp.json()["name"] == "update-test"  # unchanged

    def test_delete_profile(self, client):
        create_resp = client.post("/profiles/", json={"name": "del-test"})
        pid = create_resp.json()["id"]

        resp = client.delete(f"/profiles/{pid}")
        assert resp.status_code == 204

        resp = client.get(f"/profiles/{pid}")
        assert resp.status_code == 404

    def test_get_profile_field(self, client):
        create_resp = client.post(
            "/profiles/", json={"name": "field-test", "salary_floor": 60000}
        )
        pid = create_resp.json()["id"]

        resp = client.get(f"/profiles/{pid}/field/salary_floor")
        assert resp.status_code == 200
        data = resp.json()
        assert data["field_name"] == "salary_floor"
        assert data["value"] == 60000

    def test_get_profile_field_unknown(self, client):
        create_resp = client.post("/profiles/", json={"name": "field-bad"})
        pid = create_resp.json()["id"]

        resp = client.get(f"/profiles/{pid}/field/nonexistent")
        assert resp.status_code == 400


class TestResumeEndpoints:
    """Resume upload, listing, and activation via the HTTP API."""

    def _create_profile(self, client) -> int:
        resp = client.post("/profiles/", json={"name": f"resume-api-{id(self)}"})
        return resp.json()["id"]

    def test_upload_resume_docx(self, client, upload_dir):
        """Upload a DOCX file and verify it's auto-activated."""
        pid = self._create_profile(client)

        docx_path = FIXTURES_DIR / "sample_resume.docx"
        if not docx_path.exists():
            pytest.skip("Fixture not generated yet — run tests/generate_fixtures.py")

        with open(docx_path, "rb") as f:
            resp = client.post(
                f"/profiles/{pid}/resumes/",
                files={"file": ("resume.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            )
        assert resp.status_code == 201
        data = resp.json()
        assert data["version"] == 1
        assert data["is_active"] is True
        assert data["original_filename"] == "resume.docx"

    def test_upload_resume_bad_extension(self, client, upload_dir):
        pid = self._create_profile(client)
        resp = client.post(
            f"/profiles/{pid}/resumes/",
            files={"file": ("resume.txt", b"just text", "text/plain")},
        )
        assert resp.status_code == 400

    def test_upload_to_nonexistent_profile(self, client, upload_dir):
        resp = client.post(
            "/profiles/99999/resumes/",
            files={"file": ("r.pdf", b"fake", "application/pdf")},
        )
        assert resp.status_code == 404

    def test_list_resumes(self, client, upload_dir):
        pid = self._create_profile(client)

        docx_path = FIXTURES_DIR / "sample_resume.docx"
        if not docx_path.exists():
            pytest.skip("Fixture not generated")

        with open(docx_path, "rb") as f:
            client.post(
                f"/profiles/{pid}/resumes/",
                files={"file": ("r1.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            )
        with open(docx_path, "rb") as f:
            client.post(
                f"/profiles/{pid}/resumes/",
                files={"file": ("r2.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            )

        resp = client.get(f"/profiles/{pid}/resumes/")
        assert resp.status_code == 200
        resumes = resp.json()
        assert len(resumes) == 2

    def test_get_active_resume(self, client, upload_dir):
        pid = self._create_profile(client)

        docx_path = FIXTURES_DIR / "sample_resume.docx"
        if not docx_path.exists():
            pytest.skip("Fixture not generated")

        with open(docx_path, "rb") as f:
            client.post(
                f"/profiles/{pid}/resumes/",
                files={"file": ("r.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            )

        resp = client.get(f"/profiles/{pid}/resumes/active")
        assert resp.status_code == 200
        assert resp.json()["is_active"] is True

    def test_activate_specific_resume(self, client, upload_dir):
        pid = self._create_profile(client)

        docx_path = FIXTURES_DIR / "sample_resume.docx"
        if not docx_path.exists():
            pytest.skip("Fixture not generated")

        # Upload two versions
        with open(docx_path, "rb") as f:
            r1_resp = client.post(
                f"/profiles/{pid}/resumes/",
                files={"file": ("r1.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            )
        r1_id = r1_resp.json()["id"]

        with open(docx_path, "rb") as f:
            r2_resp = client.post(
                f"/profiles/{pid}/resumes/",
                files={"file": ("r2.docx", f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            )
        r2_id = r2_resp.json()["id"]

        # r2 should be active (most recently uploaded)
        active = client.get(f"/profiles/{pid}/resumes/active").json()
        assert active["id"] == r2_id

        # Activate r1 instead
        resp = client.put(f"/profiles/{pid}/resumes/{r1_id}/activate")
        assert resp.status_code == 200
        assert resp.json()["is_active"] is True

        # Verify r1 is now active
        active = client.get(f"/profiles/{pid}/resumes/active").json()
        assert active["id"] == r1_id

    def test_no_active_resume_404(self, client):
        pid = self._create_profile(client)
        resp = client.get(f"/profiles/{pid}/resumes/active")
        assert resp.status_code == 404
