"""Lots, upload multi-fichiers, tableau des documents."""

from __future__ import annotations

import logging
import shutil
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from idreader.api.deps import AuthDep, DbDep, SettingsDep, api_error
from idreader.core import audit
from idreader.core import models as m
from idreader.core.db import new_id
from idreader.core.storage import StorageError, clean_original_name, file_dir, save_upload
from idreader.worker.queue import enqueue

log = logging.getLogger(__name__)
router = APIRouter(tags=["batches"])


class BatchIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class JobOut(BaseModel):
    id: str
    status: str
    progress: float
    stage: str | None
    error_code: str | None
    attempts: int


class FileOut(BaseModel):
    id: str
    original_name: str
    mime: str
    size_bytes: int
    upload_order: int
    duplicate_of_id: str | None
    created_at: datetime
    job: JobOut | None


class BatchOut(BaseModel):
    id: str
    name: str
    status: str
    created_at: datetime
    file_count: int
    job_counts: dict[str, int]


class BatchDetailOut(BatchOut):
    files: list[FileOut]


class UploadResult(BaseModel):
    name: str
    status: Literal["queued", "duplicate", "rejected"]
    file_id: str | None = None
    job_id: str | None = None
    duplicate_of_id: str | None = None
    error_code: str | None = None


def _get_batch(db: Session, batch_id: str) -> m.Batch:
    batch = db.get(m.Batch, batch_id)
    if batch is None:
        raise api_error(404, "BATCH_NOT_FOUND", "Lot introuvable.")
    return batch


def _job_counts(db: Session, batch_ids: list[str]) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {b: {} for b in batch_ids}
    if not batch_ids:
        return counts
    rows = db.execute(
        select(m.Job.batch_id, m.Job.status, func.count())
        .where(m.Job.batch_id.in_(batch_ids))
        .group_by(m.Job.batch_id, m.Job.status)
    )
    for batch_id, status, n in rows:
        if batch_id is not None:
            counts[batch_id][status] = n
    return counts


def _file_counts(db: Session, batch_ids: list[str]) -> dict[str, int]:
    if not batch_ids:
        return {}
    rows = db.execute(
        select(m.File.batch_id, func.count())
        .where(m.File.batch_id.in_(batch_ids))
        .group_by(m.File.batch_id)
    )
    return {b: n for b, n in rows}


def _batch_out(batch: m.Batch, files: int, jobs: dict[str, int]) -> BatchOut:
    return BatchOut(
        id=batch.id,
        name=batch.name,
        status=batch.status,
        created_at=batch.created_at,
        file_count=files,
        job_counts=jobs,
    )


@router.get("/batches")
def list_batches(_auth: AuthDep, db: DbDep) -> list[BatchOut]:
    batches = db.scalars(select(m.Batch).order_by(m.Batch.created_at.desc())).all()
    ids = [b.id for b in batches]
    files, jobs = _file_counts(db, ids), _job_counts(db, ids)
    return [_batch_out(b, files.get(b.id, 0), jobs[b.id]) for b in batches]


@router.post("/batches", status_code=201)
def create_batch(body: BatchIn, auth: AuthDep, db: DbDep) -> BatchOut:
    batch = m.Batch(name=body.name.strip(), created_by=auth.user_id)
    db.add(batch)
    db.flush()
    audit.record(db, "batch_created", user_id=auth.user_id, entity="batch", entity_id=batch.id)
    db.commit()
    log.info("batch created batch_id=%s", batch.id)
    return _batch_out(batch, 0, {})


@router.get("/batches/{batch_id}")
def get_batch(batch_id: str, _auth: AuthDep, db: DbDep) -> BatchDetailOut:
    batch = _get_batch(db, batch_id)
    files = db.scalars(
        select(m.File).where(m.File.batch_id == batch_id).order_by(m.File.upload_order)
    ).all()
    jobs = {
        j.ref_id: j
        for j in db.scalars(
            select(m.Job).where(m.Job.batch_id == batch_id, m.Job.type == m.JobType.PROCESS_FILE)
        )
    }
    out_files = []
    for f in files:
        j = jobs.get(f.id)
        out_files.append(
            FileOut(
                id=f.id,
                original_name=f.original_name,
                mime=f.mime,
                size_bytes=f.size_bytes,
                upload_order=f.upload_order,
                duplicate_of_id=f.duplicate_of_id,
                created_at=f.created_at,
                job=JobOut.model_validate(j, from_attributes=True) if j else None,
            )
        )
    base = _batch_out(batch, len(files), _job_counts(db, [batch_id])[batch_id])
    return BatchDetailOut(**base.model_dump(), files=out_files)


@router.post("/batches/{batch_id}/files", status_code=201)
def upload_files(
    batch_id: str,
    files: Annotated[list[UploadFile], File(description="Fichiers PDF, JPG ou PNG")],
    auth: AuthDep,
    db: DbDep,
    settings: SettingsDep,
) -> list[UploadResult]:
    batch = _get_batch(db, batch_id)
    if batch.status != m.BatchStatus.OPEN:
        raise api_error(409, "BATCH_CLOSED", "Ce lot est archivé.")
    db.rollback()  # libère le verrou d'écriture pendant la copie des fichiers
    return [_store_one(upload, batch_id, auth.user_id, db, settings) for upload in files]


def _store_one(
    upload: UploadFile, batch_id: str, user_id: str, db: Session, settings: SettingsDep
) -> UploadResult:
    name = clean_original_name(upload.filename)
    file_id = new_id()
    dest = file_dir(settings.data_dir, batch_id, file_id)
    try:
        stored = save_upload(upload.file, dest, settings.max_upload_bytes)
    except StorageError as exc:
        log.info("upload rejected batch_id=%s code=%s", batch_id, exc.code)
        return UploadResult(name=name, status="rejected", error_code=exc.code)
    try:
        original = db.scalar(
            select(m.File).where(
                m.File.batch_id == batch_id,
                m.File.sha256 == stored.sha256,
                m.File.duplicate_of_id.is_(None),
            )
        )
        order = (
            db.scalar(select(func.max(m.File.upload_order)).where(m.File.batch_id == batch_id)) or 0
        ) + 1
        rel = stored.path.relative_to(settings.data_dir).as_posix()
        if original is not None:
            # Fichier identique : statut « Doublon », pas de seconde copie sur disque.
            shutil.rmtree(dest, ignore_errors=True)
            rel = original.storage_path
        f = m.File(
            id=file_id,
            batch_id=batch_id,
            original_name=name,
            mime=stored.mime,
            sha256=stored.sha256,
            size_bytes=stored.size_bytes,
            storage_path=rel,
            upload_order=order,
            duplicate_of_id=original.id if original else None,
        )
        db.add(f)
        db.flush()
        job_id = None
        if original is None:
            job_id = enqueue(db, m.JobType.PROCESS_FILE, ref_id=f.id, batch_id=batch_id).id
        audit.record(db, "file_uploaded", user_id=user_id, entity="file", entity_id=f.id)
        db.commit()
    except BaseException:
        db.rollback()
        shutil.rmtree(dest, ignore_errors=True)
        raise
    log.info(
        "file stored batch_id=%s file_id=%s size=%d mime=%s duplicate=%s",
        batch_id,
        file_id,
        stored.size_bytes,
        stored.mime,
        original is not None,
    )
    if original is not None:
        return UploadResult(
            name=name, status="duplicate", file_id=file_id, duplicate_of_id=original.id
        )
    return UploadResult(name=name, status="queued", file_id=file_id, job_id=job_id)
