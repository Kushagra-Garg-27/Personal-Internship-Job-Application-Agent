"""Tests for the opportunity repository layer."""

from __future__ import annotations

import pytest

from sqlalchemy.exc import IntegrityError

from core.repositories import opportunity_repo
from core.services import opportunity_service


def _make_opp(db_session, title="Test Job", company="TestCo", url="https://test.com/job"):
    """Create a test opportunity via the service layer."""
    return opportunity_service.create_opportunity(
        db_session, title=title, company=company, url=url, source="test"
    )


class TestOpportunityCRUD:
    """Basic CRUD operations."""

    def test_create_opportunity(self, db_session):
        opp = _make_opp(db_session)
        assert opp.id is not None
        assert opp.status == "discovered"
        assert opp.title == "Test Job"
        assert opp.company == "TestCo"
        assert opp.dedup_hash  # should be populated

    def test_get_opportunity(self, db_session):
        opp = _make_opp(db_session)
        fetched = opportunity_repo.get_opportunity(db_session, opp.id)
        assert fetched is not None
        assert fetched.id == opp.id

    def test_get_opportunity_not_found(self, db_session):
        assert opportunity_repo.get_opportunity(db_session, 99999) is None

    def test_get_by_dedup_hash(self, db_session):
        opp = _make_opp(db_session)
        fetched = opportunity_repo.get_by_dedup_hash(db_session, opp.dedup_hash)
        assert fetched is not None
        assert fetched.id == opp.id

    def test_delete_opportunity(self, db_session):
        opp = _make_opp(db_session)
        assert opportunity_repo.delete_opportunity(db_session, opp.id) is True
        assert opportunity_repo.get_opportunity(db_session, opp.id) is None

    def test_delete_not_found(self, db_session):
        assert opportunity_repo.delete_opportunity(db_session, 99999) is False


class TestDedupConstraint:
    """Deduplication via unique dedup_hash."""

    def test_duplicate_dedup_hash_raises(self, db_session):
        """Inserting two opportunities with the same dedup key raises IntegrityError."""
        _make_opp(db_session, title="Same Job", company="SameCo", url="https://same.com")
        with pytest.raises(IntegrityError):
            opportunity_repo.create_opportunity(
                db_session,
                dedup_hash=opportunity_service.compute_dedup_hash(
                    "SameCo", "Same Job", "https://same.com"
                ),
                status="discovered",
                title="Same Job",
                company="SameCo",
                url="https://same.com",
            )
            db_session.flush()

    def test_upsert_existing_updates(self, db_session):
        """Upsert with existing dedup hash updates fields, doesn't create new row."""
        opp1, created1 = opportunity_service.upsert_opportunity(
            db_session, title="Job X", company="Co X", url="https://x.com/job",
            description="old description",
        )
        assert created1 is True

        opp2, created2 = opportunity_service.upsert_opportunity(
            db_session, title="Job X", company="Co X", url="https://x.com/job",
            description="updated description",
        )
        assert created2 is False
        assert opp2.id == opp1.id
        assert opp2.description == "updated description"

    def test_upsert_new_creates(self, db_session):
        _, created = opportunity_service.upsert_opportunity(
            db_session, title="Brand New", company="New Co", url="https://new.com"
        )
        assert created is True

    def test_dedup_hash_is_case_insensitive(self, db_session):
        """Company/title casing differences should produce the same hash."""
        hash1 = opportunity_service.compute_dedup_hash("Acme Corp", "Backend Dev", "https://acme.com")
        hash2 = opportunity_service.compute_dedup_hash("acme corp", "backend dev", "https://acme.com")
        assert hash1 == hash2


class TestFilteredQueries:
    """Opportunity listing with filters."""

    def test_filter_by_status(self, db_session):
        opp1 = _make_opp(db_session, title="Job A", company="A", url="https://a.com")
        opp2 = _make_opp(db_session, title="Job B", company="B", url="https://b.com")
        opportunity_service.transition_status(db_session, opp1.id, "recommended")

        discovered = opportunity_repo.list_opportunities(db_session, status="discovered")
        recommended = opportunity_repo.list_opportunities(db_session, status="recommended")
        assert any(o.id == opp2.id for o in discovered)
        assert any(o.id == opp1.id for o in recommended)

    def test_filter_by_tier(self, db_session):
        _make_opp(db_session, title="Stable", company="S", url="https://s.com")
        results = opportunity_repo.list_opportunities(db_session, tier="stable")
        assert all(o.reliability_tier == "stable" for o in results)

    def test_filter_by_company(self, db_session):
        _make_opp(db_session, title="Dev", company="Google", url="https://g.com/dev")
        results = opportunity_repo.list_opportunities(db_session, company="Google")
        assert all("google" in o.company.lower() for o in results)

    def test_limit_and_offset(self, db_session):
        for i in range(5):
            _make_opp(db_session, title=f"Batch {i}", company=f"Co{i}", url=f"https://co{i}.com")
        page1 = opportunity_repo.list_opportunities(db_session, limit=2, offset=0)
        page2 = opportunity_repo.list_opportunities(db_session, limit=2, offset=2)
        assert len(page1) == 2
        assert len(page2) == 2
        assert page1[0].id != page2[0].id


class TestDirectStatusUpdateBlocked:
    """The repo layer blocks direct status updates."""

    def test_update_opportunity_rejects_status(self, db_session):
        opp = _make_opp(db_session)
        with pytest.raises(ValueError, match="Cannot update status directly"):
            opportunity_repo.update_opportunity(db_session, opp.id, status="recommended")


class TestMetadataJson:
    """The extensible metadata_json column."""

    def test_store_and_retrieve_metadata(self, db_session):
        opp = _make_opp(db_session, title="Meta", company="MetaCo", url="https://meta.com")
        opportunity_repo.update_opportunity(
            db_session, opp.id,
            metadata_json={"greenhouse_id": "12345", "tags": ["urgent", "remote"]}
        )
        fetched = opportunity_repo.get_opportunity(db_session, opp.id)
        assert fetched.metadata_json["greenhouse_id"] == "12345"
        assert "remote" in fetched.metadata_json["tags"]
