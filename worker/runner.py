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
from core.status import OpportunityStatus
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

    def run_once(self, limit: int = 10) -> list[dict[str, Any]]:
        """Execute a single queue scan cycle using a fresh DB session."""
        with get_session() as session:
            return self.poll_and_process_queue(session, limit=limit)

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
