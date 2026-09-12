"""Worker runner and APScheduler queue poller (Phase 9).

Polls the durable `opportunities` table for listings in `ready_to_apply` status.
Because state is tracked durably in the database rather than an in-memory queue,
the Worker can restart at any time and resume cleanly without dropping tasks.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select
from sqlalchemy.orm import Session

from core.config import settings
from core.database import get_session
from core.models.opportunity import Opportunity
from core.status import ApplicationStatus, OpportunityStatus, is_human_approved
from worker.engine.filler import ApplicationFiller

logger = logging.getLogger(__name__)


class WorkerRunner:
    """Manages the background polling loop for the Browser Automation Worker."""

    def __init__(
        self,
        poll_interval: int | None = None,
        filler: ApplicationFiller | None = None,
    ) -> None:
        self.poll_interval = (
            poll_interval
            if poll_interval is not None
            else getattr(settings, "WORKER_POLL_INTERVAL", 20)
        )
        self.filler = filler or ApplicationFiller()
        self._scheduler: BlockingScheduler | BackgroundScheduler | None = None

    def poll_and_process_queue(self, session: Session, limit: int = 10) -> list[dict[str, Any]]:
        """Run a single processing cycle against pending ready_to_apply opportunities."""
        stmt = (
            select(Opportunity)
            .where(Opportunity.status == OpportunityStatus.READY_TO_APPLY.value)
            .order_by(Opportunity.id.asc())
            .limit(limit)
        )
        ready_opps = list(session.scalars(stmt).all())
        results = []

        if ready_opps:
            logger.info("Found %d opportunities ready to apply", len(ready_opps))

        for opp in ready_opps:
            try:
                res = self.filler.process_opportunity(session, opp.id)
                results.append(res)
            except Exception as exc:
                logger.exception("Error processing opportunity #%d: %s", opp.id, exc)
                results.append({
                    "opportunity_id": opp.id,
                    "status": "error",
                    "error": str(exc),
                })

        return results

    def claim_application_for_submission(
        self,
        session: Session,
        application_id: int,
        worker_id: str = "worker",
    ) -> bool:
        """Atomically claim an approved application in the database for browser submission.
        
        Guarantees that at most one worker process can claim an application.
        Uses a conditional UPDATE to ensure atomic exclusion across worker processes.
        """
        from core.models.opportunity import Application, Opportunity
        from core.status import ApplicationStatus, OpportunityStatus, is_human_approved
        from sqlalchemy import update
        from datetime import datetime, timezone
        import json

        app = session.get(Application, application_id)
        if not app or not app.notes:
            return False

        if app.status == ApplicationStatus.SUBMITTED.value:
            return False

        try:
            notes_data = json.loads(app.notes)
        except Exception:
            return False

        # Verify approval token
        if not is_human_approved(notes_data.get("approval_token")):
            return False

        # If already claimed, cannot claim
        if notes_data.get("submission_claimed"):
            return False

        opp = session.get(Opportunity, app.opportunity_id)
        if not opp or opp.status != OpportunityStatus.AWAITING_SUBMISSION.value:
            return False

        # Record claim
        now_iso = datetime.now(timezone.utc).isoformat()
        notes_data["submission_claimed"] = True
        notes_data["claimed_at"] = now_iso
        notes_data["claimed_by"] = worker_id
        claimed_notes = json.dumps(notes_data, default=str)

        # Atomic conditional update: update ONLY IF notes does not already have submission_claimed: true
        stmt = (
            update(Application)
            .where(
                Application.id == application_id,
                Application.status != ApplicationStatus.SUBMITTED.value,
                ~Application.notes.like('%"submission_claimed": true%'),
                ~Application.notes.like('%"submission_claimed":true%'),
            )
            .values(notes=claimed_notes)
        )
        result = session.execute(stmt)
        session.commit()

        # Refresh app instance
        session.refresh(app)
        return result.rowcount > 0

    def poll_and_submit_queue(self, session: Session, limit: int = 10, worker_id: str = "worker") -> list[dict[str, Any]]:
        """Run a single cycle to execute explicitly-approved browser submissions (U7)."""
        from core.models.opportunity import Application
        from core.status import is_human_approved
        import json

        stmt = (
            select(Opportunity)
            .where(Opportunity.status == OpportunityStatus.AWAITING_SUBMISSION.value)
            .order_by(Opportunity.id.asc())
            .limit(limit)
        )
        awaiting_opps = list(session.scalars(stmt).all())
        results = []

        if awaiting_opps:
            logger.info("Found %d opportunities awaiting submission", len(awaiting_opps))

        for opp in awaiting_opps:
            try:
                # Find the active application
                app = session.scalar(
                    select(Application)
                    .where(Application.opportunity_id == opp.id)
                    .order_by(Application.attempt_number.desc())
                )
                if not app or not app.notes:
                    continue

                if app.status == ApplicationStatus.SUBMITTED.value:
                    continue

                try:
                    notes_data = json.loads(app.notes)
                except Exception:
                    continue
                
                # Verify that the API recorded a valid human approval token for this browser-tier app
                approval_token = notes_data.get("approval_token")
                if not is_human_approved(approval_token):
                    continue

                # Skip if already claimed by another worker or in-progress submission
                if notes_data.get("submission_claimed"):
                    continue

                # Atomically claim candidate before starting browser submission
                claimed = self.claim_application_for_submission(session, app.id, worker_id=worker_id)
                if not claimed:
                    logger.info("Application #%d was already claimed by another worker; skipping.", app.id)
                    continue

                res = self.filler.execute_browser_submission(session, app.id, approval_token)
                results.append(res)
            except Exception as exc:
                logger.exception("Error submitting opportunity #%d: %s", opp.id, exc)
                results.append({
                    "opportunity_id": opp.id,
                    "status": "error",
                    "error": str(exc),
                })

        return results

    def run_once(self, limit: int = 10) -> list[dict[str, Any]]:
        """Execute a single queue scan cycle using a fresh DB session."""
        with get_session() as session:
            process_results = self.poll_and_process_queue(session, limit=limit)
            submit_results = self.poll_and_submit_queue(session, limit=limit)
            return process_results + submit_results

    def start_blocking(self) -> None:
        """Start the worker loop in the foreground (blocking scheduler)."""
        logger.info("Starting Worker Runner (foreground, every %ds)", self.poll_interval)
        sched = BlockingScheduler()
        sched.add_job(
            self.run_once,
            "interval",
            seconds=self.poll_interval,
            next_run_time=datetime.now(timezone.utc),
            id="worker_queue_poll",
            name="Worker Queue Poller",
        )
        self._scheduler = sched
        try:
            sched.start()
        except (KeyboardInterrupt, SystemExit):
            logger.info("Worker Runner stopped by user.")

    def start_background(self) -> BackgroundScheduler:
        """Start the worker loop in the background."""
        logger.info("Starting Worker Runner (background, every %ds)", self.poll_interval)
        sched = BackgroundScheduler()
        sched.add_job(
            self.run_once,
            "interval",
            seconds=self.poll_interval,
            id="worker_queue_poll",
            name="Worker Queue Poller",
        )
        sched.start()
        self._scheduler = sched
        return sched

    def stop(self) -> None:
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Worker Runner stopped.")
