"""Outils de test. Contenus synthétiques uniquement (aucune vraie pièce d'identité)."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from idreader.core.config import Settings
from idreader.core.models import Batch, File, Job, JobType
from idreader.worker.queue import enqueue

# Plus petit PNG valide (1 × 1 pixel, synthétique)
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


def make_batch_with_file(
    factory: sessionmaker[Session], settings: Settings, content: bytes = TINY_PNG
) -> tuple[str, str, str]:
    """Crée un lot, un fichier stocké sur disque et son job. Renvoie (batch_id, file_id, job_id)."""
    with factory() as db:
        batch = Batch(name="Lot synthétique")
        db.add(batch)
        db.flush()
        f = File(
            batch_id=batch.id,
            original_name="synthetique.png",
            mime="image/png",
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            storage_path="",
            upload_order=1,
        )
        db.add(f)
        db.flush()
        rel = f"batches/{batch.id}/files/{f.id}/original.png"
        f.storage_path = rel
        path = settings.data_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        job = enqueue(db, JobType.PROCESS_FILE, ref_id=f.id, batch_id=batch.id)
        db.commit()
        return batch.id, f.id, job.id


def get_job(factory: sessionmaker[Session], job_id: str) -> Job:
    with factory() as db:
        job = db.get(Job, job_id)
        assert job is not None
        return job


def wait_for(predicate: Callable[[], bool], timeout: float = 20.0, step: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()
