"""F — Classification du document (03_PIPELINE_ANALYSE.md § F).

Avec MRZ : type par le code de document (P passeport, V visa, I/A/C carte d'identité, avec
surcharges par pays dans `reference/doc_codes.yaml`), pays émetteur, format de MRZ.
Sans MRZ : mots-clés sur l'OCR complet et format physique → « type probable », document
toujours « À vérifier ».
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from idreader.pipeline.extract import fold
from idreader.pipeline.mrz.decode import MrzResult
from idreader.pipeline.mrz.formats import MrzFormat
from idreader.pipeline.reference import country_headers, doc_code_overrides, mrz_state_codes


class DocType(StrEnum):
    PASSPORT = "passport"
    ID_CARD = "id_card"
    RESIDENCE_PERMIT = "residence_permit"
    VISA = "visa"
    TRAVEL_DOCUMENT = "travel_document"
    OTHER = "other"


DOC_TYPE_LABELS = {
    DocType.PASSPORT: "Passeport",
    DocType.ID_CARD: "Carte d'identité",
    DocType.RESIDENCE_PERMIT: "Titre de séjour",
    DocType.VISA: "Visa",
    DocType.TRAVEL_DOCUMENT: "Document de voyage",
    DocType.OTHER: "Autre",
}

PHYSICAL_FORMAT = {
    MrzFormat.TD1: "ID1",
    MrzFormat.TD2: "ID2",
    MrzFormat.MRVB: "ID2",
    MrzFormat.FRA_ID: "ID2",
    MrzFormat.TD3: "ID3",
    MrzFormat.MRVA: "ID3",
}

# Mots-clés (repliés : majuscules sans accents), § F. Graine à enrichir.
KEYWORDS: dict[DocType, tuple[str, ...]] = {
    DocType.PASSPORT: (
        "PASSPORT", "PASSEPORT", "PASAPORTE", "PASSAPORTE", "PASSAPORTO", "REISEPASS",
        "PASPOORT", "PASZPORT", "PASAPORT", "ПАСПОРТ", "护照", "旅券", "여권", "جواز سفر",
    ),
    DocType.ID_CARD: (
        "IDENTITY CARD", "CARTE NATIONALE D IDENTITE", "CARTE D IDENTITE",
        "DOCUMENTO NACIONAL DE IDENTIDAD", "PERSONALAUSWEIS", "CARTA D IDENTITA",
        "CARTAO DE CIDADAO", "IDENTITEITSKAART", "DOWOD OSOBISTY", "CARTE DE IDENTITATE",
        "KIMLIK KARTI", "NATIONAL ID",
    ),
    DocType.RESIDENCE_PERMIT: (
        "TITRE DE SEJOUR", "RESIDENCE PERMIT", "AUFENTHALTSTITEL", "PERMISO DE RESIDENCIA",
        "PERMESSO DI SOGGIORNO",
    ),
    DocType.VISA: ("VISA", "VISUM"),
}  # fmt: skip


class Classification(BaseModel):
    model_config = ConfigDict(frozen=True)

    doc_type: DocType
    doc_code: str | None = None
    issuing_state: str | None = None
    mrz_format: str | None = None
    physical_format: str = "unknown"
    probable: bool = False  # True : déduit sans MRZ (jamais validé automatiquement)
    keywords: list[str] = Field(default_factory=list)


def doc_type_from_code(code: str, state: str | None) -> DocType:
    code = code.replace("<", "")
    if state and (state, code) in doc_code_overrides():
        return DocType(doc_code_overrides()[(state, code)])
    first = code[:1]
    if first == "P":
        return DocType.PASSPORT
    if first == "V":
        return DocType.VISA
    if first in ("I", "A", "C"):
        return DocType.ID_CARD
    return DocType.OTHER


def classify_mrz(mrz: MrzResult) -> Classification:
    code_field = mrz.fields.get("document_code")
    state_field = mrz.fields.get("issuing_state")
    code = code_field.value if code_field and code_field.value else ""
    state = state_field.value if state_field and state_field.value else None
    if mrz.format is MrzFormat.FRA_ID:
        code, state = "ID", "FRA"
    return Classification(
        doc_type=doc_type_from_code(code, state),
        doc_code=code or None,
        issuing_state=state,
        mrz_format=mrz.format.value,
        physical_format=PHYSICAL_FORMAT[mrz.format],
    )


def classify_text(texts: Sequence[str], *, physical_format: str = "unknown") -> Classification:
    """Sans MRZ : mots-clés, pays nommé dans le texte, format physique. Type « probable »."""
    folded = " " + " ".join(fold(t) for t in texts) + " "
    found: dict[DocType, list[str]] = {}
    for doc_type, words in KEYWORDS.items():
        for word in words:
            key = fold(word) or word
            if f" {key} " in folded or (not key.isascii() and key in folded):
                found.setdefault(doc_type, []).append(word)
    # Priorité : titre de séjour > carte > passeport > visa (« VISA » apparaît aussi
    # dans les mentions des passeports).
    for doc_type in (DocType.RESIDENCE_PERMIT, DocType.ID_CARD, DocType.PASSPORT, DocType.VISA):
        if doc_type in found:
            chosen = doc_type
            break
    else:
        chosen = DocType.OTHER
    if chosen is DocType.OTHER and physical_format == "ID3":
        chosen = DocType.PASSPORT
    state = _state_in_text(folded)
    return Classification(
        doc_type=chosen,
        issuing_state=state,
        physical_format=physical_format,
        probable=True,
        keywords=sorted({w for words in found.values() for w in words}),
    )


def _state_in_text(folded: str) -> str | None:
    """État émetteur sans MRZ : code ICAO isolé dans le texte (« FRA », « UTO ») s'il est unique,
    sinon intitulé d'État en en-tête (« RÉPUBLIQUE FRANÇAISE ») s'il désigne un seul État."""
    tokens = {t for t in folded.split() if len(t) == 3 and t.isalpha()}
    hits = sorted(t for t in tokens if t in mrz_state_codes())
    if len(hits) == 1:
        return hits[0]
    named = sorted(code for code, pattern in country_headers().items() if pattern.search(folded))
    return named[0] if len(named) == 1 else None
