"""B — Qualité : netteté, reflet, résolution estimée.

Ces mesures ne bloquent pas l'analyse : elles produisent des alertes et, si la MRZ échoue,
la raison d'échec affichée. Les seuils sont des valeurs de départ, à calibrer sur le banc
golden (jamais sur le banc synthétique ni sous Windows).
"""

from __future__ import annotations

import cv2
import numpy as np
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


def glare(image: RGBImage) -> tuple[float, float]:
    """(part de pixels saturés sur les trois canaux, taille de la plus grande tache)."""
    mask = np.all(image > SATURATED, axis=2).astype(np.uint8)
    total = mask.size
    if total == 0 or not mask.any():
        return 0.0, 0.0
    _, _, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    largest = int(stats[1:, cv2.CC_STAT_AREA].max()) if len(stats) > 1 else 0
    return float(mask.sum()) / total, largest / total


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
