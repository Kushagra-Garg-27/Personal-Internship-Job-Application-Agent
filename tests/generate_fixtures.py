"""Generate test fixture files for resume parsing tests.

Run this script once to create:
- sample_resume.pdf   (a simple PDF with known text content)
- sample_resume.docx  (a simple DOCX with known text content)
- empty_scanned.pdf   (a PDF with no extractable text — simulates a scanned image)
"""

from pathlib import Path

FIXTURES_DIR = Path(__file__).parent / "fixtures"
FIXTURES_DIR.mkdir(exist_ok=True)

SAMPLE_TEXT = (
    "John Doe\n"
    "Software Engineer\n"
    "john.doe@example.com | (555) 123-4567\n"
    "\n"
    "Education\n"
    "B.Tech in Computer Science, Example University, 2024\n"
    "\n"
    "Skills\n"
    "Python, FastAPI, SQLAlchemy, PostgreSQL, Docker, Git\n"
    "\n"
    "Experience\n"
    "Software Engineering Intern at TechCorp (May 2023 - Aug 2023)\n"
    "- Built REST APIs serving 10k requests/day\n"
    "- Implemented CI/CD pipelines with GitHub Actions\n"
)


def _build_pdf_bytes(text_lines: list[str]) -> bytes:
    """Build a minimal valid PDF from raw operators.

    This constructs a PDF manually (no library needed for generation) so we
    don't have to install an extra dependency just for tests.
    """
    # Font: use built-in Helvetica
    font_obj = (
        "4 0 obj\n"
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\n"
        "endobj\n"
    )

    # Content stream with text operators
    stream_lines = ["BT", "/F1 12 Tf", "72 720 Td"]
    for line in text_lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream_lines.append(f"({escaped}) Tj")
        stream_lines.append("0 -16 Td")
    stream_lines.append("ET")
    stream_data = "\n".join(stream_lines)

    stream_obj = (
        f"5 0 obj\n"
        f"<< /Length {len(stream_data)} >>\n"
        f"stream\n"
        f"{stream_data}\n"
        f"endstream\n"
        f"endobj\n"
    )

    # Page object
    page_obj = (
        "3 0 obj\n"
        "<< /Type /Page /Parent 2 0 R "
        "/MediaBox [0 0 612 792] "
        "/Resources << /Font << /F1 4 0 R >> >> "
        "/Contents 5 0 R >>\n"
        "endobj\n"
    )

    # Pages object
    pages_obj = (
        "2 0 obj\n"
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>\n"
        "endobj\n"
    )

    # Catalog
    catalog_obj = (
        "1 0 obj\n"
        "<< /Type /Catalog /Pages 2 0 R >>\n"
        "endobj\n"
    )

    # Assemble PDF
    body_parts = [catalog_obj, pages_obj, page_obj, font_obj, stream_obj]

    pdf = "%PDF-1.4\n"
    offsets = []
    for part in body_parts:
        offsets.append(len(pdf))
        pdf += part

    xref_offset = len(pdf)
    pdf += "xref\n"
    pdf += f"0 {len(offsets) + 1}\n"
    pdf += "0000000000 65535 f \n"
    for off in offsets:
        pdf += f"{off:010d} 00000 n \n"

    pdf += "trailer\n"
    pdf += f"<< /Size {len(offsets) + 1} /Root 1 0 R >>\n"
    pdf += "startxref\n"
    pdf += f"{xref_offset}\n"
    pdf += "%%EOF\n"

    return pdf.encode("latin-1")


def generate_sample_pdf():
    lines = [l for l in SAMPLE_TEXT.strip().split("\n") if l.strip()]
    dest = FIXTURES_DIR / "sample_resume.pdf"
    dest.write_bytes(_build_pdf_bytes(lines))
    print(f"Created {dest}")


def generate_empty_pdf():
    """Create a PDF with no text content (blank page)."""
    pdf_content = (
        "%PDF-1.4\n"
        "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        "2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>\nendobj\n"
    )
    xref_offset = len(pdf_content)
    pdf_content += (
        "xref\n0 4\n"
        "0000000000 65535 f \n"
        "0000000009 00000 n \n"
        "0000000058 00000 n \n"
        "0000000115 00000 n \n"
        "trailer\n<< /Size 4 /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    )
    dest = FIXTURES_DIR / "empty_scanned.pdf"
    dest.write_bytes(pdf_content.encode("latin-1"))
    print(f"Created {dest}")


def generate_sample_docx():
    """Create a simple DOCX with known text content using python-docx."""
    from docx import Document

    doc = Document()
    for line in SAMPLE_TEXT.strip().split("\n"):
        doc.add_paragraph(line)

    dest = FIXTURES_DIR / "sample_resume.docx"
    doc.save(str(dest))
    print(f"Created {dest}")


if __name__ == "__main__":
    generate_sample_pdf()
    generate_sample_docx()
    generate_empty_pdf()
    print("All fixture files generated.")
