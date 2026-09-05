"""FastAPI application entry-point.

Run with::

    uvicorn api.main:app --reload
"""

from fastapi import FastAPI

from api.routers import applications, opportunities, profiles, resumes

app = FastAPI(
    title="Job Application Agent — Career Intelligence Core",
    description=(
        "Phase 1 & 2 API: profile/resume management and opportunity tracking "
        "with a status machine, audit trail, and application attempt records."
    ),
    version="0.2.0",
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
