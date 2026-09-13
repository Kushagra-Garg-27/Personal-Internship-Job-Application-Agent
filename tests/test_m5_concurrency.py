"""M5 Real Concurrency Tests (BLOCKER 7).

Proves race safety between revoke and claim using a real file-backed SQLite database
with WAL mode, busy timeout, and two independent database sessions/engines.

Tests:
1. Revoke wins race: revoke CAS succeeds (rowcount=1); claim CAS fails (rowcount=0).
2. Claim wins race: claim CAS succeeds (rowcount=1); revoke CAS fails (rowcount=0).
3. Double revoke race: two concurrent revoke attempts; exactly one succeeds, one fails.
4. Double claim race: two concurrent claim attempts; exactly one succeeds, one fails.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from core.models.base import Base
from core.models.opportunity import Application, Opportunity
from core.services import submission_service
from core.status import ApplicationStatus, OpportunityStatus
from worker.runner import WorkerRunner


@pytest.fixture()
def db_file(tmp_path: Path) -> Path:
    db_path = tmp_path / "m5_race.db"
    return db_path


def make_engine_and_session(db_path: Path):
    db_url = f"sqlite:///{db_path.as_posix()}"
    engine = create_engine(
        db_url,
        connect_args={"check_same_thread": False, "timeout": 15},
    )

    @event.listens_for(engine, "connect")
    def _pragmas(conn, _rec):
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=15000")
        cur.close()

    session_cls = sessionmaker(bind=engine, autoflush=True, autocommit=False)
    return engine, session_cls


@pytest.fixture()
def setup_db(db_file: Path):
    engine, session_cls = make_engine_and_session(db_file)
    Base.metadata.create_all(engine)
    engine.dispose()
    return db_file


def _seed_approved_app(session: Session) -> tuple[int, int]:
    opp = Opportunity(
        dedup_hash="test-hash-" + str(threading.get_ident()) + "-" + str(datetime.now().timestamp()),
        status=OpportunityStatus.AWAITING_SUBMISSION.value,
        reliability_tier="experimental",
        title="Software Engineer",
        company="TechCorp",
        url="https://example.com/jobs/1",
    )
    session.add(opp)
    session.flush()

    app = Application(
        opportunity_id=opp.id,
        status=ApplicationStatus.FORM_FILLED.value,
        attempt_number=1,
        approved_at=datetime.now(timezone.utc),
        approved_by="human_approver",
    )
    session.add(app)
    session.commit()
    return app.id, opp.id


def test_concurrency_revoke_wins_race(setup_db: Path):
    """When revoke executes first, revoke CAS succeeds and claim CAS returns False."""
    engine1, session_cls1 = make_engine_and_session(setup_db)
    engine2, session_cls2 = make_engine_and_session(setup_db)

    sess_init = session_cls1()
    app_id, _ = _seed_approved_app(sess_init)
    sess_init.close()

    results = {}
    barrier = threading.Barrier(2)

    def run_revoke():
        sess = session_cls1()
        try:
            barrier.wait(timeout=5)
            res = submission_service.revoke_approval(sess, app_id, reason="Changed mind")
            sess.commit()
            results["revoke"] = ("success", res)
        except Exception as exc:
            sess.rollback()
            results["revoke"] = ("error", str(exc))
        finally:
            sess.close()

    def run_claim():
        sess = session_cls2()
        try:
            barrier.wait(timeout=5)
            # Short sleep to allow revoke transaction to commit first
            threading.Event().wait(0.05)
            runner = WorkerRunner()
            claimed = runner.claim_application_for_submission(sess, app_id, worker_id="worker-1")
            results["claim"] = ("result", claimed)
        except Exception as exc:
            results["claim"] = ("error", str(exc))
        finally:
            sess.close()

    t1 = threading.Thread(target=run_revoke)
    t2 = threading.Thread(target=run_claim)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert results["revoke"][0] == "success"
    assert results["claim"][0] == "result"
    assert results["claim"][1] is False, "Claim must fail once application is revoked"

    # Verify state in DB
    verify_sess = session_cls1()
    app = verify_sess.get(Application, app_id)
    assert app.approved_at is None
    assert app.approval_revoked_at is not None
    assert app.submission_claimed_at is None
    verify_sess.close()

    engine1.dispose()
    engine2.dispose()


def test_concurrency_claim_wins_race(setup_db: Path):
    """When worker claim executes first, claim CAS succeeds and revoke raises ValueError."""
    engine1, session_cls1 = make_engine_and_session(setup_db)
    engine2, session_cls2 = make_engine_and_session(setup_db)

    sess_init = session_cls1()
    app_id, _ = _seed_approved_app(sess_init)
    sess_init.close()

    results = {}
    barrier = threading.Barrier(2)

    def run_claim():
        sess = session_cls1()
        try:
            barrier.wait(timeout=5)
            runner = WorkerRunner()
            claimed = runner.claim_application_for_submission(sess, app_id, worker_id="worker-winner")
            results["claim"] = ("result", claimed)
        except Exception as exc:
            results["claim"] = ("error", str(exc))
        finally:
            sess.close()

    def run_revoke():
        sess = session_cls2()
        try:
            barrier.wait(timeout=5)
            # Short sleep to ensure claim commits first
            threading.Event().wait(0.05)
            res = submission_service.revoke_approval(sess, app_id, reason="Too late")
            sess.commit()
            results["revoke"] = ("success", res)
        except Exception as exc:
            sess.rollback()
            results["revoke"] = ("error", str(exc))
        finally:
            sess.close()

    t1 = threading.Thread(target=run_claim)
    t2 = threading.Thread(target=run_revoke)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert results["claim"][0] == "result"
    assert results["claim"][1] is True, "Claim must succeed on approved unclaimed app"
    assert results["revoke"][0] == "error"
    assert "claimed" in results["revoke"][1], "Revoke must fail with claimed message"

    # Verify state in DB
    verify_sess = session_cls1()
    app = verify_sess.get(Application, app_id)
    assert app.submission_claimed_at is not None
    assert app.claimed_by == "worker-winner"
    assert app.approval_revoked_at is None
    verify_sess.close()

    engine1.dispose()
    engine2.dispose()


def test_concurrency_double_revoke_exactly_one_wins(setup_db: Path):
    """Two concurrent revoke attempts: exactly one succeeds, the other fails."""
    engine1, session_cls1 = make_engine_and_session(setup_db)
    engine2, session_cls2 = make_engine_and_session(setup_db)

    sess_init = session_cls1()
    app_id, _ = _seed_approved_app(sess_init)
    sess_init.close()

    results = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def run_revoke(operator_name: str, session_cls):
        sess = session_cls()
        try:
            barrier.wait(timeout=5)
            res = submission_service.revoke_approval(sess, app_id, revoked_by=operator_name)
            sess.commit()
            with lock:
                results.append(("success", operator_name, res))
        except Exception as exc:
            sess.rollback()
            with lock:
                results.append(("error", operator_name, str(exc)))
        finally:
            sess.close()

    t1 = threading.Thread(target=run_revoke, args=("op-1", session_cls1))
    t2 = threading.Thread(target=run_revoke, args=("op-2", session_cls2))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    successes = [r for r in results if r[0] == "success"]
    errors = [r for r in results if r[0] == "error"]

    assert len(successes) == 1, f"Exactly one revoke must succeed, got {successes}"
    assert len(errors) == 1, f"Exactly one revoke must fail, got {errors}"

    verify_sess = session_cls1()
    app = verify_sess.get(Application, app_id)
    assert app.approval_revoked_at is not None
    assert app.approved_at is None
    verify_sess.close()

    engine1.dispose()
    engine2.dispose()


def test_concurrency_double_claim_exactly_one_wins(setup_db: Path):
    """Two concurrent worker claims: exactly one returns True, the other returns False."""
    engine1, session_cls1 = make_engine_and_session(setup_db)
    engine2, session_cls2 = make_engine_and_session(setup_db)

    sess_init = session_cls1()
    app_id, _ = _seed_approved_app(sess_init)
    sess_init.close()

    claims = []
    lock = threading.Lock()
    barrier = threading.Barrier(2)

    def run_claim(worker_id: str, session_cls):
        sess = session_cls()
        try:
            barrier.wait(timeout=5)
            runner = WorkerRunner()
            won = runner.claim_application_for_submission(sess, app_id, worker_id=worker_id)
            with lock:
                claims.append((worker_id, won))
        except Exception as exc:
            with lock:
                claims.append((worker_id, False, str(exc)))
        finally:
            sess.close()

    t1 = threading.Thread(target=run_claim, args=("worker-A", session_cls1))
    t2 = threading.Thread(target=run_claim, args=("worker-B", session_cls2))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    wins = [c for c in claims if c[1] is True]
    losses = [c for c in claims if c[1] is False]

    assert len(wins) == 1, f"Exactly one worker must win claim race, got {claims}"
    assert len(losses) == 1, f"Exactly one worker must lose claim race, got {claims}"

    winning_worker = wins[0][0]
    verify_sess = session_cls1()
    app = verify_sess.get(Application, app_id)
    assert app.submission_claimed_at is not None
    assert app.claimed_by == winning_worker
    verify_sess.close()

    engine1.dispose()
    engine2.dispose()
