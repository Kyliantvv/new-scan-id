"""Enchaînement des étapes pour un fichier : A → B → C → D → E → F → G → H → L.

L'appariement recto/verso (J) se fait ensuite au niveau du lot (`pipeline/pairing.py`).
Sans Vision (phase 1a/2 sous Windows) :
- D (orientation) : la MRZ est cherchée à 0°, puis 180°, puis 90°/270° pour un recadrage en
  portrait ; sans MRZ, un recadrage en portrait est tourné dans le sens où l'OCR de la zone
  visuelle lit le plus de texte (§ D.1 simplifié) ;
- C : détection OpenCV seule.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np

from idreader.engines.base import OcrEngine
from idreader.pipeline import detect as detect_mod
from idreader.pipeline.classify import Classification, classify_mrz, classify_text
from idreader.pipeline.consolidate import DocumentResult, consolidate, failed
from idreader.pipeline.extract import Extraction, extract
from idreader.pipeline.ingest import PageInfo, RGBImage, load_pages
from idreader.pipeline.mrz.locate import crop_band
from idreader.pipeline.mrz.stage import MrzStage, run_mrz
from idreader.pipeline.pairing import combine_viz
from idreader.pipeline.quality import CARD_WIDTH_MM, PASSPORT_WIDTH_MM, Quality, measure, mrz_glare
from idreader.pipeline.viz_ocr import VizOcr, run_viz_ocr

log = logging.getLogger("idreader.pipeline")

Progress = Callable[[float, str], None]


@dataclass
class Engines:
    mrz: list[OcrEngine]
    viz: list[OcrEngine]

    @property
    def names(self) -> list[str]:
        return sorted({e.name for e in [*self.mrz, *self.viz]})


@dataclass
class CropAnalysis:
    page_index: int
    index: int  # rang du recadrage sur la page (ordre de lecture)
    image: RGBImage  # recadrage redressé et orienté
    quad: list[list[float]]  # dans la page
    rotation: int
    physical_format: str
    side: str  # front | back | unknown
    detection_source: str
    quality: Quality
    mrz: MrzStage
    viz: VizOcr
    extraction: Extraction
    classification: Classification
    result: DocumentResult
    ms: dict[str, float] = field(default_factory=dict)
    mrz_glare: bool = False

    @property
    def has_mrz(self) -> bool:
        return self.mrz.result is not None

    def evidence(self) -> CropEvidence:
        return CropEvidence(
            mrz=self.mrz,
            viz=self.viz,
            classification=self.classification,
            quality_alerts=list(self.quality.alerts),
            mrz_glare=self.mrz_glare,
        )


@dataclass
class CropEvidence:
    """Ce dont l'appariement a besoin d'une face (relu depuis les artefacts JSON)."""

    mrz: MrzStage
    viz: VizOcr
    classification: Classification
    quality_alerts: list[str]
    mrz_glare: bool = False


@dataclass
class PairAnalysis:
    result: DocumentResult
    viz: VizOcr  # zone visuelle fusionnée (cadres du verso décalés, voir pairing.py)
    extraction: Extraction
    classification: Classification
    mrz: MrzStage
    mrz_side: str  # front | back


def consolidate_pair(
    front: CropEvidence,
    back: CropEvidence,
    *,
    today: date,
    threshold: float,
    allow_specimen: bool = False,
) -> PairAnalysis:
    """Recto + verso : la MRZ d'une face confirme la zone visuelle des deux (03 § J, H, L)."""
    mrz_side = "back" if back.mrz.result is not None or front.mrz.result is None else "front"
    side = back if mrz_side == "back" else front
    viz = combine_viz(front.viz, back.viz)
    extraction = extract(viz, side.mrz.result, today=today)
    classification = side.classification if side.mrz.result is not None else front.classification
    result = consolidate(
        mrz=side.mrz.result,
        extraction=extraction,
        classification=classification,
        today=today,
        threshold=threshold,
        quality_alerts=sorted({*front.quality_alerts, *back.quality_alerts}),
        mrz_glare=side.mrz_glare,
        mrz_failure=side.mrz.failure,
        allow_specimen=allow_specimen,
    )
    return PairAnalysis(result, viz, extraction, classification, side.mrz, mrz_side)


@dataclass
class FileAnalysis:
    pages: list[PageInfo]
    page_images: list[RGBImage]
    crops: list[CropAnalysis]
    failure: str | None = None


def _rotate(image: RGBImage, degrees: int) -> RGBImage:
    k = {0: 0, 90: 3, 180: 2, 270: 1}[degrees]  # sens horaire
    return np.ascontiguousarray(np.rot90(image, k)) if k else image


def _orient_by_mrz(
    crop: RGBImage,
    engines: Sequence[OcrEngine],
    today: date,
    line_engines: Sequence[OcrEngine] = (),
) -> tuple[int, RGBImage, MrzStage]:
    h, w = crop.shape[:2]
    order = [0, 180] if w >= h else [90, 270, 0, 180]
    first = (order[0], _rotate(crop, order[0]))
    stage = run_mrz(first[1], engines, today=today, line_engines=line_engines)
    result = (first[0], first[1], stage)
    if stage.result is not None or stage.failure == "NO_ENGINE":
        return result
    for degrees in order[1:]:
        rotated = _rotate(crop, degrees)
        candidate = run_mrz(rotated, engines, today=today, line_engines=line_engines)
        if candidate.result is not None:
            return degrees, rotated, candidate
    return result


def _text_score(viz: VizOcr) -> float:
    return sum(r.confidence * len(r.text) for s in viz.slots for r in s.readings)


def analyze_crop(
    crop: detect_mod.Crop,
    page_index: int,
    index: int,
    engines: Engines,
    *,
    today: date,
    threshold: float,
    allow_specimen: bool = False,
) -> CropAnalysis:
    ms: dict[str, float] = {}
    t0 = time.perf_counter()
    rotation, image, stage = _orient_by_mrz(crop.image, engines.mrz, today, engines.viz)
    ms["mrz"] = (time.perf_counter() - t0) * 1000
    mrz = stage.result
    classification = classify_mrz(mrz) if mrz is not None else None
    state = classification.issuing_state if classification else None

    t0 = time.perf_counter()
    viz = run_viz_ocr(image, engines.viz, mrz_band=stage.band if mrz else None, state=state)
    h, w = crop.image.shape[:2]
    if mrz is None and h > w and engines.viz:
        # D.1 simplifié : portrait sans MRZ → sens où la zone visuelle se lit le mieux
        best = (_text_score(viz), 0, image, viz)
        for degrees in (90, 270):
            rotated = _rotate(crop.image, degrees)
            candidate = run_viz_ocr(rotated, engines.viz, state=state)
            if _text_score(candidate) > best[0]:
                best = (_text_score(candidate), degrees, rotated, candidate)
        _, rotation, image, viz = best
    ms["viz"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    extraction = extract(viz, mrz, today=today)
    if classification is None:
        physical = {"id1": "ID1"}.get(crop.quad.size_class.value, "unknown")
        classification = classify_text([s.text for s in viz.slots], physical_format=physical)
    physical = classification.physical_format
    width_mm = {"ID1": CARD_WIDTH_MM, "ID2": 105.0, "ID3": PASSPORT_WIDTH_MM}.get(physical)
    quality = measure(image, physical_width_mm=width_mm)
    glare = bool(
        stage.band is not None and mrz is not None and mrz_glare(crop_band(image, stage.band))
    )
    if not engines.mrz and not engines.viz:
        result = failed("NO_ENGINE")
    elif mrz is None and not viz.slots:
        result = failed(_failure_from_quality(quality))
    else:
        # Le siècle confirmé par la zone visuelle (année sur 4 chiffres) est appliqué
        # par l'étape L (E.6.5).
        result = consolidate(
            mrz=mrz,
            extraction=extraction,
            classification=classification,
            today=today,
            threshold=threshold,
            quality_alerts=quality.alerts,
            mrz_glare=glare,
            mrz_failure=stage.failure,
            allow_specimen=allow_specimen,
        )
    ms["extract"] = (time.perf_counter() - t0) * 1000
    if mrz is not None:
        side = "back" if mrz.format.value == "TD1" else "front"
    else:
        side = "front" if extraction.fields else "unknown"
    return CropAnalysis(
        page_index=page_index,
        index=index,
        image=image,
        quad=[[float(x), float(y)] for x, y in crop.quad.points],
        rotation=rotation,
        physical_format=physical,
        side=side,
        detection_source=crop.quad.source,
        quality=quality,
        mrz=stage,
        viz=viz,
        extraction=extraction,
        classification=classification,
        result=result,
        ms=ms,
        mrz_glare=glare,
    )


def _failure_from_quality(quality: Quality) -> str:
    if "QUALITY_BLUR" in quality.alerts:
        return "TOO_BLURRY"
    if "QUALITY_LOW_RESOLUTION" in quality.alerts:
        return "LOW_RESOLUTION"
    return "NOTHING_READABLE"


def analyze_pages(
    images: Sequence[RGBImage],
    engines: Engines,
    *,
    today: date,
    threshold: float,
    allow_specimen: bool = False,
    progress: Progress | None = None,
) -> list[CropAnalysis]:
    out: list[CropAnalysis] = []
    for p, image in enumerate(images):
        page_crops = detect_mod.crops(image)
        for i, crop in enumerate(page_crops):
            if progress:
                progress((p + i / max(len(page_crops), 1)) / max(len(images), 1), "analyze")
            out.append(
                analyze_crop(
                    crop,
                    p,
                    i,
                    engines,
                    today=today,
                    threshold=threshold,
                    allow_specimen=allow_specimen,
                )
            )
    return out


def analyze_file(
    path: Path,
    engines: Engines,
    *,
    today: date,
    threshold: float,
    allow_specimen: bool = False,
    progress: Progress | None = None,
) -> FileAnalysis:
    pages = load_pages(path)
    images = [p.image for p in pages]
    crops = analyze_pages(
        images, engines, today=today, threshold=threshold, allow_specimen=allow_specimen,
        progress=progress,
    )  # fmt: skip
    return FileAnalysis(pages=[p.info for p in pages], page_images=images, crops=crops)
