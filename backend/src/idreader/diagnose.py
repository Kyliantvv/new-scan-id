"""Diagnostic d'un lot : où le pipeline casse, sans aucune donnée personnelle.

`idreader diagnose --batch <id>` lit la base et les artefacts JSON (`mrz.json`, `viz.json`,
`extract.json`, `classify.json`) d'un lot et en sort des CODES, des COMPTEURS et des DRAPEAUX :
type détecté, nombre de recadrages, MRZ trouvée / valide, format, sommes en échec, étape où
ça casse, source de chaque champ. Jamais une valeur lue, un nom de fichier, un nom de lot,
un numéro ni une ligne de MRZ (règle 2 de CLAUDE.md).

Les valeurs finales des champs ne servent qu'à calculer des drapeaux (champ trop court, chiffres
dans un nom, libellé numéroté pris pour une valeur…) ; seule leur *forme* (« (9A) ») est donnée,
et seulement pour un champ suspect.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from idreader.core import models as m
from idreader.core.config import Settings
from idreader.pipeline.extract import fold, label_field

NAME_FIELDS = ("surname", "given_names", "usage_name")
DATE_FIELDS = ("date_of_birth", "date_of_issue", "date_of_expiry")
# Codes de type de document : une valeur égale à l'un d'eux n'est pas un nom.
_DOC_CODE_WORDS = {"P", "ID", "I", "PP", "PASSEPORT", "PASSPORT", "CNI", "FRA", "TYPE"}
_NUMBERING = re.compile(r"\(\s*\d{1,2}\s*[a-z]?\s*\)|^\s*\d{1,2}\s*[.)]", re.IGNORECASE)
_USAGE_MARK = re.compile(r"\b(EP|EPSE|EPOUSE|EPOUX|USAGE|NEE|NE|VEUVE|VVE|DIVORCEE)\b")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class FieldDiag(BaseModel):
    status: str
    confidence: float | None
    source: str  # mrz | viz:<méthode> | mrz+viz:<méthode> | human | none
    length: int = 0
    flags: list[str] = Field(default_factory=list)
    shape: str | None = None  # forme du texte (« (9A) »), seulement si le champ est suspect


class CropDiag(BaseModel):
    crop_id: str
    file_no: int  # rang de dépôt du fichier dans le lot
    page: int
    side: str
    physical_format: str
    doc_type: str | None
    doc_code: str | None
    issuing_state: str | None
    probable_type: bool
    mrz_found: bool
    mrz_failure: str | None
    mrz_format: str | None
    mrz_valid: bool | None
    failed_checks: list[str]
    mrz_reasons: list[str]
    mrz_changes: int
    mrz_engines: list[str]
    viz_slots: int
    viz_engines: list[str]
    quality_alerts: list[str]
    stage: str  # première étape en cause, « aucune » si tout est passé
    # Géométrie (aucune donnée personnelle) : taille du recadrage et de la page, part de la page
    # couverte par le quadrilatère détecté, rotation, nombre de recadrages sur la même page.
    crop_px: tuple[int, int] = (0, 0)
    page_px: tuple[int, int] = (0, 0)
    quad_area_pct: float | None = None
    rotation: int = 0
    detection: str | None = None
    crops_on_page: int = 1
    mrz_band_source: str | None = None
    # Champs suspects : « surname:label val@(x%,y%) lab@(x%,y%) » (positions dans le recadrage).
    geometry: list[str] = Field(default_factory=list)
    # Sonde du repli OCR pour la MRZ, par moteur : lignes vues, lignes plausibles, presque.
    mrz_probe: dict[str, str] = Field(default_factory=dict)


class DocumentDiag(BaseModel):
    document_id: str
    status: str
    reasons: list[str]
    alerts: list[str]
    faces: str  # front | back | both | none
    pairing: str  # paired | single | suggested
    pair_score: float | None
    doc_type: str | None
    issuing_state: str | None
    stage: str
    crops: list[CropDiag]
    fields: dict[str, FieldDiag]


class JobDiag(BaseModel):
    type: str
    status: str
    error_code: str | None
    attempts: int
    count: int
    due_in_s: int | None = None  # job en attente : secondes avant l'heure prévue (< 0 : en retard)


class BatchDiagnosis(BaseModel):
    batch_id: str
    files: int
    files_without_crop: int
    pages: int
    crops: int
    crops_per_file: dict[str, int]
    documents: list[DocumentDiag]
    jobs: list[JobDiag]
    summary: dict[str, dict[str, int]]


# --- Calcul des drapeaux -------------------------------------------------------------


def shape_of(value: str) -> str:
    """Forme d'un texte : majuscules → A, minuscules → a, chiffres → 9, espace → _, le reste tel
    quel ; les suites identiques sont réduites à un seul signe."""
    out: list[str] = []
    for ch in value:
        if ch.isalpha():
            c = "A" if ch.isupper() else "a"
        elif ch.isdigit():
            c = "9"
        elif ch.isspace():
            c = "_"
        else:
            c = ch
        if not out or out[-1] != c:
            out.append(c)
    return "".join(out)


def value_flags(name: str, value: str | None, doc_code: str | None) -> list[str]:
    """Drapeaux d'anomalie d'une valeur. Ne renvoie que des codes."""
    if not value:
        return []
    flags: list[str] = []
    folded = fold(value)
    if _NUMBERING.search(value):
        flags.append("NUMBERING")  # « Prénoms (2) », « (6Z) » : un libellé numéroté, pas une valeur
    if folded and label_field(value)[0] is not None and not _NUMBERING.search(value):
        flags.append("LABEL_WORD")
    if name in NAME_FIELDS:
        letters = sum(c.isalpha() for c in value)
        if letters < 2:
            flags.append("SHORT")
        if any(c.isdigit() for c in value):
            flags.append("DIGITS")
        if any(c.islower() for c in value):
            flags.append("LOWERCASE")
        if folded in _DOC_CODE_WORDS or (doc_code and folded == doc_code.replace("<", "")):
            flags.append("DOC_CODE")
        if re.search(r"[^\w\s'’,.\-]", value):
            flags.append("ODD_CHAR")
    if name in ("surname", "given_names") and _USAGE_MARK.search(folded):
        flags.append("USAGE_MARK")  # « ép. », « usage » : va dans le nom d'usage
    if name in DATE_FIELDS and not _ISO_DATE.match(value):
        flags.append("DATE_FORMAT")
    return flags


def source_tag(status: str, evidence: Iterable[dict[str, Any]]) -> str:
    if status == "manual":
        return "human"
    parts: list[str] = []
    viz = ""
    for ev in evidence:
        src = ev.get("src")
        if src == "mrz" and "mrz" not in parts:
            parts.append("mrz")
        elif src == "viz":
            viz = f"viz:{ev.get('method', '?')}"
    if viz:
        parts.append(viz)
    return "+".join(parts) or "none"


# --- Étape en cause ------------------------------------------------------------------


def break_stage(
    *,
    mrz: dict[str, Any] | None,
    viz_slots: int,
    doc_type: str | None,
    side: str,
    field_flags: dict[str, list[str]],
) -> str:
    """Première étape en cause pour un recadrage."""
    if mrz is None:
        return "?(mrz.json absent)"
    failure = mrz.get("failure")
    if failure == "NO_ENGINE":
        return "E: aucun moteur"
    if failure == "MRZ_NOT_FOUND":
        # Une MRZ est attendue sur un passeport et au verso d'une CNI ; sur un recto de CNI, non.
        expected = doc_type == "passport" or side == "back"
        return "E.1 MRZ non localisée" if expected else "E.1 pas de MRZ (recto ?)"
    if failure == "MRZ_UNREADABLE":
        return "E.2-E.4 MRZ illisible"
    result = mrz.get("result")
    if result is not None and not result.get("checksums_valid", False):
        return "E.5 sommes de contrôle"
    if viz_slots == 0:
        return "G: zone visuelle sans texte"
    bad = [n for n in NAME_FIELDS if field_flags.get(n)]
    if bad:
        return "H/L: " + ",".join(bad)
    return "aucune"


# --- Lecture du lot ------------------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _polygon_area(points: list[list[float]]) -> float:
    n = len(points)
    return (
        abs(
            sum(
                points[i][0] * points[(i + 1) % n][1] - points[(i + 1) % n][0] * points[i][1]
                for i in range(n)
            )
        )
        / 2
    )


def _center_pct(quad: list[list[float]], width: int, height: int) -> str:
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    return f"({round(100 * cx / max(width, 1))}%,{round(100 * cy / max(height, 1))}%)"


def _probe_summary(probe: dict[str, Any]) -> dict[str, str]:
    """« lignes=61 mrz=2[(y%,car.,larg.%)…] proches=0 » par moteur : positions et longueurs."""
    out: dict[str, str] = {}
    for engine, p in probe.items():
        like = [tuple(c) for c in p.get("mrz_like", [])]
        near = [tuple(c) for c in p.get("near", [])]
        out[engine] = f"lignes={p.get('lines')} mrz={len(like)}{like} proches={len(near)}{near}"
    return out


def _suspect_geometry(art: Path, width: int, height: int, doc_code: str | None) -> list[str]:
    """Pour chaque champ suspect de l'extraction (H) : méthode et positions relatives du cadre de
    la valeur et du cadre du libellé. Aucun texte."""
    extract = _read_json(art / "extract.json") or {}
    viz = _read_json(art / "viz.json") or {}
    slots = {s["id"]: s for s in viz.get("slots") or []}
    label_slots: dict[str, list[int]] = extract.get("label_slots") or {}
    out: list[str] = []
    for name, f in (extract.get("fields") or {}).items():
        flags = value_flags(name, f.get("value"), doc_code)
        if not flags:
            continue
        value_ids = f.get("slot_ids") or []
        vpos = ",".join(
            _center_pct(slots[i]["quad"]["points"], width, height) for i in value_ids if i in slots
        )
        lpos = ",".join(
            _center_pct(slots[i]["quad"]["points"], width, height)
            for i in label_slots.get(name, [])
            if i in slots
        )
        out.append(
            f"{name}:{f.get('method')} {','.join(flags)} valeur@{vpos or '?'} libellé@{lpos or '-'}"
        )
    return out


def _crop_diag(
    crop: m.Crop,
    page: m.Page,
    file: m.File,
    art: Path,
    field_flags: dict[str, list[str]],
    crops_on_page: int = 1,
) -> CropDiag:
    mrz = _read_json(art / "mrz.json")
    viz = _read_json(art / "viz.json") or {}
    classify = _read_json(art / "classify.json") or {}
    result = (mrz or {}).get("result") or {}
    checks: dict[str, bool] = result.get("checks") or {}
    slots = viz.get("slots") or []
    doc_type = classify.get("doc_type")
    timings = _read_json(art / "timings.json") or {}
    quad_pts = crop.quad_json if isinstance(crop.quad_json, list) else []
    page_area = max(page.width * page.height, 1)
    return CropDiag(
        crop_id=crop.id,
        file_no=file.upload_order,
        page=page.page_index + 1,
        side=crop.side,
        physical_format=crop.physical_format,
        doc_type=doc_type,
        doc_code=classify.get("doc_code"),
        issuing_state=classify.get("issuing_state"),
        probable_type=bool(classify.get("probable", False)),
        mrz_found=bool(mrz and mrz.get("band") is not None),
        mrz_failure=(mrz or {}).get("failure"),
        mrz_format=result.get("format"),
        mrz_valid=result.get("checksums_valid") if result else None,
        failed_checks=sorted(k for k, ok in checks.items() if not ok),
        mrz_reasons=list(result.get("reasons") or []),
        mrz_changes=len(result.get("changes") or []),
        mrz_engines=list((mrz or {}).get("engines") or []),
        viz_slots=len(slots),
        viz_engines=list(viz.get("engines") or []),
        quality_alerts=list((crop.quality_json or {}).get("alerts") or []),
        stage=break_stage(
            mrz=mrz,
            viz_slots=len(slots),
            doc_type=doc_type,
            side=crop.side,
            field_flags=field_flags,
        ),
        crop_px=(crop.width, crop.height),
        page_px=(page.width, page.height),
        quad_area_pct=round(100 * _polygon_area(quad_pts) / page_area, 1) if quad_pts else None,
        rotation=crop.rotation,
        detection=timings.get("detection"),
        crops_on_page=crops_on_page,
        mrz_band_source=((mrz or {}).get("band") or {}).get("source"),
        mrz_probe=_probe_summary((mrz or {}).get("probe") or {}),
        geometry=_suspect_geometry(art, crop.width, crop.height, classify.get("doc_code")),
    )


def diagnose_batch(db: Session, settings: Settings, batch_id: str) -> BatchDiagnosis:
    batch = db.get(m.Batch, batch_id)
    if batch is None:
        raise LookupError("BATCH_NOT_FOUND")
    files = db.scalars(
        select(m.File).where(m.File.batch_id == batch_id).order_by(m.File.upload_order)
    ).all()
    file_by_id = {f.id: f for f in files}
    pages = (
        db.scalars(select(m.Page).where(m.Page.file_id.in_(list(file_by_id)))).all()
        if file_by_id
        else []
    )
    page_by_id = {p.id: p for p in pages}
    crops = (
        db.scalars(select(m.Crop).where(m.Crop.page_id.in_(list(page_by_id)))).all()
        if page_by_id
        else []
    )
    crop_by_id = {c.id: c for c in crops}
    per_page: Counter[str] = Counter(c.page_id for c in crops)
    crops_per_file: Counter[str] = Counter()
    files_with_crops: set[str] = set()
    for c in crops:
        fid = page_by_id[c.page_id].file_id
        files_with_crops.add(fid)
        crops_per_file[str(file_by_id[fid].upload_order)] += 1

    art_root = settings.data_dir / "batches" / batch_id / "artifacts"
    docs = db.scalars(
        select(m.Document).where(m.Document.batch_id == batch_id).order_by(m.Document.created_at)
    ).all()
    diags: list[DocumentDiag] = []
    for d in docs:
        fields_db = {
            f.name: f
            for f in db.scalars(select(m.DocumentField).where(m.DocumentField.document_id == d.id))
        }
        fdiag: dict[str, FieldDiag] = {}
        for name in m.FIELD_NAMES:
            f = fields_db.get(name)
            if f is None or f.status == "missing" or not f.value:
                fdiag[name] = FieldDiag(
                    status=f.status if f else "missing",
                    confidence=f.confidence if f else None,
                    source="none",
                )
                continue
            flags = value_flags(name, f.value, d.doc_code)
            fdiag[name] = FieldDiag(
                status=f.status,
                confidence=round(f.confidence, 2) if f.confidence is not None else None,
                source=source_tag(f.status, f.evidence_json or []),
                length=len(f.value) if name in NAME_FIELDS else 0,
                flags=flags,
                shape=shape_of(f.value) if flags else None,
            )
        flags_by_field = {n: fd.flags for n, fd in fdiag.items()}
        crop_ids = [i for i in (d.front_crop_id, d.back_crop_id) if i]
        cdiags = []
        for cid in crop_ids:
            crop = crop_by_id.get(cid)
            if crop is None:
                continue
            p = page_by_id[crop.page_id]
            cdiags.append(
                _crop_diag(
                    crop,
                    p,
                    file_by_id[p.file_id],
                    art_root / cid,
                    flags_by_field,
                    crops_on_page=per_page[p.id],
                )
            )
        faces = (
            "both"
            if d.front_crop_id and d.back_crop_id
            else "front"
            if d.front_crop_id
            else "back"
            if d.back_crop_id
            else "none"
        )
        pairing = "paired" if faces == "both" else "suggested" if d.suggested_pair_id else "single"
        stage = next((c.stage for c in cdiags if c.stage != "aucune"), "aucune")
        diags.append(
            DocumentDiag(
                document_id=d.id,
                status=d.status,
                reasons=[
                    str(r.get("code", r)) if isinstance(r, dict) else str(r)
                    for r in (d.status_reasons_json or [])
                ],
                alerts=[str(a) for a in (d.alerts_json or [])],
                faces=faces,
                pairing=pairing,
                pair_score=round(d.pair_score, 2) if d.pair_score is not None else None,
                doc_type=d.doc_type,
                issuing_state=d.issuing_state,
                stage=stage,
                crops=cdiags,
                fields=fdiag,
            )
        )
    used: Counter[str] = Counter(c.crop_id for d in diags for c in d.crops)
    crops_by_doc_count = Counter(used.values())  # n documents → combien de recadrages
    jobs = db.scalars(select(m.Job).where(m.Job.batch_id == batch_id)).all()
    job_counts: Counter[tuple[str, str, str | None]] = Counter()
    job_attempts: Counter[tuple[str, str, str | None]] = Counter()
    due: dict[tuple[str, str, str | None], int] = {}
    now = datetime.now(UTC)
    for j in jobs:
        key = (j.type, j.status, j.error_code)
        job_counts[key] += 1
        job_attempts[key] += j.attempts
        if j.status == "queued":
            due[key] = min(due.get(key, 10**9), round((j.run_after - now).total_seconds()))
    job_diags = [
        JobDiag(
            type=t,
            status=s,
            error_code=e,
            attempts=job_attempts[(t, s, e)],
            count=n,
            due_in_s=due.get((t, s, e)),
        )
        for (t, s, e), n in sorted(job_counts.items(), key=lambda kv: (kv[0][0], kv[0][1]))
    ]
    return BatchDiagnosis(
        batch_id=batch_id,
        files=len(files),
        files_without_crop=sum(1 for f in files if f.id not in files_with_crops),
        pages=len(pages),
        crops=len(crops),
        crops_per_file=dict(sorted(crops_per_file.items(), key=lambda kv: int(kv[0]))),
        documents=diags,
        jobs=job_diags,
        summary=summarize(diags, job_counts, per_page, crops_by_doc_count),
    )


# --- Agrégats ------------------------------------------------------------------------


def summarize(
    docs: list[DocumentDiag],
    job_counts: Counter[tuple[str, str, str | None]],
    per_page: Counter[str] | None = None,
    crop_doc_counts: Counter[int] | None = None,
) -> dict[str, dict[str, int]]:
    out: dict[str, Counter[str]] = {
        k: Counter()
        for k in (
            "doc_type", "issuing_state", "mrz_format", "physical_format", "pairing", "faces",
            "document_status", "stage", "mrz_found", "mrz_valid", "mrz_failure",
            "failed_check", "reason", "quality_alert", "job", "crops_per_page",
            "documents_per_crop", "detection", "mrz_band_source",
        )
    }  # fmt: skip
    for d in docs:
        out["doc_type"][d.doc_type or "?"] += 1
        out["issuing_state"][d.issuing_state or "?"] += 1
        out["pairing"][d.pairing] += 1
        out["faces"][d.faces] += 1
        out["document_status"][d.status] += 1
        out["stage"][d.stage] += 1
        for r in d.reasons:
            out["reason"][r] += 1
        for c in d.crops:
            out["mrz_format"][c.mrz_format or "-"] += 1
            out["physical_format"][c.physical_format] += 1
            out["mrz_found"]["oui" if c.mrz_found else "non"] += 1
            out["mrz_valid"][{True: "oui", False: "non", None: "n/a"}[c.mrz_valid]] += 1
            if c.mrz_failure:
                out["mrz_failure"][c.mrz_failure] += 1
            for k in c.failed_checks:
                out["failed_check"][k] += 1
            for q in c.quality_alerts:
                out["quality_alert"][q] += 1
        for name, f in d.fields.items():
            out.setdefault(f"source:{name}", Counter())[f.source] += 1
            out.setdefault(f"status:{name}", Counter())[f.status] += 1
            for flag in f.flags:
                out.setdefault("field_flag", Counter())[f"{name}:{flag}"] += 1
    for page_crops in (per_page or Counter()).values():
        out["crops_per_page"][str(page_crops)] += 1
    for n_docs, n_crops in (crop_doc_counts or Counter()).items():
        out["documents_per_crop"][str(n_docs)] += n_crops
    for d in docs:
        for c in d.crops:
            out["detection"][c.detection or "?"] += 1
            out["mrz_band_source"][c.mrz_band_source or "-"] += 1
    for (t, s, e), n in job_counts.items():
        out["job"][f"{t}/{s}" + (f"/{e}" if e else "")] += n
    return {k: dict(sorted(v.items(), key=lambda kv: (-kv[1], kv[0]))) for k, v in out.items()}


# --- Rendu texte ---------------------------------------------------------------------


def _fmt_field(name: str, f: FieldDiag) -> str:
    if f.status == "missing":
        return f"{name}=∅"
    conf = f"{f.confidence:.2f}" if f.confidence is not None else "-"
    text = f"{name}={f.status}/{conf}/{f.source}" + (f"/n{f.length}" if f.length else "")
    if f.flags:
        text += f"!{','.join(f.flags)}[{f.shape}]"
    return text


def render_text(diag: BatchDiagnosis) -> str:
    out: list[str] = []
    w = out.append
    w(f"# Diagnostic du lot {diag.batch_id} (aucune donnée personnelle)")
    w(
        f"fichiers={diag.files} (sans recadrage : {diag.files_without_crop}) pages={diag.pages}"
        f" recadrages={diag.crops} documents={len(diag.documents)}"
    )
    per = Counter(diag.crops_per_file.values())
    w(
        "recadrages par fichier : "
        + (", ".join(f"{k}→{v} fichier(s)" for k, v in sorted(per.items())) or "-")
    )
    w("")
    w("## Résumé")
    for key, counts in diag.summary.items():
        if not counts:
            continue
        w(f"{key}: " + ", ".join(f"{k}={v}" for k, v in counts.items()))
    w("")
    w("## Jobs")
    for j in diag.jobs:
        w(
            f"{j.type} {j.status} {j.error_code or '-'} x{j.count} tentatives={j.attempts}"
            + (f" prévu_dans={j.due_in_s}s" if j.due_in_s is not None else "")
        )
    w("")
    w("## Documents")
    for i, d in enumerate(diag.documents, 1):
        w(
            f"[{i:02d}] {d.document_id[:8]} {d.status} type={d.doc_type or '?'}"
            f" pays={d.issuing_state or '?'} faces={d.faces} appariement={d.pairing}"
            + (f"({d.pair_score})" if d.pair_score is not None else "")
            + f" étape={d.stage}"
        )
        if d.reasons:
            counted = Counter(d.reasons).items()
            w("     motifs : " + ", ".join(f"{k} x{n}" if n > 1 else k for k, n in counted))
        for c in d.crops:
            w(
                f"     recadrage {c.crop_id[:8]} fichier#{c.file_no} p{c.page}"
                f" ({c.crops_on_page} sur la page) {c.side} {c.physical_format}"
                f" type={c.doc_type or '?'}{'(probable)' if c.probable_type else ''}"
                f" mrz={'oui' if c.mrz_found else 'non'}"
                f" valide={ {True: 'oui', False: 'non', None: 'n/a'}[c.mrz_valid] }"
                f" format={c.mrz_format or '-'}"
                + (f" sommes_KO={','.join(c.failed_checks)}" if c.failed_checks else "")
                + (f" echec={c.mrz_failure}" if c.mrz_failure else "")
                + f" réparations={c.mrz_changes} texte_zone_visuelle={c.viz_slots}"
                + f" moteurs_mrz={'+'.join(c.mrz_engines) or '-'}"
                + (f" qualité={','.join(c.quality_alerts)}" if c.quality_alerts else "")
                + f" étape={c.stage}"
            )
            w(
                f"       géométrie : recadrage={c.crop_px[0]}x{c.crop_px[1]}"
                f" page={c.page_px[0]}x{c.page_px[1]} quad={c.quad_area_pct}% de la page"
                f" rotation={c.rotation} détection={c.detection or '?'}"
                f" bande_mrz={c.mrz_band_source or '-'}"
            )
            for engine, text in c.mrz_probe.items():
                w(f"       sonde_ocr {engine} {text}")
            for g in c.geometry:
                w(f"       suspect {g}")
        w(
            "     "
            + " ".join(_fmt_field(n, f) for n, f in d.fields.items() if f.status != "missing")
        )
        missing = [n for n, f in d.fields.items() if f.status == "missing"]
        if missing:
            w("     vides : " + ",".join(missing))
    return "\n".join(out) + "\n"
