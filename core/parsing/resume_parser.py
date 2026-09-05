"""Deterministic resume text extraction from PDF and DOCX files.

Fail-closed design: if extraction produces fewer than ``MIN_PARSE_CHARS``
characters, the result is flagged as ``parse_failed`` rather than silently
storing empty or near-empty text.
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.config import settings

logger = logging.getLogger(__name__)

# Recognised file extensions → handler mapping
_SUPPORTED_EXTENSIONS = {".pdf", ".docx"}


def extract_text(file_path: Path | str) -> tuple[str | None, str]:
    """Extract raw text from a resume file.

    Parameters
    ----------
    file_path:
        Absolute or relative path to a ``.pdf`` or ``.docx`` file.

    Returns
    -------
    tuple[str | None, str]
        ``(extracted_text, status)`` where *status* is one of:

        - ``"success"`` — extraction yielded meaningful text.
        - ``"parse_failed"`` — extraction failed or yielded too little text.
    """
    file_path = Path(file_path)

    if not file_path.exists():
        logger.error("Resume file not found: %s", file_path)
        return None, "parse_failed"

    suffix = file_path.suffix.lower()
    if suffix not in _SUPPORTED_EXTENSIONS:
        logger.warning("Unsupported file type: %s", suffix)
        return None, "parse_failed"

    try:
        if suffix == ".pdf":
            text = _extract_pdf(file_path)
        else:
            text = _extract_docx(file_path)
    except Exception:
        logger.exception("Failed to parse resume: %s", file_path)
        return None, "parse_failed"

    # ── Fail-closed threshold check ───────────────────────────────────
    if not text or len(text.strip()) < settings.MIN_PARSE_CHARS:
        logger.warning(
            "Extracted text below threshold (%d chars) for %s — marking parse_failed",
            len(text.strip()) if text else 0,
            file_path,
        )
        return None, "parse_failed"

    return text.strip(), "success"


# ── Private helpers ───────────────────────────────────────────────────────


def _extract_pdf(file_path: Path) -> str:
    """Extract text from all pages of a PDF using ``pypdf``."""
    from pypdf import PdfReader

    reader = PdfReader(str(file_path))
    pages_text: list[str] = []
    for page in reader.pages:
        page_text = page.extract_text()
        if page_text:
            pages_text.append(page_text)
    return "\n".join(pages_text)


def _extract_docx(file_path: Path) -> str:
    """Extract text from all paragraphs of a DOCX using ``python-docx``."""
    from docx import Document

    doc = Document(str(file_path))
    paragraphs: list[str] = []
    for para in doc.paragraphs:
        if para.text.strip():
            paragraphs.append(para.text)
    return "\n".join(paragraphs)
