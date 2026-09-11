"""U4 offline tests for the Unstop human-approval submission gate.

Covers the Phase 5 matrix without touching the real Unstop site:
1. ``ready_for_review`` does not submit.
2. No explicit approval -> no submission.
3. Explicit approval -> submission is permitted.
4. Successful submission -> correct local status.
5. Failed submission -> correct failure state.
6. Duplicate submission protection.
7. Confirmation must come from the actual Unstop result.
8. Fill-only execution cannot cross the submission boundary.

Uses lightweight fake page doubles — no browser, no network, no credentials.
"""

from __future__ import annotations

import json

import pytest

from core.services import application_service, opportunity_service
from core.status import (
    HUMAN_SUBMISSION_APPROVAL_TOKEN,
    ApplicationStatus,
    OpportunityStatus,
    SubmissionApprovalRequiredError,
    is_human_approved,
)
from worker.adapters.base import ApplicationContext
from worker.adapters.unstop import UnstopAdapter
from worker.engine.filler import ApplicationFiller


# ── Fake Playwright doubles ───────────────────────────────────────────────

class _FakeElement:
    def __init__(self, page, visible=True, enabled=True):
        self._page = page
        self._visible = visible
        self._enabled = enabled

    def is_visible(self):
        return self._visible

    def is_enabled(self):
        return self._enabled

    def click(self):
        self._page.clicks.append("submit")
        # Simulate Unstop navigating to a confirmation screen after submit.
        self._page.url = "https://unstop.com/competitions/1753995/register/success"
        self._page.confirmation_shown = True


class _FakeLocator:
    def __init__(self, page, selector):
        self._page = page
        self._selector = selector

    def count(self):
        sel = self._selector
        if "challenges.cloudflare" in sel or "challenge-stage" in sel:
            return 0
        if sel in UnstopAdapter.SUBMISSION_SELECTORS:
            return 1 if self._page.submit_present else 0
        if sel.startswith("text="):
            wanted = sel[len("text="):].strip("'\"")
            if wanted in ("Successfully Registered",) and self._page.confirmation_shown:
                return 1
            return 0
        return 0

    @property
    def first(self):
        return _FakeElement(self._page)

    def nth(self, _i):
        return _FakeElement(self._page)


class _FakePage:
    """Minimal stand-in for a Playwright page on the Unstop register form."""

    def __init__(self, url="https://unstop.com/competitions/1753995/register"):
        self.url = url
        self.clicks: list[str] = []
        self.confirmation_shown = False
        self.submit_present = True

    def locator(self, selector):
        return _FakeLocator(self, selector)

    def wait_for_timeout(self, _ms):
        return None


def _unstop_ctx(page=None, opportunity_id=1753995):
    return ApplicationContext(
        opportunity_id=opportunity_id,
        listing_url="https://unstop.com/competitions/1753995/register",
        adapter_name="unstop",
        tier=UnstopAdapter.tier,
        browser_page=page if page is not None else _FakePage(),
    )


def _awaiting_unstop_app(db_session):
    opp = opportunity_service.create_opportunity(
        db_session,
        title="System Admin",
        company="Yugasa Software Labs",
        url="https://unstop.com/competitions/1753995/register",
        source="unstop",
        reliability_tier="experimental",
    )
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.RECOMMENDED)
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.READY_TO_APPLY)
    opportunity_service.transition_status(db_session, opp.id, OpportunityStatus.AWAITING_SUBMISSION)
    app = application_service.create_application(
        db_session, opportunity_id=opp.id, adapter_name="unstop"
    )
    application_service.transition_application_status(
        db_session,
        app.id,
        ApplicationStatus.FORM_FILLED,
        notes=json.dumps({"adapter": "unstop", "tier": "experimental"}),
    )
    db_session.commit()
    return opp, app


# ── 1 & 8. ready_for_review / fill-only never submits ─────────────────────

def test_ready_for_review_state_is_not_approval():
    """Case 1+8: pre-submit states are data, never an approval signal."""
    for probe in ["ready_for_review", "FORM_FILLED", "form_filled",
                  "awaiting_submission", "AWAITING_SUBMISSION", "valid", "autofilled"]:
        assert is_human_approved(probe) is False
    assert is_human_approved(None) is False
    assert is_human_approved("") is False
    assert is_human_approved(True) is False  # type: ignore[arg-type]
    assert is_human_approved(HUMAN_SUBMISSION_APPROVAL_TOKEN) is True


def test_submit_application_without_token_raises_before_touching_browser():
    """Case 2+8: no approval -> PermissionError, zero browser interaction."""
    adapter = UnstopAdapter()
    page = _FakePage()
    ctx = _unstop_ctx(page)
    for probe in [None, "", "ready_for_review", "submitted", True, 1]:
        with pytest.raises(PermissionError):
            adapter.submit_application(ctx, approval_token=probe)  # type: ignore[arg-type]
    assert page.clicks == []


# ── 3 & 4 & 7. Approved submit observes real Unstop confirmation ──────────

def test_approved_submit_clicks_once_and_reports_platform_confirmation():
    """Cases 3+4+7: with approval, exactly one click; success comes from Unstop."""
    adapter = UnstopAdapter()
    page = _FakePage()
    ctx = _unstop_ctx(page)

    result = adapter.submit_application(ctx, approval_token=HUMAN_SUBMISSION_APPROVAL_TOKEN)

    assert result["success"] is True
    assert result["confirmed"] is True
    assert page.clicks == ["submit"]  # exactly one click
    assert result["confirmation_ref"] == "UNSTOP-CONFIRMED-1753995"
    assert "Successfully Registered" in result["detail"] or "success" in result["detail"].lower()


def test_approved_submit_without_platform_confirmation_reports_unconfirmed():
    """Case 7: a click with no observable Unstop confirmation is NOT success."""
    adapter = UnstopAdapter()
    page = _FakePage()

    orig_click = _FakeElement.click

    def _no_nav_click(self):
        self._page.clicks.append("submit")
        # Unstop shows nothing: no navigation, no confirmation banner.

    _FakeElement.click = _no_nav_click
    try:
        ctx = _unstop_ctx(page)
        # check_status sees /register URL -> not_submitted; wait loop expires.
        result = adapter.submit_application(ctx, approval_token=HUMAN_SUBMISSION_APPROVAL_TOKEN)
    finally:
        _FakeElement.click = orig_click

    assert result["success"] is False
    assert result["confirmed"] is False
    assert page.clicks == ["submit"]


# ── 6. Duplicate protection ───────────────────────────────────────────────

def test_submit_refuses_when_already_confirmed():
    """Case 6: an already-confirmed page is never clicked again."""
    adapter = UnstopAdapter()
    page = _FakePage()
    page.confirmation_shown = True
    ctx = _unstop_ctx(page)

    result = adapter.submit_application(ctx, approval_token=HUMAN_SUBMISSION_APPROVAL_TOKEN)

    assert result["success"] is True
    assert result.get("already_confirmed") is True
    assert page.clicks == []


def test_confirm_and_submit_twice_refuses_second(db_session):
    """Case 6: local status machine refuses a second submission of the same attempt."""
    opp, app = _awaiting_unstop_app(db_session)
    filler = ApplicationFiller()
    first = filler.confirm_and_submit(
        db_session,
        app.id,
        approval_token=HUMAN_SUBMISSION_APPROVAL_TOKEN,
        platform_confirmed=True,
        confirmation_ref="UNSTOP-CONFIRMED-1753995",
        confirmation_detail="Unstop showed 'Successfully Registered'.",
    )
    assert first["success"] is True

    with pytest.raises(ValueError, match="already submitted"):
        filler.confirm_and_submit(
            db_session,
            app.id,
            approval_token=HUMAN_SUBMISSION_APPROVAL_TOKEN,
            platform_confirmed=True,
            confirmation_ref="UNSTOP-CONFIRMED-1753995",
        )

    apps = [a for a in db_session.query(type(app)).filter_by(opportunity_id=opp.id).all()]
    assert len(apps) == 1
    assert apps[0].status == ApplicationStatus.SUBMITTED.value


# ── 4 & 5. Local status outcomes ──────────────────────────────────────────

def test_confirmed_browser_submission_updates_local_status(db_session):
    """Case 4: platform-confirmed -> Application SUBMITTED + Opportunity APPLIED."""
    opp, app = _awaiting_unstop_app(db_session)
    result = ApplicationFiller().confirm_and_submit(
        db_session,
        app.id,
        approval_token=HUMAN_SUBMISSION_APPROVAL_TOKEN,
        platform_confirmed=True,
        confirmation_ref="UNSTOP-CONFIRMED-1753995",
        confirmation_detail="Unstop showed 'Successfully Registered'.",
    )
    assert result["success"] is True
    assert result["status"] == "applied"

    db_session.refresh(opp)
    db_session.refresh(app)
    assert app.status == ApplicationStatus.SUBMITTED.value
    assert app.submitted_at is not None
    assert app.confirmation_ref == "UNSTOP-CONFIRMED-1753995"
    assert opp.status == OpportunityStatus.APPLIED.value


def test_unconfirmed_browser_submission_keeps_review_state(db_session):
    """Case 5: approved but unconfirmed -> stays FORM_FILLED / AWAITING_SUBMISSION."""
    opp, app = _awaiting_unstop_app(db_session)
    result = ApplicationFiller().confirm_and_submit(
        db_session, app.id, approval_token=HUMAN_SUBMISSION_APPROVAL_TOKEN
    )
    assert result["success"] is False
    assert result["status"] == "unconfirmed"

    db_session.refresh(opp)
    db_session.refresh(app)
    assert app.status == ApplicationStatus.FORM_FILLED.value
    assert opp.status == OpportunityStatus.AWAITING_SUBMISSION.value


# ── 2. Service + API gate ─────────────────────────────────────────────────

def test_service_without_approval_token_fails_closed(db_session):
    """Case 2: core service refuses without the token; nothing mutates."""
    from core.services import submission_service

    opp, app = _awaiting_unstop_app(db_session)
    for probe in [None, "", "ready_for_review", "submitted", "Human approved"]:
        with pytest.raises(SubmissionApprovalRequiredError):
            submission_service.confirm_and_submit(db_session, app.id, approval_token=probe)

    db_session.refresh(opp)
    db_session.refresh(app)
    assert app.status == ApplicationStatus.FORM_FILLED.value
    assert opp.status == OpportunityStatus.AWAITING_SUBMISSION.value


def test_api_requires_approval_token(client, db_session):
    """Case 2: HTTP layer — missing token -> 422, wrong token -> 403, no mutation."""
    opp, app = _awaiting_unstop_app(db_session)

    resp = client.post(f"/applications/{app.id}/confirm-submit", json={})
    assert resp.status_code == 422

    resp = client.post(
        f"/applications/{app.id}/confirm-submit",
        json={"approval_token": "ready_for_review"},
    )
    assert resp.status_code == 403

    db_session.refresh(opp)
    db_session.refresh(app)
    assert app.status == ApplicationStatus.FORM_FILLED.value
    assert opp.status == OpportunityStatus.AWAITING_SUBMISSION.value
