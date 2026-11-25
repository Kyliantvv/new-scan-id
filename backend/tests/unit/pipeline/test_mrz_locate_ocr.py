"""E.1 — repli par les lignes OCR : la MRZ est retrouvée même si la morphologie échoue.

Aucune donnée réelle : lignes MRZ de spécimen ICAO (UTO) et documents synthétiques.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from idreader.engines.base import Quad, TextLine
from idreader.pipeline.mrz.locate import (
    locate_band_from_lines,
    looks_like_mrz_line,
    normalize_mrz_text,
    probe_lines,
)
from idreader.pipeline.mrz.stage import run_mrz
from tests.unit.pipeline.test_mrz_stage import TD3, ScriptedEngine

TODAY = date(2026, 11, 25)
TD1 = [
    "I<UTOD231458907<<<<<<<<<<<<<<<",
    "7408122F1204159UTO<<<<<<<<<<<6",
    "ERIKSSON<<ANNA<MARIA<<<<<<<<<<",
]


def line(
    text: str, x: float, y: float, w: float, h: float = 30, engine: str = "vision"
) -> TextLine:
    quad = Quad(points=((x, y), (x + w, y), (x + w, y + h), (x, y + h)))
    return TextLine(text=text, confidence=0.9, quad=quad, engine=engine)


class LineEngine:
    """Moteur de détection factice : renvoie des lignes fixées, ou échoue."""

    def __init__(self, lines: list[TextLine] | None, name: str = "vision") -> None:
        self.name, self.lines, self.calls = name, lines, 0

    def recognize(self, image, **_kw):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.lines is None:
            raise RuntimeError("panne")
        return self.lines


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (TD3[0], True),
        (TD3[1], True),
        ("P<UTOERIKSSON««ANNA«MARIA«««««««««««««««««««", True),  # chevrons lus « par l'OCR
        ("P < UTO ERIKSSON << ANNA MARIA <<<<<<<<<<<<<<<<<<<", True),  # espaces intercalés
        ("Nom / Surname DUPONT", False),
        ("RÉPUBLIQUE FRANÇAISE PASSEPORT", False),
        ("P<FRA", False),  # trop court
        ("L898902C36UTO7408122F1204159ZE184226B<<<<<10", True),
    ],
)
def test_looks_like_mrz_line(text: str, expected: bool) -> None:
    assert looks_like_mrz_line(text) is expected


def test_normalize_mrz_text() -> None:
    assert normalize_mrz_text("p « fra ‹") == "P<FRA<"


def test_locates_stack_at_bottom_among_noise() -> None:
    lines = [
        line("RÉPUBLIQUE FRANÇAISE", 100, 40, 500),
        line("Nom / Surname", 100, 300, 200),
        line(TD3[0], 80, 1000, 1000),
        line(TD3[1], 80, 1045, 1000),
    ]
    band = locate_band_from_lines(lines, 1200, 1200)
    assert band is not None and band.source == "ocr_lines"
    assert band.y <= 1000 and band.y + band.height >= 1075
    assert band.x <= 80 and band.x + band.width >= 1080


def test_locates_three_line_card() -> None:
    lines = [line(t, 60, 700 + 50 * i, 700) for i, t in enumerate(TD1)]
    band = locate_band_from_lines(lines, 900, 900)
    assert band is not None and band.height >= 3 * 30


def test_single_line_at_top_is_not_a_band() -> None:
    assert locate_band_from_lines([line(TD3[0], 80, 100, 1000)], 1200, 1200) is None


def test_single_full_line_at_bottom_gives_a_grown_band() -> None:
    """Une seule ligne lue (reflet sur l'autre) : la bande est élargie pour retrouver l'autre."""
    band = locate_band_from_lines([line(TD3[1], 80, 1000, 1000)], 1200, 1200)
    assert band is not None and band.source == "ocr_lines"
    assert band.y < 1000 - 30 and band.y + band.height > 1030 + 30


def test_misaligned_lines_are_not_stacked_but_lowest_line_is_used() -> None:
    shifted = [line(TD3[0], 80, 1000, 1000), line(TD3[1], 500, 1045, 600)]
    band = locate_band_from_lines(shifted, 1200, 1200)
    assert band is not None and band.height < 200  # une ligne élargie, pas une pile


def test_truncated_line_next_to_a_full_line_is_stacked() -> None:
    truncated = TD3[1][-24:]  # 24 caractères avec chevrons : ligne coupée par un reflet
    lines = [line(TD3[0], 80, 1000, 1000), line(truncated, 80, 1045, 520)]
    band = locate_band_from_lines(lines, 1200, 1200)
    assert band is not None and band.y <= 1000 and band.y + band.height >= 1075


def test_merged_block_of_two_lines_is_one_band() -> None:
    merged = TD3[0] + TD3[1]  # l'OCR rend les deux lignes d'un seul tenant
    band = locate_band_from_lines([line(merged, 80, 1000, 1000, h=80)], 1200, 1200)
    assert band is not None and band.height >= 80


def test_filler_lookalikes_count_as_chevrons() -> None:
    assert looks_like_mrz_line("PKFRADURANDALKKKKJEANKKKKKKKKKKKKKKKKKKKKKK")
    assert not looks_like_mrz_line("REPUBLIQUE FRANCAISE PASSEPORT PASSPORT")


def test_probe_counts_lines_without_text() -> None:
    lines = [
        line(TD3[0], 80, 1000, 1000),
        line(TD3[1][:20], 80, 1045, 400),
        line("Nom / Surname", 100, 300, 200),
    ]
    probe = probe_lines(lines, 1200, 1200)
    assert probe["lines"] == 3
    assert str(probe["mrz_like"]).count("[") == 2  # une liste de listes à un élément
    assert str(probe["near"]).count("[") == 2
    assert "ERIKSSON" not in str(probe)


def test_lowest_stack_wins() -> None:
    lines = [
        line(TD3[0], 80, 300, 1000),
        line(TD3[1], 80, 345, 1000),
        line(TD3[0], 80, 1000, 1000),
        line(TD3[1], 80, 1045, 1000),
    ]
    band = locate_band_from_lines(lines, 1200, 1200)
    assert band is not None and band.y > 900


def test_stage_falls_back_to_ocr_lines_when_morphology_finds_nothing() -> None:
    blank = np.full((1200, 1200, 3), 255, np.uint8)  # rien pour la morphologie
    finder = LineEngine([line(TD3[0], 80, 1000, 1000), line(TD3[1], 80, 1045, 1000)])
    reader = ScriptedEngine("tesseract", TD3)
    stage = run_mrz(blank, [reader], today=TODAY, line_engines=[finder])
    assert stage.result is not None and stage.result.lines == TD3
    assert stage.band is not None and stage.band.source == "ocr_lines"
    assert "locate_ocr" in stage.ms


def test_stage_without_line_engines_still_reports_not_found() -> None:
    blank = np.full((1200, 1200, 3), 255, np.uint8)
    stage = run_mrz(blank, [ScriptedEngine("tesseract", TD3)], today=TODAY)
    assert stage.failure == "MRZ_NOT_FOUND" and stage.band is None


def test_stage_survives_failing_line_engine_and_uses_the_next() -> None:
    blank = np.full((1200, 1200, 3), 255, np.uint8)
    broken = LineEngine(None, name="vision")
    good = LineEngine([line(TD3[0], 80, 1000, 1000), line(TD3[1], 80, 1045, 1000)], name="ppocr")
    stage = run_mrz(
        blank, [ScriptedEngine("tesseract", TD3)], today=TODAY, line_engines=[broken, good]
    )
    assert stage.result is not None
    assert "vision:RuntimeError" in stage.engine_errors


def test_stage_reports_not_found_when_ocr_sees_no_mrz() -> None:
    blank = np.full((1200, 1200, 3), 255, np.uint8)
    finder = LineEngine([line("Nom / Surname", 100, 300, 200)])
    stage = run_mrz(blank, [ScriptedEngine("tesseract", TD3)], today=TODAY, line_engines=[finder])
    assert stage.failure == "MRZ_NOT_FOUND"
    assert finder.calls == 1


def test_ocr_fallback_skipped_when_morphology_already_reads() -> None:
    img = np.full((300, 1200, 3), 255, np.uint8)  # bande « whole_image » : lue directement
    finder = LineEngine([])
    stage = run_mrz(img, [ScriptedEngine("tesseract", TD3)], today=TODAY, line_engines=[finder])
    assert stage.result is not None and finder.calls == 0
