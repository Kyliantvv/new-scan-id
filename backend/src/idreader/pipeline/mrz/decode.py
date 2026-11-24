"""E.4 → E.7 assemblés : lignes votées → champs MRZ avec statut et preuves.

Statuts de champ :
- `valid`      : prouvé tel que lu (chiffre de contrôle, composite, ou contraintes + vote
                 unanime pour les champs que la norme ne contrôle pas) ;
- `repaired`   : corrigé, et la correction est prouvée (hors angle mort, variante unique) ;
- `unverified` : non contrôlé par la norme et lu par un seul moteur ;
- `to_review`  : « À vérifier » (angle mort non confirmé, réparation ambiguë, siècle incertain…) ;
- `invalid`    : faux et non réparable, ou valeur impossible.

Les confiances sont des valeurs **de départ** (03_PIPELINE_ANALYSE.md § L), à recalibrer
sur le banc golden (phase 1b).
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from idreader.pipeline.mrz.checkdigit import FILLER, blind_spot_partners
from idreader.pipeline.mrz.formats import (
    SPECS,
    FieldSpec,
    Kind,
    MrzFormat,
    detect_format,
    field_value,
    long_number_extension,
)
from idreader.pipeline.mrz.names import split_name
from idreader.pipeline.mrz.repair import Change, RepairResult, repair
from idreader.pipeline.mrz.vote import VotedMrz
from idreader.pipeline.reference import doc_number_patterns, mrz_state_codes


class MrzFieldStatus(StrEnum):
    VALID = "valid"
    REPAIRED = "repaired"
    UNVERIFIED = "unverified"
    TO_REVIEW = "to_review"
    INVALID = "invalid"


CONFIDENCE = {
    "check_digit": 0.99,  # chiffre de contrôle valide sans réparation
    "repaired": 0.97,  # réparé hors angle mort, réparation unique
    "blind_spot": 0.60,  # angle mort sans confirmation
    "constrained_unanimous": 0.95,  # non contrôlé, contraintes OK, ≥ 2 moteurs d'accord
    "unanimous": 0.90,  # non contrôlé, ≥ 2 moteurs d'accord
    "constrained_single": 0.80,
    "single": 0.75,
    "invalid": 0.20,
}

# Codes de raison stables (libellés en français pour l'interface).
REASONS = {
    "MRZ_CHECK_FAILED": "Chiffre de contrôle faux, aucune réparation trouvée",
    "MRZ_REPAIR_AMBIGUOUS": "Plusieurs corrections possibles",
    "MRZ_BLIND_SPOT": "Caractère ambigu que la somme de contrôle ne peut pas vérifier",
    "MRZ_COMPOSITE_FAILED": "Somme de contrôle globale fausse",
    "MRZ_INVALID_DATE": "Date impossible",
    "MRZ_CENTURY_UNCERTAIN": "Siècle de naissance incertain",
    "MRZ_AGE_IMPLAUSIBLE": "Âge impossible",
    "MRZ_EXPIRY_BEFORE_BIRTH": "Expiration antérieure à la naissance",
    "MRZ_UNKNOWN_COUNTRY": "Code pays inconnu",
    "MRZ_INVALID_SEX": "Sexe illisible",
    "MRZ_NAME_TRUNCATED": "Nom peut-être tronqué dans la MRZ",
}
AGE_MAX = 120
AGE_UNCERTAIN_BELOW = 2
AGE_UNCERTAIN_ABOVE = 98


class MrzField(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    raw: str
    value: str | None
    status: MrzFieldStatus
    confidence: float
    evidence: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)


class MrzChange(BaseModel):
    model_config = ConfigDict(frozen=True)

    line: int
    index: int
    read: str
    value: str
    reason: str


class MrzResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    format: MrzFormat
    lines_read: list[str]
    lines: list[str]
    checksums_valid: bool
    checks: dict[str, bool]
    fields: dict[str, MrzField]
    changes: list[MrzChange]
    reasons: list[str]
    alerts: list[str]
    engines: list[str]

    @property
    def needs_review(self) -> bool:
        return bool(self.reasons) or any(
            f.status
            in (MrzFieldStatus.TO_REVIEW, MrzFieldStatus.INVALID, MrzFieldStatus.UNVERIFIED)
            for f in self.fields.values()
        )


def parse_yymmdd(raw: str) -> tuple[int, int, int] | None:
    if len(raw) != 6 or not raw.isdigit():
        return None
    return int(raw[:2]), int(raw[2:4]), int(raw[4:])


def _make_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def birth_date(raw: str, today: date, viz_year: int | None = None) -> date | None:
    """Siècle (E.6.5) : `20AA`, sauf si la date tombe après aujourd'hui → `19AA`.

    La zone visuelle (année sur 4 chiffres) a le dernier mot quand elle est fournie.
    """
    parts = parse_yymmdd(raw)
    if parts is None:
        return None
    yy, mm, dd = parts
    if viz_year is not None and viz_year % 100 == yy:
        return _make_date(viz_year, mm, dd)
    candidate = _make_date(2000 + yy, mm, dd)
    if candidate is not None and candidate <= today:
        return candidate
    return _make_date(1900 + yy, mm, dd)


def expiry_date(raw: str) -> date | None:
    parts = parse_yymmdd(raw)
    return None if parts is None else _make_date(2000 + parts[0], parts[1], parts[2])


def age_on(born: date, today: date) -> int:
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


class _Builder:
    def __init__(self, spec_format: MrzFormat, voted: VotedMrz, rr: RepairResult) -> None:
        self.spec = SPECS[spec_format]
        self.voted = voted
        self.rr = rr
        self.lines = rr.lines
        self.changes_at = {(c.line, c.index): c for c in rr.changes}
        self.ext = long_number_extension(self.spec, self.lines) or []

    def positions(self, f: FieldSpec) -> list[tuple[int, int]]:
        pos = f.positions()
        if f.name == "document_number":
            pos = pos + self.ext[:-1]
        return pos

    def unanimous(self, f: FieldSpec) -> bool:
        return all(self.voted.vote_at(*p).unanimous for p in self.positions(f))

    def changed(self, f: FieldSpec) -> list[Change]:
        return [self.changes_at[p] for p in self.positions(f) if p in self.changes_at]

    def covering_check(self, f: FieldSpec) -> str | None:
        """Chiffre de contrôle qui prouve ce champ : le sien, sinon la composite."""
        names = {c.name for c in self.rr.resolved}
        if f.name in names:
            return f.name
        composite = next((c for c in self.rr.resolved if c.name == "composite"), None)
        if composite is not None and set(self.positions(f)) <= set(composite.data):
            return "composite"
        return None

    def blind_spots(self, f: FieldSpec, value: str) -> list[str]:
        """Valeurs alternatives non exclues par une autre preuve (angles morts E.5)."""
        if f.kind is not Kind.ALNUM:
            return []
        positions = self.positions(f)
        chars = [self.lines[i][j] for i, j in positions]
        last = max((k for k, c in enumerate(chars) if c != FILLER), default=-1)
        state = self.lines[0][2:5].replace(FILLER, "")
        pattern = doc_number_patterns().get((state, self.lines[0][:1]))
        alternatives: list[str] = []
        for k, (pos, c) in enumerate(zip(positions, chars, strict=True)):
            if k > last:
                break
            partners = blind_spot_partners(c)
            if not partners or (pos not in self.changes_at and self.voted.vote_at(*pos).unanimous):
                continue
            for p in sorted(partners):
                swapped = [*chars[:k], p, *chars[k + 1 :]]
                alt = "".join(swapped).replace(FILLER, "")
                if (
                    f.name == "document_number"
                    and pattern is not None
                    and pattern.match(value)
                    and not pattern.match(alt)
                ):
                    continue  # le motif du pays tranche
                alternatives.append(alt)
        return alternatives


def decode(voted: VotedMrz, *, today: date, viz_birth_year: int | None = None) -> MrzResult | None:
    fmt = detect_format(voted.lines)
    if fmt is None:
        return None
    spec = SPECS[fmt]
    rr = repair(spec, voted)
    b = _Builder(fmt, voted, rr)
    lines = rr.lines
    fields: dict[str, MrzField] = {}
    reasons: list[str] = []
    alerts: list[str] = []
    states = mrz_state_codes()

    def add(
        name: str,
        raw: str,
        value: str | None,
        status: MrzFieldStatus,
        confidence: float,
        evidence: list[str],
        why: list[str],
        alternatives: list[str] | None = None,
    ) -> None:
        fields[name] = MrzField(
            name=name,
            raw=raw,
            value=value,
            status=status,
            confidence=confidence,
            evidence=evidence,
            reasons=why,
            alternatives=alternatives or [],
        )
        for r in why:
            if r not in reasons:
                reasons.append(r)

    for f in spec.fields:
        raw = (
            field_value(spec, lines, f.name)
            if f.name == "document_number"
            else "".join(lines[i][j] for i, j in f.positions())
        )
        if f.name == "optional_data_1" and b.ext:
            raw = "".join(lines[i][j] for i, j in f.positions() if (i, j) not in b.ext)
        check = b.covering_check(f)
        changes = b.changed(f)
        evidence: list[str] = []
        why: list[str] = []
        alternatives: list[str] = []
        if check is not None:
            outcome = rr.checks[check]
            evidence.append("check_digit" if check != "composite" else "composite")
            if not outcome.valid:
                status, conf = MrzFieldStatus.INVALID, CONFIDENCE["invalid"]
                why.append("MRZ_CHECK_FAILED" if check != "composite" else "MRZ_COMPOSITE_FAILED")
            elif outcome.ambiguous and changes:
                status, conf = MrzFieldStatus.TO_REVIEW, CONFIDENCE["blind_spot"]
                why.append("MRZ_REPAIR_AMBIGUOUS")
            elif changes:
                status, conf = MrzFieldStatus.REPAIRED, CONFIDENCE["repaired"]
                evidence.append("repair")
            else:
                status, conf = MrzFieldStatus.VALID, CONFIDENCE["check_digit"]
        else:
            constrained = f.name in ("issuing_state", "nationality", "sex", "document_code")
            unanimous = b.unanimous(f)
            if unanimous:
                evidence.append("vote_unanimous")
            if constrained:
                evidence.append("constraint")
            key = ("constrained_" if constrained else "") + ("unanimous" if unanimous else "single")
            conf = CONFIDENCE[key]
            status = MrzFieldStatus.VALID if unanimous else MrzFieldStatus.UNVERIFIED
        if status in (MrzFieldStatus.VALID, MrzFieldStatus.REPAIRED):
            alternatives = b.blind_spots(f, raw.replace(FILLER, ""))
            if alternatives:
                status, conf = MrzFieldStatus.TO_REVIEW, CONFIDENCE["blind_spot"]
                why.append("MRZ_BLIND_SPOT")

        value: str | None = raw.replace(FILLER, "")
        if f.name in ("issuing_state", "nationality"):
            if value not in states:
                status, conf = MrzFieldStatus.TO_REVIEW, min(conf, CONFIDENCE["blind_spot"])
                why.append("MRZ_UNKNOWN_COUNTRY")
        elif f.name == "sex":
            if raw not in ("M", "F", "X", FILLER):
                status, conf = MrzFieldStatus.INVALID, CONFIDENCE["invalid"]
                why.append("MRZ_INVALID_SEX")
            value = "X" if raw == FILLER else raw
        elif f.name == "date_of_birth":
            born = birth_date(raw, today, viz_birth_year)
            value = born.isoformat() if born else None
            if born is None:
                status, conf = MrzFieldStatus.INVALID, CONFIDENCE["invalid"]
                why.append("MRZ_INVALID_DATE")
            else:
                age = age_on(born, today)
                if age > AGE_MAX:
                    status, conf = MrzFieldStatus.INVALID, CONFIDENCE["invalid"]
                    why.append("MRZ_AGE_IMPLAUSIBLE")
                elif viz_birth_year is None and not (
                    AGE_UNCERTAIN_BELOW <= age <= AGE_UNCERTAIN_ABOVE
                ):
                    status, conf = MrzFieldStatus.TO_REVIEW, min(conf, CONFIDENCE["blind_spot"])
                    why.append("MRZ_CENTURY_UNCERTAIN")
        elif f.name == "date_of_expiry":
            expires = expiry_date(raw)
            value = expires.isoformat() if expires else None
            if expires is None:
                status, conf = MrzFieldStatus.INVALID, CONFIDENCE["invalid"]
                why.append("MRZ_INVALID_DATE")
        elif f.name in ("name", "surname", "given_names"):
            if f.name == "name":
                surname, given, truncated = split_name(raw)
                if truncated:
                    alerts.append("MRZ_NAME_TRUNCATED")
                for sub, sub_value in (("surname", surname), ("given_names", given)):
                    add(sub, raw, sub_value, status, conf, evidence, why)
                continue
            value = " ".join(raw.replace(FILLER, " ").split())
        add(f.name, raw, value, status, conf, evidence, why, alternatives)

    # Expiration postérieure à la naissance (E.6.4)
    dob, doe = fields.get("date_of_birth"), fields.get("date_of_expiry")
    if dob and doe and dob.value and doe.value and doe.value <= dob.value:
        fields["date_of_expiry"] = doe.model_copy(
            update={
                "status": MrzFieldStatus.TO_REVIEW,
                "reasons": [*doe.reasons, "MRZ_EXPIRY_BEFORE_BIRTH"],
            }
        )
        reasons.append("MRZ_EXPIRY_BEFORE_BIRTH")
    # Le n° personnel du TD3 est dans les données facultatives (ICAO 9303 partie 4, note l).
    if fmt is MrzFormat.TD3 and "optional_data" in fields:
        fields["personal_number"] = fields["optional_data"].model_copy(
            update={"name": "personal_number"}
        )
    if (
        "composite" in rr.checks
        and not rr.checks["composite"].valid
        and ("MRZ_COMPOSITE_FAILED" not in reasons)
    ):
        reasons.append("MRZ_COMPOSITE_FAILED")

    return MrzResult(
        format=fmt,
        lines_read=voted.lines,
        lines=lines,
        checksums_valid=all(o.valid for o in rr.checks.values()),
        checks={n: o.valid for n, o in rr.checks.items()},
        fields=fields,
        changes=[MrzChange(**c.__dict__) for c in rr.changes],
        reasons=reasons,
        alerts=alerts,
        engines=voted.engines,
    )
