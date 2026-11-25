"""Étape E de bout en bout sur un recadrage : localisation → lectures → vote → décodage."""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from idreader.engines.base import OcrEngine
from idreader.pipeline.ingest import RGBImage
from idreader.pipeline.mrz.decode import MrzResult, decode
from idreader.pipeline.mrz.locate import (
    Band,
    crop_band,
    deskew,
    locate_band,
    locate_band_from_lines,
    probe_lines,
)
from idreader.pipeline.mrz.read import read_band
from idreader.pipeline.mrz.vote import vote


class MrzStage(BaseModel):
    """Artefact JSON de l'étape E pour un recadrage."""

    model_config = ConfigDict(frozen=True)

    engines: list[str]
    band: Band | None
    readings: int
    engine_errors: list[str] = Field(default_factory=list)
    result: MrzResult | None
    failure: str | None = None  # NO_ENGINE | MRZ_NOT_FOUND | MRZ_UNREADABLE
    # Sonde du repli par les lignes OCR, par moteur (diagnostic, aucun texte).
    probe: dict[str, dict[str, object]] = Field(default_factory=dict)
    ms: dict[str, float] = Field(default_factory=dict)


def _read_and_decode(
    crop: RGBImage,
    band: Band,
    engines: Sequence[OcrEngine],
    today: date,
    plan: dict[str, tuple[str, ...]] | None,
    ms: dict[str, float],
    suffix: str = "",
) -> tuple[int, list[str], MrzResult | None]:
    t0 = time.perf_counter()
    readings, errors = read_band(deskew(crop_band(crop, band)), engines, plan=plan)
    ms["read" + suffix] = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    voted = vote(readings)
    result = decode(voted, today=today) if voted is not None else None
    ms["decode" + suffix] = (time.perf_counter() - t0) * 1000
    return len(readings), errors, result


def _bands_differ(a: Band, b: Band) -> bool:
    """Vrai si les deux bandes se recouvrent mal (IoU < 0,5) : la relire vaut la peine."""
    ix = min(a.x + a.width, b.x + b.width) - max(a.x, b.x)
    iy = min(a.y + a.height, b.y + b.height) - max(a.y, b.y)
    inter = max(0, ix) * max(0, iy)
    union = a.width * a.height + b.width * b.height - inter
    return union <= 0 or inter / union < 0.5


def _locate_by_ocr(
    crop: RGBImage,
    line_engines: Sequence[OcrEngine],
    ms: dict[str, float],
    probe: dict[str, dict[str, object]],
) -> tuple[Band | None, list[str]]:
    """Repli E.1 : lignes MRZ retrouvées par un moteur de détection de texte (Vision, PP-OCR)."""
    t0 = time.perf_counter()
    errors: list[str] = []
    h, w = crop.shape[:2]
    for engine in line_engines:
        try:
            lines = engine.recognize(crop)
        except Exception as exc:  # un moteur défaillant ne bloque pas les autres
            errors.append(f"{engine.name}:{getattr(exc, 'code', type(exc).__name__)}")
            continue
        probe[engine.name] = probe_lines(lines, w, h)
        band = locate_band_from_lines(lines, w, h)
        if band is not None:
            ms["locate_ocr"] = (time.perf_counter() - t0) * 1000
            return band, errors
    ms["locate_ocr"] = (time.perf_counter() - t0) * 1000
    return None, errors


def run_mrz(
    crop: RGBImage,
    engines: Sequence[OcrEngine],
    *,
    today: date,
    plan: dict[str, tuple[str, ...]] | None = None,
    line_engines: Sequence[OcrEngine] = (),
) -> MrzStage:
    """`line_engines` : moteurs de détection de texte pour le repli par les lignes OCR, utilisé
    quand la morphologie ne trouve rien ou que la bande trouvée ne se décode pas."""
    names = [e.name for e in engines]
    if not engines:
        return MrzStage(engines=names, band=None, readings=0, result=None, failure="NO_ENGINE")
    ms: dict[str, float] = {}
    t0 = time.perf_counter()
    band = locate_band(crop)
    ms["locate"] = (time.perf_counter() - t0) * 1000
    readings = 0
    errors: list[str] = []
    result: MrzResult | None = None
    probe: dict[str, dict[str, object]] = {}
    if band is not None:
        readings, errors, result = _read_and_decode(crop, band, engines, today, plan, ms)
    if result is None and line_engines:
        alt, loc_errors = _locate_by_ocr(crop, line_engines, ms, probe)
        errors += loc_errors
        if alt is not None and (band is None or _bands_differ(band, alt)):
            n, errs, alt_result = _read_and_decode(crop, alt, engines, today, plan, ms, "_ocr")
            readings, errors = readings + n, errors + errs
            if alt_result is not None or band is None:
                band, result = alt, alt_result
    if band is None:
        return MrzStage(
            engines=names, band=None, readings=0, result=None, failure="MRZ_NOT_FOUND",
            engine_errors=errors, ms=ms, probe=probe,
        )  # fmt: skip
    return MrzStage(
        engines=names,
        band=band,
        readings=readings,
        engine_errors=errors,
        result=result,
        failure=None if result is not None else "MRZ_UNREADABLE",
        ms=ms,
        probe=probe,
    )
