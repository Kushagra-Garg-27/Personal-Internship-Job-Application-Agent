"""Offline unit & integration tests for Unstop form extraction and classification.

Tests Angular custom components (un-radio-group, mat-select, un-checkbox)
and ensures strict demographic data protection (fail-closed to REQUIRES_USER).
"""

from __future__ import annotations

from pathlib import Path
import pytest
from playwright.sync_api import sync_playwright

from worker.adapters.base import ApplicationContext
from worker.adapters.unstop import (
    FormField,
    QuestionClassification,
    UnstopAdapter,
)

FIXTURE_ANGULAR_HTML = """
<!DOCTYPE html>
<html>
<head><title>Unstop Angular Registration Form</title></head>
<body>
  <app-notification class="ng-star-inserted">
    <button class="GTM_ACCEPT_COOKIE">Accept Cookies</button>
  </app-notification>

  <form class="ng-invalid">
    <div>
      <label for="player_firstname">First Name*</label>
      <input id="player_firstname" name="player_firstname" type="text" required />
    </div>

    <div>
      <label for="player_name_last">Last Name (if applicable)</label>
      <input id="player_name_last" name="player_name_last" type="text" />
    </div>

    <div>
      <label for="player_email">Email*</label>
      <input id="player_email" name="player_email" type="email" required />
    </div>

    <div>
      <label for="tel">Mobile*</label>
      <input id="tel" name="tel" type="tel" required />
    </div>

    <div>
      <label>Gender*</label>
      <un-radio-group name="user_gender" required>
        <label>Female</label>
        <label>Male</label>
        <label>Non-binary</label>
        <label>Prefer not to say</label>
      </un-radio-group>
    </div>

    <div>
      <label for="cities_input">Location*</label>
      <input id="cities_input" name="player_location" type="text" readonly required />
      <un-icon class="geo_location"></un-icon>
    </div>

    <div>
      <label>Are you differently abled?*</label>
      <un-radio-group name="user_differently_abled" required>
        <label>No</label>
        <label>Yes</label>
      </un-radio-group>
    </div>

    <div>
      <label>User Type*</label>
      <un-radio-group name="user_type" required>
        <label>College Students</label>
        <label>Professional</label>
        <label>Fresher</label>
      </un-radio-group>
    </div>

    <div>
      <label>Course Pursuing*</label>
      <mat-select name="course_pursuing" placeholder="Select Course" required>
        <mat-option>Engineering</mat-option>
        <mat-option>Management</mat-option>
      </mat-select>
    </div>

    <div>
      <un-checkbox id="acceptance" name="acceptance" onclick="this.setAttribute('data-consent-clicked', 'true')">
        <label>I accept terms and conditions*</label>
      </un-checkbox>
    </div>

    <div>
      <label>Why do you want to apply?</label>
      <textarea name="statement_of_purpose" rows="4"></textarea>
    </div>

    <div>
      <button type="button" data-test="save-form-btn">Next</button>
      <button type="button" id="unstop_submit" class="submit-btn">Complete Registration</button>
    </div>
  </form>
</body>
</html>
"""


@pytest.fixture(scope="module")
def playwright_instance():
    with sync_playwright() as p:
        yield p


def test_extract_angular_custom_components(playwright_instance):
    """Verify that un-radio-group, mat-select, and un-checkbox are correctly extracted."""
    browser = playwright_instance.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content(FIXTURE_ANGULAR_HTML)

    adapter = UnstopAdapter(headless=True, playwright_instance=playwright_instance)
    fields = adapter.extract_form_fields(page)

    field_roles = {f.field_role: f for f in fields}

    assert "first_name" in field_roles
    assert "email" in field_roles
    assert "phone" in field_roles
    assert "location" in field_roles

    # Check Angular components
    assert "gender" in field_roles
    gender_field = field_roles["gender"]
    assert gender_field.tag == "un-radio-group"
    assert gender_field.required is True
    assert "Prefer not to say" in gender_field.options

    assert "differently_abled" in field_roles
    da_field = field_roles["differently_abled"]
    assert da_field.tag == "un-radio-group"
    assert da_field.required is True
    assert "No" in da_field.options

    assert "user_type" in field_roles
    ut_field = field_roles["user_type"]
    assert ut_field.tag == "un-radio-group"
    assert ut_field.required is True

    assert "course_pursuing" in field_roles
    course_field = field_roles["course_pursuing"]
    assert course_field.tag == "mat-select"
    assert course_field.required is True

    assert "acceptance" in field_roles
    acc_field = field_roles["acceptance"]
    assert acc_field.tag == "un-checkbox"
    assert acc_field.required is True

    browser.close()


def test_classify_sensitive_demographic_fields():
    """Verify Rule #5: Demographic fields must NEVER be invented and classify as REQUIRES_USER."""
    adapter = UnstopAdapter()

    gender_field = FormField(
        id="gender",
        name="user_gender",
        tag="un-radio-group",
        type="radio_group",
        label="Gender*",
        required=True,
        field_role="gender",
    )

    da_field = FormField(
        id="differently_abled",
        name="user_differently_abled",
        tag="un-radio-group",
        type="radio_group",
        label="Are you differently abled?*",
        required=True,
        field_role="differently_abled",
    )

    # When candidate profile lacks demographic fields
    candidate_no_demographics = {
        "full_name": "Alex Morgan",
        "email": "alex@example.com",
        "phone": "+91 9876543210",
    }

    cls_gender, val_gender = adapter.classify_field(gender_field, candidate_no_demographics)
    assert cls_gender == QuestionClassification.REQUIRES_USER
    assert val_gender is None

    cls_da, val_da = adapter.classify_field(da_field, candidate_no_demographics)
    assert cls_da == QuestionClassification.REQUIRES_USER
    assert val_da is None

    # When candidate profile explicitly contains demographic data
    candidate_with_demographics = {
        "full_name": "Alex Morgan",
        "email": "alex@example.com",
        "phone": "+91 9876543210",
        "gender": "Prefer not to say",
        "differently_abled": "No",
    }

    cls_gender_ok, val_gender_ok = adapter.classify_field(gender_field, candidate_with_demographics)
    assert cls_gender_ok == QuestionClassification.PROFILE_FACT
    assert val_gender_ok == "Prefer not to say"

    cls_da_ok, val_da_ok = adapter.classify_field(da_field, candidate_with_demographics)
    assert cls_da_ok == QuestionClassification.PROFILE_FACT
    assert val_da_ok == "No"


def test_fill_fails_closed_when_demographics_missing(playwright_instance):
    """Verify that fill() stops and reports manual_required when required demographic fields are missing."""
    browser = playwright_instance.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content(FIXTURE_ANGULAR_HTML)

    adapter = UnstopAdapter(headless=True, playwright_instance=playwright_instance)

    app_ctx = ApplicationContext(
        opportunity_id=301,
        listing_url="https://unstop.com/competitions/test/register",
        adapter_name="unstop",
        tier=adapter.tier,
        browser_page=page,
    )

    # Incomplete candidate data lacking gender/disability
    candidate_data = {
        "full_name": "Alex Morgan",
        "email": "alex@example.com",
        "phone": "9876543210",
        "location": "Mumbai",
    }

    result = adapter.fill(app_ctx, candidate_data)

    assert result.success is False
    assert result.status == "manual_required"
    assert "requires user decision" in result.error_reason.lower()

    browser.close()


def test_fill_succeeds_and_stops_before_submit_when_demographics_present(playwright_instance):
    """Verify that fill() succeeds up to pre-submit review when trusted data is provided."""
    browser = playwright_instance.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content(FIXTURE_ANGULAR_HTML)

    adapter = UnstopAdapter(headless=True, playwright_instance=playwright_instance)

    app_ctx = ApplicationContext(
        opportunity_id=302,
        listing_url="https://unstop.com/competitions/test/register",
        adapter_name="unstop",
        tier=adapter.tier,
        browser_page=page,
    )

    candidate_data = {
        "full_name": "Alex Morgan",
        "email": "alex@example.com",
        "phone": "9876543210",
        "location": "Mumbai",
        "gender": "Prefer not to say",
        "differently_abled": "No",
        "user_type": "College Students",
        "course_pursuing": "Engineering",
        # U9: consent is application-scoped and is supplied explicitly below —
        # never as a profile attribute and never defaulted.
    }
    custom_answers = [
        {
            "question_id": "statement_of_purpose",
            "answer": "Looking forward to solving impactful engineering challenges.",
        },
        # Explicit, per-application consent is the only thing that resolves the
        # terms acceptance checkbox.
        {"question_id": "acceptance", "answer": "yes"},
    ]

    result = adapter.fill(app_ctx, candidate_data, custom_answers=custom_answers)

    assert result.success is True
    assert result.status == "ready_for_review"

    # Verify input values
    assert page.locator("input#player_firstname").input_value() == "Alex"
    assert page.locator("input#player_name_last").input_value() == "Morgan"
    assert page.locator("input#player_email").input_value() == "alex@example.com"
    assert page.locator("input#tel").input_value() == "9876543210"

    # Verify explicit consent was recorded on the acceptance checkbox
    acceptance_box = page.locator("un-checkbox#acceptance")
    assert acceptance_box.count() > 0
    assert acceptance_box.get_attribute("data-consent-clicked") == "true"

    # Verify SOP has AI draft tag
    sop_val = page.locator("textarea[name='statement_of_purpose']").input_value()
    assert "[AI DRAFT - PENDING APPROVAL]" in sop_val

    # CRITICAL CHECK: Final submission button was NEVER clicked
    submit_btn = page.locator("#unstop_submit")
    assert submit_btn.count() > 0

    browser.close()


def test_explicit_user_supplied_demographic_via_custom_answers():
    """Verify that demographic fields can be supplied explicitly by the candidate via custom_answers."""
    adapter = UnstopAdapter()

    gender_field = FormField(
        id="gender",
        name="user_gender",
        tag="un-radio-group",
        type="radio_group",
        label="Gender*",
        required=True,
        field_role="gender",
    )

    da_field = FormField(
        id="differently_abled",
        name="user_differently_abled",
        tag="un-radio-group",
        type="radio_group",
        label="Are you differently abled?*",
        required=True,
        field_role="differently_abled",
    )

    # Candidate profile has NO demographic data
    candidate_data = {
        "full_name": "Alex Morgan",
        "email": "alex@example.com",
    }

    # Explicit user answers supplied during review
    user_supplied_answers = [
        {"question_id": "user_gender", "answer": "Prefer not to say"},
        {"question_id": "user_differently_abled", "answer": "No"},
    ]

    cls_g, val_g = adapter.classify_field(gender_field, candidate_data, custom_answers=user_supplied_answers)
    assert cls_g == QuestionClassification.PROFILE_FACT
    assert val_g == "Prefer not to say"

    cls_da, val_da = adapter.classify_field(da_field, candidate_data, custom_answers=user_supplied_answers)
    assert cls_da == QuestionClassification.PROFILE_FACT
    assert val_da == "No"


def test_resume_upload_handled_in_fill(playwright_instance, tmp_path):
    """Verify that resume upload is handled cleanly during form fill."""
    resume_file = tmp_path / "alex_resume.pdf"
    resume_file.write_bytes(b"%PDF-1.4 Mock resume content")

    html = """
    <html><body>
      <form>
        <label for="player_firstname">First Name*</label>
        <input id="player_firstname" name="player_firstname" type="text" required />
        <label for="resume">Resume</label>
        <input id="resume" type="file" name="resume" />
        <button type="button" id="unstop_submit">Submit</button>
      </form>
    </body></html>
    """
    browser = playwright_instance.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content(html)

    adapter = UnstopAdapter(headless=True, playwright_instance=playwright_instance)
    app_ctx = ApplicationContext(
        opportunity_id=303,
        listing_url="https://unstop.com/jobs/test",
        adapter_name="unstop",
        tier=adapter.tier,
        browser_page=page,
    )

    candidate_data = {"full_name": "Alex Morgan", "email": "alex@example.com"}
    result = adapter.fill(app_ctx, candidate_data, resume_path=str(resume_file))

    assert result.success is True
    assert result.status == "ready_for_review"

    # Verify input file was attached
    files = page.locator("input[type='file']").evaluate("e => e.files.length")
    assert files == 1

    browser.close()


def test_guarantee_final_submit_control_is_never_invoked(playwright_instance):
    """Verify that form fill NEVER invokes the final submission control."""
    html = """
    <html><body>
      <form id="reg-form">
        <label for="player_firstname">First Name*</label>
        <input id="player_firstname" name="player_firstname" type="text" required />
        <button type="button" id="unstop_submit" class="submit-btn" onclick="window.__SUBMIT_CALLED = true">Complete Registration</button>
      </form>
    </body></html>
    """
    browser = playwright_instance.chromium.launch(headless=True)
    page = browser.new_page()
    page.set_content(html)

    adapter = UnstopAdapter(headless=True, playwright_instance=playwright_instance)
    app_ctx = ApplicationContext(
        opportunity_id=304,
        listing_url="https://unstop.com/jobs/test",
        adapter_name="unstop",
        tier=adapter.tier,
        browser_page=page,
    )

    candidate_data = {"full_name": "Alex Morgan", "email": "alex@example.com"}
    result = adapter.fill(app_ctx, candidate_data)

    assert result.success is True
    # Verify that window.__SUBMIT_CALLED was NEVER set
    submit_called = page.evaluate("() => window.__SUBMIT_CALLED === true")
    assert submit_called is False, "CRITICAL VIOLATION: Final submit button was clicked!"

    browser.close()


def test_dynamic_candidate_data_resolution():
    """Verify that candidate data (course, duration, graduation) is resolved dynamically."""
    adapter = UnstopAdapter()

    # Create mock FormFields for course_pursuing and course_duration
    course_field = FormField(
        id="course", name="course_pursuing", tag="input", type="text", label="Course Pursuing*", required=True, field_role="course_pursuing"
    )
    duration_field = FormField(
        id="duration", name="course_duration", tag="input", type="text", label="Course Duration*", required=True, field_role="course_duration"
    )
    grad_year_field = FormField(
        id="grad", name="graduation_year", tag="input", type="text", label="Graduation Year*", required=True, field_role="graduation_year"
    )

    # 1. Non-Engineering dynamic data
    candidate_arts = {
        "full_name": "Alex",
        "education": [{
            "domain": "Arts",
            "branch": "Fine Arts",
            "duration": "3 Years",
            "graduation_year": 2030
        }]
    }

    cls1, val1 = adapter.classify_field(course_field, candidate_arts)
    assert cls1 == QuestionClassification.PROFILE_FACT
    assert val1 == "Arts"

    cls2, val2 = adapter.classify_field(duration_field, candidate_arts)
    assert cls2 == QuestionClassification.PROFILE_FACT
    assert val2 == "3 Years"

    cls3, val3 = adapter.classify_field(grad_year_field, candidate_arts)
    assert cls3 == QuestionClassification.PROFILE_FACT
    assert val3 == "2030"

    # 2. Missing data should fail closed (requires user)
    candidate_missing = {
        "full_name": "Alex",
        "education": [{
            "domain": "",
            "branch": "",
            "duration": "",
            "graduation_year": None
        }]
    }

    cls_m1, val_m1 = adapter.classify_field(duration_field, candidate_missing)
    assert cls_m1 == QuestionClassification.REQUIRES_USER
    assert val_m1 is None

    cls_m2, val_m2 = adapter.classify_field(grad_year_field, candidate_missing)
    assert cls_m2 == QuestionClassification.REQUIRES_USER
    assert val_m2 is None


def test_dynamic_candidate_skills_resolution():
    """Verify that opportunity skills do not become candidate skills without evidence."""
    adapter = UnstopAdapter()

    # Form asks for specific skills via options
    skill_field = FormField(
        id="skills", name="skills", tag="un-checkbox", type="checkbox", label="Skills*", required=True, field_role="skills", options=["Java", "Python", "React"]
    )

    # 1. Candidate has Java and Python
    candidate_match = {
        "full_name": "Alex",
        "skills": [{"skill_name": "Java"}, {"skill_name": "Python"}, {"skill_name": "C++"}]
    }

    cls1, val1 = adapter.classify_field(skill_field, candidate_match)
    assert cls1 == QuestionClassification.PROFILE_FACT
    assert set(val1) == {"Java", "Python"}

    # 2. Candidate has NO matching skills
    candidate_no_match = {
        "full_name": "Alex",
        "skills": [{"skill_name": "C++"}, {"skill_name": "Ruby"}]
    }

    cls2, val2 = adapter.classify_field(skill_field, candidate_no_match)
    assert cls2 == QuestionClassification.REQUIRES_USER
    assert val2 is None

    # 3. Optional field with no match should resolve to safe inference
    optional_skill_field = FormField(
        id="skills", name="skills", tag="un-checkbox", type="checkbox", label="Skills", required=False, field_role="skills", options=["Java", "Python", "React"]
    )
    cls3, val3 = adapter.classify_field(optional_skill_field, candidate_no_match)
    assert cls3 == QuestionClassification.PROFILE_FACT
    assert val3 is None
