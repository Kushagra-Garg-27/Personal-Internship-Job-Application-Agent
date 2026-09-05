"""Standard notification event builders and formatting (Phase 8)."""

from __future__ import annotations

from typing import Any

from core.notifications.base import NotificationEvent


def build_message_event(
    classification: str,
    sender: str,
    subject: str,
    body_preview: str,
    opportunity_title: str | None = None,
    company: str | None = None,
    confidence: float | None = None,
    message_id: int | None = None,
) -> NotificationEvent:
    """Build an event for an inbound recruiter message classification."""
    clean_cls = classification.replace("_", " ").title()
    title = f"Recruiter Message: {clean_cls}"
    
    lines = [
        f"*Status:* {clean_cls}",
        f"*From:* `{sender}`",
        f"*Subject:* {subject}",
    ]
    if company or opportunity_title:
        target = f"{company} — {opportunity_title}" if company and opportunity_title else (company or opportunity_title)
        lines.append(f"*Linked Application:* {target}")
    if confidence is not None:
        lines.append(f"*Confidence:* {confidence:.0%}")
    
    if body_preview:
        lines.append(f"\n> {body_preview.strip()[:300]}")

    return NotificationEvent(
        event_type=classification,
        title=title,
        body="\n".join(lines),
        payload={
            "classification": classification,
            "sender": sender,
            "subject": subject,
            "company": company,
            "opportunity_title": opportunity_title,
            "confidence": confidence,
            "message_id": message_id,
        },
    )


def build_health_event(
    event_type: str,
    detail: str | None = None,
    integration_name: str = "gmail_response_poller",
) -> NotificationEvent:
    """Build an event for an integration health or authentication failure."""
    title = "⚠️ Integration Alert: Gmail Authentication Degraded"
    body = (
        f"*Integration:* `{integration_name}`\n"
        f"*Event:* `{event_type}`\n"
        f"*Detail:* {detail or 'OAuth token refresh failed or credentials expired.'}\n\n"
        "Please re-authenticate your Gmail integration to resume response monitoring."
    )
    return NotificationEvent(
        event_type="integration_unhealthy",
        title=title,
        body=body,
        payload={
            "integration_name": integration_name,
            "event_type": event_type,
            "detail": detail,
        },
    )


def build_quota_event(
    opportunity_id: int | None = None,
    company: str | None = None,
    title: str | None = None,
    rule: str | None = None,
) -> NotificationEvent:
    """Build an event for a Gemini API quota exhaustion deferral."""
    event_title = "⏳ System Alert: Gemini Daily Quota Limit Reached"
    opp_desc = f"Opportunity #{opportunity_id} ({company} - {title})" if opportunity_id else "Incoming opportunity"
    body = (
        f"{opp_desc} was flagged as ambiguous but could not be evaluated by AI "
        "because the daily Gemini free-tier quota has been exhausted.\n\n"
        "Per fail-closed rules, this item has been deferred in `discovered` status "
        "and will automatically re-evaluate upon quota rollover."
    )
    return NotificationEvent(
        event_type="quota_exhausted",
        title=event_title,
        body=body,
        payload={
            "opportunity_id": opportunity_id,
            "company": company,
            "title": title,
            "rule": rule,
        },
    )


def build_channel_degraded_event(
    provider_name: str,
    error_detail: str | None = None,
) -> NotificationEvent:
    """Build an event warning that a secondary notification mirror is broken."""
    title = f"⚠️ Notification Channel Degraded: {provider_name.title()}"
    body = (
        f"The secondary `{provider_name}` mirror provider failed to deliver a notification.\n"
        f"*Error Detail:* {error_detail or 'Unknown provider error'}\n\n"
        "Telegram delivery continues operating normally. Please check your "
        f"{provider_name.title()} API credentials or billing status."
    )
    return NotificationEvent(
        event_type="channel_degraded",
        title=title,
        body=body,
        payload={
            "degraded_provider": provider_name,
            "error": error_detail,
        },
    )


def build_test_event() -> NotificationEvent:
    """Build a test event for connectivity verification."""
    title = "🔔 Test Notification — Job Application Agent"
    body = (
        "This is a test notification verifying that your messaging channels "
        "are configured correctly and active.\n\n"
        "• Telegram Bot: OK\n"
        "• Career Intelligence Core: Online"
    )
    return NotificationEvent(
        event_type="test_event",
        title=title,
        body=body,
        payload={"is_test": True},
    )


def build_application_ready_event(
    opportunity_id: int,
    company: str,
    title: str,
    adapter_name: str | None = None,
    tier: str | None = None,
    url: str | None = None,
    custom_questions_count: int = 0,
) -> NotificationEvent:
    """Build an event notifying user that an application is filled and awaiting their submission click."""
    event_title = f"📝 Application Ready for Review: {company} — {title}"
    
    tier_label = f" ({tier})" if tier else ""
    adapter_info = f"`{adapter_name}{tier_label}`" if adapter_name else "Worker"
    
    lines = [
        f"*Company:* {company}",
        f"*Role:* {title}",
        f"*Adapter:* {adapter_info}",
    ]
    if url:
        lines.append(f"*Listing:* {url}")
    if custom_questions_count > 0:
        lines.append(f"*AI Drafted Answers:* {custom_questions_count} (clearly marked `[AI DRAFT]`, review required)")
    
    lines.append("\n👉 *Action Required:* The form is filled. Review the fields and click **Submit** to finalize.")
    
    return NotificationEvent(
        event_type="application_ready_for_review",
        title=event_title,
        body="\n".join(lines),
        payload={
            "opportunity_id": opportunity_id,
            "company": company,
            "title": title,
            "adapter_name": adapter_name,
            "tier": tier,
            "url": url,
            "custom_questions_count": custom_questions_count,
        },
    )


def build_manual_application_event(
    opportunity_id: int,
    company: str,
    title: str,
    reason: str,
    url: str | None = None,
) -> NotificationEvent:
    """Build an event notifying user that automated application failed or was skipped and needs manual apply."""
    event_title = f"⚠️ Manual Application Required: {company} — {title}"
    lines = [
        f"*Company:* {company}",
        f"*Role:* {title}",
        f"*Reason:* {reason}",
    ]
    if url:
        lines.append(f"*Listing URL:* {url}")
    lines.append("\n👉 *Action Required:* Automated fill could not complete. Please apply manually at the link above.")

    return NotificationEvent(
        event_type="application_manual_required",
        title=event_title,
        body="\n".join(lines),
        payload={
            "opportunity_id": opportunity_id,
            "company": company,
            "title": title,
            "reason": reason,
            "url": url,
        },
    )

