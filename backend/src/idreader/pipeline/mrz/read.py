"""E.2 — Lectures multi-moteurs × prétraitements de la bande MRZ.

Prétraitements (03_PIPELINE_ANALYSE.md § E.2) :
  a — niveaux de gris + CLAHE          b — (a) agrandi × 2 (cubique)
  c — binarisation d'Otsu              d — seuil adaptatif
  e — (a) + accentuation légère

Le plan de lecture choisit, par moteur, les prétraitements à lire (budget de 6 à 9
lectures au total). Le pipeline fonctionne avec n'importe quel sous-ensemble de moteurs :
les moteurs absents sont simplement ignorés.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

import cv2
import numpy as np

from idreader.engines.base import OcrEngine
from idreader.pipeline.ingest import RGBImage
from idreader.pipeline.mrz.locate import split_lines
from idreader.pipeline.mrz.vote import BandReading

log = logging.getLogger("idreader.pipeline.mrz")

Gray = RGBImage  # tableau 2D uint8

# Plan de départ, à recalibrer sur le banc golden (phase 1b).
DEFAULT_PLAN: dict[str, tuple[str, ...]] = {
    "vision": ("a", "b", "c"),
    "tesseract": ("a", "c", "d"),
    "ppocr": ("a", "b", "e"),
}
LINE_BY_LINE = frozenset({"ppocr"})  # reconnaissance seule : une ligne à la fois


def _clahe(gray: Gray) -> Gray:
    return np.asarray(cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray), np.uint8)


def _a(gray: Gray) -> Gray:
    return _clahe(gray)


def _b(gray: Gray) -> Gray:
    a = _clahe(gray)
    return np.asarray(cv2.resize(a, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC), np.uint8)


def _c(gray: Gray) -> Gray:
    _, out = cv2.threshold(
        cv2.GaussianBlur(gray, (3, 3), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    return np.asarray(out, np.uint8)


def _d(gray: Gray) -> Gray:
    block = max(15, (gray.shape[0] // 6) | 1)
    out = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, block, 10
    )
    return np.asarray(out, np.uint8)


def _e(gray: Gray) -> Gray:
    a = _clahe(gray)
    blur = cv2.GaussianBlur(a, (0, 0), 1.0)
    return np.asarray(cv2.addWeighted(a, 1.5, blur, -0.5, 0), np.uint8)


PREPROCESS: dict[str, Callable[[Gray], Gray]] = {"a": _a, "b": _b, "c": _c, "d": _d, "e": _e}


def to_gray(image: RGBImage) -> Gray:
    return (
        image if image.ndim == 2 else np.asarray(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), np.uint8)
    )


def read_band(
    band: RGBImage,
    engines: Sequence[OcrEngine],
    *,
    plan: dict[str, tuple[str, ...]] | None = None,
    expected_lines: int | None = None,
) -> tuple[list[BandReading], list[str]]:
    """Toutes les lectures de la bande. Renvoie (lectures, codes d'erreur des moteurs)."""
    plan = DEFAULT_PLAN if plan is None else plan
    gray = to_gray(band)
    variants: dict[str, Gray] = {}
    readings: list[BandReading] = []
    errors: list[str] = []
    for engine in engines:
        for key in plan.get(engine.name, ("a",)):
            if key not in variants:
                variants[key] = PREPROCESS[key](gray)
            image = variants[key]
            try:
                if engine.name in LINE_BY_LINE:
                    texts: list[str] = []
                    confs: list[float] = []
                    for y0, y1 in split_lines(image, expected=expected_lines):
                        for line in engine.recognize(np.ascontiguousarray(image[y0:y1])):
                            texts.append(line.text)
                            confs.append(line.confidence)
                else:
                    result = engine.recognize(image)
                    texts = [line.text for line in result]
                    confs = [line.confidence for line in result]
            except Exception as exc:  # un moteur défaillant ne bloque pas les autres
                code = getattr(exc, "code", type(exc).__name__)
                log.warning("mrz read failed engine=%s variant=%s code=%s", engine.name, key, code)
                errors.append(f"{engine.name}:{code}")
                continue
            if texts:
                readings.append(
                    BandReading(
                        engine=engine.name,
                        variant=key,
                        lines=texts,
                        confidence=float(np.mean(confs)) if confs else 1.0,
                    )
                )
    return readings, errors
