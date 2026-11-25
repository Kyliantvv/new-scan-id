"""Job `pair_batch` : appariement recto/verso du lot (étape J), puis doublons de document.

Ne s'exécute que lorsque tous les fichiers du lot sont analysés ; sinon il se reprogramme.
Seuls les documents d'une face, non relus par un humain, sont appariés automatiquement.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from sqlalchemy import func, select

from idreader.core import models as m
from idreader.core.db import make_session_factory
from idreader.pipeline.pairing import Side, pair_sides
from idreader.worker.handlers import JobContext, JobError
from idreader.worker.queue import schedule_pairing
from idreader.worker.store import load_evidence, mark_duplicates, merge_documents

log = logging.getLogger(__name__)

PAIR_DELAY = timedelta(minutes=2)
PAIRABLE_STATUSES = (m.DocumentStatus.TO_REVIEW, m.DocumentStatus.AUTO_VALIDATED)


def pair_batch(ctx: JobContext) -> None:
    batch_id = ctx.job.ref_id
    if batch_id is None:
        raise JobError("MISSING_REF")
    settings = ctx.settings
    # Pas de ctx.report() dans la transaction d'écriture : il écrit par une autre connexion.
    ctx.report(0.1, "pairing")
    with make_session_factory(ctx.engine, immediate=True)() as db:
        pending = db.scalar(
            select(func.count()).where(
                m.Job.batch_id == batch_id,
                m.Job.type == m.JobType.PROCESS_FILE,
                m.Job.status.in_((m.JobStatus.QUEUED, m.JobStatus.RUNNING)),
            )
        )
        if pending:
            schedule_pairing(db, batch_id, PAIR_DELAY)
            db.commit()
            log.info("pairing postponed batch_id=%s pending_files=%d", batch_id, pending)
            return
        docs = db.scalars(
            select(m.Document).where(
                m.Document.batch_id == batch_id,
                m.Document.status.in_(PAIRABLE_STATUSES),
                m.Document.reviewed_by.is_(None),
            )
        ).all()
        single = [d for d in docs if (d.front_crop_id is None) != (d.back_crop_id is None)]
        sides: list[Side] = []
        by_key: dict[str, m.Document] = {}
        for d in single:
            crop_id = d.front_crop_id or d.back_crop_id
            row = db.execute(
                select(m.Crop, m.Page, m.File)
                .join(m.Page, m.Page.id == m.Crop.page_id)
                .join(m.File, m.File.id == m.Page.file_id)
                .where(m.Crop.id == crop_id)
            ).first()
            if row is None:
                continue
            crop, page, f = row
            ev = load_evidence(settings, batch_id, crop)
            if ev is None:
                continue
            sides.append(
                Side(
                    key=d.id,
                    file_order=f.upload_order,
                    page_key=page.id,
                    physical_format=crop.physical_format,
                    mrz=ev.mrz.result,
                    texts=tuple(s.text for s in ev.viz.slots),
                )
            )
            by_key[d.id] = d
        auto, suggested = pair_sides(sides)
        for d in db.scalars(select(m.Document).where(m.Document.batch_id == batch_id)):
            d.suggested_pair_id, d.pair_score = None, None
        today = date.today()
        for p in auto:
            merge_documents(
                db, settings, by_key[p.mrz_key], by_key[p.other_key],
                user_id=None, score=p.score, today=today,
            )  # fmt: skip
        for p in suggested:
            a, b = by_key[p.mrz_key], by_key[p.other_key]
            a.suggested_pair_id, b.suggested_pair_id = b.id, a.id
            a.pair_score = b.pair_score = p.score
        duplicates = mark_duplicates(db, batch_id)
        db.commit()
    ctx.report(1.0, "done")
    log.info(
        "batch paired batch_id=%s candidates=%d auto=%d suggested=%d duplicates=%d",
        batch_id,
        len(sides),
        len(auto),
        len(suggested),
        duplicates,
    )
