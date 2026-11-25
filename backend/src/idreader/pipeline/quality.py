"""B — Qualité : netteté, reflet, résolution estimée.

Ces mesures ne bloquent pas l'analyse : elles produisent des alertes et, si la MRZ échoue,
la raison d'échec affichée. Les seuils sont des valeurs de départ, à calibrer sur le banc
golden (jamais sur le banc synthétique ni sous Windows).
"""

from __future__ import annotations

import cv2
import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field

from idreader.pipeline.ingest import RGBImage

SHARPNESS_SIDE = 1000  # taille normalisée pour la variance du laplacien
SATURATED = 250
# Seuils de départ (non calibrés)
BLUR_BELOW = 60.0
GLARE_RATIO_ABOVE = 0.02
GLARE_BLOB_ABOVE = 0.005
MRZ_GLARE_ABOVE = 0.01
LOW_DPI_BELOW = 200.0

# Largeur physique (mm) par format, pour la résolution estimée.
CARD_WIDTH_MM = 85.6
PASSPORT_WIDTH_MM = 125.0


class Quality(BaseModel):
    model_config = ConfigDict(frozen=True)

    sharpness: float
    glare_ratio: float
    glare_largest_blob: float  # part de la surface
    estimated_dpi: float | None = None
    alerts: list[str] = Field(default_factory=list)


def sharpness(image: RGBImage) -> float:
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    scale = SHARPNESS_SIDE / max(h, w)
    if scale != 1:
        gray = cv2.resize(gray, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


BoolArray = NDArray[np.bool_]
PAPER_MIN_AREA = 0.0005  # une zone saturée plus petite que 0,05 % de l'image n'est jamais du fond
PAPER_DARK_PIXEL = 110  # niveau de gris sous lequel un pixel est de l'encre
PAPER_MIN_INK = 0.015  # part d'encre dans la boîte d'une zone saturée pour que ce soit du papier


def _ink_inside(component: BoolArray, ink: BoolArray) -> float:
    """Part d'encre dans l'enveloppe convexe d'une zone saturée : un reflet n'en a presque pas,
    une page blanche (texte, photo) en a."""
    contours, _ = cv2.findContours(
        component.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not contours:
        return 0.0
    hull = cv2.convexHull(max(contours, key=cv2.contourArea))
    filled = np.zeros(ink.shape, np.uint8)
    cv2.fillConvexPoly(filled, hull, 1)
    area = int(filled.sum())
    return float((ink & (filled > 0)).sum()) / area if area else 0.0


def glare(image: RGBImage) -> tuple[float, float]:
    """(part de pixels saturés sur les trois canaux, taille de la plus grande tache).

    Le fond blanc d'un scan (marges, papier du passeport, fond de la bande MRZ) est saturé lui
    aussi : une grande zone saturée dont l'enveloppe convexe contient de l'encre (texte, photo)
    est du papier, pas un reflet. Un reflet sur une carte plastifiée n'a pas de texte dessous.
    """
    mask = np.all(image > SATURATED, axis=2).astype(np.uint8)
    total = mask.size
    if total == 0 or not mask.any():
        return 0.0, 0.0
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    ink: BoolArray = np.asarray(gray < PAPER_DARK_PIXEL)
    areas: list[int] = []
    for i in range(1, count):
        area = int(stats[i][cv2.CC_STAT_AREA])
        if area >= PAPER_MIN_AREA * total and _ink_inside(labels == i, ink) >= PAPER_MIN_INK:
            continue  # fond de papier
        areas.append(area)
    if not areas:
        return 0.0, 0.0
    return sum(areas) / total, max(areas) / total


def measure(image: RGBImage, *, physical_width_mm: float | None = None) -> Quality:
    sharp = sharpness(image)
    ratio, blob = glare(image)
    dpi = image.shape[1] / (physical_width_mm / 25.4) if physical_width_mm else None
    alerts: list[str] = []
    if sharp < BLUR_BELOW:
        alerts.append("QUALITY_BLUR")
    if ratio > GLARE_RATIO_ABOVE or blob > GLARE_BLOB_ABOVE:
        alerts.append("QUALITY_GLARE")
    if dpi is not None and dpi < LOW_DPI_BELOW:
        alerts.append("QUALITY_LOW_RESOLUTION")
    return Quality(
        sharpness=sharp,
        glare_ratio=ratio,
        glare_largest_blob=blob,
        estimated_dpi=dpi,
        alerts=alerts,
    )


def mrz_glare(band: RGBImage) -> bool:
    """Reflet sur la bande MRZ : alerte forte."""
    ratio, _ = glare(band)
    return ratio > MRZ_GLARE_ABOVE
