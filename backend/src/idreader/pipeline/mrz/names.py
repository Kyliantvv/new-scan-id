"""E.7 — Noms : découpage, troncature, translittération ICAO.

ICAO 9303 partie 3, §6 (translittération des caractères latins nationaux) : certains
caractères ont plusieurs transcriptions admises ; on les génère toutes.
"""

from __future__ import annotations

import itertools
import unicodedata

from anyascii import anyascii
from rapidfuzz import fuzz

from idreader.pipeline.mrz.checkdigit import FILLER

# ICAO 9303 partie 3, §6 : transcriptions multiples ou non décomposables par Unicode.
TRANSLIT: dict[str, tuple[str, ...]] = {
    "Ä": ("AE", "A"),
    "Ö": ("OE", "O"),
    "Ü": ("UE", "U"),
    "Å": ("AA", "A"),
    "Æ": ("AE",),
    "Ø": ("OE",),
    "Œ": ("OE",),
    "ß": ("SS",),
    "ẞ": ("SS",),
    "Þ": ("TH",),
    "Ð": ("D",),
    "Đ": ("D",),
    "Ł": ("L",),
    "Ħ": ("H",),
    "Ĳ": ("IJ",),
    "ı": ("I",),
}
MAX_VARIANTS = 64
NON_LATIN_MIN_SIMILARITY = 85.0  # rapidfuzz, 0–100 ; à calibrer sur le banc golden


def split_name(field: str) -> tuple[str, str, bool]:
    """Champ nom MRZ → (nom, prénoms, peut-être tronqué).

    Premier `<<` : séparation nom / prénoms ; `<` simple → espace ; remplissage final
    supprimé. Champ rempli jusqu'au dernier caractère : le nom est peut-être tronqué.
    """
    truncated = bool(field) and not field.endswith(FILLER)
    body = field.rstrip(FILLER)
    surname, _, given = body.partition(FILLER * 2)
    return _spaces(surname), _spaces(given), truncated


def _spaces(text: str) -> str:
    return " ".join(text.replace(FILLER, " ").split())


def _is_latin(text: str) -> bool:
    return all(not (ch.isalpha() and "LATIN" not in unicodedata.name(ch, "")) for ch in text)


def _char_options(ch: str) -> tuple[str, ...]:
    up = ch.upper() if ch not in ("ß", "ı") else ch
    if up in TRANSLIT:
        return TRANSLIT[up]
    if ch in TRANSLIT:
        return TRANSLIT[ch]
    if ch in "-‐‑–— ":
        return (" ",)
    base = "".join(c for c in unicodedata.normalize("NFKD", up) if not unicodedata.combining(c))
    return ("".join(c for c in base if "A" <= c <= "Z"),)


def mrz_forms(viz: str) -> set[str]:
    """Formes MRZ possibles d'un nom lu dans la zone visuelle (espaces simples)."""
    options = [_char_options(ch) for ch in viz]
    forms: set[str] = set()
    for combo in itertools.islice(itertools.product(*options), MAX_VARIANTS):
        forms.add(" ".join("".join(combo).split()))
    return forms


def names_match(mrz_value: str, viz_value: str, *, truncated: bool = False) -> bool:
    """La valeur MRZ (espaces simples) correspond-elle au nom de la zone visuelle ?

    Écriture latine : égalité avec une des formes ICAO, ou préfixe si la MRZ est tronquée.
    Autre écriture : similarité avec `anyascii(viz)`, plus tolérante.
    """
    mrz = " ".join(mrz_value.upper().split())
    if not mrz:
        return False
    if _is_latin(viz_value):
        for form in mrz_forms(viz_value):
            if form == mrz or (
                truncated and form.replace(" ", "").startswith(mrz.replace(" ", ""))
            ):
                return True
        return False
    latin = " ".join(anyascii(viz_value).upper().replace("-", " ").split())
    return fuzz.ratio(mrz, latin) >= NON_LATIN_MIN_SIMILARITY
