"""Écriture des documents en base à partir des résultats du pipeline.

- un document par recadrage après `process_file` ;
- fusion de deux recadrages en un document recto/verso (appariement automatique ou manuel) ;
- dissociation : chaque face retrouve son document d'origine, relu depuis ses artefacts JSON ;
- doublons de document (pays + n° + date de naissance) dans le lot : alerte + lien.

Aucune donnée personnelle dans les logs : identifiants seulement.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from idreader.core import app_settings, audit
from idreader.core import models as m
from idreader.core.config import Settings
from idreader.core.db import new_id
from idreader.pipeline.analyze import CropEvidence, consolidate_pair
from idreader.pipeline.classify import Classification
from idreader.pipeline.consolidate import DocState, DocumentResult
from idreader.pipeline.mrz.stage import MrzStage
from idreader.pipeline.pairing import unshift_bbox
from idreader.pipeline.viz_ocr import Slot, VizOcr

log = logging.getLogger(__name__)

DOC_STATUS = {
    DocState.AUTO_VALIDATED: m.DocumentStatus.AUTO_VALIDATED,
    DocState.TO_REVIEW: m.DocumentStatus.TO_REVIEW,
    DocState.FAILED: m.DocumentStatus.FAILED,
}


def artifact_dir(settings: Settings, batch_id: str, crop_id: str) -> Path:
    return settings.data_dir / "batches" / batch_id / "artifacts" / crop_id


def _bbox(slots: list[Slot], slot_ids: list[int], default_side: str) -> dict[str, Any] | None:
    boxes = [unshift_bbox(s) for s in slots if s.id in slot_ids]
    if not boxes:
        return None
    # Un champ peut couvrir plusieurs cadres : on garde ceux de la face du premier.
    side = boxes[0][0]
    same = [b for s, b in boxes if s == side]
    x0 = min(b[0] for b in same)
    y0 = min(b[1] for b in same)
    x1 = max(b[2] for b in same)
    y1 = max(b[3] for b in same)
    paired = any(s.id >= 100_000 for s in slots)
    return {
        "x": x0,
        "y": y0,
        "w": x1 - x0,
        "h": y1 - y0,
        "side": side if paired else default_side,
    }


def create_document(
    db: Session,
    *,
    batch_id: str,
    result: DocumentResult,
    classification: Classification,
    mrz: MrzStage,
    viz: VizOcr,
    front_crop_id: str | None,
    back_crop_id: str | None,
) -> m.Document:
    """Nouveau document et ses champs (statut, motifs, alertes, preuves, cadres)."""
    r = result
    res = mrz.result
    doc = m.Document(
        id=new_id(),
        batch_id=batch_id,
        status=DOC_STATUS[r.status],
        status_reasons_json=[
            {"code": x.code, "field": x.field, "label": x.label} for x in r.reasons
        ],
        doc_type=r.doc_type.value,
        doc_code=classification.doc_code,
        issuing_state=r.issuing_state,
        mrz_format=res.format.value if res else None,
        mrz_lines_json=list(res.lines) if res else [],
        mrz_valid=r.mrz_valid,
        confidence=r.confidence,
        alerts_json=list(r.alerts),
        front_crop_id=front_crop_id,
        back_crop_id=back_crop_id,
    )
    db.add(doc)
    db.flush()
    default_side = "front" if front_crop_id else "back"
    for name in m.FIELD_NAMES:
        f = r.fields.get(name)
        if f is None:
            continue
        db.add(
            m.DocumentField(
                document_id=doc.id,
                name=name,
                value=f.value,
                value_native=f.value_native,
                confidence=f.confidence,
                status=f.status.value,
                evidence_json=[*f.evidence, {"sources": f.sources, "reasons": f.reasons}],
                candidates_json=list(f.candidates),
                bbox_json=_bbox(viz.slots, f.slot_ids, default_side),
            )
        )
    db.flush()
    return doc


# --- Artefacts d'une face -----------------------------------------------------------------


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_evidence(settings: Settings, batch_id: str, crop: m.Crop) -> CropEvidence | None:
    d = artifact_dir(settings, batch_id, crop.id)
    try:
        result = DocumentResult.model_validate(_read(d / "result.json"))
        return CropEvidence(
            mrz=MrzStage.model_validate(_read(d / "mrz.json")),
            viz=VizOcr.model_validate(_read(d / "viz.json")),
            classification=Classification.model_validate(_read(d / "classify.json")),
            quality_alerts=list((crop.quality_json or {}).get("alerts", [])),
            mrz_glare=any(x.code == "QUALITY_GLARE_MRZ" for x in result.reasons),
        )
    except (OSError, ValueError):
        return None


def restore_single(
    db: Session, settings: Settings, batch_id: str, crop: m.Crop
) -> m.Document | None:
    """Document d'une seule face, tel que `process_file` l'avait produit."""
    d = artifact_dir(settings, batch_id, crop.id)
    ev = load_evidence(settings, batch_id, crop)
    if ev is None:
        return None
    result = DocumentResult.model_validate(_read(d / "result.json"))
    back = crop.side == m.Side.BACK
    return create_document(
        db,
        batch_id=batch_id,
        result=result,
        classification=ev.classification,
        mrz=ev.mrz,
        viz=ev.viz,
        front_crop_id=None if back else crop.id,
        back_crop_id=crop.id if back else None,
    )


def document_crops(db: Session, doc: m.Document) -> list[m.Crop]:
    ids = [c for c in (doc.front_crop_id, doc.back_crop_id) if c]
    return [c for c in (db.get(m.Crop, i) for i in ids) if c is not None]


# --- Fusion et dissociation ---------------------------------------------------------------


class PairError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def merge_documents(
    db: Session,
    settings: Settings,
    first: m.Document,
    second: m.Document,
    *,
    user_id: str | None,
    score: float | None = None,
    today: date | None = None,
) -> m.Document:
    """Fusionne deux documents d'une face en un document recto/verso.

    La face qui porte une MRZ de carte (TD1) devient le verso ; sinon l'ordre donné.
    """
    if first.batch_id != second.batch_id or first.id == second.id:
        raise PairError("PAIR_INVALID")
    crops = [document_crops(db, first), document_crops(db, second)]
    if any(len(c) != 1 for c in crops):
        raise PairError("PAIR_ALREADY_PAIRED")
    a, b = crops[0][0], crops[1][0]
    ev_a = load_evidence(settings, first.batch_id, a)
    ev_b = load_evidence(settings, first.batch_id, b)
    if ev_a is None or ev_b is None:
        raise PairError("PAIR_ARTIFACTS_MISSING")
    if ev_a.mrz.result is not None and ev_b.mrz.result is not None:
        raise PairError("PAIR_TWO_MRZ")
    if ev_b.mrz.result is not None:
        (a, ev_a), (b, ev_b) = (b, ev_b), (a, ev_a)
    # a : face avec MRZ éventuelle (verso d'une TD1), b : l'autre face.
    if ev_a.mrz.result is not None and ev_a.mrz.result.format.value != "TD1":
        front, back, ev_front, ev_back = a, b, ev_a, ev_b
    else:
        front, back, ev_front, ev_back = b, a, ev_b, ev_a
    threshold = app_settings.load(db, settings).auto_validate_threshold
    pa = consolidate_pair(
        ev_front,
        ev_back,
        today=today or date.today(),
        threshold=threshold,
        allow_specimen=settings.allow_specimen,
    )
    batch_id = first.batch_id
    old = [first.id, second.id]
    db.delete(first)
    db.delete(second)
    db.flush()
    front.side, back.side = m.Side.FRONT, m.Side.BACK
    doc = create_document(
        db,
        batch_id=batch_id,
        result=pa.result,
        classification=pa.classification,
        mrz=pa.mrz,
        viz=pa.viz,
        front_crop_id=front.id,
        back_crop_id=back.id,
    )
    doc.pair_score = score
    audit.record(
        db,
        "documents_paired",
        user_id=user_id,
        entity="document",
        entity_id=doc.id,
        before={"documents": old},
        after={"front_crop": front.id, "back_crop": back.id, "score": score},
    )
    log.info("documents paired document_id=%s auto=%s", doc.id, user_id is None)
    return doc


def unpair_document(
    db: Session, settings: Settings, doc: m.Document, *, user_id: str | None
) -> list[m.Document]:
    crops = document_crops(db, doc)
    if len(crops) != 2:
        raise PairError("NOT_PAIRED")
    batch_id = doc.batch_id
    db.delete(doc)
    db.flush()
    restored = [d for d in (restore_single(db, settings, batch_id, c) for c in crops) if d]
    audit.record(
        db,
        "documents_unpaired",
        user_id=user_id,
        entity="document",
        entity_id=doc.id,
        after={"documents": [d.id for d in restored]},
    )
    log.info("document unpaired document_id=%s", doc.id)
    return restored


def detach_crops(db: Session, settings: Settings, crop_ids: list[str]) -> None:
    """Avant de supprimer des recadrages : leurs documents disparaissent, et l'autre face
    d'un document apparié retrouve son propre document."""
    if not crop_ids:
        return
    docs = db.scalars(
        select(m.Document).where(
            or_(m.Document.front_crop_id.in_(crop_ids), m.Document.back_crop_id.in_(crop_ids))
        )
    ).all()
    for doc in docs:
        others = [c for c in document_crops(db, doc) if c.id not in crop_ids]
        batch_id = doc.batch_id
        db.delete(doc)
        db.flush()
        for crop in others:
            restore_single(db, settings, batch_id, crop)
    db.flush()


# --- Doublons de document -----------------------------------------------------------------


def mark_duplicates(db: Session, batch_id: str) -> int:
    """Même pays + n° + date de naissance dans le lot : le plus ancien est l'original, les
    autres portent l'alerte DUPLICATE_DOCUMENT et `duplicate_of_id` (01 § Statuts)."""
    docs = db.scalars(
        select(m.Document).where(m.Document.batch_id == batch_id).order_by(m.Document.created_at)
    ).all()
    values: dict[str, dict[str, str | None]] = {d.id: {} for d in docs}
    for f in db.scalars(
        select(m.DocumentField).where(
            m.DocumentField.document_id.in_(list(values)),
            m.DocumentField.name.in_(("document_number", "date_of_birth")),
        )
    ):
        values[f.document_id][f.name] = f.value
    first: dict[tuple[str, str, str], str] = {}
    count = 0
    for d in docs:
        v = values[d.id]
        number, dob = v.get("document_number"), v.get("date_of_birth")
        key = (d.issuing_state or "", number or "", dob or "")
        alerts = [a for a in d.alerts_json if a != "DUPLICATE_DOCUMENT"]
        if all(key) and key in first:
            d.duplicate_of_id = first[key]
            d.alerts_json = [*alerts, "DUPLICATE_DOCUMENT"]
            count += 1
        else:
            if all(key):
                first[key] = d.id
            if d.duplicate_of_id is not None or len(alerts) != len(d.alerts_json):
                d.duplicate_of_id = None
                d.alerts_json = alerts
    db.flush()
    return count
