"""FastAPI application entry-point.

Run with::

    uvicorn api.main:app --reload
"""

from fastapi import FastAPI

from api.routers import profiles, resumes

app = FastAPI(
    title="Job Application Agent — Career Intelligence Core",
    description=(
        "Phase 1 API: profile and resume management. "
        "Provides CRUD on named profiles (with education, skills, links) "
        "and versioned resume uploads with deterministic text extraction."
    ),
    version="0.1.0",
)

app.include_router(profiles.router)
app.include_router(resumes.router)


@app.get("/health", tags=["system"])
def health_check():
    """Simple liveness probe."""
    return {"status": "ok"}
