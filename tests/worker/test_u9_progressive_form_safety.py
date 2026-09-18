"""U9 remediation regression tests: progressive-form safety, consent, candidate
data hardening, dropdown safety, /register/edit recognition, and persistence.

All tests are strictly OFFLINE: synthetic HTML fixtures rendered by headless
Playwright, in-memory SQLite, mocks, and static AST inspection.  No network, no
real Unstop page, no live submission, and no production database mutation.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import textwrap
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from playwright.sync_api import sync_playwright

from core.models.opportunity import Application, Opportunity
from core.models.profile import Profile, ProfileEducation
from core.schemas.profile import EducationCreate
from core.services import application_service, opportunity_service
from core.services.submission_service import issue_approval_token
from core.status import ApplicationStatus, OpportunityStatus, ReliabilityTier
from worker.adapters.base import (
    ApplicationContext,
    ExtractedListing,
    FillResult,
    SubmissionStatus,
)
from worker.adapters.submission_controls import (
    SubmissionControlCandidate,
    SubmissionControlClassification,
    SubmissionControlResolutionStatus,
    TerminalStepContext,
    classify_submission_control,
    resolve_final_submission_control,
)
from worker.adapters.unstop import (
    FormField,
    QuestionClassification,
    UnstopAdapter,
    evaluate_submission_confirmation,
)
from worker.engine.filler import ApplicationFiller, serialize_profile
from worker.runner import WorkerRunner

OPP_URL = "https://unstop.com/competitions/999999/register"
OPP_ID = 999999


# ── Fixtures ─────────────────────────────────────────────────────────────────


# Field ids chosen so the Unstop classifier resolves them from _candidate_data()
# (first name / last name / email / phone).  This keeps the fixture focused on
# the progressive-form terminal boundary instead of an unrelated classification
# path that would stop the fill before the step loop is ever reached.
_STEP_FIELDS = [
    ("player_firstname", "First Name*"),
    ("player_lastname", "Last Name*"),
    ("player_email", "Email*"),
    ("player_phone", "Phone*"),
]


def _progressive_html(step_count: int) -> str:
    """Synthetic Angular Material progressive registration form.

    The terminal step's navigation control is labelled "Next" — exactly the U9
    hazard.  Clicking the terminal control would submit the application.
    """
    headers = "\n".join(
        f'      <mat-step data-step="{i}">\n'
        f'        <mat-step-header aria-selected="{"true" if i == 0 else "false"}">'
        f"Step {i + 1}</mat-step-header>\n"
        f'        <div class="step-panel" style="{"display:none" if i else ""}">\n'
        f'          <label for="{field_id}">{label}</label>\n'
        f'          <input id="{field_id}" name="{field_id}" type="text" required />\n'
        f'          <button type="button" class="step-next" data-step="{i}">Next</button>\n'
        f"        </div>\n"
        f"      </mat-step>\n"
        for i, (field_id, label) in enumerate(_STEP_FIELDS[:step_count])
    )
    return f"""<!DOCTYPE html>
<html>
<body>
<form id="reg-form">
  <mat-horizontal-stepper id="stepper">
{headers}
  </mat-horizontal-stepper>
</form>
<script>
  // Total step count is read from the stepper headers, which Angular does NOT
  // remove when a step is completed, so terminality stays stable across clicks.
  var TOTAL_STEPS = document.querySelectorAll("#stepper mat-step-header").length;
  document.querySelectorAll(".step-next").forEach(function (btn) {{
    btn.addEventListener("click", function () {{
      var idx = Number(btn.dataset.step);
      var isLast = idx === TOTAL_STEPS - 1;
      if (isLast) {{
        // TERMINAL ACTION: this is what a real submission would do.
        window.__SUBMITTED = true;
        window.__SUBMIT_CLICKS = (window.__SUBMIT_CLICKS || 0) + 1;
        var form = document.getElementById("reg-form");
        if (form) {{ form.remove(); }}
        var banner = document.createElement("div");
        banner.textContent = "Successfully Registered";
        document.body.appendChild(banner);
      }} else {{
        // Angular destroys the completed step's navigation control.
        btn.remove();
        window.__NEXT_CLICKS = (window.__NEXT_CLICKS || 0) + 1;
        var headers = document.querySelectorAll("#stepper mat-step-header");
        var panels = document.querySelectorAll("#stepper .step-panel");
        headers.forEach(function (h, i) {{ h.setAttribute("aria-selected", String(i === idx + 1)); }});
        panels.forEach(function (p, i) {{ p.style.display = (i === idx + 1) ? "" : "none"; }});
      }}
    }});
  }});
</script>
</body>
</html>
"""


@pytest.fixture(scope="module")
def playwright_browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture()
def page(playwright_browser):
    pg = playwright_browser.new_page()
    yield pg
    pg.close()


def _set_offline_page(pg, html: str, url: str = OPP_URL) -> None:
    """Load HTML at a synthetic Unstop URL via route interception (no network)."""
    pg.route("**/*", lambda route: route.fulfill(status=200, content_type="text/html", body=html))
    pg.goto(url)


def _candidate_data() -> dict:
    return {
        "full_name": "Alex Morgan",
        "email": "alex@example.com",
        "phone": "9876543210",
        "location": "Mumbai",
        "gender": "Prefer not to say",
        "differently_abled": "No",
        "user_type": "College Students",
    }


def _adapter() -> UnstopAdapter:
    return UnstopAdapter()


# ── 1. Progressive-form terminal-step safety ─────────────────────────────────


class TestProgressiveFormSafety:
    def test_unstop_progressive_form_halts_before_terminal_next(self, page):
        """A 2-step progressive form: fill must stop at the terminal boundary."""
        _set_offline_page(page, _progressive_html(2))
        adapter = _adapter()
        ctx = ApplicationContext(
            opportunity_id=OPP_ID,
            listing_url=OPP_URL,
            adapter_name="unstop",
            tier=ReliabilityTier.EXPERIMENTAL,
            browser_page=page,
        )

        result = adapter.fill(ctx, _candidate_data())

        assert result.success is True
        assert result.status == "form_filled"
        assert result.is_terminal_reached is True

        # The intermediate Next WAS used to advance the form...
        assert page.evaluate("() => (window.__NEXT_CLICKS || 0) === 1")
        # ...and the terminal Next was NEVER clicked, so nothing was submitted.
        assert page.evaluate("() => window.__SUBMITTED !== true")
        assert page.evaluate("() => (window.__SUBMIT_CLICKS || 0) === 0")
        # The terminal control itself is still present and unclicked.
        assert page.locator("button.step-next").count() == 1

    def test_intermediate_next_is_still_clickable(self, page):
        """A 3-step form: both intermediate steps advance, terminal is not clicked."""
        _set_offline_page(page, _progressive_html(3))
        adapter = _adapter()
        ctx = ApplicationContext(
            opportunity_id=OPP_ID,
            listing_url=OPP_URL,
            adapter_name="unstop",
            tier=ReliabilityTier.EXPERIMENTAL,
            browser_page=page,
        )

        result = adapter.fill(ctx, _candidate_data())

        assert result.success is True
        assert result.status == "form_filled"
        assert result.is_terminal_reached is True
        # Two intermediate advances happened...
        assert page.evaluate("() => (window.__NEXT_CLICKS || 0) === 2")
        # ...and the terminal control never fired.
        assert page.evaluate("() => (window.__SUBMIT_CLICKS || 0) === 0")

    def test_progressive_fill_never_clicks_terminal_submit(self, page):
        """The terminal 'Next' — which performs the real submission — is never clicked by fill()."""
        _set_offline_page(page, _progressive_html(2))
        adapter = _adapter()
        ctx = ApplicationContext(
            opportunity_id=OPP_ID,
            listing_url=OPP_URL,
            adapter_name="unstop",
            tier=ReliabilityTier.EXPERIMENTAL,
            browser_page=page,
        )

        result = adapter.fill(ctx, _candidate_data())

        assert result.success is True
        # The form is still present: no submission happened.
        assert page.locator("#reg-form").count() == 1
        assert page.evaluate("() => window.__SUBMITTED !== true")

    def test_submission_state_machine_owns_the_terminal_click(self, page):
        """After fill halts, submit_application clicks the terminal Next exactly once and confirms."""
        _set_offline_page(page, _progressive_html(2))
        adapter = _adapter()
        ctx = ApplicationContext(
            opportunity_id=OPP_ID,
            listing_url=OPP_URL,
            adapter_name="unstop",
            tier=ReliabilityTier.EXPERIMENTAL,
            browser_page=page,
        )

        fill_result = adapter.fill(ctx, _candidate_data())
        assert fill_result.is_terminal_reached is True
        assert page.evaluate("() => (window.__SUBMIT_CLICKS || 0) === 0")
        # The terminal control is now resolved through the explicit
        # terminal-step context and clicked exactly once by the state machine.
        submit_result = adapter.submit_application(ctx)
        assert submit_result["success"] is True
        assert submit_result["confirmed"] is True
        assert page.evaluate("() => (window.__SUBMIT_CLICKS || 0) === 1")
        assert page.evaluate("() => window.__SUBMITTED === true")

    def test_terminal_detection_is_read_only(self, page):
        """Terminal-step detection never mutates the page."""
        _set_offline_page(page, _progressive_html(2))
        before = page.content()
        context = UnstopAdapter().detect_terminal_step_context(page)
        assert context.is_terminal is False  # step 0 of 2 on load
        assert context.total_steps == 2
        assert page.content() == before


# ── 2. Terminal "Next" control classification ────────────────────────────────


def _candidate(text: str, *, element_id: str = "") -> SubmissionControlCandidate:
    return SubmissionControlCandidate(
        candidate_id="c",
        tag_name="button",
        text=text,
        raw_text=text.title(),
        is_visible=True,
        is_disabled=False,
        is_in_active_form=True,
        element_id=element_id,
        control_type="button",
    )


def test_terminal_next_classified_as_final_submit_with_step_context():
    """'Next' + explicit terminal-step context -> FINAL_SUBMIT."""
    context = TerminalStepContext(
        is_terminal=True,
        evidence="angular_stepper_last_step_selected",
        current_step=1,
        total_steps=2,
    )
    assert (
        classify_submission_control(_candidate("next"), terminal_context=context)
        == SubmissionControlClassification.FINAL_SUBMIT
    )


def test_generic_next_remains_intermediate_without_terminal_context():
    """'Next' without terminal-step context stays INTERMEDIATE — never globally promoted."""
    assert classify_submission_control(_candidate("next")) == SubmissionControlClassification.INTERMEDIATE
    # Non-terminal step context must not promote either.
    context = TerminalStepContext(
        is_terminal=False, evidence="angular_stepper_intermediate_step", current_step=0, total_steps=2
    )
    assert (
        classify_submission_control(_candidate("next"), terminal_context=context)
        == SubmissionControlClassification.INTERMEDIATE
    )


def test_terminal_context_never_promotes_destructive_actions():
    """Back/Cancel are never promoted to FINAL_SUBMIT, even with terminal context."""
    context = TerminalStepContext(is_terminal=True, evidence="angular_stepper_last_step_selected")
    for text in ("back", "cancel", "dismiss", "previous"):
        assert (
            classify_submission_control(_candidate(text), terminal_context=context)
            == SubmissionControlClassification.INTERMEDIATE
        )


def test_terminal_next_resolves_on_real_dom(page):
    """resolve_final_submission_control promotes a terminal Next on the terminal step."""
    _set_offline_page(page, _progressive_html(2))
    # Advance to the terminal step.
    page.locator("button.step-next").first.click()
    page.wait_for_timeout(300)

    context = UnstopAdapter().detect_terminal_step_context(page)
    assert context.is_terminal is True

    resolution = resolve_final_submission_control(page, terminal_context=context)
    assert resolution.status == SubmissionControlResolutionStatus.EXACTLY_ONE_FINAL
    assert resolution.verified_control is not None
    assert resolution.verified_control.text == "next"
    assert resolution.locator is not None


def test_terminal_next_does_not_resolve_without_context(page):
    """Without terminal context, the same terminal Next page resolves to zero final controls."""
    _set_offline_page(page, _progressive_html(2))
    page.locator("button.step-next").first.click()
    page.wait_for_timeout(300)

    resolution = resolve_final_submission_control(page)
    assert resolution.status in (
        SubmissionControlResolutionStatus.ZERO_FINAL,
        SubmissionControlResolutionStatus.ONLY_INTERMEDIATE_OR_UNKNOWN,
    )
    assert resolution.locator is None


# ── 3. Candidate education duration ──────────────────────────────────────────


def test_profile_duration_is_serialized_when_explicitly_present(db_session):
    """An explicit duration on ProfileEducation is carried into candidate data."""
    profile = Profile(name="u9-duration-present", full_name="Alex Morgan")
    profile.education = [
        ProfileEducation(
            degree="B. Tech",
            branch="Computer Science",
            institution="Test Institute",
            graduation_year=2028,
            duration="4 Years",
        )
    ]
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    data = serialize_profile(profile)
    assert data["education"][0].get("duration") == "4 Years"

    adapter = _adapter()
    field = FormField(
        id="course_duration",
        name="course_duration",
        tag="input",
        type="text",
        label="Course Duration*",
        required=True,
        field_role="course_duration",
    )
    cls, val = adapter.classify_field(field, data)
    assert cls == QuestionClassification.PROFILE_FACT
    assert val == "4 Years"


def test_profile_without_duration_does_not_infer_duration(db_session):
    """Absence of duration stays absence — never inferred from degree/year/form options."""
    profile = Profile(name="u9-duration-absent", full_name="Alex Morgan")
    profile.education = [
        ProfileEducation(
            degree="B. Tech",
            branch="Computer Science",
            institution="Test Institute",
            graduation_year=2028,
        )
    ]
    db_session.add(profile)
    db_session.commit()
    db_session.refresh(profile)

    data = serialize_profile(profile)
    assert "duration" not in data["education"][0]
    assert data["education"][0].get("duration") is None

    adapter = _adapter()
    field = FormField(
        id="course_duration",
        name="course_duration",
        tag="mat-select",
        type="select",
        label="Course Duration*",
        required=True,
        field_role="course_duration",
        options=["3 Years", "4 Years", "5 Years"],  # form options are NOT candidate evidence
    )
    cls, val = adapter.classify_field(field, data)
    assert cls == QuestionClassification.REQUIRES_USER
    assert val is None


def test_missing_course_duration_requires_user():
    """A required course-duration field with no candidate value requires the user."""
    adapter = _adapter()
    field = FormField(
        id="course_duration",
        name="course_duration",
        tag="mat-select",
        type="select",
        label="Course Duration*",
        required=True,
        field_role="course_duration",
        options=["4 Years"],
    )
    cls, val = adapter.classify_field(field, {"full_name": "Alex", "education": [{"degree": "B. Tech"}]})
    assert cls == QuestionClassification.REQUIRES_USER
    assert val is None


def test_education_schema_supports_optional_duration():
    """The API schema accepts an explicit duration and defaults to None."""
    assert EducationCreate(degree="B. Tech", branch="CSE", institution="X").duration is None
    assert EducationCreate(
        degree="B. Tech", branch="CSE", institution="X", duration="4 Years"
    ).duration == "4 Years"


# ── 4. Consent handling ──────────────────────────────────────────────────────


def test_agree_terms_not_in_serialized_profile(db_session):
    """serialize_profile never emits synthetic consent."""
    profile = Profile(name="u9-consent", full_name="Alex Morgan")
    db_session.add(profile)
    db_session.commit()

    data = serialize_profile(profile)
    assert "agree_terms" not in data
    assert data.get("agree_terms") is None


def test_acceptance_is_not_profile_fact():
    """A profile-level agree_terms value is NOT consent — consent is application-scoped."""
    adapter = _adapter()
    field = FormField(
        id="acceptance",
        name="acceptance",
        tag="un-checkbox",
        type="checkbox",
        label="I accept terms and conditions*",
        required=True,
        field_role="acceptance",
    )

    cls, val = adapter.classify_field(field, {"full_name": "Alex", "agree_terms": True})
    assert cls == QuestionClassification.CONSENT
    assert val is None, "Profile-level agree_terms must not resolve consent"


def test_acceptance_without_explicit_consent_requires_user():
    """No explicit per-application answer → unresolved consent → REQUIRES_USER-style failure."""
    adapter = _adapter()
    field = FormField(
        id="acceptance",
        name="acceptance",
        tag="un-checkbox",
        type="checkbox",
        label="I accept terms and conditions*",
        required=True,
        field_role="acceptance",
    )
    cls, val = adapter.classify_field(field, {"full_name": "Alex"})
    assert cls == QuestionClassification.CONSENT
    assert val is None
    assert cls != QuestionClassification.PROFILE_FACT

    # The fill loop treats unresolved CONSENT on a required field as fail-closed.
    assert field.required and val is None


def test_acceptance_resolved_only_by_explicit_application_answer():
    """An explicit per-application affirmative answer resolves consent."""
    adapter = _adapter()
    field = FormField(
        id="acceptance",
        name="acceptance",
        tag="un-checkbox",
        type="checkbox",
        label="I accept terms and conditions*",
        required=True,
        field_role="acceptance",
    )
    for explicit in (
        {"question_id": "acceptance", "answer": "yes"},
        {"question_id": "acceptance", "answer": True},
        {"question_id": "consent", "answer": "I accept"},
    ):
        cls, val = adapter.classify_field(
            field, {"full_name": "Alex"}, custom_answers=[explicit]
        )
        assert cls == QuestionClassification.CONSENT
        assert val is True

    # A negative explicit answer is not affirmative consent.
    cls, val = adapter.classify_field(
        field, {"full_name": "Alex"}, custom_answers=[{"question_id": "acceptance", "answer": "no"}]
    )
    assert cls == QuestionClassification.CONSENT
    assert val is None


def test_fill_does_not_check_terms_box_without_explicit_consent(page):
    """A required consent checkbox with no explicit answer is left untouched and fill fails closed."""
    html = """
    <form id="reg-form">
      <label for="player_firstname">First Name*</label>
      <input id="player_firstname" name="player_firstname" type="text" required />
      <un-checkbox id="acceptance" name="acceptance"
                   onclick="this.setAttribute('data-consent-clicked', 'true')">
        <label>I accept terms and conditions*</label>
      </un-checkbox>
      <button type="button" id="unstop_submit">Complete Registration</button>
    </form>
    """
    _set_offline_page(page, html)
    adapter = _adapter()
    ctx = ApplicationContext(
        opportunity_id=OPP_ID,
        listing_url=OPP_URL,
        adapter_name="unstop",
        tier=ReliabilityTier.EXPERIMENTAL,
        browser_page=page,
    )

    result = adapter.fill(ctx, _candidate_data())

    assert result.success is False
    assert result.status == "manual_required"
    # The unresolved CONSENT classification is what stopped the fill.
    assert result.error_reason is not None
    assert "CONSENT" in result.error_reason
    assert "requires user decision" in result.error_reason
    # The checkbox was never checked merely because it existed.
    box = page.locator("un-checkbox#acceptance")
    assert box.count() > 0
    assert box.get_attribute("data-consent-clicked") is None
    # And the final control was never touched.
    assert page.evaluate("() => window.__SUBMITTED !== true")


# ── 5. Dropdown safety ───────────────────────────────────────────────────────


_MAT_SELECT_HTML = """
<form id="reg-form">
  <label for="course_pursuing">Course Pursuing*</label>
  <mat-select id="course_pursuing" name="course_pursuing" placeholder="Select Course" required
              style="display:block; width:200px; height:40px; border:1px solid #ccc">
    <mat-option data-opt="0" onclick="window.__OPTION_CLICKED = (window.__OPTION_CLICKED || 0) + 1">
      Engineering</mat-option>
    <mat-option data-opt="1" onclick="window.__OPTION_CLICKED = (window.__OPTION_CLICKED || 0) + 1">
      Management</mat-option>
  </mat-select>
</form>
"""


def test_mat_select_unknown_option_fails_closed(page):
    """A candidate value with no matching dropdown option is never substituted."""
    _set_offline_page(page, _MAT_SELECT_HTML)
    adapter = _adapter()
    ctx = ApplicationContext(
        opportunity_id=OPP_ID,
        listing_url=OPP_URL,
        adapter_name="unstop",
        tier=ReliabilityTier.EXPERIMENTAL,
        browser_page=page,
    )
    candidate = _candidate_data()
    candidate["course_pursuing"] = "Law"  # not among the offered options

    result = adapter.fill(ctx, candidate)

    assert result.success is False
    assert result.status == "manual_required"
    assert "no dropdown option" in result.error_reason
    # No option was clicked: nothing was selected on the candidate's behalf.
    assert page.evaluate("() => (window.__OPTION_CLICKED || 0) === 0")


def test_mat_select_known_option_is_selected(page):
    """A matching candidate value is selected exactly."""
    _set_offline_page(page, _MAT_SELECT_HTML)
    adapter = _adapter()
    ctx = ApplicationContext(
        opportunity_id=OPP_ID,
        listing_url=OPP_URL,
        adapter_name="unstop",
        tier=ReliabilityTier.EXPERIMENTAL,
        browser_page=page,
    )
    candidate = _candidate_data()
    candidate["course_pursuing"] = "Engineering"

    result = adapter.fill(ctx, candidate)

    assert result.success is True
    assert page.evaluate("() => (window.__OPTION_CLICKED || 0) === 1")


_RADIO_HTML = """
<form id="reg-form">
  <label>How did you hear about us?*</label>
  <un-radio-group name="user_type" formcontrolname="user_type">
    <un-radio><label
        onclick="window.__RADIO_CLICKED = (window.__RADIO_CLICKED || 0) + 1">College Students</label></un-radio>
    <un-radio><label
        onclick="window.__RADIO_CLICKED = (window.__RADIO_CLICKED || 0) + 1">Professional</label></un-radio>
  </un-radio-group>
  <button type="button" id="unstop_submit">Complete Registration</button>
</form>
"""


def test_radio_group_unknown_value_fails_closed(page):
    """A candidate value with no matching radio option is never substituted."""
    _set_offline_page(page, _RADIO_HTML)
    adapter = _adapter()
    ctx = ApplicationContext(
        opportunity_id=OPP_ID,
        listing_url=OPP_URL,
        adapter_name="unstop",
        tier=ReliabilityTier.EXPERIMENTAL,
        browser_page=page,
    )
    candidate = _candidate_data()
    candidate["user_type"] = "Working Professional"  # not an offered option

    result = adapter.fill(ctx, candidate)

    # The radio group was never clicked on the candidate's behalf.
    assert page.evaluate("() => (window.__RADIO_CLICKED || 0) === 0")
    assert result.success is False
    assert result.status == "manual_required"


def test_radio_group_known_value_is_selected(page):
    """A matching candidate value selects exactly that radio option."""
    _set_offline_page(page, _RADIO_HTML)
    adapter = _adapter()
    ctx = ApplicationContext(
        opportunity_id=OPP_ID,
        listing_url=OPP_URL,
        adapter_name="unstop",
        tier=ReliabilityTier.EXPERIMENTAL,
        browser_page=page,
    )
    candidate = _candidate_data()
    candidate["user_type"] = "Professional"

    result = adapter.fill(ctx, candidate)

    assert result.success is True
    selected = page.locator("un-radio-group[name='user_type'] label").filter(has_text="Professional")
    assert selected.count() == 1


def test_update_details_control_is_not_a_fresh_submission():
    """A post-registration 'Update Details' control is never a final submission."""
    context = TerminalStepContext(is_terminal=True, evidence="angular_stepper_last_step_selected")
    assert (
        classify_submission_control(_candidate("update details"), terminal_context=context)
        == SubmissionControlClassification.UNKNOWN
    )
    assert (
        classify_submission_control(_candidate("update details"))
        == SubmissionControlClassification.UNKNOWN
    )


def test_no_first_option_dropdown_fallback():
    """Static guarantee: fill() never selects an arbitrary first option.

    An option-bearing locator is only ever consumed by
    ``.filter(has_text=<candidate value>)`` (exact-match selection) or by
    ``_match_option_by_text()`` (exact normalized match).  Any other option
    locator dereferenced with ``.first``/``.last``/``.nth(0)`` would be a blind
    arbitrary selection and must not exist.
    """
    source = textwrap.dedent(inspect.getsource(UnstopAdapter.fill))
    assert "fallback to first option" not in source

    tree = ast.parse(source)
    option_selector = re.compile(r"mat-option|mat-mdc-option|autocomplete-content")

    def _is_option_locator(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "locator"
            and any(
                isinstance(a, ast.Constant)
                and isinstance(a.value, str)
                and option_selector.search(a.value)
                for a in node.args
            )
        )

    # Consumers that select by matching the candidate value are the only safe
    # ways to dereference an option list.
    guarded_ids: set[int] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if node.func.attr == "filter" and _is_option_locator(node.func.value):
            # page.locator(<options>).filter(has_text=<candidate value>)
            guarded_ids.add(id(node.func.value))
        elif node.func.attr == "all" and _is_option_locator(node.func.value):
            # page.locator(<options>).all() - safe, just reading options
            guarded_ids.add(id(node.func.value))
        elif node.func.attr == "_match_option_by_text":
            # self._match_option_by_text(page.locator(<options>), <candidate value>)
            for arg in node.args:
                if _is_option_locator(arg):
                    guarded_ids.add(id(arg))

    unguarded = [
        node
        for node in ast.walk(tree)
        if _is_option_locator(node) and id(node) not in guarded_ids
    ]
    assert not unguarded, (
        "fill() contains an option-bearing locator that is neither filtered by "
        "the candidate value nor passed to _match_option_by_text(): a blind "
        "dropdown fallback could select an arbitrary option."
    )

    # No unguarded option locator may be dereferenced by position.
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in ("first", "last"):
            for sub in ast.walk(node.value):
                if _is_option_locator(sub) and id(sub) not in guarded_ids:
                    pytest.fail(
                        "fill() dereferences an option locator with "
                        f".{node.attr} — a blind first/last option fallback."
                    )


# ── 6. /register/edit status recognition ─────────────────────────────────────


def _fake_page(url: str) -> MagicMock:
    """Fake page whose probes report a clean, unchallenged, unsubmitted form.

    The Cloudflare/challenge probe must report zero challenges, otherwise the
    submission state machine's gate 2 fails closed before status is ever read.
    """
    fake = MagicMock()
    fake.url = url

    def _locator(selector: str) -> MagicMock:
        loc = MagicMock()
        if "challenges.cloudflare" in selector or "challenge-stage" in selector:
            loc.count.return_value = 0
        else:
            loc.count.return_value = 1
            visible = MagicMock()
            visible.is_visible.return_value = True
            loc.nth.return_value = visible
        return loc

    fake.locator.side_effect = _locator
    return fake


def test_evaluate_submission_confirmation_register_edit_confirmed():
    """/register/edit is reliable evidence of an existing registration → confirmed."""
    status = evaluate_submission_confirmation(
        _fake_page("https://unstop.com/competitions/999999/register/edit"),
        opportunity_id=OPP_ID,
    )
    assert status.confirmed is True
    assert status.status == "confirmed"
    assert status.detail == "registration_edit_page"
    assert str(OPP_ID) in (status.confirmation_ref or "")

    # Sub-path variant.
    status2 = evaluate_submission_confirmation(
        _fake_page("https://unstop.com/competitions/999999/register/edit/"), opportunity_id=OPP_ID
    )
    assert status2.confirmed is True


def test_register_edit_blocks_resubmission_by_the_state_machine():
    """submit_application sees an already-confirmed state and clicks nothing."""
    adapter = _adapter()
    page = _fake_page("https://unstop.com/competitions/999999/register/edit")
    ctx = ApplicationContext(
        opportunity_id=OPP_ID,
        listing_url=OPP_URL,
        adapter_name="unstop",
        tier=ReliabilityTier.EXPERIMENTAL,
        browser_page=page,
    )

    result = adapter.submit_application(ctx)
    assert result["success"] is True
    assert result["confirmed"] is True
    assert result.get("already_confirmed") is True


def test_fresh_register_route_still_unsubmitted():
    """A plain /register route (no /edit) is still classified as unsubmitted."""
    status = evaluate_submission_confirmation(
        _fake_page("https://unstop.com/competitions/999999/register"), opportunity_id=OPP_ID
    )
    assert status.confirmed is False
    assert status.status == "not_submitted"


# ── 7. Persistence and duplicate protection ──────────────────────────────────


def _make_offline_unstop_opportunity(db_session) -> Opportunity:
    opp = opportunity_service.create_opportunity(
        db_session,
        title="U9 Offline Role",
        company="Offline Co",
        url=OPP_URL,
        source="unstop",
        reliability_tier="experimental",
    )
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.RECOMMENDED)
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.READY_TO_APPLY)
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.AWAITING_SUBMISSION)
    db_session.commit()
    return opp


def _fake_terminal_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.adapter_name = "unstop"
    adapter.tier = ReliabilityTier.EXPERIMENTAL
    adapter.requires_resume = False
    adapter.extract.return_value = ExtractedListing(
        company="Offline Co",
        title="U9 Offline Role",
        url=OPP_URL,
        custom_questions=[],
    )
    ctx = ApplicationContext(
        opportunity_id=OPP_ID,
        listing_url=OPP_URL,
        adapter_name="unstop",
        tier=ReliabilityTier.EXPERIMENTAL,
        browser_page=MagicMock(),
    )
    adapter.open_application.return_value = ctx
    adapter.fill.return_value = FillResult(
        success=True,
        status="form_filled",
        message="halted before terminal control",
        is_terminal_reached=True,
    )
    adapter.submit_application.return_value = {
        "success": True,
        "confirmed": True,
        "confirmation_ref": f"UNSTOP-CONFIRMED-{OPP_ID}",
        "clicked_selector": "ctrl_terminal_next",
        "url": "https://unstop.com/competitions/999999/register",
    }
    return adapter


def test_progressive_submit_persists_submitted_state(db_session):
    """form_filled -> claimed -> executed -> external success -> submitted -> opportunity applied."""
    opp = _make_offline_unstop_opportunity(db_session)
    app = application_service.create_application(
        db_session, opportunity_id=opp.id, adapter_name="unstop"
    )
    notes = {
        "adapter": "unstop",
        "tier": "experimental",
        "custom_answers": [],
        "approved_input_snapshot": {
            "url": OPP_URL,
            "resume_id": None,
            "custom_answers": [],
            "form_questions": [],
        },
    }
    application_service.transition_application_status(
        db_session, app.id, ApplicationStatus.FORM_FILLED, notes=json.dumps(notes)
    )
    db_session.commit()

    # Durable human approval + atomic claim are prerequisites.
    app.approved_at = datetime.now(timezone.utc)
    app.approved_by = "human_reviewer"
    db_session.commit()

    filler = ApplicationFiller(adapter_override=_fake_terminal_adapter())
    runner = WorkerRunner(poll_interval=60, filler=filler)

    result = runner.submit_single_application(db_session, app.id, worker_id="u9_worker")

    assert result["success"] is True
    assert result["confirmation_ref"] == f"UNSTOP-CONFIRMED-{OPP_ID}"

    db_session.refresh(app)
    db_session.refresh(opp)
    assert app.status == ApplicationStatus.SUBMITTED.value
    assert app.submitted_at is not None
    assert app.confirmation_ref == f"UNSTOP-CONFIRMED-{OPP_ID}"
    assert opp.status == OpportunityStatus.APPLIED.value
    assert app.submission_claimed_at is not None
    assert app.claimed_by == "u9_worker"

    filler.adapter_override.submit_application.assert_called_once()
    # The persisted notes carry the submission confirmation evidence.
    persisted = json.loads(app.notes)
    assert persisted["submission_confirmation"]["confirmed"] is True
    assert persisted["submission_confirmation"]["source"] == "platform_browser"


def test_persistence_failure_is_exposed_not_hidden(db_session):
    """If the platform did NOT confirm, the application must not be recorded as submitted."""
    opp = _make_offline_unstop_opportunity(db_session)
    app = application_service.create_application(
        db_session, opportunity_id=opp.id, adapter_name="unstop"
    )
    notes = {
        "adapter": "unstop",
        "tier": "experimental",
        "custom_answers": [],
        "approved_input_snapshot": {
            "url": OPP_URL,
            "resume_id": None,
            "custom_answers": [],
            "form_questions": [],
        },
    }
    application_service.transition_application_status(
        db_session, app.id, ApplicationStatus.FORM_FILLED, notes=json.dumps(notes)
    )
    db_session.commit()
    app.approved_at = datetime.now(timezone.utc)
    app.approved_by = "human_reviewer"
    db_session.commit()

    adapter = _fake_terminal_adapter()
    adapter.submit_application.return_value = {
        "success": False,
        "confirmed": False,
        "error": "pre_click_status_not_allowed:ambiguous",
    }
    filler = ApplicationFiller(adapter_override=adapter)
    runner = WorkerRunner(poll_interval=60, filler=filler)

    result = runner.submit_single_application(db_session, app.id, worker_id="u9_worker")

    assert result["success"] is False
    db_session.refresh(app)
    db_session.refresh(opp)
    assert app.status != ApplicationStatus.SUBMITTED.value
    assert app.submitted_at is None
    assert opp.status != OpportunityStatus.APPLIED.value


def test_duplicate_submission_remains_blocked(db_session):
    """An already-submitted application can never be submitted again."""
    opp = _make_offline_unstop_opportunity(db_session)
    app = application_service.create_application(
        db_session, opportunity_id=opp.id, adapter_name="unstop"
    )
    application_service.transition_application_status(
        db_session, app.id, ApplicationStatus.FORM_FILLED, notes=json.dumps({"adapter": "unstop"})
    )
    db_session.commit()

    # The approval token must be issued while the application is still in a
    # pre-submit status; it is the proof of authorization carried into the
    # duplicate check below.
    token = issue_approval_token(db_session, app.id)
    db_session.commit()
    # Mimic the durable human approval that would have been recorded before the
    # submission completed (the token minter itself resets approved_at).
    app.approved_at = datetime.now(timezone.utc)
    app.approved_by = "human_reviewer"
    db_session.commit()

    # Record a completed submission directly through the status machine.
    application_service.update_application_status(
        db_session,
        app.id,
        status=ApplicationStatus.SUBMITTED.value,
        submitted_at=datetime.now(timezone.utc),
        confirmation_ref="UNSTOP-CONFIRMED-EARLIER",
    )
    db_session.commit()

    # 1. confirm_and_submit refuses a duplicate.
    with pytest.raises(ValueError, match="Duplicate submission refused"):
        ApplicationFiller().confirm_and_submit(db_session, app.id, approval_token=token)

    # 2. The worker's single-application path rejects it before any browser work.
    filler = ApplicationFiller(adapter_override=_fake_terminal_adapter())
    runner = WorkerRunner(poll_interval=60, filler=filler)
    result = runner.submit_single_application(db_session, app.id, worker_id="u9_worker")
    assert result["success"] is False
    assert "form_filled or pending" in result["reason"]
    filler.adapter_override.submit_application.assert_not_called()


def test_unknown_submission_outcome_remains_fail_closed():
    """An unknown platform outcome never permits a click."""
    adapter = _adapter()
    page = MagicMock()
    page.url = OPP_URL
    challenge_loc = MagicMock()
    challenge_loc.count.return_value = 0
    page.locator.return_value = challenge_loc

    ctx = ApplicationContext(
        opportunity_id=OPP_ID,
        listing_url=OPP_URL,
        adapter_name="unstop",
        tier=ReliabilityTier.EXPERIMENTAL,
        browser_page=page,
    )
    unknown = SubmissionStatus(confirmed=False, status="unknown", detail="No signal yet")
    with patch.object(adapter, "check_status", return_value=unknown):
        result = adapter.submit_application(ctx)

    assert result["success"] is False
    assert "unknown" in result["error"]
    page.locator.return_value.click.assert_not_called()
