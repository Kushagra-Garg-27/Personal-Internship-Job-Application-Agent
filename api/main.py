"""FastAPI application entry-point.

Run with::

    uvicorn api.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.routers import applications, opportunities, profiles, resumes
from core.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start/stop the discovery scheduler with the app lifecycle."""
    if settings.SCHEDULER_ENABLED:
        from core.discovery.scheduler import start_scheduler, stop_scheduler

        try:
            start_scheduler()
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "Discovery scheduler failed to start (likely no sources configured)"
            )
    yield
    if settings.SCHEDULER_ENABLED:
        from core.discovery.scheduler import stop_scheduler

        stop_scheduler()


app = FastAPI(
    title="Job Application Agent — Career Intelligence Core",
    description=(
        "Phases 1–4 API: profile/resume management, opportunity tracking "
        "with status machine, automated discovery (Greenhouse, Lever, RSS, Gmail), "
        "and multi-stage AI/LLM evaluation funnel (eligibility filter + relevance scoring)."
    ),
    version="0.4.0",
    lifespan=lifespan,
)

# Phase 1
app.include_router(profiles.router)
app.include_router(resumes.router)

# Phase 2
app.include_router(opportunities.router)
app.include_router(applications.router)


@app.get("/health", tags=["system"])
def health_check():
    """Simple liveness probe."""
    return {"status": "ok"}


@app.get("/discovery/status", tags=["discovery"])
def discovery_status():
    """Current scheduler status and last-run info for each discovery source."""
    from core.discovery.scheduler import get_scheduler_status

    return get_scheduler_status()
