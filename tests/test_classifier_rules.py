"""Tests for the deterministic rules classifier (Phase 7)."""

from __future__ import annotations

import pytest

from core.messaging.classifier_rules import (
    RULES_CONFIDENCE_THRESHOLD,
    RulesClassificationResult,
    classify_by_rules,
)


class TestInterviewClassification:
    def test_schedule_interview(self):
        result = classify_by_rules("Schedule your interview", "")
        assert result.classification == "interview_invite"
        assert result.confidence >= RULES_CONFIDENCE_THRESHOLD

    def test_next_round(self):
        result = classify_by_rules("", "We'd like to invite you to the next round")
        assert result.classification == "interview_invite"
        assert result.confidence >= RULES_CONFIDENCE_THRESHOLD

    def test_phone_screen(self):
        result = classify_by_rules("Phone screen for Engineering role", "")
        assert result.classification == "interview_invite"
        assert result.confidence >= RULES_CONFIDENCE_THRESHOLD

    def test_video_call(self):
        result = classify_by_rules("Video interview with hiring manager", "")
        assert result.classification == "interview_invite"
        assert result.confidence >= RULES_CONFIDENCE_THRESHOLD


class TestRejectionClassification:
    def test_unfortunately(self):
        result = classify_by_rules("", "Unfortunately, we have decided...")
        assert result.classification == "rejection"
        assert result.confidence >= RULES_CONFIDENCE_THRESHOLD

    def test_not_moving_forward(self):
        result = classify_by_rules("Update on your application", "We are not moving forward with your candidacy")
        assert result.classification == "rejection"
        assert result.confidence >= RULES_CONFIDENCE_THRESHOLD

    def test_position_filled(self):
        result = classify_by_rules("Application status", "The position has been filled")
        assert result.classification == "rejection"
        assert result.confidence >= RULES_CONFIDENCE_THRESHOLD


class TestOfferClassification:
    def test_pleased_to_offer(self):
        result = classify_by_rules("Job Offer", "We are pleased to offer you the position")
        assert result.classification == "offer"
        assert result.confidence >= RULES_CONFIDENCE_THRESHOLD

    def test_offer_letter(self):
        result = classify_by_rules("Your offer letter", "Please find attached your offer letter")
        assert result.classification == "offer"
        assert result.confidence >= RULES_CONFIDENCE_THRESHOLD

    def test_compensation_package(self):
        result = classify_by_rules("", "Here's your compensation package details")
        assert result.classification == "offer"
        assert result.confidence >= RULES_CONFIDENCE_THRESHOLD


class TestScreeningClassification:
    def test_coding_challenge(self):
        result = classify_by_rules("Coding challenge", "Please complete the coding challenge")
        assert result.classification == "screening_question"
        assert result.confidence >= RULES_CONFIDENCE_THRESHOLD

    def test_take_home(self):
        result = classify_by_rules("Take-home assignment", "")
        assert result.classification == "screening_question"
        assert result.confidence >= RULES_CONFIDENCE_THRESHOLD


class TestFollowUpClassification:
    def test_following_up(self):
        result = classify_by_rules("Following up on your application", "")
        assert result.classification == "follow_up"
        assert result.confidence >= RULES_CONFIDENCE_THRESHOLD

    def test_checking_in(self):
        result = classify_by_rules("", "Just checking in on the status")
        assert result.classification == "follow_up"


class TestGenericClassification:
    def test_received_application(self):
        result = classify_by_rules("", "We have received your application")
        assert result.classification == "generic"
        assert result.confidence >= RULES_CONFIDENCE_THRESHOLD

    def test_thank_you_for_applying(self):
        result = classify_by_rules("", "Thank you for applying to our company")
        assert result.classification == "generic"
        assert result.confidence >= RULES_CONFIDENCE_THRESHOLD


class TestUnclassified:
    def test_no_patterns(self):
        result = classify_by_rules("Meeting notes", "Here are the meeting notes from today")
        assert result.classification == "unclassified"
        assert result.confidence < RULES_CONFIDENCE_THRESHOLD

    def test_empty_inputs(self):
        result = classify_by_rules("", "")
        assert result.classification == "unclassified"
        assert result.confidence == 0.0


class TestPriority:
    def test_offer_over_interview(self):
        """Offer has higher priority than interview in the pattern order."""
        result = classify_by_rules(
            "Congratulations!",
            "We are pleased to offer you the position after your interview"
        )
        assert result.classification == "offer"
