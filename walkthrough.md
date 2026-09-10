# Walkthrough: SQLite Concurrency Hardening

## Overview
Hardened SQLite connection configuration across Core and Worker processes to eliminate immediate write lock failures (`OperationalError: database is locked`) under concurrent write contention.

---

## Changes Implemented

### 1. SQLite Connection Pragma Configuration
- **[`core/database.py`](file:///c:/Users/kusha/OneDrive/Desktop/AI-AGent/job-app-agent/core/database.py)**:
  - Updated the SQLAlchemy `connect` event listener (`_set_sqlite_pragmas`) to execute `PRAGMA busy_timeout = 5000`.
  - Under SQLite's default behavior (timeout = 0), a transaction attempting to write while another process holds a write lock fails immediately.
  - With `busy_timeout = 5000`, concurrent transactions automatically wait up to 5,000 ms to acquire the lock and complete cleanly.
  - Because `worker/runner.py` uses `core.database.get_session`, the same engine and pragma listener globally govern both Core (FastAPI) and Worker (Playwright) connections.

### 2. Test Engine Pragma Parity
- **[`tests/conftest.py`](file:///c:/Users/kusha/OneDrive/Desktop/AI-AGent/job-app-agent/tests/conftest.py)**:
  - Updated the test engine fixture to also execute `PRAGMA busy_timeout = 5000`, ensuring consistent behavioral parity during testing.

### 3. Concurrency Stress Test Suite
- **[`tests/test_concurrency.py`](file:///c:/Users/kusha/OneDrive/Desktop/AI-AGent/job-app-agent/tests/test_concurrency.py)**:
  - `test_production_engine_sets_busy_timeout`: Asserts that `PRAGMA busy_timeout` scalar returns `5000` on the production engine.
  - `test_concurrent_writers_with_busy_timeout`: Uses two concurrent worker threads synchronised via a `threading.Barrier` hitting an on-disk WAL-mode SQLite database simultaneously with 50 writes each (100 total). Confirms all 100 rows land successfully without errors.
  - `test_zero_busy_timeout_causes_lock_errors`: Control test running with `timeout=0` confirming that concurrent writes produce `OperationalError: database is locked` without the pragma.

### 4. Architecture Documentation
- **[`ARCHITECTURE.md`](file:///c:/Users/kusha/OneDrive/Desktop/AI-AGent/job-app-agent/ARCHITECTURE.md)**:
  - Updated Section 2.1 (Database & Storage) to record `busy_timeout = 5000ms`.
  - Updated Section 9.4 (Deduplication & Concurrency Locks) detailing the concurrency hardening mechanism.
  - Updated test counts to 452 backend tests.

---

## Verification Results

### Automated Tests
1. **Concurrency Tests**:
   ```
   tests/test_concurrency.py::test_production_engine_sets_busy_timeout PASSED
   tests/test_concurrency.py::test_concurrent_writers_with_busy_timeout PASSED
   tests/test_concurrency.py::test_zero_busy_timeout_causes_lock_errors PASSED
   ```
2. **Full Backend Test Suite**:
   - `452 passed, 3 warnings in 141.44s` (100% passing across all 10 phases + worker).
3. **Frontend Test Suite**:
   - `13 passed across 3 test files` in Vitest.
4. **Frontend Production Build**:
   - `vite build` completed cleanly in 641ms.
