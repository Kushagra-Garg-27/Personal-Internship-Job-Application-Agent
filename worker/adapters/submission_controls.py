"""Domain model, classifier, and unique resolver for Unstop submission controls (M2A).

Provides fail-closed, read-only inspection and semantic classification of submission
controls on web application pages (e.g. Unstop).

Invariants:
- Authoritative DOM inspection is strictly read-only: never calls click(), press(), submit(), or mutating evaluate().
- Eliminates permissive fallbacks: if DOM inspection fails, return explicit INSPECTION_ERROR with locator=None.
- Acquires bounded snapshot of ElementHandles first, extracting candidate metadata directly from that exact handle.
- Zero locator reconstruction, zero ID lookup, zero :has-text() substring matching.
- Retains exact enclosing form identity and compares live closest form by DOM identity.
- Re-inspects all bounded controls immediately before clicking: requires exactly one visible, enabled FINAL_SUBMIT matching selected node.
- Revalidates all fingerprint fields: text, tag, ID, name, role, control type, accessibility attributes, form identity, and sanitized form action.
- Mandatory handle methods: evaluate, is_visible, and is_enabled must be callable and valid.
- Hashes arbitrary DOM attributes by default; emits raw values only from narrow structural allowlists.
- Bounded diagnostic reason codes: diagnostics, logs, and adapter results never expose raw changed text, selectors, URLs, or Playwright exception content.
- Captures no candidate PII, input values, textarea contents, file paths, cookies, or tokens.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import re
from typing import Any
from urllib.parse import urlsplit


class SubmissionControlClassification(str, Enum):
    """Semantic classification of a candidate submission control."""

    FINAL_SUBMIT = "final_submit"
    INTERMEDIATE = "intermediate"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class SubmissionControlResolutionStatus(str, Enum):
    """Outcome of resolving the final submission control on a page."""

    EXACTLY_ONE_FINAL = "exactly_one_final"
    ZERO_FINAL = "zero_final"
    MULTIPLE_FINAL = "multiple_final"
    FINAL_DISABLED = "final_disabled"
    FINAL_HIDDEN = "final_hidden"
    ONLY_INTERMEDIATE_OR_UNKNOWN = "only_intermediate_or_unknown"
    INSPECTION_ERROR = "inspection_error"


# Bounded canonical action vocabulary permitted in persisted diagnostics
ALLOWED_CANONICAL_ACTIONS = {
    "submit application",
    "complete registration",
    "next",
    "continue",
    "save",
    "save and continue",
    "save & continue",
    "save and next",
    "save & next",
    "preview",
    "review",
    "back",
    "previous",
    "prev",
    "edit",
    "upload",
    "upload resume",
    "add",
    "verify",
    "verify otp",
    "send otp",
    "proceed to questions",
    "proceed",
    "cancel",
    "close",
    "dismiss",
}

# Narrow structural allowlists for zero-sensitive diagnostic serialization
ALLOWED_TAGS = {"button", "input"}
ALLOWED_CONTROL_TYPES = {"button", "submit", "reset"}
ALLOWED_ROLES = {"button"}
ALLOWED_HTTP_METHODS = {"GET", "POST"}
ALLOWED_ELEMENT_IDS = {"unstop_submit"}
ALLOWED_NAMES = {"submit", "register", "apply"}
ALLOWED_CLASSES = {
    "btn",
    "button",
    "submit-btn",
    "btn-submit",
    "submit",
    "register",
    "primary",
    "secondary",
    "btn-primary",
    "btn-secondary",
    "disabled",
    "active",
}

# Canonical candidate-control selector shared across inspection and readiness detection
CANDIDATE_CONTROL_SELECTOR = (
    "button, input[type='submit'], input[type='button'], [role='button']"
)


def hash_attribute(val: str | None, prefix: str = "attr") -> str | None:
    """Deterministically hash attribute string."""
    if not val:
        return None
    val_str = str(val).strip()
    if not val_str:
        return None
    h = hashlib.sha256(val_str.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}_{h}"


def sanitize_attribute_value(
    val: str | None,
    allowlist: set[str] | None = None,
    prefix: str = "attr",
    max_len: int = 64,
) -> str | None:
    """Sanitize attribute string by strict structural allowlist.

    Any value not in allowlist is hashed by default (never exposed in raw form).
    """
    if not val:
        return None
    val_str = str(val).strip()
    if not val_str:
        return None

    if allowlist and val_str.lower() in {a.lower() for a in allowlist}:
        return val_str.lower()[:max_len]

    return hash_attribute(val_str, prefix=prefix)


def redact_url(raw_url: str | None) -> str:
    """Redact user credentials, query parameters, and fragments from URL for logs, errors, and output.

    Constructed strictly from normalized scheme, hostname, optional validated port, and path.
    Never uses parts.netloc, preventing userinfo/credential leaks.
    """
    if not raw_url:
        return ""
    try:
        parts = urlsplit(str(raw_url).strip())
        scheme = (parts.scheme or "").lower()
        hostname = (parts.hostname or "").lower()
        path = parts.path or "/"
        if not path.startswith("/"):
            path = f"/{path}"
        if not scheme or not hostname:
            return path
        port_str = ""
        if parts.port is not None and parts.port not in (80, 443):
            port_str = f":{parts.port}"
        return f"{scheme}://{hostname}{port_str}{path}"
    except Exception:
        return "/"


@dataclass
class SubmissionControlCandidate:
    """Non-sensitive structural metadata for a candidate submission control."""

    candidate_id: str
    tag_name: str
    text: str
    raw_text: str
    is_visible: bool
    is_disabled: bool
    is_in_active_form: bool
    control_type: str | None = None
    element_id: str | None = None
    name: str | None = None
    role: str | None = None
    aria_label: str | None = None
    title: str | None = None
    data_testid: str | None = None
    form_action_path: str | None = None
    form_method: str | None = None
    selector_strategy: str = ""
    css_classes: list[str] = field(default_factory=list)
    form_handle: Any = None

    @classmethod
    def create(
        cls,
        tag_name: str,
        text: str,
        raw_text: str,
        is_visible: bool,
        is_disabled: bool,
        is_in_active_form: bool,
        control_type: str | None = None,
        element_id: str | None = None,
        name: str | None = None,
        role: str | None = None,
        aria_label: str | None = None,
        title: str | None = None,
        data_testid: str | None = None,
        form_action_path: str | None = None,
        form_method: str | None = None,
        selector_strategy: str = "",
        css_classes: list[str] | None = None,
        form_handle: Any = None,
    ) -> SubmissionControlCandidate:
        """Create candidate with deterministic SHA-256 derived identifier and explicit safety booleans."""
        if not isinstance(is_visible, bool) or not isinstance(is_disabled, bool) or not isinstance(is_in_active_form, bool):
            raise ValueError("Safety fields (is_visible, is_disabled, is_in_active_form) must be explicit booleans.")

        norm_text = normalize_control_text(text or raw_text)
        classes = css_classes or []
        clean_action = sanitize_url_path(form_action_path)
        raw_sig = (
            f"{tag_name}|{element_id or ''}|{name or ''}|{role or ''}|"
            f"{control_type or ''}|{norm_text}|{clean_action or ''}|{selector_strategy}"
        )
        cand_id = f"ctrl_{hashlib.sha256(raw_sig.encode('utf-8')).hexdigest()[:16]}"
        return cls(
            candidate_id=cand_id,
            tag_name=tag_name,
            text=norm_text,
            raw_text=raw_text or "",
            is_visible=is_visible,
            is_disabled=is_disabled,
            is_in_active_form=is_in_active_form,
            control_type=control_type,
            element_id=element_id,
            name=name,
            role=role,
            aria_label=aria_label,
            title=title,
            data_testid=data_testid,
            form_action_path=clean_action,
            form_method=form_method,
            selector_strategy=selector_strategy,
            css_classes=classes,
            form_handle=form_handle,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to non-sensitive dictionary safe for diagnostics and persistent JSON.

        Never outputs arbitrary raw text, user credentials, or unredacted tokens.
        Arbitrary attributes are hashed by default; only narrow structural allowlists emit raw values.
        """
        # Canonical action vocabulary
        if self.text in ALLOWED_CANONICAL_ACTIONS:
            persisted_text = self.text
        else:
            h = hashlib.sha256(self.text.encode("utf-8")).hexdigest()[:8]
            persisted_text = f"UNKNOWN_TEXT_{h}"

        sanitized_classes = [
            sanitize_attribute_value(c, allowlist=ALLOWED_CLASSES, prefix="cls")
            for c in self.css_classes[:10]
            if c
        ]

        tag = self.tag_name.lower() if self.tag_name.lower() in ALLOWED_TAGS else hash_attribute(self.tag_name, "tag")
        c_type = sanitize_attribute_value(self.control_type, allowlist=ALLOWED_CONTROL_TYPES, prefix="type")
        e_id = sanitize_attribute_value(self.element_id, allowlist=ALLOWED_ELEMENT_IDS, prefix="id")
        name = sanitize_attribute_value(self.name, allowlist=ALLOWED_NAMES, prefix="name")
        role = sanitize_attribute_value(self.role, allowlist=ALLOWED_ROLES, prefix="role")
        aria_label = sanitize_attribute_value(self.aria_label, allowlist=ALLOWED_CANONICAL_ACTIONS, prefix="label")
        title = sanitize_attribute_value(self.title, allowlist=ALLOWED_CANONICAL_ACTIONS, prefix="title")
        testid = sanitize_attribute_value(self.data_testid, allowlist=ALLOWED_CANONICAL_ACTIONS, prefix="testid")
        method = (self.form_method or "").upper() if self.form_method else None
        clean_method = method if method in ALLOWED_HTTP_METHODS else (hash_attribute(method, "method") if method else None)

        return {
            "candidate_id": self.candidate_id,
            "tag_name": tag,
            "canonical_action": persisted_text,
            "text_length": len(self.raw_text),
            "control_type": c_type,
            "element_id": e_id,
            "name": name,
            "role": role,
            "aria_label": aria_label,
            "title": title,
            "data_testid": testid,
            "is_disabled": self.is_disabled,
            "is_visible": self.is_visible,
            "form_action_path": hash_attribute(self.form_action_path, prefix="action") if self.form_action_path else None,
            "form_method": clean_method,
            "is_in_active_form": self.is_in_active_form,
            "selector_strategy": hash_attribute(self.selector_strategy, prefix="sel") if self.selector_strategy else "",
            "css_classes": sanitized_classes,
        }


@dataclass
class SubmissionControlResolution:
    """Result of attempting to resolve the unique final submission control."""

    status: SubmissionControlResolutionStatus
    verified_control: SubmissionControlCandidate | None = None
    locator: Any = None  # Playwright ElementHandle, populated ONLY when status == EXACTLY_ONE_FINAL
    form_handle: Any = None  # Playwright ElementHandle of enclosing form
    candidates: list[SubmissionControlCandidate] = field(default_factory=list)
    classified_candidates: list[tuple[SubmissionControlCandidate, SubmissionControlClassification]] = field(
        default_factory=list
    )
    diagnostics: str = ""


# Exact normalized text phrases representing irreversible final submission
FINAL_SUBMIT_TEXTS = {
    "submit application",
    "complete registration",
}

# Known platform ID that represents the final submission button when inside active form
KNOWN_FINAL_SUBMIT_IDS = {
    "unstop_submit",
}

# Words/phrases indicating multi-step navigation, intermediate saving, or review
INTERMEDIATE_SUBSTRINGS = (
    "next",
    "continue",
    "save",
    "save and continue",
    "save & continue",
    "save and next",
    "save & next",
    "preview",
    "review",
    "back",
    "previous",
    "prev",
    "edit",
    "modify",
    "login",
    "sign in",
    "sign up",
    "upload",
    "add",
    "verify",
    "send otp",
    "resend otp",
    "otp",
    "proceed to questions",
    "proceed",
    "accept cookies",
    "accept cookie",
    "cookie",
    "cancel",
    "close",
    "dismiss",
    "agree",
    "terms",
)


def normalize_control_text(text: str | None) -> str:
    """Normalize button/control text for deterministic comparison.

    Strips leading/trailing whitespace, collapses internal whitespace, and lowercases.
    Never applies fuzzy matching.
    """
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip().lower()


def sanitize_url_path(url: str | None) -> str | None:
    """Sanitize URL to pathname only, strictly stripping scheme, netloc, userinfo, queries, and fragments."""
    if not url:
        return None
    try:
        parts = urlsplit(str(url).strip())
        path = parts.path or "/"
        return path if path.startswith("/") else f"/{path}"
    except Exception:
        return None


def classify_submission_control(candidate: SubmissionControlCandidate) -> SubmissionControlClassification:
    """Pure semantic classifier for candidate submission controls.

    Parameters
    ----------
    candidate : SubmissionControlCandidate
        Extracted metadata for a single interactive control.

    Returns
    -------
    SubmissionControlClassification
        FINAL_SUBMIT, INTERMEDIATE, REJECTED, or UNKNOWN.
    """
    norm_text = candidate.text
    elem_id = (candidate.element_id or "").strip().lower()

    # Rule 1: Controls outside the active application form are REJECTED
    if not candidate.is_in_active_form:
        return SubmissionControlClassification.REJECTED

    # Rule 2: Explicit intermediate meanings (Next, Save, Continue, Preview, Back, etc.)
    combined_desc = " ".join(
        filter(
            None,
            [
                norm_text,
                normalize_control_text(candidate.aria_label),
                normalize_control_text(candidate.title),
                normalize_control_text(candidate.data_testid),
            ],
        )
    )

    for marker in INTERMEDIATE_SUBSTRINGS:
        pattern = rf"(^|\b){re.escape(marker)}(\b|$)"
        if re.search(pattern, combined_desc):
            return SubmissionControlClassification.INTERMEDIATE

    # Rule 3: Exact final submit text matches
    if norm_text in FINAL_SUBMIT_TEXTS:
        return SubmissionControlClassification.FINAL_SUBMIT

    # Rule 4: Known platform final submit ID (unstop_submit)
    if elem_id in KNOWN_FINAL_SUBMIT_IDS:
        if norm_text in FINAL_SUBMIT_TEXTS or norm_text in ("submit", "register", "apply"):
            return SubmissionControlClassification.FINAL_SUBMIT

    # Rule 5: Generic .submit-btn class, input[type='submit'], or partial "submit" alone
    if norm_text == "submit":
        return SubmissionControlClassification.UNKNOWN

    if any(c in ("submit-btn", "btn-submit", "submit") for c in candidate.css_classes):
        return SubmissionControlClassification.UNKNOWN

    if candidate.control_type == "submit":
        return SubmissionControlClassification.UNKNOWN

    return SubmissionControlClassification.UNKNOWN


# JavaScript function executed directly on each acquired ElementHandle.
# Extracts candidate metadata and performs deterministic active application form evaluation.
# Visibility is determined by traversing the FULL ancestor chain, checking:
#   display:none, visibility:hidden/collapse, opacity:0, hidden attribute, aria-hidden=true, inert
JS_HANDLE_INSPECTOR = """
(el) => {
    // 0. Full ancestor chain visibility check
    function isAncestorChainVisible(node) {
        let current = node;
        while (current && current !== document.documentElement.parentElement) {
            if (current.nodeType !== 1) { current = current.parentElement; continue; }
            // hidden attribute
            if (current.hasAttribute('hidden')) return false;
            // aria-hidden=true
            if (current.getAttribute('aria-hidden') === 'true') return false;
            // inert attribute
            if (current.hasAttribute('inert')) return false;
            // computed style checks
            const cs = window.getComputedStyle ? window.getComputedStyle(current) : null;
            if (cs) {
                if (cs.display === 'none') return false;
                if (cs.visibility === 'hidden' || cs.visibility === 'collapse') return false;
                if (parseFloat(cs.opacity) === 0) return false;
            }
            current = current.parentElement;
        }
        return true;
    }

    // 1. Analyze forms in document to evaluate application forms deterministically
    const allForms = Array.from(document.querySelectorAll("form, [role='form']"));
    const plausibleAppForms = [];

    for (let i = 0; i < allForms.length; i++) {
        const f = allForms[i];
        const role = (f.getAttribute("role") || "").toLowerCase();
        const action = (f.getAttribute("action") || "").toLowerCase();
        const fId = (f.id || "").toLowerCase();
        const fClass = (f.className || "").toLowerCase();

        const isSearch = role === "search" ||
            fId.includes("search") ||
            fClass.includes("search") ||
            action.includes("search") ||
            (f.querySelectorAll("input").length === 1 && f.querySelector("input[type='search'], input[name='q'], input[name='query'], input[name='search']"));

        const isNewsletter = fId.includes("newsletter") ||
            fClass.includes("newsletter") ||
            fId.includes("subscribe") ||
            fClass.includes("subscribe") ||
            action.includes("subscribe") ||
            action.includes("newsletter");

        if (!isSearch && !isNewsletter) {
            plausibleAppForms.push(f);
        }
    }

    // 2. Identify el's closest form
    const closestForm = el.closest("form, [role='form']");
    let isInActiveForm = false;
    let formAction = null;
    let formMethod = null;
    let formId = null;

    if (closestForm) {
        formAction = closestForm.getAttribute("action");
        formMethod = closestForm.getAttribute("method");
        formId = closestForm.id || null;
        if (plausibleAppForms.length === 1 && closestForm === plausibleAppForms[0]) {
            isInActiveForm = true;
        }
    }

    // 3. Extract text
    let text = "";
    if (el.tagName.toLowerCase() === "input") {
        text = el.value || el.placeholder || "";
    } else {
        text = el.innerText || el.textContent || "";
    }

    // 4. Disabled
    const isDisabled = el.disabled === true ||
        el.hasAttribute("disabled") ||
        el.getAttribute("aria-disabled") === "true";

    // 5. Visibility — traverse full ancestor chain for all hiding mechanisms
    const isVisible = isAncestorChainVisible(el);

    const classes = Array.from(el.classList || []);

    return {
        tag_name: el.tagName ? el.tagName.toLowerCase() : "",
        raw_text: text,
        control_type: el.getAttribute("type"),
        element_id: el.id || null,
        name: el.getAttribute("name"),
        role: el.getAttribute("role"),
        aria_label: el.getAttribute("aria-label"),
        title: el.getAttribute("title"),
        data_testid: el.getAttribute("data-testid") || el.getAttribute("data-test") || null,
        is_disabled: isDisabled,
        is_visible: isVisible,
        form_action: formAction,
        form_method: formMethod,
        is_in_active_form: isInActiveForm,
        has_form: closestForm !== null,
        form_id: formId,
        css_classes: classes
    };
}
"""


def inspect_submission_controls(page: Any) -> list[tuple[SubmissionControlCandidate, Any]]:
    """Inspect all visible button-like controls on the page using bounded selectors (read-only).

    Strictly read-only:
    - First acquires a bounded snapshot of ElementHandles.
    - Extracts each candidate's metadata directly from that exact handle.
    - Never pairs metadata and handles from separate queries or by array index.
    - If handle acquisition throws or returns invalid structures, raises RuntimeError.
    - Requires mandatory handle methods (evaluate, is_visible, is_enabled).
    """
    if page is None:
        return []

    if not hasattr(page, "query_selector_all"):
        raise RuntimeError("mandatory_page_methods_missing")

    try:
        handles = page.query_selector_all(CANDIDATE_CONTROL_SELECTOR)
    except Exception as exc:
        raise RuntimeError(f"handle_acquisition_failed: {type(exc).__name__}") from exc

    if not isinstance(handles, list):
        raise RuntimeError("malformed_handles_collection")

    results: list[tuple[SubmissionControlCandidate, Any]] = []

    for handle in handles:
        if handle is None:
            raise RuntimeError("null_handle_in_snapshot")

        # Mandatory handle methods check: evaluate, evaluate_handle, is_visible, is_enabled
        if not (
            callable(getattr(handle, "evaluate", None))
            and callable(getattr(handle, "evaluate_handle", None))
            and callable(getattr(handle, "is_visible", None))
            and callable(getattr(handle, "is_enabled", None))
        ):
            raise RuntimeError("mandatory_handle_methods_missing")

        # Directly inspect metadata on the handle
        try:
            item = handle.evaluate(JS_HANDLE_INSPECTOR)
        except Exception as exc:
            raise RuntimeError(f"dom_inspection_failed: {type(exc).__name__}") from exc

        # Require structured authoritative dictionary payload
        if not isinstance(item, dict):
            raise RuntimeError("malformed_inspection_payload")

        # Mandatory safety boolean fields check
        for field_name in ("is_visible", "is_disabled", "is_in_active_form", "has_form"):
            if field_name not in item or not isinstance(item[field_name], bool):
                raise RuntimeError(f"missing_or_invalid_safety_field:{field_name}")

        # Combine DOM evaluation with Playwright handle API probes
        try:
            live_visible = handle.is_visible()
            if not isinstance(live_visible, bool):
                raise RuntimeError("invalid_visibility_probe_result")
        except Exception as exc:
            raise RuntimeError(f"visibility_probe_failed: {type(exc).__name__}") from exc

        try:
            live_enabled = handle.is_enabled()
            if not isinstance(live_enabled, bool):
                raise RuntimeError("invalid_enabled_probe_result")
        except Exception as exc:
            raise RuntimeError(f"enabled_probe_failed: {type(exc).__name__}") from exc

        # Handle fails closed: if either DOM style or Playwright probe says not visible, it's not visible
        final_visible = item["is_visible"] and live_visible
        # If either says disabled, it's disabled
        final_disabled = item["is_disabled"] or (not live_enabled)

        norm_text = normalize_control_text(item.get("raw_text"))
        sanitized_action = sanitize_url_path(item.get("form_action"))

        # Acquire form handle directly when has_form is True.
        # evaluate_handle() returns a JSHandle which may wrap null or a non-element;
        # we must call .as_element() to obtain a true ElementHandle and fail closed otherwise.
        form_handle = None
        if item["has_form"]:
            try:
                jsh = handle.evaluate_handle("el => el.closest('form, [role=\"form\"]')")
            except Exception as exc:
                raise RuntimeError(f"dom_inspection_failed: {type(exc).__name__}") from exc
            if jsh is None:
                raise RuntimeError("missing_or_invalid_form_handle")
            # Convert JSHandle → ElementHandle; missing or non-callable as_element() must fail closed
            if not callable(getattr(jsh, "as_element", None)):
                raise RuntimeError("missing_or_invalid_form_handle")
            form_handle = jsh.as_element()
            if form_handle is None:
                raise RuntimeError("missing_or_invalid_form_handle")

        # is_in_active_form=True without has_form=True or without a form handle is an inspection error
        if item["is_in_active_form"] and (not item["has_form"] or form_handle is None):
            raise RuntimeError("active_form_without_form_handle")

        cand = SubmissionControlCandidate.create(
            tag_name=str(item.get("tag_name", "")),
            text=norm_text,
            raw_text=str(item.get("raw_text", "")),
            is_visible=final_visible,
            is_disabled=final_disabled,
            is_in_active_form=item["is_in_active_form"],
            control_type=item.get("control_type"),
            element_id=item.get("element_id"),
            name=item.get("name"),
            role=item.get("role"),
            aria_label=item.get("aria_label"),
            title=item.get("title"),
            data_testid=item.get("data_testid"),
            form_action_path=sanitized_action,
            form_method=item.get("form_method"),
            selector_strategy=str(item.get("tag_name", "button")),
            css_classes=item.get("css_classes", []),
            form_handle=form_handle,
        )

        results.append((cand, handle))

    return results


def resolve_final_submission_control(page: Any) -> SubmissionControlResolution:
    """Resolve the unique, irreversible final submission control on the page.

    Enforces fail-closed safety rules:
    - Exactly one verified final control is required.
    - Zero verified final controls fails closed.
    - Multiple verified final controls fails closed.
    - A disabled final control fails closed.
    - A hidden final control fails closed.
    - Intermediate / unknown controls are never clicked.
    - Any inspection or handle error returns INSPECTION_ERROR with locator=None.
    - Completely eliminates locator reconstruction or fallback lookup.
    """
    try:
        inspected = inspect_submission_controls(page)
    except Exception as exc:
        return SubmissionControlResolution(
            status=SubmissionControlResolutionStatus.INSPECTION_ERROR,
            diagnostics="dom_inspection_failed",
            locator=None,
        )

    candidates: list[SubmissionControlCandidate] = []
    classified: list[tuple[SubmissionControlCandidate, SubmissionControlClassification]] = []
    final_candidates: list[tuple[SubmissionControlCandidate, Any]] = []
    intermediate_count = 0
    unknown_count = 0

    for cand, handle in inspected:
        candidates.append(cand)
        cls = classify_submission_control(cand)
        classified.append((cand, cls))

        if cls == SubmissionControlClassification.FINAL_SUBMIT:
            final_candidates.append((cand, handle))
        elif cls == SubmissionControlClassification.INTERMEDIATE:
            intermediate_count += 1
        elif cls == SubmissionControlClassification.UNKNOWN:
            unknown_count += 1

    # Case 1: Multiple final controls detected
    if len(final_candidates) > 1:
        return SubmissionControlResolution(
            status=SubmissionControlResolutionStatus.MULTIPLE_FINAL,
            candidates=candidates,
            classified_candidates=classified,
            diagnostics="multiple_final_controls",
            locator=None,
        )

    # Case 2: Zero final controls detected
    if len(final_candidates) == 0:
        status = (
            SubmissionControlResolutionStatus.ONLY_INTERMEDIATE_OR_UNKNOWN
            if (intermediate_count > 0 or unknown_count > 0)
            else SubmissionControlResolutionStatus.ZERO_FINAL
        )
        diag = "only_intermediate_or_unknown" if (intermediate_count > 0 or unknown_count > 0) else "zero_final_controls"
        return SubmissionControlResolution(
            status=status,
            candidates=candidates,
            classified_candidates=classified,
            diagnostics=diag,
            locator=None,
        )

    # Exactly one final candidate
    cand, handle = final_candidates[0]

    # Verify visibility
    if not cand.is_visible:
        return SubmissionControlResolution(
            status=SubmissionControlResolutionStatus.FINAL_HIDDEN,
            candidates=candidates,
            classified_candidates=classified,
            diagnostics="final_control_hidden",
            locator=None,
        )

    # Verify enabled state
    if cand.is_disabled:
        return SubmissionControlResolution(
            status=SubmissionControlResolutionStatus.FINAL_DISABLED,
            candidates=candidates,
            classified_candidates=classified,
            diagnostics="final_control_disabled",
            locator=None,
        )

    # Verify handle presence
    if handle is None:
        return SubmissionControlResolution(
            status=SubmissionControlResolutionStatus.INSPECTION_ERROR,
            candidates=candidates,
            classified_candidates=classified,
            diagnostics="missing_dom_handle",
            locator=None,
        )

    # Verify handle count / uniqueness if supported
    if hasattr(handle, "count"):
        try:
            count = handle.count()
            if count != 1:
                return SubmissionControlResolution(
                    status=SubmissionControlResolutionStatus.MULTIPLE_FINAL,
                    candidates=candidates,
                    classified_candidates=classified,
                    diagnostics="handle_count_not_one",
                    locator=None,
                )
        except Exception:
            return SubmissionControlResolution(
                status=SubmissionControlResolutionStatus.INSPECTION_ERROR,
                candidates=candidates,
                classified_candidates=classified,
                diagnostics="handle_count_failed",
                locator=None,
            )

    return SubmissionControlResolution(
        status=SubmissionControlResolutionStatus.EXACTLY_ONE_FINAL,
        verified_control=cand,
        locator=handle,
        form_handle=cand.form_handle,
        candidates=candidates,
        classified_candidates=classified,
        diagnostics="exactly_one_final_resolved",
    )


def revalidate_handle_before_click(
    page: Any,
    handle: Any,
    expected_candidate: SubmissionControlCandidate,
    expected_form_handle: Any = None,
) -> tuple[bool, str]:
    """Strictly revalidate the exact DOM element handle immediately before the click path.

    Comprehensive verification:
    1. Reruns full bounded control inspection on the page: requires exactly one visible, enabled
       FINAL_SUBMIT, and verifies it matches this handle by DOM identity.
    2. Verifies mandatory handle methods (evaluate, is_visible, is_enabled).
    3. Verifies handle is still attached to the document DOM (isConnected === true).
    4. Verifies handle is still visible and enabled.
    5. Verifies handle is still inside the same enclosing form by DOM identity.
    6. Verifies all fingerprint fields (text, tag, ID, name, role, control type, accessibility metadata, sanitized form action).
    7. Dynamically checks active-form rules without hardcoding True.
    8. Re-classifies candidate as FINAL_SUBMIT.
    """
    if handle is None:
        return False, "handle_none"

    if page is None:
        return False, "page_none"

    # 1. Rerun full bounded control inspection to detect inserted competing controls or replacement
    try:
        reinspected = inspect_submission_controls(page)
    except Exception:
        return False, "reinspection_failed"

    final_matches = []
    for r_cand, r_handle in reinspected:
        cls = classify_submission_control(r_cand)
        if cls == SubmissionControlClassification.FINAL_SUBMIT and r_cand.is_visible and not r_cand.is_disabled:
            final_matches.append((r_cand, r_handle))

    if len(final_matches) == 0:
        return False, "no_final_controls_detected"
    if len(final_matches) > 1:
        return False, "competing_final_controls_detected"

    sole_cand, sole_handle = final_matches[0]

    # Verify DOM identity with previously resolved handle
    if hasattr(handle, "evaluate"):
        try:
            is_same_node = handle.evaluate("(el, other) => el === other", sole_handle)
            if not is_same_node:
                return False, "control_node_replaced"
        except Exception:
            return False, "node_identity_check_failed"

    # 2. Mandatory handle methods check: evaluate, evaluate_handle, is_visible, is_enabled
    if not (
        callable(getattr(handle, "evaluate", None))
        and callable(getattr(handle, "evaluate_handle", None))
        and callable(getattr(handle, "is_visible", None))
        and callable(getattr(handle, "is_enabled", None))
    ):
        return False, "mandatory_handle_methods_missing"

    # 3. Attached check: require exactly True
    try:
        is_connected = handle.evaluate("el => el.isConnected === true")
        if is_connected is not True:
            return False, "handle_detached"
    except Exception:
        return False, "attachment_probe_failed"

    # 4. Visibility and enabled checks
    try:
        if not handle.is_visible():
            return False, "handle_hidden"
    except Exception:
        return False, "visibility_probe_failed"

    try:
        if not handle.is_enabled():
            return False, "handle_disabled"
    except Exception:
        return False, "enabled_probe_failed"

    # 5. Enclosing form DOM identity check
    form_to_compare = expected_form_handle or sole_cand.form_handle
    if form_to_compare is not None:
        try:
            is_same_form = handle.evaluate("(el, f) => el.closest('form, [role=\"form\"]') === f", form_to_compare)
            if not is_same_form:
                return False, "form_mismatch"
        except Exception:
            return False, "form_identity_check_failed"

    # 6. Extract live element properties and evaluate active form dynamically
    try:
        live_meta = handle.evaluate(JS_HANDLE_INSPECTOR)
    except Exception:
        return False, "metadata_probe_failed"

    if not isinstance(live_meta, dict):
        return False, "metadata_probe_failed"

    # Require explicit boolean values for is_visible, is_disabled, is_in_active_form, and has_form in live_meta
    for field_name in ("is_visible", "is_disabled", "is_in_active_form", "has_form"):
        if field_name not in live_meta or not isinstance(live_meta[field_name], bool):
            return False, f"missing_or_invalid_safety_field:{field_name}"

    if not live_meta["is_visible"]:
        return False, "handle_hidden"

    if live_meta["is_disabled"]:
        return False, "handle_disabled"

    if not live_meta["has_form"]:
        return False, "no_enclosing_form"

    # Compare fingerprint fields
    live_norm_text = normalize_control_text(live_meta.get("raw_text"))
    if live_norm_text != expected_candidate.text:
        return False, "control_text_changed"

    if (live_meta.get("tag_name") or "").lower() != (expected_candidate.tag_name or "").lower():
        return False, "control_tag_changed"

    if (live_meta.get("element_id") or None) != (expected_candidate.element_id or None):
        return False, "control_id_changed"

    if (live_meta.get("name") or None) != (expected_candidate.name or None):
        return False, "control_name_changed"

    if (live_meta.get("role") or None) != (expected_candidate.role or None):
        return False, "control_role_changed"

    if (live_meta.get("control_type") or None) != (expected_candidate.control_type or None):
        return False, "control_type_changed"

    if (live_meta.get("aria_label") or None) != (expected_candidate.aria_label or None):
        return False, "accessibility_label_changed"

    if (live_meta.get("title") or None) != (expected_candidate.title or None):
        return False, "accessibility_title_changed"

    if (live_meta.get("data_testid") or None) != (expected_candidate.data_testid or None):
        return False, "accessibility_testid_changed"

    live_action_path = sanitize_url_path(live_meta.get("form_action"))
    if live_action_path != expected_candidate.form_action_path:
        return False, "form_action_changed"

    # 7. Dynamic re-classification (without hardcoding is_in_active_form=True)
    if not live_meta["is_in_active_form"]:
        return False, "not_in_active_form"

    recheck_cand = SubmissionControlCandidate.create(
        tag_name=live_meta.get("tag_name", ""),
        text=live_norm_text,
        raw_text=live_meta.get("raw_text", ""),
        is_visible=live_meta["is_visible"],
        is_disabled=live_meta["is_disabled"],
        is_in_active_form=live_meta["is_in_active_form"],
        control_type=live_meta.get("control_type"),
        element_id=live_meta.get("element_id"),
        name=live_meta.get("name"),
        role=live_meta.get("role"),
        aria_label=live_meta.get("aria_label"),
        title=live_meta.get("title"),
        data_testid=live_meta.get("data_testid"),
        form_action_path=live_action_path,
        form_method=live_meta.get("form_method"),
    )
    if classify_submission_control(recheck_cand) != SubmissionControlClassification.FINAL_SUBMIT:
        return False, "reclassification_failed"

    return True, "revalidation_passed"
