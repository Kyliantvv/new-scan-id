"""E.5 — Sommes de contrôle MRZ et classes indiscernables.

ICAO 9303 partie 3, §4.9 : valeurs `0–9` → 0–9, `A–Z` → 10–35, `<` → 0 ; poids 7, 3, 1
répétés ; somme modulo 10.
"""

from __future__ import annotations

MRZ_ALPHABET = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<")
FILLER = "<"
WEIGHTS = (7, 3, 1)  # ICAO 9303 partie 3, §4.9

# Confusions OCR que la somme de contrôle ne voit pas (03_PIPELINE_ANALYSE.md § E.5) :
# les deux caractères ont la même valeur modulo 10.
BLIND_SPOT_PAIRS: frozenset[frozenset[str]] = frozenset(
    frozenset(p) for p in ("S8", "G6", "B1", "Z5", "U0", "K<")
)


def char_value(c: str) -> int:
    """Valeur d'un caractère MRZ (ICAO 9303 partie 3, §4.9)."""
    if len(c) != 1 or c not in MRZ_ALPHABET:
        raise ValueError("caractère hors alphabet MRZ")
    if c == FILLER:
        return 0
    if c.isdigit():
        return ord(c) - ord("0")
    return ord(c) - ord("A") + 10


def value_class(c: str) -> int:
    """Classe indiscernable : deux caractères de même classe donnent la même somme."""
    return char_value(c) % 10


def compute_check_digit(data: str) -> int:
    return sum(char_value(c) * WEIGHTS[i % 3] for i, c in enumerate(data)) % 10


def check_digit_ok(data: str, digit: str, *, filler_allowed: bool = False) -> bool:
    """Vrai si `digit` est le chiffre de contrôle de `data`.

    `filler_allowed` : pour les données facultatives du TD3, le chiffre peut être `<`
    quand le champ est entièrement vide (ICAO 9303 partie 4, §4.2.2.2, note k).
    """
    if any(c not in MRZ_ALPHABET for c in data):
        return False
    if digit == FILLER:
        return filler_allowed and set(data) <= {FILLER}
    if not digit.isdigit() or len(digit) != 1:
        return False
    return compute_check_digit(data) == int(digit)


def same_class(a: str, b: str) -> bool:
    return value_class(a) == value_class(b)


def is_blind_spot(a: str, b: str) -> bool:
    """Confusion OCR courante invisible pour la somme de contrôle."""
    return frozenset((a, b)) in BLIND_SPOT_PAIRS


def blind_spot_partners(c: str) -> frozenset[str]:
    """Caractères avec lesquels `c` forme une confusion invisible."""
    return frozenset(x for pair in BLIND_SPOT_PAIRS if c in pair for x in pair if x != c)


def classes() -> dict[int, str]:
    """Table des classes indiscernables (pour la doc et les tests)."""
    table: dict[int, str] = {}
    for c in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ<":
        table.setdefault(value_class(c), "")
        table[value_class(c)] += c
    return table
