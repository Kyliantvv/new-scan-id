"""Images synthétiques pour les tests (aucune vraie pièce)."""

from __future__ import annotations

import cv2
import numpy as np

from idreader.pipeline.ingest import RGBImage

A4_300DPI = (3508, 2480)  # hauteur, largeur
MM = 300 / 25.4  # pixels par mm à 300 dpi


def card(width_mm: float, height_mm: float, color: tuple[int, int, int], seed: int = 0) -> RGBImage:
    """Carte unie avec bandeau, « photo » et lignes de faux texte."""
    w, h = round(width_mm * MM), round(height_mm * MM)
    img = np.full((h, w, 3), color, dtype=np.uint8)
    rng = np.random.default_rng(seed)
    cv2.rectangle(img, (0, 0), (w, h // 7), (60, 80, 140), -1)
    cv2.rectangle(img, (w // 20, h // 4), (w // 3, h * 4 // 5), (150, 130, 120), -1)
    for i in range(6):
        y = h // 4 + i * h // 12
        x2 = int(w * rng.uniform(0.6, 0.95))
        cv2.putText(
            img,
            "XXXX XXXXXX 0000",
            (w * 2 // 5, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.2,
            (40, 40, 40),
            3,
            cv2.LINE_AA,
        )
        cv2.line(img, (w * 2 // 5, y + 12), (x2, y + 12), (90, 90, 90), 2)
    cv2.putText(
        img,
        "IDUTO<<<<<<<<<<<<<<<<<<<<<<",
        (w // 20, h - h // 10),
        cv2.FONT_HERSHEY_SIMPLEX,
        1.6,
        (20, 20, 20),
        3,
        cv2.LINE_AA,
    )
    return img


def paste(page: RGBImage, item: RGBImage, center: tuple[int, int], angle: float) -> None:
    """Colle `item` tourné de `angle` degrés, centré en `center`, sur `page` (en place)."""
    h, w = item.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    cos, sin = abs(matrix[0, 0]), abs(matrix[0, 1])
    bw, bh = int(h * sin + w * cos), int(h * cos + w * sin)
    matrix[0, 2] += bw / 2 - w / 2
    matrix[1, 2] += bh / 2 - h / 2
    rotated = cv2.warpAffine(item, matrix, (bw, bh), borderValue=(0, 0, 0))
    mask = cv2.warpAffine(np.full((h, w), 255, np.uint8), matrix, (bw, bh))
    x0, y0 = center[0] - bw // 2, center[1] - bh // 2
    region = page[y0 : y0 + bh, x0 : x0 + bw]
    region[mask > 0] = rotated[mask > 0]


def blank_a4(seed: int = 0) -> RGBImage:
    rng = np.random.default_rng(seed)
    page = np.full((*A4_300DPI, 3), 246, dtype=np.uint8)
    noise = rng.normal(0, 3, page.shape)
    return np.clip(page + noise, 0, 255).astype(np.uint8)


def a4_recto_verso() -> RGBImage:
    """Page A4 scannée à 300 dpi : recto et verso d'une carte ID-1 côte à côte."""
    page = blank_a4()
    paste(page, card(85.6, 54, (205, 220, 235), seed=1), (650, 800), 2.0)
    paste(page, card(85.6, 54, (225, 215, 195), seed=2), (1800, 820), -1.5)
    return page
