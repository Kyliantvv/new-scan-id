"""Purge selon la durée de conservation (docs/05_SECURITE_RGPD.md § Conservation et purge).

- Images (`retention_images_days`, 30 jours) : original, pages, recadrages et artefacts
  d'analyse (sorties OCR brutes) de chaque fichier téléversé avant la date limite. Les
  données extraites restent consultables, sans image.
- Données (`retention_data_days`, 365 jours) : lot entier (fichiers, documents, champs,
  jobs) dont le dernier fichier est plus ancien que la date limite, entrées du journal
  d'audit, jobs terminés hors lot.
- Dossiers de débogage (`data/debug/`) : même durée que les images.
- Sessions expirées.

Les lots qui ont un job en file ou en cours sont laissés de côté (purgés au passage suivant).
`data/golden/` n'est jamais touché : sa durée relève de la décision du DPO.
Purger = supprimer les fichiers **et** les lignes, puis VACUUM (`vacuum`).
"""

from __future__ import annotations

import logging
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import Engine, delete, func, select
from sqlalchemy.orm import Session

from idreader.core import audit
from idreader.core import models as m
from idreader.core.config import Settings
from idreader.core.db import utcnow

log = logging.getLogger(__name__)

ACTIVE_JOBS = (m.JobStatus.QUEUED, m.JobStatus.RUNNING)


class PurgeReport(BaseModel):
    dry_run: bool = False
    images_days: int
    data_days: int
    files_images_purged: int = 0  # fichiers dont les images et artefacts ont été supprimés
    batches_deleted: int = 0
    audit_entries_deleted: int = 0
    jobs_deleted: int = 0
    sessions_deleted: int = 0
    debug_dirs_deleted: int = 0
    batches_skipped_busy: int = 0

    @property
    def rows_deleted(self) -> bool:
        return bool(self.batches_deleted or self.audit_entries_deleted or self.jobs_deleted)


class BatchBusyError(Exception):
    """Le lot a un job en file ou en cours : on ne le purge pas sous les pieds du worker."""


def _batch_dir(settings: Settings, batch_id: str) -> Path:
    return settings.data_dir / "batches" / batch_id


def _busy_batches(db: Session) -> set[str]:
    rows = db.scalars(
        select(m.Job.batch_id).where(m.Job.status.in_(ACTIVE_JOBS), m.Job.batch_id.is_not(None))
    )
    return {b for b in rows if b}


def _purge_file_images(db: Session, settings: Settings, f: m.File, *, dry_run: bool) -> bool:
    """Supprime l'original, les pages, les recadrages et les artefacts d'un fichier.

    Renvoie True si quelque chose existait encore sur le disque.
    """
    bdir = _batch_dir(settings, f.batch_id)
    targets: list[Path] = [(settings.data_dir / f.storage_path).parent]
    pages = db.scalars(select(m.Page).where(m.Page.file_id == f.id)).all()
    for p in pages:
        if p.storage_path:
            targets.append(bdir / p.storage_path)
        for c in db.scalars(select(m.Crop).where(m.Crop.page_id == p.id)):
            if c.storage_path:
                targets.append(bdir / c.storage_path)
            targets.append(bdir / "artifacts" / c.id)
    existing = [t for t in targets if t.exists() and t.resolve().is_relative_to(bdir.resolve())]
    if dry_run:
        return bool(existing)
    for t in existing:
        if t.is_dir():
            shutil.rmtree(t, ignore_errors=True)
        else:
            t.unlink(missing_ok=True)
    return bool(existing)


def delete_batch(db: Session, settings: Settings, batch: m.Batch, *, dry_run: bool = False) -> None:
    """Lot entier : lignes (cascade : fichiers, pages, recadrages, documents, champs, jobs)
    et dossier sur le disque."""
    if dry_run:
        return
    bdir = _batch_dir(settings, batch.id)
    db.delete(batch)
    db.flush()
    shutil.rmtree(bdir, ignore_errors=True)


def purge_batch(
    db: Session, settings: Settings, batch_id: str, *, user_id: str | None, dry_run: bool = False
) -> bool:
    """Purge manuelle d'un lot (administration). False si le lot n'existe pas."""
    batch = db.get(m.Batch, batch_id)
    if batch is None:
        return False
    if batch_id in _busy_batches(db):
        raise BatchBusyError(batch_id)
    delete_batch(db, settings, batch, dry_run=dry_run)
    if not dry_run:
        audit.record(db, "batch_purged", user_id=user_id, entity="batch", entity_id=batch_id)
        log.info("batch purged batch_id=%s", batch_id)
    return True


def purge_expired(
    db: Session,
    settings: Settings,
    *,
    images_days: int,
    data_days: int,
    now: datetime | None = None,
    user_id: str | None = None,
    dry_run: bool = False,
) -> PurgeReport:
    """Purge automatique (quotidienne) selon les deux durées de conservation."""
    now = now or utcnow()
    images_cutoff = now - timedelta(days=images_days)
    data_cutoff = now - timedelta(days=data_days)
    report = PurgeReport(dry_run=dry_run, images_days=images_days, data_days=data_days)
    busy = _busy_batches(db)

    # Données : lots dont le dernier fichier (ou la création, si vide) dépasse la durée.
    last_activity = (
        select(m.Batch, func.max(m.File.created_at))
        .outerjoin(m.File, m.File.batch_id == m.Batch.id)
        .group_by(m.Batch.id)
    )
    for batch, last_file in db.execute(last_activity).all():
        last = max(batch.created_at, last_file) if last_file else batch.created_at
        if last >= data_cutoff:
            continue
        if batch.id in busy:
            report.batches_skipped_busy += 1
            continue
        delete_batch(db, settings, batch, dry_run=dry_run)
        report.batches_deleted += 1

    # Images : fichier par fichier, dans les lots restants.
    old_files = db.scalars(select(m.File).where(m.File.created_at < images_cutoff)).all()
    skipped: set[str] = set()
    for f in old_files:
        if f.batch_id in busy:
            skipped.add(f.batch_id)
            continue
        if _purge_file_images(db, settings, f, dry_run=dry_run):
            report.files_images_purged += 1
    report.batches_skipped_busy += len(skipped)

    # Journal d'audit et jobs terminés hors lot (ceux d'un lot partent avec lui).
    audit_q = select(func.count()).where(m.AuditLog.at < data_cutoff)
    jobs_where = (
        m.Job.finished_at.is_not(None),
        m.Job.finished_at < data_cutoff,
        m.Job.status.not_in(ACTIVE_JOBS),
    )
    report.audit_entries_deleted = db.scalar(audit_q) or 0
    report.jobs_deleted = db.scalar(select(func.count()).where(*jobs_where)) or 0
    report.sessions_deleted = (
        db.scalar(select(func.count()).where(m.UserSession.expires_at < now)) or 0
    )
    if not dry_run:
        db.execute(delete(m.AuditLog).where(m.AuditLog.at < data_cutoff))
        db.execute(delete(m.Job).where(*jobs_where))
        db.execute(delete(m.UserSession).where(m.UserSession.expires_at < now))

    # Débogage (`idreader analyze --debug`) : sorties OCR brutes, durée des images.
    debug = settings.data_dir / "debug"
    if debug.is_dir():
        limit = time.time() - images_days * 86_400
        for d in debug.iterdir():
            if d.is_dir() and d.stat().st_mtime < limit:
                if not dry_run:
                    shutil.rmtree(d, ignore_errors=True)
                report.debug_dirs_deleted += 1

    if not dry_run:
        # L'entrée « purge » ne contient que des compteurs (aucune donnée personnelle).
        audit.record(
            db,
            "purge",
            user_id=user_id,
            entity="settings",
            after=report.model_dump(exclude={"dry_run"}),
        )
    log.info(
        "purge done dry_run=%s batches=%d files_images=%d audit=%d jobs=%d skipped_busy=%d",
        dry_run,
        report.batches_deleted,
        report.files_images_purged,
        report.audit_entries_deleted,
        report.jobs_deleted,
        report.batches_skipped_busy,
    )
    return report


def vacuum(engine: Engine) -> None:
    """VACUUM hors transaction : rend au disque la place des lignes supprimées."""
    # Connexion DBAPI brute : l'événement « begin » du moteur ouvrirait une transaction.
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute("VACUUM")
        cursor.close()
    finally:
        raw.close()
