# Job Application Agent — Career Intelligence Core

The **Career Intelligence Core** — a Python/FastAPI backend for managing user profiles, versioned resumes, opportunity tracking with a status machine, automated discovery from Greenhouse, Lever, RSS feeds, and Gmail job alerts, and a multi-stage evaluation funnel (deterministic eligibility filter + local embedding relevance scoring). Phases 1–4 of a larger Job/Internship Application Agent.

## What's Included

| Layer | Description |
|---|---|
| **SQLAlchemy Models** | `profiles` + children, `resumes`, `opportunities`, `status_history`, `applications`, `scoring_verdicts` (9 tables total) |
| **Alembic Migrations** | Three migrations (0001 + 0002 + 0003) in a chain; ready for future schema growth |
| **Repository Layer** | `profile_repo`, `resume_repo`, `opportunity_repo`, `status_history_repo`, `application_repo`, `scoring_repo` |
| **Service Layer** | Profile/resume services + opportunity transition engine + funnel orchestrator |
| **Resume Parser** | Deterministic text extraction from PDF/DOCX via `pypdf`/`python-docx` with fail-closed logic |
| **Status Machine** | 14-status lifecycle with enforced transition map, audit trail, and idempotency primitives |
| **Discovery Adapters** | Greenhouse, Lever (stable), RSS feeds (stable), Gmail job alerts (discovery_only) |
| **Multi-Stage Funnel** | Pluggable pipeline: deterministic eligibility filtering -> local `all-MiniLM-L6-v2` relevance scoring |
| **Scheduler** | APScheduler with independent per-source discovery jobs and periodic funnel batch evaluations |
| **FastAPI API** | CRUD endpoints + discovery status + status transitions + applications + scoring verdicts |
| **Test Suite** | 238 pytest tests — repos, parser, status transitions, adapters, pipeline, scheduler, eligibility, relevance, funnel, API |

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

## Phase 4 — Multi-Stage AI/LLM Funnel (Stages 1 & 3)

Phase 4 introduces the first two stages of the five-stage evaluation funnel described in §5.2. It enforces strict short-circuit execution: if an earlier stage rejects an opportunity, later stages (and computationally expensive embedding or LLM models) are **never executed**.

### The 5-Stage Funnel

```
Stage 1: Eligibility Filter (deterministic, no AI) ────► [Phase 4 — Built]
         │ (Pass)
         ▼
Stage 2: Scam/Risk Filter (deterministic) ─────────────► [Phase 5 — Slot reserved]
         │ (Pass)
         ▼
Stage 3: Relevance Scoring (local embeddings) ─────────► [Phase 4 — Built]
         │ (Score >= threshold)
         ▼
Stage 4: LLM Reasoning (Gemini free tier) ─────────────► [Later Phase — Ambiguous cases]
         │
         ▼
Stage 5: Human Review ─────────────────────────────────► [Phase 6 — Dashboard]
```

### Stage 1: Deterministic Eligibility Filter (`core/funnel/eligibility.py`)

Checks hard constraints without AI or API calls:
- **Application Deadline**: Verifies `opportunity.deadline_at` has not passed.
- **Salary Floor**: Ensures `opportunity.salary_max >= profile.salary_floor` (benefits given when unspecified).
- **Location & Remote Matching**: Enforces remote requirement if `remote_preference == "remote"` and matches preferred cities/tokens for on-site roles.
- **Degree Requirements**: Scans descriptions for explicit requirements (e.g. PhD, MBA, Master's, CS branch) and validates candidate holds matching credentials.

If eligibility fails, the opportunity transitions `discovered -> ineligible`, storing the specific rule and reason in `scoring_verdicts`.

### Stage 3: Local Semantic Relevance Scoring (`core/funnel/relevance.py`)

- **Model**: `all-MiniLM-L6-v2` via `sentence-transformers` (runs CPU-only, cached locally).
- **Zero API/cloud dependencies**: Runs completely offline.
- **Explainability**:
  - Computes candidate-to-job cosine similarity score (`0.0` to `1.0`).
  - Generates top matching candidate skills with similarity breakdown.
  - Identifies top matching description sentences.
- **State Transition**: Opportunities passing eligibility transition `discovered -> recommended`.

### Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/opportunities/{id}/verdict` | Fetch latest scoring verdict and explanation |
| `POST` | `/opportunities/{id}/evaluate` | Run evaluation funnel on an opportunity on demand |


