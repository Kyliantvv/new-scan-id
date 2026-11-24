from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import update

from idreader.core.db import make_engine, make_session_factory, utcnow
from idreader.core.models import Job, JobStatus, JobType
from idreader.worker import queue
from idreader.worker.handlers import HANDLERS, JobError
from idreader.worker.main import Worker
from tests.helpers import get_job, make_batch_with_file

STALE = timedelta(seconds=60)


@pytest.fixture
def engine(migrated):
    eng = make_engine(migrated)
    yield eng
    eng.dispose()


@pytest.fixture
def factory(engine):
    return make_session_factory(engine)


def _enqueue(factory, priority: int = 0, run_after=None) -> str:
    with factory() as db:
        job = queue.enqueue(db, JobType.PROCESS_FILE, priority=priority, run_after=run_after)
        db.commit()
        return job.id


def test_claim_marks_running_and_counts_attempt(engine, factory) -> None:
    jid = _enqueue(factory)
    claimed = queue.claim_next(engine, "w1", stale_after=STALE, max_attempts=3)
    assert claimed is not None and claimed.id == jid and claimed.attempts == 1
    job = get_job(factory, jid)
    assert job.status == JobStatus.RUNNING
    assert job.worker_id == "w1"
    assert job.heartbeat_at is not None
    assert queue.claim_next(engine, "w2", stale_after=STALE, max_attempts=3) is None


def test_priority_then_fifo(engine, factory) -> None:
    low = _enqueue(factory, priority=0)
    high = _enqueue(factory, priority=5)
    low2 = _enqueue(factory, priority=0)
    order = [queue.claim_next(engine, "w", stale_after=STALE, max_attempts=3) for _ in range(3)]
    assert [j.id for j in order if j] == [high, low, low2]


def test_run_after_respected(engine, factory) -> None:
    _enqueue(factory, run_after=utcnow() + timedelta(hours=1))
    assert queue.claim_next(engine, "w", stale_after=STALE, max_attempts=3) is None


def test_heartbeat_only_for_owner(engine, factory) -> None:
    _enqueue(factory)
    job = queue.claim_next(engine, "w1", stale_after=STALE, max_attempts=3)
    assert job is not None
    assert queue.heartbeat(engine, job.id, "w1")
    assert not queue.heartbeat(engine, job.id, "intrus")


def _age_heartbeat(factory, job_id: str, seconds: float) -> None:
    with factory() as db:
        db.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(heartbeat_at=utcnow() - timedelta(seconds=seconds))
        )
        db.commit()


def test_fresh_running_job_not_reclaimed(engine, factory) -> None:
    _enqueue(factory)
    queue.claim_next(engine, "w1", stale_after=STALE, max_attempts=3)
    assert queue.claim_next(engine, "w2", stale_after=STALE, max_attempts=3) is None


def test_stale_job_reclaimed_by_other_worker(engine, factory) -> None:
    jid = _enqueue(factory)
    queue.claim_next(engine, "w1", stale_after=STALE, max_attempts=3)
    _age_heartbeat(factory, jid, 61)
    again = queue.claim_next(engine, "w2", stale_after=STALE, max_attempts=3)
    assert again is not None and again.id == jid and again.attempts == 2
    # l'ancien propriétaire ne peut plus écrire
    assert not queue.heartbeat(engine, jid, "w1")
    assert not queue.finish(engine, jid, "w1")
    assert queue.finish(engine, jid, "w2")


def test_three_attempts_then_failed(engine, factory) -> None:
    jid = _enqueue(factory)
    for n in range(3):
        job = queue.claim_next(engine, f"w{n}", stale_after=STALE, max_attempts=3)
        assert job is not None and job.attempts == n + 1
        _age_heartbeat(factory, jid, 61)
    assert queue.claim_next(engine, "w9", stale_after=STALE, max_attempts=3) is None
    job_row = get_job(factory, jid)
    assert job_row.status == JobStatus.FAILED
    assert job_row.error_code == "WORKER_LOST"


def test_progress_clamped(engine, factory) -> None:
    jid = _enqueue(factory)
    queue.claim_next(engine, "w", stale_after=STALE, max_attempts=3)
    queue.set_progress(engine, jid, "w", 1.7, "dummy")
    assert get_job(factory, jid).progress == 1.0


def test_worker_runs_dummy_job_to_done(migrated, engine, factory) -> None:
    _, _, jid = make_batch_with_file(factory, migrated)
    assert Worker(migrated, engine, "w").run(exit_when_idle=True) == 1
    job = get_job(factory, jid)
    assert job.status == JobStatus.DONE
    assert job.progress == 1.0
    assert job.finished_at is not None


def test_worker_fails_job_with_stable_code(migrated, engine, factory) -> None:
    _, _, jid = make_batch_with_file(factory, migrated)
    path = next((migrated.data_dir / "batches").rglob("original.png"))
    path.write_bytes(b"altere")
    Worker(migrated, engine, "w").run(exit_when_idle=True)
    job = get_job(factory, jid)
    assert job.status == JobStatus.FAILED
    assert job.error_code == "FILE_CHECKSUM_MISMATCH"


def test_unexpected_exception_logged_without_message(
    migrated, engine, factory, monkeypatch, caplog
) -> None:
    def boom(_ctx) -> None:
        raise ValueError("ERIKSSON ANNA L898902C3")  # donnée fictive qui ne doit pas fuiter

    monkeypatch.setitem(HANDLERS, JobType.PROCESS_FILE, boom)
    _, _, jid = make_batch_with_file(factory, migrated)
    Worker(migrated, engine, "w").run(exit_when_idle=True)
    assert get_job(factory, jid).error_code == "INTERNAL_ERROR"
    assert "ValueError" in caplog.text
    assert "ERIKSSON" not in caplog.text
    assert "L898902C3" not in caplog.text


def test_job_error_carries_code() -> None:
    assert JobError("X").code == "X"
