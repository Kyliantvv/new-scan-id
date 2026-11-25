"""Données de référence (`reference/*.yaml`), chargées une fois."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import iso3166
import yaml

REFERENCE_DIR = Path(__file__).resolve().parents[4] / "reference"


def _load(name: str) -> dict[str, object]:
    data = yaml.safe_load((REFERENCE_DIR / name).read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


@lru_cache(maxsize=1)
def mrz_state_codes() -> frozenset[str]:
    """Codes d'État ou de nationalité admis en MRZ, sans remplissage (`D` pour l'Allemagne).

    ISO 3166-1 alpha-3 + codes spéciaux ICAO 9303 partie 3 (`icao_special_codes.yaml`).
    """
    special = {str(k) for k in _load("icao_special_codes.yaml")}
    return frozenset(set(iso3166.countries_by_alpha3) | special)


@lru_cache(maxsize=1)
def doc_number_patterns() -> dict[tuple[str, str], re.Pattern[str]]:
    """(État, première lettre du code de document) → motif du numéro."""
    table: dict[tuple[str, str], re.Pattern[str]] = {}
    for state, by_code in _load("doc_number_patterns.yaml").items():
        if isinstance(by_code, dict):
            for code, pattern in by_code.items():
                table[(str(state), str(code))] = re.compile(str(pattern))
    return table


@lru_cache(maxsize=1)
def country_scripts() -> dict[str, dict[str, list[str]]]:
    """Pays (code MRZ) → {"vision": [préfixes de langues], "ppocr": [groupes de modèles]}."""
    out: dict[str, dict[str, list[str]]] = {}
    for state, entry in _load("country_scripts.yaml").items():
        if isinstance(entry, dict):
            out[str(state)] = {str(k): [str(x) for x in v] for k, v in entry.items()}
    return out


def country_languages(state: str) -> list[str]:
    return list(country_scripts().get(state, {}).get("vision", []))


@lru_cache(maxsize=1)
def month_names() -> dict[str, dict[int, list[str]]]:
    """Langue → {numéro du mois: [noms et abréviations]} (`reference/months.yaml`)."""
    out: dict[str, dict[int, list[str]]] = {}
    for lang, by_month in _load("months.yaml").items():
        if isinstance(by_month, dict):
            out[str(lang)] = {int(k): [str(x) for x in v] for k, v in by_month.items()}
    return out


@lru_cache(maxsize=1)
def viz_labels() -> dict[str, list[str]]:
    """Champ → libellés de la zone visuelle, toutes langues confondues (`viz_labels.yaml`)."""
    out: dict[str, list[str]] = {}
    for field, by_lang in _load("viz_labels.yaml").items():
        if isinstance(by_lang, dict):
            out[str(field)] = [str(x) for labels in by_lang.values() for x in labels]
    return out


@lru_cache(maxsize=1)
def doc_code_overrides() -> dict[tuple[str, str], str]:
    """(État, code de document à 2 caractères) → type (`reference/doc_codes.yaml`)."""
    table: dict[tuple[str, str], str] = {}
    for state, codes in _load("doc_codes.yaml").items():
        if isinstance(codes, dict):
            for code, doc_type in codes.items():
                table[(str(state), str(code))] = str(doc_type)
    return table


@lru_cache(maxsize=1)
def nationality_words() -> dict[str, str]:
    """Nationalité ou pays en toutes lettres (replié : minuscules, sans accents) → code ICAO.

    Adjectifs (`demonyms`) et noms de pays en français (`countries`) :
    `reference/nationality_words.yaml`.
    """
    data = _load("nationality_words.yaml")
    out: dict[str, str] = {}
    for section in ("countries", "demonyms"):  # les adjectifs l'emportent sur les noms de pays
        block = data.get(section)
        if isinstance(block, dict):
            out.update({str(k): str(v) for k, v in block.items()})
    return out


@lru_cache(maxsize=1)
def country_headers() -> dict[str, re.Pattern[str]]:
    """Code ICAO → motif de l'intitulé d'État imprimé en tête du document (texte replié)."""
    data = _load("country_headers.yaml")
    return {str(k): re.compile(rf"(?<![A-Z])(?:{v})(?![A-Z])") for k, v in data.items()}
