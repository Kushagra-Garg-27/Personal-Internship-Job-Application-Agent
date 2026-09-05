"""Business logic for resume upload, parsing, and version management."""

from __future__ import annotations

import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from core.config import settings
from core.models.resume import Resume
from core.parsing.resume_parser import extract_text
from core.repositories import resume_repo


def upload_resume(
    session: Session,
    profile_id: int,
    filename: str,
    file_content: bytes,
) -> Resume:
    """Save a resume file to disk, parse its text, and create a versioned DB record.

    The newly uploaded resume is automatically set as the active resume for
    the profile.

    Parameters
    ----------
    session:
        Active SQLAlchemy session (caller manages commit/rollback).
    profile_id:
        The profile to attach this resume to.
    filename:
        Original filename from the upload (e.g. ``"resume_v3.pdf"``).
    file_content:
        Raw bytes of the uploaded file.

    Returns
    -------
    Resume
        The newly created resume record.
    """
    version = resume_repo.get_next_version(session, profile_id)

    # ── Persist file to disk ──────────────────────────────────────────
    upload_dir = settings.UPLOAD_DIR / str(profile_id)
    upload_dir.mkdir(parents=True, exist_ok=True)

    safe_filename = f"v{version}_{filename}"
    dest = upload_dir / safe_filename
    dest.write_bytes(file_content)

    # ── Parse text ────────────────────────────────────────────────────
    parsed_text, parse_status = extract_text(dest)

    # ── Deactivate previous active resume and create new record ───────
    resume = resume_repo.create_resume(
        session,
        profile_id=profile_id,
        version=version,
        file_path=str(dest),
        original_filename=filename,
        file_size_bytes=len(file_content),
        parsed_text=parsed_text,
        parse_status=parse_status,
        is_active=False,  # set_active_resume handles the swap
    )

    resume_repo.set_active_resume(session, profile_id, resume.id)
    return resume
