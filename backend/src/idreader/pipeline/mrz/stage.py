"""Étape E de bout en bout sur un recadrage : localisation → lectures → vote → décodage."""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

from idreader.engines.base import OcrEngine
from idreader.pipeline.ingest import RGBImage
from idreader.pipeline.mrz.decode import MrzResult, decode
from idreader.pipeline.mrz.locate import Band, crop_band, deskew, locate_band
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
    ms: dict[str, float] = Field(default_factory=dict)


def run_mrz(
    crop: RGBImage,
    engines: Sequence[OcrEngine],
    *,
    today: date,
    plan: dict[str, tuple[str, ...]] | None = None,
) -> MrzStage:
    names = [e.name for e in engines]
    if not engines:
        return MrzStage(engines=names, band=None, readings=0, result=None, failure="NO_ENGINE")
    ms: dict[str, float] = {}
    t0 = time.perf_counter()
    band = locate_band(crop)
    ms["locate"] = (time.perf_counter() - t0) * 1000
    if band is None:
        return MrzStage(
            engines=names, band=None, readings=0, result=None, failure="MRZ_NOT_FOUND", ms=ms
        )
    t0 = time.perf_counter()
    readings, errors = read_band(deskew(crop_band(crop, band)), engines, plan=plan)
    ms["read"] = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter()
    voted = vote(readings)
    result = decode(voted, today=today) if voted is not None else None
    ms["decode"] = (time.perf_counter() - t0) * 1000
    return MrzStage(
        engines=names,
        band=band,
        readings=len(readings),
        engine_errors=errors,
        result=result,
        failure=None if result is not None else "MRZ_UNREADABLE",
        ms=ms,
    )
