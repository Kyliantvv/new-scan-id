"""E.4 — Formats de MRZ : positions des champs et des chiffres de contrôle.

Positions écrites comme dans la norme (1 = premier caractère, bornes incluses) :
ICAO 9303 partie 4 (TD3), partie 5 (TD1), partie 6 (TD2), partie 7 (MRV-A, MRV-B).
L'ancienne CNI française (2 × 36) suit un format national (03_PIPELINE_ANALYSE.md § E.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from idreader.pipeline.mrz.checkdigit import FILLER


class MrzFormat(StrEnum):
    TD1 = "TD1"
    TD2 = "TD2"
    TD3 = "TD3"
    MRVA = "MRV-A"
    MRVB = "MRV-B"
    FRA_ID = "FRA-ID"  # ancienne CNI française (avant 2021)


class Kind(StrEnum):
    """Type imposé par la norme à une position (sert à la projection de type, E.6)."""

    ALPHA = "alpha"  # A–Z et <
    NUMERIC = "numeric"  # 0–9 (dates)
    ALNUM = "alnum"  # A–Z, 0–9, <
    SEX = "sex"  # M, F, X, <
    CHECK = "check"  # 0–9 (ou < si le champ contrôlé est vide et que la norme l'admet)


Pos = tuple[int, int]  # (ligne, index) tous deux à partir de 0


@dataclass(frozen=True)
class FieldSpec:
    name: str
    line: int  # 0-based
    start: int  # 1-based, comme dans la norme
    end: int  # 1-based, inclus
    kind: Kind

    def positions(self) -> list[Pos]:
        return [(self.line, i) for i in range(self.start - 1, self.end)]


@dataclass(frozen=True)
class CheckSpec:
    name: str  # champ contrôlé, ou "composite"
    digit: Pos  # (ligne, index) à partir de 0, contrairement à `covers`
    covers: tuple[tuple[int, int, int], ...]  # (ligne, début, fin) 1-based inclus
    filler_allowed: bool = False


@dataclass(frozen=True)
class FormatSpec:
    format: MrzFormat
    lines: int
    length: int
    fields: tuple[FieldSpec, ...]
    checks: tuple[CheckSpec, ...]
    has_composite: bool

    def field(self, name: str) -> FieldSpec:
        for f in self.fields:
            if f.name == name:
                return f
        raise KeyError(name)

    def kinds(self) -> list[list[Kind]]:
        table = [[Kind.ALNUM] * self.length for _ in range(self.lines)]
        for f in self.fields:
            for line, idx in f.positions():
                table[line][idx] = f.kind
        for c in self.checks:
            table[c.digit[0]][c.digit[1]] = Kind.CHECK
        return table


@dataclass(frozen=True)
class ResolvedCheck:
    """Chiffre de contrôle appliqué à une MRZ donnée (positions exactes)."""

    name: str
    data: tuple[Pos, ...]
    digit: Pos
    filler_allowed: bool = False
    field_names: tuple[str, ...] = field(default=())


def _f(name: str, line: int, start: int, end: int, kind: Kind) -> FieldSpec:
    return FieldSpec(name, line, start, end, kind)


A, N, X, S = Kind.ALPHA, Kind.NUMERIC, Kind.ALNUM, Kind.SEX

# Ligne 2 commune à TD3, TD2, MRV-A et MRV-B jusqu'à la position 28.
# ICAO 9303 partie 4, §4.2.2.2 ; partie 6, §4.2.2.2 ; partie 7, §4.2.2.
_L2_COMMON = (
    _f("document_number", 1, 1, 9, X),
    _f("nationality", 1, 11, 13, A),
    _f("date_of_birth", 1, 14, 19, N),
    _f("sex", 1, 21, 21, S),
    _f("date_of_expiry", 1, 22, 27, N),
)
_L2_COMMON_CHECKS = (
    CheckSpec("document_number", (1, 9), ((1, 1, 9),)),
    CheckSpec("date_of_birth", (1, 19), ((1, 14, 19),)),
    CheckSpec("date_of_expiry", (1, 27), ((1, 22, 27),)),
)


def _header(length: int) -> tuple[FieldSpec, ...]:
    return (
        _f("document_code", 0, 1, 2, A),
        _f("issuing_state", 0, 3, 5, A),
        _f("name", 0, 6, length, A),
    )


SPECS: dict[MrzFormat, FormatSpec] = {
    # ICAO 9303 partie 4, §4.2.2
    MrzFormat.TD3: FormatSpec(
        MrzFormat.TD3,
        2,
        44,
        (*_header(44), *_L2_COMMON, _f("optional_data", 1, 29, 42, X)),
        (
            *_L2_COMMON_CHECKS,
            CheckSpec("optional_data", (1, 42), ((1, 29, 42),), filler_allowed=True),
            CheckSpec("composite", (1, 43), ((1, 1, 10), (1, 14, 20), (1, 22, 43))),
        ),
        True,
    ),
    # ICAO 9303 partie 6, §4.2.2
    MrzFormat.TD2: FormatSpec(
        MrzFormat.TD2,
        2,
        36,
        (*_header(36), *_L2_COMMON, _f("optional_data", 1, 29, 35, X)),
        (
            *_L2_COMMON_CHECKS,
            CheckSpec("composite", (1, 35), ((1, 1, 10), (1, 14, 20), (1, 22, 35))),
        ),
        True,
    ),
    # ICAO 9303 partie 7, §4.2.2 (MRV-A) : pas de somme composite
    MrzFormat.MRVA: FormatSpec(
        MrzFormat.MRVA,
        2,
        44,
        (*_header(44), *_L2_COMMON, _f("optional_data", 1, 29, 44, X)),
        _L2_COMMON_CHECKS,
        False,
    ),
    # ICAO 9303 partie 7, §5.2.2 (MRV-B)
    MrzFormat.MRVB: FormatSpec(
        MrzFormat.MRVB,
        2,
        36,
        (*_header(36), *_L2_COMMON, _f("optional_data", 1, 29, 36, X)),
        _L2_COMMON_CHECKS,
        False,
    ),
    # ICAO 9303 partie 5, §4.2.2
    MrzFormat.TD1: FormatSpec(
        MrzFormat.TD1,
        3,
        30,
        (
            _f("document_code", 0, 1, 2, A),
            _f("issuing_state", 0, 3, 5, A),
            _f("document_number", 0, 6, 14, X),
            _f("optional_data_1", 0, 16, 30, X),
            _f("date_of_birth", 1, 1, 6, N),
            _f("sex", 1, 8, 8, S),
            _f("date_of_expiry", 1, 9, 14, N),
            _f("nationality", 1, 16, 18, A),
            _f("optional_data_2", 1, 19, 29, X),
            _f("name", 2, 1, 30, A),
        ),
        (
            CheckSpec("document_number", (0, 14), ((0, 6, 14),)),
            CheckSpec("date_of_birth", (1, 6), ((1, 1, 6),)),
            CheckSpec("date_of_expiry", (1, 14), ((1, 9, 14),)),
            CheckSpec("composite", (1, 29), ((0, 6, 30), (1, 1, 7), (1, 9, 15), (1, 19, 29))),
        ),
        True,
    ),
    # Ancienne CNI française, format national (vérifié sur le spécimen de § E.8)
    MrzFormat.FRA_ID: FormatSpec(
        MrzFormat.FRA_ID,
        2,
        36,
        (
            _f("document_code", 0, 1, 2, A),
            _f("issuing_state", 0, 3, 5, A),
            _f("surname", 0, 6, 30, A),
            _f("admin_code", 0, 31, 36, X),
            _f("document_number", 1, 1, 12, X),
            _f("given_names", 1, 14, 27, A),
            _f("date_of_birth", 1, 28, 33, N),
            _f("sex", 1, 35, 35, S),
        ),
        (
            CheckSpec("document_number", (1, 12), ((1, 1, 12),)),
            CheckSpec("date_of_birth", (1, 33), ((1, 28, 33),)),
            CheckSpec("composite", (1, 35), ((0, 1, 36), (1, 1, 35))),
        ),
        True,
    ),
}

SHAPES: dict[tuple[int, int], tuple[MrzFormat, ...]] = {
    (2, 44): (MrzFormat.TD3, MrzFormat.MRVA),
    (2, 36): (MrzFormat.TD2, MrzFormat.MRVB, MrzFormat.FRA_ID),
    (3, 30): (MrzFormat.TD1,),
}


def detect_format(lines: list[str]) -> MrzFormat | None:
    """Format d'après la forme (lignes × longueur) puis le contenu.

    TD3/MRV-A et TD2/MRV-B se distinguent par le premier caractère (`P` ou `V`) ;
    l'ancienne CNI française par `IDFRA` et une ligne 2 sans code de nationalité
    en positions 11–13.
    """
    if not lines:
        return None
    shape = (len(lines), len(lines[0]))
    if any(len(line) != shape[1] for line in lines) or shape not in SHAPES:
        return None
    first = lines[0][:1]
    if shape == (3, 30):
        return MrzFormat.TD1
    if shape == (2, 44):
        return MrzFormat.MRVA if first == "V" else MrzFormat.TD3
    if first == "V":
        return MrzFormat.MRVB
    if lines[0].startswith("IDFRA") and not lines[1][10:13].isalpha():
        return MrzFormat.FRA_ID
    return MrzFormat.TD2


def _range_positions(line: int, start: int, end: int) -> list[Pos]:
    return [(line, i) for i in range(start - 1, end)]


def char_at(lines: list[str], pos: Pos) -> str:
    return lines[pos[0]][pos[1]]


def long_number_extension(spec: FormatSpec, lines: list[str]) -> list[Pos] | None:
    """Positions de la suite d'un n° de document de plus de 9 caractères.

    ICAO 9303 partie 5, §4.2.4 (TD1) et partie 6, note j (TD2) : `<` à la place du chiffre
    de contrôle, puis la suite du numéro et son chiffre de contrôle au début des
    données facultatives. Renvoie les positions de la suite **chiffre compris**.
    """
    if spec.format not in (MrzFormat.TD1, MrzFormat.TD2):
        return None
    check = next(c for c in spec.checks if c.name == "document_number")
    if char_at(lines, check.digit) != FILLER:
        return None
    opt = spec.field("optional_data_1" if spec.format is MrzFormat.TD1 else "optional_data")
    ext: list[Pos] = []
    for pos in opt.positions():
        if char_at(lines, pos) == FILLER:
            break
        ext.append(pos)
    return ext if len(ext) >= 2 else None


def resolve_checks(spec: FormatSpec, lines: list[str]) -> list[ResolvedCheck]:
    """Chiffres de contrôle applicables à ces lignes (n° long compris)."""
    resolved: list[ResolvedCheck] = []
    ext = long_number_extension(spec, lines)
    for c in spec.checks:
        data = [p for line, s, e in c.covers for p in _range_positions(line, s, e)]
        digit = c.digit
        names: tuple[str, ...] = (c.name,)
        if c.name == "document_number" and ext:
            data = data + ext[:-1]
            digit = ext[-1]
        if c.name == "composite":
            names = tuple(f.name for f in spec.fields if set(f.positions()) & set(data))
        resolved.append(ResolvedCheck(c.name, tuple(data), digit, c.filler_allowed, names))
    return resolved


def field_value(spec: FormatSpec, lines: list[str], name: str) -> str:
    """Valeur brute d'un champ (remplissage compris), n° long reconstitué."""
    f = spec.field(name)
    raw = "".join(char_at(lines, p) for p in f.positions())
    if name == "document_number":
        ext = long_number_extension(spec, lines)
        if ext:
            raw = raw + "".join(char_at(lines, p) for p in ext[:-1])
    return raw
