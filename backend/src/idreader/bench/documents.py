"""Documents synthétiques complets du pays fictif UTO (Utopie, État des spécimens ICAO).

Chaque cas est un passeport (page de données TD3) ou une carte d'identité (recto avec la
zone visuelle, verso avec l'adresse, la délivrance et la MRZ TD1). Zone visuelle : libellés
bilingues tirés de `reference/viz_labels.yaml`, valeurs accentuées en Roboto ; MRZ valide en
OCR-B, translittérée selon ICAO 9303 partie 3. Images posées sur un fond de scanner puis
dégradées. Identités inventées (syllabes aléatoires) : aucune donnée réelle.

Mises en page :
- `separate` : un fichier par face (passeport : un fichier) ;
- `a4` : recto et verso côte à côte sur une page A4 (cartes seulement).
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from idreader.bench.synthetic import CONSONANTS, VOWELS, degrade
from idreader.pipeline.ingest import RGBImage
from idreader.pipeline.mrz.formats import MrzFormat
from idreader.pipeline.mrz.names import TRANSLIT
from idreader.pipeline.mrz.synth import MrzData, build
from idreader.pipeline.reference import month_names, viz_labels

STATE = "UTO"
QUALITIES = ("good", "medium", "poor")
VIZ_FONT = Path("fonts") / "Roboto.ttf"
MRZ_FONT = Path("fonts") / "ocrb10.otf"
OCRB_ADVANCE = 0.725
ACCENTS = {
    "A": "ÄÅÀÂ", "E": "ÉÈÊË", "I": "ÏÎ", "O": "ÖÔØ", "U": "ÜÛÙ", "C": "Ç", "N": "Ñ", "S": "ß",
}  # fmt: skip
LANG_PAIRS = (("fr", "en"), ("fr", "en"), ("fr", "en"), ("es", "en"), ("de", "en"), ("it", "en"))
PLACES = ("UTOPIA", "NOUVELLE-ÉTOILE", "SAINT-ÉLOI-DES-PRÉS", "BÜRGENSTADT", "ÅLBORG-SUR-MER",
          "MÖNTREUX", "PORT-JOLI", "VILLEFRANCHE-LA-GRÂCE", "SANTA LUCÍA", "KØBENHOLM")  # fmt: skip
AUTHORITIES = ("PRÉFECTURE D'UTOPIA", "MINISTÈRE DE L'INTÉRIEUR", "CONSULAT GÉNÉRAL",
               "AMBASSADE D'UTOPIE")  # fmt: skip


@dataclass
class DocCase:
    case_id: str
    kind: str  # passport | id_card
    quality: str
    layout: str  # separate | a4
    images: list[str]  # fichiers, dans l'ordre d'upload
    fields: dict[str, str | None]  # valeurs attendues après analyse
    mrz_lines: list[str]
    degradations: list[dict[str, float]] = field(default_factory=list)


# --- Identités ----------------------------------------------------------------------------


def _accented_name(rng: random.Random, syllables: tuple[int, int], accent_rate: float) -> str:
    out = []
    for _ in range(rng.randint(*syllables)):
        c, v = rng.choice(CONSONANTS), rng.choice(VOWELS.replace("Y", ""))
        if rng.random() < accent_rate and v in ACCENTS:
            v = rng.choice(ACCENTS[v])
        out.append(c + v)
    return "".join(out)


def to_mrz_name(text: str) -> str:
    """Forme MRZ préférée d'un nom (ICAO 9303 partie 3 § 6) : première transcription admise."""
    out = []
    for ch in text.upper():
        if ch in TRANSLIT:
            out.append(TRANSLIT[ch][0])
        elif ch == "ß":
            out.append("SS")
        elif ch in "-' ":
            out.append(" ")
        elif ch == ",":
            continue
        else:
            import unicodedata

            base = unicodedata.normalize("NFKD", ch)
            out.append("".join(c for c in base if "A" <= c <= "Z"))
    return " ".join("".join(out).split())


def _random_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randrange((end - start).days))


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def random_person(rng: random.Random, today: date) -> dict[str, str]:
    surname = _accented_name(rng, (2, 4), 0.35)
    if rng.random() < 0.25:
        surname += "-" + _accented_name(rng, (2, 3), 0.35)
    given = [_accented_name(rng, (2, 3), 0.3).capitalize() for _ in range(rng.choice((1, 1, 2)))]
    born = _random_date(rng, date(1945, 1, 1), date(2004, 12, 31))
    validity = rng.choice((10, 10, 5))
    issued = _random_date(
        rng, today - timedelta(days=365 * (validity - 1)), today - timedelta(days=30)
    )
    expires = _add_years(issued, validity) - timedelta(days=1)
    return {
        "surname": surname.upper(),
        "given_names": (", " if rng.random() < 0.5 else " ").join(given).upper(),
        "sex": rng.choice("MF"),
        "date_of_birth": born.isoformat(),
        "place_of_birth": rng.choice(PLACES),
        "date_of_issue": issued.isoformat(),
        "date_of_expiry": expires.isoformat(),
        "authority": rng.choice(AUTHORITIES),
        "usage_name": ("ÉP. " + _accented_name(rng, (2, 3), 0.3).upper())
        if rng.random() < 0.15
        else "",
    }


# --- Rendu --------------------------------------------------------------------------------


def _label(field_name: str, langs: tuple[str, str]) -> str:
    import yaml

    from idreader.pipeline.reference import REFERENCE_DIR

    data = yaml.safe_load((REFERENCE_DIR / "viz_labels.yaml").read_text(encoding="utf-8"))
    by_lang = data[field_name]
    parts = [by_lang[lang][0] for lang in langs if lang in by_lang]
    return " / ".join(dict.fromkeys(parts)) or viz_labels()[field_name][0]


def _format_date(d: date, rng: random.Random, langs: tuple[str, str]) -> str:
    style = rng.choice(("bilingual", "bilingual", "dots", "slashes"))
    if style == "dots":
        return f"{d.day:02d}.{d.month:02d}.{d.year}"
    if style == "slashes":
        return f"{d.day:02d}/{d.month:02d}/{d.year}"
    names = month_names()
    m1 = names[langs[0]][d.month][0]
    m2 = names[langs[1]][d.month][0]
    month = m1 if m1 == m2 else f"{m1}/{m2}"
    return f"{d.day:02d} {month} {d.year}"


def _background(rng: random.Random, w: int, h: int, tint: tuple[int, int, int]) -> Image.Image:
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    img = np.zeros((h, w, 3), np.float32) + np.array(tint, np.float32)
    for _ in range(rng.randint(2, 4)):
        f, phase, amp = rng.uniform(0.004, 0.02), rng.uniform(0, 6.3), rng.uniform(2, 6)
        img += (np.sin(xs * f + np.sin(ys * f * 0.6) * 3 + phase) * amp)[..., None]
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))


class _Painter:
    def __init__(self, img: Image.Image, font_dir: Path) -> None:
        self.img = img
        self.draw = ImageDraw.Draw(img)
        self.font_dir = font_dir
        self.h = img.height

    def font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        f = ImageFont.truetype(str(self.font_dir / VIZ_FONT), size)
        f.set_variation_by_axes([100, 650 if bold else 400])
        return f

    def field(self, x: int, y: int, label: str, value: str, size: int) -> int:
        self.draw.text((x, y), label, font=self.font(int(size * 0.62)), fill=(70, 70, 95))
        y2 = y + int(size * 0.78)
        self.draw.text((x, y2), value, font=self.font(size, bold=True), fill=(18, 18, 24))
        return y2 + int(size * 1.45)

    def mrz(self, lines: list[str], ratio: float, bottom_ratio: float) -> None:
        w, h = self.img.size
        size = round(ratio * w / (len(lines[0]) * OCRB_ADVANCE))
        font = ImageFont.truetype(str(self.font_dir / MRZ_FONT), size)
        pitch = round(size * 1.45)
        x = round((w - font.getlength(lines[0])) / 2)
        bottom = h - round(h * bottom_ratio)
        for i, line in enumerate(reversed(lines)):
            self.draw.text((x, bottom - (i + 1) * pitch), line, font=font, fill=(25, 25, 30))

    def photo(self, box: tuple[int, int, int, int]) -> None:
        self.draw.rectangle(box, fill=(165, 160, 158), outline=(120, 120, 120), width=3)
        x0, y0, x1, _ = box
        cx, w = (x0 + x1) // 2, x1 - x0
        self.draw.ellipse(
            (cx - w // 4, y0 + w // 6, cx + w // 4, y0 + w // 6 + w // 2), fill=(140, 135, 132)
        )


def render_passport(
    p: dict[str, str], number: str, lines: list[str], rng: random.Random, font_dir: Path
) -> RGBImage:
    w, h = 2500, 1760
    img = _background(rng, w, h, (232, 228, 238))
    paint = _Painter(img, font_dir)
    langs = rng.choice(LANG_PAIRS)
    size = 50
    paint.draw.text((60, 40), "UTOPIE / UTOPIA", font=paint.font(64, True), fill=(40, 40, 90))
    paint.draw.text(
        (1100, 50), "PASSEPORT / PASSPORT", font=paint.font(52, True), fill=(40, 40, 90)
    )
    paint.photo((70, 230, 640, 980))
    x, x2 = 760, 1650
    y = 200
    paint.field(x, y, "Type", "P", size)
    paint.field(x + 380, y, "Code", STATE, size)
    y = paint.field(x2, y, _label("document_number", langs), number, size)
    y = paint.field(x, y, _label("surname", langs), p["surname"], size)
    if p["usage_name"]:
        y = paint.field(x, y, _label("usage_name", ("fr", "fr")), p["usage_name"], size)
    y = paint.field(x, y, _label("given_names", langs), p["given_names"], size)
    y_nat = paint.field(x, y, _label("nationality", langs), "UTOPIENNE", size)
    paint.field(x2, y, _label("sex", langs), p["sex"], size)
    y = y_nat
    y_dob = paint.field(
        x,
        y,
        _label("date_of_birth", langs),
        _format_date(date.fromisoformat(p["date_of_birth"]), rng, langs),
        size,
    )
    paint.field(x2, y, _label("place_of_birth", langs), p["place_of_birth"], size)
    y = y_dob
    y_iss = paint.field(
        x,
        y,
        _label("date_of_issue", langs),
        _format_date(date.fromisoformat(p["date_of_issue"]), rng, langs),
        size,
    )
    paint.field(x2, y, _label("authority", langs), p["authority"], size)
    paint.field(
        x,
        y_iss,
        _label("date_of_expiry", langs),
        _format_date(date.fromisoformat(p["date_of_expiry"]), rng, langs),
        size,
    )
    paint.mrz(lines, 0.90, 0.06)
    return np.asarray(img, dtype=np.uint8).copy()


def render_card(
    p: dict[str, str], number: str, lines: list[str], rng: random.Random, font_dir: Path
) -> tuple[RGBImage, RGBImage]:
    w, h = 1712, 1080
    langs = rng.choice(LANG_PAIRS)
    size = 40
    # Recto
    front = _background(rng, w, h, (226, 234, 228))
    paint = _Painter(front, font_dir)
    paint.draw.text((40, 30), "RÉPUBLIQUE D'UTOPIE", font=paint.font(46, True), fill=(30, 60, 50))
    paint.draw.text(
        (40, 88),
        "CARTE NATIONALE D'IDENTITÉ / IDENTITY CARD",
        font=paint.font(36, True),
        fill=(30, 60, 50),
    )
    paint.photo((50, 200, 470, 760))
    x, x2 = 540, 1220
    y = 190
    y = paint.field(x, y, _label("surname", langs), p["surname"], size)
    if p["usage_name"]:
        y = paint.field(x, y, _label("usage_name", ("fr", "fr")), p["usage_name"], size)
    y = paint.field(x, y, _label("given_names", langs), p["given_names"], size)
    y_sex = paint.field(x, y, _label("sex", langs), p["sex"], size)
    paint.field(x + 300, y, _label("nationality", langs), STATE, size)
    y = y_sex
    y_dob = paint.field(
        x,
        y,
        _label("date_of_birth", langs),
        _format_date(date.fromisoformat(p["date_of_birth"]), rng, langs),
        size,
    )
    paint.field(x2 - 120, y, _label("place_of_birth", langs), p["place_of_birth"], size)
    y = y_dob
    paint.field(x, y, _label("document_number", langs), number, size)
    paint.field(
        x2 - 120,
        y,
        _label("date_of_expiry", langs),
        _format_date(date.fromisoformat(p["date_of_expiry"]), rng, langs),
        size,
    )
    # Verso
    back = _background(rng, w, h, (230, 230, 222))
    paint = _Painter(back, font_dir)
    y = 70
    y = paint.field(
        60, y, _label("address", langs), "12 RUE DES ÉTOILES, " + p["place_of_birth"], size
    )
    y_iss = paint.field(
        60,
        y,
        _label("date_of_issue", langs),
        _format_date(date.fromisoformat(p["date_of_issue"]), rng, langs),
        size,
    )
    paint.field(900, y, _label("authority", langs), p["authority"], size)
    paint.mrz(lines, 0.88, 0.06)
    del y_iss
    return np.asarray(front, dtype=np.uint8).copy(), np.asarray(back, dtype=np.uint8).copy()


def on_scanner(doc: RGBImage, rng: random.Random, margin: float = 0.08) -> RGBImage:
    """Pose le document sur un fond de scanner (gris clair, bord net) avec une marge."""
    h, w = doc.shape[:2]
    mx, my = int(w * margin), int(h * margin)
    tone = rng.randint(238, 250)
    page = np.full((h + 2 * my, w + 2 * mx, 3), tone, np.uint8)
    page[my : my + h, mx : mx + w] = doc
    cv2.rectangle(page, (mx, my), (mx + w - 1, my + h - 1), (150, 150, 150), 2)
    return page


def on_a4(front: RGBImage, back: RGBImage, rng: random.Random) -> RGBImage:
    """Recto et verso côte à côte sur une page A4 à 300 dpi (carte : 85,6 mm ≈ 1 011 px)."""
    page = np.full((3508, 2480, 3), rng.randint(242, 252), np.uint8)
    card_w = 1011
    card_h = round(card_w * front.shape[0] / front.shape[1])
    y = rng.randint(250, 600)
    for x, img in ((180, front), (180 + card_w + rng.randint(60, 160), back)):
        small = cv2.resize(img, (card_w, card_h), interpolation=cv2.INTER_AREA)
        page[y : y + card_h, x : x + card_w] = small
        cv2.rectangle(page, (x, y), (x + card_w - 1, y + card_h - 1), (140, 140, 140), 2)
    return page


# --- Génération ---------------------------------------------------------------------------


def _write(img: RGBImage, path: Path, jpeg: int) -> None:
    cv2.imwrite(str(path), cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, jpeg])


def generate_documents(
    out_dir: Path, *, count: int, seed: int, models_dir: Path, today: date, a4_share: float = 0.3
) -> list[DocCase]:
    """Génère `count` documents UTO dans `out_dir` (images JPEG + `truth.json`)."""
    for font in (VIZ_FONT, MRZ_FONT):
        if not (models_dir / font).is_file():
            raise FileNotFoundError(f"police absente : {font} (scripts/fetch_models.py)")
    rng = random.Random(seed)  # noqa: S311 (données synthétiques)
    out_dir.mkdir(parents=True, exist_ok=True)
    cases: list[DocCase] = []
    for i in range(count):
        quality = QUALITIES[i % len(QUALITIES)]
        kind = "passport" if rng.random() < 0.45 else "id_card"
        p = random_person(rng, today)
        surname_mrz = to_mrz_name(p["surname"])
        given_mrz = to_mrz_name(p["given_names"])
        if kind == "passport":
            number = "".join(rng.choice("ABCDEFGHJKLMNPRSTUVWXYZ0123456789") for _ in range(9))
            fmt, code = MrzFormat.TD3, "P"
        else:
            number = "".join(rng.choice("0123456789ABCDEFGHJKLMNPRTUVWXYZ") for _ in range(9))
            fmt, code = MrzFormat.TD1, "ID"
        data = MrzData(
            document_code=code,
            issuing_state=STATE,
            surname=surname_mrz,
            given_names=given_mrz,
            document_number=number,
            nationality=STATE,
            date_of_birth=date.fromisoformat(p["date_of_birth"]).strftime("%y%m%d"),
            sex=p["sex"],
            date_of_expiry=date.fromisoformat(p["date_of_expiry"]).strftime("%y%m%d"),
        )
        lines = build(fmt, data)
        fields: dict[str, str | None] = {
            "surname": p["surname"],
            "given_names": p["given_names"],
            "sex": p["sex"],
            "date_of_birth": p["date_of_birth"],
            "place_of_birth": p["place_of_birth"],
            "nationality": STATE,
            "issuing_state": STATE,
            "document_number": number,
            "date_of_issue": p["date_of_issue"],
            "date_of_expiry": p["date_of_expiry"],
            "authority": p["authority"],
            "usage_name": p["usage_name"].removeprefix("ÉP. ") or None,
        }
        case_id = f"doc-{seed}-{i:05d}"
        images: list[str] = []
        degradations: list[dict[str, float]] = []
        if kind == "passport":
            raw = on_scanner(render_passport(p, number, lines, rng, models_dir), rng)
            img, d, jpeg = degrade(raw, quality, rng)
            name = f"{case_id}.jpg"
            _write(img, out_dir / name, jpeg)
            images, degradations, layout = [name], [d], "separate"
        else:
            front, back = render_card(p, number, lines, rng, models_dir)
            if rng.random() < a4_share:
                img, d, jpeg = degrade(on_a4(front, back, rng), quality, rng)
                name = f"{case_id}_a4.jpg"
                _write(img, out_dir / name, jpeg)
                images, degradations, layout = [name], [d], "a4"
            else:
                layout = "separate"
                for side, face in (("recto", front), ("verso", back)):
                    img, d, jpeg = degrade(on_scanner(face, rng), quality, rng)
                    name = f"{case_id}_{side}.jpg"
                    _write(img, out_dir / name, jpeg)
                    images.append(name)
                    degradations.append(d)
        cases.append(DocCase(case_id, kind, quality, layout, images, fields, lines, degradations))
    truth = {"seed": seed, "count": count, "today": today.isoformat(),
             "cases": [asdict(c) for c in cases]}  # fmt: skip
    (out_dir / "truth.json").write_text(
        json.dumps(truth, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    return cases


def load_doc_cases(out_dir: Path) -> tuple[list[DocCase], date]:
    data = json.loads((out_dir / "truth.json").read_text(encoding="utf-8"))
    return [DocCase(**c) for c in data["cases"]], date.fromisoformat(data["today"])
