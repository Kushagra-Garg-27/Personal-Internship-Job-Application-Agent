# Job Application Agent — Career Intelligence Core

The **Career Intelligence Core** — a Python/FastAPI backend for managing user profiles, versioned resumes, opportunity tracking with a status machine, automated discovery from Greenhouse, Lever, RSS feeds, and Gmail job alerts, and a multi-stage evaluation funnel (deterministic eligibility filter + local embedding relevance scoring). Phases 1–4 of a larger Job/Internship Application Agent.

## What's Included

| Layer | Description |
|---|---|
| **SQLAlchemy Models** | `profiles` + children, `resumes`, `opportunities`, `status_history`, `applications`, `scoring_verdicts`, `scam_content_signatures` (10 tables total) |
| **Alembic Migrations** | Four migrations (0001 + 0002 + 0003 + 0004) in a chain; ready for future schema growth |
| **Repository Layer** | `profile_repo`, `resume_repo`, `opportunity_repo`, `status_history_repo`, `application_repo`, `scoring_repo`, `scam_signature_repo` |
| **Service Layer** | Profile/resume services + opportunity transition engine + funnel orchestrator + scam review service |
| **Resume Parser** | Deterministic text extraction from PDF/DOCX via `pypdf`/`python-docx` with fail-closed logic |
| **Status Machine** | 15-status lifecycle with enforced transition map, audit trail, and idempotency primitives |
| **Discovery Adapters** | Greenhouse, Lever (stable), RSS feeds (stable), Gmail job alerts (discovery_only) |
| **Multi-Stage Funnel** | 3-stage pipeline: deterministic eligibility -> deterministic scam/risk filter + Gemini for ambiguous -> local relevance |
| **Scheduler** | APScheduler with independent per-source discovery jobs and periodic funnel batch evaluations |
| **FastAPI API** | CRUD endpoints + discovery status + status transitions + applications + scoring verdicts + scam review |
| **Test Suite** | 283 pytest tests — repos, parser, status transitions, adapters, pipeline, scheduler, eligibility, relevance, scam rules, gemini client, review API, funnel |

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

## Phase 5 — Scam & Risk Filter Stage (Stage 2)

Phase 5 inserts Stage 2 of the 5-stage funnel (§5.2) directly between Stage 1 Eligibility and Stage 3 Relevance:
`FUNNEL_STAGES = [eligibility_stage, scam_risk_stage, relevance_stage]`

It adheres to strict economic and safety principles: deterministic rules first with zero AI cost, reserving the Gemini Developer API (free tier) strictly for the genuinely ambiguous minority (~10–20%). Ambiguous listings halt for mandatory human review—never auto-committing.

```
Stage 1: Eligibility Filter (deterministic) ────────► [Phase 4]
         │ (Pass)
         ▼
Stage 2: Scam/Risk Filter ──────────────────────────► [Phase 5]
         ├── Deterministic Rules:
         │   ├── Duplicate Content Hash (`scam_content_signatures`)
         │   ├── Keyword Blocklist (wire money, crypto transfer, upfront fees)
         │   ├── Free Email Recruiter Pattern (corporate brand impersonation)
         │   └── WHOIS Domain Age (< 30 days old flag)
         │
         ├── Outcomes:
         │   ├── Clear ─────────────► Proceeds to Stage 3 Relevance
         │   ├── Hard Reject ───────► SCAM_RISK_REJECTED + Hash stored in DB
         │   └── Ambiguous ─────────► Escalate to Gemini Free Tier (Stage 4)
         │                              │
         │                              ▼
         │                        Fail-Closed Halt: SCAM_REVIEW_PENDING
         │                        (Awaiting human approval/rejection)
         │
         ▼ (Pass)
Stage 3: Relevance Scoring (local embeddings) ──────► [Phase 4]
```

### Deterministic Rules (`core/funnel/scam_risk/rules.py`)

1. **Duplicate Content Signature Store**: Normalized SHA-256 hash comparison against confirmed scam signatures in `scam_content_signatures`. Matching confirmed signatures triggers instant rejection without AI.
2. **Keyword Blocklist**: Hard rejection on explicit financial or fraudulent demands (`wire money`, `western union`, `crypto transfer`, `pay upfront`, `buy equipment from our vendor`). Suspicious phrases trigger ambiguous classification for LLM analysis.
3. **Free Email Recruiter Impersonation**: Corporate brand names recruiting through free consumer domains (`@gmail.com`, `@yahoo.com`, etc.) trigger immediate rejection. Generic listings provide ambiguous signals.
4. **WHOIS Domain Age**: Newly registered domains (< 30 days old) are flagged as ambiguous. Established job platforms (LinkedIn, Greenhouse, Lever, etc.) are whitelisted and skip WHOIS lookups.

### Gemini Analysis Client (`core/funnel/scam_risk/gemini_client.py`)

- **Quota Enforcement**: Tracks daily usage against free tier limit (1500 calls/day) with automatic midnight UTC rollover.
- **Fail-Closed Guarantee**: If the daily quota is exhausted or an API error occurs, evaluation is deferred (`quota_deferred_at`), preserving the listing in `discovered` rather than silently passing or rejecting.
- **Human Approval Barrier**: LLM evaluations halt in `scam_review_pending`—never directly committing to `recommended` or `scam_risk_rejected`.

### Closed-Loop Learning Feedback Loop

When a human reviewer reviews an ambiguous listing and rejects it, its content hash is saved into `scam_content_signatures`. Subsequent postings with identical content (even from different posters or platforms) are automatically auto-rejected at Stage 2 with zero AI cost.

### Scam Review Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/opportunities/scam-review/pending` | List opportunities awaiting human scam/risk review |
| `POST` | `/opportunities/{id}/scam-review/approve` | Approve listing, evaluate Stage 3 relevance, and transition to `recommended` |
| `POST` | `/opportunities/{id}/scam-review/reject` | Reject listing, store body hash in `scam_content_signatures`, and transition to `scam_risk_rejected` |

## Phase 6 — Dashboard & Human Approval UI

Phase 6 delivers the human review interface and closes the **[HUMAN APPROVES]** gate from §2's system architecture diagram. Built as a high-fidelity React + Vite + TypeScript application in `frontend/`, it replaces raw API interactions with an editorial, dark-first intelligence dashboard.

### Two Distinct Human Review Actions

It is critical not to conflate the two review gates:

1. **Scam-Review Clear (Phase 5 Stage 2 Gate)**: A human reviews an ambiguous listing flagged by deterministic rules or the Gemini LLM. Clicking **Clear Listing** confirms it is not a scam and returns it to the funnel to proceed to Stage 3 Relevance Scoring.
2. **Final Application Approval (Phase 6 §2 Gate)**: A candidate reviews a fully scored `recommended` opportunity, selects a specific candidate resume version (defaulting to their active resume), and clicks **Confirm & Approve**. This transitions the opportunity from `recommended` to `ready_to_apply`—the exact state consumed by Phase 9 application workers.

### Architecture & Frontend Stack

- **Stack**: React 19 + Vite + TypeScript + Vanilla CSS Design Tokens (zero Tailwind, zero bulky generic component kits).
- **Aesthetic**: Deep charcoal/black surfaces (`#07070b`), subtle violet/indigo ambient glows, and glowing controlled orange (`#f97316`) reserved for high-value actions (**Approve**).
- **Typography**: Editorial serif (`Newsreader`) for large titles and match badges paired with `Inter` for crisp metadata and UI controls.
- **Reliability Tier Prominence**: Verified stable adapters (`Greenhouse`, `Lever`, `RSS`) vs unverified sources (`Gmail Job Alerts`) are displayed as first-class visual badges (`Stable Source` vs `Discovery Only`).
- **Components**:
  - `AppLayout`: Shell with system telemetry and navigation tabs (`Opportunities`, `Scam Review`, `Response Center`).
  - `OpportunityCard`: Large editorial card with reliability badge, match score pill, matched skill tags, match summary quote, and action bar.
  - `OpportunityDetailDrawer`: Slide-over drawer with full relevance breakdown, sentence-level cosine similarities, Stage 1 eligibility audit, Stage 2 scam audit, and complete job description.
  - `ApprovalModal`: Human approval dialog allowing the candidate to select which versioned resume to attach before transitioning to `ready_to_apply`.
  - `ScamReviewQueue`: Dedicated triage workspace showing deterministic rule triggers and Gemini LLM stated reasoning with distinct **Clear Listing** vs **Reject as Scam** actions.
  - `ResponseCenterPlaceholder`: Architecture roadmap placeholder for Phase 7 incoming recruiter communications.

### Backend Endpoints (Phase 6)

| Method | Path | Description |
|---|---|---|
| `POST` | `/opportunities/{id}/approve` | Transitions `recommended -> ready_to_apply` with candidate `selected_resume_id` |
| `POST` | `/opportunities/{id}/dismiss` | Transitions opportunity to terminal `dismissed` status |
| `GET` | `/opportunities/dashboard-feed` | Consolidated feed query returning opportunities, relevance verdicts, and profile resumes |

## Phase 7 — Recruiter Response Tracking & Intelligence

Phase 7 watches the inbox for recruiter replies to submitted applications, classifies them with a rules-first + LLM-escalation pipeline, links each message to its originating application, and provides proactive integration health monitoring.

```
Incoming Gmail Message
         │
         ▼
Candidate Filter (Fast Rejection)
  ├── Exclude self-sent messages
  ├── Exclude no-reply / system automated addresses
  └── Require job-related keyword or active employer domain match
         │
         ▼ (Pass)
Two-Stage Classification Pipeline
  ├── Stage 1: Deterministic Regex Rules (interview, rejection, offer, screening, follow-up, generic)
  │     └── Confidence ≥ 0.8 ──► Verdict Final (Zero AI Cost)
  └── Stage 2: Gemini LLM Escalation (Ambiguous Cases Only)
        └── Fail-Closed: Quota exhaustion or API error ──► `unclassified`
         │
         ▼
Application Linker
  ├── 1. Thread ID Inheritance (Existing thread match)
  ├── 2. Sender Domain Matching (Against active applications)
  ├── 3. Subject / Body Cross-Referencing (Company name & title)
  └── Fail-Closed: Multiple matches or zero matches ──► Refuses to guess (remains unlinked)
         │
         ▼
Response Center UI & Read-Only API
```

### Key Principles

1. **Rules First, LLM for Nuance**: Just like Phase 5's scam/risk engine, high-confidence deterministic patterns resolve instantly with zero AI cost. Only genuinely ambiguous emails invoke Gemini.
2. **Fail-Closed Application Linking**: Messages only link to an application when evidence is unambiguous. If multiple applications match a domain or company name, the linker refuses to guess and stores the message unlinked for candidate review.
3. **Proactive Integration Health**: OAuth token expiration or poller failures are recorded in `integration_health_events` and surfaced prominently in the dashboard header, preventing silent monitoring failures.
4. **Purely Informational**: Phase 7 does not send automated replies or unilaterally mutate application statuses; it presents organized intelligence in the Response Center for the candidate.

### API Endpoints (Phase 7)

| Method | Path | Description |
|---|---|---|
| `GET` | `/messages` | List recruiter messages with filters (`classification`, `application_id`, `linked`, `limit`, `offset`) |
| `GET` | `/messages/{message_id}` | Get single message with enriched opportunity details |
| `GET` | `/messages/stats` | Aggregated response counts by classification and linking status |
| `GET` | `/integration-health` | List poller health events and token failure diagnostics |

## Phase 8 — Notification Layer (MVP Complete)

Per §5.5 and §7 of the system architecture, Phase 8 delivers the alerting and notification layer, completing the full, free, genuinely useful MVP.

```
NotificationService
 ├── TelegramProvider   (Primary, required default, 100% free, no verification)
 └── WhatsAppProvider   (Secondary, optional mirror, feature-flagged)
```

### Provider Architecture & Fail-Closed Behavior

1. **Telegram as the Non-Negotiable Default**:
   - Built on the Telegram Bot API — free, verified by zero third-party gatekeepers, with no message-category restrictions.
   - Operates with automatic retries, exponential backoff, and loud critical logging on permanent failure.
2. **WhatsApp as an Optional Mirror**:
   - Behind a feature flag (`WHATSAPP_ENABLED=false` by default).
   - Accounts for Meta's post-October 2026 per-message billing.
   - **Fail-Closed Fallback (§6)**: If WhatsApp encounters an error (auth expired, unpaid billing, rate limit), **Telegram delivery continues uninterrupted**, and an additional `"WhatsApp channel degraded"` warning is dispatched to Telegram.
   - **WhatsApp is Never Load-Bearing**: The entire system runs cleanly whether WhatsApp is configured or not.
3. **Event-Type Filtering (Permissive by Default)**:
   - Configurable per-event notification toggles via dashboard UI or environment.
   - Defaults: `interview_invite` (ON), `offer` (ON), `screening_question` (ON), `follow_up` (ON), `rejection` (ON), `unclassified` (ON), `integration_unhealthy` (ON), `quota_exhausted` (ON), `generic` (OFF).
4. **Real Event Wiring**:
   - **Phase 7 Recruiter Replies**: Instant push alerts on incoming classified recruiter messages.
   - **Phase 7 Gmail Health**: Instant alerts when Gmail OAuth tokens expire or refresh fails.
   - **Phase 5 Gemini Quota**: Alerts when opportunities are deferred due to daily quota exhaustion.
5. **Delivery Audit Log**:
   - Durable record of every notification attempt, channel, status (`delivered`, `failed`, `skipped`), retry counts, and error details.

### Provider Setup Guide

#### 1. Telegram Bot (Required Default)
1. Open Telegram and message [@BotFather](https://t.me/BotFather) with `/newbot`.
2. Follow prompts to name your bot and receive your **HTTP API Bot Token**.
3. Start a chat with your new bot (or add it to a personal group) and send `/start`.
4. Retrieve your numeric **Chat ID** (e.g. by messaging [@userinfobot](https://t.me/userinfobot)).
5. Add to your `.env` file:
   ```env
   TELEGRAM_BOT_TOKEN="123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ"
   TELEGRAM_CHAT_ID="987654321"
   ```

#### 2. WhatsApp Cloud API (Optional Mirror)
1. Register at [developers.facebook.com](https://developers.facebook.com) and create a Business App with WhatsApp product.
2. Obtain your **Phone Number ID**, **Recipient Phone Number**, and **User Access Token**.
3. Add to your `.env` file:
   ```env
   WHATSAPP_ENABLED=true
   WHATSAPP_PHONE_NUMBER_ID="your-phone-number-id"
   WHATSAPP_RECIPIENT_PHONE="+1234567890"
   WHATSAPP_ACCESS_TOKEN="your-access-token"
   ```

### API Endpoints (Phase 8)

| Method | Path | Description |
|---|---|---|
| `GET` | `/notifications/settings` | Get current event filters, WhatsApp toggle, and provider status (zero secrets exposed) |
| `PATCH` | `/notifications/settings` | Update enabled event types and toggle WhatsApp mirroring on/off |
| `GET` | `/notifications/logs` | Query delivery audit logs with filters (`channel`, `status`, `event_type`) |
| `POST` | `/notifications/test` | Trigger an immediate test notification to verify channel connectivity |

### Running the Application

1. **Seed Demo Data**:
   Populate realistic scored opportunities, candidate resumes, and recruiter responses:
   ```bash
   .venv\Scripts\python scripts/seed_dashboard.py
   .venv\Scripts\python scripts/seed_messages.py
   ```

2. **Start the FastAPI Backend (Career Intelligence Core)**:
   ```bash
   .venv\Scripts\activate  # Windows (or source .venv/bin/activate on Unix)
   uvicorn api.main:app --reload --port 8000
   ```

3. **Start the Browser Automation Worker (Separate Process)**:
   ```bash
   # Run the durable background queue poller
   .venv\Scripts\python -m worker.main --interval 20

   # Or run a single queue pass
   .venv\Scripts\python -m worker.main --once
   ```

4. **Start the Vite Frontend**:
   ```bash
   cd frontend
   npm run dev
   ```
   Open **http://localhost:5173** to access the Dashboard:
   - **Opportunities**: Scoring and human approval queue
   - **Scam Review**: AI/Deterministic scam triage
   - **Response Center**: Inbound recruiter communication tracking
   - **Notifications**: Channel status, event filter toggles, test alert, and audit logs

5. **Running the Full Test Suite**:
   ```bash
   # Backend and Worker tests (431 passing across Phases 1–9)
   .venv\Scripts\pytest -v

   # Frontend type check
   cd frontend && npx tsc --noEmit
   ```

## Phase 9 — Application Automation (Browser Automation Worker)

Per §2, §5.3, §5.6, and §7 of the system architecture, Phase 9 builds the **Browser Automation Worker**—the second of two separate processes.

```
[Core Process]
Opportunities Table (ready_to_apply) ──► Worker Poller (worker/runner.py)
                                             │
                                             ▼
                                     Adapter Resolution
                        ┌────────────────────┴────────────────────┐
                        ▼                                         ▼
            Stable Tier (HTTP API)                  Experimental Tier (Playwright)
            • Greenhouse / Lever                    • Internshala / Unstop
            • No browser needed                     • Encrypted storage_state at rest
            • Prepares pending review draft         • Fills form in visible browser
                        │                                         │
                        ▼                                         ▼
          [HUMAN CONFIRMS VIA API/UI]                 [HUMAN CLICKS SUBMIT IN BROWSER]
                        │                                         │
                        └────────────────────┬────────────────────┘
                                             ▼
                                  Status -> `applied`
                                  Applications Record Created
                                             │
                        ┌────────────────────┴────────────────────┐
                        ▼                                         ▼
             Core-side Watcher Job                    Ambiguous Timeout Handling
             • Polls DB for status changes            • Status check BEFORE any retry
             • Fires Telegram/WhatsApp alert          • Fail closed on uncertainty
             • Zero worker credential exposure        • Never blindly retry
```

### Architectural Boundaries & Security

1. **Two Distinct Processes**:
   - **Career Intelligence Core (`core/`, `api/`)**: Never imports Playwright. Houses discovery, funnel, messaging, and notification credentials.
   - **Browser Automation Worker (`worker/`)**: The ONLY component that imports Playwright. Deployed and scheduled independently.
2. **Strict Credential Blast-Radius Isolation**:
   - The Worker holds active browser platform sessions, but **never** touches email credentials or notification tokens (`NotificationService`).
   - Communication happens strictly via the shared SQLite database: Worker transitions opportunity status (`awaiting_submission` or `manual_application_required`), and a Core-side watcher job inside the Core process detects these transitions and fires Telegram/WhatsApp alerts.
3. **The Non-Negotiable Human-Submit Gate**:
   - **System Invariant: Never autonomously submits; explicit human authorization is required.**
   - **Experimental tier (Internshala, Unstop)**: Playwright fills the form, uploads the pinned resume, inserts AI-drafted answers, and **leaves the real browser window open** at the completed, unsubmitted form. The human candidate reviews the form in that window and clicks Submit themselves. The Worker never clicks submit in the browser. The candidate then clicks "Confirm Submitted" on the dashboard (or calls `POST /applications/{id}/confirm-submit`), which updates and confirms the `applied` status in the database.
   - **Stable tier (Greenhouse, Lever)**: Assembles the HTTP submission payload into a pending reviewable draft. The actual submission call only fires after an explicit confirm action (`POST /applications/{id}/confirm-submit`), never automatically or autonomously once filling completes.
4. **AI-Drafted Custom Questions**:
   - Free-text application questions ("Why do you want to work here?", etc.) are drafted with Gemini, reusing Phase 5's client & quota budget.
   - Hard architectural rule: every generated answer is prefixed with `[AI DRAFT - PENDING APPROVAL]` and requires explicit human review and approval before use.
5. **Storage-State Security at Rest**:
   - Interactive session states (`storage_state`) are encrypted at rest using Fernet (AES-128-CBC + HMAC-SHA256) at `worker/storage/{platform}_storage_state.enc`.
   - Raw session files, cookies, and keys are strictly excluded from git.
6. **Fail-Closed Duplicate Prevention (§5.7)**:
   - Uses Phase 2's `mark_submission_attempted` primitive to write a durable DB record before any risky action.
   - An ambiguous post-submit timeout triggers an automated platform status check (`adapter.check_status()`) **before** any retry is considered. Retries are strictly forbidden for non-idempotent submit actions.
7. **Recurring Fixture Maintenance**:
   - Experimental platform adapters depend on DOM selectors that change when platforms redesign their frontend.
   - Integration tests run against anonymized HTML snapshots in `tests/worker/fixtures/`. Refreshing these fixtures when platform redesigns occur is documented in `tests/worker/fixtures/README.md`.

### Worker Commands & Session Setup

#### 1. Interactive Session Setup (One-Time Login)
To capture and encrypt your active session for experimental platforms:
```bash
# Internshala
.venv\Scripts\python -m worker.setup_session --platform internshala

# Unstop
.venv\Scripts\python -m worker.setup_session --platform unstop
```
This opens a visible browser window, allows you to log in manually, captures session state upon pressing `[ENTER]`, and encrypts it at rest without storing passwords.

#### 2. Running the Worker
```bash
# Foreground daemon polling every 20 seconds
.venv\Scripts\python -m worker.main --interval 20

# Single queue pass (ideal for cron or test verification)
.venv\Scripts\python -m worker.main --once
```

### API Endpoints (Phase 9)

| Method | Path | Description |
|---|---|---|
| `POST` | `/opportunities/{id}/applications` | Create application attempt (`mark_submission_attempted`) |
| `PATCH` | `/applications/{id}` | Update application status, confirmation ref, or notes |
| `POST` | `/applications/{id}/confirm-submit` | Human confirmation action to execute pending draft submission for stable HTTP adapters |

## Phase 10 — Recruiter Response Loop

Phase 10 closes the recruiter interaction loop per §5.4 and §7: `Classify → notify → suggested reply → you send`, ending with `Status update → back into Application DB`.

```
Recruiter Email ──► Classify & Link (Phase 7)
                         │
                         ▼
        Notify Candidate (Phase 8: Telegram/WhatsApp)
                         │
                         ▼
       Gemini Suggested Reply Drafting (reusing Phase 5 quota)
                         │
                         ▼
            [YOU REVIEW / EDIT / APPROVE]  <── Mandatory Human Gate
                         │
        ┌────────────────┴────────────────┐
        ▼                                 ▼
Reply-Bearing Category           Non-Reply Category (Rejection)
  • Editable Reply                 • Direct "Acknowledge" action
  • "Approve & create draft"       • No email reply drafted
        │                                 │
        ▼                                 ▼
Create Gmail Draft (Drafts only)         —
        │                                 │
        └────────────────┬────────────────┘
                         ▼
        Automatic Status Update via Status Machine
        • interview_scheduled / offer_received / rejected_by_recruiter
        • Atomic write logged in status_history
```

### The Hard Invariant: "Any temptation to let it auto-send — resist it."

1. **Restricted Gmail OAuth Permissions**:
   The app requests **strictly** the following OAuth scopes:
   - `https://www.googleapis.com/auth/gmail.readonly` (reading alerts & recruiter replies)
   - `https://www.googleapis.com/auth/gmail.compose` (creating drafts in Drafts folder)

   **Deliberately excluded**:
   - `https://www.googleapis.com/auth/gmail.send` is **never requested**.
   - `https://mail.google.com/` (full access) is **never requested**.

   This turns "you send" from an application-level promise into a permission-level guarantee: even a severe bug in our code cannot send an email on your behalf, because the credentials held by the application lack the structural capability to send messages.

2. **The Review Action — Two Shapes, One Principle**:
   - **Reply-bearing categories** (`interview_invite`, `screening_question`, `offer`, `follow_up`): Displays the Gemini-drafted reply (prefixed with `[AI SUGGESTED REPLY - EDIT BEFORE SENDING]`) in an editable text area. Clicking **Approve & create draft** creates a draft in your Gmail account via `service.users().drafts().create(...)`. You physically open Gmail and click Send.
   - **Non-reply categories** (`rejection`, `generic`): Displays an **Acknowledge** button. A plain rejection does not draft a reply, but requires explicit candidate confirmation before transitioning the application to `rejected_by_recruiter`.

3. **Phase 7 Non-Goal Invariant Preserved**:
   - A newly received or classified message, on its own with no human action taken, **never** changes an application's status.
   - Status transitions happen exclusively upon clicking **Approve & create draft** or **Acknowledge**.

### Backend Endpoints (Phase 10)

| Method | Path | Description |
|---|---|---|
| `POST` | `/messages/{id}/draft-reply` | Generate or refresh an AI-suggested reply draft with Gemini |
| `POST` | `/messages/{id}/approve-reply` | Approve reply (with optional human edits), create Gmail draft, and transition application to `interview_scheduled` or `offer_received` |
| `POST` | `/messages/{id}/acknowledge` | Acknowledge a non-reply message (e.g. rejection) and transition application to `rejected_by_recruiter` |





