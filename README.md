# Job Application Agent — Career Intelligence Core

The **Career Intelligence Core** — a Python/FastAPI backend for managing user profiles, versioned resumes, opportunity tracking with a status machine, and automated discovery from Greenhouse, Lever, RSS feeds, and Gmail job alerts. Phases 1–3 of a larger Job/Internship Application Agent. No AI/LLM, no browser automation, no frontend yet.

## What's Included

| Layer | Description |
|---|---|
| **SQLAlchemy Models** | `profiles` + children, `resumes`, `opportunities`, `status_history`, `applications` (8 tables total) |
| **Alembic Migrations** | Two migrations (0001 + 0002) in a chain; ready for future schema growth |
| **Repository Layer** | `profile_repo`, `resume_repo`, `opportunity_repo`, `status_history_repo`, `application_repo` |
| **Service Layer** | Profile/resume services + opportunity transition engine with allowed-transition map |
| **Resume Parser** | Deterministic text extraction from PDF/DOCX via `pypdf`/`python-docx` with fail-closed logic |
| **Status Machine** | 14-status lifecycle with enforced transition map, audit trail, and idempotency primitives |
| **Discovery Adapters** | Greenhouse, Lever (stable), RSS feeds (stable), Gmail job alerts (discovery_only) |
| **Scheduler** | APScheduler with independent per-source jobs, configurable intervals |
| **FastAPI API** | CRUD endpoints + discovery status + status transitions + applications |
| **Test Suite** | 190 pytest tests — repos, parser, status transitions, adapters, pipeline, scheduler, API |

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
│   ├── status.py           # OpportunityStatus enum + ALLOWED_TRANSITIONS
│   ├── models/             # SQLAlchemy ORM models
│   ├── repositories/       # Data-access layer
│   ├── services/           # Business logic + transition engine
│   ├── schemas/            # Pydantic request/response models
│   └── parsing/            # Resume text extraction
├── api/                    # FastAPI application
│   ├── main.py             # App entry point
│   ├── deps.py             # Dependency injection
│   └── routers/            # Endpoint definitions
├── tests/                  # pytest suite (111 tests)
│   ├── fixtures/           # Sample PDF/DOCX files
│   └── ...
├── alembic/                # Database migrations (0001, 0002)
├── alembic.ini
└── pyproject.toml
```

## Phase 1 — Profile & Resume System

### Key Design Decisions

- **Multi-profile support**: Multiple named profiles (e.g. `"default"`, `"staging"`) coexist.
- **Normalized child tables**: Education, skills, and links are separate tables — every field is independently queryable.
- **Versioned resumes**: Uploads are append-only; old versions are never destroyed. One resume per profile is `is_active`.
- **Fail-closed parsing**: If extracted text is below 50 characters → `parse_status = "parse_failed"`.
- **WAL mode**: SQLite journal mode is set to WAL on every connection.

### Profile & Resume Endpoints

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

## Phase 2 — Opportunity Tracking & Status Machine

### Status Lifecycle

Opportunities flow through a 14-status lifecycle. Every transition is validated against an allowed-transition map and logged to `status_history`:

```
discovered → recommended → ready_to_apply → applied → submitted → interview → offered → accepted
                                                                             ↘ rejected_by_recruiter
                        ↘ rejected_by_user                  ↘ withdrawn
           ↘ ineligible                       ↘ expired
           ↘ scam_risk_rejected
```

**⚠️ Status changes must ONLY go through `opportunity_service.transition_status()` — never via direct column writes.** The repo layer will raise an error if you try to update `status` directly.

### Adding a New Status

1. Add it to `OpportunityStatus` in `core/status.py`
2. Add transitions to/from it in `ALLOWED_TRANSITIONS`
3. No migration needed — status is stored as `String(30)`

### Key Design Decisions

- **`ready_to_apply` is the Worker queue**: No separate queue table. Querying `WHERE status = 'ready_to_apply'` is the handoff to Phase 9's Browser Worker.
- **Dedup by content hash**: `SHA-256(company|title|url)` with a unique constraint prevents duplicate listings.
- **Reliability tiers**: `stable` / `experimental` / `discovery_only` — set by the adapter that discovered the listing.
- **Multiple application attempts**: The `applications` table supports retries with `attempt_number`.
- **Pre-write primitive**: `mark_submission_attempted()` writes a pending Application record *before* any risky action — ready for Phase 9's fail-closed design.

### Opportunity & Application Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/opportunities/` | Create/upsert an opportunity |
| `GET` | `/opportunities/` | List with filters (status, tier, company) |
| `GET` | `/opportunities/{id}` | Get a single opportunity |
| `PATCH` | `/opportunities/{id}` | Update fields (NOT status) |
| `DELETE` | `/opportunities/{id}` | Delete an opportunity |
| `POST` | `/opportunities/{id}/transition` | Transition status |
| `GET` | `/opportunities/{id}/history` | Full status history |
| `POST` | `/opportunities/{id}/applications` | Create application attempt |
| `GET` | `/opportunities/{id}/applications` | List application attempts |
| `PATCH` | `/applications/{id}` | Update application status |
| `GET` | `/health` | Liveness probe |

## Phase 3 — Discovery Adapters

### Configured Sources

| Source | Tier | API | Polling Interval |
|---|---|---|---|
| **Greenhouse** | `stable` | `boards-api.greenhouse.io/v1/boards/{token}/jobs` | 6 hours |
| **Lever** | `stable` | `api.lever.co/v0/postings/{slug}` | 6 hours |
| **RSS/Atom** | `stable` | Any RSS/Atom feed URL | 2 hours |
| **Gmail Alerts** | `discovery_only` | Gmail API v1 `history.list` | 5 minutes |

### Adding Sources

All sources are configured via environment variables or `.env`:

```bash
# Greenhouse boards (JSON array)
GREENHOUSE_BOARDS='[{"token": "vaulttec", "company": "Vault-Tec"}, {"token": "airbnb", "company": "Airbnb"}]'

# Lever companies (JSON array)
LEVER_COMPANIES='[{"slug": "netflix", "company": "Netflix"}]'

# RSS feeds (JSON array)
RSS_FEEDS='[{"url": "https://company.com/careers/rss", "company": "Company"}]'

# Gmail (requires OAuth2 setup — run: python -m core.discovery.gmail_client)
GMAIL_CREDENTIALS_FILE=credentials.json
```

### Gmail OAuth2 Setup

1. Create a Google Cloud project, enable the Gmail API
2. Create OAuth2 Desktop App credentials → download `credentials.json`
3. Set `GMAIL_CREDENTIALS_FILE=credentials.json` in `.env`
4. Run `python -m core.discovery.gmail_client` (opens browser once for consent)

### Discovery Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/discovery/status` | Scheduler status + last-run info per source |

### Architecture

```
DiscoverySource (ABC)
├── GreenhouseSource  [stable]     → httpx → Greenhouse API
├── LeverSource       [stable]     → httpx → Lever API
├── RSSFeedSource     [stable]     → feedparser → RSS/Atom feeds
└── GmailAlertSource  [discovery_only] → Gmail API → rules-based parsing
                          │
                          ▼
                    pipeline.py
              (normalize + dedup + upsert)
                          │
                          ▼
           opportunity_service.upsert_opportunity()
                          │
                          ▼
              opportunities table (status=discovered)
```

Each source runs independently via APScheduler — one failing doesn't block the others.

