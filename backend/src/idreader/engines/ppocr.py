"""Moteur OCR n° 2 : PP-OCRv6, via ONNX Runtime.

- MRZ (`PpOcrRecEngine`) : reconnaissance seule (modèle small), ligne par ligne sur les
  lignes découpées par le pipeline (03_PIPELINE_ANALYSE.md § E.2). L'image passée à
  `recognize` est traitée comme **une seule ligne de texte**.
- Zone visuelle (`PpOcrTextEngine`) : détection DB des lignes puis reconnaissance (modèles
  medium, § G), lignes renvoyées dans le repère de l'image.

ONNX Runtime est appelé directement (pas RapidOCR) : aucun téléchargement implicite,
fichiers de modèles explicites, fournisseur CPU imposé. Prétraitements et décodages
reprennent ceux de PaddleOCR (`RecResizeImg` hauteur 48, normalisation (x/255 − 0,5)/0,5,
canaux BGR, `CTCLabelDecode` ; `DetResizeForTest`, normalisation ImageNet, `DBPostProcess`).
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
from idreader.engines.base import ImageArray, ImageInput, OcrLevel, Point, Quad, Roi, TextLine
from idreader.engines.tesseract import crop, load_array

MODEL_DIR = "ppocr/v6_small_rec"
VIZ_DET_DIR = "ppocr/v6_medium_det"
VIZ_REC_DIR = "ppocr/v6_medium_rec"
INPUT_HEIGHT = 48
MIN_WIDTH = 320
MAX_WIDTH = 3200  # forme dynamique maximale annoncée par le modèle

# Détection DB : valeurs de PostProcess de inference.yml (PP-OCRv6 medium det)
DET_THRESH = 0.2
DET_BOX_THRESH = 0.45
DET_UNCLIP_RATIO = 1.4
DET_MAX_CANDIDATES = 3000
DET_MAX_SIDE = 1600  # côté long de l'image de détection (multiple de 32), compromis CPU
DET_MIN_SIDE_PX = 3
IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], np.float32)

FloatArray = npt.NDArray[np.float32]


class PpOcrError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _session(path: Path) -> Any:
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.log_severity_level = 3
    return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])


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
        self._session = _session(self.model_path)
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


# --- Détection (zone visuelle) ----------------------------------------------------------


def det_preprocess(
    image: ImageArray, max_side: int = DET_MAX_SIDE
) -> tuple[FloatArray, float, float]:
    """RGB → tenseur 1×3×H×W (BGR, normalisation ImageNet), côtés multiples de 32.
    Renvoie aussi les facteurs d'échelle (x, y) vers l'image d'origine."""
    rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB) if image.ndim == 2 else image
    h, w = rgb.shape[:2]
    scale = min(1.0, max_side / max(h, w))
    nh = max(32, round(h * scale / 32) * 32)
    nw = max(32, round(w * scale / 32) * 32)
    resized = cv2.resize(rgb, (nw, nh), interpolation=cv2.INTER_LINEAR)
    bgr = resized[:, :, ::-1].astype(np.float32) / 255.0
    norm = (bgr - IMAGENET_MEAN) / IMAGENET_STD
    return norm.transpose(2, 0, 1)[np.newaxis].astype(np.float32), w / nw, h / nh


def order_box(points: FloatArray) -> FloatArray:
    """Coins dans l'ordre haut-gauche, haut-droite, bas-droite, bas-gauche."""
    s = points.sum(axis=1)
    d = np.diff(points, axis=1).ravel()
    return np.array(
        [points[np.argmin(s)], points[np.argmin(d)], points[np.argmax(s)], points[np.argmax(d)]],
        np.float32,
    )


def db_postprocess(
    prob: FloatArray,
    scale_x: float,
    scale_y: float,
    *,
    thresh: float = DET_THRESH,
    box_thresh: float = DET_BOX_THRESH,
    unclip_ratio: float = DET_UNCLIP_RATIO,
) -> list[tuple[FloatArray, float]]:
    """Carte de probabilité H×W → [(quadrilatère 4×2 dans l'image d'origine, score)].

    Reprend `DBPostProcess` de PaddleOCR : seuil, contours, rectangle d'aire minimale,
    score moyen dans le contour, dilatation (« unclip ») d'une distance aire × ratio /
    périmètre.
    """
    bitmap = (prob > thresh).astype(np.uint8)
    contours, _ = cv2.findContours(bitmap, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    out: list[tuple[FloatArray, float]] = []
    h, w = prob.shape
    for contour in contours[:DET_MAX_CANDIDATES]:
        (cx, cy), (rw, rh), angle = cv2.minAreaRect(contour)
        if min(rw, rh) < DET_MIN_SIDE_PX:
            continue
        mask = np.zeros_like(bitmap)
        cv2.fillPoly(mask, [contour.reshape(-1, 2)], 1)
        score = float(cv2.mean(prob, mask=mask)[0])
        if score < box_thresh:
            continue
        distance = (rw * rh) * unclip_ratio / max(2 * (rw + rh), 1e-6)
        box = np.asarray(
            cv2.boxPoints(((cx, cy), (rw + 2 * distance, rh + 2 * distance), angle)), np.float32
        )
        box[:, 0] = np.clip(box[:, 0], 0, w - 1) * scale_x
        box[:, 1] = np.clip(box[:, 1], 0, h - 1) * scale_y
        out.append((order_box(box), score))
    return out


def crop_quad(image: ImageArray, quad: FloatArray) -> ImageArray:
    """Redresse le quadrilatère en rectangle (vertical très allongé → tourné de 90°)."""
    width = int(max(np.linalg.norm(quad[0] - quad[1]), np.linalg.norm(quad[2] - quad[3])))
    height = int(max(np.linalg.norm(quad[0] - quad[3]), np.linalg.norm(quad[1] - quad[2])))
    width, height = max(width, 1), max(height, 1)
    dst = np.array([[0, 0], [width, 0], [width, height], [0, height]], np.float32)
    matrix = cv2.getPerspectiveTransform(quad.astype(np.float32), dst)
    out = cv2.warpPerspective(
        image, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    if height / width >= 1.5:
        out = np.rot90(out)
    return np.ascontiguousarray(out, dtype=np.uint8)


def reading_order(lines: list[TextLine]) -> list[TextLine]:
    """Haut → bas par bandes de la hauteur des lignes, puis gauche → droite."""
    if not lines:
        return []
    median_h = float(np.median([line.quad.bbox[3] for line in lines])) or 1.0

    def key(line: TextLine) -> tuple[int, float]:
        x, y, _, h = line.quad.bbox
        return (int((y + h / 2) / (0.8 * median_h)), x)

    return sorted(lines, key=key)


@dataclass
class PpOcrTextEngine:
    """Zone visuelle : détection DB + reconnaissance, PP-OCRv6 medium. Ignore `languages`
    (modèle multilingue latin + CJK ; le cyrillique, l'arabe… demanderont les modèles v5)."""

    det_path: Path
    rec_path: Path
    rec_config_path: Path
    name: str = "ppocr"
    max_side: int = DET_MAX_SIDE
    _det: Any = field(default=None, init=False, repr=False)
    _rec: PpOcrRecEngine | None = field(default=None, init=False, repr=False)

    @classmethod
    def from_settings(cls, settings: Settings) -> PpOcrTextEngine | None:
        det = settings.models_dir / VIZ_DET_DIR / "inference.onnx"
        rec = settings.models_dir / VIZ_REC_DIR / "inference.onnx"
        cfg = settings.models_dir / VIZ_REC_DIR / "inference.yml"
        if not (det.is_file() and rec.is_file() and cfg.is_file()):
            return None
        return cls(det_path=det, rec_path=rec, rec_config_path=cfg)

    def detect(self, image: ImageArray) -> list[tuple[FloatArray, float]]:
        if self._det is None:
            self._det = _session(self.det_path)
        tensor, sx, sy = det_preprocess(image, self.max_side)
        name = self._det.get_inputs()[0].name
        prob = np.asarray(self._det.run(None, {name: tensor})[0], np.float32)[0, 0]
        return db_postprocess(prob, sx, sy)

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
        if array.ndim == 2:
            array = np.ascontiguousarray(cv2.cvtColor(array, cv2.COLOR_GRAY2RGB), np.uint8)
        if self._rec is None:
            self._rec = PpOcrRecEngine(self.rec_path, self.rec_config_path, name=self.name)
        lines: list[TextLine] = []
        for quad, _ in self.detect(array):
            text, conf = self._rec.recognize_line(crop_quad(array, quad))
            text = " ".join(text.split())
            if not text:
                continue
            p = [(float(x) + ox, float(y) + oy) for x, y in quad]
            points: tuple[Point, Point, Point, Point] = (p[0], p[1], p[2], p[3])
            lines.append(
                TextLine(
                    text=text,
                    confidence=min(max(conf, 0.0), 1.0),
                    quad=Quad(points=points),
                    engine=self.name,
                )
            )
        return reading_order(lines)
