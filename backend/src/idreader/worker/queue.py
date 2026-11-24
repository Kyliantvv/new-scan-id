"""File de jobs dans SQLite (docs/02_ARCHITECTURE.md § File de jobs).

- Prise : `BEGIN IMMEDIATE`, job `queued` le plus prioritaire avec `run_after <= now`.
- Battement de cœur : `heartbeat_at` mis à jour toutes les 10 s par le worker propriétaire.
- Reprise : un job `running` sans battement depuis 60 s est abandonné ; il repasse `queued`,
  ou `failed` (`WORKER_LOST`) s'il a déjà épuisé ses essais (3 par défaut).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from sqlalchemy import ColumnElement, Connection, Engine, Table, and_, or_, select, update
from sqlalchemy.orm import Session

from idreader.core.db import immediate_connection, utcnow
from idreader.core.models import Job, JobStatus, JobType

jobs = cast(Table, Job.__table__)


@dataclass(frozen=True)
class ClaimedJob:
    id: str
    type: str
    ref_id: str | None
    batch_id: str | None
    attempts: int


def enqueue(
    db: Session,
    job_type: JobType,
    *,
    ref_id: str | None = None,
    batch_id: str | None = None,
    priority: int = 0,
    run_after: datetime | None = None,
) -> Job:
    now = utcnow()
    job = Job(
        type=job_type,
        ref_id=ref_id,
        batch_id=batch_id,
        priority=priority,
        run_after=run_after or now,
        created_at=now,
    )
    db.add(job)
    db.flush()
    return job


def reclaim_stale(
    conn: Connection, now: datetime, stale_after: timedelta, max_attempts: int
) -> int:
    """Remet en file (ou en échec) les jobs dont le worker ne donne plus signe de vie."""
    limit = now - stale_after
    stale = and_(
        jobs.c.status == JobStatus.RUNNING,
        or_(jobs.c.heartbeat_at.is_(None), jobs.c.heartbeat_at < limit),
    )
    failed = conn.execute(
        update(jobs)
        .where(stale, jobs.c.attempts >= max_attempts)
        .values(
            status=JobStatus.FAILED,
            error_code="WORKER_LOST",
            worker_id=None,
            finished_at=now,
        )
    ).rowcount
    requeued = conn.execute(
        update(jobs)
        .where(stale, jobs.c.attempts < max_attempts)
        .values(status=JobStatus.QUEUED, worker_id=None, locked_at=None, stage=None, run_after=now)
    ).rowcount
    return int(failed) + int(requeued)


def claim_next(
    engine: Engine,
    worker_id: str,
    *,
    stale_after: timedelta,
    max_attempts: int,
    now: datetime | None = None,
) -> ClaimedJob | None:
    """Prend le prochain job ; chaque prise compte comme un essai (`attempts + 1`)."""
    now = now or utcnow()
    with immediate_connection(engine) as conn:
        reclaim_stale(conn, now, stale_after, max_attempts)
        row = conn.execute(
            select(jobs.c.id, jobs.c.type, jobs.c.ref_id, jobs.c.batch_id, jobs.c.attempts)
            .where(jobs.c.status == JobStatus.QUEUED, jobs.c.run_after <= now)
            .order_by(jobs.c.priority.desc(), jobs.c.created_at, jobs.c.id)
            .limit(1)
        ).one_or_none()
        if row is None:
            return None
        conn.execute(
            update(jobs)
            .where(jobs.c.id == row.id)
            .values(
                status=JobStatus.RUNNING,
                worker_id=worker_id,
                locked_at=now,
                heartbeat_at=now,
                attempts=row.attempts + 1,
                progress=0.0,
                stage=None,
                error_code=None,
            )
        )
        return ClaimedJob(row.id, row.type, row.ref_id, row.batch_id, row.attempts + 1)


def _owned(job_id: str, worker_id: str) -> ColumnElement[bool]:
    return and_(
        jobs.c.id == job_id, jobs.c.worker_id == worker_id, jobs.c.status == JobStatus.RUNNING
    )


def heartbeat(engine: Engine, job_id: str, worker_id: str) -> bool:
    """Vrai si le job appartient encore à ce worker (sinon il a été repris ailleurs)."""
    with immediate_connection(engine) as conn:
        n = conn.execute(
            update(jobs).where(_owned(job_id, worker_id)).values(heartbeat_at=utcnow())
        ).rowcount
    return bool(n)


def set_progress(engine: Engine, job_id: str, worker_id: str, progress: float, stage: str) -> bool:
    with immediate_connection(engine) as conn:
        n = conn.execute(
            update(jobs)
            .where(_owned(job_id, worker_id))
            .values(progress=max(0.0, min(1.0, progress)), stage=stage, heartbeat_at=utcnow())
        ).rowcount
    return bool(n)


def finish(engine: Engine, job_id: str, worker_id: str) -> bool:
    now = utcnow()
    with immediate_connection(engine) as conn:
        n = conn.execute(
            update(jobs)
            .where(_owned(job_id, worker_id))
            .values(status=JobStatus.DONE, progress=1.0, stage=None, finished_at=now)
        ).rowcount
    return bool(n)


def fail(engine: Engine, job_id: str, worker_id: str, error_code: str) -> bool:
    now = utcnow()
    with immediate_connection(engine) as conn:
        n = conn.execute(
            update(jobs)
            .where(_owned(job_id, worker_id))
            .values(status=JobStatus.FAILED, error_code=error_code, finished_at=now)
        ).rowcount
    return bool(n)
