"""Étapes A, B, C sur des images synthétiques."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from idreader.pipeline import detect, ingest, quality
from tests.unit.pipeline.images import a4_recto_verso, blank_a4, card, paste

# --- A ----------------------------------------------------------------------------------


def test_detect_kind() -> None:
    assert ingest.detect_kind(b"%PDF-1.7") is ingest.FileKind.PDF
    assert ingest.detect_kind(b"\xff\xd8\xff\xe0") is ingest.FileKind.JPEG
    assert ingest.detect_kind(b"\x00\x00\x00\x18ftypheic") is ingest.FileKind.HEIC
    assert ingest.detect_kind(b"GIF89a") is None


def test_exif_orientation_applied(tmp_path: Path) -> None:
    img = Image.new("RGB", (300, 100), (200, 10, 10))
    exif = Image.Exif()
    exif[0x0112] = 6  # rotation de 90° à l'affichage
    path = tmp_path / "photo.jpg"
    img.save(path, exif=exif)
    [page] = ingest.load_pages(path)
    assert (page.info.width, page.info.height) == (100, 300)
    assert page.image.shape == (300, 100, 3) and page.image.dtype == np.uint8


def test_alpha_and_grayscale_become_rgb(tmp_path: Path) -> None:
    Image.new("RGBA", (50, 40), (0, 0, 0, 0)).save(tmp_path / "a.png")
    [page] = ingest.load_pages(tmp_path / "a.png")
    assert page.image.shape == (40, 50, 3) and page.image.min() == 255  # fond blanc
    Image.new("I;16", (50, 40)).save(tmp_path / "g.png")
    assert ingest.load_pages(tmp_path / "g.png")[0].image.shape == (40, 50, 3)


def test_long_side_capped(tmp_path: Path) -> None:
    Image.new("RGB", (5000, 3000), (1, 2, 3)).save(tmp_path / "big.png")
    [page] = ingest.load_pages(tmp_path / "big.png")
    assert (page.info.width, page.info.height) == (4000, 2400)
    assert page.info.scale == pytest.approx(0.8)
    assert page.info.original_width == 5000


def test_scanned_pdf_keeps_native_resolution(tmp_path: Path) -> None:
    scan = Image.fromarray(card(85.6, 54, (200, 210, 220)))
    path = tmp_path / "scan.pdf"
    scan.save(path, resolution=300)
    [page] = ingest.load_pages(path)
    assert page.info.source == "pdf_image"
    assert (page.info.original_width, page.info.original_height) == scan.size
    assert page.info.dpi == pytest.approx(300, rel=0.01)


MINIMAL_TEXT_PDF = (
    b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 100]/Contents 4 0 R"
    b"/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
    b"4 0 obj<</Length 44>>stream\nBT /F1 24 Tf 10 40 Td (SPECIMEN) Tj ET\nendstream endobj\n"
    b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n"
)


def test_text_pdf_rendered_at_300_dpi(tmp_path: Path) -> None:
    path = tmp_path / "texte.pdf"
    path.write_bytes(MINIMAL_TEXT_PDF)
    [page] = ingest.load_pages(path)
    assert page.info.source == "pdf_render"
    assert abs(page.info.width - 200 * 300 / 72) <= 1  # pdfium arrondit au pixel supérieur
    assert page.image.min() < 100  # le texte est rendu


def test_pdf_page_limit(tmp_path: Path) -> None:
    frames = [Image.new("RGB", (20, 20)) for _ in range(3)]
    path = tmp_path / "multi.pdf"
    frames[0].save(path, save_all=True, append_images=frames[1:])
    assert len(ingest.load_pages(path)) == 3
    with pytest.raises(ingest.IngestError, match="PDF_TOO_MANY_PAGES"):
        ingest.load_pages(path, max_pages=2)


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (b"GIF89a....", "UNSUPPORTED_FORMAT"),
        (b"\x00\x00\x00\x18ftypheic" + b"\x00" * 16, "HEIC_NEEDS_IDVISION"),
        (b"%PDF-1.4 tronque", "PDF_UNREADABLE"),
        (b"\xff\xd8\xff\xe0 tronque", "IMAGE_UNREADABLE"),
    ],
)
def test_ingest_errors(tmp_path: Path, content: bytes, code: str) -> None:
    path = tmp_path / "f"
    path.write_bytes(content)
    with pytest.raises(ingest.IngestError) as err:
        ingest.load_pages(path)
    assert err.value.code == code


# --- B ----------------------------------------------------------------------------------


def test_quality_blur_glare_resolution() -> None:
    sharp = card(85.6, 54, (210, 210, 210))
    good = quality.measure(sharp, physical_width_mm=quality.CARD_WIDTH_MM)
    assert good.alerts == []
    assert good.estimated_dpi == pytest.approx(300, rel=0.01)

    blurred = cv2.GaussianBlur(sharp, (0, 0), 8)
    assert "QUALITY_BLUR" in quality.measure(blurred).alerts
    assert quality.sharpness(blurred) < good.sharpness / 10

    glared = sharp.copy()
    cv2.circle(glared, (500, 300), 120, (255, 255, 255), -1)
    q = quality.measure(glared)
    assert "QUALITY_GLARE" in q.alerts and q.glare_largest_blob > 0.005

    small = cv2.resize(sharp, (400, 252))
    assert "QUALITY_LOW_RESOLUTION" in quality.measure(small, physical_width_mm=85.6).alerts


def test_mrz_glare() -> None:
    band = np.full((100, 800, 3), 200, np.uint8)
    assert not quality.mrz_glare(band)
    band[:, :100] = 255
    assert quality.mrz_glare(band)


# --- C ----------------------------------------------------------------------------------


def test_a4_recto_verso_gives_two_crops() -> None:
    page = a4_recto_verso()
    quads = detect.detect_documents(page)
    assert [q.source for q in quads] == ["opencv", "opencv"]
    assert all(q.size_class is detect.SizeClass.ID1 for q in quads)
    assert all(q.aspect == pytest.approx(85.6 / 54, rel=0.04) for q in quads)
    assert quads[0].points[0][0] < quads[1].points[0][0]  # gauche puis droite
    crops = detect.crops(page)
    assert [c.image.shape for c in crops] == [(1080, 1712, 3), (1080, 1712, 3)]
    # le fond de la page ne déborde pas dans le recadrage : coins de la couleur de la carte
    for crop, color in zip(crops, ((205, 220, 235), (225, 215, 195)), strict=True):
        corner = crop.image[1000:1060, 1640:1700].reshape(-1, 3).mean(axis=0)
        assert np.abs(corner - color).max() < 25


def test_passport_page_is_id2_or_id3() -> None:
    page = blank_a4(seed=3)
    paste(page, card(125, 88, (215, 225, 210), seed=4), (1240, 1500), 4.0)
    [quad] = detect.detect_documents(page)
    assert quad.size_class is detect.SizeClass.ID23
    assert detect.warp(page, quad).image.shape == (1760, 2500, 3)


def test_portrait_card_keeps_portrait_canonical_size() -> None:
    page = blank_a4(seed=5)
    paste(page, card(85.6, 54, (200, 215, 230), seed=6), (1240, 1700), 90.0)
    [quad] = detect.detect_documents(page)
    assert detect.warp(page, quad).image.shape == (1712, 1080, 3)


def test_no_document_falls_back_to_page() -> None:
    page = blank_a4(seed=7)
    [quad] = detect.detect_documents(page)
    assert quad.source == "page"
    assert detect.warp(page, quad).image.shape[:2] == page.shape[:2]
