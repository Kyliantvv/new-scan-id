"""Documents : tableau, détail pour l'écran de vérification, corrections, actions, images.

Toute modification est journalisée dans `audit_log` avec l'avant/après (en base seulement,
jamais dans le journal technique). Les images sont servies authentifiées, sans cache.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from idreader.api.deps import AdminDep, AuthDep, DbDep, SettingsDep, api_error
from idreader.core import app_settings, audit
from idreader.core import models as m
from idreader.core.config import Settings
from idreader.core.db import utcnow
from idreader.core.rows import document_rows
from idreader.pipeline.consolidate import ALERT_LABELS, REASON_LABELS
from idreader.pipeline.mrz.decode import MrzResult
from idreader.pipeline.mrz.formats import SPECS, resolve_checks
from idreader.worker.queue import enqueue, schedule_pairing
from idreader.worker.store import PairError, mark_duplicates, merge_documents, unpair_document

log = logging.getLogger(__name__)
router = APIRouter(tags=["documents"])

FIELD_LABELS = {
    "surname": "Nom",
    "given_names": "Prénoms",
    "usage_name": "Nom d'usage",
    "sex": "Sexe",
    "date_of_birth": "Date de naissance",
    "place_of_birth": "Lieu de naissance",
    "nationality": "Nationalité",
    "document_number": "N° du document",
    "date_of_issue": "Date de délivrance",
    "date_of_expiry": "Date d'expiration",
    "authority": "Autorité de délivrance",
    "personal_number": "N° personnel",
}
EDITABLE = frozenset(FIELD_LABELS)
DATE_FIELDS = frozenset({"date_of_birth", "date_of_issue", "date_of_expiry"})
COUNTRY = re.compile(r"^[A-Z<]{1,3}$")
MAX_VALUE = 200


# --- Modèles ----------------------------------------------------------------------------


class Box(BaseModel):
    x: float
    y: float
    w: float
    h: float


class FieldOut(BaseModel):
    name: str
    label: str
    value: str | None
    value_native: str | None
    status: str
    confidence: float | None
    sources: list[str]
    reasons: list[str]
    candidates: list[str]
    bbox: Box | None
    side: str | None


class CropOut(BaseModel):
    id: str
    width: int
    height: int
    page_id: str
    file_id: str
    file_name: str
    page_index: int
    rotation: int


class MrzCheckOut(BaseModel):
    name: str
    line: int
    index: int
    ok: bool


class MrzOut(BaseModel):
    format: str
    lines: list[str]
    checks: list[MrzCheckOut]
    changed: list[dict[str, int]]
    side: str


class ReasonOut(BaseModel):
    code: str
    field: str | None
    label: str


class SuggestionOut(BaseModel):
    id: str
    score: float | None
    file_name: str | None
    crop_id: str | None


class DocumentOut(BaseModel):
    id: str
    batch_id: str
    status: str
    reasons: list[ReasonOut]
    alerts: list[str]
    alert_labels: list[str]
    doc_type: str | None
    issuing_state: str | None
    mrz_valid: bool | None
    confidence: float | None
    duplicate_of_id: str | None
    reviewed_at: str | None
    fields: list[FieldOut]
    front: CropOut | None
    back: CropOut | None
    mrz: MrzOut | None
    golden_enabled: bool
    paired: bool
    pair_score: float | None
    suggestion: SuggestionOut | None


class FieldsIn(BaseModel):
    fields: dict[str, str | None] = {}


class PairIn(BaseModel):
    first_id: str
    second_id: str


# --- Lecture ----------------------------------------------------------------------------


def get_document(db: Session, document_id: str) -> m.Document:
    doc = db.get(m.Document, document_id)
    if doc is None:
        raise api_error(404, "DOCUMENT_NOT_FOUND", "Document introuvable.")
    return doc


def _crop_out(db: Session, crop_id: str | None) -> tuple[CropOut | None, m.Crop | None]:
    if crop_id is None:
        return None, None
    row = db.execute(
        select(m.Crop, m.Page, m.File)
        .join(m.Page, m.Page.id == m.Crop.page_id)
        .join(m.File, m.File.id == m.Page.file_id)
        .where(m.Crop.id == crop_id)
    ).first()
    if row is None:
        return None, None
    crop, page, f = row
    out = CropOut(
        id=crop.id,
        width=crop.width,
        height=crop.height,
        page_id=page.id,
        file_id=f.id,
        file_name=f.original_name,
        page_index=page.page_index,
        rotation=crop.rotation,
    )
    return out, crop


def _artifact(settings: Settings, batch_id: str, crop_id: str, stage: str) -> Any | None:
    path = settings.data_dir / "batches" / batch_id / "artifacts" / crop_id / f"{stage}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _mrz_out(
    settings: Settings, doc: m.Document, crops: list[tuple[str, m.Crop | None]]
) -> MrzOut | None:
    for side, crop in crops:
        if crop is None or not crop.has_mrz:
            continue
        stage = _artifact(settings, doc.batch_id, crop.id, "mrz")
        if not stage or not stage.get("result"):
            continue
        result = MrzResult.model_validate(stage["result"])
        spec = SPECS[result.format]
        checks = [
            MrzCheckOut(
                name=c.name, line=c.digit[0], index=c.digit[1], ok=result.checks.get(c.name, False)
            )
            for c in resolve_checks(spec, result.lines)
        ]
        changed = [{"line": c.line, "index": c.index} for c in result.changes]
        return MrzOut(
            format=result.format.value,
            lines=result.lines,
            checks=checks,
            changed=changed,
            side=side,
        )
    return None


def _field_out(f: m.DocumentField, default_side: str | None) -> FieldOut:
    last = f.evidence_json[-1] if f.evidence_json and isinstance(f.evidence_json[-1], dict) else {}
    sources = list(last.get("sources", [])) if "sources" in last else []
    reasons = [REASON_LABELS.get(r, r) for r in last.get("reasons", [])]
    bbox = f.bbox_json or None
    return FieldOut(
        name=f.name,
        label=FIELD_LABELS.get(f.name, f.name),
        value=f.value,
        value_native=f.value_native,
        status=f.status,
        confidence=f.confidence,
        sources=sources,
        reasons=reasons,
        candidates=[str(c) for c in f.candidates_json],
        bbox=Box(**{k: bbox[k] for k in ("x", "y", "w", "h")}) if bbox else None,
        side=(bbox or {}).get("side", default_side) if bbox else default_side,
    )


def document_out(db: Session, settings: Settings, doc: m.Document) -> DocumentOut:
    front, front_crop = _crop_out(db, doc.front_crop_id)
    back, back_crop = _crop_out(db, doc.back_crop_id)
    default_side = "front" if front else ("back" if back else None)
    rows = {
        f.name: f
        for f in db.scalars(select(m.DocumentField).where(m.DocumentField.document_id == doc.id))
    }
    fields = []
    for name in FIELD_LABELS:
        if name in rows:
            fields.append(_field_out(rows[name], default_side))
        else:
            fields.append(
                FieldOut(
                    name=name,
                    label=FIELD_LABELS[name],
                    value=None,
                    value_native=None,
                    status="missing",
                    confidence=None,
                    sources=[],
                    reasons=[],
                    candidates=[],
                    bbox=None,
                    side=None,
                )
            )
    return DocumentOut(
        id=doc.id,
        batch_id=doc.batch_id,
        status=doc.status,
        reasons=[
            ReasonOut(code=r.get("code", ""), field=r.get("field"), label=r.get("label") or "")
            for r in doc.status_reasons_json
            if isinstance(r, dict)
        ],
        alerts=list(doc.alerts_json),
        alert_labels=[ALERT_LABELS.get(a, a) for a in doc.alerts_json],
        doc_type=doc.doc_type,
        issuing_state=doc.issuing_state,
        mrz_valid=doc.mrz_valid,
        confidence=doc.confidence,
        duplicate_of_id=doc.duplicate_of_id,
        reviewed_at=doc.reviewed_at.isoformat() if doc.reviewed_at else None,
        fields=fields,
        front=front,
        back=back,
        mrz=_mrz_out(settings, doc, [("front", front_crop), ("back", back_crop)]),
        golden_enabled=app_settings.load(db, settings).golden_enabled,
        paired=bool(doc.front_crop_id and doc.back_crop_id),
        pair_score=doc.pair_score,
        suggestion=_suggestion(db, doc),
    )


def _suggestion(db: Session, doc: m.Document) -> SuggestionOut | None:
    other = db.get(m.Document, doc.suggested_pair_id) if doc.suggested_pair_id else None
    if other is None:
        return None
    crop, _ = _crop_out(db, other.front_crop_id or other.back_crop_id)
    return SuggestionOut(
        id=other.id,
        score=doc.pair_score,
        file_name=crop.file_name if crop else None,
        crop_id=crop.id if crop else None,
    )


@router.get("/documents")
def list_documents(
    _auth: AuthDep,
    db: DbDep,
    batch: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    sort: Annotated[str | None, Query(max_length=40)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=5000)] = 1000,
) -> list[dict[str, Any]]:
    """Lignes du tableau (vue `documents_flat` + fichier source et aperçu)."""
    return document_rows(db, batch=batch, status=status, q=q, sort=sort, offset=offset, limit=limit)


@router.get("/documents/{document_id}")
def get_document_detail(
    document_id: str, _auth: AuthDep, db: DbDep, settings: SettingsDep
) -> DocumentOut:
    return document_out(db, settings, get_document(db, document_id))


# --- Corrections et actions ---------------------------------------------------------------


def _clean(name: str, value: str | None) -> str | None:
    if name not in EDITABLE:
        raise api_error(422, "FIELD_NOT_EDITABLE", "Ce champ ne peut pas être modifié.")
    if value is None:
        return None
    value = " ".join(value.split()).upper() if name != "authority" else " ".join(value.split())
    if value == "":
        return None
    if len(value) > MAX_VALUE:
        raise api_error(422, "VALUE_TOO_LONG", "Valeur trop longue.")
    if name in DATE_FIELDS:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise api_error(
                422, "INVALID_DATE", "Date invalide : format JJ/MM/AAAA attendu."
            ) from exc
    if name == "sex" and value not in ("M", "F", "X"):
        raise api_error(422, "INVALID_SEX", "Sexe : M, F ou X.")
    if name == "nationality" and not COUNTRY.match(value):
        raise api_error(422, "INVALID_COUNTRY", "Nationalité : code pays ICAO à 3 lettres.")
    return value


def apply_corrections(
    db: Session, doc: m.Document, changes: dict[str, str | None], user_id: str
) -> int:
    """Applique les corrections ; renvoie le nombre de champs réellement modifiés."""
    cleaned = {name: _clean(name, value) for name, value in changes.items()}
    rows = {
        f.name: f
        for f in db.scalars(select(m.DocumentField).where(m.DocumentField.document_id == doc.id))
    }
    changed = 0
    for name, value in cleaned.items():
        row = rows.get(name)
        before = row.value if row else None
        if before == value:
            continue
        if row is None:
            row = m.DocumentField(
                document_id=doc.id, name=name, evidence_json=[], candidates_json=[]
            )
            db.add(row)
        row.value = value
        row.status = m.FieldStatus.MANUAL if value is not None else m.FieldStatus.MISSING
        row.confidence = 1.0 if value is not None else 0.0
        row.evidence_json = [*row.evidence_json, {"sources": ["human"], "reasons": []}]
        if before is not None and before != value and before not in row.candidates_json:
            row.candidates_json = [before, *row.candidates_json][:5]
        audit.record(
            db,
            "field_corrected",
            user_id=user_id,
            entity="document",
            entity_id=doc.id,
            before={"field": name, "value": before},
            after={"field": name, "value": value},
        )
        changed += 1
    return changed


def _mark_reviewed(doc: m.Document, status: str, user_id: str) -> None:
    doc.status = status
    doc.reviewed_by = user_id
    doc.reviewed_at = utcnow()


@router.patch("/documents/{document_id}/fields")
def patch_fields(
    document_id: str, body: FieldsIn, auth: AuthDep, db: DbDep, settings: SettingsDep
) -> DocumentOut:
    """Correction en ligne : journalisée, le document passe en « Validé (manuel) »."""
    doc = get_document(db, document_id)
    if apply_corrections(db, doc, body.fields, auth.user_id):
        _mark_reviewed(doc, m.DocumentStatus.MANUAL_VALIDATED, auth.user_id)
    db.commit()
    log.info("document corrected document_id=%s fields=%d", doc.id, len(body.fields))
    return document_out(db, settings, doc)


@router.post("/documents/{document_id}/validate")
def validate_document(
    document_id: str, body: FieldsIn, auth: AuthDep, db: DbDep, settings: SettingsDep
) -> DocumentOut:
    doc = get_document(db, document_id)
    changed = apply_corrections(db, doc, body.fields, auth.user_id)
    before = doc.status
    _mark_reviewed(doc, m.DocumentStatus.MANUAL_VALIDATED, auth.user_id)
    audit.record(
        db, "document_validated", user_id=auth.user_id, entity="document", entity_id=doc.id,
        before={"status": before}, after={"status": doc.status, "fields_corrected": changed},
    )  # fmt: skip
    db.commit()
    log.info("document validated document_id=%s corrected=%d", doc.id, changed)
    return document_out(db, settings, doc)


@router.post("/documents/{document_id}/reject")
def reject_document(
    document_id: str, auth: AuthDep, db: DbDep, settings: SettingsDep
) -> DocumentOut:
    """« Pas une pièce d'identité » : statut Échec avec une raison explicite."""
    doc = get_document(db, document_id)
    before = doc.status
    _mark_reviewed(doc, m.DocumentStatus.FAILED, auth.user_id)
    doc.status_reasons_json = [
        {"code": "NOT_AN_ID_DOCUMENT", "field": None, "label": "Pas une pièce d'identité"}
    ]
    audit.record(
        db, "document_rejected", user_id=auth.user_id, entity="document", entity_id=doc.id,
        before={"status": before}, after={"status": doc.status},
    )  # fmt: skip
    db.commit()
    log.info("document rejected document_id=%s", doc.id)
    return document_out(db, settings, doc)


def _file_ids(db: Session, doc: m.Document) -> list[str]:
    crop_ids = [c for c in (doc.front_crop_id, doc.back_crop_id) if c]
    if not crop_ids:
        return []
    return list(
        db.scalars(
            select(m.Page.file_id)
            .join(m.Crop, m.Crop.page_id == m.Page.id)
            .where(m.Crop.id.in_(crop_ids))
            .distinct()
        )
    )


@router.post("/documents/{document_id}/reprocess", status_code=202)
def reprocess_document(document_id: str, auth: AuthDep, db: DbDep) -> Response:
    """Relance l'analyse des fichiers du document (leurs documents sont remplacés)."""
    doc = get_document(db, document_id)
    files = _file_ids(db, doc)
    if not files:
        raise api_error(409, "NO_SOURCE_FILE", "Aucun fichier source pour ce document.")
    for file_id in files:
        enqueue(db, m.JobType.PROCESS_FILE, ref_id=file_id, batch_id=doc.batch_id, priority=1)
    doc.status = m.DocumentStatus.PROCESSING
    audit.record(
        db, "document_reprocessed", user_id=auth.user_id, entity="document", entity_id=doc.id
    )
    db.commit()
    log.info("document reprocess queued document_id=%s files=%d", doc.id, len(files))
    return Response(status_code=202)


@router.delete("/documents/{document_id}", status_code=204)
def delete_document(document_id: str, auth: AuthDep, db: DbDep, settings: SettingsDep) -> Response:
    """Supprime le document, ses champs et ses recadrages (la page et le fichier restent)."""
    doc = get_document(db, document_id)
    batch_dir = settings.data_dir / "batches" / doc.batch_id
    crops = [db.get(m.Crop, c) for c in (doc.front_crop_id, doc.back_crop_id) if c]
    db.delete(doc)
    for crop in crops:
        if crop is None:
            continue
        others = db.scalar(
            select(m.Document.id).where(
                m.Document.id != document_id,
                or_(m.Document.front_crop_id == crop.id, m.Document.back_crop_id == crop.id),
            )
        )
        if others is None:
            (batch_dir / crop.storage_path).unlink(missing_ok=True)
            shutil.rmtree(batch_dir / "artifacts" / crop.id, ignore_errors=True)
            db.delete(crop)
    audit.record(
        db, "document_deleted", user_id=auth.user_id, entity="document", entity_id=document_id
    )
    db.commit()
    log.info("document deleted document_id=%s", document_id)
    return Response(status_code=204)


@router.post("/documents/{document_id}/golden", status_code=201)
def add_to_golden(document_id: str, auth: AdminDep, db: DbDep, settings: SettingsDep) -> Response:
    """Copie recadrages + valeurs (corrigées) dans data/golden/ (hors git, disque chiffré)."""
    if not app_settings.load(db, settings).golden_enabled:
        raise api_error(403, "GOLDEN_DISABLED", "L'ajout au jeu de test est désactivé (réglages).")
    doc = get_document(db, document_id)
    if doc.status not in (m.DocumentStatus.MANUAL_VALIDATED, m.DocumentStatus.AUTO_VALIDATED):
        raise api_error(
            409, "NOT_VALIDATED", "Validez le document avant de l'ajouter au jeu de test."
        )
    dest = settings.data_dir / "golden" / "cases" / doc.id
    dest.mkdir(parents=True, exist_ok=True)
    batch_dir = settings.data_dir / "batches" / doc.batch_id
    images = {}
    for side, crop_id in (("front", doc.front_crop_id), ("back", doc.back_crop_id)):
        crop = db.get(m.Crop, crop_id) if crop_id else None
        if crop is not None and (batch_dir / crop.storage_path).is_file():
            shutil.copyfile(batch_dir / crop.storage_path, dest / f"{side}.png")
            images[side] = f"{side}.png"
    truth = {
        "document_id": doc.id,
        "doc_type": doc.doc_type,
        "issuing_state": doc.issuing_state,
        "mrz_lines": list(doc.mrz_lines_json),
        "images": images,
        "fields": {
            f.name: f.value
            for f in db.scalars(
                select(m.DocumentField).where(m.DocumentField.document_id == doc.id)
            )
        },
    }
    (dest / "truth.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    audit.record(db, "golden_added", user_id=auth.user_id, entity="document", entity_id=doc.id)
    db.commit()
    log.info("document added to golden document_id=%s", doc.id)
    return Response(status_code=201)


# --- Recto / verso ------------------------------------------------------------------------

PAIR_ERRORS = {
    "PAIR_INVALID": "Association impossible : même document ou lots différents.",
    "PAIR_ALREADY_PAIRED": "L'un des documents a déjà un recto et un verso : dissociez-le d'abord.",
    "PAIR_TWO_MRZ": "Les deux faces portent une MRZ : ce sont deux documents distincts.",
    "PAIR_ARTIFACTS_MISSING": "Analyse introuvable pour l'une des faces : relancez l'analyse.",
    "NOT_PAIRED": "Ce document n'a pas de recto et de verso associés.",
}


@router.post("/documents/pair")
def pair_documents(body: PairIn, auth: AuthDep, db: DbDep, settings: SettingsDep) -> DocumentOut:
    """Association manuelle recto/verso (touche P de l'écran de vérification)."""
    first = get_document(db, body.first_id)
    second = get_document(db, body.second_id)
    try:
        doc = merge_documents(db, settings, first, second, user_id=auth.user_id)
    except PairError as exc:
        raise api_error(409, exc.code, PAIR_ERRORS[exc.code]) from exc
    mark_duplicates(db, doc.batch_id)
    db.commit()
    return document_out(db, settings, doc)


@router.post("/documents/{document_id}/unpair")
def unpair(document_id: str, auth: AuthDep, db: DbDep, settings: SettingsDep) -> list[str]:
    doc = get_document(db, document_id)
    batch_id = doc.batch_id
    try:
        restored = unpair_document(db, settings, doc, user_id=auth.user_id)
    except PairError as exc:
        raise api_error(409, exc.code, PAIR_ERRORS[exc.code]) from exc
    mark_duplicates(db, batch_id)
    db.commit()
    return [d.id for d in restored]


@router.post("/batches/{batch_id}/pair", status_code=202)
def run_pairing(batch_id: str, auth: AuthDep, db: DbDep) -> Response:
    """Bouton « Lancer l'appariement » : tout de suite si les analyses sont terminées."""
    if db.get(m.Batch, batch_id) is None:
        raise api_error(404, "BATCH_NOT_FOUND", "Lot introuvable.")
    schedule_pairing(db, batch_id, timedelta(0))
    audit.record(db, "pairing_requested", user_id=auth.user_id, entity="batch", entity_id=batch_id)
    db.commit()
    return Response(status_code=202)


# --- Images -------------------------------------------------------------------------------


def _image(settings: Settings, batch_id: str, rel: str, media_type: str) -> FileResponse:
    base = (settings.data_dir / "batches" / batch_id).resolve()
    path = (base / rel).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise api_error(404, "IMAGE_NOT_FOUND", "Image introuvable (purgée ?).")
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "no-store"})


@router.get("/crops/{crop_id}/image")
def crop_image(crop_id: str, _auth: AuthDep, db: DbDep, settings: SettingsDep) -> FileResponse:
    row = db.execute(
        select(m.Crop.storage_path, m.File.batch_id)
        .join(m.Page, m.Page.id == m.Crop.page_id)
        .join(m.File, m.File.id == m.Page.file_id)
        .where(m.Crop.id == crop_id)
    ).first()
    if row is None:
        raise api_error(404, "IMAGE_NOT_FOUND", "Image introuvable.")
    return _image(settings, row[1], row[0], "image/png")


@router.get("/pages/{page_id}/image")
def page_image(page_id: str, _auth: AuthDep, db: DbDep, settings: SettingsDep) -> FileResponse:
    row = db.execute(
        select(m.Page.storage_path, m.File.batch_id)
        .join(m.File, m.File.id == m.Page.file_id)
        .where(m.Page.id == page_id)
    ).first()
    if row is None:
        raise api_error(404, "IMAGE_NOT_FOUND", "Image introuvable.")
    return _image(settings, row[1], row[0], "image/png")


@router.get("/files/{file_id}/original")
def file_original(file_id: str, _auth: AuthDep, db: DbDep, settings: SettingsDep) -> FileResponse:
    f = db.get(m.File, file_id)
    if f is None:
        raise api_error(404, "FILE_NOT_FOUND", "Fichier introuvable.")
    base = settings.data_dir.resolve()
    path = (base / f.storage_path).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise api_error(404, "FILE_NOT_FOUND", "Fichier introuvable (purgé ?).")
    return FileResponse(path, media_type=f.mime, headers={"Cache-Control": "no-store"})


def batch_dir(settings: Settings, batch_id: str) -> Path:
    return settings.data_dir / "batches" / batch_id
