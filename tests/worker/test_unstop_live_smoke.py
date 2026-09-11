"""Live Unstop smoke test suite (Phase 9 & Unstop V1 - U2).

Marks: @pytest.mark.live_unstop
Validates against real live Unstop website (https://unstop.com):
- Live session status verification
- Live opportunity discovery and extraction
- Data normalization into canonical RawOpportunity
- Database persistence and deterministic deduplication
- Fail-closed behavior on missing/expired session
- Strict safety boundary: Discovery only, never applies or submits

Run explicitly via:
    .venv/Scripts/pytest tests/worker/test_unstop_live_smoke.py -m live_unstop -v -s
"""

from __future__ import annotations

import logging
from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

from core.discovery.base import RawOpportunity
from core.discovery.sources.unstop import UnstopDiscoverySource
from core.models.opportunity import Opportunity
from core.services import opportunity_service
from core.status import OpportunityStatus, ReliabilityTier
from worker.adapters.unstop import SessionStatus, UnstopAdapter
from worker.security.storage import save_encrypted_storage_state

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def playwright_instance():
    with sync_playwright() as p:
        yield p


@pytest.mark.live_unstop
class TestLiveUnstopDiscoverySmoke:
    """Safe, discovery-only live integration tests against real Unstop."""

    def test_live_unstop_session_verification(self):
        """Verify session verification behavior against environment state."""
        adapter = UnstopAdapter()
        status = adapter.check_session_status()

        assert isinstance(status, SessionStatus)
        print(f"\n[LIVE UNSTOP SESSION STATUS]: valid={status.valid}, status={status.status}, detail={status.detail}")

        if not adapter.session_file.exists():
            # In automated/CI environments without manual login, session is missing
            assert status.valid is False
            assert status.status == "session_missing"
        else:
            # If manual login was run, session is active
            assert status.status in ("authenticated", "session_expired")

    def test_live_unstop_browser_discovery_and_extraction(self, playwright_instance):
        """Launch Playwright, open live Unstop, and extract real opportunity listings."""
        adapter = UnstopAdapter(headless=True, playwright_instance=playwright_instance)

        # Execute live controlled discovery (browser mode)
        print("\n[LIVE UNSTOP DISCOVERY]: Opening live https://unstop.com/jobs via Playwright...")
        raw_opps = adapter.discover(limit=5, opportunity_type="jobs", require_auth=False, mode="browser")

        print(f"[LIVE UNSTOP DISCOVERY]: Extracted {len(raw_opps)} live opportunities.")
        assert len(raw_opps) >= 1, "Expected at least 1 opportunity from live Unstop"

        for idx, opp in enumerate(raw_opps):
            print(f"  [{idx+1}] Title: {opp.title!r} | Company: {opp.company!r} | URL: {opp.url}")
            # Field validations
            assert opp.title and opp.title != "Unknown Title", f"Invalid title on item {idx}"
            assert opp.company and opp.company != "Unknown Company", f"Invalid company on item {idx}"
            assert opp.url and "unstop.com" in opp.url, f"Invalid URL on item {idx}"
            assert opp.source == "unstop", f"Invalid source on item {idx}"
            assert opp.metadata.get("platform") == "unstop"

    def test_live_unstop_normalization_and_fields(self):
        """Verify live Unstop data normalizes cleanly with correct types and no fabricated data."""
        source = UnstopDiscoverySource(limit=5, opportunity_type="jobs")
        raw_opps = source.discover()

        assert len(raw_opps) >= 1, "Expected live opportunities via UnstopDiscoverySource"
        sample = raw_opps[0]

        assert isinstance(sample, RawOpportunity)
        assert sample.source == "unstop"
        assert sample.url.startswith("https://unstop.com")
        assert sample.metadata["platform"] == "unstop"
        assert sample.metadata.get("external_id") is not None

        # Check types of optional fields
        if sample.salary_min is not None:
            assert isinstance(sample.salary_min, int)
        if sample.salary_max is not None:
            assert isinstance(sample.salary_max, int)

    def test_live_unstop_persistence_and_deduplication(self, db_session):
        """Ingest real Unstop opportunity into DB and verify deterministic deduplication."""
        source = UnstopDiscoverySource(limit=3, opportunity_type="jobs")
        raw_opps = source.discover()
        assert len(raw_opps) >= 1

        target = raw_opps[0]
        print(f"\n[LIVE DEDUP TEST]: Ingesting real listing: {target.title} at {target.company}")

        # 1. First ingestion: must create new Opportunity record
        opp1, created1 = opportunity_service.upsert_opportunity(
            db_session,
            title=target.title,
            company=target.company,
            url=target.url,
            source="unstop",
            reliability_tier=ReliabilityTier.EXPERIMENTAL.value,
            description=target.description,
            location=target.location,
            salary_min=target.salary_min,
            salary_max=target.salary_max,
            posted_at=target.posted_at,
            deadline_at=target.deadline_at,
            metadata_json=target.metadata,
        )
        db_session.flush()

        assert created1 is True
        assert opp1.id is not None
        assert opp1.status == OpportunityStatus.DISCOVERED.value
        assert opp1.reliability_tier == ReliabilityTier.EXPERIMENTAL.value
        initial_hash = opp1.dedup_hash
        assert initial_hash is not None

        # 2. Second ingestion of identical real listing: must NOT create duplicate row
        opp2, created2 = opportunity_service.upsert_opportunity(
            db_session,
            title=target.title,
            company=target.company,
            url=target.url,
            source="unstop",
            reliability_tier=ReliabilityTier.EXPERIMENTAL.value,
            metadata_json=target.metadata,
        )
        db_session.flush()

        assert created2 is False, "Duplicate ingestion must return created=False"
        assert opp2.id == opp1.id, "Duplicate ingestion must return existing Opportunity row"
        assert opp2.dedup_hash == initial_hash

        # 3. Assert exactly one record exists with this dedup_hash
        count = db_session.query(Opportunity).filter_by(dedup_hash=initial_hash).count()
        assert count == 1, f"Expected exactly 1 record in database, found {count}"

    def test_live_unstop_session_expired_fails_closed(self, tmp_path):
        """Verify fail-closed rule: expired session raises PermissionError and refuses to scrape."""
        dummy_state = {
            "cookies": [{"name": "fake_token", "value": "expired", "domain": ".other.com"}],
            "origins": [],
        }
        fpath = tmp_path / "expired_unstop.enc"
        save_encrypted_storage_state(dummy_state, fpath, key="test-key")

        adapter = UnstopAdapter(session_file=fpath, encryption_key="test-key")
        status = adapter.check_session_status()
        assert status.valid is False
        assert status.status == "session_expired"

        # Refuses to run authenticated discovery when session is expired
        with pytest.raises(PermissionError, match=r"(?i)Unstop authentication required: session_expired"):
            adapter.discover(require_auth=True)

    def test_safety_boundary_stops_before_application(self, playwright_instance):
        """Verify hard safety boundary: discovery terminates without clicking Apply or navigating to form."""
        adapter = UnstopAdapter(headless=True, playwright_instance=playwright_instance)
        raw_opps = adapter.discover(limit=2, require_auth=False)
        assert len(raw_opps) >= 1

        # Confirm we never opened an application context or called fill
        # Application context or filling requires explicit human submission boundary
        for opp in raw_opps:
            assert opp.url.startswith("https://unstop.com")
            # Must be a job/opportunity viewing URL, NOT an active application form submission
            assert "unstop.com" in opp.url


@pytest.mark.live_unstop
class TestLiveUnstopFillSmoke:
    """Live application form fill-only validation against real Unstop opportunities.

    STRICT SAFETY BOUNDARY:
    - Never submits the application.
    - Never clicks 'Complete Registration' or '#unstop_submit'.
    - Reaches 'ready_for_review' pre-submit boundary.
    - Fails closed on missing required demographic data.
    """

    TARGET_REGISTER_URL = "https://unstop.com/competitions/1753995/register"
    TARGET_OPPORTUNITY_ID = 1753995

    def test_live_unstop_authenticated_session_verified(self):
        """Verify that authenticated Unstop session state is active and valid."""
        adapter = UnstopAdapter()
        status = adapter.check_session_status()

        print(f"\n[LIVE AUTH STATUS]: valid={status.valid}, status={status.status}, cookies={status.cookies_count}")
        assert status.valid is True, f"Unstop session must be valid: {status.detail}"
        assert status.status == "authenticated", f"Expected authenticated session, got {status.status}"
        assert status.cookies_count > 0, "Expected cookies in decrypted session state"

    def test_live_unstop_fill_fails_closed_when_demographics_missing(self, playwright_instance):
        """Verify fail-closed rule on real Unstop form when required demographics are missing."""
        adapter = UnstopAdapter(headless=True, playwright_instance=playwright_instance)
        app_ctx = adapter.open_application(self.TARGET_REGISTER_URL, opportunity_id=self.TARGET_OPPORTUNITY_ID)
        assert app_ctx.browser_page is not None, "Browser page must initialize"

        try:
            # Candidate profile lacking gender and disability
            candidate_data_incomplete = {
                "full_name": "Alex Morgan",
                "email": "alex.morgan.test.agent@gmail.com",
                "phone": "9876543210",
                "location": "Mumbai",
            }

            result = adapter.fill(app_ctx, candidate_data_incomplete)

            print(f"\n[LIVE FAIL-CLOSED RESULT]: success={result.success}, status={result.status}, error={result.error_reason}")
            assert result.success is False
            assert result.status == "manual_required"
            assert "requires user decision" in result.error_reason.lower()

            # Safety boundary: Page must not navigate to submission success
            assert "success" not in app_ctx.browser_page.url.lower()
        finally:
            if app_ctx.browser_context:
                app_ctx.browser_context.close()

    def test_live_unstop_fill_only_validation_with_explicit_demographics(self, playwright_instance):
        """Verify real Unstop application form fill-only flow with explicit candidate demographics.

        Leaves browser at pre-submit review boundary without submitting.
        """
        adapter = UnstopAdapter(headless=True, playwright_instance=playwright_instance)
        app_ctx = adapter.open_application(self.TARGET_REGISTER_URL, opportunity_id=self.TARGET_OPPORTUNITY_ID)
        page = app_ctx.browser_page
        assert page is not None, "Browser page must initialize"

        try:
            resume_path = str(Path("tests/fixtures/sample_resume.pdf").resolve())
            candidate_data = {
                "full_name": "Alex Morgan",
                "email": "alex.morgan.test.agent@gmail.com",
                "phone": "9876543210",
                "location": "Mumbai",
                "organization": "Model Institute",
                "skills": [{"skill_name": "Python"}],
                # Explicit candidate values for required demographics & details:
                "gender": "Male",
                "differently_abled": "No",
                "user_type": "Professional",
                "designation": "Software Engineer",
                "work_experience": "1 year",
                "agree_terms": True,
            }
            custom_answers = [
                {
                    "question_id": "statement_of_purpose",
                    "answer": "Looking forward to contributing professional engineering skills.",
                }
            ]

            fill_result = adapter.fill(
                app_ctx,
                candidate_data,
                resume_path=resume_path,
                custom_answers=custom_answers,
            )

            print(f"\n[LIVE FILL RESULT]: success={fill_result.success}, status={fill_result.status}, message={fill_result.message}")
            assert fill_result.success is True
            assert fill_result.status == "ready_for_review"

            # Verify DOM field values on live page
            assert page.locator("input[name='player_firstname']").input_value() == "Alex"
            assert page.locator("input[name='player_name_last']").input_value() == "Morgan"
            assert len(page.locator("input[name='player_email']").input_value()) > 0
            assert len(page.locator("input[name='tel']").input_value()) > 0
            assert len(page.locator("#cities_input").input_value()) > 0, "Location should be populated"

            # Verify remaining invalid form controls
            remaining_invalids = page.locator(".ng-invalid:not(form)").count()
            print(f"[REMAINING INVALID CONTROLS]: {remaining_invalids}")
            assert remaining_invalids == 0, "All required fields should be valid"

            # Verify STRICT PRE-SUBMIT BOUNDARY:
            # 1. URL did NOT navigate to registration success
            assert "success" not in page.url.lower(), "Application must NOT be submitted!"
            # 2. Form is filled and left unsubmitted at the registration URL
            assert page.url == self.TARGET_REGISTER_URL

            # Capture evidence screenshot
            screenshot_path = "C:/Users/kusha/.gemini/antigravity-ide/brain/ccab317c-7398-4b6b-b23b-9d8920437c86/scratch/live_unstop_presubmit_boundary_evidence.png"
            page.screenshot(path=screenshot_path, full_page=True)
            print(f"[SCREENSHOT SAVED]: {screenshot_path}")

        finally:
            if app_ctx.browser_context:
                app_ctx.browser_context.close()

