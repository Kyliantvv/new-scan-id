"""Moteurs disponibles sur cette machine. Le pipeline fonctionne avec n'importe quel
sous-ensemble (y compris aucun : l'étape E renvoie alors « aucun moteur »)."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from idreader.core.config import Settings
from idreader.engines.base import OcrEngine
from idreader.engines.ppocr import PpOcrRecEngine
from idreader.engines.tesseract import TesseractMrzEngine

log = logging.getLogger("idreader.engines")

ENGINE_NAMES = ("vision", "tesseract", "ppocr")


def available_engines(settings: Settings, only: Iterable[str] | None = None) -> list[OcrEngine]:
    wanted = set(ENGINE_NAMES if only is None else only)
    engines: list[OcrEngine] = []
    if "vision" in wanted and settings.idvision_path.is_file():
        from idreader.engines.apple_vision import AppleVisionEngine, VisionHelperClient

        engines.append(
            AppleVisionEngine(
                VisionHelperClient.from_settings(settings), tmp_dir=settings.data_dir / "tmp"
            )
        )
    if "tesseract" in wanted and (t := TesseractMrzEngine.from_settings(settings)) is not None:
        engines.append(t)
    if "ppocr" in wanted and (p := PpOcrRecEngine.from_settings(settings)) is not None:
        engines.append(p)
    missing = sorted(wanted - {e.name for e in engines})
    if missing:
        log.info("engines unavailable: %s", ",".join(missing))
    return engines
