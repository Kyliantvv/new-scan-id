"""Générateur de documents UTO, PP-OCR (détection + reconnaissance) et enchaînement complet.

Les tests qui demandent les modèles ou les polices sont ignorés s'ils sont absents
(`scripts/fetch_models.py`). Aucune vraie pièce : documents synthétiques du pays fictif UTO.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import cv2
import numpy as np
import pytest

from idreader.bench.documents import generate_documents, load_doc_cases, to_mrz_name
from idreader.core.config import Settings
from idreader.engines.ppocr import PpOcrTextEngine
from idreader.engines.registry import available_engines, available_viz_engines
from idreader.pipeline.analyze import Engines, analyze_file
from idreader.pipeline.detect import detect_documents

TODAY = date(2026, 11, 25)
SETTINGS = Settings()
FONTS = (SETTINGS.models_dir / "fonts" / "Roboto.ttf").is_file() and (
    SETTINGS.models_dir / "fonts" / "ocrb10.otf"
).is_file()
VIZ = PpOcrTextEngine.from_settings(SETTINGS)
needs_fonts = pytest.mark.skipif(not FONTS, reason="polices absentes (fetch_models.py)")
needs_models = pytest.mark.skipif(VIZ is None or not FONTS, reason="modèles absents")


@pytest.mark.parametrize(
    ("name", "mrz"),
    [
        ("ÉRIKSSON", "ERIKSSON"),
        ("MÜLLER-LÜDENSCHEIDT", "MUELLER LUEDENSCHEIDT"),
        ("ANNA, MARIA", "ANNA MARIA"),
        ("D'ARTAGNAN", "D ARTAGNAN"),
        ("STRAßE", "STRASSE"),
    ],
)
def test_to_mrz_name(name: str, mrz: str) -> None:
    assert to_mrz_name(name) == mrz


def test_nested_rectangle_is_not_a_document() -> None:
    """Une photo d'identité dans une carte n'est pas un second document."""
    page = np.full((1400, 2000, 3), 245, np.uint8)
    cv2.rectangle(page, (200, 200), (1800, 1210), (150, 160, 150), -1)  # carte ID1
    cv2.rectangle(page, (260, 330), (620, 800), (60, 60, 60), -1)  # photo, portrait
    quads = detect_documents(page)
    assert len(quads) == 1
    assert quads[0].area_ratio > 0.4


@needs_fonts
def test_generator_writes_truth(tmp_path: Path) -> None:
    cases = generate_documents(
        tmp_path, count=4, seed=1, models_dir=SETTINGS.models_dir, today=TODAY
    )
    loaded, today = load_doc_cases(tmp_path)
    assert today == TODAY and len(loaded) == 4
    for c in cases:
        assert all((tmp_path / n).is_file() for n in c.images)
        assert c.fields["issuing_state"] == "UTO"
        assert len(c.mrz_lines) in (2, 3)
    # Mêmes graine et date : mêmes documents.
    again = generate_documents(
        tmp_path / "b", count=4, seed=1, models_dir=SETTINGS.models_dir, today=TODAY
    )
    assert [c.fields for c in again] == [c.fields for c in cases]


@needs_models
def test_ppocr_reads_accented_text() -> None:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (900, 220), (240, 240, 240))
    font = ImageFont.truetype(str(SETTINGS.models_dir / "fonts" / "Roboto.ttf"), 48)
    draw = ImageDraw.Draw(img)
    draw.text((30, 30), "DÉSIRÉ-LAFORÊT", font=font, fill=(20, 20, 20))
    draw.text((30, 120), "12 AVR/APR 1987", font=font, fill=(20, 20, 20))
    assert VIZ is not None
    lines = VIZ.recognize(np.asarray(img), languages=["fr"], level="accurate", top_candidates=1)
    texts = [ln.text for ln in lines]
    assert texts == ["DÉSIRÉ-LAFORÊT", "12 AVR/APR 1987"]
    assert lines[0].quad.bbox[1] < lines[1].quad.bbox[1]


@needs_models
def test_passport_end_to_end(tmp_path: Path) -> None:
    cases = generate_documents(
        tmp_path, count=1, seed=3, models_dir=SETTINGS.models_dir, today=TODAY, a4_share=0
    )
    (case,) = cases
    assert case.kind == "passport"
    engines = Engines(mrz=available_engines(SETTINGS), viz=available_viz_engines(SETTINGS))
    fa = analyze_file(
        tmp_path / case.images[0], engines, today=TODAY, threshold=0.95, allow_specimen=True
    )
    assert len(fa.crops) == 1
    crop = fa.crops[0]
    assert crop.has_mrz and crop.physical_format == "ID3" and crop.side == "front"
    fields = {k: f.value for k, f in crop.result.fields.items()}
    for name in ("surname", "given_names", "date_of_birth", "document_number", "date_of_expiry"):
        assert fields[name] == case.fields[name], name
    # Un seul moteur sous Windows : jamais de validation automatique.
    if engines.names == ["ppocr"]:
        assert crop.result.status.value == "to_review"
