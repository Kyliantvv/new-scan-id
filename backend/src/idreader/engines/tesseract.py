"""Moteur OCR n° 3 (MRZ seulement) : Tesseract 5 + `mrz.traineddata`, en sous-processus.

Commande (03_PIPELINE_ANALYSE.md § E.2) :
    tesseract stdin stdout -l mrz --oem 1 --psm 6
        -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789< tsv

L'image passe par l'entrée standard (PNG) : rien n'est écrit sur disque.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from idreader.core.config import Settings
from idreader.engines.base import ImageArray, ImageInput, OcrLevel, Quad, Roi, TextLine

WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
MODEL = "mrz"
TIMEOUT_SECONDS = 30


class TesseractError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def load_array(image: ImageInput) -> ImageArray:
    if isinstance(image, Path):
        data = cv2.imread(str(image), cv2.IMREAD_COLOR)
        if data is None:
            raise TesseractError("IMAGE_UNREADABLE")
        return np.ascontiguousarray(cv2.cvtColor(data, cv2.COLOR_BGR2RGB), dtype=np.uint8)
    return image


def crop(image: ImageArray, roi: Roi | None) -> tuple[ImageArray, float, float]:
    if roi is None:
        return image, 0.0, 0.0
    x, y = round(roi.x), round(roi.y)
    w, h = round(roi.width), round(roi.height)
    return image[y : y + h, x : x + w], float(x), float(y)


def parse_tsv(tsv: str, *, offset_x: float = 0.0, offset_y: float = 0.0) -> list[TextLine]:
    """Sortie TSV de Tesseract → une `TextLine` par ligne (mots regroupés)."""
    lines: dict[tuple[int, int, int], list[tuple[int, int, int, int, float, str]]] = {}
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5":  # niveau 5 = mot
            continue
        text = cols[11].strip()
        if not text:
            continue
        key = (int(cols[2]), int(cols[3]), int(cols[4]))
        left, top, width, height = (int(c) for c in cols[6:10])
        lines.setdefault(key, []).append((left, top, width, height, float(cols[10]), text))
    out: list[TextLine] = []
    for key in sorted(lines):
        words = lines[key]
        x0 = min(w[0] for w in words) + offset_x
        y0 = min(w[1] for w in words) + offset_y
        x1 = max(w[0] + w[2] for w in words) + offset_x
        y1 = max(w[1] + w[3] for w in words) + offset_y
        confs = [w[4] for w in words if w[4] >= 0]
        conf = (sum(confs) / len(confs) / 100) if confs else 0.0
        out.append(
            TextLine(
                text="".join(w[5] for w in words),
                confidence=min(max(conf, 0.0), 1.0),
                quad=Quad(points=((x0, y0), (x1, y0), (x1, y1), (x0, y1))),
                engine="tesseract",
            )
        )
    return out


@dataclass
class TesseractMrzEngine:
    """Tesseract spécialisé MRZ (police OCR-B). Ignore `languages` et `level`."""

    command: str
    tessdata_dir: Path
    timeout_seconds: float = TIMEOUT_SECONDS
    launcher: tuple[str, ...] = ()  # préfixe de commande (tests : interpréteur Python)
    name: str = "tesseract"

    @classmethod
    def from_settings(cls, settings: Settings) -> TesseractMrzEngine | None:
        """None si le binaire ou `mrz.traineddata` manque (le pipeline s'en passe)."""
        command = shutil.which(settings.tesseract_cmd)
        tessdata = settings.models_dir / "tesseract"
        if command is None or not (tessdata / f"{MODEL}.traineddata").is_file():
            return None
        return cls(command=command, tessdata_dir=tessdata)

    def argv(self) -> list[str]:
        return [
            *self.launcher,
            self.command,
            "stdin",
            "stdout",
            "--tessdata-dir",
            str(self.tessdata_dir),
            "-l",
            MODEL,
            "--oem",
            "1",
            "--psm",
            "6",
            "-c",
            f"tessedit_char_whitelist={WHITELIST}",
            "tsv",
        ]

    def recognize(
        self,
        image: ImageInput,
        *,
        roi: Roi | None = None,
        languages: Sequence[str] = (),
        level: OcrLevel = "accurate",
        top_candidates: int = 1,
    ) -> list[TextLine]:
        array, ox, oy = crop(load_array(image), roi)
        bgr = array if array.ndim == 2 else cv2.cvtColor(array, cv2.COLOR_RGB2BGR)
        ok, png = cv2.imencode(".png", bgr)
        if not ok:
            raise TesseractError("IMAGE_ENCODE_FAILED")
        try:
            proc = subprocess.run(  # noqa: S603 — argv fixe, binaire résolu par shutil.which
                self.argv(),
                input=png.tobytes(),
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise TesseractError("TESSERACT_TIMEOUT") from None
        except OSError:
            raise TesseractError("TESSERACT_NOT_FOUND") from None
        if proc.returncode != 0:
            raise TesseractError("TESSERACT_FAILED")  # stderr non journalisé
        return parse_tsv(proc.stdout.decode("utf-8", "replace"), offset_x=ox, offset_y=oy)
