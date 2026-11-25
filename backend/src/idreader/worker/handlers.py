"""Exécution des jobs par type.

`process_file` exécute le pipeline (`worker/process.py`). Le job factice de la phase 0
(vérification du fichier, avancement simulé) reste disponible pour les tests du worker
(`IDR_DUMMY_PIPELINE=true`).
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from sqlalchemy import Engine

from idreader.core.config import Settings
from idreader.core.db import make_session_factory
from idreader.core.models import File, JobType
from idreader.worker.queue import ClaimedJob


class JobError(Exception):
    """Échec métier d'un job, avec un code stable (jamais de donnée dans le message)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class JobLost(Exception):
    """Le job a été repris par un autre worker : arrêter sans rien écrire."""


class Reporter(Protocol):
    def __call__(self, progress: float, stage: str) -> None: ...


@dataclass
class JobContext:
    settings: Settings
    engine: Engine
    job: ClaimedJob
    report: Reporter
    is_lost: Callable[[], bool] = field(default=lambda: False)

    def check(self) -> None:
        if self.is_lost():
            raise JobLost(self.job.id)


Handler = Callable[[JobContext], None]


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def process_file_dummy(ctx: JobContext) -> None:
    """Job factice de la phase 0."""
    if ctx.job.ref_id is None:
        raise JobError("MISSING_REF")
    with make_session_factory(ctx.engine, immediate=False)() as db:
        f = db.get(File, ctx.job.ref_id)
        if f is None:
            raise JobError("FILE_NOT_FOUND")
        path = ctx.settings.data_dir / f.storage_path
        expected = f.sha256
    ctx.report(0.1, "verify")
    if not path.is_file():
        raise JobError("FILE_MISSING_ON_DISK")
    if sha256_of(path) != expected:
        raise JobError("FILE_CHECKSUM_MISMATCH")
    steps = 10
    for i in range(steps):
        ctx.check()
        time.sleep(ctx.settings.dummy_job_seconds / steps)
        ctx.report(0.1 + 0.9 * (i + 1) / steps, "dummy")


def process_file(ctx: JobContext) -> None:
    if ctx.settings.dummy_pipeline:
        process_file_dummy(ctx)
        return
    from idreader.worker.process import process_file as run  # import différé : moteurs lourds

    run(ctx)


def pair_batch(ctx: JobContext) -> None:
    from idreader.worker.pair import pair_batch as run

    run(ctx)


HANDLERS: dict[str, Handler] = {
    JobType.PROCESS_FILE: process_file,
    JobType.PAIR_BATCH: pair_batch,
}
