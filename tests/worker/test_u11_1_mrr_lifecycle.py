"""U11.1 — MRR lifecycle hardening & consent-order regression tests.

Covers the three post-live-audit findings:

A. Explicit MRR invalidation/replacement — a previously RESOLVED manual answer
   that fails re-validation against the current live form is explicitly marked
   ``invalidated`` before a new PENDING request is recorded.  The old row is
   retained for audit and can never be replayed as an active resolution.

B. Deterministic manual-resolution retrieval — active resolutions are selected by
   an explicit ordered query (only RESOLVED rows; latest wins per field), never by
   relationship/dictionary iteration order.

C. Consent-order regression — the deferred required-field check lives inside
   ``_fill_all_fields`` so fields are processed in DOM order.  An earlier taxonomy
   mismatch therefore generates an MRR *before* a later unresolved CONSENT field
   can halt the fill.  Consent is never fabricated to get past it.

All tests are strictly OFFLINE: synthetic HTML rendered by headless Playwright,
in-memory SQLite, and static inspection.  No network, no real Unstop page, no live
submission, and no production database mutation.
"""

from __future__ import annotations

import ast
import inspect
import textwrap
from datetime import datetime, timezone
from typing import Any

import pytest
from playwright.sync_api import sync_playwright

from core.models.opportunity import Application, ManualResolutionRequest, Opportunity
from core.models.profile import Profile, ProfileEducation
from core.services import (
    application_service,
    manual_resolution_service,
    opportunity_service,
)
from core.services.manual_resolution_service import (
    STATUS_INVALIDATED,
    STATUS_PENDING,
    STATUS_RESOLVED,
    get_active_resolutions,
    invalidate_active_requests,
    resolve_request,
    resume_application,
)
from core.status import ApplicationStatus, OpportunityStatus, ReliabilityTier
from worker.adapters.base import ApplicationContext, ExtractedListing
from worker.adapters.unstop import FormField, QuestionClassification, UnstopAdapter
from worker.engine.filler import ApplicationFiller

OPP_URL = "https://unstop.com/competitions/888888/register"
OPP_ID = 888888
RESUME_PATH = "tests/fixtures/sample_resume.pdf"


# ── Offline Playwright harness ─────────────────────────────────────────────────


@pytest.fixture(scope="module")
def playwright_browser():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture()
def page(playwright_browser):
    ctx = playwright_browser.new_context()
    pg = ctx.new_page()
    yield pg
    pg.close()
    ctx.close()


def _set_page(pg, html: str) -> None:
    """Render a synthetic Unstop registration form (no network)."""
    pg.goto("about:blank")
    pg.set_content(html)
    pg.evaluate(
        "() => { window.__SUBMITTED = false; window.__SPEC_OPT = null;"
        " window.__CONSENT_CLICKED = false; }"
    )


_CANDIDATE = {
    "full_name": "Alex Morgan",
    "email": "alex@example.com",
    "phone": "9876543210",
    "location": "Mumbai",
    "gender": "Prefer not to say",
    "differently_abled": "No",
    "user_type": "College Students",
}


def _education(branch: str, domain: str | None = "Engineering") -> list[dict]:
    """Candidate education record.  ``branch`` drives course_specialization."""
    edu = {
        "degree": "B.Tech/BE",
        "branch": branch,
        "institution": "Test Institute",
        "graduation_year": 2028,
        "duration": "4 Years",
    }
    if domain is not None:
        edu["domain"] = domain
    return [edu]


def _make_context(pg) -> ApplicationContext:
    return ApplicationContext(
        opportunity_id=OPP_ID,
        listing_url=OPP_URL,
        adapter_name="unstop",
        tier=ReliabilityTier.EXPERIMENTAL,
        browser_page=pg,
    )


def _spec_options_html(options: list[str]) -> str:
    """Registration form: identity fields, then a specialization dropdown, then a
    required consent checkbox, then the terminal control.

    DOM order is deliberate — the taxonomy field precedes consent so the
    consent-order regression is exercised exactly as it occurs on the live form.
    """
    opts = "\n".join(
        f'    <mat-option data-val="{o}"'
        f' onclick="window.__SPEC_OPT = &quot;{o}&quot;">{o}</mat-option>'
        for o in options
    )
    return f"""<!DOCTYPE html>
<html>
<head>
  <style>
    mat-select {{ display: block; width: 220px; height: 40px; border: 1px solid #ccc; }}
    mat-option {{ display: block; padding: 8px; cursor: pointer; }}
  </style>
</head>
<body>
<form id="reg-form">
  <label for="player_firstname">First Name*</label>
  <input id="player_firstname" name="player_firstname" type="text" required />
  <label for="player_lastname">Last Name*</label>
  <input id="player_lastname" name="player_lastname" type="text" required />
  <label for="player_email">Email*</label>
  <input id="player_email" name="player_email" type="email" required />
  <label for="player_phone">Phone*</label>
  <input id="player_phone" name="player_phone" type="tel" required />

  <label for="course_specialization">Specialization*</label>
  <mat-select id="course_specialization" name="course_specialization" required>
    <div class="mat-mdc-select-trigger">Select Specialization</div>
  </mat-select>
  <div class="cdk-overlay-pane">
{opts}
  </div>

  <un-checkbox id="acceptance" name="acceptance"
               onclick="window.__CONSENT_CLICKED = true">
    <label>I accept terms and conditions*</label>
  </un-checkbox>
  <button type="button" id="unstop_submit"
          onclick="window.__SUBMITTED = true">Submit Application</button>
</form>
</body>
</html>
"""


# ── C. Consent-order regression ────────────────────────────────────────────────


class TestConsentOrderRegression:
    """The deferred required-field check processes fields in DOM order."""

    def test_earlier_taxonomy_mismatch_generates_mrr_before_later_consent(self, page):
        """REGRESSION: a required taxonomy field earlier in the form must be
        reached — and generate an MRR — before a later required CONSENT field.

        Under the pre-fix behaviour the classification loop aborted as soon as it
        saw the unresolved CONSENT field, so this FillResult carried no
        resolution_request at all and the legitimate earlier mismatch was never
        surfaced for human resolution.
        """
        _set_page(page, _spec_options_html(["Computer Science", "Information Technology"]))
        adapter = UnstopAdapter()
        candidate = dict(_CANDIDATE)
        candidate["education"] = _education("Computer Science and Engineering")

        result = adapter.fill(_make_context(page), candidate)

        # The fill fails closed...
        assert result.success is False
        assert result.status == "manual_required"
        # ...but the failure is the TAXONOMY MISMATCH, reached first in DOM order,
        # not the later consent field.
        assert result.resolution_request is not None
        assert result.resolution_request["field_name"] == "course_specialization"
        assert result.resolution_request["candidate_value"] == "Computer Science and Engineering"
        assert set(result.resolution_request["available_options"]) == {
            "Computer Science",
            "Information Technology",
        }
        assert result.resolution_request["reason"] == "NO_EXACT_MATCH"
        # Consent was never fabricated to get past the checkbox...
        assert page.evaluate("() => window.__CONSENT_CLICKED !== true")
        # ...no option was selected on the candidate's behalf...
        assert page.evaluate("() => window.__SPEC_OPT === null")
        # ...and nothing was submitted.
        assert page.evaluate("() => window.__SUBMITTED !== true")
        assert page.locator("#reg-form").count() == 1

    def test_later_unresolved_consent_still_blocks_when_earlier_fields_resolve(self, page):
        """COMPLEMENTARY safety test: when earlier fields resolve cleanly, the
        fill must still halt at a later unresolved required consent field.

        This proves the ordering fix did not weaken consent safety — consent is
        reached, found unresolved, and fails closed without an MRR (a consent
        halt is not a taxonomy ambiguity).
        """
        _set_page(
            page,
            _spec_options_html(["Computer Science and Engineering", "Information Technology"]),
        )
        adapter = UnstopAdapter()
        candidate = dict(_CANDIDATE)
        candidate["education"] = _education("Computer Science and Engineering")

        result = adapter.fill(_make_context(page), candidate)

        assert result.success is False
        assert result.status == "manual_required"
        # The specialization value matched exactly, so it was selected...
        assert page.evaluate("() => window.__SPEC_OPT") == "Computer Science and Engineering"
        # ...and the halt is the unresolved CONSENT field, not a taxonomy mismatch.
        assert result.resolution_request is None
        assert result.error_reason is not None
        assert "CONSENT" in result.error_reason
        assert "requires user decision" in result.error_reason
        # The checkbox was still never checked merely because it existed.
        assert page.evaluate("() => window.__CONSENT_CLICKED !== true")
        assert page.evaluate("() => window.__SUBMITTED !== true")

    def test_manual_resolution_value_is_selected_before_consent(self, page):
        """An application-scoped manual resolution resolves the earlier taxonomy
        field, so the fill proceeds to consent (and halts there)."""
        _set_page(
            page,
            _spec_options_html(["Computer Science", "Information Technology"]),
        )
        adapter = UnstopAdapter()
        candidate = dict(_CANDIDATE)
        candidate["education"] = _education("Computer Science and Engineering")

        result = adapter.fill(
            _make_context(page),
            candidate,
            manual_resolutions={"course_specialization": "Computer Science"},
        )

        # Consent is still unresolved → still fails closed.
        assert result.success is False
        assert result.status == "manual_required"
        assert "CONSENT" in (result.error_reason or "")
        # The manual answer selected the exact matching option; no fallback.
        assert page.evaluate("() => window.__SPEC_OPT") == "Computer Science"
        assert page.evaluate("() => window.__CONSENT_CLICKED !== true")


# ── Static guarantee: the required-field check is DOM-ordered ─────────────────


def test_required_field_check_lives_inside_the_fill_loop():
    """Static guard: classify-then-fill must NOT early-exit on an unresolved
    required field during classification.  The unresolved-required check belongs
    inside ``_fill_all_fields`` so fields are processed in DOM order.
    """
    source = textwrap.dedent(inspect.getsource(UnstopAdapter.fill))
    tree = ast.parse(source)

    # The *initial* classification loop iterates the freshly extracted fields.
    # (A later, separate loop classifies conditional step fields and legitimately
    # returns early — it is not the consent-order hazard.)
    cls_loop = None
    fill_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_fill_all_fields":
            fill_fn = node
        if isinstance(node, ast.For):
            iterator = node.iter
            if isinstance(iterator, ast.Name) and iterator.id == "extracted_fields" and any(
                isinstance(t, ast.Call)
                and isinstance(t.func, ast.Attribute)
                and t.func.attr == "classify_field"
                for t in ast.walk(node)
            ):
                cls_loop = node

    assert fill_fn is not None, "_fill_all_fields must be defined inside fill()"
    assert cls_loop is not None, "the initial classification loop must exist"

    # The classification loop must contain NO return of a manual_required
    # FillResult — early exit there is exactly the consent-order bug: it aborts
    # before earlier fields in DOM order can be filled / surfaced as MRRs.
    def _returns_fillresult(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Return)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "FillResult"
        )

    early_exits = [n for n in ast.walk(cls_loop) if _returns_fillresult(n)]
    assert not early_exits, (
        "The initial classification loop must not early-exit with a manual_required "
        "FillResult — unresolved required fields are checked inside "
        "_fill_all_fields so fields are processed in DOM order."
    )

    # _fill_all_fields must perform the unresolved-required check itself.
    fill_src = ast.unparse(fill_fn)
    assert "REQUIRES_USER" in fill_src and "CONSENT" in fill_src, (
        "_fill_all_fields must fail closed on unresolved REQUIRES_USER/CONSENT "
        "fields in DOM order."
    )


# ── Offline integration through ApplicationFiller.process_opportunity ──────────


class _OfflineUnstopAdapter(UnstopAdapter):
    """Real UnstopAdapter fill + extraction, but the browser page is injected.

    ``open_application`` is overridden so no real browser/session is launched;
    every other method (notably ``fill``) is the production implementation.
    """

    def __init__(self, page: Any) -> None:
        super().__init__()
        self._page = page
        # The offline fixture carries no file input; the resume gate is satisfied
        # separately by the opportunity's pinned, on-disk resume.
        self.requires_resume = False

    def open_application(self, url: str, **kwargs: Any) -> ApplicationContext:
        return ApplicationContext(
            opportunity_id=kwargs.get("opportunity_id", OPP_ID),
            listing_url=url,
            adapter_name="unstop",
            tier=self.tier,
            extracted=ExtractedListing(
                company="Offline Co",
                title="U11.1 Offline Role",
                url=url,
                custom_questions=[],
            ),
            browser_page=self._page,
        )


def _make_offline_opportunity(db_session) -> tuple[Opportunity, Profile]:
    """A ready_to_apply Unstop opportunity with a pinned, on-disk resume."""
    profile = Profile(
        name="u11-1-candidate",
        full_name="Alex Morgan",
        email="alex@example.com",
        phone="9876543210",
        location="Mumbai",
        gender="Prefer not to say",
        differently_abled="No",
        user_type="College Students",
    )
    profile.education = [
        ProfileEducation(
            degree="B.Tech/BE",
            branch="Computer Science and Engineering",
            institution="Test Institute",
            graduation_year=2028,
            duration="4 Years",
            domain="Engineering",
        )
    ]
    db_session.add(profile)
    db_session.flush()

    opp = opportunity_service.create_opportunity(
        db_session,
        title="U11.1 Offline Role",
        company="Offline Co",
        url=OPP_URL,
        source="unstop",
        reliability_tier=ReliabilityTier.EXPERIMENTAL.value,
    )
    opp.profile_id = profile.id
    opp.selected_resume_id = None  # resolved from the profile's active resume below
    opportunity_service.transition_status(
        db_session, opp.id, OpportunityStatus.RECOMMENDED, actor="test"
    )
    opportunity_service.transition_status(
        db_session, opp.id, OpportunityStatus.READY_TO_APPLY, actor="test"
    )
    db_session.commit()
    return opp, profile


def _fill_via_worker(db_session, page, opp) -> dict:
    adapter = _OfflineUnstopAdapter(page)
    filler = ApplicationFiller(adapter_override=adapter)
    result = filler.process_opportunity(db_session, opp.id)
    # process_opportunity returns the adapter's app_ctx for browser inspection.
    return result


def test_consent_order_mismatch_persists_mrr_and_marks_manual_required(
    db_session, page
):
    """End-to-end (offline): the earlier taxonomy mismatch is reached before the
    later consent field, so an MRR is persisted and the application hands off to
    a human without any submission.
    """
    _set_page(page, _spec_options_html(["Computer Science", "Information Technology"]))
    opp, profile = _make_offline_opportunity(db_session)

    result = _fill_via_worker(db_session, page, opp)

    assert result["status"] == "manual_required"
    db_session.refresh(opp)
    app = (
        db_session.query(Application)
        .filter_by(opportunity_id=opp.id)
        .order_by(Application.id.desc())
        .first()
    )
    assert app is not None
    assert app.status == ApplicationStatus.FAILED.value
    assert opp.status == OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value

    # Exactly one MRR, for the taxonomy field — created before consent was ever
    # evaluated as a halting condition.
    reqs = list(app.manual_resolutions)
    assert len(reqs) == 1
    assert reqs[0].field_name == "course_specialization"
    assert reqs[0].status == STATUS_PENDING
    assert reqs[0].candidate_value == "Computer Science and Engineering"
    assert set(reqs[0].available_options) == {"Computer Science", "Information Technology"}

    # Submission separation: nothing was submitted.
    assert app.status != ApplicationStatus.SUBMITTED.value
    assert app.submitted_at is None
    assert app.confirmation_ref is None
    assert opp.status != OpportunityStatus.APPLIED.value

    # Consent was never fabricated.
    assert page.evaluate("() => window.__CONSENT_CLICKED !== true")
    assert page.evaluate("() => window.__SUBMITTED !== true")


def test_mrr_lifecycle_through_worker_resume_and_invalidation(db_session, page, playwright_browser):
    """End-to-end (offline) MRR lifecycle through the worker:

    mismatch -> PENDING MRR #1 -> human RESOLVED -> resume -> live form no longer
    offers the answer -> #1 explicitly INVALIDATED + new PENDING MRR #2 created.

    Only #2 represents the current unresolved state, and #1 is retained for audit.
    """
    # Run 1: the specialization value is absent from the offered options.
    ctx1 = playwright_browser.new_context()
    page1 = ctx1.new_page()
    _set_page(page1, _spec_options_html(["Computer Science", "Information Technology"]))
    opp, profile = _make_offline_opportunity(db_session)

    result1 = _fill_via_worker(db_session, page1, opp)
    assert result1["status"] == "manual_required"
    page1.close()
    ctx1.close()

    app = (
        db_session.query(Application)
        .filter_by(opportunity_id=opp.id)
        .order_by(Application.id.desc())
        .first()
    )
    mrr1 = list(app.manual_resolutions)[-1]
    assert mrr1.field_name == "course_specialization"
    assert mrr1.status == STATUS_PENDING

    # The human resolves the ambiguity with an offered option.
    resolve_request(db_session, mrr1.id, "Computer Science", "human")
    resume_application(db_session, app.id, "human")
    db_session.refresh(app)
    db_session.refresh(opp)
    assert app.status == ApplicationStatus.PENDING.value
    assert opp.status == OpportunityStatus.READY_TO_APPLY.value

    # Run 2: the live form has changed and no longer offers "Computer Science".
    ctx2 = playwright_browser.new_context()
    page2 = ctx2.new_page()
    _set_page(page2, _spec_options_html(["Information Technology", "Mechanical Engineering"]))

    result2 = _fill_via_worker(db_session, page2, opp)
    assert result2["status"] == "manual_required"
    page2.close()
    ctx2.close()

    # The stale resolution was retired and a replacement request recorded.
    db_session.refresh(mrr1)
    assert mrr1.status == STATUS_INVALIDATED
    # Historical answer is still auditable.
    assert mrr1.resolved_value == "Computer Science"
    assert mrr1.resolved_by == "human"

    reqs = sorted(app.manual_resolutions, key=lambda r: r.id)
    assert len(reqs) == 2
    assert [r.status for r in reqs] == [STATUS_INVALIDATED, STATUS_PENDING]
    new = reqs[-1]
    assert new.status == STATUS_PENDING
    assert new.candidate_value == "Computer Science"
    assert set(new.available_options) == {"Information Technology", "Mechanical Engineering"}

    # Exactly one active request; and no active resolution (the new one is a
    # question, not an answer).
    assert get_active_resolutions(db_session, app.id) == {}
    active = [r for r in reqs if r.status in (STATUS_PENDING, STATUS_RESOLVED)]
    assert len(active) == 1
    assert active[0].id == new.id

    # Still nothing submitted.
    db_session.refresh(app)
    db_session.refresh(opp)
    assert app.status != ApplicationStatus.SUBMITTED.value
    assert app.submitted_at is None
    assert opp.status == OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value


# ── A. MRR lifecycle: explicit invalidation / replacement ─────────────────────


def _mrr(db_session, app: Application, field: str, status: str, **kw) -> ManualResolutionRequest:
    req = ManualResolutionRequest(
        application_id=app.id,
        opportunity_id=app.opportunity_id,
        field_name=field,
        available_options=kw.get("available_options", ["Computer Science", "Information Technology"]),
        reason=kw.get("reason", "NO_EXACT_MATCH"),
        status=status,
        resolved_value=kw.get("resolved_value"),
        resolved_at=kw.get("resolved_at"),
        resolved_by=kw.get("resolved_by"),
    )
    db_session.add(req)
    db_session.commit()
    return req


def _app(db_session, opp: Opportunity, status: str = ApplicationStatus.FAILED.value) -> Application:
    app = Application(
        opportunity_id=opp.id,
        status=status,
        attempt_number=1,
    )
    db_session.add(app)
    db_session.commit()
    return app


def _opp(db_session, status: str = OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value) -> Opportunity:
    opp = Opportunity(
        company="Lifecycle Co",
        title="Lifecycle Role",
        url=OPP_URL,
        dedup_hash=f"u11-1-opp-{status}-{datetime.now(timezone.utc).timestamp()}",
        status=status,
    )
    db_session.add(opp)
    db_session.commit()
    return opp


class TestMRRLifecycle:
    def test_pending_to_resolved(self, db_session):
        """pending -> resolved via an explicit human answer in the option set."""
        opp = _opp(db_session)
        app = _app(db_session, opp)
        req = _mrr(db_session, app, "course_specialization", STATUS_PENDING)

        resolved = resolve_request(db_session, req.id, "Computer Science", "human")

        assert resolved.status == STATUS_RESOLVED
        assert resolved.resolved_value == "Computer Science"
        assert resolved.resolved_by == "human"
        assert resolved.resolved_at is not None

    def test_resolved_to_invalidated_explicit(self, db_session):
        """resolved -> invalidated: the retired row keeps its audit metadata."""
        opp = _opp(db_session)
        app = _app(db_session, opp)
        req = _mrr(
            db_session,
            app,
            "course_specialization",
            STATUS_RESOLVED,
            resolved_value="Computer Science",
            resolved_at=datetime.now(timezone.utc),
            resolved_by="human",
        )

        count = invalidate_active_requests(
            db_session, app.id, "course_specialization", actor="worker"
        )

        assert count == 1
        db_session.refresh(req)
        assert req.status == STATUS_INVALIDATED
        # Historical answer is retained for audit, never deleted.
        assert req.resolved_value == "Computer Science"
        assert req.resolved_by == "human"
        assert req.resolved_at is not None

    def test_invalidated_then_new_pending(self, db_session):
        """invalidated -> new pending: only the new request is active."""
        opp = _opp(db_session)
        app = _app(db_session, opp)
        old = _mrr(
            db_session,
            app,
            "course_specialization",
            STATUS_RESOLVED,
            resolved_value="Computer Science",
            resolved_at=datetime.now(timezone.utc),
            resolved_by="human",
        )

        # The live form no longer offers the old answer → retire it, create new.
        invalidate_active_requests(db_session, app.id, "course_specialization")
        new = _mrr(
            db_session, app, "course_specialization", STATUS_PENDING,
            available_options=["Information Technology", "Mechanical Engineering"],
            candidate_value="Computer Science",
        )

        db_session.refresh(old)
        assert old.status == STATUS_INVALIDATED
        assert new.status == STATUS_PENDING
        # Exactly one active request for the field.
        active = [
            r for r in app.manual_resolutions if r.status in (STATUS_PENDING, STATUS_RESOLVED)
        ]
        assert len(active) == 1
        assert active[0].id == new.id

    def test_invalidated_request_cannot_be_resolved(self, db_session):
        """An invalidated request is history: re-resolving it is refused."""
        opp = _opp(db_session)
        app = _app(db_session, opp)
        req = _mrr(db_session, app, "course_specialization", STATUS_INVALIDATED)

        with pytest.raises(ValueError, match="not pending"):
            resolve_request(db_session, req.id, "Computer Science", "human")

    def test_invalidation_is_scoped_to_field(self, db_session):
        """Invalidating field A leaves an active field B resolution untouched."""
        opp = _opp(db_session)
        app = _app(db_session, opp)
        a = _mrr(db_session, app, "course_specialization", STATUS_RESOLVED,
                 resolved_value="Computer Science")
        b = _mrr(db_session, app, "course_duration", STATUS_RESOLVED,
                 resolved_value="4 Years")

        invalidate_active_requests(db_session, app.id, "course_specialization")

        db_session.refresh(a)
        db_session.refresh(b)
        assert a.status == STATUS_INVALIDATED
        assert b.status == STATUS_RESOLVED
        assert b.resolved_value == "4 Years"


# ── B. Deterministic manual-resolution retrieval ──────────────────────────────


class TestDeterministicRetrieval:
    def test_one_resolved_mrr_is_selected(self, db_session):
        opp = _opp(db_session)
        app = _app(db_session, opp)
        _mrr(db_session, app, "course_specialization", STATUS_RESOLVED,
             resolved_value="Computer Science")

        active = get_active_resolutions(db_session, app.id)

        assert active == {"course_specialization": "Computer Science"}

    def test_one_invalidated_plus_one_resolved_selects_only_resolved(self, db_session):
        opp = _opp(db_session)
        app = _app(db_session, opp)
        _mrr(db_session, app, "course_specialization", STATUS_INVALIDATED,
             resolved_value="Stale Value")
        _mrr(db_session, app, "course_specialization", STATUS_RESOLVED,
             resolved_value="Computer Science")

        active = get_active_resolutions(db_session, app.id)

        assert active == {"course_specialization": "Computer Science"}

    def test_multiple_invalidated_mrrs_select_none(self, db_session):
        opp = _opp(db_session)
        app = _app(db_session, opp)
        _mrr(db_session, app, "course_specialization", STATUS_INVALIDATED,
             resolved_value="Old")
        _mrr(db_session, app, "course_specialization", STATUS_INVALIDATED,
             resolved_value="Older")

        active = get_active_resolutions(db_session, app.id)

        assert active == {}

    def test_pending_mrr_is_never_an_answer(self, db_session):
        opp = _opp(db_session)
        app = _app(db_session, opp)
        _mrr(db_session, app, "course_specialization", STATUS_PENDING,
             resolved_value=None)

        active = get_active_resolutions(db_session, app.id)

        assert active == {}

    def test_multiple_historical_records_are_deterministic(self, db_session):
        """Several historical rows for one field must resolve to exactly one
        deterministic value regardless of row-return order.

        The rule: the most recent RESOLVED request wins; everything invalidated or
        still pending is excluded.
        """
        opp = _opp(db_session)
        app = _app(db_session, opp)
        # Deliberately interleave statuses so any dict-overwrite or row-order
        # dependence would surface as a flaky value.
        first = _mrr(db_session, app, "course_specialization", STATUS_RESOLVED,
                     resolved_value="First", resolved_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        second = _mrr(db_session, app, "course_specialization", STATUS_INVALIDATED,
                      resolved_value="Second")
        third = _mrr(db_session, app, "course_specialization", STATUS_RESOLVED,
                     resolved_value="Third", resolved_at=datetime(2026, 3, 1, tzinfo=timezone.utc))
        fourth = _mrr(db_session, app, "course_specialization", STATUS_PENDING)

        # id ordering: first < second < third < fourth, so the latest RESOLVED is
        # `third`.  A naive "last write wins" dict would return the pending
        # `fourth` (None) or the invalidated `second`.
        active = get_active_resolutions(db_session, app.id)

        assert active == {"course_specialization": "Third"}
        assert first.status == STATUS_RESOLVED
        assert second.status == STATUS_INVALIDATED
        assert third.status == STATUS_RESOLVED
        assert fourth.status == STATUS_PENDING

    def test_different_fields_do_not_collapse(self, db_session):
        """Field A's resolution must never overwrite field B's."""
        opp = _opp(db_session)
        app = _app(db_session, opp)
        _mrr(db_session, app, "course_specialization", STATUS_RESOLVED,
             resolved_value="Computer Science")
        _mrr(db_session, app, "course_duration", STATUS_RESOLVED,
             resolved_value="4 Years")
        _mrr(db_session, app, "graduation_year", STATUS_RESOLVED,
             resolved_value="2028")

        active = get_active_resolutions(db_session, app.id)

        assert active == {
            "course_specialization": "Computer Science",
            "course_duration": "4 Years",
            "graduation_year": "2028",
        }

    def test_application_a_resolution_cannot_affect_application_b(self, db_session):
        """Resolutions are application-scoped; a second attempt on the same
        opportunity never inherits the first attempt's answer."""
        opp = _opp(db_session)
        app_a = _app(db_session, opp)
        app_b = _app(db_session, opp)
        _mrr(db_session, app_a, "course_specialization", STATUS_RESOLVED,
             resolved_value="Computer Science")
        _mrr(db_session, app_b, "course_specialization", STATUS_RESOLVED,
             resolved_value="Information Technology")

        active_a = get_active_resolutions(db_session, app_a.id)
        active_b = get_active_resolutions(db_session, app_b.id)

        assert active_a == {"course_specialization": "Computer Science"}
        assert active_b == {"course_specialization": "Information Technology"}

    def test_opportunity_scoping_remains_correct(self, db_session):
        """The MRR is also opportunity-indexed; retrieval stays application-scoped
        and never crosses opportunities."""
        opp_a = _opp(db_session)
        opp_b = _opp(db_session, status=OpportunityStatus.READY_TO_APPLY.value)
        app_a = _app(db_session, opp_a)
        app_b = _app(db_session, opp_b, status=ApplicationStatus.PENDING.value)
        _mrr(db_session, app_a, "course_specialization", STATUS_RESOLVED,
             resolved_value="Computer Science")
        _mrr(db_session, app_b, "course_specialization", STATUS_RESOLVED,
             resolved_value="Mechanical Engineering")

        assert get_active_resolutions(db_session, app_a.id) == {
            "course_specialization": "Computer Science"
        }
        assert get_active_resolutions(db_session, app_b.id) == {
            "course_specialization": "Mechanical Engineering"
        }

    def test_submitted_application_cannot_resume(self, db_session):
        """A submitted application is terminal: an old MRR can never resume it."""
        opp = _opp(db_session)
        app = _app(db_session, opp, status=ApplicationStatus.FAILED.value)
        _mrr(db_session, app, "course_specialization", STATUS_RESOLVED,
             resolved_value="Computer Science")

        # Simulate a completed submission on this attempt.
        application_service.transition_application_status(
            db_session, app.id, ApplicationStatus.PENDING, reason="retry"
        )
        application_service.update_application_status(
            db_session,
            app.id,
            status=ApplicationStatus.SUBMITTED.value,
            submitted_at=datetime.now(timezone.utc),
            confirmation_ref="EARLIER-CONFIRMED",
        )
        db_session.commit()

        with pytest.raises(ValueError, match="already submitted"):
            resume_application(db_session, app.id)


# ── D. Safety invariants around resume ────────────────────────────────────────


class TestResumeSafety:
    def _pending_opp(self, db_session):
        opp = _opp(db_session)
        app = _app(db_session, opp)
        req = _mrr(db_session, app, "course_specialization", STATUS_PENDING)
        return opp, app, req

    def test_resume_requires_all_pending_resolved(self, db_session):
        opp, app, req = self._pending_opp(db_session)

        with pytest.raises(ValueError, match="still pending"):
            resume_application(db_session, app.id)

    def test_resume_returns_application_to_fill_pipeline_only(self, db_session):
        """resolve -> resume puts the application back to PENDING and the
        opportunity back to READY_TO_APPLY.  It does NOT submit anything."""
        opp, app, req = self._pending_opp(db_session)
        resolve_request(db_session, req.id, "Computer Science", "human")

        resumed = resume_application(db_session, app.id, "human")

        db_session.refresh(opp)
        assert resumed.status == ApplicationStatus.PENDING.value
        assert opp.status == OpportunityStatus.READY_TO_APPLY.value
        # Submission separation: still untouched.
        assert resumed.status != ApplicationStatus.SUBMITTED.value
        assert resumed.submitted_at is None
        assert opp.status != OpportunityStatus.APPLIED.value

    def test_resume_requires_manual_application_required_state(self, db_session):
        opp = _opp(db_session, status=OpportunityStatus.AWAITING_SUBMISSION.value)
        app = _app(db_session, opp, status=ApplicationStatus.FAILED.value)

        with pytest.raises(ValueError, match="MANUAL_APPLICATION_REQUIRED"):
            resume_application(db_session, app.id)

    def test_profile_remains_immutable_after_manual_resolution(self, db_session):
        """An application-scoped manual answer must never mutate the profile."""
        profile = Profile(name="u11-1-immutable", full_name="Alex Morgan")
        profile.education = [
            ProfileEducation(
                degree="B.Tech/BE",
                branch="Computer Science and Engineering",
                institution="Test Institute",
            )
        ]
        db_session.add(profile)
        db_session.commit()

        opp = _opp(db_session)
        app = _app(db_session, opp)
        req = _mrr(db_session, app, "course_specialization", STATUS_PENDING)
        resolve_request(db_session, req.id, "Computer Science", "human")
        resume_application(db_session, app.id, "human")

        db_session.refresh(profile)
        # The profile's branch is exactly what the candidate provided — the
        # application answer never wrote back into it.
        assert profile.education[0].branch == "Computer Science and Engineering"
        assert profile.education[0].domain is None

    def test_stale_answer_fails_closed_at_fill_time(self, page):
        """A resolved manual answer absent from the current live options is
        rejected by the DOM re-validation — it is never substituted."""
        _set_page(page, _spec_options_html(["Information Technology", "Mechanical Engineering"]))
        adapter = UnstopAdapter()
        candidate = dict(_CANDIDATE)
        candidate["education"] = _education("Computer Science and Engineering")

        result = adapter.fill(
            _make_context(page),
            candidate,
            manual_resolutions={"course_specialization": "Computer Science"},  # stale
        )

        assert result.success is False
        assert result.status == "manual_required"
        assert result.resolution_request is not None
        assert result.resolution_request["field_name"] == "course_specialization"
        assert result.resolution_request["candidate_value"] == "Computer Science"
        assert set(result.resolution_request["available_options"]) == {
            "Information Technology",
            "Mechanical Engineering",
        }
        # Nothing was selected on the candidate's behalf.
        assert page.evaluate("() => window.__SPEC_OPT === null")
        assert page.evaluate("() => window.__SUBMITTED !== true")

    def test_consent_is_never_resolved_by_profile_fact(self, page):
        """Profile-level consent is not consent — only an explicit per-application
        answer resolves the checkbox."""
        _set_page(
            page,
            _spec_options_html(["Computer Science and Engineering", "Information Technology"]),
        )
        adapter = UnstopAdapter()
        candidate = dict(_CANDIDATE)
        candidate["education"] = _education("Computer Science and Engineering")
        # A profile-level "agree_terms" must NOT resolve the consent field.
        candidate["agree_terms"] = True

        result = adapter.fill(_make_context(page), candidate)

        assert result.success is False
        assert "CONSENT" in (result.error_reason or "")
        assert page.evaluate("() => window.__CONSENT_CLICKED !== true")


# ── Static guarantee: fill() never fabricates consent or option fallback ──────


def test_fill_source_contains_no_consent_default():
    """The fill loop must never treat the mere existence of a consent checkbox as
    authorization to check it — every checkbox click sits under an explicit
    truthy guard derived from an application-scoped answer.
    """
    source = textwrap.dedent(inspect.getsource(UnstopAdapter.fill))
    tree = ast.parse(source)

    # Find the un-checkbox handling branch: `elif f.tag == "un-checkbox":`.
    checkbox_branch = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if (
            isinstance(test, ast.Compare)
            and isinstance(test.left, ast.Attribute)
            and test.left.attr == "tag"
            and any(
                isinstance(c, ast.Constant) and c.value == "un-checkbox"
                for c in test.comparators
            )
        ):
            checkbox_branch = node
            break

    assert checkbox_branch is not None, "the un-checkbox branch must exist in fill()"

    # Every locator click inside the branch must be nested under the explicit
    # `if bool(val):` guard, so an unresolved CONSENT field (val is None) never
    # reaches a click.
    def _is_bool_call(node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        return (
            (isinstance(func, ast.Name) and func.id == "bool")
            or (isinstance(func, ast.Attribute) and func.attr == "bool")
        )

    guard = next(
        (
            stmt
            for stmt in checkbox_branch.body
            if isinstance(stmt, ast.If) and _is_bool_call(stmt.test)
        ),
        None,
    )
    assert guard is not None, "the checkbox branch must be guarded by an explicit truthy value"

    def _clicks(nodes):
        for n in nodes:
            if (
                isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "click"
            ):
                yield n

    branch_clicks = list(_clicks(ast.walk(checkbox_branch)))
    assert branch_clicks, "the checkbox branch must be reachable when consent resolves"
    unguarded = [c for c in branch_clicks if not _is_within(guard, c)]
    assert not unguarded, (
        "A checkbox click exists outside the explicit truthy guard — an "
        "unresolved consent checkbox could be checked."
    )


def _is_within(outer: ast.AST, inner: ast.AST) -> bool:
    """True if ``inner`` is a descendant of ``outer``."""
    for node in ast.walk(outer):
        if node is inner:
            return True
    return False


def test_manual_resolution_precedence_is_application_scoped():
    """classify_field honours an application-scoped manual resolution over the
    profile value, and never mutates the profile."""
    adapter = UnstopAdapter()
    field = FormField(
        id="course_specialization",
        name="course_specialization",
        tag="mat-select",
        type="select",
        label="Specialization*",
        required=True,
        field_role="course_specialization",
        options=["Computer Science", "Information Technology"],
    )
    candidate = {"full_name": "Alex", "education": [{"branch": "Computer Science and Engineering"}]}

    cls, val = adapter.classify_field(
        field, candidate, manual_resolutions={"course_specialization": "Computer Science"}
    )
    assert cls == QuestionClassification.PROFILE_FACT
    assert val == "Computer Science"
    # The supplied candidate dict (a stand-in for the profile) is untouched.
    assert candidate["education"][0]["branch"] == "Computer Science and Engineering"
