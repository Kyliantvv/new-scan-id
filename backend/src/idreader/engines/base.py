"""Interface commune des moteurs OCR. Le pipeline ne connaît que ces types.

Coordonnées : pixels, origine en haut à gauche, dans le repère de l'image passée au moteur
(CLAUDE.md § Conventions).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict, Field

OcrLevel = Literal["fast", "accurate"]
# Fichier image, ou tableau RGB / niveaux de gris 8 bits (prétraitements de la bande MRZ).
ImageArray = npt.NDArray[np.uint8]
ImageInput = Path | ImageArray
Point = tuple[float, float]


class Quad(BaseModel):
    """Quadrilatère : haut-gauche, haut-droite, bas-droite, bas-gauche."""

    model_config = ConfigDict(frozen=True)

    points: tuple[Point, Point, Point, Point]

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        """Rectangle englobant (x, y, largeur, hauteur)."""
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


class Roi(BaseModel):
    """Zone d'intérêt en pixels, origine en haut à gauche."""

    model_config = ConfigDict(frozen=True)

    x: float = Field(ge=0)
    y: float = Field(ge=0)
    width: float = Field(gt=0)
    height: float = Field(gt=0)

    def as_list(self) -> list[float]:
        return [self.x, self.y, self.width, self.height]


class Candidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    confidence: float = Field(ge=0, le=1)


class TextLine(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    confidence: float = Field(ge=0, le=1)
    quad: Quad
    candidates: tuple[Candidate, ...] = ()
    engine: str


class OcrEngine(Protocol):
    """Tout moteur OCR (Vision, PP-OCR, Tesseract…) implémente cette interface."""

    name: str

    def recognize(
        self,
        image: ImageInput,
        *,
        roi: Roi | None = None,
        languages: Sequence[str] = (),
        level: OcrLevel = "accurate",
        top_candidates: int = 1,
    ) -> list[TextLine]: ...
