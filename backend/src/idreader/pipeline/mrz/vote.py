"""E.3 — Alignement des lectures et vote par caractère, pondéré par moteur."""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, ConfigDict, Field

from idreader.pipeline.mrz.formats import SHAPES
from idreader.pipeline.mrz.normalize import mrz_candidate_lines, realign

# Poids de départ (03_PIPELINE_ANALYSE.md § E.3), à recalibrer sur le banc golden (phase 1b).
DEFAULT_WEIGHTS: dict[str, float] = {"vision": 1.0, "tesseract": 0.9, "ppocr": 0.8}
UNKNOWN_ENGINE_WEIGHT = 0.5
RANK_DISCOUNT = 0.5  # candidat de rang r (0 = meilleur) : poids × 0,5^r


class BandReading(BaseModel):
    """Une lecture de la bande MRZ par un moteur sur un prétraitement."""

    model_config = ConfigDict(frozen=True)

    engine: str
    variant: str = ""
    lines: list[str]
    confidence: float = 1.0
    rank: int = 0


class PositionVote(BaseModel):
    model_config = ConfigDict(frozen=True)

    char: str
    share: float  # part du poids total qui vote pour `char`
    alternatives: dict[str, float] = Field(default_factory=dict)  # autres caractères proposés
    engines_for: list[str] = Field(default_factory=list)  # moteurs (rang 0) d'accord
    engines_against: list[str] = Field(default_factory=list)  # moteurs (rang 0) en désaccord

    @property
    def unanimous(self) -> bool:
        """Au moins deux moteurs différents d'accord, aucun contre."""
        return len(self.engines_for) >= 2 and not self.engines_against


class VotedMrz(BaseModel):
    model_config = ConfigDict(frozen=True)

    lines: list[str]
    positions: list[list[PositionVote]]
    readings_used: int
    engines: list[str]

    def vote_at(self, line: int, index: int) -> PositionVote:
        return self.positions[line][index]


def _weight(r: BandReading, weights: dict[str, float]) -> float:
    return weights.get(r.engine, UNKNOWN_ENGINE_WEIGHT) * r.confidence * RANK_DISCOUNT**r.rank


def _fit(lines: list[str], n: int, length: int) -> list[str] | None:
    """Meilleure fenêtre de `n` lignes consécutives réalignables à `length` (la plus basse)."""
    for start in range(len(lines) - n, -1, -1):
        window = [realign(line, length) for line in lines[start : start + n]]
        if all(w is not None for w in window):
            return [w for w in window if w is not None]
    return None


def vote(readings: list[BandReading], weights: dict[str, float] | None = None) -> VotedMrz | None:
    """Vote par position sur les lectures compatibles avec la forme la plus soutenue."""
    w = DEFAULT_WEIGHTS if weights is None else weights
    candidates = [(r, mrz_candidate_lines(r.lines)) for r in readings]

    best: tuple[float, tuple[int, int]] | None = None
    fits: dict[tuple[int, int], list[tuple[BandReading, list[str]]]] = defaultdict(list)
    for shape in SHAPES:
        for r, lines in candidates:
            fitted = _fit(lines, *shape)
            if fitted is not None:
                fits[shape].append((r, fitted))
        score = sum(_weight(r, w) for r, _ in fits[shape])
        if score > 0 and (best is None or score > best[0]):
            best = (score, shape)
    if best is None:
        return None
    n, length = best[1]
    used = fits[best[1]]

    voted_lines: list[str] = []
    positions: list[list[PositionVote]] = []
    for i in range(n):
        row: list[PositionVote] = []
        for j in range(length):
            scores: dict[str, float] = defaultdict(float)
            for r, lines in used:
                scores[lines[i][j]] += _weight(r, w)
            total = sum(scores.values())
            char = min(scores, key=lambda c: (-scores[c], c))
            # Plusieurs prétraitements d'un même moteur : il n'est « pour » que si toutes
            # ses lectures de rang 0 donnent ce caractère.
            by_engine: dict[str, set[str]] = defaultdict(set)
            for r, lines in used:
                if r.rank == 0:
                    by_engine[r.engine].add(lines[i][j])
            row.append(
                PositionVote(
                    char=char,
                    share=scores[char] / total,
                    alternatives={c: s / total for c, s in scores.items() if c != char},
                    engines_for=sorted(e for e, chars in by_engine.items() if chars == {char}),
                    engines_against=sorted(e for e, chars in by_engine.items() if chars != {char}),
                )
            )
        positions.append(row)
        voted_lines.append("".join(p.char for p in row))
    return VotedMrz(
        lines=voted_lines,
        positions=positions,
        readings_used=len(used),
        engines=sorted({r.engine for r, _ in used}),
    )


def single_reading(lines: list[str], engine: str = "manual") -> VotedMrz | None:
    """Vote sur une seule lecture (tests, saisie manuelle)."""
    return vote([BandReading(engine=engine, lines=lines)])
