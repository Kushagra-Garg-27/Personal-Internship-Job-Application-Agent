"""Worker runner and APScheduler queue poller (Phase 9 / M1).

Polls the durable `opportunities` table for listings in `ready_to_apply` status.
Because state is tracked durably in the database rather than an in-memory queue,
the Worker can restart at any time and resume cleanly without dropping tasks.

M1: Claim state is now stored in dedicated Application columns
(``approved_at``, ``submission_claimed_at``, ``claimed_by``) rather than the
``notes`` JSON blob.  Atomic claim uses a column-predicate UPDATE.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from core.config import settings
from core.database import get_session
from core.models.opportunity import Application, Opportunity
from core.status import ApplicationStatus, OpportunityStatus
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
        """Atomically claim an approved application for browser submission (M1).

        Uses a column-predicate UPDATE on the dedicated approval/claim columns
        rather than SQL LIKE matching against the notes JSON blob.  At most one
        worker process can win the claim race for a given application_id.

        Returns True if this caller won the claim; False otherwise.
        """
        app = session.get(Application, application_id)
        if app is None:
            return False

        # Pre-flight: fail-closed allowlist for claimable application statuses
        claimable_statuses = [
            ApplicationStatus.FORM_FILLED.value,
            ApplicationStatus.PENDING.value,
        ]
        if app.status not in claimable_statuses:
            return False
        if app.submission_claimed_at is not None:
            return False

        # Pre-flight: valid DB-based approval present?
        # approved_at is the durable approval record; the token is cleared after
        # confirm_and_submit (single-use). Check approved_at — not the token.
        if app.approved_at is None:
            return False

        # Pre-flight: skip actively revoked applications
        if app.approval_revoked_at is not None and (
            app.approved_at is None or app.approved_at <= app.approval_revoked_at
        ):
            return False

        opp = session.get(Opportunity, app.opportunity_id)
        if opp is None or opp.status != OpportunityStatus.AWAITING_SUBMISSION.value:
            return False

        # Use naive UTC for the SQL WHERE clause — SQLite stores naive datetimes
        # and SQLAlchemy's in-memory evaluator can't compare naive vs aware.
        now_naive = datetime.utcnow()

        # Atomic conditional UPDATE: wins only if submission_claimed_at is still NULL.
        # approved_at IS NOT NULL is the durable authorization signal (token may be
        # already consumed / cleared by confirm_and_submit).
        # Also ensures no active (un-superseded) revocation is present.
        # Fail-closed allowlist: only FORM_FILLED or PENDING applications can be claimed.
        stmt = (
            update(Application)
            .where(
                Application.id == application_id,
                Application.submission_claimed_at.is_(None),
                Application.approved_at.is_not(None),
                or_(
                    Application.approval_revoked_at.is_(None),
                    Application.approved_at > Application.approval_revoked_at,
                ),
                Application.status.in_(claimable_statuses),
            )
            .values(
                submission_claimed_at=now_naive,
                claimed_by=worker_id,
            )
        )
        result = session.execute(stmt)
        session.commit()
        session.refresh(app)
        return result.rowcount > 0


    def poll_and_submit_queue(self, session: Session, limit: int = 10, worker_id: str = "worker") -> list[dict[str, Any]]:
        """Run a single cycle to execute explicitly-approved browser submissions (U7 / M1)."""
        now = datetime.now(timezone.utc)
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
                # Find the most recent active application for this opportunity
                app = session.scalar(
                    select(Application)
                    .where(Application.opportunity_id == opp.id)
                    .order_by(Application.attempt_number.desc())
                )
                if app is None:
                    continue

                # Skip non-claimable / terminal applications
                if app.status not in (
                    ApplicationStatus.FORM_FILLED.value,
                    ApplicationStatus.PENDING.value,
                ):
                    continue

                # M1: Validate durable approval (approved_at is set by
                # confirm_and_submit after consuming the single-use token).
                # The token itself is cleared upon confirmation — do NOT
                # re-check it here.
                if app.approved_at is None:
                    continue  # Human has not confirmed yet

                # M5: Skip explicitly revoked approvals.
                # Belt-and-suspenders guard covering any edge case where the
                # revocation timestamp is set, while allowing newer re-approvals to proceed.
                if app.approval_revoked_at is not None and (
                    app.approved_at is None or app.approved_at <= app.approval_revoked_at
                ):
                    continue

                # Skip already-claimed
                if app.submission_claimed_at is not None:
                    continue

                # Atomically claim before starting browser submission
                claimed = self.claim_application_for_submission(session, app.id, worker_id=worker_id)
                if not claimed:
                    logger.info("Application #%d was already claimed by another worker; skipping.", app.id)
                    continue

                # Carry the exact persisted identity of the claim we just won
                # across the browser boundary; do not let the filler infer it
                # from a possibly stale identity map.
                session.refresh(app)
                res = self.filler.execute_browser_submission(
                    session,
                    app.id,
                    expected_claimed_by=worker_id,
                    expected_submission_claimed_at=app.submission_claimed_at,
                )
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
