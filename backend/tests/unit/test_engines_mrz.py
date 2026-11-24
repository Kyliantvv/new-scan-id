"""Moteurs Tesseract (faux binaire) et PP-OCR (décodage ; modèle réel si installé)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

from idreader.core.config import Settings
from idreader.engines.base import Roi
from idreader.engines.ppocr import PpOcrError, PpOcrRecEngine, ctc_decode, preprocess
from idreader.engines.registry import available_engines
from idreader.engines.tesseract import TesseractError, TesseractMrzEngine, parse_tsv

FAKE = Path(__file__).resolve().parents[1] / "fakes" / "fake_tesseract.py"
TD3 = [
    "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
    "L898902C36UTO7408122F1204159ZE184226B<<<<<10",
]


@pytest.fixture
def tesseract(tmp_path: Path) -> TesseractMrzEngine:
    return TesseractMrzEngine(
        command=str(FAKE), tessdata_dir=tmp_path, launcher=(sys.executable,), timeout_seconds=5
    )


def band() -> np.ndarray:
    return np.full((200, 1000, 3), 255, np.uint8)


def test_tesseract_reads_lines_with_offset(tesseract: TesseractMrzEngine) -> None:
    lines = tesseract.recognize(band(), roi=Roi(x=10, y=5, width=900, height=150))
    assert [line.text for line in lines] == TD3
    assert lines[0].engine == "tesseract"
    assert lines[0].confidence == pytest.approx(0.915)
    assert lines[0].quad.points[0] == (30.0, 65.0)  # décalage de la zone ajouté


def test_tesseract_failure_and_timeout(
    tesseract: TesseractMrzEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_TESSERACT_MODE", "fail")
    with pytest.raises(TesseractError, match="TESSERACT_FAILED"):
        tesseract.recognize(band())
    monkeypatch.setenv("FAKE_TESSERACT_MODE", "hang")
    tesseract.timeout_seconds = 0.5
    with pytest.raises(TesseractError, match="TESSERACT_TIMEOUT"):
        tesseract.recognize(band())


def test_tesseract_missing_binary(tmp_path: Path) -> None:
    engine = TesseractMrzEngine(command=str(tmp_path / "absent"), tessdata_dir=tmp_path)
    with pytest.raises(TesseractError, match="TESSERACT_NOT_FOUND"):
        engine.recognize(band())


def test_parse_tsv_ignores_empty_words() -> None:
    tsv = "header\n5\t1\t1\t1\t1\t1\t0\t0\t10\t10\t-1\t \n5\t1\t1\t1\t1\t2\t0\t0\t10\t10\t50\tAB\n"
    [line] = parse_tsv(tsv)
    assert line.text == "AB" and line.confidence == pytest.approx(0.5)


def test_ctc_decode() -> None:
    dictionary = ["A", "B", "<"]
    # pas de temps : A A blanc A B < < (blanc entre les deux < absent → fusionnés)
    idx = [1, 1, 0, 1, 2, 3, 3, 0, 3, 4]
    probs = np.full((len(idx), 5), 0.01, np.float32)
    for t, i in enumerate(idx):
        probs[t, i] = 0.96
    text, conf = ctc_decode(probs, dictionary)
    assert text == "AAB<< "
    assert conf == pytest.approx(0.96)
    with pytest.raises(PpOcrError):
        ctc_decode(np.zeros((2, 2, 2), np.float32), dictionary)


def test_ctc_decode_applies_softmax_to_logits() -> None:
    logits = np.array([[0.0, 5.0, -3.0], [9.0, 0.0, 0.0]], np.float32)
    assert ctc_decode(logits, ["X", "Y"])[0] == "X"


def test_preprocess_shape() -> None:
    t = preprocess(np.zeros((40, 1000), np.uint8))
    assert t.shape == (1, 3, 48, 1200) and t.dtype == np.float32
    assert t.min() == -1.0
    assert preprocess(np.zeros((40, 50, 3), np.uint8)).shape == (1, 3, 48, 320)


def test_registry_with_no_engine(settings: Settings, tmp_path: Path) -> None:
    s = settings.model_copy(update={"models_dir": tmp_path, "tesseract_cmd": "absent-xyz"})
    assert available_engines(s) == []


REAL = Settings().models_dir / "ppocr" / "v6_small_rec" / "inference.onnx"
FONT = Settings().models_dir / "fonts" / "ocrb10.otf"


@pytest.mark.skipif(
    not (REAL.is_file() and FONT.is_file()), reason="modèle PP-OCR ou police absents"
)
def test_ppocr_real_model_reads_ocrb_line() -> None:
    from PIL import Image, ImageDraw, ImageFont

    engine = PpOcrRecEngine.from_settings(Settings())
    assert engine is not None
    font = ImageFont.truetype(str(FONT), 40)
    line = TD3[0]
    img = Image.new("RGB", (int(font.getlength(line)) + 40, 64), "white")
    ImageDraw.Draw(img).text((20, 8), line, font=font, fill="black")
    [result] = engine.recognize(np.asarray(img))
    assert result.text.upper() == line
    assert result.confidence > 0.9
