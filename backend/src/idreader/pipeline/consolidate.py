"""L — Consolidation des preuves, confiance par champ, statut du document.

03_PIPELINE_ANALYSE.md § L. Principe (CLAUDE.md, règle 3) : un document n'est validé
automatiquement que si ses valeurs obligatoires sont **prouvées**. Les confiances de départ
sont celles du tableau du § L ; elles ne sont pas calibrées (calibration sur golden, macOS).
"""

from __future__ import annotations

from datetime import date, timedelta
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from idreader.pipeline.classify import Classification, DocType
from idreader.pipeline.extract import Extraction, VizField
from idreader.pipeline.mrz.decode import REASONS as MRZ_REASONS
from idreader.pipeline.mrz.decode import MrzField, MrzFieldStatus, MrzResult, age_on
from idreader.pipeline.reference import doc_number_patterns

MANDATORY = (
    "surname",
    "given_names",
    "date_of_birth",
    "sex",
    "nationality",
    "document_number",
    "date_of_expiry",
    "issuing_state",
)
OUTPUT_FIELDS = (
    "surname", "given_names", "usage_name", "sex", "date_of_birth", "place_of_birth",
    "nationality", "document_number", "date_of_issue", "date_of_expiry", "authority",
    "personal_number",
 "issuing_state",
)  # fmt: skip

CONFIDENCE = {
    "viz_confirmed_by_mrz": 0.99,
    "viz_two_engines": 0.90,
    "viz_single_engine": 0.75,  # un moteur, confiance OCR ≥ 0,8
    "viz_weak": 0.50,  # un moteur, confiance OCR < 0,8
    "ai_unanchored": 0.40,  # phase 6
}
VIZ_SINGLE_MIN_OCR = 0.8
EXPIRES_SOON_DAYS = 183
ADULT_AGE = 18
# Durée de validité (années) d'un titre délivré à un majeur, par (État, type) : la date de
# délivrance se déduit alors de l'expiration (délivré le 16/01/2026 → expire le 15/01/2036).
# Repris du catalogue du projet « Scan » ; la CNI française 2021 est la seule carte TD1 de la FRA.
ADULT_VALIDITY_YEARS = {("FRA", "passport"): 10, ("FRA", "id_card"): 10}

REASON_LABELS: dict[str, str] = {
    **MRZ_REASONS,
    "NO_ENGINE": "Aucun moteur OCR disponible",
    "NO_MRZ": "Pas de MRZ : valeurs lues dans la zone visuelle seule",
    "MRZ_NOT_FOUND": "MRZ introuvable",
    "MRZ_UNREADABLE": "MRZ illisible",
    "FIELD_MISSING": "Champ obligatoire absent",
    "FIELD_LOW_CONFIDENCE": "Confiance insuffisante",
    "FIELD_UNVERIFIED": "Valeur lue par un seul moteur, sans preuve",
    "VIZ_ACCENTS_UNCONFIRMED": "Accents du nom lus par un seul moteur : à confirmer",
    "VIZ_MRZ_NAME_CONFLICT": "Nom différent entre la MRZ et la zone visuelle",
    "VIZ_MRZ_DOB_CONFLICT": "Date de naissance différente entre la MRZ et la zone visuelle",
    "VIZ_MRZ_EXPIRY_CONFLICT": "Date d'expiration différente entre la MRZ et la zone visuelle",
    "QUALITY_GLARE_MRZ": "Reflet sur la MRZ",
    "SPECIMEN_DOCUMENT": "Spécimen (pays fictif UTO)",
    "PROBABLE_TYPE": "Type de document déduit sans MRZ",
    "NO_DOCUMENT": "Aucun document détecté",
    "NOTHING_READABLE": "Aucun texte lisible",
    "TOO_BLURRY": "Image trop floue",
    "GLARE_ON_MRZ": "Reflet sur la MRZ",
    "LOW_RESOLUTION": "Résolution insuffisante",
    "UNSUPPORTED_FORMAT": "Format de fichier non pris en charge",
}
ALERT_LABELS: dict[str, str] = {
    "EXPIRED": "Document expiré",
    "MINOR": "Titulaire mineur",
    "EXPIRES_SOON": "Expire dans moins de 6 mois",
    "LOW_QUALITY": "Qualité faible",
    "UNUSUAL_NUMBER": "Motif de numéro inhabituel",
    "DUPLICATE_DOCUMENT": "Doublon",
    "ISSUE_VALIDITY_UNUSUAL": "Durée de validité inhabituelle",
    "ISSUE_DATE_INCONSISTENT": "Date de délivrance incohérente",
    "MRZ_NAME_TRUNCATED": "Nom tronqué dans la MRZ",
    "ISSUE_DATE_DEDUCED": "Date de délivrance déduite de l'expiration (non lue)",
}


class FieldState(StrEnum):
    """Statut d'un champ (colonne `fields.status`)."""

    OK = "ok"
    REPAIRED = "repaired"
    UNCERTAIN = "uncertain"  # lu mais non prouvé
    CONFLICT = "conflict"  # sources en désaccord
    MISSING = "missing"
    MANUAL = "manual"


class DocState(StrEnum):
    AUTO_VALIDATED = "auto_validated"
    TO_REVIEW = "to_review"
    FAILED = "failed"


class Reason(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str
    field: str | None = None

    @property
    def label(self) -> str:
        return REASON_LABELS.get(self.code, self.code)


class FieldResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    value: str | None
    value_native: str | None = None
    status: FieldState
    confidence: float
    sources: list[str] = Field(default_factory=list)  # mrz | viz | human
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    candidates: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    slot_ids: list[int] = Field(default_factory=list)


class DocumentResult(BaseModel):
    """Artefact JSON de l'étape L."""

    model_config = ConfigDict(frozen=True)

    status: DocState
    reasons: list[Reason]
    alerts: list[str]
    confidence: float
    fields: dict[str, FieldResult]
    doc_type: DocType
    issuing_state: str | None
    mrz_valid: bool | None


def _viz_conf(v: VizField) -> float:
    if v.confirms_mrz:
        return CONFIDENCE["viz_confirmed_by_mrz"]
    if v.agreed:
        return CONFIDENCE["viz_two_engines"]
    if v.ocr_confidence >= VIZ_SINGLE_MIN_OCR:
        return CONFIDENCE["viz_single_engine"]
    return CONFIDENCE["viz_weak"]


def _viz_evidence(v: VizField) -> dict[str, Any]:
    return {
        "src": "viz",
        "method": v.method,
        "engines": v.engines,
        "agreed": v.agreed,
        "confirms_mrz": v.confirms_mrz,
        "slots": v.slot_ids,
    }


def _from_mrz(f: MrzField, v: VizField | None, name: str) -> FieldResult:
    evidence: list[dict[str, Any]] = [
        {"src": "mrz", "status": f.status.value, "checks": f.evidence}
    ]
    sources = ["mrz"]
    reasons = list(f.reasons)
    value, native = f.value, None
    candidates = [a for a in f.alternatives if a != f.value]
    status = {
        MrzFieldStatus.VALID: FieldState.OK,
        MrzFieldStatus.REPAIRED: FieldState.REPAIRED,
        MrzFieldStatus.UNVERIFIED: FieldState.UNCERTAIN,
        MrzFieldStatus.TO_REVIEW: FieldState.UNCERTAIN,
        MrzFieldStatus.INVALID: FieldState.CONFLICT,
    }[f.status]
    confidence = f.confidence
    if v is not None:
        evidence.append(_viz_evidence(v))
        sources.append("viz")
        if v.confirms_mrz:
            # La zone visuelle (lue par d'autres moteurs, dans une autre police) est une
            # preuve indépendante : elle confirme un nom non contrôlé, le siècle de
            # naissance, ou tranche un angle mort (E.5).
            if name in ("surname", "given_names"):
                value = v.value
                native = v.value_native
            if name == "document_number" and v.value != f.value:
                value = v.value
            if f.status is not MrzFieldStatus.INVALID:
                confidence = max(confidence, CONFIDENCE["viz_confirmed_by_mrz"])
                status = FieldState.REPAIRED if status is FieldState.REPAIRED else FieldState.OK
                reasons = [
                    r
                    for r in reasons
                    if r not in ("MRZ_BLIND_SPOT", "MRZ_CENTURY_UNCERTAIN", "MRZ_REPAIR_AMBIGUOUS")
                ]
            if name == "date_of_birth" and v.year4 is not None:
                value = v.value
            if name in ("surname", "given_names") and not v.agreed:
                # La MRZ prouve les lettres, pas les accents (Ï et I s'y écrivent I) : un seul
                # moteur peut avoir perdu ou inventé un accent (03 § H.1, deux moteurs).
                status = FieldState.UNCERTAIN
                confidence = min(confidence, CONFIDENCE["viz_two_engines"])
                reasons = [*reasons, "VIZ_ACCENTS_UNCONFIRMED"]
        elif name == "document_number" and v.value != f.value:
            candidates = [v.value, *[c for c in candidates if c != v.value]]
        elif v.value and v.value != f.value:
            candidates = [v.value, *candidates]
    return FieldResult(
        name=name,
        value=value,
        value_native=native,
        status=status,
        confidence=round(confidence, 4),
        sources=sources,
        evidence=evidence,
        candidates=candidates[:5],
        reasons=reasons,
        slot_ids=v.slot_ids if v else [],
    )


def _from_viz(v: VizField) -> FieldResult:
    conf = _viz_conf(v)
    return FieldResult(
        name=v.name,
        value=v.value,
        value_native=v.value_native,
        status=FieldState.OK if conf >= CONFIDENCE["viz_two_engines"] else FieldState.UNCERTAIN,
        confidence=conf,
        sources=["viz"],
        evidence=[_viz_evidence(v)],
        candidates=v.candidates,
        slot_ids=v.slot_ids,
    )


def _missing(name: str) -> FieldResult:
    return FieldResult(name=name, value=None, status=FieldState.MISSING, confidence=0.0)


def _iso(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def consolidate(
    *,
    mrz: MrzResult | None,
    extraction: Extraction | None,
    classification: Classification,
    today: date,
    threshold: float,
    quality_alerts: list[str] | None = None,
    mrz_glare: bool = False,
    mrz_failure: str | None = None,
    allow_specimen: bool = False,
) -> DocumentResult:
    quality_alerts = quality_alerts or []
    viz = extraction.fields if extraction else {}
    fields: dict[str, FieldResult] = {}
    reasons: list[Reason] = []

    def reason(code: str, field: str | None = None) -> None:
        r = Reason(code=code, field=field)
        if r not in reasons:
            reasons.append(r)

    for name in OUTPUT_FIELDS:
        mrz_field = mrz.fields.get(name) if mrz else None
        if mrz_field is not None and mrz_field.value not in (None, ""):
            fields[name] = _from_mrz(mrz_field, viz.get(name), name)
        elif name in viz:
            fields[name] = _from_viz(viz[name])
        else:
            fields[name] = _missing(name)

    if fields["issuing_state"].status is FieldState.MISSING and classification.issuing_state:
        fields["issuing_state"] = FieldResult(  # lu sur l'en-tête : jamais prouvé par une MRZ
            name="issuing_state",
            value=classification.issuing_state,
            status=FieldState.UNCERTAIN,
            confidence=CONFIDENCE["viz_weak"],
            sources=["viz"],
            evidence=[{"src": "viz", "method": "country_header"}],
        )
    deduced = _deduced_issue_date(fields, classification)
    if deduced is not None:
        fields["date_of_issue"] = deduced

    # Raisons venues de la MRZ : celles des champs affichés (après confirmation éventuelle
    # par la zone visuelle), puis celles du document entier.
    if mrz is not None:
        for f in fields.values():
            for code in f.reasons:
                reason(code, f.name)
        if "MRZ_COMPOSITE_FAILED" in mrz.reasons:
            reason("MRZ_COMPOSITE_FAILED")
        if not mrz.checksums_valid and not reasons:
            reason("MRZ_CHECK_FAILED")
        surname = viz.get("surname")
        if "MRZ_NAME_TRUNCATED" in mrz.alerts and not (surname and surname.confirms_mrz):
            reason("MRZ_NAME_TRUNCATED", "surname")
    else:
        reason(mrz_failure or "NO_MRZ")
        if mrz_failure in ("MRZ_UNREADABLE", "NO_ENGINE"):
            reason("NO_MRZ")
    if classification.probable:
        reason("PROBABLE_TYPE")
    if extraction is not None:
        for code in extraction.conflicts:
            field = {
                "VIZ_MRZ_NAME_CONFLICT": "surname",
                "VIZ_MRZ_DOB_CONFLICT": "date_of_birth",
                "VIZ_MRZ_EXPIRY_CONFLICT": "date_of_expiry",
            }.get(code)
            reason(code, field)
            if field and field in fields:
                fields[field] = fields[field].model_copy(update={"status": FieldState.CONFLICT})
    if mrz_glare:
        reason("QUALITY_GLARE_MRZ")
    if classification.issuing_state == "UTO" and not allow_specimen:
        reason("SPECIMEN_DOCUMENT")

    # Champs obligatoires
    for name in MANDATORY:
        f = fields[name]
        if f.status is FieldState.MISSING:
            reason("FIELD_MISSING", name)
        elif f.status in (FieldState.UNCERTAIN, FieldState.CONFLICT) and not any(
            r.field == name for r in reasons
        ):
            reason(
                "FIELD_UNVERIFIED" if f.status is FieldState.UNCERTAIN else "FIELD_LOW_CONFIDENCE",
                name,
            )
        elif f.confidence < threshold and not any(r.field == name for r in reasons):
            reason("FIELD_LOW_CONFIDENCE", name)

    alerts = _alerts(fields, mrz, extraction, quality_alerts, today, classification)
    mandatory_conf = [fields[n].confidence for n in MANDATORY]
    confidence = round(min(mandatory_conf), 4) if mandatory_conf else 0.0

    auto = (
        mrz is not None
        and mrz.checksums_valid
        and not reasons
        and all(fields[n].status in (FieldState.OK, FieldState.REPAIRED) for n in MANDATORY)
        and all(fields[n].confidence >= threshold for n in MANDATORY)
    )
    return DocumentResult(
        status=DocState.AUTO_VALIDATED if auto else DocState.TO_REVIEW,
        reasons=reasons,
        alerts=alerts,
        confidence=confidence,
        fields=fields,
        doc_type=classification.doc_type,
        issuing_state=classification.issuing_state,
        mrz_valid=mrz.checksums_valid if mrz is not None else None,
    )


def _deduced_issue_date(
    fields: dict[str, FieldResult], classification: Classification
) -> FieldResult | None:
    """Date de délivrance non lue, déduite de la date d'expiration (titulaire majeur à la
    délivrance, durée de validité connue du modèle). Jamais plus qu'« incertaine » : c'est une
    règle de calcul, pas une lecture (règle 3 de CLAUDE.md)."""
    issue = fields["date_of_issue"]
    if issue.status is not FieldState.MISSING:
        return None
    if classification.mrz_format == "FRA-ID":
        return _issue_from_old_card_number(fields["document_number"])
    years = ADULT_VALIDITY_YEARS.get(
        (classification.issuing_state or "", classification.doc_type.value)
    )
    expiry, born = fields["date_of_expiry"], fields["date_of_birth"]
    if years is None or (
        classification.doc_type is DocType.ID_CARD and classification.mrz_format != "TD1"
    ):
        return None
    exp, dob = _iso(expiry.value), _iso(born.value)
    if exp is None or dob is None or expiry.status not in (FieldState.OK, FieldState.REPAIRED):
        return None
    try:
        issued = exp.replace(year=exp.year - years) + timedelta(days=1)
    except ValueError:  # 29 février
        issued = exp.replace(year=exp.year - years, day=28) + timedelta(days=1)
    if age_on(dob, issued) < ADULT_AGE:
        return None  # titulaire mineur à la délivrance : durée différente, on ne devine pas
    return FieldResult(
        name="date_of_issue",
        value=issued.isoformat(),
        status=FieldState.UNCERTAIN,
        confidence=0.5,
        sources=["rule"],
        evidence=[{"src": "rule", "method": "validity_deduction", "years": years}],
    )


def _issue_from_old_card_number(number: FieldResult) -> FieldResult | None:
    """Ancienne CNI française : les 4 premiers chiffres du n° de carte sont l'année et le mois
    de délivrance (AAMM). Le jour est inconnu : fixé au 01 et signalé comme déduit."""
    digits = (number.value or "")[:4]
    if number.status not in (FieldState.OK, FieldState.REPAIRED) or not digits.isdigit():
        return None
    yy, month = int(digits[:2]), int(digits[2:])
    year = 1900 + yy if yy >= 88 else 2000 + yy  # modèle créé en 1988, remplacé en 2021
    if not 1 <= month <= 12 or year > 2021:
        return None
    return FieldResult(
        name="date_of_issue",
        value=date(year, month, 1).isoformat(),
        status=FieldState.UNCERTAIN,
        confidence=0.5,
        sources=["rule"],
        evidence=[{"src": "rule", "method": "card_number"}],
    )


def _alerts(
    fields: dict[str, FieldResult],
    mrz: MrzResult | None,
    extraction: Extraction | None,
    quality_alerts: list[str],
    today: date,
    classification: Classification,
) -> list[str]:
    alerts: list[str] = []
    expires = _iso(fields["date_of_expiry"].value)
    born = _iso(fields["date_of_birth"].value)
    if expires is not None:
        if expires < today:
            alerts.append("EXPIRED")
        elif (expires - today).days < EXPIRES_SOON_DAYS:
            alerts.append("EXPIRES_SOON")
    if born is not None and 0 <= age_on(born, today) < ADULT_AGE:
        alerts.append("MINOR")
    if fields["date_of_issue"].sources == ["rule"]:
        alerts.append("ISSUE_DATE_DEDUCED")
    if quality_alerts:
        alerts.append("LOW_QUALITY")
    number = fields["document_number"].value
    state = classification.issuing_state
    code = (classification.doc_code or "")[:1]
    pattern = doc_number_patterns().get((state or "", code))
    if number and pattern is not None and not pattern.fullmatch(number):
        alerts.append("UNUSUAL_NUMBER")
    if mrz is not None:
        alerts.extend(a for a in mrz.alerts if a in ALERT_LABELS)
    if extraction is not None:
        alerts.extend(extraction.alerts)
    return sorted(set(alerts))


def failed(code: str) -> DocumentResult:
    """Document en échec (aucun document, rien de lisible…), avec sa raison."""
    return DocumentResult(
        status=DocState.FAILED,
        reasons=[Reason(code=code)],
        alerts=[],
        confidence=0.0,
        fields={name: _missing(name) for name in OUTPUT_FIELDS},
        doc_type=DocType.OTHER,
        issuing_state=None,
        mrz_valid=None,
    )
