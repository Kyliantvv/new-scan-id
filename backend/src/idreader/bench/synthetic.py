"""Jeu synthétique du banc : MRZ valides, rendues en OCR-B sur des fonds variés, dégradées.

Identités entièrement inventées (syllabes aléatoires) : aucune donnée réelle.
Chaque cas = un recadrage déjà redressé et orienté (entrée de l'étape E) + sa vérité.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

import cv2
import numpy as np
import numpy.typing as npt
from PIL import Image, ImageDraw, ImageFont

from idreader.pipeline.ingest import RGBImage
from idreader.pipeline.mrz.formats import MrzFormat
from idreader.pipeline.mrz.synth import MrzData, build

QUALITIES = ("good", "medium", "poor")
FONT_FILE = Path("fonts") / "ocrb10.otf"  # sous models/ (scripts/models.toml)
OCRB_ADVANCE = 0.725  # avance d'un caractère OCR-B / taille de police (ocrb10.otf)

# Taille du recadrage canonique (≈ 500 dpi) et part de la largeur occupée par la MRZ.
LAYOUT: dict[MrzFormat, tuple[int, int, float]] = {
    MrzFormat.TD1: (1712, 1080, 0.88),
    MrzFormat.FRA_ID: (1712, 1080, 0.90),
    MrzFormat.TD2: (2100, 1480, 0.88),
    MrzFormat.MRVB: (2100, 1480, 0.88),
    MrzFormat.TD3: (2500, 1760, 0.88),
    MrzFormat.MRVA: (2500, 1760, 0.88),
}
FORMAT_WEIGHTS = {
    MrzFormat.TD3: 40,
    MrzFormat.TD1: 30,
    MrzFormat.TD2: 8,
    MrzFormat.MRVA: 7,
    MrzFormat.MRVB: 5,
    MrzFormat.FRA_ID: 10,
}
STATES = (
    "FRA", "D", "ESP", "ITA", "BEL", "NLD", "PRT", "GBR", "IRL", "CHE", "POL", "ROU", "USA",
    "CAN", "MAR", "DZA", "TUN", "SEN", "CIV", "CHN", "IND", "BRA", "TUR", "UKR", "SWE",
)  # fmt: skip
CONSONANTS = "BCDFGHJKLMNPRSTVWXZ"
VOWELS = "AEIOUY"
ALNUM = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


@dataclass
class Case:
    case_id: str
    format: str
    quality: str
    lines: list[str]
    image: str
    degradations: dict[str, float] = field(default_factory=dict)


def _name(rng: random.Random, syllables: tuple[int, int]) -> str:
    n = rng.randint(*syllables)
    return "".join(rng.choice(CONSONANTS) + rng.choice(VOWELS) for _ in range(n))


def _random_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randrange((end - start).days))


def random_identity(rng: random.Random, fmt: MrzFormat) -> MrzData:
    state = rng.choice(STATES)
    surname = " ".join(_name(rng, (2, 4)) for _ in range(rng.choice((1, 1, 1, 2))))
    if rng.random() < 0.05:
        surname = _name(rng, (9, 12))  # nom long : troncature
    given = " ".join(_name(rng, (2, 3)) for _ in range(rng.choice((1, 1, 2, 3))))
    born = _random_date(rng, date(1940, 1, 1), date(2015, 12, 31))
    expires = _random_date(rng, date(2025, 1, 1), date(2036, 12, 31))
    code = {
        MrzFormat.TD3: "P",
        MrzFormat.MRVA: "V",
        MrzFormat.MRVB: "V",
        MrzFormat.FRA_ID: "ID",
    }.get(fmt, rng.choice(("I", "ID", "IR", "A", "C")))
    if fmt is MrzFormat.FRA_ID:
        state = "FRA"
        number = "".join(rng.choice("0123456789") for _ in range(12))
    elif state == "FRA" and fmt is MrzFormat.TD3:
        number = f"{rng.randint(0, 99):02d}{rng.choice(CONSONANTS)}{rng.choice(CONSONANTS)}"
        number += f"{rng.randint(0, 99999):05d}"
    else:
        length = 9
        if fmt in (MrzFormat.TD1, MrzFormat.TD2) and rng.random() < 0.1:
            length = rng.randint(10, 12 if fmt is MrzFormat.TD1 else 11)
        number = "".join(rng.choice(ALNUM) for _ in range(length))
    optional = ""
    if fmt is MrzFormat.TD3 and rng.random() < 0.5:
        optional = "".join(rng.choice(ALNUM) for _ in range(rng.randint(6, 14)))
    elif fmt in (MrzFormat.TD1,) and rng.random() < 0.3 and len(number) <= 9:
        optional = "".join(rng.choice(ALNUM) for _ in range(rng.randint(3, 15)))
    return MrzData(
        document_code=code,
        issuing_state=state,
        surname=surname,
        given_names=given,
        document_number=number,
        nationality=state if rng.random() < 0.9 else rng.choice(STATES),
        date_of_birth=born.strftime("%y%m%d"),
        sex=rng.choice("MMFFFX<") if fmt is not MrzFormat.FRA_ID else rng.choice("MF"),
        date_of_expiry=expires.strftime("%y%m%d") if fmt is not MrzFormat.FRA_ID else "",
        optional_data=optional,
        admin_code=f"{rng.randint(1, 95):02d}{rng.randint(0, 9999):04d}"
        if fmt is MrzFormat.FRA_ID
        else "",
    )


def _background(rng: random.Random, w: int, h: int) -> RGBImage:
    base = np.array(
        [rng.randint(200, 245), rng.randint(200, 245), rng.randint(195, 240)], np.float32
    )
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.zeros((h, w, 3), np.float32) + base
    # dégradé + guillochis clair
    img += (xs / w)[..., None] * rng.uniform(-15, 15)
    for _ in range(rng.randint(2, 5)):
        f, phase, amp = rng.uniform(0.005, 0.03), rng.uniform(0, 6.3), rng.uniform(3, 9)
        img += (np.sin(xs * f + np.sin(ys * f * 0.7) * 3 + phase) * amp)[..., None]
    img8 = np.clip(img, 0, 255).astype(np.uint8)
    # zone visuelle factice : « photo » et lignes de texte
    cv2.rectangle(img8, (w // 25, h // 6), (w * 3 // 10, h * 11 // 16), (150, 140, 135), -1)
    for i in range(rng.randint(5, 8)):
        y = h // 6 + 40 + i * h // 14
        text = " ".join(_name(rng, (1, 3)) for _ in range(rng.randint(1, 3)))
        cv2.putText(
            img8,
            text,
            (w * 7 // 20, y),
            cv2.FONT_HERSHEY_DUPLEX,
            h / 900,
            (45, 45, 60),
            2,
            cv2.LINE_AA,
        )
    return np.asarray(img8, dtype=np.uint8)


def render(lines: list[str], fmt: MrzFormat, font_path: Path, rng: random.Random) -> RGBImage:
    w, h, ratio = LAYOUT[fmt]
    page = Image.fromarray(_background(rng, w, h))
    size = round(ratio * w / (len(lines[0]) * OCRB_ADVANCE))
    font = ImageFont.truetype(str(font_path), size)
    draw = ImageDraw.Draw(page)
    pitch = round(size * rng.uniform(1.35, 1.55))
    x = round((w - font.getlength(lines[0])) / 2 + rng.uniform(-0.01, 0.01) * w)
    bottom = h - round(h * rng.uniform(0.05, 0.08))
    ink = rng.randint(10, 50)
    for i, line in enumerate(reversed(lines)):
        draw.text((x, bottom - (i + 1) * pitch), line, font=font, fill=(ink, ink, ink + 5))
    return np.asarray(page, dtype=np.uint8).copy()


@dataclass(frozen=True)
class Degradation:
    """Plages de dégradation d'un niveau de qualité."""

    blur: tuple[float, float]
    noise: tuple[float, float]
    contrast: tuple[float, float]
    jpeg: tuple[int, int]
    rotation: float  # degrés, ±
    perspective: float  # part de la largeur, ±
    glare: float  # probabilité d'un reflet


DEGRADATIONS = {
    "good": Degradation((0, 0.6), (0, 3), (0.95, 1.0), (88, 95), 0.3, 0.0, 0.0),
    "medium": Degradation((0.6, 1.4), (3, 8), (0.75, 1.0), (70, 85), 1.0, 0.006, 0.2),
    "poor": Degradation((1.4, 2.4), (8, 14), (0.5, 0.8), (45, 65), 2.0, 0.015, 0.5),
}


def degrade(
    img: RGBImage, quality: str, rng: random.Random
) -> tuple[RGBImage, dict[str, float], int]:
    """Flou, bruit, contraste, reflet, perspective, rotation ; renvoie aussi la qualité JPEG."""
    p = DEGRADATIONS[quality]
    h, w = img.shape[:2]
    d: dict[str, float] = {}
    d["contrast"] = c = rng.uniform(*p.contrast)
    out: npt.NDArray[np.float32] = (img.astype(np.float32) - 128) * c + 128
    if rng.random() < p.glare:
        cx, cy = rng.uniform(0.2, 0.8) * w, rng.uniform(0.3, 0.95) * h
        r = rng.uniform(0.06, 0.15) * w
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
        mask = np.clip(1 - np.hypot(xs - cx, (ys - cy) * 1.6) / r, 0, 1) ** 1.5
        out = out + mask[..., None] * 160
        d["glare"] = 1.0
    d["blur"] = sigma = rng.uniform(*p.blur)
    if sigma > 0.05:
        out = np.asarray(cv2.GaussianBlur(out, (0, 0), sigma), np.float32)
    d["noise"] = n = rng.uniform(*p.noise)
    noise = np.random.default_rng(rng.randrange(2**32)).normal(0, n, out.shape)
    img8 = np.clip(out + noise, 0, 255).astype(np.uint8)
    angle = rng.uniform(-p.rotation, p.rotation)
    jitter = p.perspective * w
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], np.float32)
    shift = [[rng.uniform(-jitter, jitter), rng.uniform(-jitter, jitter)] for _ in range(4)]
    dst = src + np.array(shift, np.float32)
    rot = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    dst = np.asarray(cv2.transform(dst[None], rot)[0], np.float32)
    matrix = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(
        img8, matrix, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    )
    d["rotation"] = angle
    jpeg = rng.randint(*p.jpeg)
    d["jpeg"] = float(jpeg)
    return np.ascontiguousarray(warped, dtype=np.uint8), d, jpeg


def generate(
    out_dir: Path,
    *,
    count: int,
    seed: int,
    font_path: Path,
    qualities: tuple[str, ...] = QUALITIES,
    today: date | None = None,
) -> list[Case]:
    """Génère `count` cas dans `out_dir` (images JPEG + `truth.json`). Reproductible par `seed`."""
    if not font_path.is_file():
        raise FileNotFoundError("police OCR-B absente : lancer scripts/fetch_models.py")
    rng = random.Random(seed)  # noqa: S311 (données synthétiques, pas de cryptographie)
    out_dir.mkdir(parents=True, exist_ok=True)
    formats = list(FORMAT_WEIGHTS)
    weights = [FORMAT_WEIGHTS[f] for f in formats]
    cases: list[Case] = []
    for i in range(count):
        fmt = rng.choices(formats, weights)[0]
        quality = qualities[i % len(qualities)]
        lines = build(fmt, random_identity(rng, fmt))
        img, degradations, jpeg = degrade(render(lines, fmt, font_path, rng), quality, rng)
        name = f"case_{i:05d}.jpg"
        cv2.imwrite(
            str(out_dir / name),
            cv2.cvtColor(img, cv2.COLOR_RGB2BGR),
            [cv2.IMWRITE_JPEG_QUALITY, jpeg],
        )
        cases.append(Case(f"syn-{seed}-{i:05d}", fmt.value, quality, lines, name, degradations))
    # Date de référence : rend le décodage reproductible (siècle, âge, expiration).
    ref = (today or date.today()).isoformat()
    truth = {"seed": seed, "count": count, "today": ref, "cases": [asdict(c) for c in cases]}
    (out_dir / "truth.json").write_text(json.dumps(truth, indent=1), encoding="utf-8")
    return cases


def load_cases(out_dir: Path) -> list[Case]:
    data = json.loads((out_dir / "truth.json").read_text(encoding="utf-8"))
    return [Case(**c) for c in data["cases"]]
