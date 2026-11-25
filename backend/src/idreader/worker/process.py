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
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from idreader.core import models as m
from idreader.core.config import Settings
from idreader.core.db import make_session_factory, new_id
from idreader.engines.registry import available_engines, available_viz_engines
from idreader.pipeline.analyze import CropAnalysis, Engines, analyze_file
from idreader.pipeline.consolidate import DocState
from idreader.pipeline.ingest import IngestError, RGBImage
from idreader.worker.handlers import JobContext, JobError, sha256_of

log = logging.getLogger(__name__)

_ENGINES: dict[tuple[Path, Path | None], Engines] = {}

DOC_STATUS = {
    DocState.AUTO_VALIDATED: m.DocumentStatus.AUTO_VALIDATED,
    DocState.TO_REVIEW: m.DocumentStatus.TO_REVIEW,
    DocState.FAILED: m.DocumentStatus.FAILED,
}


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


def _field_bbox(ca: CropAnalysis, slot_ids: list[int]) -> dict[str, Any] | None:
    boxes = [s.bbox for s in ca.viz.slots if s.id in slot_ids]
    if not boxes:
        return None
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[2] for b in boxes)
    y1 = max(b[3] for b in boxes)
    return {"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0}


def _remove_previous(db: Session, file_id: str, batch_dir: Path) -> None:
    pages = db.scalars(select(m.Page).where(m.Page.file_id == file_id)).all()
    page_ids = [p.id for p in pages]
    crops = db.scalars(select(m.Crop).where(m.Crop.page_id.in_(page_ids))).all() if pages else []
    crop_ids = [c.id for c in crops]
    if crop_ids:
        for doc in db.scalars(
            select(m.Document).where(
                or_(m.Document.front_crop_id.in_(crop_ids), m.Document.back_crop_id.in_(crop_ids))
            )
        ):
            db.delete(doc)
    for c in crops:
        (batch_dir / c.storage_path).unlink(missing_ok=True)
        shutil.rmtree(batch_dir / "artifacts" / c.id, ignore_errors=True)
    for p in pages:
        (batch_dir / p.storage_path).unlink(missing_ok=True)
        db.delete(p)
    db.flush()


def _document(ca: CropAnalysis, batch_id: str, crop_id: str) -> m.Document:
    r = ca.result
    mrz = ca.mrz.result
    doc = m.Document(
        id=new_id(),
        batch_id=batch_id,
        status=DOC_STATUS[r.status],
        status_reasons_json=[
            {"code": x.code, "field": x.field, "label": x.label} for x in r.reasons
        ],
        doc_type=r.doc_type.value,
        doc_code=ca.classification.doc_code,
        issuing_state=r.issuing_state,
        mrz_format=mrz.format.value if mrz else None,
        mrz_lines_json=list(mrz.lines) if mrz else [],
        mrz_valid=r.mrz_valid,
        confidence=r.confidence,
        alerts_json=list(r.alerts),
    )
    if ca.side == "back":
        doc.back_crop_id = crop_id
    else:
        doc.front_crop_id = crop_id
    return doc


def _fields(ca: CropAnalysis, doc_id: str) -> list[m.DocumentField]:
    out = []
    for name in m.FIELD_NAMES:
        f = ca.result.fields.get(name)
        if f is None:
            continue
        out.append(
            m.DocumentField(
                document_id=doc_id,
                name=name,
                value=f.value,
                value_native=f.value_native,
                confidence=f.confidence,
                status=f.status.value,
                evidence_json=[*f.evidence, {"sources": f.sources, "reasons": f.reasons}],
                candidates_json=list(f.candidates),
                bbox_json=_field_bbox(ca, f.slot_ids),
            )
        )
    return out


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

    def progress(p: float, stage: str) -> None:
        ctx.check()
        ctx.report(0.05 + 0.85 * p, stage)

    try:
        fa = analyze_file(
            path,
            engines,
            today=date.today(),
            threshold=settings.auto_validate_threshold,
            allow_specimen=settings.allow_specimen,
            progress=progress,
        )
    except IngestError as exc:
        raise JobError(exc.code) from exc
    ctx.check()
    ctx.report(0.92, "store")

    batch_dir = settings.data_dir / "batches" / batch_id
    with make_session_factory(ctx.engine, immediate=True)() as db:
        _remove_previous(db, file_id, batch_dir)
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
            doc = _document(ca, batch_id, crop.id)
            db.add(doc)
            db.flush()
            db.add_all(_fields(ca, doc.id))
            statuses[doc.status] = statuses.get(doc.status, 0) + 1
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
