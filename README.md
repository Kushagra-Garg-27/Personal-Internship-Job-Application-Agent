# Job Application Agent — Phase 1: Profile & Resume System

The **Career Intelligence Core** — a Python/FastAPI backend for managing user profiles and versioned resumes. This is Phase 1 of a larger Job/Internship Application Agent. No AI/LLM, no browser automation, no frontend yet.

## What's Included

| Layer | Description |
|---|---|
| **SQLAlchemy Models** | `profiles` (with child tables: `profile_education`, `profile_skills`, `profile_links`) and `resumes` |
| **Alembic Migrations** | Initial migration creating all 5 tables; ready for future schema growth |
| **Repository Layer** | `profile_repo` + `resume_repo` — clean data-access abstractions |
| **Service Layer** | `profile_service` + `resume_service` — business logic (nested creation, upload + parse workflow) |
| **Resume Parser** | Deterministic text extraction from PDF/DOCX via `pypdf`/`python-docx` with fail-closed logic |
| **FastAPI API** | CRUD endpoints for profiles, file upload for resumes, field-level queries |
| **Test Suite** | pytest — repo tests, parser tests, API integration tests |

## Quick Start

### 1. Create a virtual environment and install dependencies

```bash
cd job-app-agent
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate

pip install -e ".[dev]"
```

### 2. Generate test fixture files

```bash
python tests/generate_fixtures.py
```

This creates sample PDF/DOCX files in `tests/fixtures/` for the parser tests.

### 3. Run database migrations

```bash
alembic upgrade head
```

This creates `job_agent.db` in the project root with WAL mode enabled.

### 4. Start the API server

```bash
uvicorn api.main:app --reload
```

Visit **http://127.0.0.1:8000/docs** for the interactive Swagger UI.

### 5. Run tests

```bash
python -m pytest tests/ -v
```

## Project Structure

```
job-app-agent/
├── core/                   # Career Intelligence Core
│   ├── config.py           # Settings (DB URL, upload dir, parse threshold)
│   ├── database.py         # SQLAlchemy engine + WAL mode setup
│   ├── models/             # SQLAlchemy ORM models
│   ├── repositories/       # Data-access layer
│   ├── services/           # Business logic
│   ├── schemas/            # Pydantic request/response models
│   └── parsing/            # Resume text extraction
├── api/                    # FastAPI application
│   ├── main.py             # App entry point
│   ├── deps.py             # Dependency injection
│   └── routers/            # Endpoint definitions
├── tests/                  # pytest suite
│   ├── fixtures/           # Sample PDF/DOCX files
│   └── ...
├── alembic/                # Database migrations
├── alembic.ini
└── pyproject.toml
```

## Key Design Decisions

- **Multi-profile support**: Multiple named profiles (e.g. `"default"`, `"staging"`) coexist. Cheap to support now, expensive to retrofit later.
- **Normalized child tables**: Education, skills, and links are separate tables (not JSON blobs) — every field is independently queryable for Phase 4 eligibility filtering.
- **Versioned resumes**: Uploads are append-only; old versions are never destroyed. Exactly one resume per profile is marked as `is_active`.
- **Fail-closed parsing**: If extracted text is below 50 characters, the resume is stored with `parse_status = "parse_failed"` rather than silently accepting empty text.
- **WAL mode**: SQLite journal mode is set to WAL on every connection for better concurrent read performance.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/profiles/` | Create a new profile |
| `GET` | `/profiles/` | List all profiles |
| `GET` | `/profiles/{id}` | Get a profile (with children) |
| `PATCH` | `/profiles/{id}` | Update profile fields |
| `DELETE` | `/profiles/{id}` | Delete a profile |
| `GET` | `/profiles/{id}/field/{name}` | Query a single field |
| `POST` | `/profiles/{id}/resumes/` | Upload a resume (PDF/DOCX) |
| `GET` | `/profiles/{id}/resumes/` | List all resume versions |
| `GET` | `/profiles/{id}/resumes/active` | Get the active resume |
| `PUT` | `/profiles/{id}/resumes/{rid}/activate` | Set a resume as active |
| `GET` | `/health` | Liveness probe |
