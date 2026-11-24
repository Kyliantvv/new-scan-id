"""H.3 — Analyse de dates de la zone visuelle, multilingue.

Formats (03_PIPELINE_ANALYSE.md § H.3) : `JJ MMM AAAA` y compris bilingue (`12 AVR/APR 1990`),
`JJ.MM.AAAA`, `JJ/MM/AAAA`, `JJ-MM-AAAA`, `JJ MM AAAA`, `AAAA.MM.JJ`, `AAAA年MM月DD日`.
Mois multilingues : `reference/months.yaml`. Une date à deux chiffres d'année est gardée avec
`year_digits = 2` : elle ne tranche jamais le siècle.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from functools import lru_cache

from pydantic import BaseModel, ConfigDict

from idreader.pipeline.reference import month_names


class ParsedDate(BaseModel):
    model_config = ConfigDict(frozen=True)

    value: date
    raw: str
    year_digits: int  # 4, ou 2 si le siècle n'est pas imprimé


def fold(text: str) -> str:
    """Majuscules sans accents (comparaison des noms de mois)."""
    decomposed = unicodedata.normalize("NFKD", text.upper())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


@lru_cache(maxsize=1)
def month_table() -> dict[str, int]:
    """Nom ou abréviation de mois (repliés) → numéro. Les formes ambiguës sont écartées."""
    table: dict[str, int] = {}
    ambiguous: set[str] = set()
    for by_month in month_names().values():
        for number, names in by_month.items():
            for name in names:
                key = fold(name)
                if key in table and table[key] != number:
                    ambiguous.add(key)
                table[key] = number
    for key in ambiguous:
        del table[key]
    return table


_SEP = r"[\s.\-/]"
_MONTH = r"[A-Za-zÀ-ÿĀ-žА-я]{3,10}\.?"
_PATTERNS = [
    # 12 AVR/APR 1990 ; 12 AVR /APR 1990 ; 12 JAN 1990 ; 12 avril 1990
    re.compile(
        rf"(?<!\d)(?P<d>\d{{1,2}})\s*(?P<m>{_MONTH})(?:\s*/\s*(?P<m2>{_MONTH}))?\s*(?P<y>\d{{4}}|\d{{2}})(?!\d)"
    ),
    # 1990年4月12日
    re.compile(r"(?P<y>\d{4})\s*年\s*(?P<mn>\d{1,2})\s*月\s*(?P<d>\d{1,2})\s*日"),
    # 1990.04.12 ; 1990-04-12 ; 1990/04/12
    re.compile(r"(?<!\d)(?P<y>\d{4})[.\-/](?P<mn>\d{1,2})[.\-/](?P<d>\d{1,2})(?!\d)"),
    # 12.04.1990 ; 12/04/1990 ; 12-04-1990 ; 12 04 1990 (et années sur 2 chiffres)
    re.compile(
        rf"(?<!\d)(?P<d>\d{{1,2}}){_SEP}\s?(?P<mn>\d{{1,2}}){_SEP}\s?(?P<y>\d{{4}}|\d{{2}})(?!\d)"
    ),
]


def _month(token: str | None) -> int | None:
    if not token:
        return None
    return month_table().get(fold(token.rstrip(".")))


def _build(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_dates(text: str, *, pivot_year: int | None = None) -> list[ParsedDate]:
    """Toutes les dates trouvées dans `text`, dans l'ordre d'apparition, sans doublon.

    Les années à deux chiffres sont rattachées au siècle courant si cela ne dépasse pas
    `pivot_year` (par défaut l'année en cours + 20), sinon au siècle précédent ; elles
    restent marquées `year_digits = 2`.
    """
    pivot = pivot_year if pivot_year is not None else date.today().year + 20
    found: list[tuple[int, ParsedDate]] = []
    taken: list[tuple[int, int]] = []
    for pattern in _PATTERNS:
        for m in pattern.finditer(text):
            span = m.span()
            if any(s < span[1] and span[0] < e for s, e in taken):
                continue
            groups = m.groupdict()
            if "m" in groups and groups.get("m") is not None:
                month = _month(groups["m"])
                second = _month(groups.get("m2"))
                if month is None or (second is not None and second != month):
                    continue
            else:
                month = int(groups["mn"])
            raw_year = groups["y"]
            year = int(raw_year)
            digits = len(raw_year)
            if digits == 2:
                year += 2000 if 2000 + year <= pivot else 1900
            value = _build(year, month, int(groups["d"]))
            if value is None:
                continue
            taken.append(span)
            found.append((span[0], ParsedDate(value=value, raw=m.group(0), year_digits=digits)))
    found.sort(key=lambda x: x[0])
    out: list[ParsedDate] = []
    for _, parsed in found:
        if parsed not in out:
            out.append(parsed)
    return out


def matches_mrz(parsed: ParsedDate, yy: int, mm: int, dd: int) -> bool:
    """La date lue correspond-elle à la date MRZ `AAMMJJ` (siècle non comparé) ?"""
    v = parsed.value
    return v.year % 100 == yy and v.month == mm and v.day == dd
