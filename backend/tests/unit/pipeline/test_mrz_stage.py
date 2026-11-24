"""E.1 localisation, E.2 prétraitements et lectures, étape E de bout en bout, jeu synthétique."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import cv2
import numpy as np
import pytest

from idreader.bench.synthetic import generate, load_cases, random_identity
from idreader.core.config import Settings
from idreader.engines.base import ImageInput, Quad, Roi, TextLine
from idreader.pipeline.mrz.formats import MrzFormat
from idreader.pipeline.mrz.locate import deskew, locate_band, skew_angle, split_lines
from idreader.pipeline.mrz.read import PREPROCESS, read_band
from idreader.pipeline.mrz.stage import run_mrz

FONT = Settings().models_dir / "fonts" / "ocrb10.otf"
needs_font = pytest.mark.skipif(not FONT.is_file(), reason="police OCR-B absente (fetch_models.py)")
TODAY = date(2026, 11, 25)
TD3 = [
    "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
    "L898902C36UTO7408122F1204159ZE184226B<<<<<10",
]


class ScriptedEngine:
    """Moteur factice : renvoie des lignes fixées, ou échoue."""

    def __init__(self, name: str, lines: list[str] | None) -> None:
        self.name = name
        self.lines = lines
        self.calls = 0

    def recognize(
        self,
        image: ImageInput,
        *,
        roi: Roi | None = None,
        languages: tuple[str, ...] = (),
        level: str = "accurate",
        top_candidates: int = 1,
    ) -> list[TextLine]:
        self.calls += 1
        if self.lines is None:
            raise RuntimeError("panne")
        quad = Quad(points=((0, 0), (1, 0), (1, 1), (0, 1)))
        return [TextLine(text=t, confidence=0.9, quad=quad, engine=self.name) for t in self.lines]


def card_with_mrz(seed: int = 1) -> tuple[np.ndarray, list[str]]:
    import random

    from idreader.bench.synthetic import render
    from idreader.pipeline.mrz.synth import build

    rng = random.Random(seed)  # noqa: S311
    lines = build(MrzFormat.TD1, random_identity(rng, MrzFormat.TD1))
    return render(lines, MrzFormat.TD1, FONT, rng), lines


@needs_font
def test_locate_band_finds_three_lines_at_bottom() -> None:
    img, _ = card_with_mrz()
    band = locate_band(img)
    assert band is not None and band.source == "morphology"
    assert band.y > img.shape[0] * 0.55
    assert band.width > img.shape[1] * 0.8
    crop = img[band.y : band.y + band.height, band.x : band.x + band.width]
    assert len(split_lines(PREPROCESS["a"](cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)))) == 3


def test_locate_band_on_blank_crop() -> None:
    assert locate_band(np.full((1080, 1712, 3), 230, np.uint8)) is None
    whole = locate_band(np.full((100, 900, 3), 230, np.uint8))
    assert whole is not None and whole.source == "whole_image"


@needs_font
def test_deskew_band() -> None:
    img, _ = card_with_mrz(2)
    band = locate_band(img)
    assert band is not None
    crop = img[band.y : band.y + band.height, band.x : band.x + band.width]
    h, w = crop.shape[:2]
    tilted = cv2.warpAffine(
        crop,
        cv2.getRotationMatrix2D((w / 2, h / 2), 2.0, 1.0),
        (w, h),
        borderMode=cv2.BORDER_REPLICATE,
    )
    assert abs(skew_angle(tilted)) > 1.0
    assert abs(skew_angle(deskew(tilted))) < 0.6


def test_preprocessings_keep_band_shape() -> None:
    gray = np.random.default_rng(0).integers(0, 255, (60, 400), dtype=np.uint8)
    for key, fn in PREPROCESS.items():
        out = fn(gray)
        assert out.dtype == np.uint8
        assert out.shape == ((120, 800) if key == "b" else (60, 400))


def test_read_band_survives_failing_engine() -> None:
    good = ScriptedEngine("tesseract", TD3)
    broken = ScriptedEngine("vision", None)
    readings, errors = read_band(np.full((100, 900, 3), 255, np.uint8), [broken, good])
    assert {r.engine for r in readings} == {"tesseract"}
    assert len(readings) == 3  # plan tesseract : a, c, d
    assert errors == ["vision:RuntimeError"] * 3


@pytest.mark.parametrize("subset", [["tesseract"], ["vision"], ["tesseract", "vision"]])
def test_stage_works_with_any_engine_subset(subset: list[str]) -> None:
    img = np.full((300, 1200, 3), 255, np.uint8)
    engines = [ScriptedEngine(name, TD3) for name in subset]
    stage = run_mrz(img, engines, today=TODAY)
    assert stage.engines == subset
    assert stage.result is not None and stage.result.lines == TD3
    assert stage.result.engines == sorted(subset)


def test_stage_without_engine() -> None:
    stage = run_mrz(np.zeros((10, 10, 3), np.uint8), [], today=TODAY)
    assert stage.failure == "NO_ENGINE" and stage.result is None


@needs_font
def test_generator_is_reproducible_and_valid(tmp_path: Path) -> None:
    a = generate(tmp_path / "a", count=6, seed=5, font_path=FONT, today=TODAY)
    b = generate(tmp_path / "b", count=6, seed=5, font_path=FONT, today=TODAY)
    assert [c.lines for c in a] == [c.lines for c in b]
    assert {c.quality for c in a} == {"good", "medium", "poor"}
    truth = json.loads((tmp_path / "a" / "truth.json").read_text(encoding="utf-8"))
    assert truth["today"] == "2026-11-25" and len(truth["cases"]) == 6
    assert load_cases(tmp_path / "a")[0].lines == a[0].lines
    from idreader.pipeline.mrz.decode import decode
    from idreader.pipeline.mrz.vote import single_reading

    for case in a:
        voted = single_reading(case.lines)
        assert voted is not None
        result = decode(voted, today=TODAY)
        assert result is not None and result.checksums_valid, case.case_id
        assert (tmp_path / "a" / case.image).is_file()
