"""H.4 — Gabarits par modèle de document (`reference/templates/<PAYS>_<type>_<version>.yaml`).

Un gabarit décrit, pour un modèle précis (ex. passeport biométrique français), où se trouve chaque
champ **dans le recadrage normalisé** (coordonnées relatives en % : x, y, largeur, hauteur, origine
en haut à gauche), les libellés attendus, le format des dates, le format de MRZ et les indices de
reconnaissance du modèle.

Ce module ne fait que décrire, charger et contrôler les gabarits, et dessiner l'image de contrôle
(spécimen + cadres + noms). Les gabarits ne sont utilisés par le pipeline qu'après validation
humaine de leur image de contrôle (docs/DECISIONS.md).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from idreader.core.config import REPO_ROOT
from idreader.core.models import FIELD_NAMES
from idreader.pipeline.ingest import RGBImage

TEMPLATES_DIR = REPO_ROOT / "reference" / "templates"
# Zones sans équivalent dans les champs extraits (utiles aux règles et à la reconnaissance).
EXTRA_FIELDS = ("address", "height", "eye_colour", "country_code", "doc_type", "card_number")
KNOWN_FIELDS = frozenset((*FIELD_NAMES, *EXTRA_FIELDS))

Zone = tuple[float, float, float, float]  # x, y, largeur, hauteur en % du recadrage normalisé


def _check_zone(zone: Zone, name: str) -> Zone:
    x, y, w, h = zone
    if w <= 0 or h <= 0 or x < 0 or y < 0 or x + w > 100.5 or y + h > 100.5:
        raise ValueError(f"zone « {name} » hors du recadrage : {zone}")
    return zone


class FieldZone(BaseModel):
    model_config = ConfigDict(frozen=True)

    zone: Zone  # où se trouve la valeur
    label: str | None = None  # libellé imprimé attendu (forme complète, bilingue)
    label_zone: Zone | None = None
    multiline: bool = False

    @model_validator(mode="after")
    def _zones_inside(self) -> FieldZone:
        _check_zone(self.zone, "valeur")
        if self.label_zone:
            _check_zone(self.label_zone, "libellé")
        return self


class Face(BaseModel):
    """Une face du document (recto ou verso ; une seule pour un passeport)."""

    model_config = ConfigDict(frozen=True)

    specimen: str  # fichier du spécimen, relatif à IDR_SPECIMENS_DIR
    mrz_format: str | None = None  # TD1 | TD2 | TD3 | FRA-ID | null (pas de MRZ sur cette face)
    mrz_zone: Zone | None = None
    photo_zone: Zone | None = None
    fixed_labels: list[str] = Field(default_factory=list)  # libellés imprimés caractéristiques
    fields: dict[str, FieldZone] = Field(default_factory=dict)
    other_zones: dict[str, Zone] = Field(default_factory=dict)  # puce, 2D-Doc, signature…

    @field_validator("fields")
    @classmethod
    def _known(cls, v: dict[str, FieldZone]) -> dict[str, FieldZone]:
        unknown = sorted(set(v) - KNOWN_FIELDS)
        if unknown:
            raise ValueError(f"champs inconnus : {unknown}")
        return v

    @model_validator(mode="after")
    def _zones_inside(self) -> Face:
        for name, z in (("mrz", self.mrz_zone), ("photo", self.photo_zone)):
            if z:
                _check_zone(z, name)
        for name, z in self.other_zones.items():
            _check_zone(z, name)
        if self.mrz_format and not self.mrz_zone:
            raise ValueError("mrz_format sans mrz_zone")
        return self


class Recognition(BaseModel):
    """Indices pour reconnaître le modèle (jamais seuls : une MRZ valide prime)."""

    model_config = ConfigDict(frozen=True)

    physical_format: str  # ID1 | ID2 | ID3
    aspect: tuple[float, float]  # largeur / hauteur du recadrage, bornes basse et haute
    mrz_format: str | None = None
    photo_side: str | None = None  # left | right | none
    notes: str = ""


class Rules(BaseModel):
    """Règles d'extraction propres au modèle."""

    model_config = ConfigDict(frozen=True)

    numbered_labels: bool = False  # libellés « Nom (1) » : le numéro n'est jamais une valeur
    given_names_separator: str = ","
    usage_name_markers: list[str] = Field(default_factory=list)
    mrz_priority: bool = True  # une MRZ valide prime sur la zone imprimée
    notes: list[str] = Field(default_factory=list)


class Template(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    country: str
    type: str  # passport | id_card
    version: str
    description: str
    recognition: Recognition
    date_format: str  # ex. « DD MM YYYY » ; mois en lettres : « DD MMM/MMM YY »
    rules: Rules = Field(default_factory=Rules)
    faces: dict[str, Face]

    @model_validator(mode="after")
    def _consistent(self) -> Template:
        if not set(self.faces) <= {"front", "back"} or not self.faces:
            raise ValueError("faces : front et/ou back")
        expected = f"{self.country}_{self.type}_{self.version}"
        if self.id != expected:
            raise ValueError(f"id « {self.id} » ≠ « {expected} »")
        return self


def load_template(path: Path) -> Template:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    template = Template.model_validate(data)
    if path.stem != template.id:
        raise ValueError(f"nom de fichier « {path.stem} » ≠ id « {template.id} »")
    return template


def load_templates(directory: Path = TEMPLATES_DIR) -> dict[str, Template]:
    return {t.id: t for t in (load_template(p) for p in sorted(directory.glob("*.yaml")))}


# --- Spécimens et image de contrôle ---------------------------------------------------------

NORMAL_WIDTH = {"ID1": 1712, "ID2": 2500, "ID3": 2500}  # largeurs des recadrages normalisés
_COLORS = {
    "value": (0, 140, 0),
    "label": (150, 150, 150),
    "mrz": (200, 0, 0),
    "photo": (0, 90, 200),
    "other": (170, 90, 0),
}


def normalize_specimen(path: Path, physical_format: str) -> RGBImage:
    """Recadrage normalisé d'un spécimen, comme le ferait le pipeline : agrandi à la largeur du
    format, entouré d'une marge blanche, puis détecté et redressé par l'étape C."""
    from idreader.pipeline import detect

    bgr = cv2.imread(str(path))
    if bgr is None:
        raise ValueError("SPECIMEN_UNREADABLE")
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    scale = NORMAL_WIDTH[physical_format] / rgb.shape[1]
    big = cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    pad = int(0.08 * big.shape[1])
    page = np.asarray(
        cv2.copyMakeBorder(big, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(255, 255, 255)),
        np.uint8,
    )
    found = detect.crops(page)
    if not found:
        raise ValueError("SPECIMEN_NOT_DETECTED")
    return np.ascontiguousarray(found[0].image)


def _rect(image: RGBImage, zone: Zone) -> tuple[tuple[int, int], tuple[int, int]]:
    h, w = image.shape[:2]
    x, y, zw, zh = zone
    return (round(w * x / 100), round(h * y / 100)), (
        round(w * (x + zw) / 100),
        round(h * (y + zh) / 100),
    )


def _tag(image: RGBImage, text: str, corner: tuple[int, int], color: tuple[int, int, int]) -> None:
    scale = max(0.5, image.shape[1] / 2500 * 0.9)
    thick = max(1, round(scale * 2))
    (tw, th), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    x, y = corner[0], max(th + 2, corner[1] - 4)
    cv2.rectangle(image, (x, y - th - 3), (x + tw + 4, y + base - 1), (255, 255, 255), -1)
    cv2.putText(image, text, (x + 2, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def render_control(image: RGBImage, face: Face, title: str) -> RGBImage:
    """Spécimen avec un cadre et le nom de chaque zone (valeurs en vert, libellés en gris,
    MRZ en rouge, photo en bleu, autres zones en brun)."""
    out = np.ascontiguousarray(image.copy())
    thick = max(2, round(out.shape[1] / 900))
    for name, f in face.fields.items():
        if f.label_zone:
            p1, p2 = _rect(out, f.label_zone)
            cv2.rectangle(out, p1, p2, _COLORS["label"], max(1, thick - 1))
        p1, p2 = _rect(out, f.zone)
        cv2.rectangle(out, p1, p2, _COLORS["value"], thick)
        _tag(out, name, p1, _COLORS["value"])
    if face.mrz_zone:
        p1, p2 = _rect(out, face.mrz_zone)
        cv2.rectangle(out, p1, p2, _COLORS["mrz"], thick)
        _tag(out, f"mrz {face.mrz_format}", p1, _COLORS["mrz"])
    if face.photo_zone:
        p1, p2 = _rect(out, face.photo_zone)
        cv2.rectangle(out, p1, p2, _COLORS["photo"], thick)
        _tag(out, "photo", p1, _COLORS["photo"])
    for name, zone in face.other_zones.items():
        p1, p2 = _rect(out, zone)
        cv2.rectangle(out, p1, p2, _COLORS["other"], thick)
        _tag(out, name, p1, _COLORS["other"])
    h = out.shape[0]
    _tag(out, title, (6, h - 8), (0, 0, 0))
    return out
