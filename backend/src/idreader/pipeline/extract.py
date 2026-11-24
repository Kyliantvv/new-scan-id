"""H — Extraction des champs de la zone visuelle.

03_PIPELINE_ANALYSE.md § H, dans l'ordre de priorité :
- H.1 recherche guidée par la MRZ : nom, prénoms (translittération ICAO), dates (la zone
  visuelle donne le siècle), n° de document ;
- H.2 ancrage sur les libellés (`reference/viz_labels.yaml`) : valeur juste en dessous
  (recouvrement horizontal ≥ 30 %, écart < 2,5 hauteurs de ligne), sinon à droite ;
- H.3 détecteurs par type : dates multilingues, sexe ;
- champs particuliers : date de délivrance (cohérence avec naissance et expiration),
  lieu de naissance, autorité, nom d'usage.
Les gabarits (H.4) viendront en phase 7.

Seules les correspondances **exactes** (après translittération ICAO) avec la MRZ comptent
comme confirmation : une lecture seulement « proche » reste une alternative.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from functools import lru_cache

from anyascii import anyascii
from pydantic import BaseModel, ConfigDict, Field
from rapidfuzz import fuzz

from idreader.pipeline.dates import ParsedDate, matches_mrz, parse_dates
from idreader.pipeline.mrz.decode import MrzResult
from idreader.pipeline.mrz.names import names_match
from idreader.pipeline.reference import viz_labels
from idreader.pipeline.viz_ocr import Slot, VizOcr

LABEL_MIN_SIMILARITY = 88.0  # rapidfuzz 0–100, à calibrer sur golden
BELOW_MAX_GAP = 2.5  # hauteurs de ligne (§ H.2)
BELOW_MIN_OVERLAP = 0.30
RIGHT_MIN_V_OVERLAP = 0.5
VALIDITY_YEARS = (5, 10)  # durées de validité habituelles (§ H, champs particuliers)
SEX_TOKENS = {
    "M": "M", "F": "F", "X": "X", "M/M": "M", "F/F": "F", "Ж": "F", "М": "M", "男": "M", "女": "F",
    "H": "M",  # « Homme » abrégé sur certains titres français
}  # fmt: skip
USAGE_PREFIX = re.compile(r"^\s*(?:ÉP(?:OUSE)?\.?|EP(?:OUSE)?\.?|VVE\.?)\s+", re.IGNORECASE)
LABEL_FIELDS = (
    "surname", "given_names", "usage_name", "date_of_birth", "place_of_birth", "sex",
    "nationality", "document_number", "date_of_issue", "date_of_expiry", "authority",
    "personal_number", "address",
)  # fmt: skip


class VizField(BaseModel):
    """Valeur d'un champ trouvée dans la zone visuelle, avec sa preuve."""

    model_config = ConfigDict(frozen=True)

    name: str
    value: str
    value_native: str | None = None
    method: str  # mrz_guided | label | type
    slot_ids: list[int]
    engines: list[str]
    ocr_confidence: float
    agreed: bool = False  # lu à l'identique par deux moteurs
    confirms_mrz: bool = False  # correspondance exacte avec la MRZ
    year4: int | None = None  # dates : année imprimée sur 4 chiffres
    candidates: list[str] = Field(default_factory=list)


class Extraction(BaseModel):
    """Artefact JSON de l'étape H pour un recadrage."""

    model_config = ConfigDict(frozen=True)

    fields: dict[str, VizField]
    label_slots: dict[str, list[int]]
    conflicts: list[str] = Field(default_factory=list)  # codes de raison
    alerts: list[str] = Field(default_factory=list)
    candidates: dict[str, list[str]] = Field(default_factory=dict)


# --- Normalisation et libellés --------------------------------------------------------


def fold(text: str) -> str:
    """Majuscules, sans accents ni ponctuation, espaces simples."""
    decomposed = unicodedata.normalize("NFKD", text.upper())
    base = "".join(c for c in decomposed if not unicodedata.combining(c))
    base = re.sub(r"[^\w\s]", " ", base)
    return " ".join(base.split())


def _strip_numbering(segment: str) -> str:
    """« 4. Nom », « (1) Nom », « 4a Nom » → « Nom »."""
    return re.sub(r"^\s*\(?\d{1,2}[a-z]?[.)]?\s*|\s*\(\d{1,2}\)\s*$", "", segment).strip()


@lru_cache(maxsize=1)
def _label_index() -> list[tuple[str, str]]:
    """(libellé replié, champ), libellés les plus longs d'abord."""
    pairs = [(fold(label), name) for name, labels in viz_labels().items() for label in labels]
    pairs = [(label, name) for label, name in pairs if label]
    return sorted(set(pairs), key=lambda p: -len(p[0]))


def label_field(text: str) -> tuple[str | None, str]:
    """Si `text` commence par un libellé connu (formes combinées « Nom/Surname/Apellidos »,
    numérotées), renvoie (champ, reste du texte après le libellé), sinon (None, text)."""
    if ":" in text:
        head, _, tail = text.partition(":")
        name, rest = label_field(head)
        if name is not None and not rest:
            return name, tail.strip()
    segments = [s for s in re.split(r"\s*/\s*", text) if s.strip()]
    if not segments:
        return None, text
    votes: dict[str, int] = {}
    for seg in segments:
        name = _match_label(_strip_numbering(seg))
        if name is None:
            break
        votes[name] = votes.get(name, 0) + 1
    if votes:
        return max(votes, key=lambda n: votes[n]), ""
    # Libellé suivi de la valeur sur la même ligne : « Sexe F », « Nom DUPONT »
    folded = fold(_strip_numbering(text))
    for label, name in _label_index():
        if len(label) >= 3 and folded.startswith(label + " ") and name in ("sex",):
            return name, text.split(None, len(label.split()))[-1]
    return None, text


def _match_label(segment: str) -> str | None:
    folded = fold(segment)
    if not folded or len(folded) > 45 or any(ch.isdigit() for ch in folded):
        return None
    best: tuple[float, str] | None = None
    for label, name in _label_index():
        if abs(len(label) - len(folded)) > max(3, len(label) // 3):
            continue
        score = fuzz.ratio(folded, label)
        if score >= LABEL_MIN_SIMILARITY and (best is None or score > best[0]):
            best = (score, name)
    return best[1] if best else None


# --- Géométrie ---------------------------------------------------------------------------


def _below(label: Slot, value: Slot) -> float | None:
    lx, ly, lw, lh = label.bbox
    vx, vy, vw, _ = value.bbox
    overlap = min(lx + lw, vx + vw) - max(lx, vx)
    if overlap < BELOW_MIN_OVERLAP * min(lw, vw):
        return None
    gap = vy - (ly + lh)
    if gap < -0.3 * lh or gap > BELOW_MAX_GAP * lh:
        return None
    return gap


def _right(label: Slot, value: Slot) -> float | None:
    lx, ly, lw, lh = label.bbox
    vx, vy, _, vh = value.bbox
    v_overlap = min(ly + lh, vy + vh) - max(ly, vy)
    if v_overlap < RIGHT_MIN_V_OVERLAP * min(lh, vh) or vx < lx + lw - 0.2 * lh:
        return None
    return vx - (lx + lw)


# --- Extraction ----------------------------------------------------------------------------


def _field(name: str, slots: list[Slot], value: str, method: str, **kw: object) -> VizField:
    return VizField(
        name=name,
        value=value,
        method=method,
        slot_ids=[s.id for s in slots],
        engines=sorted({r.engine for s in slots for r in s.readings}),
        ocr_confidence=min(s.best.confidence for s in slots),
        agreed=all(s.agreed for s in slots),
        **kw,
    )


def _clean_name(text: str) -> str:
    return " ".join(text.replace(",", " , ").split()).replace(" , ", ", ").strip(" ,")


def _name_variants(slot: Slot) -> list[str]:
    """Textes d'un emplacement à comparer à un nom MRZ : lectures entières, et partie après
    un libellé éventuel."""
    out: list[str] = []
    for r in slot.readings:
        for text in (r.text, *r.candidates):
            name, rest = label_field(text)
            for cand in (rest,) if name else (text,):
                cand = _clean_name(cand)
                if cand and cand not in out:
                    out.append(cand)
    return out


def _mrz_value(mrz: MrzResult | None, name: str) -> str | None:
    if mrz is None:
        return None
    f = mrz.fields.get(name)
    return f.value if f is not None else None


def _mrz_names(mrz: MrzResult) -> tuple[str | None, str | None, bool]:
    """(nom, prénoms, peut-être tronqué) d'après la MRZ décodée."""
    surname = mrz.fields.get("surname")
    given = mrz.fields.get("given_names")
    truncated = "MRZ_NAME_TRUNCATED" in mrz.alerts
    return (
        surname.value if surname is not None else None,
        given.value if given is not None else None,
        truncated,
    )


def extract(viz: VizOcr, mrz: MrzResult | None, *, today: date) -> Extraction:
    slots = viz.slots
    by_id = {s.id: s for s in slots}
    fields: dict[str, VizField] = {}
    candidates: dict[str, list[str]] = {}
    conflicts: list[str] = []
    alerts: list[str] = []

    # Libellés
    label_slots: dict[str, list[int]] = {}
    inline: dict[int, tuple[str, str]] = {}
    for s in slots:
        name, rest = label_field(s.text)
        if name is not None:
            label_slots.setdefault(name, []).append(s.id)
            if rest:
                inline[s.id] = (name, rest)
    is_label = {i for ids in label_slots.values() for i in ids if i not in inline}

    def value_for(field: str) -> tuple[list[Slot], str] | None:
        """H.2 : valeur sous le libellé, sinon à droite, sinon sur la même ligne."""
        for label_id in label_slots.get(field, []):
            if label_id in inline:
                return [by_id[label_id]], inline[label_id][1]
            label = by_id[label_id]
            below = [
                (g, s)
                for s in slots
                if s.id not in is_label and (g := _below(label, s)) is not None
            ]
            if below:
                best = min(below, key=lambda x: x[0])[1]
                return [best], best.text
            right = [
                (g, s)
                for s in slots
                if s.id not in is_label and (g := _right(label, s)) is not None
            ]
            if right:
                best = min(right, key=lambda x: x[0])[1]
                return [best], best.text
        return None

    value_slots = [s for s in slots if s.id not in is_label]

    # H.1 — noms guidés par la MRZ
    if mrz is not None and "surname" in mrz.fields:
        surname, given, truncated = _mrz_names(mrz)
        for field, mrz_name in (("surname", surname), ("given_names", given)):
            if not mrz_name:
                continue
            labelled = value_for(field)
            pool = ([labelled[0][0]] if labelled else []) + value_slots
            match: tuple[Slot, str] | None = None
            close: list[str] = []
            for s in pool:
                for text in _name_variants(s):
                    if names_match(mrz_name, text, truncated=truncated and field == "surname"):
                        match = (s, text)
                        break
                    if fuzz.ratio(fold(text), mrz_name) >= 85 and text not in close:
                        close.append(text)
                if match:
                    break
            if match:
                s, text = match
                fields[field] = _field(
                    field, [s], text, "mrz_guided", confirms_mrz=True,
                    value_native=None if _latin(text) else text,
                )  # fmt: skip
            elif labelled is not None:
                lslots, text = labelled
                text = _clean_name(text)
                if text:
                    fields[field] = _field(field, lslots, text, "label")
                    similar = max(
                        fuzz.ratio(fold(text), mrz_name), fuzz.ratio(fold(anyascii(text)), mrz_name)
                    )
                    if similar < 60:
                        conflicts.append("VIZ_MRZ_NAME_CONFLICT")
            if close:
                candidates[field] = close[:5]

    # H.1 / H.3 — dates
    all_dates: list[tuple[Slot, ParsedDate]] = [
        (s, d) for s in value_slots for r in s.readings for d in parse_dates(r.text)
    ]
    for field in ("date_of_birth", "date_of_expiry"):
        mrz_raw = mrz.fields[field].raw if mrz is not None and field in mrz.fields else None
        labelled = value_for(field)
        labelled_dates = [(labelled[0][0], d) for d in parse_dates(labelled[1])] if labelled else []
        if mrz_raw and mrz_raw.isdigit() and len(mrz_raw) == 6:
            yy, mm, dd = int(mrz_raw[:2]), int(mrz_raw[2:4]), int(mrz_raw[4:6])
            hits = [(s, d) for s, d in labelled_dates + all_dates if matches_mrz(d, yy, mm, dd)]
            four = [(s, d) for s, d in hits if d.year_digits == 4]
            chosen = (four or hits)[:1]
            if chosen:
                s, d = chosen[0]
                fields[field] = _field(
                    field, [s], d.value.isoformat(), "mrz_guided", confirms_mrz=True,
                    year4=d.value.year if d.year_digits == 4 else None,
                )  # fmt: skip
            elif labelled_dates:
                s, d = labelled_dates[0]
                fields[field] = _field(field, [s], d.value.isoformat(), "label")
                conflicts.append(
                    "VIZ_MRZ_DOB_CONFLICT"
                    if field == "date_of_birth"
                    else "VIZ_MRZ_EXPIRY_CONFLICT"
                )
        elif labelled_dates:
            s, d = labelled_dates[0]
            fields[field] = _field(
                field, [s], d.value.isoformat(), "label",
                year4=d.value.year if d.year_digits == 4 else None,
            )  # fmt: skip

    # H.1 — n° de document
    number = _mrz_value(mrz, "document_number")
    alternatives = (
        mrz.fields["document_number"].alternatives
        if mrz is not None and "document_number" in mrz.fields
        else []
    )
    if number:
        wanted = [number, *alternatives]
        for s in value_slots:
            compact = re.sub(r"[\s<]", "", s.text.upper())
            hit = next((w for w in wanted if w and w in compact), None)
            if hit:
                fields["document_number"] = _field(
                    "document_number", [s], hit, "mrz_guided", confirms_mrz=hit == number,
                    candidates=[w for w in wanted if w != hit],
                )  # fmt: skip
                break
    elif (labelled := value_for("document_number")) is not None:
        lslots, text = labelled
        compact = re.sub(r"\s", "", text.upper())
        if compact:
            fields["document_number"] = _field("document_number", lslots, compact, "label")

    # H.2 / H.3 — autres champs ancrés sur leur libellé
    for field in ("place_of_birth", "authority", "usage_name", "personal_number", "nationality"):
        labelled = value_for(field)
        if labelled is None:
            continue
        lslots, text = labelled
        text = " ".join(text.split())
        if field == "usage_name":
            text = USAGE_PREFIX.sub("", text)
        if text:
            fields[field] = _field(field, lslots, text, "label")
    if mrz is None:
        for field in ("surname", "given_names"):
            if field not in fields and (labelled := value_for(field)) is not None:
                lslots, text = labelled
                if text := _clean_name(text):
                    fields[field] = _field(field, lslots, text, "label")
    # « ép. MARTIN » écrit à la suite du nom, sans libellé dédié
    if "usage_name" not in fields:
        for s in value_slots:
            m = USAGE_PREFIX.match(s.text)
            if m:
                fields["usage_name"] = _field("usage_name", [s], s.text[m.end() :].strip(), "type")
                break

    # Sexe
    labelled = value_for("sex")
    if labelled is not None:
        lslots, text = labelled
        token = text.strip().upper().replace(" ", "")
        if token in SEX_TOKENS:
            fields["sex"] = _field("sex", lslots, SEX_TOKENS[token], "label")

    # Date de délivrance (champ particulier)
    labelled = value_for("date_of_issue")
    if labelled is not None:
        lslots, text = labelled
        parsed = parse_dates(text)
        if parsed:
            issued = parsed[0].value
            fields["date_of_issue"] = _field(
                "date_of_issue", lslots, issued.isoformat(), "label",
                year4=issued.year if parsed[0].year_digits == 4 else None,
            )  # fmt: skip
            alerts.extend(_issue_checks(issued, fields, mrz))

    return Extraction(
        fields=fields,
        label_slots=label_slots,
        conflicts=sorted(set(conflicts)),
        alerts=sorted(set(alerts)),
        candidates=candidates,
    )


def _latin(text: str) -> bool:
    return all(not (ch.isalpha() and "LATIN" not in unicodedata.name(ch, "")) for ch in text)


def _iso(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _issue_checks(issued: date, fields: dict[str, VizField], mrz: MrzResult | None) -> list[str]:
    """Délivrance ≤ expiration et ≥ naissance ; durée de validité habituelle (5 ou 10 ans)."""
    born = _iso(_mrz_value(mrz, "date_of_birth")) or _iso(
        fields["date_of_birth"].value if "date_of_birth" in fields else None
    )
    expires = _iso(_mrz_value(mrz, "date_of_expiry")) or _iso(
        fields["date_of_expiry"].value if "date_of_expiry" in fields else None
    )
    alerts: list[str] = []
    if (expires and issued > expires) or (born and issued < born):
        alerts.append("ISSUE_DATE_INCONSISTENT")
    elif expires:
        years = (expires - issued).days / 365.25
        if not any(abs(years - v) <= 0.1 for v in VALIDITY_YEARS):
            alerts.append("ISSUE_VALIDITY_UNUSUAL")
    return alerts
