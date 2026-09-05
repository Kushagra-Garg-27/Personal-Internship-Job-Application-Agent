"""Resume upload and version management endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from api.deps import get_db
from core.repositories import profile_repo, resume_repo
from core.schemas.resume import ResumeResponse
from core.services import resume_service

router = APIRouter(prefix="/profiles/{profile_id}/resumes", tags=["resumes"])


def _ensure_profile_exists(profile_id: int, db: Session) -> None:
    if profile_repo.get_profile(db, profile_id) is None:
        raise HTTPException(status_code=404, detail="Profile not found.")


@router.post("/", response_model=ResumeResponse, status_code=status.HTTP_201_CREATED)
async def upload_resume(
    profile_id: int,
    file: UploadFile,
    db: Session = Depends(get_db),
):
    """Upload a new resume version (PDF or DOCX).

    The uploaded resume is automatically parsed and set as the active
    version.  If parsing fails (e.g. scanned-image PDF), the resume is
    stored with ``parse_status = "parse_failed"`` — it is **not** silently
    accepted with empty text.
    """
    _ensure_profile_exists(profile_id, db)

    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required.")

    suffix = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if suffix not in ("pdf", "docx"):
        raise HTTPException(
            status_code=400,
            detail="Only .pdf and .docx files are supported.",
        )

    content = await file.read()
    resume = resume_service.upload_resume(db, profile_id, file.filename, content)
    db.commit()
    db.refresh(resume)
    return resume


@router.get("/", response_model=list[ResumeResponse])
def list_resumes(profile_id: int, db: Session = Depends(get_db)):
    """List all resume versions for a profile (newest first)."""
    _ensure_profile_exists(profile_id, db)
    return resume_repo.list_resume_versions(db, profile_id)


@router.get("/active", response_model=ResumeResponse)
def get_active_resume(profile_id: int, db: Session = Depends(get_db)):
    """Get the currently active resume for a profile."""
    _ensure_profile_exists(profile_id, db)
    resume = resume_repo.get_active_resume(db, profile_id)
    if resume is None:
        raise HTTPException(status_code=404, detail="No active resume found.")
    return resume


@router.put("/{resume_id}/activate", response_model=ResumeResponse)
def activate_resume(
    profile_id: int,
    resume_id: int,
    db: Session = Depends(get_db),
):
    """Set a specific resume version as the active one."""
    _ensure_profile_exists(profile_id, db)
    resume = resume_repo.set_active_resume(db, profile_id, resume_id)
    if resume is None:
        raise HTTPException(
            status_code=404,
            detail="Resume not found or does not belong to this profile.",
        )
    db.commit()
    db.refresh(resume)
    return resume
