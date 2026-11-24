"""Test du vrai helper `idvision` sur une image synthétique (macOS uniquement).

Ignoré si le binaire n'est pas compilé (`swift build -c release --package-path vision-helper`)
ou hors macOS. Vérifie surtout la conversion des coordonnées : pixels, origine en haut à gauche.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from idreader.core.config import Settings
from idreader.engines.apple_vision import AppleVisionEngine, VisionHelperClient
from idreader.engines.base import Roi

BINARY = Settings().idvision_path
pytestmark = pytest.mark.skipif(
    sys.platform != "darwin" or not BINARY.is_file(),
    reason="helper idvision non compilé ou système autre que macOS",
)

TEXT = "IDREADER 2468"


@pytest.fixture
def synthetic(tmp_path: Path) -> tuple[Path, tuple[int, int, int, int]]:
    img = Image.new("RGB", (1400, 700), "white")
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=72)
    origin = (200, 450)  # texte dans la moitié BASSE : une inversion de l'axe y se verrait
    draw.text(origin, TEXT, fill="black", font=font)
    box = draw.textbbox(origin, TEXT, font=font)
    path = tmp_path / "synthetic.png"
    img.save(path)
    return path, (box[0], box[1], box[2], box[3])


def _check(lines, box) -> None:
    hits = [line for line in lines if "IDREADER" in line.text.replace(" ", "")]
    assert hits, [line.text for line in lines]
    x, y, w, h = hits[0].quad.bbox
    left, top, right, bottom = box
    tol = 25
    assert abs(x - left) < tol and abs(y - top) < tol
    assert abs((x + w) - right) < tol and abs((y + h) - bottom) < tol


def test_coordinates_top_left_pixels(synthetic) -> None:
    path, box = synthetic
    with VisionHelperClient([str(BINARY), "serve"]) as client:
        lines = AppleVisionEngine(client).recognize(path, languages=["en-US"])
    _check(lines, box)


def test_roi_coordinates_in_full_image(synthetic) -> None:
    path, box = synthetic
    with VisionHelperClient([str(BINARY), "serve"]) as client:
        lines = AppleVisionEngine(client).recognize(
            path, roi=Roi(x=100, y=380, width=1200, height=250), languages=["en-US"]
        )
    _check(lines, box)


def test_doctor_lists_languages() -> None:
    with VisionHelperClient([str(BINARY), "serve"]) as client:
        info = client.doctor()
    assert info["macos_version"][0] >= 15
    assert any(lang.startswith("en") for lang in info["languages"]["accurate"])
