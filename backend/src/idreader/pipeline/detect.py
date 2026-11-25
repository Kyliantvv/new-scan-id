"""C — Détection et recadrage des documents (version OpenCV).

Phase 1a : repli OpenCV seul (contours → quadrilatères), puis la page entière avec
l'alerte « document non détouré ». `idvision detect_documents` (Vision) et le repli par
la MRZ viennent en phase 1b.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import cv2
import numpy as np
import numpy.typing as npt
from cv2.typing import MatLike
from pydantic import BaseModel, ConfigDict

from idreader.pipeline.ingest import RGBImage

FloatArray = npt.NDArray[np.float32]

MIN_AREA_RATIO = 0.05  # > 5 % de la page
MAX_AREA_RATIO = 0.98
MIN_ASPECT, MAX_ASPECT = 1.25, 1.80  # ID-1 ≈ 1,59 ; ID-2 et ID-3 ≈ 1,42
NMS_IOU = 0.6
MAX_CANDIDATES = 6
WORK_SIDE = 1200  # détection sur une version réduite
ID1_MIN_ASPECT = 1.50  # au-dessus : hypothèse ID-1, sinon ID-2/ID-3


class SizeClass(StrEnum):
    """Hypothèse tirée du rapport largeur/hauteur ; `physical_format` reste `unknown`."""

    ID1 = "id1"
    ID23 = "id2_or_id3"
    UNKNOWN = "unknown"


# Tailles canoniques (≈ 500 dpi), 03_PIPELINE_ANALYSE.md § C. ID-2 et ID-3 ont le même
# rapport : on redresse à la taille ID-3, la plus grande.
CANONICAL: dict[SizeClass, tuple[int, int]] = {
    SizeClass.ID1: (1712, 1080),
    SizeClass.ID23: (2500, 1760),
}


class DocumentQuad(BaseModel):
    model_config = ConfigDict(frozen=True)

    points: list[tuple[float, float]]  # TL, TR, BR, BL en pixels de la page
    score: float
    area_ratio: float
    aspect: float  # côté long / côté court
    size_class: SizeClass
    source: str  # opencv | page


@dataclass(frozen=True)
class Crop:
    quad: DocumentQuad
    image: RGBImage


def order_points(pts: FloatArray) -> FloatArray:
    """Ordonne 4 points : haut-gauche, haut-droit, bas-droit, bas-gauche."""
    pts = pts.reshape(4, 2).astype(np.float32)
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array(
        [pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]],
        dtype=np.float32,
    )


def _sides(q: FloatArray) -> tuple[float, float]:
    w = (np.linalg.norm(q[1] - q[0]) + np.linalg.norm(q[2] - q[3])) / 2
    h = (np.linalg.norm(q[3] - q[0]) + np.linalg.norm(q[2] - q[1])) / 2
    return float(w), float(h)


def _iou(a: FloatArray, b: FloatArray) -> float:
    area_a = cv2.contourArea(a)
    area_b = cv2.contourArea(b)
    inter, _ = cv2.intersectConvexConvex(a, b)
    union = area_a + area_b - inter
    return float(inter / union) if union > 0 else 0.0


def _masks(gray: MatLike) -> list[MatLike]:
    """Deux stratégies : contours (Canny + dilatation) et seuil d'Otsu sur le fond."""
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 30, 100)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    edges = cv2.dilate(edges, kernel, iterations=2)
    _, otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    otsu = cv2.morphologyEx(otsu, cv2.MORPH_CLOSE, kernel, iterations=3)
    return [edges, otsu]


def _quad_from_contour(contour: MatLike) -> FloatArray | None:
    peri = cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
    if len(approx) == 4 and cv2.isContourConvex(approx):
        return order_points(approx.astype(np.float32))
    # Coins arrondis des cartes : rectangle d'aire minimale si le contour le remplit bien.
    rect = cv2.minAreaRect(contour)
    box = cv2.boxPoints(rect).astype(np.float32)
    rect_area = rect[1][0] * rect[1][1]
    if rect_area > 0 and cv2.contourArea(contour) / rect_area > 0.85:
        return order_points(box)
    return None


def _inside(inner: FloatArray, outer: FloatArray) -> bool:
    """`inner` est-il un élément d'`outer` (photo d'identité, encadré) ? Centre à l'intérieur
    et aire au plus moitié : un document ne contient jamais un autre document."""
    cx, cy = inner.mean(axis=0)
    contour = outer.reshape(-1, 1, 2).astype(np.float32)
    return bool(
        cv2.pointPolygonTest(contour, (float(cx), float(cy)), False) >= 0
        and cv2.contourArea(inner) <= 0.5 * cv2.contourArea(outer)
    )


def size_class(aspect: float) -> SizeClass:
    if not MIN_ASPECT <= aspect <= MAX_ASPECT:
        return SizeClass.UNKNOWN
    return SizeClass.ID1 if aspect >= ID1_MIN_ASPECT else SizeClass.ID23


def detect_documents(
    image: RGBImage, *, max_candidates: int = MAX_CANDIDATES
) -> list[DocumentQuad]:
    """Quadrilatères des documents de la page (au plus `max_candidates`), sinon la page."""
    h, w = image.shape[:2]
    scale = min(1.0, WORK_SIDE / max(h, w))
    small = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    page_area = float(gray.shape[0] * gray.shape[1])

    found: list[tuple[float, FloatArray]] = []
    for mask in _masks(gray):
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for c in contours:
            area = cv2.contourArea(c)
            if not MIN_AREA_RATIO * page_area <= area <= MAX_AREA_RATIO * page_area:
                continue
            quad = _quad_from_contour(c)
            if quad is None:
                continue
            sw, sh = _sides(quad)
            aspect = max(sw, sh) / max(min(sw, sh), 1.0)
            if size_class(aspect) is SizeClass.UNKNOWN:
                continue
            rectangularity = area / max(cv2.contourArea(quad), 1.0)
            found.append((area / page_area * min(rectangularity, 1.0), quad))

    found.sort(key=lambda x: -x[0])
    kept: list[tuple[float, FloatArray]] = []
    for score, quad in found:
        if all(_iou(quad, k) <= NMS_IOU for _, k in kept) and not any(
            _inside(quad, k) for _, k in kept
        ):
            kept.append((score, quad))
        if len(kept) == max_candidates:
            break

    result: list[DocumentQuad] = []
    for score, quad in kept:
        full = quad / scale
        sw, sh = _sides(full)
        aspect = max(sw, sh) / max(min(sw, sh), 1.0)
        result.append(
            DocumentQuad(
                points=[(float(x), float(y)) for x, y in full],
                score=float(score),
                area_ratio=float(cv2.contourArea(quad) / page_area),
                aspect=aspect,
                size_class=size_class(aspect),
                source="opencv",
            )
        )
    if not result:
        aspect = max(w, h) / min(w, h)
        result.append(
            DocumentQuad(
                points=[(0.0, 0.0), (float(w), 0.0), (float(w), float(h)), (0.0, float(h))],
                score=0.0,
                area_ratio=1.0,
                aspect=aspect,
                size_class=size_class(aspect),
                source="page",
            )
        )
    # Ordre de lecture : de haut en bas puis de gauche à droite.
    result.sort(
        key=lambda q: (round(min(p[1] for p in q.points) / (h / 4)), min(p[0] for p in q.points))
    )
    return result


def warp(image: RGBImage, quad: DocumentQuad) -> Crop:
    """Redressement par perspective vers la taille canonique (portrait conservé : l'étape D
    tourne ensuite le recadrage)."""
    src = np.array(quad.points, dtype=np.float32)
    sw, sh = _sides(src)
    if quad.source == "page" or quad.size_class is SizeClass.UNKNOWN:
        out_w, out_h = round(sw), round(sh)
    else:
        long_side, short_side = CANONICAL[quad.size_class]
        out_w, out_h = (long_side, short_side) if sw >= sh else (short_side, long_side)
    dst = np.array(
        [[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]], dtype=np.float32
    )
    matrix = cv2.getPerspectiveTransform(src, dst)
    crop = cv2.warpPerspective(image, matrix, (out_w, out_h), flags=cv2.INTER_CUBIC)
    return Crop(quad=quad, image=np.ascontiguousarray(crop, dtype=np.uint8))


def crops(image: RGBImage) -> list[Crop]:
    return [warp(image, q) for q in detect_documents(image)]
