"""H.4 — gabarits français : schéma, cohérence, et test sur les spécimens PRADO.

Les tests sur spécimens sont sautés si `IDR_SPECIMENS_DIR` (défaut `docs-entrainement/`) ou les
modèles OCR manquent : les spécimens ne sont pas dans le dépôt (conditions d'utilisation de PRADO).
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pytest
import yaml

from idreader.core.config import Settings
from idreader.pipeline.mrz.locate import looks_like_mrz_line
from idreader.pipeline.templates import (
    TEMPLATES_DIR,
    Template,
    Zone,
    load_template,
    load_templates,
    normalize_specimen,
    render_control,
)
from tests.unit.pipeline.test_analyze import VIZ

SETTINGS = Settings()
SPECIMENS = SETTINGS.specimens_dir
needs_specimens = pytest.mark.skipif(
    not (SPECIMENS / "France").is_dir() or VIZ is None,
    reason="spécimens PRADO (IDR_SPECIMENS_DIR) ou modèles OCR absents",
)
FRENCH = ("FRA_passport_2009", "FRA_id_card_2021", "FRA_id_card_1995")


# --- Schéma et cohérence (sans spécimen) -----------------------------------------------------


def test_french_templates_are_present_and_valid() -> None:
    templates = load_templates()
    assert set(FRENCH) <= set(templates)
    assert set(templates["FRA_id_card_2021"].faces) == {"front", "back"}
    assert set(templates["FRA_id_card_1995"].faces) == {"front", "back"}
    assert set(templates["FRA_passport_2009"].faces) == {"front"}


@pytest.mark.parametrize("tid", FRENCH)
def test_template_matches_file_name_and_formats(tid: str) -> None:
    t = load_template(TEMPLATES_DIR / f"{tid}.yaml")
    assert t.id == tid and t.country == "FRA"
    assert t.recognition.physical_format in {"ID1", "ID2", "ID3"}
    low, high = t.recognition.aspect
    assert 1.3 < low < high < 1.7
    assert t.rules.mrz_priority  # une MRZ valide prime toujours sur la zone imprimée
    assert t.rules.given_names_separator == ","
    for marker in ("ép.", "usage"):
        assert marker in t.rules.usage_name_markers


def test_mrz_faces() -> None:
    t = load_templates()
    assert t["FRA_passport_2009"].faces["front"].mrz_format == "TD3"
    assert t["FRA_id_card_2021"].faces["front"].mrz_format is None
    assert t["FRA_id_card_2021"].faces["back"].mrz_format == "TD1"
    assert t["FRA_id_card_1995"].faces["front"].mrz_format == "FRA-ID"
    assert t["FRA_id_card_1995"].faces["back"].mrz_format is None


def test_numbered_labels_only_on_the_numbered_passport() -> None:
    t = load_templates()
    assert t["FRA_passport_2009"].rules.numbered_labels
    face = t["FRA_passport_2009"].faces["front"]
    assert face.fields["surname"].label == "Nom / Surname (1)"
    assert face.fields["given_names"].label == "Prénoms / Given names (2)"


def test_usage_name_and_given_names_zones_are_distinct() -> None:
    face = load_templates()["FRA_id_card_2021"].faces["front"]
    usage, given = face.fields["usage_name"].zone, face.fields["given_names"].zone
    assert usage[1] > given[1] + given[3]  # le nom d'usage est sous les prénoms, jamais dedans


def _overlap(a: Zone, b: Zone) -> bool:
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


@pytest.mark.parametrize("tid", FRENCH)
def test_value_zones_do_not_overlap_each_other(tid: str) -> None:
    for side, face in load_templates()[tid].faces.items():
        zones = [(n, f.zone) for n, f in face.fields.items()]
        for i, (na, za) in enumerate(zones):
            for nb, zb in zones[i + 1 :]:
                assert not _overlap(za, zb), f"{tid} {side} : {na} et {nb} se chevauchent"
        if face.mrz_zone:
            assert not any(_overlap(face.mrz_zone, z) for _n, z in zones)


def _base() -> dict:
    return yaml.safe_load((TEMPLATES_DIR / "FRA_passport_2009.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["faces"]["front"]["fields"]["surname"].update(zone=[90, 10, 20, 5]),  # déborde
        lambda d: d["faces"]["front"]["fields"]["surname"].update(zone=[10, 10, 0, 5]),  # largeur 0
        lambda d: d["faces"]["front"]["fields"].update(inconnu={"zone": [1, 1, 5, 5]}),
        lambda d: d.update(id="FRA_passport_1999"),  # id ≠ pays_type_version
        lambda d: d["faces"]["front"].update(mrz_zone=None),  # mrz_format sans zone
        lambda d: d["faces"].update(side={}),
    ],
)
def test_invalid_templates_are_rejected(mutate) -> None:
    data = _base()
    mutate(data)
    with pytest.raises(ValueError):
        Template.model_validate(data)


def test_file_name_must_match_id(tmp_path: Path) -> None:
    (tmp_path / "autre.yaml").write_text((TEMPLATES_DIR / "FRA_passport_2009.yaml").read_text())
    with pytest.raises(ValueError, match="nom de fichier"):
        load_template(tmp_path / "autre.yaml")


def test_render_control_draws_boxes_and_names() -> None:
    face = load_templates()["FRA_passport_2009"].faces["front"]
    blank = np.full((1760, 2500, 3), 255, np.uint8)
    control = render_control(blank, face, "titre")
    assert control.shape == blank.shape and not np.array_equal(control, blank)
    assert (blank == 255).all()  # l'image d'origine n'est pas modifiée


# --- Spécimens PRADO : chaque zone tombe sur le bon texte ---------------------------------


def _zone_text(image: np.ndarray, zone: Zone) -> str:
    assert VIZ is not None
    h, w = image.shape[:2]
    x, y, zw, zh = zone
    crop = np.ascontiguousarray(
        image[
            int(h * y / 100) : int(h * (y + zh) / 100), int(w * x / 100) : int(w * (x + zw) / 100)
        ]
    )
    return " | ".join(line.text for line in VIZ.recognize(crop, languages=["fr", "en"]))


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _specimen(tid: str, side: str) -> tuple[Template, np.ndarray]:
    t = load_templates()[tid]
    face = t.faces[side]
    return t, normalize_specimen(SPECIMENS / face.specimen, t.recognition.physical_format)


@needs_specimens
def test_passport_specimen_fields_fall_in_their_zones() -> None:
    t, img = _specimen("FRA_passport_2009", "front")
    f = t.faces["front"].fields
    assert _zone_text(img, f["surname"].zone) == "SPECIMEN ABB"
    assert _zone_text(img, f["given_names"].zone) == "NATACHA"
    assert _zone_text(img, f["country_code"].zone) == "FRA"
    assert _zone_text(img, f["doc_type"].zone) == "P"
    assert _digits(_zone_text(img, f["date_of_birth"].zone)) == "12071973"
    assert _digits(_zone_text(img, f["date_of_issue"].zone)) == "05032019"
    assert _digits(_zone_text(img, f["date_of_expiry"].zone)) == "03032029"
    assert "TOULON" in _zone_text(img, f["place_of_birth"].zone)
    assert "Pr" in _zone_text(img, f["authority"].zone)
    assert "LODEVE" in _zone_text(img, f["address"].zone)
    number = _zone_text(img, f["document_number"].zone).upper().replace("O", "0")
    assert number == "90RF02331"  # O/0 : même confusion que la MRZ (ICAO 9303)
    # Un libellé numéroté n'est jamais dans une zone de valeur.
    for name in ("surname", "given_names", "nationality", "date_of_birth"):
        assert not re.search(r"\(\s*\d{1,2}\s*\)", _zone_text(img, f[name].zone))


@needs_specimens
def test_passport_specimen_mrz_zone_holds_two_mrz_lines() -> None:
    t, img = _specimen("FRA_passport_2009", "front")
    text = _zone_text(img, t.faces["front"].mrz_zone or (0, 0, 1, 1))
    lines = [x for x in text.split(" | ") if looks_like_mrz_line(x)]
    assert len(lines) == 2 and lines[0].startswith("P<FRA")


@needs_specimens
def test_id_card_2021_front_specimen_fields() -> None:
    t, img = _specimen("FRA_id_card_2021", "front")
    f = t.faces["front"].fields
    assert _zone_text(img, f["surname"].zone) == "MARTIN"
    assert _zone_text(img, f["document_number"].zone) == "X4RTBPFW4"
    assert _digits(_zone_text(img, f["date_of_expiry"].zone)) == "11022030"
    assert _digits(_zone_text(img, f["card_number"].zone)) == "384213"
    assert "PARIS" in _zone_text(img, f["place_of_birth"].zone)
    # Le nom d'usage est une zone distincte des prénoms.
    assert "Marie" in _zone_text(img, f["given_names"].zone)
    assert "Marie" not in _zone_text(img, f["usage_name"].zone)


@needs_specimens
def test_id_card_2021_back_specimen_holds_mrz_td1_and_issue_date() -> None:
    t, img = _specimen("FRA_id_card_2021", "back")
    face = t.faces["back"]
    text = _zone_text(img, face.mrz_zone or (0, 0, 1, 1))
    assert len([x for x in text.split(" | ") if looks_like_mrz_line(x)]) >= 2
    assert _digits(_zone_text(img, face.fields["date_of_issue"].zone)) == "12022020"


@needs_specimens
def test_old_id_card_specimen_mrz_and_back_dates() -> None:
    t, img = _specimen("FRA_id_card_1995", "front")
    text = _zone_text(img, t.faces["front"].mrz_zone or (0, 0, 1, 1))
    assert len([x for x in text.split(" | ") if looks_like_mrz_line(x)]) >= 1
    _t, back = _specimen("FRA_id_card_1995", "back")
    fb = t.faces["back"].fields
    assert _digits(_zone_text(back, fb["date_of_expiry"].zone)).endswith("2004")
    assert "104" in _zone_text(back, fb["address"].zone)
