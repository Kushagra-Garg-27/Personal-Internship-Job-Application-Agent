"""Core-side watcher for Worker status changes (Phase 9).

Per §5.6: The Worker process never touches notification credentials or tokens.
Instead, when the Worker fills a form or requires manual application, it updates
the database.  This watcher runs inside the Core process via APScheduler, detects
those state transitions, and safely triggers Phase 8's NotificationService.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models.opportunity import Application, Opportunity, StatusHistory
from core.notifications.events import (
    build_application_ready_event,
    build_manual_application_event,
)
from core.notifications.service import notification_service
from core.status import OpportunityStatus

logger = logging.getLogger(__name__)


def poll_worker_events(session: Session) -> dict[str, Any]:
    """Scan for opportunities in awaiting_submission or manual_application_required
    that have not yet notified the user.
    """
    stmt = (
        select(Opportunity)
        .where(
            Opportunity.status.in_([
                OpportunityStatus.AWAITING_SUBMISSION.value,
                OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value,
            ])
        )
        .order_by(Opportunity.id.asc())
    )
    opportunities = list(session.scalars(stmt).all())
    
    stats = {"checked": len(opportunities), "notified": 0}

    for opp in opportunities:
        meta = dict(opp.metadata_json or {})
        already_notified_for = meta.get("worker_notification_sent_for")

        # Deduplication: only notify once per status state
        if already_notified_for == opp.status:
            continue

        if opp.status == OpportunityStatus.AWAITING_SUBMISSION.value:
            # Find latest application record to get adapter and details
            app_stmt = (
                select(Application)
                .where(Application.opportunity_id == opp.id)
                .order_by(Application.attempt_number.desc())
            )
            latest_app = session.scalars(app_stmt).first()
            adapter_name = latest_app.adapter_name if latest_app else opp.source
            
            # Count any AI-drafted custom answers if present in notes or metadata
            custom_count = 0
            if latest_app and latest_app.notes and "[AI DRAFT" in latest_app.notes:
                custom_count = latest_app.notes.count("[AI DRAFT")

            event = build_application_ready_event(
                opportunity_id=opp.id,
                company=opp.company,
                title=opp.title,
                adapter_name=adapter_name,
                tier=opp.reliability_tier,
                url=opp.url,
                custom_questions_count=custom_count,
            )
            notification_service.dispatch(event, session=session)
            meta["worker_notification_sent_for"] = opp.status
            opp.metadata_json = meta
            session.flush()
            stats["notified"] += 1
            logger.info("Notified user of application ready for review: opp #%d (%s)", opp.id, opp.company)

        elif opp.status == OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value:
            # Fetch latest reason from status history
            hist_stmt = (
                select(StatusHistory)
                .where(
                    StatusHistory.opportunity_id == opp.id,
                    StatusHistory.new_status == OpportunityStatus.MANUAL_APPLICATION_REQUIRED.value,
                )
                .order_by(StatusHistory.changed_at.desc())
            )
            latest_hist = session.scalars(hist_stmt).first()
            reason = (
                latest_hist.reason
                if latest_hist and latest_hist.reason
                else "Automated application could not be completed."
            )

            event = build_manual_application_event(
                opportunity_id=opp.id,
                company=opp.company,
                title=opp.title,
                reason=reason,
                url=opp.url,
            )
            notification_service.dispatch(event, session=session)
            meta["worker_notification_sent_for"] = opp.status
            opp.metadata_json = meta
            session.flush()
            stats["notified"] += 1
            logger.info("Notified user of manual application required: opp #%d (%s)", opp.id, opp.company)

    if stats["notified"] > 0:
        session.commit()

    return stats
