"""E.1 — Localisation de la bande MRZ et découpage en lignes.

Deux localisations, dans cet ordre :
1. morphologique OpenCV (gradient horizontal, fermeture, bandes larges et denses dans le bas
   du document) ;
2. repli par les lignes OCR (`locate_band_from_lines`) : les lignes de texte d'un moteur de
   détection (Vision, PP-OCR) qui ressemblent à une MRZ. Sur des scans de passeports pris dans
   une page entière (marges, reflets, fond chargé), la morphologie échoue et l'OCR trouve
   les lignes sans difficulté.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import cv2
import numpy as np
from cv2.typing import MatLike
from pydantic import BaseModel, ConfigDict

from idreader.engines.base import TextLine
from idreader.pipeline.ingest import RGBImage

WORK_WIDTH = 1200
MARGIN_Y = 0.08  # marge de 8 % en hauteur (E.1)
MARGIN_X = 0.03  # marge de 3 % en largeur
MIN_WIDTH_RATIO = 0.55  # la MRZ couvre presque toute la largeur du document
MIN_ASPECT = 4.0
BAND_ASPECT = 3.0  # image déjà réduite à la bande (au-delà de ce rapport)
LOWER_FROM = 0.40  # centre de la bande sous 40 % de la hauteur


class Band(BaseModel):
    model_config = ConfigDict(frozen=True)

    x: int
    y: int
    width: int
    height: int
    source: str  # morphology | whole_image | ocr_lines


def _gray(image: RGBImage) -> MatLike:
    return image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)


def locate_band(image: RGBImage) -> Band | None:
    """Bande MRZ (pixels de `image`), ou None si rien de plausible."""
    h, w = image.shape[:2]
    if w / max(h, 1) >= BAND_ASPECT:
        return Band(x=0, y=0, width=w, height=h, source="whole_image")
    scale = WORK_WIDTH / w
    gray = cv2.resize(
        _gray(image), (WORK_WIDTH, max(1, round(h * scale))), interpolation=cv2.INTER_AREA
    )
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    rect = cv2.getStructuringElement(cv2.MORPH_RECT, (13, 5))
    square = cv2.getStructuringElement(cv2.MORPH_RECT, (21, 21))
    blackhat = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, rect)  # texte sombre sur fond clair
    grad = np.absolute(np.asarray(cv2.Sobel(blackhat, cv2.CV_32F, 1, 0, ksize=-1), np.float32))
    lo, hi = float(grad.min()), float(grad.max())
    grad8: MatLike = ((grad - lo) / (hi - lo + 1e-6) * 255).astype(np.uint8)
    grad8 = cv2.morphologyEx(grad8, cv2.MORPH_CLOSE, rect)
    _, thresh = cv2.threshold(grad8, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, square)
    thresh = cv2.erode(thresh, np.ones((3, 3), np.uint8), iterations=2)

    gh, gw = thresh.shape
    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    pieces: list[Box] = []
    for c in contours:
        bx, by, bw, bh = cv2.boundingRect(c)
        pieces.append((int(bx), int(by), int(bw), int(bh)))
    pieces = [b for b in pieces if b[2] >= 0.05 * gw and b[2] / max(b[3], 1) >= 2]
    rows = _merge_rows(pieces, max_gap=0.12 * gw)
    boxes = [b for b in rows if b[2] >= MIN_WIDTH_RATIO * gw and b[2] / max(b[3], 1) >= MIN_ASPECT]
    # Lignes MRZ empilées : écart < 1,5 hauteur de ligne et recouvrement horizontal > 70 %.
    boxes.sort(key=lambda b: b[1])
    groups: list[list[Box]] = []
    for box in boxes:
        last = groups[-1][-1] if groups else None
        if last is not None:
            gap = box[1] - (last[1] + last[3])
            overlap = min(box[0] + box[2], last[0] + last[2]) - max(box[0], last[0])
            if gap < 1.5 * max(box[3], last[3]) and overlap > 0.7 * min(box[2], last[2]):
                groups[-1].append(box)
                continue
        groups.append([box])
    best: tuple[float, tuple[int, int, int, int]] | None = None
    for group in groups:
        x = min(b[0] for b in group)
        y = min(b[1] for b in group)
        cw = max(b[0] + b[2] for b in group) - x
        ch = max(b[1] + b[3] for b in group) - y
        if (y + ch / 2) / gh < LOWER_FROM:
            continue
        lines_bonus = 1.0 if 2 <= len(group) <= 3 else 0.5
        score = (cw / gw) * lines_bonus + (y + ch) / gh  # large, 2–3 lignes, le plus bas
        if best is None or score > best[0]:
            best = (score, (x, y, cw, ch))
    if best is None:
        return None
    x, y, cw, ch = (v / scale for v in best[1])
    mx, my = MARGIN_X * w, MARGIN_Y * ch
    x0, y0 = max(0, round(x - mx)), max(0, round(y - my))
    x1, y1 = min(w, round(x + cw + mx)), min(h, round(y + ch + my))
    return Band(x=x0, y=y0, width=x1 - x0, height=y1 - y0, source="morphology")


Box = tuple[int, int, int, int]


def _merge_rows(pieces: list[Box], *, max_gap: float) -> list[Box]:
    """Fusionne les morceaux d'une même rangée de texte (les longues suites de `<` coupent
    souvent une ligne MRZ en plusieurs blocs)."""
    rows: list[Box] = []
    for b in sorted(pieces, key=lambda b: b[0]):
        for i, r in enumerate(rows):
            v_overlap = min(b[1] + b[3], r[1] + r[3]) - max(b[1], r[1])
            gap = b[0] - (r[0] + r[2])
            if v_overlap > 0.5 * min(b[3], r[3]) and gap < max_gap:
                x0, y0 = min(r[0], b[0]), min(r[1], b[1])
                x1, y1 = max(r[0] + r[2], b[0] + b[2]), max(r[1] + r[3], b[1] + b[3])
                rows[i] = (x0, y0, x1 - x0, y1 - y0)
                break
        else:
            rows.append(b)
    return rows


def crop_band(image: RGBImage, band: Band) -> RGBImage:
    return np.ascontiguousarray(image[band.y : band.y + band.height, band.x : band.x + band.width])


def split_lines(band: RGBImage, *, expected: int | None = None) -> list[tuple[int, int]]:
    """Lignes de texte de la bande (début, fin en y) par profil de projection horizontal."""
    gray = _gray(band)
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    profile = (ink > 0).sum(axis=1).astype(np.float32)
    if profile.max() <= 0:
        return []
    active = profile > 0.15 * profile.max()
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for y, on in enumerate(active):
        if on and start is None:
            start = y
        elif not on and start is not None:
            runs.append((start, y))
            start = None
    if start is not None:
        runs.append((start, len(active)))
    if not runs:
        return []
    tallest = max(e - s for s, e in runs)
    runs = [r for r in runs if r[1] - r[0] >= 0.4 * tallest]
    # Fusion des morceaux trop rapprochés (accents, bruit) jusqu'au nombre attendu.
    while expected is not None and len(runs) > expected:
        gaps = [runs[i + 1][0] - runs[i][1] for i in range(len(runs) - 1)]
        i = int(np.argmin(gaps))
        runs[i : i + 2] = [(runs[i][0], runs[i + 1][1])]
    pad = max(2, round(0.25 * tallest))
    h = gray.shape[0]
    return [(max(0, s - pad), min(h, e + pad)) for s, e in runs]


MAX_SKEW_DEGREES = 5.0  # D.3 : redressement fin limité à ± 5°


def _profile_score(ink: MatLike, angle: float) -> float:
    h, w = ink.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    rotated = cv2.warpAffine(ink, matrix, (w, h), flags=cv2.INTER_NEAREST, borderValue=0)
    profile = rotated.sum(axis=1, dtype=np.float64)
    return float(np.var(profile))


def skew_angle(band: RGBImage) -> float:
    """Angle (degrés, sens de cv2.getRotationMatrix2D) qui redresse les lignes de la bande :
    celui qui rend le profil de projection horizontal le plus contrasté (lignes nettes).
    Recherche grossière par pas de 0,5° puis fine par pas de 0,1°, limitée à ± 5°."""
    gray = _gray(band)
    h, w = gray.shape[:2]
    scale = min(1.0, 800 / max(w, 1))
    if scale < 1.0:
        gray = cv2.resize(
            gray, (round(w * scale), max(1, round(h * scale))), interpolation=cv2.INTER_AREA
        )
    _, ink = cv2.threshold(gray, 0, 1, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    if int(ink.sum()) < 50:
        return 0.0
    coarse = np.arange(-MAX_SKEW_DEGREES, MAX_SKEW_DEGREES + 1e-6, 0.5)
    best = max(coarse, key=lambda a: _profile_score(ink, float(a)))
    fine = np.arange(best - 0.5, best + 0.5 + 1e-6, 0.1)
    angle = float(max(fine, key=lambda a: _profile_score(ink, float(a))))
    return round(angle, 2) if abs(angle) <= MAX_SKEW_DEGREES else 0.0


def deskew(band: RGBImage) -> RGBImage:
    angle = skew_angle(band)
    if abs(angle) < 0.2:
        return band
    h, w = band.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    out = cv2.warpAffine(
        band, matrix, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    return np.ascontiguousarray(out, dtype=np.uint8)


# --- Repli par les lignes OCR -----------------------------------------------------------------

MRZ_LINE_LENGTHS = (30, 36, 44)  # TD1, TD2 / MRV-B, TD3 / MRV-A (ICAO 9303)
MIN_MRZ_CHARS = 26  # une ligne MRZ lue avec quelques caractères en moins reste reconnue
MRZ_ALPHABET = re.compile(r"^[A-Z0-9<]+$")
_FILLER_LOOKALIKES = str.maketrans({"«": "<", "‹": "<", "〈": "<", "＜": "<"})
_MRZ_START = ("P<", "I<", "V<", "A<", "C<", "ID", "IP", "PP")
STACK_GAP = 2.5  # écart maximal entre deux lignes MRZ, en hauteurs de ligne
SAME_EDGE = 0.08  # bords gauches alignés à 8 % de la largeur près
SAME_LENGTH = 0.15  # longueurs alignées à 15 % près


def normalize_mrz_text(text: str) -> str:
    """Texte réduit à l'alphabet MRZ : majuscules, sans espaces, chevrons homogènes."""
    return re.sub(r"\s+", "", text.upper().translate(_FILLER_LOOKALIKES))


def looks_like_mrz_line(text: str) -> bool:
    """Ligne de MRZ plausible : longue, presque entièrement dans l'alphabet MRZ, avec des
    chevrons ou un début de code de document (ICAO 9303 partie 3)."""
    t = normalize_mrz_text(text)
    if len(t) < MIN_MRZ_CHARS:
        return False
    in_alphabet = sum(1 for c in t if c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<")
    if in_alphabet / len(t) < 0.9:
        return False
    return t.count("<") >= 2 or t.startswith(_MRZ_START)


def locate_band_from_lines(lines: Sequence[TextLine], width: int, height: int) -> Band | None:
    """Bande MRZ d'après les lignes OCR : la pile de 2 ou 3 lignes MRZ la plus basse."""
    cands = []
    for line in lines:
        if looks_like_mrz_line(line.text):
            x, y, w, h = line.quad.bbox
            if h > 0 and w / max(h, 1) >= 6:  # une ligne MRZ est très allongée
                cands.append((x, y, w, h))
    cands.sort(key=lambda b: b[1])
    stacks: list[list[tuple[float, float, float, float]]] = []
    for c in cands:
        for stack in stacks:
            last = stack[-1]
            gap = c[1] - (last[1] + last[3])
            aligned = abs(c[0] - last[0]) <= SAME_EDGE * width
            similar = abs(c[2] - last[2]) <= SAME_LENGTH * max(c[2], last[2])
            if aligned and similar and -0.5 * last[3] <= gap <= STACK_GAP * max(c[3], last[3]):
                stack.append(c)
                break
        else:
            stacks.append([c])
    stacks = [s for s in stacks if 2 <= len(s) <= 3]
    if not stacks:
        return None
    best = max(stacks, key=lambda s: s[0][1])  # la plus basse
    x0 = min(b[0] for b in best)
    y0 = min(b[1] for b in best)
    x1 = max(b[0] + b[2] for b in best)
    y1 = max(b[1] + b[3] for b in best)
    mx, my = MARGIN_X * width, MARGIN_Y * (y1 - y0)
    bx0, by0 = max(0, round(x0 - mx)), max(0, round(y0 - my))
    bx1, by1 = min(width, round(x1 + mx)), min(height, round(y1 + my))
    return Band(x=bx0, y=by0, width=bx1 - bx0, height=by1 - by0, source="ocr_lines")
