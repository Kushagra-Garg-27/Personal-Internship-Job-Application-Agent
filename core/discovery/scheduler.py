"""APScheduler setup for periodic discovery polling.

Each source gets its own independent job — one source failing (e.g.,
Gmail token expired) does not block the others from running.  Uses
``BackgroundScheduler`` (sync, thread-based) since the discovery
adapters are all synchronous HTTP calls.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler

from core.config import settings
from core.discovery.pipeline import run_discovery

logger = logging.getLogger(__name__)

# Module-level scheduler instance
_scheduler: BackgroundScheduler | None = None
_last_results: dict[str, dict] = {}


def _run_source_job(source_factory, source_name: str):
    """Wrapper that catches all exceptions so the scheduler stays alive."""
    try:
        source = source_factory()
        result = run_discovery(source)
        _last_results[source_name] = {
            "last_run": datetime.now(timezone.utc).isoformat(),
            "total_fetched": result.total_fetched,
            "new": result.new,
            "updated": result.updated,
            "errors": result.errors,
            "status": "ok" if result.errors == 0 else "partial_error",
        }
    except Exception:
        logger.exception("Scheduler job [%s] failed", source_name)
        _last_results[source_name] = {
            "last_run": datetime.now(timezone.utc).isoformat(),
            "status": "error",
        }


def _run_funnel_job() -> None:
    """Run funnel evaluation on newly discovered opportunities."""
    try:
        from core.database import get_session
        from core.funnel.runner import run_funnel_batch

        with get_session() as session:
            results = run_funnel_batch(session, limit=50)
            session.commit()
            _last_results["funnel"] = {
                "last_run": datetime.now(timezone.utc).isoformat(),
                "processed": len(results),
                "status": "ok",
            }
    except Exception:
        logger.exception("Scheduler job [funnel] failed")
        _last_results["funnel"] = {
            "last_run": datetime.now(timezone.utc).isoformat(),
            "status": "error",
        }


def start_scheduler() -> BackgroundScheduler:
    """Create, configure, and start the discovery scheduler.

    Registers a job for each configured source type.  Sources with
    empty configuration are skipped (no jobs registered).
    """
    global _scheduler

    if _scheduler is not None and _scheduler.running:
        logger.warning("Scheduler already running")
        return _scheduler

    _scheduler = BackgroundScheduler(
        job_defaults={"coalesce": True, "max_instances": 1}
    )

    # ── Greenhouse (stable) ───────────────────────────────────────────
    if settings.GREENHOUSE_BOARDS:
        from core.discovery.sources.greenhouse import GreenhouseSource

        _scheduler.add_job(
            _run_source_job,
            "interval",
            seconds=settings.GREENHOUSE_POLL_INTERVAL,
            args=[GreenhouseSource, "greenhouse"],
            id="discovery_greenhouse",
            name="Greenhouse Discovery",
        )
        logger.info(
            "Scheduled Greenhouse discovery (every %ds, %d boards)",
            settings.GREENHOUSE_POLL_INTERVAL, len(settings.GREENHOUSE_BOARDS),
        )

    # ── Lever (stable) ────────────────────────────────────────────────
    if settings.LEVER_COMPANIES:
        from core.discovery.sources.lever import LeverSource

        _scheduler.add_job(
            _run_source_job,
            "interval",
            seconds=settings.LEVER_POLL_INTERVAL,
            args=[LeverSource, "lever"],
            id="discovery_lever",
            name="Lever Discovery",
        )
        logger.info(
            "Scheduled Lever discovery (every %ds, %d companies)",
            settings.LEVER_POLL_INTERVAL, len(settings.LEVER_COMPANIES),
        )

    # ── RSS feeds (stable) ────────────────────────────────────────────
    if settings.RSS_FEEDS:
        from core.discovery.sources.rss_feed import RSSFeedSource

        _scheduler.add_job(
            _run_source_job,
            "interval",
            seconds=settings.RSS_POLL_INTERVAL,
            args=[RSSFeedSource, "rss"],
            id="discovery_rss",
            name="RSS Feed Discovery",
        )
        logger.info(
            "Scheduled RSS discovery (every %ds, %d feeds)",
            settings.RSS_POLL_INTERVAL, len(settings.RSS_FEEDS),
        )

    # ── Gmail alerts (discovery_only) ─────────────────────────────────
    if settings.GMAIL_CREDENTIALS_FILE is not None:
        from core.discovery.sources.gmail_alerts import GmailAlertSource

        _scheduler.add_job(
            _run_source_job,
            "interval",
            seconds=settings.GMAIL_POLL_INTERVAL,
            args=[GmailAlertSource, "gmail_alert"],
            id="discovery_gmail",
            name="Gmail Alert Discovery",
        )
        logger.info(
            "Scheduled Gmail alert discovery (every %ds)",
            settings.GMAIL_POLL_INTERVAL,
        )

    # ── Funnel evaluation (Phase 4) ───────────────────────────────────
    if getattr(settings, "FUNNEL_EVALUATOR_ENABLED", False) is True:
        raw_interval = getattr(settings, "FUNNEL_EVALUATOR_INTERVAL", 300)
        interval = raw_interval if isinstance(raw_interval, (int, float)) else 300
        _scheduler.add_job(
            _run_funnel_job,
            "interval",
            seconds=interval,
            id="funnel_evaluator",
            name="Funnel Evaluation",
        )
        logger.info("Scheduled Funnel evaluation (every %ds)", interval)

    _scheduler.start()
    logger.info("Discovery scheduler started")
    return _scheduler


def stop_scheduler():
    """Shut down the scheduler gracefully."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Discovery scheduler stopped")
    _scheduler = None


def get_scheduler_status() -> dict:
    """Return the current scheduler status and last-run info for each source."""
    return {
        "running": _scheduler is not None and _scheduler.running,
        "sources": dict(_last_results),
        "registered_jobs": (
            [j.id for j in _scheduler.get_jobs()]
            if _scheduler and _scheduler.running
            else []
        ),
    }
