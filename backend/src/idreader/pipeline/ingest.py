"""A — Ingestion : fichier → pages en RGB 8 bits.

- type réel par octets magiques ;
- PDF (pypdfium2) : page faite d'une seule image (scan) → image à sa résolution native,
  sinon rendu à 300 dpi ; limite de pages ;
- images : orientation EXIF appliquée, conversion RGB ;
- côté long plafonné à 4 000 px pour le traitement (l'original reste intact).

HEIC (ImageIO via `idvision decode_image`) : phase 1b, sur macOS.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt
import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c
from PIL import Image, ImageOps
from pydantic import BaseModel, ConfigDict

RGBImage = npt.NDArray[np.uint8]

MAX_PAGES = 200
MAX_SIDE = 4000
RENDER_DPI = 300
# Garde-fou contre les images bombes (Pillow lève une erreur au-delà de 2× cette valeur).
MAX_PIXELS = 150_000_000


class FileKind(StrEnum):
    PDF = "pdf"
    JPEG = "jpeg"
    PNG = "png"
    TIFF = "tiff"
    WEBP = "webp"
    HEIC = "heic"


class IngestError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def detect_kind(head: bytes) -> FileKind | None:
    if head.startswith(b"%PDF-"):
        return FileKind.PDF
    if head.startswith(b"\xff\xd8\xff"):
        return FileKind.JPEG
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return FileKind.PNG
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return FileKind.TIFF
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return FileKind.WEBP
    if head[4:8] == b"ftyp" and head[8:12] in (b"heic", b"heix", b"mif1", b"heim", b"hevc"):
        return FileKind.HEIC
    return None


class PageInfo(BaseModel):
    """Artefact JSON de l'étape A pour une page."""

    model_config = ConfigDict(frozen=True)

    page: int  # 1-based
    source: str  # pdf_image | pdf_render | image
    original_width: int
    original_height: int
    width: int
    height: int
    scale: float  # taille de travail / taille d'origine
    dpi: float | None  # résolution d'origine si connue


@dataclass(frozen=True)
class Page:
    info: PageInfo
    image: RGBImage


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def cap_size(image: RGBImage, max_side: int = MAX_SIDE) -> tuple[RGBImage, float]:
    h, w = image.shape[:2]
    side = max(h, w)
    if side <= max_side:
        return image, 1.0
    scale = max_side / side
    resized = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(resized, dtype=np.uint8), scale


def _to_rgb(img: Image.Image) -> RGBImage:
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        background = Image.new("RGBA", img.size, (255, 255, 255, 255))
        img = Image.alpha_composite(background, img)
    if img.mode != "RGB":
        img = img.convert("RGB")  # 16 bits, CMJN, niveaux de gris → RGB 8 bits
    return np.asarray(img, dtype=np.uint8).copy()


def _page(page: int, source: str, image: RGBImage, dpi: float | None, max_side: int) -> Page:
    oh, ow = image.shape[:2]
    work, scale = cap_size(image, max_side)
    h, w = work.shape[:2]
    info = PageInfo(
        page=page,
        source=source,
        original_width=ow,
        original_height=oh,
        width=w,
        height=h,
        scale=scale,
        dpi=dpi,
    )
    return Page(info, work)


def load_image(path: Path, *, max_side: int = MAX_SIDE) -> list[Page]:
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(path) as img:
            dpi_info = img.info.get("dpi")
            frame = ImageOps.exif_transpose(img)
            rgb = _to_rgb(frame)
    except Image.DecompressionBombError:
        raise IngestError("IMAGE_TOO_LARGE") from None
    except OSError:
        raise IngestError("IMAGE_UNREADABLE") from None
    dpi = float(dpi_info[0]) if isinstance(dpi_info, tuple) and dpi_info and dpi_info[0] else None
    return [_page(1, "image", rgb, dpi, max_side)]


def _single_image(page: pdfium.PdfPage) -> pdfium.PdfImage | None:
    objects = list(page.get_objects(max_depth=1))
    images = [o for o in objects if o.type == pdfium_c.FPDF_PAGEOBJ_IMAGE]
    others = [
        o for o in objects if o.type in (pdfium_c.FPDF_PAGEOBJ_TEXT, pdfium_c.FPDF_PAGEOBJ_PATH)
    ]
    if len(images) == 1 and not others:
        image = images[0]
        return image if isinstance(image, pdfium.PdfImage) else None
    return None


def load_pdf(path: Path, *, max_pages: int = MAX_PAGES, max_side: int = MAX_SIDE) -> list[Page]:
    try:
        pdf = pdfium.PdfDocument(path)
    except pdfium.PdfiumError:
        raise IngestError("PDF_UNREADABLE") from None
    try:
        if len(pdf) == 0:
            raise IngestError("PDF_EMPTY")
        if len(pdf) > max_pages:
            raise IngestError("PDF_TOO_MANY_PAGES")
        pages: list[Page] = []
        for i in range(len(pdf)):
            page = pdf[i]
            try:
                width_pt = page.get_width()
                image_obj = _single_image(page)
                if image_obj is not None:
                    bitmap = image_obj.get_bitmap(render=False)
                    rgb = _to_rgb(bitmap.to_pil())
                    dpi = rgb.shape[1] / (width_pt / 72) if width_pt else None
                    pages.append(_page(i + 1, "pdf_image", rgb, dpi, max_side))
                else:
                    bitmap = page.render(scale=RENDER_DPI / 72)
                    rgb = _to_rgb(bitmap.to_pil())
                    pages.append(_page(i + 1, "pdf_render", rgb, float(RENDER_DPI), max_side))
            finally:
                page.close()
        return pages
    finally:
        pdf.close()


def load_pages(path: Path, *, max_pages: int = MAX_PAGES, max_side: int = MAX_SIDE) -> list[Page]:
    with path.open("rb") as f:
        kind = detect_kind(f.read(16))
    if kind is FileKind.PDF:
        return load_pdf(path, max_pages=max_pages, max_side=max_side)
    if kind in (FileKind.JPEG, FileKind.PNG, FileKind.TIFF, FileKind.WEBP):
        return load_image(path, max_side=max_side)
    if kind is FileKind.HEIC:
        raise IngestError("HEIC_NEEDS_IDVISION")  # phase 1b, macOS
    raise IngestError("UNSUPPORTED_FORMAT")
