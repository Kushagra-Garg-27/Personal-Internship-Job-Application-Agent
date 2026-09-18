"""Tests for explicit candidate education domain (U10.2).

Verifies:
- Test A: Explicit domain ("Engineering") selects "Engineering" among UI options.
- Test B: Missing domain (None) fails closed as manual_required with 0 selections.
- Test C: Missing domain (None) never falls back to the first UI option ("Management").
- Test D: Case and whitespace normalization ("  engineering  " matches "Engineering").
- Schema & Model: ProfileEducation.domain is nullable, optional, and serialized only when present.
- Never infers or auto-derives domain from degree or branch.
"""

from __future__ import annotations

import pytest
from playwright.sync_api import sync_playwright

from core.models.profile import Profile, ProfileEducation
from core.schemas.profile import EducationCreate, EducationResponse
from core.status import ReliabilityTier
from worker.adapters.base import ApplicationContext
from worker.adapters.unstop import FormField, QuestionClassification, UnstopAdapter
from worker.engine.filler import serialize_profile

OPP_ID = 1755069
OPP_URL = f"https://unstop.com/competitions/{OPP_ID}/register"

_UNSTOP_DOMAIN_SELECT_HTML = """
<!DOCTYPE html>
<html>
<head>
  <style>
    mat-select { display: block; width: 200px; height: 40px; border: 1px solid #ccc; }
    mat-option { display: block; padding: 8px; cursor: pointer; }
  </style>
</head>
<body>
<form id="reg-form">
  <label for="course_pursuing">Course Pursuing*</label>
  <mat-select id="course_pursuing" name="course_pursuing" placeholder="Select Course" required
              style="display:block; width:200px; height:40px; border:1px solid #ccc">
    <div class="mat-mdc-select-trigger" style="width:100%; height:40px;">Select Course</div>
  </mat-select>
  <div class="cdk-overlay-pane" style="margin-top: 10px;">
    <mat-option data-val="Management" onclick="window.__CLICKED_OPTION = 'Management'; window.__OPTION_CLICK_COUNT = (window.__OPTION_CLICK_COUNT || 0) + 1">Management</mat-option>
    <mat-option data-val="Engineering" onclick="window.__CLICKED_OPTION = 'Engineering'; window.__OPTION_CLICK_COUNT = (window.__OPTION_CLICK_COUNT || 0) + 1">Engineering</mat-option>
    <mat-option data-val="Arts & Science" onclick="window.__CLICKED_OPTION = 'Arts & Science'; window.__OPTION_CLICK_COUNT = (window.__OPTION_CLICK_COUNT || 0) + 1">Arts & Science</mat-option>
    <mat-option data-val="Medicine" onclick="window.__CLICKED_OPTION = 'Medicine'; window.__OPTION_CLICK_COUNT = (window.__OPTION_CLICK_COUNT || 0) + 1">Medicine</mat-option>
    <mat-option data-val="Law" onclick="window.__CLICKED_OPTION = 'Law'; window.__OPTION_CLICK_COUNT = (window.__OPTION_CLICK_COUNT || 0) + 1">Law</mat-option>
  </div>
</form>
</body>
</html>
"""


@pytest.fixture(scope="module")
def playwright_browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def page(playwright_browser):
    ctx = playwright_browser.new_context()
    pg = ctx.new_page()
    yield pg
    pg.close()
    ctx.close()


def _set_page(page, html: str) -> None:
    page.goto("about:blank")
    page.set_content(html)
    page.evaluate("() => { window.__CLICKED_OPTION = null; window.__OPTION_CLICK_COUNT = 0; }")


def _candidate_base() -> dict:
    return {
        "full_name": "Kushagra Garg",
        "email": "kushagra@example.com",
        "phone": "9876543210",
        "user_type": "College Students",
        "location": "Pune, Maharashtra, India",
        "organization": "Vishwakarma Institute of Technology, Pune, Maharashtra",
        "differently_abled": "No",
        "gender": "Male",
    }


def _make_context(page) -> ApplicationContext:
    return ApplicationContext(
        opportunity_id=OPP_ID,
        listing_url=OPP_URL,
        adapter_name="unstop",
        tier=ReliabilityTier.EXPERIMENTAL,
        browser_page=page,
    )


# ── Schema & Model tests ───────────────────────────────────────────────────


def test_profile_education_domain_schema():
    """EducationCreate and EducationResponse support nullable domain."""
    schema = EducationCreate(
        degree="B.Tech/BE",
        branch="Computer Science",
        institution="VIT Pune",
        duration="4 Years",
        domain="Engineering",
    )
    assert schema.domain == "Engineering"

    # Default is None
    schema_default = EducationCreate(
        degree="B.Tech/BE",
        branch="Computer Science",
        institution="VIT Pune",
    )
    assert schema_default.domain is None


def test_profile_education_domain_serialization():
    """serialize_profile includes domain only when explicitly set."""
    p = Profile(name="test_user", full_name="Test User")
    edu_with_domain = ProfileEducation(
        degree="B.Tech/BE",
        branch="Computer Science",
        institution="VIT Pune",
        graduation_year=2028,
        duration="4 Years",
        domain="Engineering",
    )
    p.education = [edu_with_domain]

    serialized = serialize_profile(p)
    assert len(serialized["education"]) == 1
    assert serialized["education"][0]["domain"] == "Engineering"
    assert serialized["education"][0]["duration"] == "4 Years"

    # Without domain: domain key is not in serialized dict
    edu_without_domain = ProfileEducation(
        degree="B.Tech/BE",
        branch="Computer Science",
        institution="VIT Pune",
        graduation_year=2028,
        duration="4 Years",
        domain=None,
    )
    p.education = [edu_without_domain]
    serialized_none = serialize_profile(p)
    assert "domain" not in serialized_none["education"][0]


# ── Resolver & Classifier tests ───────────────────────────────────────────


def test_course_pursuing_classification_with_explicit_domain():
    """Adapter classifies course_pursuing as PROFILE_FACT when domain is present."""
    adapter = UnstopAdapter()
    field = FormField(
        id="course_pursuing",
        name="course_pursuing",
        tag="mat-select",
        type="select",
        label="Course Pursuing*",
        required=True,
        field_role="course_pursuing",
    )
    candidate = {
        "education": [{
            "degree": "B.Tech/BE",
            "branch": "Computer Science and Engineering",
            "domain": "Engineering",
        }]
    }
    cls, val = adapter.classify_field(field, candidate)
    assert cls == QuestionClassification.PROFILE_FACT
    assert val == "Engineering"


def test_course_pursuing_classification_missing_domain_requires_user():
    """Adapter classifies course_pursuing as REQUIRES_USER when domain is missing (no fallback to branch)."""
    adapter = UnstopAdapter()
    field = FormField(
        id="course_pursuing",
        name="course_pursuing",
        tag="mat-select",
        type="select",
        label="Course Pursuing*",
        required=True,
        field_role="course_pursuing",
    )
    candidate = {
        "education": [{
            "degree": "B.Tech/BE",
            "branch": "Computer Science and Engineering",
            # domain is None / absent
        }]
    }
    cls, val = adapter.classify_field(field, candidate)
    assert cls == QuestionClassification.REQUIRES_USER
    assert val is None


# ── Playwright DOM tests (Tests A, B, C, D) ───────────────────────────────


def test_a_explicit_domain_selects_engineering(page):
    """Test A: Explicit domain ('Engineering') selects 'Engineering' among UI options."""
    _set_page(page, _UNSTOP_DOMAIN_SELECT_HTML)
    adapter = UnstopAdapter()
    ctx = _make_context(page)

    candidate = _candidate_base()
    candidate["education"] = [{
        "degree": "B.Tech/BE",
        "branch": "Computer Science and Engineering",
        "domain": "Engineering",
        "duration": "4 Years",
        "graduation_year": 2028,
    }]

    result = adapter.fill(ctx, candidate)
    assert result.success is True
    assert page.evaluate("() => window.__CLICKED_OPTION") == "Engineering"
    assert page.evaluate("() => window.__OPTION_CLICK_COUNT") == 1


def test_b_missing_domain_returns_manual_required_zero_selections(page):
    """Test B: Missing domain (None) returns manual_required and performs zero arbitrary selections."""
    _set_page(page, _UNSTOP_DOMAIN_SELECT_HTML)
    adapter = UnstopAdapter()
    ctx = _make_context(page)

    candidate = _candidate_base()
    candidate["education"] = [{
        "degree": "B.Tech/BE",
        "branch": "Computer Science and Engineering",
        "duration": "4 Years",
        "graduation_year": 2028,
        # domain is absent
    }]

    result = adapter.fill(ctx, candidate)
    assert result.success is False
    assert result.status == "manual_required"
    assert "requires user decision" in (result.error_reason or "") or "requires user" in (result.error_reason or "").lower()
    # Zero options clicked
    assert page.evaluate("() => window.__OPTION_CLICK_COUNT || 0") == 0
    assert page.evaluate("() => window.__CLICKED_OPTION") is None


def test_c_missing_domain_no_first_option_fallback(page):
    """Test C: If domain=None and first UI option is 'Management', 'Management' is never selected."""
    _set_page(page, _UNSTOP_DOMAIN_SELECT_HTML)
    adapter = UnstopAdapter()
    ctx = _make_context(page)

    candidate = _candidate_base()
    candidate["education"] = [{
        "degree": "B.Tech/BE",
        "branch": "Computer Science and Engineering",
        "duration": "4 Years",
        "graduation_year": 2028,
        # domain is absent
    }]

    result = adapter.fill(ctx, candidate)
    assert result.success is False
    # Specifically assert Management was NOT chosen as a blind fallback
    assert page.evaluate("() => window.__CLICKED_OPTION !== 'Management'")
    assert page.evaluate("() => window.__OPTION_CLICK_COUNT || 0") == 0


def test_d_case_and_whitespace_normalization(page):
    """Test D: Explicit '  engineering  ' safely matches 'Engineering' via normalization."""
    _set_page(page, _UNSTOP_DOMAIN_SELECT_HTML)
    adapter = UnstopAdapter()
    ctx = _make_context(page)

    candidate = _candidate_base()
    candidate["education"] = [{
        "degree": "B.Tech/BE",
        "branch": "Computer Science and Engineering",
        "domain": "  engineering  ",
        "duration": "4 Years",
        "graduation_year": 2028,
    }]

    result = adapter.fill(ctx, candidate)
    assert result.success is True
    assert page.evaluate("() => window.__CLICKED_OPTION") == "Engineering"
    assert page.evaluate("() => window.__OPTION_CLICK_COUNT") == 1


# ── U10.4 Deterministic Field Role Classification Tests ───────────────────


def test_detect_field_role_course_stream_not_hijacked_by_course_label():
    """U10.4: An element with name='course_stream' and label='Course*' must NOT be classified as course_pursuing."""
    role = UnstopAdapter._detect_field_role(
        name="course_stream",
        el_id="mat-select-2",
        label="Course*",
        tag="mat-select",
    )
    assert role == "course_stream"


def test_detect_field_role_course_pursuing_with_domain_label():
    """U10.4: An element with name='course_pursuing' and label='Domain*' classifies as course_pursuing."""
    role = UnstopAdapter._detect_field_role(
        name="course_pursuing",
        el_id="mat-select-1",
        label="Domain*",
        tag="mat-select",
    )
    assert role == "course_pursuing"


def test_detect_field_role_bare_course_label_without_name_classifies_as_stream():
    """U10.4: When name is absent, label='Course*' denotes degree/stream, not domain."""
    role = UnstopAdapter._detect_field_role(
        name="",
        el_id="mat-select-opt",
        label="Course*",
        tag="mat-select",
    )
    assert role == "course_stream"


def test_detect_field_role_course_pursuing_label_without_name_classifies_as_pursuing():
    """U10.4: When name is absent, label='Course Pursuing*' denotes domain."""
    role = UnstopAdapter._detect_field_role(
        name="",
        el_id="mat-select-opt",
        label="Course Pursuing*",
        tag="mat-select",
    )
    assert role == "course_pursuing"


def test_detect_field_role_domain_label_without_name_classifies_as_pursuing():
    """U10.4: When name is absent, label='Domain*' denotes domain."""
    role = UnstopAdapter._detect_field_role(
        name="",
        el_id="mat-select-opt",
        label="Domain*",
        tag="mat-select",
    )
    assert role == "course_pursuing"


def test_detect_field_role_education_hierarchy():
    """U10.4: Verify all education fields are deterministically distinguished."""
    assert UnstopAdapter._detect_field_role("course_duration", "", "Course Duration*") == "course_duration"
    assert UnstopAdapter._detect_field_role("", "", "Duration*") == "course_duration"
    assert UnstopAdapter._detect_field_role("course_specialization", "", "Specialization*") == "course_specialization"
    assert UnstopAdapter._detect_field_role("", "", "Branch*") == "course_specialization"
    assert UnstopAdapter._detect_field_role("", "", "Specialization*") == "course_specialization"
    assert UnstopAdapter._detect_field_role("course_stream", "", "Degree*") == "course_stream"
    assert UnstopAdapter._detect_field_role("", "", "Degree*") == "course_stream"
    assert UnstopAdapter._detect_field_role("", "", "Stream*") == "course_stream"
    assert UnstopAdapter._detect_field_role("passout_year", "", "Graduation Year*") == "graduation_year"
    assert UnstopAdapter._detect_field_role("", "", "Passing Year*") == "graduation_year"


_UNSTOP_MULTI_EDUCATION_HTML = """<!DOCTYPE html>
<html>
<body>
  <form>
    <div>
      <label for="course_pursuing">Domain*</label>
      <mat-select id="course_pursuing" name="course_pursuing" required>
        <mat-option>Engineering</mat-option>
        <mat-option>Management</mat-option>
      </mat-select>
    </div>
    <div>
      <label for="course_stream">Course*</label>
      <mat-select id="course_stream" name="course_stream" required>
        <mat-option>B.Tech/BE</mat-option>
        <mat-option>M.Tech/ME</mat-option>
      </mat-select>
    </div>
    <div>
      <label for="course_specialization">Specialization*</label>
      <mat-select id="course_specialization" name="course_specialization" required>
        <mat-option>Computer Science and Engineering</mat-option>
        <mat-option>Mechanical Engineering</mat-option>
      </mat-select>
    </div>
    <div>
      <label for="course_duration">Course Duration*</label>
      <mat-select id="course_duration" name="course_duration" required>
        <mat-option>4 Years</mat-option>
        <mat-option>2 Years</mat-option>
      </mat-select>
    </div>
  </form>
</body>
</html>
"""


def test_multi_dropdown_education_form_deterministic_separation(page):
    """U10.4: Form with Domain ('course_pursuing') and Course ('course_stream') both resolve accurately."""
    _set_page(page, _UNSTOP_MULTI_EDUCATION_HTML)
    adapter = UnstopAdapter()
    fields = adapter.extract_form_fields(page)
    role_map = {f.field_role: f for f in fields}

    assert "course_pursuing" in role_map, "Domain must be mapped to course_pursuing"
    assert "course_stream" in role_map, "Course* must be mapped to course_stream (NOT course_pursuing)"
    assert "course_specialization" in role_map, "Specialization must be mapped to course_specialization"
    assert "course_duration" in role_map, "Duration must be mapped to course_duration"

    candidate = {
        "education": [{
            "degree": "B.Tech/BE",
            "branch": "Computer Science and Engineering",
            "domain": "Engineering",
            "duration": "4 Years",
            "graduation_year": 2028,
        }]
    }

    cls_domain, val_domain = adapter.classify_field(role_map["course_pursuing"], candidate)
    assert cls_domain == QuestionClassification.PROFILE_FACT
    assert val_domain == "Engineering"

    cls_stream, val_stream = adapter.classify_field(role_map["course_stream"], candidate)
    assert cls_stream == QuestionClassification.PROFILE_FACT
    assert val_stream == "B.Tech/BE"

    cls_spec, val_spec = adapter.classify_field(role_map["course_specialization"], candidate)
    assert cls_spec == QuestionClassification.PROFILE_FACT
    assert val_spec == "Computer Science and Engineering"

    cls_dur, val_dur = adapter.classify_field(role_map["course_duration"], candidate)
    assert cls_dur == QuestionClassification.PROFILE_FACT
    assert val_dur == "4 Years"


# -- U10.6 mat-select Already-Selected Detection Tests -----------------------

_UNSTOP_MAT_SELECT_HTML = """
<!DOCTYPE html>
<html>
<body>
<form id="reg-form">
  <label for="course_pursuing">Course Pursuing*</label>
  <mat-select id="course_pursuing" name="course_pursuing" required>
    <div class="mat-mdc-select-trigger" id="trigger-content">Select Course</div>
  </mat-select>
  <div class="cdk-overlay-pane">
    <mat-option data-val="Management" onclick="window.__CLICKED_OPTION = 'Management'; window.__OPTION_CLICK_COUNT = (window.__OPTION_CLICK_COUNT || 0) + 1">Management</mat-option>
    <mat-option data-val="Engineering" onclick="window.__CLICKED_OPTION = 'Engineering'; window.__OPTION_CLICK_COUNT = (window.__OPTION_CLICK_COUNT || 0) + 1">Engineering</mat-option>
    <mat-option data-val="B.Tech/BE" onclick="window.__CLICKED_OPTION = 'B.Tech/BE'; window.__OPTION_CLICK_COUNT = (window.__OPTION_CLICK_COUNT || 0) + 1">B.Tech/BE</mat-option>
  </div>
</form>
</body>
</html>
"""

def test_u10_6_a_already_selected_correct_value(page):
    """Test A: Already-selected correct value."""
    _set_page(page, _UNSTOP_MAT_SELECT_HTML)
    # Simulate already selected correct value
    page.evaluate("() => { document.getElementById('trigger-content').innerText = 'Engineering'; }")
    
    adapter = UnstopAdapter()
    ctx = _make_context(page)

    candidate = _candidate_base()
    candidate["education"] = [{
        "degree": "B.Tech/BE",
        "branch": "Computer Science and Engineering",
        "domain": "Engineering",
        "duration": "4 Years",
        "graduation_year": 2028,
    }]

    result = adapter.fill(ctx, candidate)
    assert result.success is True
    # Should be 0 clicks because it was already selected
    assert page.evaluate("() => window.__OPTION_CLICK_COUNT || 0") == 0


def test_u10_6_b_already_selected_value_with_hidden_option(page):
    """Test B: Already-selected value with hidden option."""
    _set_page(page, _UNSTOP_MAT_SELECT_HTML)
    # Simulate already selected correct value and hidden option
    page.evaluate("""() => { 
        document.getElementById('trigger-content').innerText = 'Engineering';
        const opt = document.querySelector('mat-option[data-val="Engineering"]');
        opt.style.display = 'none';
        opt.setAttribute('aria-selected', 'true');
    }""")
    
    adapter = UnstopAdapter()
    ctx = _make_context(page)

    candidate = _candidate_base()
    candidate["education"] = [{
        "degree": "B.Tech/BE",
        "branch": "Computer Science and Engineering",
        "domain": "Engineering",
        "duration": "4 Years",
        "graduation_year": 2028,
    }]

    result = adapter.fill(ctx, candidate)
    assert result.success is True
    # Should be 0 clicks because it was already selected, regardless of option visibility
    assert page.evaluate("() => window.__OPTION_CLICK_COUNT || 0") == 0


def test_u10_6_c_different_current_value(page):
    """Test C: Different current value."""
    _set_page(page, _UNSTOP_MAT_SELECT_HTML)
    # Simulate already selected wrong value
    page.evaluate("() => { document.getElementById('trigger-content').innerText = 'Management'; }")
    
    adapter = UnstopAdapter()
    ctx = _make_context(page)

    candidate = _candidate_base()
    candidate["education"] = [{
        "degree": "B.Tech/BE",
        "branch": "Computer Science and Engineering",
        "domain": "Engineering",
        "duration": "4 Years",
        "graduation_year": 2028,
    }]

    result = adapter.fill(ctx, candidate)
    assert result.success is True
    # Should click the correct option
    assert page.evaluate("() => window.__CLICKED_OPTION") == "Engineering"
    assert page.evaluate("() => window.__OPTION_CLICK_COUNT") == 1


def test_u10_6_d_missing_candidate_value(page):
    """Test D: Missing candidate value."""
    _set_page(page, _UNSTOP_MAT_SELECT_HTML)
    page.evaluate("() => { document.getElementById('trigger-content').innerText = 'Select Course'; }")
    
    adapter = UnstopAdapter()
    ctx = _make_context(page)

    candidate = _candidate_base()
    candidate["education"] = [{
        "degree": "B.Tech/BE",
        "branch": "Computer Science and Engineering",
        "duration": "4 Years",
        "graduation_year": 2028,
    }]

    result = adapter.fill(ctx, candidate)
    assert result.success is False
    assert result.status == "manual_required"
    assert page.evaluate("() => window.__OPTION_CLICK_COUNT || 0") == 0


def test_u10_6_e_candidate_value_absent_from_options(page):
    """Test E: Candidate value absent from options."""
    _set_page(page, _UNSTOP_MAT_SELECT_HTML)
    page.evaluate("() => { document.getElementById('trigger-content').innerText = 'Select Course'; }")
    
    adapter = UnstopAdapter()
    ctx = _make_context(page)

    candidate = _candidate_base()
    candidate["education"] = [{
        "degree": "B.Tech/BE",
        "branch": "Computer Science and Engineering",
        "domain": "Lawyer", # Not in options
        "duration": "4 Years",
        "graduation_year": 2028,
    }]

    result = adapter.fill(ctx, candidate)
    assert result.success is False
    assert result.status == "manual_required"
    assert page.evaluate("() => window.__OPTION_CLICK_COUNT || 0") == 0


def test_u10_6_f_normalization(page):
    """Test F: Normalization."""
    _set_page(page, _UNSTOP_MAT_SELECT_HTML)
    # Simulate already selected correct value but with different spacing/case
    page.evaluate("() => { document.getElementById('trigger-content').innerText = '   engineering  '; }")
    
    adapter = UnstopAdapter()
    ctx = _make_context(page)

    candidate = _candidate_base()
    candidate["education"] = [{
        "degree": "B.Tech/BE",
        "branch": "Computer Science and Engineering",
        "domain": "Engineering",
        "duration": "4 Years",
        "graduation_year": 2028,
    }]

    result = adapter.fill(ctx, candidate)
    assert result.success is True
    # Should be 0 clicks because it was already selected and matched via normalization
    assert page.evaluate("() => window.__OPTION_CLICK_COUNT || 0") == 0


def test_u10_6_g_hidden_unrelated_option(page):
    """Test G: Hidden unrelated option must not be clicked."""
    _set_page(page, _UNSTOP_MAT_SELECT_HTML)
    # Simulate trigger doesn't match candidate, and candidate's option is hidden
    page.evaluate("""() => { 
        document.getElementById('trigger-content').innerText = 'Management';
        const opt = document.querySelector('mat-option[data-val="Engineering"]');
        opt.style.display = 'none'; // hidden but not currently selected in trigger
    }""")
    
    adapter = UnstopAdapter()
    ctx = _make_context(page)

    candidate = _candidate_base()
    candidate["education"] = [{
        "degree": "B.Tech/BE",
        "branch": "Computer Science and Engineering",
        "domain": "Engineering",
        "duration": "4 Years",
        "graduation_year": 2028,
    }]

    result = adapter.fill(ctx, candidate)
    assert result.success is False
    assert result.status == "manual_required"
    # Option is hidden and trigger didn't match, so we shouldn't force click it
    assert page.evaluate("() => window.__OPTION_CLICK_COUNT || 0") == 0


# -- U10.8 Specialization Resolution Safety Tests -----------------------------

_UNSTOP_SPECIALIZATION_HTML = """
<!DOCTYPE html>
<html>
<body>
<form id="reg-form">
  <label for="course_specialization">Course Specialization*</label>
  <mat-select id="course_specialization" name="course_specialization" required>
    <div class="mat-mdc-select-trigger" id="spec-trigger-content">Select Specialization</div>
  </mat-select>
  <div class="cdk-overlay-pane">
    <mat-option data-val="Computer Science and Engineering" onclick="window.__CLICKED_OPTION = 'Computer Science and Engineering'; window.__OPTION_CLICK_COUNT = (window.__OPTION_CLICK_COUNT || 0) + 1">Computer Science and Engineering</mat-option>
    <mat-option data-val="Computer Science" onclick="window.__CLICKED_OPTION = 'Computer Science'; window.__OPTION_CLICK_COUNT = (window.__OPTION_CLICK_COUNT || 0) + 1">Computer Science</mat-option>
    <mat-option data-val="Information Technology" onclick="window.__CLICKED_OPTION = 'Information Technology'; window.__OPTION_CLICK_COUNT = (window.__OPTION_CLICK_COUNT || 0) + 1">Information Technology</mat-option>
  </div>
</form>
</body>
</html>
"""

def test_u10_8_a_exact_specialization_match(page):
    """Test A: Exact specialization match."""
    _set_page(page, _UNSTOP_SPECIALIZATION_HTML)
    adapter = UnstopAdapter()
    ctx = _make_context(page)

    candidate = _candidate_base()
    candidate["education"] = [{
        "branch": "Computer Science and Engineering",
        "domain": "Engineering",
        "duration": "4 Years",
    }]

    res = adapter.fill(ctx, candidate)
    assert res.success is True, "Exact match should succeed"
    assert page.evaluate("window.__OPTION_CLICK_COUNT") == 1
    assert page.evaluate("window.__CLICKED_OPTION") == "Computer Science and Engineering"


def test_u10_8_b_no_exact_match(page):
    """Test B: No exact match."""
    _set_page(page, _UNSTOP_SPECIALIZATION_HTML)
    adapter = UnstopAdapter()
    ctx = _make_context(page)

    candidate = _candidate_base()
    candidate["education"] = [{
        "branch": "Mechanical Engineering",
        "domain": "Engineering",
        "duration": "4 Years",
    }]

    res = adapter.fill(ctx, candidate)
    assert res.success is False, "No match should fail closed"
    assert res.status == "manual_required"
    assert page.evaluate("window.__OPTION_CLICK_COUNT") == 0
    assert page.evaluate("window.__CLICKED_OPTION") is None


def test_u10_8_c_similar_but_non_equivalent_value(page):
    """Test C: Similar but non-equivalent value."""
    _set_page(page, _UNSTOP_SPECIALIZATION_HTML)
    adapter = UnstopAdapter()
    ctx = _make_context(page)

    candidate = _candidate_base()
    candidate["education"] = [{
        "branch": "Computer Science Engineering",
        "domain": "Engineering",
        "duration": "4 Years",
    }]

    res = adapter.fill(ctx, candidate)
    assert res.success is False, "Similar but non-exact match should fail closed"
    assert res.status == "manual_required"
    assert page.evaluate("window.__OPTION_CLICK_COUNT") == 0
    assert page.evaluate("window.__CLICKED_OPTION") is None
