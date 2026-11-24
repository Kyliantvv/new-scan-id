"""G — OCR de la zone visuelle et fusion des moteurs en « emplacements ».

03_PIPELINE_ANALYSE.md § G :
- langues déduites du pays émetteur (`reference/country_scripts.yaml`) + en, fr, es ;
- la bande MRZ est masquée pour ne pas être relue comme texte visuel ;
- les lignes des moteurs sont appariées par recouvrement (IoU ≥ 0,5) : un emplacement porte
  1 à 2 lectures ; une lecture identique par deux moteurs est une preuve forte.
"""

from __future__ import annotations

import logging
import time
import unicodedata
from collections.abc import Sequence

import cv2
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from idreader.engines.base import OcrEngine, Quad, TextLine
from idreader.pipeline.ingest import RGBImage
from idreader.pipeline.mrz.locate import Band
from idreader.pipeline.reference import country_languages

log = logging.getLogger("idreader.pipeline.viz")

IOU_MIN = 0.5
DEFAULT_LANGUAGES = ("en", "fr", "es")


class VizReading(BaseModel):
    model_config = ConfigDict(frozen=True)

    engine: str
    text: str
    confidence: float
    candidates: list[str] = Field(default_factory=list)


class Slot(BaseModel):
    """Emplacement de texte : un cadre et les lectures des moteurs qui le couvrent."""

    model_config = ConfigDict(frozen=True)

    id: int
    quad: Quad
    readings: list[VizReading]

    @property
    def best(self) -> VizReading:
        return max(self.readings, key=lambda r: r.confidence)

    @property
    def text(self) -> str:
        return self.best.text

    @property
    def agreed(self) -> bool:
        """Deux moteurs ou plus ont lu exactement le même texte (espaces normalisés)."""
        texts = {_canon(r.text) for r in self.readings}
        return (
            len(self.readings) >= 2
            and len({r.engine for r in self.readings}) >= 2
            and len(texts) == 1
        )

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return self.quad.bbox


class VizOcr(BaseModel):
    """Artefact JSON de l'étape G pour un recadrage."""

    model_config = ConfigDict(frozen=True)

    engines: list[str]
    languages: list[str]
    slots: list[Slot]
    engine_errors: list[str] = Field(default_factory=list)
    ms: float = 0.0


def _canon(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


def languages_for(state: str | None) -> list[str]:
    """Préfixes de langues pour Vision : langues du pays + en, fr, es (sans doublon)."""
    langs = list(country_languages(state)) if state else []
    for lang in DEFAULT_LANGUAGES:
        if lang not in langs:
            langs.append(lang)
    return langs


def bbox_iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    iw = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    ih = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def fuse(lines: Sequence[TextLine], *, iou_min: float = IOU_MIN) -> list[Slot]:
    """Regroupe les lignes de tous les moteurs en emplacements (au plus une lecture par moteur
    et par emplacement), dans l'ordre de lecture."""
    groups: list[tuple[Quad, list[VizReading]]] = []
    for line in lines:
        if not line.text.strip():
            continue
        reading = VizReading(
            engine=line.engine,
            text=_canon(line.text),
            confidence=line.confidence,
            candidates=[c.text for c in line.candidates if c.text != line.text],
        )
        best_i, best_iou = -1, iou_min
        for i, (quad, readings) in enumerate(groups):
            if any(r.engine == line.engine for r in readings):
                continue
            iou = bbox_iou(quad.bbox, line.quad.bbox)
            if iou >= best_iou:
                best_i, best_iou = i, iou
        if best_i >= 0:
            groups[best_i][1].append(reading)
        else:
            groups.append((line.quad, [reading]))

    def order(g: tuple[Quad, list[VizReading]]) -> tuple[float, float]:
        x, y, _, h = g[0].bbox
        return (round((y + h / 2) / max(h, 1.0)), x)

    groups.sort(key=order)
    return [Slot(id=i, quad=q, readings=r) for i, (q, r) in enumerate(groups)]


def mask_band(image: RGBImage, band: Band | None) -> RGBImage:
    """Remplit la bande MRZ par la couleur médiane du recadrage."""
    if band is None:
        return image
    out = image.copy()
    fill = np.median(image.reshape(-1, image.shape[-1]) if image.ndim == 3 else image, axis=0)
    out[band.y : band.y + band.height, band.x : band.x + band.width] = fill.astype(np.uint8)
    return out


def run_viz_ocr(
    crop: RGBImage,
    engines: Sequence[OcrEngine],
    *,
    mrz_band: Band | None = None,
    state: str | None = None,
) -> VizOcr:
    """Lit la zone visuelle avec tous les moteurs disponibles puis fusionne."""
    t0 = time.perf_counter()
    image = np.ascontiguousarray(mask_band(crop, mrz_band), dtype=np.uint8)
    if image.ndim == 2:
        image = np.ascontiguousarray(cv2.cvtColor(image, cv2.COLOR_GRAY2RGB), dtype=np.uint8)
    langs = languages_for(state)
    lines: list[TextLine] = []
    errors: list[str] = []
    for engine in engines:
        try:
            lines.extend(
                engine.recognize(image, languages=langs, level="accurate", top_candidates=3)
            )
        except Exception as exc:  # un moteur en panne n'empêche pas les autres
            code = getattr(exc, "code", type(exc).__name__)
            log.warning("viz read failed engine=%s code=%s", engine.name, code)
            errors.append(f"{engine.name}:{code}")
    return VizOcr(
        engines=[e.name for e in engines],
        languages=langs,
        slots=fuse(lines),
        engine_errors=errors,
        ms=(time.perf_counter() - t0) * 1000,
    )
