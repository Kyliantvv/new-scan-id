"""Boucle du worker : prise de job, battement de cœur, exécution, reprise."""

from __future__ import annotations

import logging
import os
import secrets
import socket
import threading
import time
from datetime import timedelta

from sqlalchemy import Engine

from idreader.core.config import Settings
from idreader.core.logging import log_exception_safely
from idreader.worker import queue
from idreader.worker.handlers import HANDLERS, JobContext, JobError, JobLost

log = logging.getLogger("idreader.worker")


def make_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(3)}"


class Worker:
    def __init__(self, settings: Settings, engine: Engine, worker_id: str | None = None) -> None:
        self.settings = settings
        self.engine = engine
        self.worker_id = worker_id or make_worker_id()
        self.stop_event = threading.Event()

    def stop(self) -> None:
        self.stop_event.set()

    def run(self, *, max_jobs: int | None = None, exit_when_idle: bool = False) -> int:
        """Traite des jobs jusqu'à l'arrêt. Renvoie le nombre de jobs traités."""
        log.info("worker start worker_id=%s", self.worker_id)
        done = 0
        while not self.stop_event.is_set():
            if max_jobs is not None and done >= max_jobs:
                break
            if self.run_one():
                done += 1
                continue
            if exit_when_idle:
                break
            self.stop_event.wait(self.settings.worker_poll_seconds)
        log.info("worker stop worker_id=%s jobs=%d", self.worker_id, done)
        return done

    def run_one(self) -> bool:
        job = queue.claim_next(
            self.engine,
            self.worker_id,
            stale_after=timedelta(seconds=self.settings.stale_after_seconds),
            max_attempts=self.settings.max_attempts,
        )
        if job is None:
            return False
        self._execute(job)
        return True

    def _execute(self, job: queue.ClaimedJob) -> None:
        start = time.perf_counter()
        log.info("job start job_id=%s type=%s attempt=%d", job.id, job.type, job.attempts)
        lost = threading.Event()
        finished = threading.Event()

        def beat() -> None:
            while not finished.wait(self.settings.heartbeat_seconds):
                if not queue.heartbeat(self.engine, job.id, self.worker_id):
                    lost.set()
                    return

        beater = threading.Thread(target=beat, name=f"heartbeat-{job.id}", daemon=True)
        beater.start()

        def report(progress: float, stage: str) -> None:
            if not queue.set_progress(self.engine, job.id, self.worker_id, progress, stage):
                lost.set()
                raise JobLost(job.id)

        ctx = JobContext(self.settings, self.engine, job, report, is_lost=lost.is_set)
        try:
            handler = HANDLERS.get(job.type)
            if handler is None:
                raise JobError("UNKNOWN_JOB_TYPE")
            handler(ctx)
        except JobLost:
            log.warning("job lost job_id=%s (repris par un autre worker)", job.id)
        except JobError as exc:
            queue.fail(self.engine, job.id, self.worker_id, exc.code)
            log.warning("job failed job_id=%s code=%s", job.id, exc.code)
        except Exception as exc:
            queue.fail(self.engine, job.id, self.worker_id, "INTERNAL_ERROR")
            log_exception_safely(log, exc, f"job failed job_id={job.id} code=INTERNAL_ERROR")
        else:
            queue.finish(self.engine, job.id, self.worker_id)
            log.info("job done job_id=%s ms=%.0f", job.id, (time.perf_counter() - start) * 1000)
        finally:
            finished.set()
            beater.join(timeout=5)
