"""API and feedback-loop tests for human scam review and duplicate hash store (Phase 5)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.funnel.runner import evaluate_opportunity
from core.funnel.scam_risk.rules import compute_content_hash
from core.models.opportunity import Opportunity
from core.models.profile import Profile
from core.models.resume import Resume
from core.repositories import scam_signature_repo, scoring_repo
from core.services import opportunity_service
from core.status import OpportunityStatus


@pytest.fixture
def test_profile(db_session):
    profile = Profile(
        name="Review Test Candidate",
        email="candidate@example.com",
        salary_floor=70000,
        remote_preference="remote",
    )
    db_session.add(profile)
    db_session.flush()

    resume = Resume(
        profile_id=profile.id,
        version=1,
        original_filename="cand.pdf",
        file_path="uploads/cand.pdf",
        file_size_bytes=500,
        is_active=True,
        parsed_text="Software engineer with strong Python skills.",
    )
    db_session.add(resume)
    db_session.flush()
    return profile


class TestScamReviewEndpoints:
    def test_list_pending_scam_reviews(self, client: TestClient, db_session):
        opp1 = opportunity_service.create_opportunity(
            db_session,
            title="Pending Job 1",
            company="Co 1",
        )
        opportunity_service.transition_status(
            db_session, opp1.id, OpportunityStatus.SCAM_REVIEW_PENDING, reason="flagged", actor="funnel"
        )
        opp2 = opportunity_service.create_opportunity(
            db_session,
            title="Other Discovered Job",
            company="Co 2",
        )

        resp = client.get("/opportunities/scam-review/pending")
        assert resp.status_code == 200
        data = resp.json()

        ids = [item["id"] for item in data]
        assert opp1.id in ids
        assert opp2.id not in ids

    def test_approve_pending_opportunity(self, client: TestClient, db_session, test_profile):
        opp = opportunity_service.create_opportunity(
            db_session,
            title="Python Developer",
            company="Startup Labs",
            description="We need a talented Python developer for full-stack work.",
            profile_id=test_profile.id,
        )
        opportunity_service.transition_status(
            db_session, opp.id, OpportunityStatus.SCAM_REVIEW_PENDING, reason="flagged", actor="funnel"
        )

        resp = client.post(
            f"/opportunities/{opp.id}/scam-review/approve",
            json={"actor": "admin_reviewer"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == OpportunityStatus.RECOMMENDED

        # Verify scoring verdict was updated with relevance score
        verdict = scoring_repo.get_verdict_by_opportunity(db_session, opp.id)
        assert verdict is not None
        assert verdict.scam_verdict == "clear"
        assert verdict.relevance_score is not None

    def test_reject_pending_opportunity_stores_hash_and_rejects(
        self, client: TestClient, db_session
    ):
        desc = "Pay $150 onboarding fee for high-paying remote job."
        opp = opportunity_service.create_opportunity(
            db_session,
            title="Data Clerk",
            company="Fraudster Co",
            description=desc,
        )
        opportunity_service.transition_status(
            db_session, opp.id, OpportunityStatus.SCAM_REVIEW_PENDING, reason="flagged", actor="funnel"
        )

        resp = client.post(
            f"/opportunities/{opp.id}/scam-review/reject",
            json={"reason": "Confirmed fake check scam during review", "actor": "admin_reviewer"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == OpportunityStatus.SCAM_RISK_REJECTED

        # Verify content hash was added to confirmed scam signatures
        chash = compute_content_hash(desc)
        sig = scam_signature_repo.get_by_hash(db_session, chash)
        assert sig is not None
        assert sig.confirmed_scam is True
        assert sig.rule_name == "human_review"
        assert "fake check scam" in (sig.notes or "")

    def test_approve_wrong_status_returns_400(self, client: TestClient, db_session):
        opp = opportunity_service.create_opportunity(
            db_session,
            title="Standard Job",
            company="Corp",
        )

        resp = client.post(f"/opportunities/{opp.id}/scam-review/approve", json={})
        assert resp.status_code == 400
        assert "expected 'scam_review_pending'" in resp.json()["detail"]

    def test_not_found_returns_404(self, client: TestClient):
        resp = client.post("/opportunities/999999/scam-review/approve", json={})
        assert resp.status_code == 404


class TestDuplicateHashFeedbackLoop:
    def test_subsequent_duplicate_listing_auto_rejected_without_ai(
        self, client: TestClient, db_session, test_profile
    ):
        """Verify the closed-loop learning:

        1. Listing A arrives -> marked ambiguous -> reviewed and rejected by human.
        2. Content hash is saved to scam_content_signatures.
        3. Listing B arrives later from a different poster with identical text.
        4. Listing B is instantly auto-rejected by deterministic rules with zero AI call.
        """
        scam_text = "Work from home envelope stuffing guaranteed $2000 per week!"

        # 1. First opportunity flagged and rejected by human
        opp1 = opportunity_service.create_opportunity(
            db_session,
            title="Assembler 1",
            company="Suspicious Co 1",
            description=scam_text,
        )
        opportunity_service.transition_status(
            db_session, opp1.id, OpportunityStatus.SCAM_REVIEW_PENDING, reason="flagged", actor="funnel"
        )
        client.post(
            f"/opportunities/{opp1.id}/scam-review/reject",
            json={"reason": "Known envelope stuffing fraud", "actor": "admin"},
        )

        # 2. Second opportunity with identical text arrives into DISCOVERED
        opp2 = opportunity_service.create_opportunity(
            db_session,
            title="Assembler 2",
            company="Suspicious Co 2",
            description=scam_text,
        )

        # 3. Evaluate through funnel
        verdict = evaluate_opportunity(db_session, opp2, profile=test_profile)

        # 4. Instant deterministic auto-rejection
        assert verdict.scam_verdict == "reject"
        assert verdict.scam_reason["rule"] == "duplicate_content_hash"
        assert opp2.status == OpportunityStatus.SCAM_RISK_REJECTED
