"""Integration tests for Phase 2 FastAPI endpoints."""

from __future__ import annotations

import pytest


class TestOpportunityEndpoints:
    """Opportunity CRUD and status transition API tests."""

    def test_create_opportunity(self, client):
        resp = client.post("/opportunities/", json={
            "title": "Frontend Dev",
            "company": "WebCo",
            "url": "https://webco.com/job",
            "source": "lever",
            "reliability_tier": "stable",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Frontend Dev"
        assert data["company"] == "WebCo"
        assert data["status"] == "discovered"
        assert data["dedup_hash"]  # computed automatically

    def test_create_opportunity_minimal(self, client):
        resp = client.post("/opportunities/", json={
            "title": "Basic Job",
            "company": "BasicCo",
        })
        assert resp.status_code == 201
        assert resp.json()["reliability_tier"] == "experimental"

    def test_upsert_existing(self, client):
        """Posting the same company+title+url twice should update, not duplicate."""
        body = {
            "title": "Upsert Job",
            "company": "UpsertCo",
            "url": "https://upsert.com/job",
            "description": "v1",
        }
        r1 = client.post("/opportunities/", json=body)
        assert r1.status_code == 201
        id1 = r1.json()["id"]

        body["description"] = "v2"
        r2 = client.post("/opportunities/", json=body)
        assert r2.status_code == 201
        assert r2.json()["id"] == id1
        assert r2.json()["description"] == "v2"

    def test_get_opportunity(self, client):
        r = client.post("/opportunities/", json={"title": "Get Me", "company": "GetCo"})
        oid = r.json()["id"]

        resp = client.get(f"/opportunities/{oid}")
        assert resp.status_code == 200
        assert resp.json()["title"] == "Get Me"

    def test_get_opportunity_not_found(self, client):
        resp = client.get("/opportunities/99999")
        assert resp.status_code == 404

    def test_list_opportunities(self, client):
        client.post("/opportunities/", json={"title": "List A", "company": "A"})
        client.post("/opportunities/", json={"title": "List B", "company": "B"})
        resp = client.get("/opportunities/")
        assert resp.status_code == 200
        assert len(resp.json()) >= 2

    def test_list_with_status_filter(self, client):
        r = client.post("/opportunities/", json={"title": "Filter", "company": "FilterCo"})
        oid = r.json()["id"]

        # Transition to recommended
        client.post(f"/opportunities/{oid}/transition", json={
            "new_status": "recommended", "actor": "test"
        })

        discovered = client.get("/opportunities/?status=discovered").json()
        recommended = client.get("/opportunities/?status=recommended").json()
        assert not any(o["id"] == oid for o in discovered)
        assert any(o["id"] == oid for o in recommended)

    def test_update_opportunity(self, client):
        r = client.post("/opportunities/", json={
            "title": "Update Me", "company": "UpdateCo", "url": "https://update.com"
        })
        oid = r.json()["id"]

        resp = client.patch(f"/opportunities/{oid}", json={"location": "Remote"})
        assert resp.status_code == 200
        assert resp.json()["location"] == "Remote"

    def test_delete_opportunity(self, client):
        r = client.post("/opportunities/", json={"title": "Delete Me", "company": "DelCo"})
        oid = r.json()["id"]

        resp = client.delete(f"/opportunities/{oid}")
        assert resp.status_code == 204

        resp = client.get(f"/opportunities/{oid}")
        assert resp.status_code == 404


class TestStatusTransitionEndpoints:
    """Status transition via the API."""

    def _create_opportunity(self, client) -> int:
        r = client.post("/opportunities/", json={
            "title": f"Transition Test {id(self)}",
            "company": "TransCo",
            "url": f"https://trans.com/{id(self)}",
        })
        return r.json()["id"]

    def test_valid_transition(self, client):
        oid = self._create_opportunity(client)
        resp = client.post(f"/opportunities/{oid}/transition", json={
            "new_status": "recommended",
            "reason": "high score",
            "actor": "test",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "recommended"

    def test_invalid_transition_returns_409(self, client):
        oid = self._create_opportunity(client)
        resp = client.post(f"/opportunities/{oid}/transition", json={
            "new_status": "accepted",
        })
        assert resp.status_code == 409

    def test_transition_not_found_returns_404(self, client):
        resp = client.post("/opportunities/99999/transition", json={
            "new_status": "recommended",
        })
        assert resp.status_code == 404

    def test_get_history(self, client):
        oid = self._create_opportunity(client)
        client.post(f"/opportunities/{oid}/transition", json={
            "new_status": "recommended", "reason": "scored", "actor": "engine",
        })
        client.post(f"/opportunities/{oid}/transition", json={
            "new_status": "ready_to_apply", "reason": "approved", "actor": "user",
        })

        resp = client.get(f"/opportunities/{oid}/history")
        assert resp.status_code == 200
        history = resp.json()
        assert len(history) == 3  # initial + 2 transitions
        assert history[0]["old_status"] is None
        assert history[0]["new_status"] == "discovered"
        assert history[1]["new_status"] == "recommended"
        assert history[2]["new_status"] == "ready_to_apply"

    def test_history_not_found(self, client):
        resp = client.get("/opportunities/99999/history")
        assert resp.status_code == 404


class TestApplicationEndpoints:
    """Application attempt API tests."""

    def _create_opportunity(self, client) -> int:
        r = client.post("/opportunities/", json={
            "title": f"App Endpoint {id(self)}",
            "company": "AppEndCo",
            "url": f"https://append.com/{id(self)}",
        })
        return r.json()["id"]

    def test_create_application(self, client):
        oid = self._create_opportunity(client)
        resp = client.post(f"/opportunities/{oid}/applications", json={
            "adapter_name": "greenhouse",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["opportunity_id"] == oid
        assert data["attempt_number"] == 1
        assert data["status"] == "pending"

    def test_list_applications(self, client):
        oid = self._create_opportunity(client)
        client.post(f"/opportunities/{oid}/applications", json={})
        client.post(f"/opportunities/{oid}/applications", json={})

        resp = client.get(f"/opportunities/{oid}/applications")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_update_application(self, client):
        oid = self._create_opportunity(client)
        r = client.post(f"/opportunities/{oid}/applications", json={})
        aid = r.json()["id"]

        resp = client.patch(f"/applications/{aid}", json={
            "status": "submitted",
            "confirmation_ref": "CONF-456",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "submitted"
        assert resp.json()["confirmation_ref"] == "CONF-456"

    def test_update_application_not_found(self, client):
        resp = client.patch("/applications/99999", json={"status": "submitted"})
        assert resp.status_code == 404

    def test_create_application_for_nonexistent_opportunity(self, client):
        resp = client.post("/opportunities/99999/applications", json={})
        assert resp.status_code == 404


class TestVerdictEndpoints:
    def test_get_verdict_not_found(self, client):
        # Create opportunity
        r = client.post("/opportunities/", json={
            "title": "Data Scientist",
            "company": "AI Labs",
            "url": "https://ailabs.com/ds",
        })
        oid = r.json()["id"]

        # No verdict yet -> 404
        resp = client.get(f"/opportunities/{oid}/verdict")
        assert resp.status_code == 404

    def test_evaluate_opportunity_and_fetch_verdict(self, client):
        # Create opportunity
        r = client.post("/opportunities/", json={
            "title": "Python Developer",
            "company": "FastAPI Inc",
            "url": "https://fastapi.org/jobs/1",
            "description": "Python developer needed for REST APIs.",
        })
        oid = r.json()["id"]

        # Trigger evaluation
        eval_resp = client.post(f"/opportunities/{oid}/evaluate")
        assert eval_resp.status_code == 200
        data = eval_resp.json()
        assert data["opportunity_id"] == oid
        assert "eligibility_passed" in data
        assert data["eligibility_passed"] is True

        # Fetch verdict
        get_resp = client.get(f"/opportunities/{oid}/verdict")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == data["id"]

    def test_verdict_endpoints_nonexistent_opportunity(self, client):
        assert client.get("/opportunities/99999/verdict").status_code == 404
        assert client.post("/opportunities/99999/evaluate").status_code == 404

