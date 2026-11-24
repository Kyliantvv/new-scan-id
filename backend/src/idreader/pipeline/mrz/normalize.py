"""E.2 (fin) — Normalisation des lectures OCR d'une ligne de MRZ.

Fonctions pures : une chaîne lue par un moteur → une chaîne de l'alphabet MRZ.
"""

from __future__ import annotations

import re
import unicodedata

from idreader.pipeline.mrz.checkdigit import FILLER, MRZ_ALPHABET

# Variantes de chevron lues à la place de `<` (03_PIPELINE_ANALYSE.md § E.2).
CHEVRONS = frozenset("«‹≤〈⟨〈くᐸ˂＜‹⋖")
# Caractères hors alphabet ramenés à leur forme visuelle la plus proche ; les autres
# sont supprimés.
VISUAL = {"Ø": "0", "Ɵ": "0", "Θ": "0", "|": "I", "!": "I", "$": "S", "§": "S", "€": "E"}

MIN_MRZ_LINE = 25  # E.1 : au moins 25 caractères une fois les espaces retirés
MAX_REALIGN = 2  # E.2 : longueur à ± 2 caractères de la longueur attendue
_FILLER_K = re.compile(r"(?<=<<<)K(?=<|$)")


def normalize_text(raw: str) -> str:
    """Majuscules, sans espaces, chevrons → `<`, accents retirés, hors alphabet supprimé."""
    out: list[str] = []
    for ch in raw:
        if ch.isspace():
            continue
        if ch in CHEVRONS:
            out.append(FILLER)
            continue
        if ch in VISUAL:
            out.append(VISUAL[ch])
            continue
        # NFKD : pleine chasse → ASCII, lettre accentuée → lettre + diacritique (retiré)
        for base in unicodedata.normalize("NFKD", ch):
            if unicodedata.combining(base):
                continue
            up = base.upper()
            if up in MRZ_ALPHABET:
                out.append(up)
            elif up in VISUAL:
                out.append(VISUAL[up])
    return "".join(out)


def fix_filler_k(line: str) -> str:
    """`K` isolé dans une zone de remplissage → `<`.

    Zone de remplissage = au moins trois `<` juste avant le `K` (deux `<` au plus séparent
    les parties d'un nom), et `<` ou fin de ligne juste après. Un prénom d'une lettre
    `K` placé après `<<` n'est donc jamais touché.
    """
    previous = None
    while previous != line:
        previous = line
        line = _FILLER_K.sub(FILLER, line)
    return line


def _filler_runs(line: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in re.finditer(r"<{2,}", line)]


def realign(line: str, expected: int) -> str | None:
    """Ramène la ligne à la longueur attendue en ajoutant ou retirant des `<`.

    Seulement dans les séquences de remplissage (au moins deux `<`), dans la plus longue ;
    à égalité, la dernière (le remplissage final est le plus fréquent). Écart > 2 : None.
    """
    diff = len(line) - expected
    if diff == 0:
        return line
    if abs(diff) > MAX_REALIGN:
        return None
    for _ in range(abs(diff)):
        runs = _filler_runs(line)
        if not runs:
            return None
        _start, end = max(runs, key=lambda r: (r[1] - r[0], r[0]))
        line = line[:end] + FILLER + line[end:] if diff < 0 else line[: end - 1] + line[end:]
    return line


def mrz_candidate_lines(lines: list[str]) -> list[str]:
    """Lignes normalisées qui peuvent appartenir à une MRZ (au moins 25 caractères)."""
    out = []
    for raw in lines:
        line = fix_filler_k(normalize_text(raw))
        if len(line) >= MIN_MRZ_LINE:
            out.append(line)
    return out
