"""E.6 — Réparation guidée par les sommes de contrôle.

1. Projection de type (sûre : le type de chaque position est imposé par la norme).
2. Recherche bornée (au plus 2 positions modifiées) pour chaque chiffre de contrôle faux,
   à partir des alternatives du vote et de la matrice de confusion OCR-B.
3. Filtre par la somme composite.

Une variante unique est acceptée ; plusieurs → la plus probable est proposée et le champ
passe « À vérifier » ; aucune → « À vérifier », MRZ invalide.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from idreader.pipeline.mrz.checkdigit import FILLER, check_digit_ok
from idreader.pipeline.mrz.formats import FormatSpec, Kind, Pos, ResolvedCheck, resolve_checks
from idreader.pipeline.mrz.vote import VotedMrz

# E.6.2 — projection de type
NUMERIC_PROJECTION = {"O": "0", "Q": "0", "D": "0", "U": "0", "I": "1", "L": "1", "Z": "2"}
NUMERIC_PROJECTION |= {"S": "5", "G": "6", "T": "7", "B": "8"}
ALPHA_PROJECTION = {"0": "O", "1": "I", "2": "Z", "5": "S", "6": "G", "8": "B"}

# Matrice de confusion OCR-B de départ (E.6), à apprendre ensuite des corrections humaines.
# Groupes de E.6 + confusions invisibles de E.5 (S↔8, G↔6, B↔1, Z↔5, U↔0).
CONFUSION_GROUPS = ("0ODQU", "1IL", "2Z", "5S", "6G", "8B", "<K", "S8", "G6", "B1", "Z5", "U0")
CONFUSION: dict[str, frozenset[str]] = {}
for _group in CONFUSION_GROUPS:
    for _c in _group:
        CONFUSION[_c] = CONFUSION.get(_c, frozenset()) | frozenset(_group) - {_c}

DIGITS = frozenset("0123456789")
LETTERS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
ALLOWED: dict[Kind, frozenset[str]] = {
    Kind.NUMERIC: DIGITS,
    Kind.CHECK: DIGITS,
    Kind.ALPHA: LETTERS | {FILLER},
    Kind.ALNUM: DIGITS | LETTERS | {FILLER},
    Kind.SEX: frozenset("MFX<"),
}
MAX_CHANGES = 2
MAX_COMBINATIONS = 512
CONFUSION_PRIOR = 0.05  # probabilité d'un caractère absent du vote mais confusable


@dataclass(frozen=True)
class Change:
    line: int
    index: int
    read: str
    value: str
    reason: str  # type_projection | check_repair | composite_repair


@dataclass
class CheckOutcome:
    name: str
    valid_as_read: bool  # après projection de type, avant recherche
    valid: bool  # après réparation
    repaired: bool = False
    ambiguous: bool = False
    alternatives: list[dict[Pos, str]] = field(default_factory=list)


@dataclass
class RepairResult:
    lines: list[str]
    changes: list[Change]
    checks: dict[str, CheckOutcome]
    resolved: list[ResolvedCheck]


def _get(lines: list[list[str]], pos: Pos) -> str:
    return lines[pos[0]][pos[1]]


def _check_ok(lines: list[list[str]], check: ResolvedCheck) -> bool:
    data = "".join(_get(lines, p) for p in check.data)
    return check_digit_ok(data, _get(lines, check.digit), filler_allowed=check.filler_allowed)


def project_types(spec: FormatSpec, lines: list[str]) -> tuple[list[list[str]], list[Change]]:
    kinds = spec.kinds()
    grid = [list(line) for line in lines]
    changes: list[Change] = []
    for i, row in enumerate(grid):
        for j, c in enumerate(row):
            kind = kinds[i][j]
            new = None
            if kind in (Kind.NUMERIC, Kind.CHECK):
                new = NUMERIC_PROJECTION.get(c)
            elif kind is Kind.ALPHA:
                new = ALPHA_PROJECTION.get(c)
            if new is not None:
                row[j] = new
                changes.append(Change(i, j, c, new, "type_projection"))
    return grid, changes


def _candidates(
    pos: Pos, current: str, read: str, kind: Kind, voted: VotedMrz, filler_ok: bool
) -> dict[str, float]:
    """Caractères possibles en `pos` (hors caractère courant) avec leur probabilité."""
    allowed = ALLOWED[kind] | ({FILLER} if filler_ok else frozenset())
    vote = voted.vote_at(*pos)
    probs: dict[str, float] = {}
    for c, share in vote.alternatives.items():
        probs[c] = share
    for base in {current, read}:
        for c in CONFUSION.get(base, frozenset()):
            probs.setdefault(c, CONFUSION_PRIOR)
    # alternatives lues comme lettre en position numérique : on les projette aussi
    for c in list(probs):
        if kind in (Kind.NUMERIC, Kind.CHECK) and c in NUMERIC_PROJECTION:
            probs.setdefault(NUMERIC_PROJECTION[c], probs[c])
    return {c: p for c, p in probs.items() if c in allowed and c != current}


def _search(
    grid: list[list[str]],
    check: ResolvedCheck,
    positions: list[Pos],
    candidates: dict[Pos, dict[str, float]],
) -> list[tuple[float, dict[Pos, str]]]:
    """Variantes minimales (1 puis 2 positions) qui valident `check`, triées par probabilité."""
    for k in range(1, MAX_CHANGES + 1):
        found: list[tuple[float, dict[Pos, str]]] = []
        for combo in itertools.combinations([p for p in positions if candidates.get(p)], k):
            for chars in itertools.product(*(candidates[p].items() for p in combo)):
                trial = [row[:] for row in grid]
                for p, (c, _) in zip(combo, chars, strict=True):
                    trial[p[0]][p[1]] = c
                if _check_ok(trial, check):
                    score = 1.0
                    for _, (_, prob) in zip(combo, chars, strict=True):
                        score *= prob
                    found.append((score, {p: c for p, (c, _) in zip(combo, chars, strict=True)}))
        if found:
            found.sort(key=lambda v: -v[0])
            return found
    return []


def repair(spec: FormatSpec, voted: VotedMrz) -> RepairResult:
    grid, changes = project_types(spec, voted.lines)
    read_grid = [list(line) for line in voted.lines]
    kinds = spec.kinds()
    resolved = resolve_checks(spec, ["".join(r) for r in grid])
    own = [c for c in resolved if c.name != "composite"]
    composite = next((c for c in resolved if c.name == "composite"), None)

    def cands(pos: Pos, filler_ok: bool) -> dict[str, float]:
        return _candidates(
            pos, _get(grid, pos), _get(read_grid, pos), kinds[pos[0]][pos[1]], voted, filler_ok
        )

    outcomes: dict[str, CheckOutcome] = {}
    variants: dict[str, list[tuple[float, dict[Pos, str]]]] = {}
    for check in own:
        ok = _check_ok(grid, check)
        outcomes[check.name] = CheckOutcome(check.name, ok, ok)
        if ok:
            continue
        positions = [*check.data, check.digit]
        table = {p: cands(p, check.filler_allowed and p == check.digit) for p in positions}
        variants[check.name] = _search(grid, check, positions, table)

    # Combinaison des variantes des champs faux, filtrée par la somme composite.
    names = [n for n in variants if variants[n]]
    combos: list[tuple[float, dict[Pos, str]]] = []
    for picks in itertools.islice(
        itertools.product(*(variants[n] for n in names)), MAX_COMBINATIONS
    ):
        merged: dict[Pos, str] = {}
        score = 1.0
        for s, v in picks:
            merged |= v
            score *= s
        # La composite ne départage que si tous les champs faux ont une variante : sinon
        # le champ sans variante la fait échouer de toute façon.
        if composite is not None and len(names) == len(variants):
            trial = [row[:] for row in grid]
            for p, c in merged.items():
                trial[p[0]][p[1]] = c
            if not _check_ok(trial, composite):
                continue
        combos.append((score, merged))
    combos.sort(key=lambda v: -v[0])

    if names and combos:
        best = combos[0][1]
        for p, c in best.items():
            changes.append(Change(p[0], p[1], _get(grid, p), c, "check_repair"))
            grid[p[0]][p[1]] = c
        for n in names:
            check_positions = {p for _, v in variants[n] for p in v}
            distinct = {
                tuple(sorted((p, c) for p, c in v.items() if p in check_positions))
                for _, v in combos
            }
            outcomes[n].valid = True
            outcomes[n].repaired = True
            outcomes[n].ambiguous = len(distinct) > 1
            outcomes[n].alternatives = [v for _, v in combos[1:6]]

    if composite is not None:
        ok = _check_ok(grid, composite)
        outcome = CheckOutcome("composite", ok, ok)
        all_fields_ok = all(o.valid for o in outcomes.values())
        if not ok and all_fields_ok:
            # Erreur dans une partie que seule la composite contrôle (données facultatives
            # du TD1, chiffre composite lui-même).
            covered = {p for c in own for p in (*c.data, c.digit)}
            positions = [p for p in composite.data if p not in covered] + [composite.digit]
            table = {p: cands(p, False) for p in positions}
            found = _search(grid, composite, positions, table)
            if found:
                for p, c in found[0][1].items():
                    changes.append(Change(p[0], p[1], _get(grid, p), c, "composite_repair"))
                    grid[p[0]][p[1]] = c
                outcome = CheckOutcome(
                    "composite",
                    False,
                    True,
                    repaired=True,
                    ambiguous=len(found) > 1,
                    alternatives=[v for _, v in found[1:6]],
                )
        outcomes["composite"] = outcome

    return RepairResult(
        lines=["".join(r) for r in grid],
        changes=changes,
        checks=outcomes,
        resolved=resolve_checks(spec, ["".join(r) for r in grid]),
    )
