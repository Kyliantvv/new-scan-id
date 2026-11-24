"""Critère d'acceptation de la phase 0 : worker tué pendant un job, puis relancé : le job reprend.

Lance de vrais processus `python -m idreader worker` ; `Popen.kill()` (SIGKILL sous macOS,
TerminateProcess sous Windows) simule un arrêt brutal sans nettoyage.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from idreader.core.db import make_engine, make_session_factory
from idreader.core.models import JobStatus
from tests.helpers import get_job, make_batch_with_file, wait_for

pytestmark = pytest.mark.slow


def _spawn_worker(settings, dummy_seconds: float) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env.update(
        {
            "IDR_DATA_DIR": str(settings.data_dir),
            "IDR_SECRET_KEY": "x",
            "IDR_HEARTBEAT_SECONDS": "0.2",
            "IDR_STALE_AFTER_SECONDS": "1.5",
            "IDR_WORKER_POLL_SECONDS": "0.1",
            "IDR_DUMMY_JOB_SECONDS": str(dummy_seconds),
        }
    )
    return subprocess.Popen(
        [sys.executable, "-m", "idreader", "worker"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_killed_worker_job_resumes(migrated) -> None:
    engine = make_engine(migrated)
    factory = make_session_factory(engine)
    _, _, jid = make_batch_with_file(factory, migrated)

    first = _spawn_worker(migrated, dummy_seconds=60)
    try:
        assert wait_for(lambda: get_job(factory, jid).status == JobStatus.RUNNING)
        assert wait_for(lambda: get_job(factory, jid).stage == "dummy")
        first_owner = get_job(factory, jid).worker_id
    finally:
        first.kill()
        first.wait(timeout=10)
    assert get_job(factory, jid).status == JobStatus.RUNNING  # orphelin, pas nettoyé

    second = _spawn_worker(migrated, dummy_seconds=0.5)
    try:
        assert wait_for(lambda: get_job(factory, jid).status == JobStatus.DONE, timeout=30)
    finally:
        second.terminate()
        second.wait(timeout=10)
    job = get_job(factory, jid)
    assert job.attempts == 2
    assert job.worker_id != first_owner
    assert job.progress == 1.0
    engine.dispose()
