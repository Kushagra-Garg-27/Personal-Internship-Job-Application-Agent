import pytest
from worker.adapters.submission_controls import (
    SubmissionControlCandidate,
    SubmissionControlClassification,
    classify_submission_control,
)

def test_known_final_submit_id_overrides_next_text():
    # `unstop_submit` + "Next" -> FINAL_SUBMIT
    candidate = SubmissionControlCandidate(
        candidate_id="c1",
        tag_name="button",
        raw_text="Next",
        text="next",
        is_in_active_form=True,
        is_visible=True,
        is_disabled=False,
        element_id="unstop_submit",
        css_classes=[],
        control_type="button",
        aria_label="",
        title="",
        data_testid=""
    )
    assert classify_submission_control(candidate) == SubmissionControlClassification.FINAL_SUBMIT

def test_known_final_submit_id_with_submit_text():
    # `unstop_submit` + "Submit" -> FINAL_SUBMIT
    candidate = SubmissionControlCandidate(
        candidate_id="c2",
        tag_name="button",
        raw_text="Submit",
        text="submit",
        is_in_active_form=True,
        is_visible=True,
        is_disabled=False,
        element_id="unstop_submit",
        css_classes=[],
        control_type="button",
        aria_label="",
        title="",
        data_testid=""
    )
    assert classify_submission_control(candidate) == SubmissionControlClassification.FINAL_SUBMIT

def test_known_final_submit_id_with_register_text():
    # `unstop_submit` + "Register" -> FINAL_SUBMIT
    candidate = SubmissionControlCandidate(
        candidate_id="c3",
        tag_name="button",
        raw_text="Register",
        text="register",
        is_in_active_form=True,
        is_visible=True,
        is_disabled=False,
        element_id="unstop_submit",
        css_classes=[],
        control_type="button",
        aria_label="",
        title="",
        data_testid=""
    )
    assert classify_submission_control(candidate) == SubmissionControlClassification.FINAL_SUBMIT

def test_generic_next():
    # generic "Next" -> INTERMEDIATE
    candidate = SubmissionControlCandidate(
        candidate_id="c4",
        tag_name="button",
        raw_text="Next",
        text="next",
        is_in_active_form=True,
        is_visible=True,
        is_disabled=False,
        element_id="",
        css_classes=[],
        control_type="button",
        aria_label="",
        title="",
        data_testid=""
    )
    assert classify_submission_control(candidate) == SubmissionControlClassification.INTERMEDIATE

def test_generic_continue():
    # generic "Continue" -> INTERMEDIATE
    candidate = SubmissionControlCandidate(
        candidate_id="c5",
        tag_name="button",
        raw_text="Continue",
        text="continue",
        is_in_active_form=True,
        is_visible=True,
        is_disabled=False,
        element_id="",
        css_classes=[],
        control_type="button",
        aria_label="",
        title="",
        data_testid=""
    )
    assert classify_submission_control(candidate) == SubmissionControlClassification.INTERMEDIATE

def test_explicit_final_submit_text():
    # explicit final-submit text -> FINAL_SUBMIT
    candidate = SubmissionControlCandidate(
        candidate_id="c6",
        tag_name="button",
        raw_text="Submit Application",
        text="submit application",
        is_in_active_form=True,
        is_visible=True,
        is_disabled=False,
        element_id="",
        css_classes=[],
        control_type="button",
        aria_label="",
        title="",
        data_testid=""
    )
    assert classify_submission_control(candidate) == SubmissionControlClassification.FINAL_SUBMIT

def test_unknown_control():
    # unknown/ambiguous control -> UNKNOWN
    candidate = SubmissionControlCandidate(
        candidate_id="c7",
        tag_name="button",
        raw_text="Click here",
        text="click here",
        is_in_active_form=True,
        is_visible=True,
        is_disabled=False,
        element_id="",
        css_classes=[],
        control_type="button",
        aria_label="",
        title="",
        data_testid=""
    )
    assert classify_submission_control(candidate) == SubmissionControlClassification.UNKNOWN
