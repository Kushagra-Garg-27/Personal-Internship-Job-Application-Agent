"""Milestone M2A offline test suite for Unstop submission control resolution and confirmation detection.

Validates the fail-closed resolver and confirmation detector using real DOM (headless Playwright)
against offline synthetic HTML fixtures.

SAFETY GUARANTEES:
- Purely offline: zero network requests.
- No real Unstop page, no real login session, no external submissions.
- No live_unstop test marker.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
import textwrap
import pytest
from unittest.mock import MagicMock, patch
from playwright.sync_api import sync_playwright

from core.status import ReliabilityTier
from worker.adapters.base import ApplicationContext, SubmissionStatus
from worker.adapters.submission_controls import (
    SubmissionControlClassification,
    SubmissionControlResolutionStatus,
    classify_submission_control,
    inspect_submission_controls,
    normalize_control_text,
    resolve_final_submission_control,
    revalidate_handle_before_click,
    sanitize_attribute_value,
    FINAL_SUBMIT_TEXTS,
)
from worker.adapters.unstop import (
    UnstopAdapter,
    evaluate_submission_confirmation,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "m2_controls"


@pytest.fixture(scope="module")
def playwright_browser():
    """Module-scoped headless browser for offline synthetic DOM testing."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture()
def page(playwright_browser):
    """Isolated page per test."""
    pg = playwright_browser.new_page()
    yield pg
    pg.close()


def load_fixture(fixture_name: str) -> str:
    """Read HTML fixture content from disk."""
    path = FIXTURES_DIR / fixture_name
    assert path.is_file(), f"Fixture file not found: {path}"
    return path.read_text(encoding="utf-8")


def set_offline_page(page, html: str, url: str = "https://unstop.com/test"):
    """Load HTML content at a synthetic URL completely offline via Playwright route interception."""
    page.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    page.goto(url)


# ── 1. Pure Semantic Classifier Unit Tests ─────────────────────────────────

class TestSemanticClassifier:

    def test_normalize_control_text(self):
        assert normalize_control_text("   Submit   Application \n\t ") == "submit application"
        assert normalize_control_text("COMPLETE REGISTRATION") == "complete registration"
        assert normalize_control_text("") == ""
        assert normalize_control_text(None) == ""

    def test_classify_final_submit_exact_phrases(self):
        from worker.adapters.submission_controls import SubmissionControlCandidate

        c1 = SubmissionControlCandidate(
            candidate_id="c1",
            tag_name="button",
            text="submit application",
            raw_text="Submit Application",
            is_visible=True,
            is_disabled=False,
            is_in_active_form=True,
        )
        assert classify_submission_control(c1) == SubmissionControlClassification.FINAL_SUBMIT

        c2 = SubmissionControlCandidate(
            candidate_id="c2",
            tag_name="button",
            text="complete registration",
            raw_text="Complete Registration",
            is_visible=True,
            is_disabled=False,
            is_in_active_form=True,
        )
        assert classify_submission_control(c2) == SubmissionControlClassification.FINAL_SUBMIT

    def test_classify_intermediate_phrases_rejected(self):
        from worker.adapters.submission_controls import SubmissionControlCandidate

        intermediate_texts = [
            "next",
            "continue",
            "save",
            "save and continue",
            "save & next",
            "preview",
            "review",
            "back",
            "previous",
            "edit",
            "upload resume",
            "add experience",
            "verify otp",
            "send otp",
            "proceed to questions",
        ]

        for text in intermediate_texts:
            cand = SubmissionControlCandidate(
                candidate_id="c_inter",
                tag_name="button",
                text=normalize_control_text(text),
                raw_text=text,
                is_visible=True,
                is_disabled=False,
                is_in_active_form=True,
            )
            assert classify_submission_control(cand) == SubmissionControlClassification.INTERMEDIATE, (
                f"Expected '{text}' to be classified as INTERMEDIATE"
            )

    def test_classify_rejects_controls_outside_active_form(self):
        from worker.adapters.submission_controls import SubmissionControlCandidate

        cand = SubmissionControlCandidate(
            candidate_id="c_outside",
            tag_name="button",
            text="submit application",
            raw_text="Submit Application",
            is_visible=True,
            is_disabled=False,
            is_in_active_form=False,  # Outside active form
        )
        assert classify_submission_control(cand) == SubmissionControlClassification.REJECTED

    def test_classify_generic_submit_without_evidence_is_unknown(self):
        from worker.adapters.submission_controls import SubmissionControlCandidate

        cand = SubmissionControlCandidate(
            candidate_id="c_generic",
            tag_name="input",
            text="submit",
            raw_text="Submit",
            is_visible=True,
            is_disabled=False,
            control_type="submit",
            is_in_active_form=True,
        )
        assert classify_submission_control(cand) == SubmissionControlClassification.UNKNOWN


# ── 2. Real DOM Resolution Tests Across 10 Synthetic Fixtures ──────────────

class TestControlResolutionFixtures:

    def test_fixture_01_exact_submit_application(self, page):
        """1. One exact 'Submit Application' final control resolves cleanly."""
        page.set_content(load_fixture("01_exact_submit_application.html"))
        resolution = resolve_final_submission_control(page)

        assert resolution.status == SubmissionControlResolutionStatus.EXACTLY_ONE_FINAL
        assert resolution.verified_control is not None
        assert resolution.verified_control.text == "submit application"
        assert resolution.locator is not None
        if hasattr(resolution.locator, "count"):
            assert resolution.locator.count() == 1
        elif hasattr(resolution.locator, "is_visible"):
            assert resolution.locator.is_visible()

    def test_fixture_02_exact_complete_registration(self, page):
        """2. One exact 'Complete Registration' final control resolves cleanly."""
        page.set_content(load_fixture("02_exact_complete_registration.html"))
        resolution = resolve_final_submission_control(page)

        assert resolution.status == SubmissionControlResolutionStatus.EXACTLY_ONE_FINAL
        assert resolution.verified_control is not None
        assert resolution.verified_control.text == "complete registration"
        assert resolution.verified_control.element_id == "unstop_submit"
        assert resolution.locator is not None

    def test_fixture_03_intermediate_controls_only(self, page):
        """3. Save, Continue, Preview, Back controls only -> fails closed."""
        page.set_content(load_fixture("03_intermediate_controls_only.html"))
        resolution = resolve_final_submission_control(page)

        assert resolution.status == SubmissionControlResolutionStatus.ONLY_INTERMEDIATE_OR_UNKNOWN
        assert resolution.verified_control is None
        assert resolution.locator is None

    def test_fixture_04_generic_submit_btn_intermediate(self, page):
        """4. Generic .submit-btn used for intermediate step -> fails closed."""
        page.set_content(load_fixture("04_generic_submit_btn_intermediate.html"))
        resolution = resolve_final_submission_control(page)

        assert resolution.status == SubmissionControlResolutionStatus.ONLY_INTERMEDIATE_OR_UNKNOWN
        assert resolution.verified_control is None
        assert resolution.locator is None

    def test_fixture_05_multiple_final_controls(self, page):
        """5. Multiple apparent final-submit controls -> fails closed as MULTIPLE_FINAL."""
        page.set_content(load_fixture("05_multiple_final_controls.html"))
        resolution = resolve_final_submission_control(page)

        assert resolution.status == SubmissionControlResolutionStatus.MULTIPLE_FINAL
        assert resolution.verified_control is None
        assert resolution.locator is None
        assert "multiple_final_controls" in resolution.diagnostics

    def test_fixture_06_hidden_final_visible_continue(self, page):
        """6. Hidden final submit + visible Continue -> fails closed as FINAL_HIDDEN."""
        page.set_content(load_fixture("06_hidden_final_visible_continue.html"))
        resolution = resolve_final_submission_control(page)

        assert resolution.status == SubmissionControlResolutionStatus.FINAL_HIDDEN
        assert resolution.verified_control is None
        assert resolution.locator is None

    def test_fixture_07_disabled_final_control(self, page):
        """7. Disabled final control -> fails closed as FINAL_DISABLED."""
        page.set_content(load_fixture("07_disabled_final_control.html"))
        resolution = resolve_final_submission_control(page)

        assert resolution.status == SubmissionControlResolutionStatus.FINAL_DISABLED
        assert resolution.verified_control is None
        assert resolution.locator is None

    def test_fixture_08_generic_input_submit_alone(self, page):
        """8. Generic input[type='submit'] without semantic evidence -> fails closed."""
        page.set_content(load_fixture("08_generic_input_submit_alone.html"))
        resolution = resolve_final_submission_control(page)

        assert resolution.status == SubmissionControlResolutionStatus.ONLY_INTERMEDIATE_OR_UNKNOWN
        assert resolution.verified_control is None
        assert resolution.locator is None

    def test_fixture_09_final_control_outside_form(self, page):
        """9. Final control outside active application form -> rejected, fails closed."""
        page.set_content(load_fixture("09_final_control_outside_form.html"))
        resolution = resolve_final_submission_control(page)

        # The button in nav is rejected; inside form there is only 'Next' (intermediate)
        assert resolution.status == SubmissionControlResolutionStatus.ONLY_INTERMEDIATE_OR_UNKNOWN
        assert resolution.verified_control is None
        assert resolution.locator is None

    def test_fixture_10_whitespace_case_normalization(self, page):
        """10. Whitespace/case normalization remains unambiguous and resolves."""
        page.set_content(load_fixture("10_whitespace_case_normalization.html"))
        resolution = resolve_final_submission_control(page)

        assert resolution.status == SubmissionControlResolutionStatus.EXACTLY_ONE_FINAL
        assert resolution.verified_control is not None
        assert resolution.verified_control.text == "submit application"
        assert resolution.locator is not None

    def test_fixture_11_header_search_form_before_application(self, page):
        """11. Header search form before application form: application form resolves, search is ignored."""
        page.set_content(load_fixture("11_header_search_form_before_application.html"))
        resolution = resolve_final_submission_control(page)

        assert resolution.status == SubmissionControlResolutionStatus.EXACTLY_ONE_FINAL
        assert resolution.verified_control is not None
        assert resolution.verified_control.text == "submit application"
        assert resolution.verified_control.is_in_active_form is True
        assert resolution.locator is not None

    def test_fixture_12_newsletter_form_after_application(self, page):
        """12. Newsletter form after application form: application form resolves, newsletter is ignored."""
        page.set_content(load_fixture("12_newsletter_form_after_application.html"))
        resolution = resolve_final_submission_control(page)

        assert resolution.status == SubmissionControlResolutionStatus.EXACTLY_ONE_FINAL
        assert resolution.verified_control is not None
        assert resolution.verified_control.text == "complete registration"
        assert resolution.verified_control.is_in_active_form is True
        assert resolution.locator is not None

    def test_fixture_13_two_plausible_application_forms(self, page):
        """13. Two competing plausible application forms: ambiguous, fails closed with 0 final resolved."""
        page.set_content(load_fixture("13_two_plausible_application_forms.html"))
        resolution = resolve_final_submission_control(page)

        # Ambiguous multiple application forms -> none treated as active -> 0 final resolved
        assert resolution.status in (
            SubmissionControlResolutionStatus.ZERO_FINAL,
            SubmissionControlResolutionStatus.ONLY_INTERMEDIATE_OR_UNKNOWN,
        )
        assert resolution.verified_control is None
        assert resolution.locator is None

    def test_duplicate_ids_fail_closed(self, page):
        """Duplicate element IDs for final submit control fail closed."""
        page.set_content("""
        <form id="app-form" action="/apply" method="POST">
          <button type="button" id="unstop_submit">Submit Application</button>
          <button type="button" id="unstop_submit">Submit Application</button>
        </form>
        """)
        resolution = resolve_final_submission_control(page)

        assert resolution.status == SubmissionControlResolutionStatus.MULTIPLE_FINAL
        assert resolution.verified_control is None
        assert resolution.locator is None

    def test_css_special_character_ids(self, page):
        """Controls with CSS special characters in ID resolve safely without selector syntax error."""
        page.set_content("""
        <form id="app-form" action="/apply" method="POST">
          <button type="button" id="btn:submit.special#1'\\"">Submit Application</button>
        </form>
        """)
        resolution = resolve_final_submission_control(page)

        assert resolution.status == SubmissionControlResolutionStatus.EXACTLY_ONE_FINAL
        assert resolution.verified_control is not None
        assert resolution.locator is not None

    def test_resolver_performs_no_clicks_or_mutations(self, page):
        """Verify resolve_final_submission_control is strictly read-only."""
        page.set_content("""
        <form>
          <button type="button" id="unstop_submit" onclick="window.__CLICKED = true">Complete Registration</button>
        </form>
        """)
        resolution = resolve_final_submission_control(page)
        assert resolution.status == SubmissionControlResolutionStatus.EXACTLY_ONE_FINAL

        # Verify onclick was NEVER executed
        clicked = page.evaluate("() => window.__CLICKED === true")
        assert clicked is False, "CRITICAL VIOLATION: resolver clicked the control!"


# ── 3. UnstopAdapter.submit_application Single-Click vs Zero-Click Tests ──

class TestAdapterSubmitExecution:

    def test_submit_application_clicks_exactly_once_on_valid_fixture(self, page):
        """When exactly one final control resolves, submit_application clicks it once."""
        html = """
        <div id="status-box"></div>
        <form id="reg-form" action="/competitions/123/register/submit" method="POST">
          <input type="text" name="name" value="Candidate" />
          <button type="button" id="unstop_submit" onclick="
            window.__CLICK_COUNT = (window.__CLICK_COUNT || 0) + 1;
            document.getElementById('reg-form').remove();
            document.getElementById('status-box').innerHTML = '<div>Successfully Registered</div>';
          ">Complete Registration</button>
        </form>
        """
        set_offline_page(page, html, "https://unstop.com/competitions/123/register")

        adapter = UnstopAdapter()
        ctx = ApplicationContext(
            opportunity_id=123,
            listing_url="https://unstop.com/competitions/123/register",
            adapter_name="unstop",
            tier=adapter.tier,
            browser_page=page,
        )

        res = adapter.submit_application(ctx)
        assert res["success"] is True
        assert res["confirmed"] is True

        click_count = page.evaluate("() => window.__CLICK_COUNT || 0")
        assert click_count == 1, f"Expected exactly 1 click, got {click_count}"

    def test_submit_application_zero_clicks_on_intermediate_fixture(self, page):
        """When only intermediate controls exist, submit_application performs 0 clicks."""
        # Use set_offline_page to set a valid Unstop URL (Gate 1 requires HTTPS Unstop origin).
        # Include a text input so check_status() returns not_submitted (form still active: Gate 3 passes).
        html = """
        <form>
          <input type="text" name="name" value="Test" />
          <button type="button" onclick="window.__CLICK_COUNT = (window.__CLICK_COUNT || 0) + 1">Continue</button>
          <button type="button" onclick="window.__CLICK_COUNT = (window.__CLICK_COUNT || 0) + 1">Save</button>
        </form>
        """
        set_offline_page(page, html, "https://unstop.com/jobs/123")
        adapter = UnstopAdapter()
        ctx = ApplicationContext(
            opportunity_id=123,
            listing_url="https://unstop.com/jobs/123",
            adapter_name="unstop",
            tier=adapter.tier,
            browser_page=page,
        )

        res = adapter.submit_application(ctx)
        assert res["success"] is False
        assert res["error"] == "manual_final_action_required"
        assert res["manual_review_required"] is True
        assert res["reason"] == "ambiguous_controls"

        click_count = page.evaluate("() => window.__CLICK_COUNT || 0")
        assert click_count == 0, f"Expected 0 clicks, got {click_count}"

    def test_next_outside_active_form_requires_manual_handoff_without_click(self, page):
        """A visible enabled Next outside the form is an ambiguous handoff, never a click target."""
        html = """
        <button type="button" onclick="window.__CLICK_COUNT = (window.__CLICK_COUNT || 0) + 1">Next</button>
        <form><input type="text" name="name" value="Test" /></form>
        """
        set_offline_page(page, html, "https://unstop.com/jobs/123")
        adapter = UnstopAdapter()
        ctx = ApplicationContext(
            opportunity_id=123,
            listing_url="https://unstop.com/jobs/123",
            adapter_name="unstop",
            tier=adapter.tier,
            browser_page=page,
        )

        res = adapter.submit_application(ctx)
        assert res == {
            "success": False,
            "confirmed": False,
            "manual_review_required": True,
            "error": "manual_final_action_required",
            "reason": "ambiguous_next_control",
            "resolution_status": "zero_final",
        }
        assert page.evaluate("() => window.__CLICK_COUNT || 0") == 0

    @pytest.mark.parametrize(
        ("controls", "expected_reason"),
        [
            ("<button disabled>Next</button>", "ambiguous_controls"),
            ("<button hidden>Next</button>", "ambiguous_controls"),
            ("<button>Back</button>", "ambiguous_controls"),
            ("<button>Do Something</button>", "ambiguous_controls"),
            ("", "no_final_control"),
            ("<button disabled>Submit Application</button>", "final_control_disabled"),
            ("<button hidden>Complete Registration</button>", "final_control_hidden"),
        ],
    )
    def test_manual_reason_is_bounded_and_accurate(self, page, controls, expected_reason):
        """Unresolved controls map to their distinct bounded handoff code with no click."""
        html = f"""
        <form><input type="text" name="name" value="Test" />{controls}</form>
        """
        set_offline_page(page, html, "https://unstop.com/jobs/123")
        adapter = UnstopAdapter()
        ctx = ApplicationContext(
            opportunity_id=123,
            listing_url="https://unstop.com/jobs/123",
            adapter_name="unstop",
            tier=adapter.tier,
            browser_page=page,
        )

        res = adapter.submit_application(ctx)
        assert res["success"] is False
        assert res["manual_review_required"] is True
        assert res["error"] == "manual_final_action_required"
        assert res["reason"] == expected_reason
        assert page.evaluate("() => window.__CLICK_COUNT || 0") == 0

    def test_submit_application_zero_clicks_on_multiple_controls_fixture(self, page):
        """When multiple final controls exist, submit_application performs 0 clicks."""
        # Use set_offline_page to set a valid Unstop URL (Gate 1 requires HTTPS Unstop origin).
        # Include a text input so check_status() returns not_submitted (form still active: Gate 3 passes).
        html = """
        <form>
          <input type="text" name="name" value="Test" />
          <button type="button" id="sub1" onclick="window.__CLICK_COUNT = (window.__CLICK_COUNT || 0) + 1">Submit Application</button>
          <button type="button" id="sub2" onclick="window.__CLICK_COUNT = (window.__CLICK_COUNT || 0) + 1">Complete Registration</button>
        </form>
        """
        set_offline_page(page, html, "https://unstop.com/jobs/123")
        adapter = UnstopAdapter()
        ctx = ApplicationContext(
            opportunity_id=123,
            listing_url="https://unstop.com/jobs/123",
            adapter_name="unstop",
            tier=adapter.tier,
            browser_page=page,
        )

        res = adapter.submit_application(ctx)
        assert res["success"] is False
        assert res["manual_review_required"] is True
        assert res["reason"] == "multiple_final_controls"
        assert "multiple_final" in res.get("resolution_status", "")

        click_count = page.evaluate("() => window.__CLICK_COUNT || 0")
        assert click_count == 0, f"Expected 0 clicks on ambiguous page, got {click_count}"


# ── 4. Confirmation Detection Hardening Tests ──────────────────────────────

class TestConfirmationDetection:

    def test_specific_confirmation_route_returns_confirmed(self, page):
        """Recognized path-only confirmation route reports confirmed."""
        set_offline_page(page, "<div><h1>Thank you</h1></div>", "https://unstop.com/competitions/555/register/success")

        status = evaluate_submission_confirmation(page, opportunity_id=555)
        assert status.confirmed is True
        assert status.status == "confirmed"
        assert status.confirmation_ref == "UNSTOP-CONFIRMED-555"

    def test_specific_visible_confirmation_text_returns_confirmed(self, page):
        """Specific visible confirmation banner reports confirmed."""
        set_offline_page(page, "<div><div class='success-banner'>Successfully Registered</div></div>", "https://unstop.com/done")

        status = evaluate_submission_confirmation(page, opportunity_id=777)
        assert status.confirmed is True
        assert status.status == "confirmed"

    def test_success_in_query_parameters_strictly_fails_closed(self, page):
        """Query parameter containing 'success' must NEVER trigger confirmation."""
        # Unsubmitted registration form with ?ref=success query string
        html = """
        <div>
          <h1>Software Engineer Opening</h1>
          <form>
            <input type="text" name="name" />
            <button>Next</button>
          </form>
        </div>
        """
        set_offline_page(page, html, "https://unstop.com/jobs/123/register?ref=success&status=confirmation")

        status = evaluate_submission_confirmation(page, opportunity_id=123)
        assert status.confirmed is False
        assert status.status != "confirmed"

    def test_unrelated_listing_title_containing_success_rejected(self, page):
        """A listing title like 'Customer Success Internship' must not trigger confirmation."""
        html = """
        <div>
          <h1>Customer Success Manager Fellowship</h1>
          <form>
            <input type="text" name="email" />
          </form>
        </div>
        """
        set_offline_page(page, html, "https://unstop.com/jobs/customer-success-manager")

        status = evaluate_submission_confirmation(page, opportunity_id=888)
        assert status.confirmed is False
        assert status.status != "confirmed"

    def test_hidden_confirmation_text_rejected(self, page):
        """Hidden confirmation text (e.g. style='display:none') does not count."""
        html = """
        <div>
          <div style="display: none;">Successfully Registered</div>
        </div>
        """
        set_offline_page(page, html, "https://unstop.com/jobs/test")

        status = evaluate_submission_confirmation(page, opportunity_id=999)
        assert status.confirmed is False

    def test_active_registration_form_inputs_prevent_confirmation(self, page):
        """Even if text matches, active unsubmitted inputs indicate unsubmitted state."""
        html = """
        <form>
          <p>Upon registration, you will be successfully registered.</p>
          <label>Full Name</label>
          <input type="text" name="full_name" />
        </form>
        """
        set_offline_page(page, html, "https://unstop.com/register")

        status = evaluate_submission_confirmation(page, opportunity_id=444)
        assert status.confirmed is False
        assert status.status == "not_submitted"

    def test_origin_subdomain_accepted(self, page):
        """https://jobs.unstop.com/.../application/submitted -> eligible evidence."""
        set_offline_page(page, "<div><h1>Done</h1></div>", "https://jobs.unstop.com/application/submitted")
        status = evaluate_submission_confirmation(page, opportunity_id=111)
        assert status.confirmed is True
        assert status.status == "confirmed"

    def test_origin_lookalike_rejected(self, page):
        """https://unstop.com.evil.test/.../register/success -> unconfirmed/ambiguous."""
        from unittest.mock import MagicMock
        fake_page = MagicMock()
        fake_page.url = "https://unstop.com.evil.test/competitions/123/register/success"
        fake_page.locator.return_value.count.return_value = 0
        status = evaluate_submission_confirmation(fake_page, opportunity_id=123)
        assert status.confirmed is False
        assert status.status == "ambiguous"

    def test_origin_parameter_lookalike_rejected(self, page):
        """https://evil.test/?next=https://unstop.com/register/success -> unconfirmed/ambiguous."""
        from unittest.mock import MagicMock
        fake_page = MagicMock()
        fake_page.url = "https://evil.test/?next=https://unstop.com/register/success"
        fake_page.locator.return_value.count.return_value = 0
        status = evaluate_submission_confirmation(fake_page, opportunity_id=123)
        assert status.confirmed is False
        assert status.status == "ambiguous"

    def test_origin_insecure_http_rejected(self, page):
        """http://unstop.com/.../register/success -> unconfirmed/ambiguous."""
        from unittest.mock import MagicMock
        fake_page = MagicMock()
        fake_page.url = "http://unstop.com/competitions/123/register/success"
        fake_page.locator.return_value.count.return_value = 0
        status = evaluate_submission_confirmation(fake_page, opportunity_id=123)
        assert status.confirmed is False
        assert status.status == "ambiguous"

    def test_origin_file_scheme_rejected(self, page):
        """file:///.../register/success -> unconfirmed/ambiguous."""
        from unittest.mock import MagicMock
        fake_page = MagicMock()
        fake_page.url = "file:///c:/path/to/register/success"
        fake_page.locator.return_value.count.return_value = 0
        status = evaluate_submission_confirmation(fake_page, opportunity_id=123)
        assert status.confirmed is False
        assert status.status == "ambiguous"

    def test_origin_login_redirect_unconfirmed(self, page):
        """Login / auth redirect on real Unstop origin -> unauthenticated / unconfirmed."""
        from unittest.mock import MagicMock
        fake_page = MagicMock()
        fake_page.url = "https://unstop.com/auth/login?redirect=/register"
        fake_page.locator.return_value.count.return_value = 0
        status = evaluate_submission_confirmation(fake_page, opportunity_id=123)
        assert status.confirmed is False
        assert status.status == "unauthenticated"

    def test_origin_missing_or_malformed_url_rejected(self, page):
        """Missing or malformed URL -> ambiguous."""
        from unittest.mock import MagicMock
        fake_page = MagicMock()
        fake_page.url = ""
        status = evaluate_submission_confirmation(fake_page, opportunity_id=123)
        assert status.confirmed is False
        assert status.status == "ambiguous"

        fake_page2 = MagicMock()
        fake_page2.url = "not-a-valid-url"
        status2 = evaluate_submission_confirmation(fake_page2, opportunity_id=123)
        assert status2.confirmed is False
        assert status2.status == "ambiguous"


# ── 5. Static AST and Safety Invariant Tests ───────────────────────────────

class TestSafetyInvariants:

    def test_fill_cannot_call_submit_application(self):
        """Verify UnstopAdapter.fill() AST contains zero calls to submit_application."""
        source = textwrap.dedent(inspect.getsource(UnstopAdapter.fill))
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and node.func.attr == "submit_application":
                    pytest.fail("CRITICAL INVARIANT VIOLATION: fill() calls submit_application()!")

    def test_final_submit_source_never_treats_next_as_clickable_final_action(self):
        """Static invariant: the only click in submit_application uses the resolved final handle."""
        assert "next" not in FINAL_SUBMIT_TEXTS
        source = textwrap.dedent(inspect.getsource(UnstopAdapter.submit_application))
        tree = ast.parse(source)
        click_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "click"
        ]
        assert len(click_calls) == 1
        assert ast.unparse(click_calls[0].func.value) == "resolution.locator"

    def test_submit_application_signature_has_no_approval_token_param(self):
        """M1 invariant: submit_application has no fake approval_token parameter."""
        sig = inspect.signature(UnstopAdapter.submit_application)
        assert "approval_token" not in sig.parameters
        assert "app_ctx" in sig.parameters


# ── 6. Fail-Closed Hardening and Handle Revalidation Tests ─────────────────

# ── 6. Fail-Closed Hardening and Handle Revalidation Tests ─────────────────

class TestFailClosedHardening:

    def test_inspection_evaluate_failure_returns_inspection_error(self):
        """When query_selector_all or handle.evaluate fails during inspection, return INSPECTION_ERROR with no locator."""
        from unittest.mock import MagicMock
        fake_page = MagicMock()
        fake_page.query_selector_all.side_effect = Exception("Authoritative DOM query selector error")

        resolution = resolve_final_submission_control(fake_page)
        assert resolution.status == SubmissionControlResolutionStatus.INSPECTION_ERROR
        assert resolution.verified_control is None
        assert resolution.locator is None
        assert "dom_inspection_failed" in resolution.diagnostics

    def test_handle_acquisition_exception_fails_closed_zero_clicks(self):
        """When query_selector_all raises an exception, adapter fails closed with zero clicks."""
        from unittest.mock import MagicMock, patch
        fake_page = MagicMock()
        fake_page.url = "https://unstop.com/jobs/123"  # Valid origin for Gate 1
        fake_page.query_selector_all.side_effect = Exception("Crash during element discovery")
        fake_page.locator.return_value.count.return_value = 0

        adapter = UnstopAdapter()
        ctx = ApplicationContext(
            opportunity_id=123,
            listing_url="https://unstop.com/jobs/123",
            adapter_name="unstop",
            tier=adapter.tier,
            browser_page=fake_page,
        )

        # Gate 3 (check_status) must return not_submitted to reach resolution gate
        with patch.object(adapter, "check_status", return_value=SubmissionStatus(confirmed=False, status="not_submitted")):
            res = adapter.submit_application(ctx)
        assert res["success"] is False
        assert res["confirmed"] is False
        assert res["resolution_status"] == "inspection_error"
        assert "resolution_failed" in res["error"]
        assert fake_page.click.call_count == 0 if hasattr(fake_page.click, "call_count") else True

    def test_dom_replacement_or_reordering_between_discovery_and_extraction(self):
        """DOM replacement or mutation between handle discovery and extraction fails closed with 0 clicks."""
        from unittest.mock import MagicMock, patch
        fake_page = MagicMock()
        fake_page.url = "https://unstop.com/jobs/123"  # Valid origin for Gate 1
        fake_handle = MagicMock()
        fake_handle.evaluate.side_effect = Exception("StaleElementReference: element detached during extraction")
        fake_page.query_selector_all.return_value = [fake_handle]
        fake_page.locator.return_value.count.return_value = 0

        adapter = UnstopAdapter()
        ctx = ApplicationContext(
            opportunity_id=123,
            listing_url="https://unstop.com/jobs/123",
            adapter_name="unstop",
            tier=adapter.tier,
            browser_page=fake_page,
        )

        with patch.object(adapter, "check_status", return_value=SubmissionStatus(confirmed=False, status="not_submitted")):
            res = adapter.submit_application(ctx)
        assert res["success"] is False
        assert res["confirmed"] is False
        assert res["resolution_status"] == "inspection_error"
        assert fake_handle.click.call_count == 0

    def test_missing_mandatory_handle_methods_fails_closed(self):
        """Handles missing evaluate, is_visible, or is_enabled produce INSPECTION_ERROR and 0 clicks."""
        from unittest.mock import MagicMock, patch
        fake_page = MagicMock()
        fake_page.url = "https://unstop.com/jobs/123"  # Valid origin for Gate 1
        # Missing is_enabled
        fake_handle = MagicMock(spec=["evaluate", "is_visible"])
        fake_page.query_selector_all.return_value = [fake_handle]
        fake_page.locator.return_value.count.return_value = 0

        resolution = resolve_final_submission_control(fake_page)
        assert resolution.status == SubmissionControlResolutionStatus.INSPECTION_ERROR
        assert resolution.locator is None

        adapter = UnstopAdapter()
        ctx = ApplicationContext(
            opportunity_id=123,
            listing_url="https://unstop.com/jobs/123",
            adapter_name="unstop",
            tier=adapter.tier,
            browser_page=fake_page,
        )
        with patch.object(adapter, "check_status", return_value=SubmissionStatus(confirmed=False, status="not_submitted")):
            res = adapter.submit_application(ctx)
        assert res["success"] is False
        assert res["resolution_status"] == "inspection_error"

    def test_malformed_missing_safety_fields_fails_closed(self):
        """Malformed or missing boolean safety fields produce INSPECTION_ERROR and 0 clicks."""
        from unittest.mock import MagicMock, patch
        fake_page = MagicMock()
        fake_page.url = "https://unstop.com/jobs/123"  # Valid origin for Gate 1
        fake_handle = MagicMock()
        # Missing is_in_active_form
        fake_handle.evaluate.return_value = {
            "tag_name": "button",
            "raw_text": "Submit Application",
            "is_visible": True,
            "is_disabled": False,
            "has_form": True,
        }
        fake_handle.is_visible.return_value = True
        fake_handle.is_enabled.return_value = True
        fake_page.query_selector_all.return_value = [fake_handle]
        fake_page.locator.return_value.count.return_value = 0

        resolution = resolve_final_submission_control(fake_page)
        assert resolution.status == SubmissionControlResolutionStatus.INSPECTION_ERROR
        assert resolution.locator is None

        adapter = UnstopAdapter()
        ctx = ApplicationContext(
            opportunity_id=123,
            listing_url="https://unstop.com/jobs/123",
            adapter_name="unstop",
            tier=adapter.tier,
            browser_page=fake_page,
        )
        with patch.object(adapter, "check_status", return_value=SubmissionStatus(confirmed=False, status="not_submitted")):
            res = adapter.submit_application(ctx)
        assert res["success"] is False
        assert res["resolution_status"] == "inspection_error"

    def test_handle_count_failure_fails_closed(self):
        """When handle.count() raises, fail closed with INSPECTION_ERROR and no locator."""
        from unittest.mock import MagicMock
        fake_page = MagicMock()
        fake_handle = MagicMock()
        fake_handle.evaluate.return_value = {
            "tag_name": "button",
            "raw_text": "Submit Application",
            "control_type": "button",
            "element_id": "sub_btn",
            "name": "submit",
            "role": "button",
            "aria_label": None,
            "title": None,
            "data_testid": None,
            "is_in_active_form": True,
            "has_form": True,
            "is_visible": True,
            "is_disabled": False,
            "form_action": "/apply",
            "form_method": "POST",
            "css_classes": ["submit-btn"],
        }
        fake_handle.is_visible.return_value = True
        fake_handle.is_enabled.return_value = True
        fake_handle.count.side_effect = Exception("Playwright handle count failed")
        fake_page.query_selector_all.return_value = [fake_handle]

        resolution = resolve_final_submission_control(fake_page)
        assert resolution.status == SubmissionControlResolutionStatus.INSPECTION_ERROR
        assert resolution.verified_control is None
        assert resolution.locator is None

    def test_handle_count_not_one_fails_closed(self):
        """When handle.count() returns > 1, fail closed with MULTIPLE_FINAL and no locator."""
        from unittest.mock import MagicMock
        fake_page = MagicMock()
        fake_handle = MagicMock()
        fake_handle.evaluate.return_value = {
            "tag_name": "button",
            "raw_text": "Submit Application",
            "control_type": "button",
            "element_id": "sub_btn",
            "name": "submit",
            "role": "button",
            "aria_label": None,
            "title": None,
            "data_testid": None,
            "is_in_active_form": True,
            "has_form": True,
            "is_visible": True,
            "is_disabled": False,
            "form_action": "/apply",
            "form_method": "POST",
            "css_classes": ["submit-btn"],
        }
        fake_handle.is_visible.return_value = True
        fake_handle.is_enabled.return_value = True
        fake_handle.count.return_value = 2
        fake_page.query_selector_all.return_value = [fake_handle]

        resolution = resolve_final_submission_control(fake_page)
        assert resolution.status == SubmissionControlResolutionStatus.MULTIPLE_FINAL
        assert resolution.verified_control is None
        assert resolution.locator is None

    def test_revalidate_handle_visibility_failure(self, page):
        """Handle revalidation fails closed if control becomes hidden before click."""
        page.set_content("""
        <form action="/apply" method="POST">
          <button type="button" id="unstop_submit">Submit Application</button>
        </form>
        """)
        resolution = resolve_final_submission_control(page)
        assert resolution.status == SubmissionControlResolutionStatus.EXACTLY_ONE_FINAL

        # Hide the element in the DOM
        page.evaluate("() => document.querySelector('#unstop_submit').style.display = 'none'")

        is_valid, reason = revalidate_handle_before_click(page, resolution.locator, resolution.verified_control, resolution.form_handle)
        assert is_valid is False
        assert reason in ("no_final_controls_detected", "handle_hidden")

    def test_revalidate_handle_enabled_failure(self, page):
        """Handle revalidation fails closed if control becomes disabled before click."""
        page.set_content("""
        <form action="/apply" method="POST">
          <button type="button" id="unstop_submit">Submit Application</button>
        </form>
        """)
        resolution = resolve_final_submission_control(page)
        assert resolution.status == SubmissionControlResolutionStatus.EXACTLY_ONE_FINAL

        # Disable the element in the DOM
        page.evaluate("() => document.querySelector('#unstop_submit').disabled = true")

        is_valid, reason = revalidate_handle_before_click(page, resolution.locator, resolution.verified_control, resolution.form_handle)
        assert is_valid is False
        assert reason in ("no_final_controls_detected", "handle_disabled")

    def test_revalidate_handle_detached_before_click(self, page):
        """Handle revalidation fails closed if control is detached from DOM before click."""
        page.set_content("""
        <form action="/apply" method="POST">
          <button type="button" id="unstop_submit">Submit Application</button>
        </form>
        """)
        resolution = resolve_final_submission_control(page)
        assert resolution.status == SubmissionControlResolutionStatus.EXACTLY_ONE_FINAL

        # Detach element from DOM
        page.evaluate("() => document.querySelector('#unstop_submit').remove()")

        is_valid, reason = revalidate_handle_before_click(page, resolution.locator, resolution.verified_control, resolution.form_handle)
        assert is_valid is False
        assert reason in ("no_final_controls_detected", "handle_detached")

    def test_revalidate_handle_changed_text_before_click(self, page):
        """Handle revalidation fails closed if control text changes before click."""
        page.set_content("""
        <form action="/apply" method="POST">
          <button type="button" id="unstop_submit">Submit Application</button>
        </form>
        """)
        resolution = resolve_final_submission_control(page)
        assert resolution.status == SubmissionControlResolutionStatus.EXACTLY_ONE_FINAL

        # Mutate text to an intermediate action
        page.evaluate("() => document.querySelector('#unstop_submit').innerText = 'Continue'")

        is_valid, reason = revalidate_handle_before_click(page, resolution.locator, resolution.verified_control, resolution.form_handle)
        assert is_valid is False
        assert reason in ("no_final_controls_detected", "control_text_changed")

    def test_revalidate_control_moved_to_different_form_fails_closed_zero_clicks(self, page):
        """Control moved to a different form before click fails closed with form_mismatch and 0 clicks."""
        page.set_content("""
        <div id="wrapper">
          <form id="form_a" action="/apply" method="POST">
            <button type="button" id="unstop_submit" onclick="window.__CLICK_COUNT = (window.__CLICK_COUNT || 0) + 1">Submit Application</button>
          </form>
        </div>
        """)
        resolution = resolve_final_submission_control(page)
        assert resolution.status == SubmissionControlResolutionStatus.EXACTLY_ONE_FINAL
        assert resolution.locator is not None
        assert resolution.form_handle is not None

        # Create form_b, move button to form_b, and remove form_a so form_b is active but different node
        page.evaluate("""() => {
            const formB = document.createElement('form');
            formB.id = 'form_b';
            formB.action = '/other';
            document.getElementById('wrapper').appendChild(formB);
            formB.appendChild(document.getElementById('unstop_submit'));
            document.getElementById('form_a').remove();
        }""")

        # Direct revalidation proves form_mismatch
        is_valid, reason = revalidate_handle_before_click(
            page, resolution.locator, resolution.verified_control, resolution.form_handle
        )
        assert is_valid is False
        assert reason == "form_mismatch"

        # End-to-end submit_application proves zero clicks when moved between resolution and revalidation
        adapter = UnstopAdapter()
        ctx = ApplicationContext(
            opportunity_id=123,
            listing_url="https://unstop.com/jobs/123",
            adapter_name="unstop",
            tier=adapter.tier,
            browser_page=page,
        )

        from unittest.mock import patch
        real_resolve = resolve_final_submission_control

        def _resolve_and_move(p):
            res = real_resolve(p)
            # Simulate element moved to another form after resolution
            p.evaluate("""() => {
                const formC = document.createElement('form');
                formC.id = 'form_c';
                document.getElementById('wrapper').appendChild(formC);
                formC.appendChild(document.getElementById('unstop_submit'));
            }""")
            return res

        # Reset DOM using set_offline_page so page.url is a valid Unstop origin (Gate 1)
        html_reset = """
        <div id="wrapper">
          <form id="form_a" action="/apply" method="POST">
            <input type="text" name="name" />
            <button type="button" id="unstop_submit" onclick="window.__CLICK_COUNT = (window.__CLICK_COUNT || 0) + 1">Submit Application</button>
          </form>
        </div>
        """
        set_offline_page(page, html_reset, "https://unstop.com/jobs/123")

        with patch("worker.adapters.unstop.resolve_final_submission_control", side_effect=_resolve_and_move):
            res = adapter.submit_application(ctx)

        assert res["success"] is False
        assert res["confirmed"] is False
        # After Gate 4, revalidation failure populates resolution_status
        assert "revalidation_failed" in (res.get("resolution_status") or res.get("error", ""))
        click_count = page.evaluate("() => window.__CLICK_COUNT || 0")
        assert click_count == 0, f"Expected 0 clicks when control moved forms, got {click_count}"

    def test_revalidate_second_final_control_inserted_before_click_fails_closed_zero_clicks(self, page):
        """Second final submit control inserted immediately before click produces competing_final_controls_detected and 0 clicks."""
        page.set_content("""
        <form id="form_a" action="/apply" method="POST">
          <button type="button" id="btn1" onclick="window.__CLICK_COUNT = (window.__CLICK_COUNT || 0) + 1">Submit Application</button>
        </form>
        """)
        resolution = resolve_final_submission_control(page)
        assert resolution.status == SubmissionControlResolutionStatus.EXACTLY_ONE_FINAL
        assert resolution.locator is not None

        # Insert competing final submit button before click
        page.evaluate("""() => {
            const form = document.getElementById('form_a');
            const btn2 = document.createElement('button');
            btn2.id = 'btn2';
            btn2.type = 'button';
            btn2.innerText = 'Complete Registration';
            btn2.onclick = () => { window.__CLICK_COUNT = (window.__CLICK_COUNT || 0) + 1; };
            form.appendChild(btn2);
        }""")

        is_valid, reason = revalidate_handle_before_click(
            page, resolution.locator, resolution.verified_control, resolution.form_handle
        )
        assert is_valid is False
        assert reason == "competing_final_controls_detected"

        click_count = page.evaluate("() => window.__CLICK_COUNT || 0")
        assert click_count == 0, f"Expected 0 clicks on competing control insertion, got {click_count}"

    def test_submit_application_revalidation_failure_produces_zero_clicks(self, page):
        """When handle revalidation fails, submit_application performs 0 clicks and returns failure."""
        # Use set_offline_page so page.url is a valid Unstop origin (Gate 1)
        html = """
        <form action="/apply" method="POST">
          <input type="text" name="name" />
          <button type="button" id="unstop_submit" onclick="window.__CLICK_COUNT = (window.__CLICK_COUNT || 0) + 1">Submit Application</button>
        </form>
        """
        set_offline_page(page, html, "https://unstop.com/jobs/123")
        from unittest.mock import patch

        adapter = UnstopAdapter()
        ctx = ApplicationContext(
            opportunity_id=123,
            listing_url="https://unstop.com/jobs/123",
            adapter_name="unstop",
            tier=adapter.tier,
            browser_page=page,
        )

        with patch("worker.adapters.unstop.revalidate_handle_before_click", return_value=(False, "mocked_revalidation_failure")):
            res = adapter.submit_application(ctx)

        assert res["success"] is False
        assert res["confirmed"] is False
        # Gate 4 revalidation populates resolution_status
        assert "revalidation_failed" in (res.get("resolution_status") or res.get("error", ""))

        click_count = page.evaluate("() => window.__CLICK_COUNT || 0")
        assert click_count == 0, f"Expected 0 clicks on revalidation failure, got {click_count}"

    def test_submit_application_challenge_check_exception_fails_closed(self):
        """When checking for Cloudflare/bot challenges raises an exception, submit_application fails closed."""
        from unittest.mock import MagicMock
        fake_page = MagicMock()
        fake_page.url = "https://unstop.com/jobs/123"  # Valid Unstop origin so Gate 1 passes
        fake_loc = MagicMock()
        fake_loc.count.side_effect = Exception("Cloudflare probe locator crashed")
        fake_page.locator.return_value = fake_loc

        adapter = UnstopAdapter()
        ctx = ApplicationContext(
            opportunity_id=123,
            listing_url="https://unstop.com/jobs/123",
            adapter_name="unstop",
            tier=adapter.tier,
            browser_page=fake_page,
        )

        res = adapter.submit_application(ctx)
        assert res["success"] is False
        assert res["confirmed"] is False
        assert res["error"] == "challenge_probe_failed"

    def test_confirmation_input_probe_exception_returns_ambiguous(self):
        """When inspecting active registration form inputs raises, confirmation evaluation returns ambiguous."""
        from unittest.mock import MagicMock
        fake_page = MagicMock()
        fake_page.url = "https://unstop.com/competitions/123/register/success"
        fake_inputs = MagicMock()
        fake_inputs.count.side_effect = Exception("Input inspection error")
        fake_page.locator.return_value = fake_inputs

        status = evaluate_submission_confirmation(fake_page, opportunity_id=123)
        assert status.confirmed is False
        assert status.status == "ambiguous"
        assert status.detail == "input_probe_failed"

    def test_confirmation_visibility_probe_exception_returns_ambiguous(self):
        """When confirmation text visibility probe raises, confirmation evaluation returns ambiguous."""
        from unittest.mock import MagicMock
        fake_page = MagicMock()
        fake_page.url = "https://unstop.com/competitions/123/register/success"

        inputs_loc = MagicMock()
        inputs_loc.count.return_value = 0

        phrase_loc = MagicMock()
        phrase_loc.count.return_value = 1
        phrase_el = MagicMock()
        phrase_el.is_visible.side_effect = Exception("Visibility probe crashed")
        phrase_loc.nth.return_value = phrase_el

        def locator_router(sel):
            if "form input" in sel:
                return inputs_loc
            return phrase_loc

        fake_page.locator.side_effect = locator_router

        status = evaluate_submission_confirmation(fake_page, opportunity_id=123)
        assert status.confirmed is False
        assert status.status == "ambiguous"
        assert status.detail == "visibility_probe_failed"


# ── 7. Privacy and Metadata Sanitization Hardening Tests ──────────────────

class TestSanitizationHardening:

    def test_metadata_sanitization_removes_sensitive_tokens_and_pii(self):
        """Persisted metadata never contains simulated emails, phones, tokens, or query strings."""
        from worker.adapters.submission_controls import SubmissionControlCandidate

        cand = SubmissionControlCandidate.create(
            tag_name="button",
            text="Submit Application",
            raw_text="Submit Application",
            is_visible=True,
            is_disabled=False,
            aria_label="Submit for candidate user@domain.com",
            title="Call +1-555-867-5309 for verification",
            name="auth_bearer_token_xyz",
            form_action_path="/apply?token=secret123#step2",
            css_classes=["btn", "submit-btn", "extra-long-class-name-" + "a" * 50],
            is_in_active_form=True,
        )
        data = cand.to_dict()

        # Check candidate_id is deterministic SHA-256 derived
        assert data["candidate_id"].startswith("ctrl_")
        assert len(data["candidate_id"]) == 21  # "ctrl_" + 16 hex chars

        # Ensure no sensitive tokens or PII leaked
        serialized = str(data)
        assert "user@domain.com" not in serialized
        assert "+1-555-867-5309" not in serialized
        assert "5558675309" not in serialized
        assert "secret123" not in serialized
        assert "auth_bearer_token_xyz" not in serialized
        assert data["form_action_path"].startswith("action_")
        assert data["form_action_path"] != "/apply"
        assert cand.form_action_path == "/apply"
        assert data["canonical_action"] == "submit application"

    def test_unknown_raw_text_hashed_and_not_persisted(self):
        """Unknown or non-canonical control text is replaced with a hash, never persisted raw."""
        from worker.adapters.submission_controls import SubmissionControlCandidate

        cand = SubmissionControlCandidate.create(
            tag_name="button",
            text="Sensitive Password Or PII In Button",
            raw_text="Sensitive Password Or PII In Button",
            is_visible=True,
            is_disabled=False,
            is_in_active_form=True,
        )
        data = cand.to_dict()

        assert "Sensitive Password Or PII In Button" not in str(data)
        assert data["canonical_action"].startswith("UNKNOWN_TEXT_")

    def test_short_opaque_sensitive_attributes_are_hashed(self):
        """Arbitrary short/opaque attributes are hashed by default; only narrow allowlists emit raw."""
        from worker.adapters.submission_controls import ALLOWED_TAGS, ALLOWED_ROLES

        # Allowlist values pass through in lowercase
        assert sanitize_attribute_value("BUTTON", allowlist=ALLOWED_TAGS) == "button"
        assert sanitize_attribute_value("button", allowlist=ALLOWED_ROLES) == "button"

        # Arbitrary short opaque values are hashed by default, never emitted raw
        short_tokens = ["c_42", "tok1", "adm", "usr_x", "secret"]
        for tok in short_tokens:
            h = sanitize_attribute_value(tok, prefix="attr")
            assert h.startswith("attr_"), f"Expected prefix attr_ for {tok}"
            assert tok not in h, f"Sensitive token {tok} leaked into hashed attribute {h}"


class TestM2AFinalCorrections:
    """Targeted tests for M2A final narrow corrections."""

    def test_missing_evaluate_handle(self):
        """Handle without callable evaluate_handle raises mandatory_handle_methods_missing."""
        from unittest.mock import MagicMock
        page = MagicMock()
        handle = MagicMock()
        handle.evaluate = MagicMock()
        handle.is_visible = MagicMock(return_value=True)
        handle.is_enabled = MagicMock(return_value=True)
        del handle.evaluate_handle
        page.query_selector_all.return_value = [handle]

        with pytest.raises(RuntimeError, match="mandatory_handle_methods_missing"):
            inspect_submission_controls(page)

    def test_evaluate_handle_exception_and_null_result(self):
        """When has_form is True, evaluate_handle failure or null result fails closed with inspection error."""
        from unittest.mock import MagicMock

        # 1. evaluate_handle raises exception
        page1 = MagicMock()
        handle1 = MagicMock()
        handle1.evaluate.return_value = {
            "tag_name": "button",
            "raw_text": "Submit Application",
            "control_type": "button",
            "element_id": "submit_btn",
            "is_visible": True,
            "is_disabled": False,
            "is_in_active_form": True,
            "has_form": True,
        }
        handle1.is_visible.return_value = True
        handle1.is_enabled.return_value = True
        handle1.evaluate_handle.side_effect = RuntimeError("form lookup error")
        page1.query_selector_all.return_value = [handle1]

        with pytest.raises(RuntimeError, match="dom_inspection_failed"):
            inspect_submission_controls(page1)

        # 2. evaluate_handle returns None
        page2 = MagicMock()
        handle2 = MagicMock()
        handle2.evaluate.return_value = {
            "tag_name": "button",
            "raw_text": "Submit Application",
            "control_type": "button",
            "element_id": "submit_btn",
            "is_visible": True,
            "is_disabled": False,
            "is_in_active_form": True,
            "has_form": True,
        }
        handle2.is_visible.return_value = True
        handle2.is_enabled.return_value = True
        handle2.evaluate_handle.return_value = None
        page2.query_selector_all.return_value = [handle2]

        with pytest.raises(RuntimeError, match="missing_or_invalid_form_handle"):
            inspect_submission_controls(page2)

    def test_active_form_true_with_has_form_false(self):
        """is_in_active_form=True without has_form=True is an inspection error."""
        from unittest.mock import MagicMock
        page = MagicMock()
        handle = MagicMock()
        handle.evaluate.return_value = {
            "tag_name": "button",
            "raw_text": "Submit Application",
            "control_type": "button",
            "element_id": "submit_btn",
            "is_visible": True,
            "is_disabled": False,
            "is_in_active_form": True,
            "has_form": False,
        }
        handle.is_visible.return_value = True
        handle.is_enabled.return_value = True
        page.query_selector_all.return_value = [handle]

        with pytest.raises(RuntimeError, match="active_form_without_form_handle"):
            inspect_submission_controls(page)

    def test_missing_live_revalidation_booleans(self):
        """revalidate_handle_before_click requires explicit booleans for all 4 safety fields."""
        from unittest.mock import MagicMock, patch
        from worker.adapters.submission_controls import SubmissionControlCandidate

        cand = SubmissionControlCandidate.create(
            tag_name="button",
            text="Submit Application",
            raw_text="Submit Application",
            is_visible=True,
            is_disabled=False,
            is_in_active_form=True,
            form_action_path="/apply",
        )

        for missing_field in ("is_visible", "is_disabled", "is_in_active_form", "has_form"):
            meta = {
                "tag_name": "button",
                "raw_text": "Submit Application",
                "control_type": "button",
                "element_id": None,
                "name": None,
                "role": None,
                "aria_label": None,
                "title": None,
                "data_testid": None,
                "is_visible": True,
                "is_disabled": False,
                "is_in_active_form": True,
                "has_form": True,
                "form_action": "/apply",
            }
            del meta[missing_field]

            mock_handle = MagicMock()
            mock_handle.evaluate.side_effect = lambda script, *args: True if "isConnected" in script or script == "(el, other) => el === other" else meta
            mock_handle.evaluate_handle.return_value = MagicMock()
            mock_handle.is_visible.return_value = True
            mock_handle.is_enabled.return_value = True

            mock_page = MagicMock()
            mock_page.query_selector_all.return_value = [mock_handle]

            with patch("worker.adapters.submission_controls.inspect_submission_controls", return_value=[(cand, mock_handle)]):
                is_valid, reason = revalidate_handle_before_click(mock_page, mock_handle, cand)
                assert not is_valid
                assert f"missing_or_invalid_safety_field:{missing_field}" in reason

    def test_missing_challenge_locator_count_methods(self):
        """Missing page.locator or locator.count during challenge probe returns challenge_probe_failed."""
        from unittest.mock import MagicMock
        adapter = UnstopAdapter()

        # 1. Missing page.locator: use a mock that has url but no locator method
        #    (spec=[] means no attributes, so getattr(page, "url", None) returns None
        #     which triggers submission_origin_invalid; instead use a mock with url set
        #     but no locator method to test Gate 2)
        mock_page_no_loc = MagicMock(spec=["url"])
        mock_page_no_loc.url = "https://unstop.com/jobs/apply"
        ctx1 = ApplicationContext(
            opportunity_id=1,
            listing_url="https://unstop.com/job/1",
            adapter_name="unstop",
            tier=ReliabilityTier.EXPERIMENTAL,
            browser_page=mock_page_no_loc,
        )
        res1 = adapter.submit_application(ctx1)
        assert res1["success"] is False
        assert res1["error"] == "challenge_probe_failed"

        # 2. Missing locator.count
        mock_page_no_count = MagicMock()
        mock_page_no_count.url = "https://unstop.com/jobs/apply"  # Valid origin for Gate 1
        mock_loc = MagicMock(spec=[])  # no count method
        mock_page_no_count.locator.return_value = mock_loc
        ctx2 = ApplicationContext(
            opportunity_id=1,
            listing_url="https://unstop.com/job/1",
            adapter_name="unstop",
            tier=ReliabilityTier.EXPERIMENTAL,
            browser_page=mock_page_no_count,
        )
        res2 = adapter.submit_application(ctx2)
        assert res2["success"] is False
        assert res2["error"] == "challenge_probe_failed"

    def test_missing_confirmation_count_nth_is_visible_methods(self):
        """evaluate_submission_confirmation fails closed on missing count, nth, or is_visible methods."""
        from unittest.mock import MagicMock

        # 1. Missing inputs.count during input probe
        mock_page_in_no_count = MagicMock()
        mock_page_in_no_count.url = "https://unstop.com/application/success"
        inputs_no_count = MagicMock(spec=[])
        mock_page_in_no_count.locator.return_value = inputs_no_count
        status1 = evaluate_submission_confirmation(mock_page_in_no_count)
        assert status1.confirmed is False
        assert status1.detail == "input_probe_failed"

        # 2. Missing inputs.nth during input probe
        mock_page_in_no_nth = MagicMock()
        mock_page_in_no_nth.url = "https://unstop.com/application/success"
        inputs_no_nth = MagicMock(spec=["count"])
        inputs_no_nth.count.return_value = 1
        mock_page_in_no_nth.locator.return_value = inputs_no_nth
        status2 = evaluate_submission_confirmation(mock_page_in_no_nth)
        assert status2.confirmed is False
        assert status2.detail == "input_probe_failed"

        # 3. Missing is_visible on input element
        mock_page_in_no_vis = MagicMock()
        mock_page_in_no_vis.url = "https://unstop.com/application/success"
        inputs_no_vis = MagicMock()
        inputs_no_vis.count.return_value = 1
        inputs_no_vis.nth.return_value = MagicMock(spec=[])  # no is_visible
        mock_page_in_no_vis.locator.return_value = inputs_no_vis
        status3 = evaluate_submission_confirmation(mock_page_in_no_vis)
        assert status3.confirmed is False
        assert status3.detail == "input_probe_failed"

        # 4. Missing count on confirmation text locator
        mock_page_txt_no_count = MagicMock()
        mock_page_txt_no_count.url = "https://unstop.com/competitions/123/apply"
        valid_empty_inputs = MagicMock()
        valid_empty_inputs.count.return_value = 0
        txt_loc_no_count = MagicMock(spec=[])
        mock_page_txt_no_count.locator.side_effect = lambda sel: valid_empty_inputs if "form input" in sel else txt_loc_no_count
        status4 = evaluate_submission_confirmation(mock_page_txt_no_count)
        assert status4.confirmed is False
        assert status4.detail == "visibility_probe_failed"

        # 5. Missing nth on confirmation text locator
        mock_page_txt_no_nth = MagicMock()
        mock_page_txt_no_nth.url = "https://unstop.com/competitions/123/apply"
        txt_loc_no_nth = MagicMock(spec=["count"])
        txt_loc_no_nth.count.return_value = 1
        mock_page_txt_no_nth.locator.side_effect = lambda sel: valid_empty_inputs if "form input" in sel else txt_loc_no_nth
        status5 = evaluate_submission_confirmation(mock_page_txt_no_nth)
        assert status5.confirmed is False
        assert status5.detail == "visibility_probe_failed"

        # 6. Missing is_visible on confirmation text element
        mock_page_txt_no_vis = MagicMock()
        mock_page_txt_no_vis.url = "https://unstop.com/competitions/123/apply"
        txt_loc_no_vis = MagicMock()
        txt_loc_no_vis.count.return_value = 1
        txt_loc_no_vis.nth.return_value = MagicMock(spec=[])  # no is_visible
        mock_page_txt_no_vis.locator.side_effect = lambda sel: valid_empty_inputs if "form input" in sel else txt_loc_no_vis
        status6 = evaluate_submission_confirmation(mock_page_txt_no_vis)
        assert status6.confirmed is False
        assert status6.detail == "visibility_probe_failed"

    def test_confirm_submission_internal_exception_returns_bounded_code(self):
        """check_status replaces any internal exception with confirmation_probe_failed without leaking exc details."""
        from unittest.mock import MagicMock, patch
        adapter = UnstopAdapter()
        mock_page = MagicMock()
        ctx = ApplicationContext(
            opportunity_id=1,
            listing_url="https://unstop.com/job/1",
            adapter_name="unstop",
            tier=ReliabilityTier.EXPERIMENTAL,
            browser_page=mock_page,
        )

        with patch("worker.adapters.unstop.evaluate_submission_confirmation", side_effect=RuntimeError("critical_db_password_leak_xyz")):
            res = adapter.check_status(ctx)

        assert res.confirmed is False
        assert res.status == "ambiguous"
        assert res.detail == "confirmation_probe_failed"
        assert "password" not in res.detail
        assert "critical_db" not in res.detail

    def test_path_based_pii_token_in_unauthorized_origin_details(self):
        """evaluate_submission_confirmation returns strictly unauthorized_origin with no path/query attached."""
        from unittest.mock import MagicMock
        mock_page = MagicMock()
        mock_page.url = "https://evil.example.com/steal?candidate_email=secret@domain.com&token=secret_xyz#frag"

        res = evaluate_submission_confirmation(mock_page)
        assert res.confirmed is False
        assert res.status == "ambiguous"
        assert res.detail == "unauthorized_origin"
        assert "secret" not in res.detail
        assert "domain.com" not in res.detail
        assert "steal" not in res.detail

    def test_query_fragment_absence_from_submit_application_result(self):
        """submit_application result url never contains queries or fragments."""
        from unittest.mock import MagicMock, patch
        adapter = UnstopAdapter()
        mock_page = MagicMock()
        mock_page.url = "https://unstop.com/register/success?token=sensitive_jwt_token_123#verification_step"
        mock_page.locator.return_value.count.return_value = 0

        ctx = ApplicationContext(
            opportunity_id=1,
            listing_url="https://unstop.com/job/1",
            adapter_name="unstop",
            tier=ReliabilityTier.EXPERIMENTAL,
            browser_page=mock_page,
        )

        # Mock successful resolution and confirmation
        mock_resolution = MagicMock()
        mock_resolution.status = SubmissionControlResolutionStatus.EXACTLY_ONE_FINAL
        mock_resolution.locator = MagicMock()
        mock_resolution.verified_control = MagicMock()
        mock_resolution.verified_control.candidate_id = "ctrl_test"
        mock_resolution.form_handle = MagicMock()

        confirmed_status = SubmissionStatus(
            confirmed=True,
            status="confirmed",
            confirmation_ref="REF-123",
            detail="submission_confirmed_success",
        )

        with (
            patch.object(adapter, "check_status", return_value=SubmissionStatus(confirmed=False, status="not_submitted")),
            patch("worker.adapters.unstop.resolve_final_submission_control", return_value=mock_resolution),
            patch("worker.adapters.unstop.revalidate_handle_before_click", return_value=(True, "valid")),
            patch.object(adapter, "wait_for_confirmation", return_value=confirmed_status),
        ):
            res = adapter.submit_application(ctx)

        assert res["success"] is True
        assert res["confirmed"] is True
        assert res["url"] == "https://unstop.com/register/success"
        assert "?" not in res["url"]
        assert "#" not in res["url"]
        assert "token" not in res["url"]
        assert "sensitive_jwt" not in res["url"]

    def test_selector_strategy_and_form_action_path_not_emitted_raw(self):
        """Persisted candidate dictionary never emits selector_strategy or form_action_path raw."""
        from worker.adapters.submission_controls import SubmissionControlCandidate

        raw_action = "/apply/custom/v1/confidential/endpoint"
        raw_selector = "button.super-confidential-action-class[data-action='submit']"

        cand = SubmissionControlCandidate.create(
            tag_name="button",
            text="Submit Application",
            raw_text="Submit Application",
            is_visible=True,
            is_disabled=False,
            is_in_active_form=True,
            form_action_path=raw_action,
            selector_strategy=raw_selector,
        )

        # In memory: exact sanitized path is preserved for fingerprinting
        assert cand.form_action_path == raw_action
        assert cand.selector_strategy == raw_selector

        data = cand.to_dict()
        # Persisted dict: hashed with prefix, never raw
        assert data["form_action_path"].startswith("action_")
        assert raw_action not in str(data)
        assert data["selector_strategy"].startswith("sel_")
        assert raw_selector not in str(data)
        assert "super-confidential" not in str(data)

    def test_confirmation_url_on_unstop_port_8443_rejected(self):
        """URLs with non-443 port like unstop.com:8443 or malformed ports are rejected."""
        from worker.adapters.unstop import is_valid_unstop_origin
        from unittest.mock import MagicMock

        assert is_valid_unstop_origin("https://unstop.com:8443/apply") is False
        assert is_valid_unstop_origin("https://unstop.com:443/apply") is True
        assert is_valid_unstop_origin("https://unstop.com/apply") is True
        assert is_valid_unstop_origin("https://unstop.com:invalid_port/apply") is False

        mock_page = MagicMock()
        mock_page.url = "https://unstop.com:8443/application/success"
        status = evaluate_submission_confirmation(mock_page)
        assert status.confirmed is False
        assert status.status == "ambiguous"
        assert status.detail == "unauthorized_origin"


# ── 9. submit_application() Zero-Click Safety Gate Tests ─────────────────────

class TestSubmitApplicationSafetyGates:
    """Verifies submit_application() never clicks for unauthorized/unsafe states."""

    def _make_ctx(self, mock_page):
        return ApplicationContext(
            opportunity_id=42,
            listing_url="https://unstop.com/jobs/test",
            adapter_name="unstop",
            tier=ReliabilityTier.EXPERIMENTAL,
            browser_page=mock_page,
        )

    def test_foreign_origin_zero_clicks(self):
        """submit_application returns submission_origin_invalid for non-Unstop URLs."""
        from unittest.mock import MagicMock
        adapter = UnstopAdapter()
        mock_page = MagicMock()
        mock_page.url = "https://evil.example.com/fake-unstop"
        ctx = self._make_ctx(mock_page)

        res = adapter.submit_application(ctx)

        assert res["success"] is False
        assert res["confirmed"] is False
        assert res["error"] == "submission_origin_invalid"
        mock_page.locator.assert_not_called()  # No locator calls before origin gate

    def test_http_origin_zero_clicks(self):
        """submit_application returns submission_origin_invalid for plain HTTP."""
        from unittest.mock import MagicMock
        adapter = UnstopAdapter()
        mock_page = MagicMock()
        mock_page.url = "http://unstop.com/jobs/apply"
        ctx = self._make_ctx(mock_page)

        res = adapter.submit_application(ctx)

        assert res["success"] is False
        assert res["error"] == "submission_origin_invalid"

    def test_non_standard_port_zero_clicks(self):
        """submit_application returns submission_origin_invalid for non-443 port."""
        from unittest.mock import MagicMock
        adapter = UnstopAdapter()
        mock_page = MagicMock()
        mock_page.url = "https://unstop.com:8443/jobs/apply"
        ctx = self._make_ctx(mock_page)

        res = adapter.submit_application(ctx)

        assert res["success"] is False
        assert res["error"] == "submission_origin_invalid"

    def test_no_browser_page_zero_clicks(self):
        """submit_application returns no_browser_page when page is None."""
        adapter = UnstopAdapter()
        ctx = ApplicationContext(
            opportunity_id=1,
            listing_url="https://unstop.com/jobs/test",
            adapter_name="unstop",
            tier=ReliabilityTier.EXPERIMENTAL,
            browser_page=None,
        )

        res = adapter.submit_application(ctx)

        assert res["success"] is False
        assert res["error"] == "no_browser_page"

    def test_login_redirect_zero_clicks_via_check_status(self):
        """When check_status returns unauthenticated, submit fails closed."""
        from unittest.mock import MagicMock, patch
        adapter = UnstopAdapter()
        mock_page = MagicMock()
        mock_page.url = "https://unstop.com/jobs/apply"
        mock_page.locator.return_value.count.return_value = 0  # no challenge
        ctx = self._make_ctx(mock_page)

        unauthenticated = SubmissionStatus(
            confirmed=False, status="unauthenticated", detail="unauthenticated_redirect"
        )
        with patch.object(adapter, "check_status", return_value=unauthenticated):
            res = adapter.submit_application(ctx)

        assert res["success"] is False
        assert res["confirmed"] is False
        assert "unauthenticated" in res["error"]
        # Ensure no resolution was attempted
        assert "resolution_status" not in res or "revalidation" not in res.get("resolution_status", "")

    def test_check_status_exception_zero_clicks(self):
        """When check_status raises an exception, submit fails closed with pre_status_probe_failed."""
        from unittest.mock import MagicMock, patch
        adapter = UnstopAdapter()
        mock_page = MagicMock()
        mock_page.url = "https://unstop.com/jobs/apply"
        mock_page.locator.return_value.count.return_value = 0
        ctx = self._make_ctx(mock_page)

        with patch.object(adapter, "check_status", side_effect=RuntimeError("internal_db_password_secret")):
            res = adapter.submit_application(ctx)

        assert res["success"] is False
        assert res["error"] == "pre_status_probe_failed"
        assert "password" not in res["error"]
        assert "secret" not in res["error"]
        assert "db" not in res["error"]

    def test_ambiguous_status_zero_clicks(self):
        """When check_status returns ambiguous, submit fails closed."""
        from unittest.mock import MagicMock, patch
        adapter = UnstopAdapter()
        mock_page = MagicMock()
        mock_page.url = "https://unstop.com/jobs/apply"
        mock_page.locator.return_value.count.return_value = 0
        ctx = self._make_ctx(mock_page)

        ambiguous_status = SubmissionStatus(
            confirmed=False, status="ambiguous", detail="confirmation_probe_failed"
        )
        with patch.object(adapter, "check_status", return_value=ambiguous_status):
            res = adapter.submit_application(ctx)

        assert res["success"] is False
        assert "ambiguous" in res["error"]
        # Must not proceed to resolution
        assert "resolution_failed" not in res.get("error", "")

    def test_already_confirmed_zero_clicks(self):
        """When check_status reports already confirmed, returns success without clicking."""
        from unittest.mock import MagicMock, patch
        adapter = UnstopAdapter()
        mock_page = MagicMock()
        mock_page.url = "https://unstop.com/register/success"
        mock_page.locator.return_value.count.return_value = 0
        ctx = self._make_ctx(mock_page)

        confirmed = SubmissionStatus(
            confirmed=True,
            status="confirmed",
            confirmation_ref="UNSTOP-CONFIRMED-42",
            detail="submission_confirmed_success",
        )
        with patch.object(adapter, "check_status", return_value=confirmed):
            res = adapter.submit_application(ctx)

        assert res["success"] is True
        assert res["confirmed"] is True
        assert res.get("already_confirmed") is True
        assert res["confirmation_ref"] == "UNSTOP-CONFIRMED-42"
        # Must not have clicked anything
        mock_page.locator.return_value.click.assert_not_called()

    def test_unknown_status_zero_clicks(self):
        """When check_status returns unknown, submit fails closed."""
        from unittest.mock import MagicMock, patch
        adapter = UnstopAdapter()
        mock_page = MagicMock()
        mock_page.url = "https://unstop.com/jobs/apply"
        mock_page.locator.return_value.count.return_value = 0
        ctx = self._make_ctx(mock_page)

        unknown_status = SubmissionStatus(
            confirmed=False, status="unknown", detail="No signal yet"
        )
        with patch.object(adapter, "check_status", return_value=unknown_status):
            res = adapter.submit_application(ctx)

        assert res["success"] is False
        assert "unknown" in res["error"]

    def test_unexpected_pre_status_bounded_code(self):
        """Completely unexpected status value is sanitized to pre_status_unexpected in bounded error code."""
        from unittest.mock import MagicMock, patch
        adapter = UnstopAdapter()
        mock_page = MagicMock()
        mock_page.url = "https://unstop.com/jobs/apply"
        mock_page.locator.return_value.count.return_value = 0
        ctx = self._make_ctx(mock_page)

        weird_status = SubmissionStatus(
            confirmed=False,
            status="INTERNAL_ERROR_WITH_CREDENTIAL_LEAK_xyz123",
            detail="raw_db_password=s3cr3t&token=xyz",
        )
        with patch.object(adapter, "check_status", return_value=weird_status):
            res = adapter.submit_application(ctx)

        assert res["success"] is False
        assert "pre_status_unexpected" in res["error"]
        # Bounded: raw detail and credential fragments must not appear in error
        assert "s3cr3t" not in res.get("error", "")
        assert "password" not in res.get("error", "")
        assert "token=xyz" not in res.get("error", "")

    def test_pre_status_non_boolean_confirmed_fails_closed(self):
        """When check_status returns truthy non-bool confirmed (e.g. 1), submit fails closed."""
        from unittest.mock import MagicMock, patch
        adapter = UnstopAdapter()
        mock_page = MagicMock()
        mock_page.url = "https://unstop.com/jobs/apply"
        mock_page.locator.return_value.count.return_value = 0
        ctx = self._make_ctx(mock_page)

        # confirmed is int 1, not bool
        malformed_status = MagicMock()
        malformed_status.confirmed = 1
        malformed_status.status = "not_submitted"
        with patch.object(adapter, "check_status", return_value=malformed_status):
            res = adapter.submit_application(ctx)

        assert res["success"] is False
        assert res["error"] == "pre_status_probe_failed"

    def test_pre_status_non_string_status_fails_closed(self):
        """When check_status returns non-string status (e.g. 123), submit fails closed."""
        from unittest.mock import MagicMock, patch
        adapter = UnstopAdapter()
        mock_page = MagicMock()
        mock_page.url = "https://unstop.com/jobs/apply"
        mock_page.locator.return_value.count.return_value = 0
        ctx = self._make_ctx(mock_page)

        malformed_status = MagicMock()
        malformed_status.confirmed = False
        malformed_status.status = 123
        with patch.object(adapter, "check_status", return_value=malformed_status):
            res = adapter.submit_application(ctx)

        assert res["success"] is False
        assert res["error"] == "pre_status_probe_failed"

    def test_pre_status_missing_structure_fails_closed(self):
        """When check_status returns an object missing expected attributes, submit fails closed."""
        from unittest.mock import MagicMock, patch
        adapter = UnstopAdapter()
        mock_page = MagicMock()
        mock_page.url = "https://unstop.com/jobs/apply"
        mock_page.locator.return_value.count.return_value = 0
        ctx = self._make_ctx(mock_page)

        class EmptyStatus:
            pass

        with patch.object(adapter, "check_status", return_value=EmptyStatus()):
            res = adapter.submit_application(ctx)

        assert res["success"] is False
        assert res["error"] == "pre_status_probe_failed"

    def test_pre_click_origin_invalid(self, page):
        """Pre-click safety envelope rejects URL redirected to foreign origin before click."""
        html = """
        <form id="app-form" action="/apply" method="POST">
          <button type="submit" id="unstop_submit">Submit Application</button>
        </form>
        """
        set_offline_page(page, html, "https://unstop.com/jobs/123/apply")
        adapter = UnstopAdapter()
        ctx = self._make_ctx(page)

        # First check_status succeeds (Gate 3).
        # Before click, simulate navigation/redirect by monkey-patching page.url
        real_url_attr = type(page).url

        call_count = 0
        def dynamic_url(self):
            nonlocal call_count
            call_count += 1
            if call_count > 2:  # Gate 1 passes, but pre-click fails
                return "https://attacker.example.com/stolen"
            return "https://unstop.com/jobs/123/apply"

        with patch.object(type(page), "url", new=property(dynamic_url)):
            res = adapter.submit_application(ctx)

        assert res["success"] is False
        assert res["error"] == "pre_click_origin_invalid"

    def test_pre_click_challenge_detected(self, page):
        """Pre-click safety envelope fails closed when bot challenge appears before click."""
        html = """
        <form id="app-form" action="/apply" method="POST">
          <button type="submit" id="unstop_submit">Submit Application</button>
        </form>
        """
        set_offline_page(page, html, "https://unstop.com/jobs/123/apply")
        adapter = UnstopAdapter()
        ctx = self._make_ctx(page)

        orig_locator = page.locator
        def dynamic_locator(selector, *args, **kwargs):
            loc = orig_locator(selector, *args, **kwargs)
            if "challenges.cloudflare" in selector:
                # Gate 2 passes (0 challenges), pre-click detects challenge (1)
                dynamic_locator.call_count = getattr(dynamic_locator, "call_count", 0) + 1
                if dynamic_locator.call_count > 1:
                    from unittest.mock import MagicMock
                    fake_loc = MagicMock()
                    fake_loc.count.return_value = 1
                    return fake_loc
            return loc

        with patch.object(page, "locator", side_effect=dynamic_locator):
            res = adapter.submit_application(ctx)

        assert res["success"] is False
        assert res["error"] == "pre_click_challenge_detected"

    def test_pre_click_challenge_probe_failed(self, page):
        """Pre-click safety envelope fails closed when challenge probe raises before click."""
        html = """
        <form id="app-form" action="/apply" method="POST">
          <button type="submit" id="unstop_submit">Submit Application</button>
        </form>
        """
        set_offline_page(page, html, "https://unstop.com/jobs/123/apply")
        adapter = UnstopAdapter()
        ctx = self._make_ctx(page)

        orig_locator = page.locator
        def dynamic_locator(selector, *args, **kwargs):
            loc = orig_locator(selector, *args, **kwargs)
            if "challenges.cloudflare" in selector:
                dynamic_locator.call_count = getattr(dynamic_locator, "call_count", 0) + 1
                if dynamic_locator.call_count > 1:
                    raise RuntimeError("challenge_dom_disconnected")
            return loc

        with patch.object(page, "locator", side_effect=dynamic_locator):
            res = adapter.submit_application(ctx)

        assert res["success"] is False
        assert res["error"] == "pre_click_challenge_probe_failed"

    def test_pre_click_status_probe_failed(self, page):
        """Pre-click safety envelope fails closed when status check raises before click."""
        html = """
        <form id="app-form" action="/apply" method="POST">
          <button type="submit" id="unstop_submit">Submit Application</button>
        </form>
        """
        set_offline_page(page, html, "https://unstop.com/jobs/123/apply")
        adapter = UnstopAdapter()
        ctx = self._make_ctx(page)

        gate3_status = SubmissionStatus(confirmed=False, status="not_submitted", detail="ok")
        call_count = 0
        def dynamic_check_status(app_ctx):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return gate3_status
            raise RuntimeError("network_reset_before_click")

        with patch.object(adapter, "check_status", side_effect=dynamic_check_status):
            res = adapter.submit_application(ctx)

        assert res["success"] is False
        assert res["error"] == "pre_click_status_probe_failed"

    def test_pre_click_status_not_allowed(self, page):
        """Pre-click safety envelope fails closed when status becomes ambiguous before click."""
        html = """
        <form id="app-form" action="/apply" method="POST">
          <button type="submit" id="unstop_submit">Submit Application</button>
        </form>
        """
        set_offline_page(page, html, "https://unstop.com/jobs/123/apply")
        adapter = UnstopAdapter()
        ctx = self._make_ctx(page)

        gate3_status = SubmissionStatus(confirmed=False, status="not_submitted", detail="ok")
        pre_click_ambiguous = SubmissionStatus(confirmed=False, status="ambiguous", detail="session_drop")

        call_count = 0
        def dynamic_check_status(app_ctx):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return gate3_status
            return pre_click_ambiguous

        with patch.object(adapter, "check_status", side_effect=dynamic_check_status):
            res = adapter.submit_application(ctx)

        assert res["success"] is False
        assert res["error"] == "pre_click_status_not_allowed"


# ── 10. evaluate_handle() → ElementHandle Verification Tests ─────────────────

class TestEvaluateHandleElementVerification:
    """Verifies that evaluate_handle results are validated via as_element()."""

    def test_missing_as_element_fails_closed(self):
        """evaluate_handle returning an object without callable as_element raises missing_or_invalid_form_handle."""
        from unittest.mock import MagicMock
        mock_page = MagicMock()

        # Build a mock handle that reports has_form=True but whose evaluate_handle
        # returns an object lacking as_element
        raw_jsh_no_as_elem = MagicMock(spec=[])  # No as_element attribute

        def make_handle():
            h = MagicMock()
            h.evaluate.side_effect = lambda script, *_args: {
                "tag_name": "button",
                "raw_text": "Submit Application",
                "control_type": None,
                "element_id": None,
                "name": None,
                "role": None,
                "aria_label": None,
                "title": None,
                "data_testid": None,
                "is_disabled": False,
                "is_visible": True,
                "form_action": "/apply",
                "form_method": "POST",
                "is_in_active_form": True,
                "has_form": True,
                "form_id": "app-form",
                "css_classes": [],
            }
            h.evaluate_handle.return_value = raw_jsh_no_as_elem
            h.is_visible.return_value = True
            h.is_enabled.return_value = True
            return h

        mock_page.query_selector_all.return_value = [make_handle()]

        with pytest.raises(RuntimeError, match="missing_or_invalid_form_handle"):
            inspect_submission_controls(mock_page)

    def test_null_js_handle_fails_closed(self):
        """evaluate_handle returning a JSHandle wrapping null raises missing_or_invalid_form_handle."""
        from unittest.mock import MagicMock
        mock_page = MagicMock()

        # Build a mock handle that reports has_form=True but whose evaluate_handle
        # returns a JSHandle that wraps null (as_element() returns None)
        null_jsh = MagicMock()
        null_jsh.as_element.return_value = None  # Simulates null ElementHandle

        def make_handle():
            h = MagicMock()
            h.evaluate.side_effect = lambda script, *_args: {
                "tag_name": "button",
                "raw_text": "Submit Application",
                "control_type": None,
                "element_id": None,
                "name": None,
                "role": None,
                "aria_label": None,
                "title": None,
                "data_testid": None,
                "is_disabled": False,
                "is_visible": True,
                "form_action": "/apply",
                "form_method": "POST",
                "is_in_active_form": True,
                "has_form": True,
                "form_id": "app-form",
                "css_classes": [],
            }
            h.evaluate_handle.return_value = null_jsh
            h.is_visible.return_value = True
            h.is_enabled.return_value = True
            return h

        mock_page.query_selector_all.return_value = [make_handle()]

        with pytest.raises(RuntimeError, match="missing_or_invalid_form_handle"):
            inspect_submission_controls(mock_page)

    def test_valid_element_handle_passes(self):
        """evaluate_handle returning a valid ElementHandle proceeds without error."""
        from unittest.mock import MagicMock
        mock_page = MagicMock()

        valid_element_handle = MagicMock()  # Valid element handle (non-null)

        valid_jsh = MagicMock()
        valid_jsh.as_element.return_value = valid_element_handle  # Non-null

        def make_handle():
            h = MagicMock()
            h.evaluate.side_effect = lambda script, *_args: {
                "tag_name": "button",
                "raw_text": "Submit Application",
                "control_type": None,
                "element_id": None,
                "name": None,
                "role": None,
                "aria_label": None,
                "title": None,
                "data_testid": None,
                "is_disabled": False,
                "is_visible": True,
                "form_action": "/apply",
                "form_method": "POST",
                "is_in_active_form": True,
                "has_form": True,
                "form_id": "app-form",
                "css_classes": [],
            }
            h.evaluate_handle.return_value = valid_jsh
            h.is_visible.return_value = True
            h.is_enabled.return_value = True
            return h

        mock_page.query_selector_all.return_value = [make_handle()]

        results = inspect_submission_controls(mock_page)
        assert len(results) == 1
        cand, handle = results[0]
        assert cand.form_handle is valid_element_handle


# ── 11. Ancestor Chain Visibility Tests ──────────────────────────────────────

class TestAncestorChainVisibility:
    """Verifies that visibility inspection traverses the full ancestor chain."""

    def test_button_hidden_via_parent_display_none(self, page):
        """Button inside display:none parent is invisible per DOM inspection."""
        html = """<!DOCTYPE html>
<html><body>
<form action="/apply" method="post">
  <div style="display:none">
    <button id="unstop_submit">Submit Application</button>
  </div>
</form>
</body></html>"""
        set_offline_page(page, html)
        results = inspect_submission_controls(page)
        # The button should be detected but marked not visible
        submit_buttons = [c for c, _ in results if c.text == "submit application"]
        assert submit_buttons, "Expected to find the button"
        assert submit_buttons[0].is_visible is False, "Button behind display:none parent must be invisible"

    def test_button_hidden_via_ancestor_visibility_hidden(self, page):
        """Button inside visibility:hidden ancestor is invisible."""
        html = """<!DOCTYPE html>
<html><body>
<form action="/apply" method="post">
  <section style="visibility:hidden">
    <div>
      <button id="unstop_submit">Submit Application</button>
    </div>
  </section>
</form>
</body></html>"""
        set_offline_page(page, html)
        results = inspect_submission_controls(page)
        submit_buttons = [c for c, _ in results if c.text == "submit application"]
        assert submit_buttons, "Expected to find the button"
        assert submit_buttons[0].is_visible is False, "Button behind visibility:hidden ancestor must be invisible"

    def test_button_hidden_via_ancestor_opacity_zero(self, page):
        """Button inside opacity:0 ancestor is invisible."""
        html = """<!DOCTYPE html>
<html><body>
<form action="/apply" method="post">
  <div style="opacity:0">
    <button id="unstop_submit">Submit Application</button>
  </div>
</form>
</body></html>"""
        set_offline_page(page, html)
        results = inspect_submission_controls(page)
        submit_buttons = [c for c, _ in results if c.text == "submit application"]
        assert submit_buttons, "Expected to find the button"
        assert submit_buttons[0].is_visible is False, "Button behind opacity:0 ancestor must be invisible"

    def test_button_hidden_via_hidden_attribute_on_ancestor(self, page):
        """Button inside element with 'hidden' attribute is invisible."""
        html = """<!DOCTYPE html>
<html><body>
<form action="/apply" method="post">
  <div hidden>
    <button id="unstop_submit">Submit Application</button>
  </div>
</form>
</body></html>"""
        set_offline_page(page, html)
        results = inspect_submission_controls(page)
        submit_buttons = [c for c, _ in results if c.text == "submit application"]
        assert submit_buttons, "Expected to find the button"
        assert submit_buttons[0].is_visible is False, "Button inside hidden-attribute ancestor must be invisible"

    def test_button_hidden_via_aria_hidden_ancestor(self, page):
        """Button inside aria-hidden=true ancestor is invisible."""
        html = """<!DOCTYPE html>
<html><body>
<form action="/apply" method="post">
  <div aria-hidden="true">
    <button id="unstop_submit">Submit Application</button>
  </div>
</form>
</body></html>"""
        set_offline_page(page, html)
        results = inspect_submission_controls(page)
        submit_buttons = [c for c, _ in results if c.text == "submit application"]
        assert submit_buttons, "Expected to find the button"
        assert submit_buttons[0].is_visible is False, "Button behind aria-hidden=true ancestor must be invisible"

    def test_button_hidden_via_inert_ancestor(self, page):
        """Button inside inert ancestor is invisible."""
        html = """<!DOCTYPE html>
<html><body>
<form action="/apply" method="post">
  <div inert>
    <button id="unstop_submit">Submit Application</button>
  </div>
</form>
</body></html>"""
        set_offline_page(page, html)
        results = inspect_submission_controls(page)
        submit_buttons = [c for c, _ in results if c.text == "submit application"]
        assert submit_buttons, "Expected to find the button"
        assert submit_buttons[0].is_visible is False, "Button inside inert ancestor must be invisible"

    def test_visible_button_not_affected_by_ancestor_checks(self, page):
        """A normally visible button with visible ancestors passes the ancestor check."""
        html = """<!DOCTYPE html>
<html><body>
<form action="/apply" method="post">
  <div style="display:block;visibility:visible;opacity:1">
    <button id="unstop_submit">Submit Application</button>
  </div>
</form>
</body></html>"""
        set_offline_page(page, html)
        results = inspect_submission_controls(page)
        submit_buttons = [c for c, _ in results if c.text == "submit application"]
        assert submit_buttons, "Expected to find the button"
        assert submit_buttons[0].is_visible is True, "Normally visible button must remain visible"

    def test_resolve_treats_hidden_ancestor_button_as_final_hidden(self, page):
        """resolve_final_submission_control returns FINAL_HIDDEN when button is behind hidden ancestor."""
        html = """<!DOCTYPE html>
<html><body>
<form action="/apply" method="post">
  <div style="display:none">
    <button id="unstop_submit">Submit Application</button>
  </div>
</form>
</body></html>"""
        set_offline_page(page, html)
        resolution = resolve_final_submission_control(page)
        assert resolution.status == SubmissionControlResolutionStatus.FINAL_HIDDEN
        assert resolution.locator is None
