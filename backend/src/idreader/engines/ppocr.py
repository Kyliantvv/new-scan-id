"""Moteur OCR n° 2 : PP-OCRv6 (reconnaissance seule), via ONNX Runtime.

Pour la MRZ, on n'utilise que le modèle de reconnaissance, ligne par ligne, sur les lignes
découpées par le pipeline (03_PIPELINE_ANALYSE.md § E.2). L'image passée à `recognize`
est donc traitée comme **une seule ligne de texte**. La détection (zone visuelle) viendra
en phase 2.

ONNX Runtime est appelé directement (pas RapidOCR) : aucun téléchargement implicite,
fichiers de modèles explicites, fournisseur CPU imposé. Prétraitement et décodage
reprennent ceux de PaddleOCR (`RecResizeImg` hauteur 48, normalisation (x/255 − 0,5)/0,5,
canaux BGR ; `CTCLabelDecode`).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import numpy.typing as npt
import yaml

from idreader.core.config import Settings
from idreader.engines.base import ImageArray, ImageInput, OcrLevel, Quad, Roi, TextLine
from idreader.engines.tesseract import crop, load_array

MODEL_DIR = "ppocr/v6_small_rec"
INPUT_HEIGHT = 48
MIN_WIDTH = 320
MAX_WIDTH = 3200  # forme dynamique maximale annoncée par le modèle

FloatArray = npt.NDArray[np.float32]


class PpOcrError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def load_dictionary(config: Path) -> list[str]:
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    chars = data.get("PostProcess", {}).get("character_dict") if isinstance(data, dict) else None
    if not isinstance(chars, list) or not chars:
        raise PpOcrError("PPOCR_DICT_MISSING")
    return [str(c) for c in chars]


def preprocess(line: ImageArray) -> FloatArray:
    """Ligne RGB ou grise → tenseur 1×3×48×L (BGR, normalisé dans [−1, 1])."""
    bgr = (
        cv2.cvtColor(line, cv2.COLOR_GRAY2BGR)
        if line.ndim == 2
        else cv2.cvtColor(line, cv2.COLOR_RGB2BGR)
    )
    h, w = bgr.shape[:2]
    target_w = min(MAX_WIDTH, max(1, math.ceil(INPUT_HEIGHT * w / max(h, 1))))
    resized = cv2.resize(bgr, (target_w, INPUT_HEIGHT), interpolation=cv2.INTER_LINEAR)
    tensor = resized.astype(np.float32).transpose(2, 0, 1) / 255.0
    tensor = (tensor - 0.5) / 0.5
    padded = np.zeros((3, INPUT_HEIGHT, max(MIN_WIDTH, target_w)), dtype=np.float32)
    padded[:, :, :target_w] = tensor
    return padded[np.newaxis]


def ctc_decode(probs: FloatArray, dictionary: list[str]) -> tuple[str, float]:
    """Sortie T×C → (texte, confiance moyenne). Classe 0 = blanc ; dernière classe éventuelle
    = espace (PaddleOCR `use_space_char`)."""
    if probs.ndim != 2:
        raise PpOcrError("PPOCR_BAD_OUTPUT")
    if probs.min() < 0 or probs.max() > 1.0001:  # logits : softmax
        e = np.exp(probs - probs.max(axis=1, keepdims=True))
        probs = (e / e.sum(axis=1, keepdims=True)).astype(np.float32)
    idx = probs.argmax(axis=1)
    best = probs.max(axis=1)
    chars: list[str] = []
    confs: list[float] = []
    previous = -1
    for t, i in enumerate(idx):
        i = int(i)
        if i != previous and i != 0:
            if 1 <= i <= len(dictionary):
                chars.append(dictionary[i - 1])
            elif i == len(dictionary) + 1:
                chars.append(" ")
            confs.append(float(best[t]))
        previous = i
    return "".join(chars), (sum(confs) / len(confs) if confs else 0.0)


@dataclass
class PpOcrRecEngine:
    """Reconnaissance PP-OCRv6 sur une ligne. Ignore `languages`, `level`."""

    model_path: Path
    config_path: Path
    name: str = "ppocr"
    _session: Any = field(default=None, init=False, repr=False)
    _dictionary: list[str] = field(default_factory=list, init=False, repr=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> PpOcrRecEngine | None:
        base = settings.models_dir / MODEL_DIR
        model, config = base / "inference.onnx", base / "inference.yml"
        if not model.is_file() or not config.is_file():
            return None
        return cls(model_path=model, config_path=config)

    def _load(self) -> None:
        if self._session is not None:
            return
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.log_severity_level = 3
        self._session = ort.InferenceSession(
            str(self.model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._dictionary = load_dictionary(self.config_path)

    def recognize_line(self, line: ImageArray) -> tuple[str, float]:
        self._load()
        tensor = preprocess(line)
        name = self._session.get_inputs()[0].name
        output = self._session.run(None, {name: tensor})[0]
        return ctc_decode(np.asarray(output[0], dtype=np.float32), self._dictionary)

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
        if array.size == 0:
            return []
        text, conf = self.recognize_line(array)
        h, w = array.shape[:2]
        quad = Quad(points=((ox, oy), (ox + w, oy), (ox + w, oy + h), (ox, oy + h)))
        return [
            TextLine(text=text, confidence=min(max(conf, 0.0), 1.0), quad=quad, engine=self.name)
        ]
