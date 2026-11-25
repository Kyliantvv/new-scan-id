"""Job `process_file` : pipeline A→L sur un fichier, écriture des pages, recadrages,
artefacts JSON et documents.

Disposition (docs/02_ARCHITECTURE.md § Stockage) :
    batches/<batch>/pages/<page>.png
    batches/<batch>/crops/<crop>.png
    batches/<batch>/artifacts/<crop>/<étape>.json

Rejouable : une nouvelle exécution remplace les pages, recadrages et documents du fichier.
Un document par recadrage ; l'appariement recto/verso (job `pair_batch`) fusionne ensuite.
Aucune donnée personnelle dans les logs : identifiants et codes seulement.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import cv2
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from idreader.core import app_settings
from idreader.core import models as m
from idreader.core.config import Settings
from idreader.core.db import make_session_factory, new_id
from idreader.engines.registry import available_engines, available_viz_engines
from idreader.pipeline.analyze import Engines, analyze_file
from idreader.pipeline.ingest import IngestError, RGBImage
from idreader.worker.handlers import JobContext, JobError, sha256_of
from idreader.worker.pair import PAIR_DELAY
from idreader.worker.queue import schedule_pairing
from idreader.worker.store import create_document, detach_crops

log = logging.getLogger(__name__)

_ENGINES: dict[tuple[Path, Path | None], Engines] = {}


def engines_for(settings: Settings) -> Engines:
    """Moteurs chargés une fois par processus (sessions ONNX coûteuses à créer)."""
    key = (settings.models_dir, settings.idvision_path)
    if key not in _ENGINES:
        _ENGINES[key] = Engines(
            mrz=available_engines(settings), viz=available_viz_engines(settings)
        )
    return _ENGINES[key]


def _write_png(path: Path, image: RGBImage) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".png", cv2.cvtColor(image, cv2.COLOR_RGB2BGR))
    if not ok:
        raise JobError("IMAGE_ENCODE_FAILED")
    path.write_bytes(buf.tobytes())


def _write_json(path: Path, model: BaseModel | dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = model.model_dump(mode="json") if isinstance(model, BaseModel) else model
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _remove_previous(db: Session, settings: Settings, file_id: str, batch_dir: Path) -> None:
    pages = db.scalars(select(m.Page).where(m.Page.file_id == file_id)).all()
    page_ids = [p.id for p in pages]
    crops = db.scalars(select(m.Crop).where(m.Crop.page_id.in_(page_ids))).all() if pages else []
    # Documents de ces recadrages supprimés ; l'autre face d'une paire retrouve le sien.
    detach_crops(db, settings, [c.id for c in crops])
    for c in crops:
        (batch_dir / c.storage_path).unlink(missing_ok=True)
        shutil.rmtree(batch_dir / "artifacts" / c.id, ignore_errors=True)
    for p in pages:
        (batch_dir / p.storage_path).unlink(missing_ok=True)
        db.delete(p)
    db.flush()


def process_file(ctx: JobContext) -> None:
    settings = ctx.settings
    if ctx.job.ref_id is None:
        raise JobError("MISSING_REF")
    factory = make_session_factory(ctx.engine, immediate=False)
    with factory() as db:
        f = db.get(m.File, ctx.job.ref_id)
        if f is None:
            raise JobError("FILE_NOT_FOUND")
        file_id, batch_id, expected = f.id, f.batch_id, f.sha256
        path = settings.data_dir / f.storage_path
    ctx.report(0.02, "verify")
    if not path.is_file():
        raise JobError("FILE_MISSING_ON_DISK")
    if sha256_of(path) != expected:
        raise JobError("FILE_CHECKSUM_MISMATCH")

    engines = engines_for(settings)
    with factory() as db:
        threshold = app_settings.load(db, settings).auto_validate_threshold

    def progress(p: float, stage: str) -> None:
        ctx.check()
        ctx.report(0.05 + 0.85 * p, stage)

    try:
        fa = analyze_file(
            path,
            engines,
            today=date.today(),
            threshold=threshold,
            allow_specimen=settings.allow_specimen,
            progress=progress,
        )
    except IngestError as exc:
        raise JobError(exc.code) from exc
    ctx.check()
    ctx.report(0.92, "store")

    batch_dir = settings.data_dir / "batches" / batch_id
    with make_session_factory(ctx.engine, immediate=True)() as db:
        _remove_previous(db, settings, file_id, batch_dir)
        page_ids: list[str] = []
        for info, image in zip(fa.pages, fa.page_images, strict=True):
            page = m.Page(
                id=new_id(),
                file_id=file_id,
                page_index=info.page - 1,
                storage_path="",
                width=info.width,
                height=info.height,
                quality_json=info.model_dump(mode="json"),
            )
            page.storage_path = f"pages/{page.id}.png"
            _write_png(batch_dir / page.storage_path, image)
            db.add(page)
            page_ids.append(page.id)
        db.flush()
        statuses: dict[str, int] = {}
        for ca in fa.crops:
            crop = m.Crop(
                id=new_id(),
                page_id=page_ids[ca.page_index],
                quad_json=ca.quad,
                storage_path="",
                width=int(ca.image.shape[1]),
                height=int(ca.image.shape[0]),
                rotation=ca.rotation,
                side=ca.side,
                physical_format=ca.physical_format
                if ca.physical_format in {p.value for p in m.PhysicalFormat}
                else m.PhysicalFormat.UNKNOWN,
                has_mrz=ca.has_mrz,
                quality_json=ca.quality.model_dump(mode="json"),
            )
            crop.storage_path = f"crops/{crop.id}.png"
            _write_png(batch_dir / crop.storage_path, ca.image)
            art = batch_dir / "artifacts" / crop.id
            _write_json(art / "mrz.json", ca.mrz)
            _write_json(art / "viz.json", ca.viz)
            _write_json(art / "extract.json", ca.extraction)
            _write_json(art / "classify.json", ca.classification)
            _write_json(art / "result.json", ca.result)
            _write_json(art / "timings.json", {"ms": ca.ms, "detection": ca.detection_source})
            db.add(crop)
            db.flush()
            doc = create_document(
                db,
                batch_id=batch_id,
                result=ca.result,
                classification=ca.classification,
                mrz=ca.mrz,
                viz=ca.viz,
                front_crop_id=None if ca.side == "back" else crop.id,
                back_crop_id=crop.id if ca.side == "back" else None,
            )
            statuses[doc.status] = statuses.get(doc.status, 0) + 1
        # Appariement recto/verso quand le lot n'a plus reçu de fichier depuis 2 minutes.
        schedule_pairing(db, batch_id, PAIR_DELAY)
        db.commit()
    ctx.report(1.0, "done")
    log.info(
        "file processed file_id=%s pages=%d crops=%d statuses=%s engines=%s",
        file_id,
        len(fa.pages),
        len(fa.crops),
        statuses,
        ",".join(engines.names),
    )
