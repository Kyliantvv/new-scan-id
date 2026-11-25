"""Étape J : appariement recto/verso au niveau du lot (docs/03_PIPELINE_ANALYSE.md § J).

Score entre un recadrage avec MRZ (verso d'une carte TD1) et un recadrage sans MRZ de même
format physique :

| Indice                                                      | Points |
|-------------------------------------------------------------|--------|
| même page, deux recadrages côte à côte                      | + 0,6  |
| fichiers consécutifs dans l'ordre d'upload du même lot      | + 0,3  |
| n° de document de la MRZ retrouvé dans l'OCR de l'autre face | + 0,5  |
| nom ou date de naissance de la MRZ retrouvé sur l'autre face | + 0,3  |

Appariement automatique si score ≥ 0,8 et aucun concurrent à moins de 0,2 (ni pour l'une,
ni pour l'autre face) ; sinon association suggérée. Fonctions pures.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from idreader.engines.base import Quad
from idreader.pipeline.dates import parse_dates
from idreader.pipeline.mrz.decode import MrzResult
from idreader.pipeline.mrz.names import names_match
from idreader.pipeline.viz_ocr import Slot, VizOcr

AUTO_MIN = 0.8
COMPETITOR_GAP = 0.2
SUGGEST_MIN = 0.3
SAME_PAGE = 0.6
CONSECUTIVE = 0.3
NUMBER_FOUND = 0.5
NAME_OR_DOB_FOUND = 0.3
# Les cadres du verso sont décalés dans la zone visuelle fusionnée : aucune relation
# géométrique (libellé au-dessus, à gauche) ne traverse d'une face à l'autre.
BACK_SLOT_OFFSET = 100_000
BACK_Y_OFFSET = 1_000_000.0
PAIRABLE_FORMATS = frozenset({"ID1", "unknown"})
ALNUM = re.compile(r"[^0-9A-Z]")


@dataclass(frozen=True)
class Side:
    """Un recadrage (document non apparié) vu par l'appariement."""

    key: str  # identifiant du document
    file_order: int  # rang d'upload du fichier dans le lot
    page_key: str  # identifiant de page (même page = même scan)
    physical_format: str
    mrz: MrzResult | None
    texts: tuple[str, ...] = ()

    @property
    def mrz_side(self) -> bool:
        return self.mrz is not None


@dataclass(frozen=True)
class Pair:
    mrz_key: str
    other_key: str
    score: float
    hints: tuple[str, ...] = field(default=())


def _norm(text: str) -> str:
    return ALNUM.sub("", text.upper())


def score(a: Side, b: Side) -> Pair:
    """`a` porte la MRZ, `b` non."""
    if a.mrz is None:
        raise ValueError("la première face doit porter la MRZ")
    total, hints = 0.0, []
    if a.page_key == b.page_key:
        total += SAME_PAGE
        hints.append("same_page")
    elif abs(a.file_order - b.file_order) == 1:
        total += CONSECUTIVE
        hints.append("consecutive_files")
    number = a.mrz.fields.get("document_number")
    joined = _norm(" ".join(b.texts))
    if number and number.value and len(number.value) >= 5 and _norm(number.value) in joined:
        total += NUMBER_FOUND
        hints.append("number")
    surname = a.mrz.fields.get("surname")
    dob = a.mrz.fields.get("date_of_birth")
    found = False
    if surname and surname.value:
        found = any(names_match(surname.value, t) for t in b.texts)
    if not found and dob and dob.value:
        found = any(d.value.isoformat() == dob.value for t in b.texts for d in parse_dates(t))
    if found:
        total += NAME_OR_DOB_FOUND
        hints.append("name_or_dob")
    return Pair(a.key, b.key, round(total, 3), tuple(hints))


def pair_sides(sides: Sequence[Side]) -> tuple[list[Pair], list[Pair]]:
    """Paires automatiques et associations suggérées (une face au plus par paire)."""
    with_mrz = [s for s in sides if s.mrz_side and s.physical_format in PAIRABLE_FORMATS]
    without = [s for s in sides if not s.mrz_side and s.physical_format in PAIRABLE_FORMATS]
    scored = [score(a, b) for a in with_mrz for b in without]
    scored = [p for p in scored if p.score > 0]
    by_a: dict[str, list[Pair]] = {}
    by_b: dict[str, list[Pair]] = {}
    for p in scored:
        by_a.setdefault(p.mrz_key, []).append(p)
        by_b.setdefault(p.other_key, []).append(p)

    def runner_up(pairs: list[Pair], best: Pair) -> float:
        return max((p.score for p in pairs if p is not best), default=0.0)

    auto: list[Pair] = []
    used: set[str] = set()
    for p in sorted(scored, key=lambda x: -x.score):
        if p.mrz_key in used or p.other_key in used or p.score < AUTO_MIN:
            continue
        rivals = max(runner_up(by_a[p.mrz_key], p), runner_up(by_b[p.other_key], p))
        if p.score - rivals < COMPETITOR_GAP:
            continue
        auto.append(p)
        used.update((p.mrz_key, p.other_key))
    suggested: list[Pair] = []
    for p in sorted(scored, key=lambda x: -x.score):
        if p.mrz_key in used or p.other_key in used or p.score < SUGGEST_MIN:
            continue
        suggested.append(p)
        used.update((p.mrz_key, p.other_key))
    return auto, suggested


def _shift(slot: Slot) -> Slot:
    pts = tuple((x, y + BACK_Y_OFFSET) for x, y in slot.quad.points)
    return slot.model_copy(update={"id": slot.id + BACK_SLOT_OFFSET, "quad": Quad(points=pts)})


def combine_viz(front: VizOcr, back: VizOcr) -> VizOcr:
    """Zone visuelle des deux faces en une seule, verso décalé (H appliquée aux deux faces)."""
    return VizOcr(
        engines=sorted({*front.engines, *back.engines}),
        languages=list(dict.fromkeys([*front.languages, *back.languages])),
        slots=[*front.slots, *(_shift(s) for s in back.slots)],
        engine_errors=[*front.engine_errors, *back.engine_errors],
        ms=front.ms + back.ms,
    )


def slot_side(slot_id: int) -> str:
    return "back" if slot_id >= BACK_SLOT_OFFSET else "front"


def unshift_bbox(slot: Slot) -> tuple[str, tuple[float, float, float, float]]:
    x0, y0, x1, y1 = slot.bbox
    if slot.id >= BACK_SLOT_OFFSET:
        return "back", (x0, y0 - BACK_Y_OFFSET, x1, y1 - BACK_Y_OFFSET)
    return "front", (x0, y0, x1, y1)
