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
