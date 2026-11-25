"""H — corrections issues du premier lot réel (diagnostic) : libellés numérotés et variantes
OCR, valeurs invraisemblables, « Type : P », marques de nom d'usage, majuscules.

Textes synthétiques uniquement (noms inventés).
"""

from __future__ import annotations

import pytest

from idreader.pipeline.extract import (
    extract,
    is_non_field_label,
    is_numbering_only,
    label_field,
    plausible_value,
    split_usage,
)
from tests.unit.pipeline.test_viz import TD3, TODAY, mrz, viz

# --- Libellés numérotés et variantes OCR -------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Prénoms (2)",
        "Prénoms (2",  # parenthèse fermante perdue
        "Prénoms {2}",
        "Prénoms [2]",
        "Prénoms(2)",
        "Prénoms 2",
        "Prenoms (Z)",  # 2 lu Z
        "Prénoms (6Z)",
        "Prénoms / Given names (2)",
        "2. Prénoms",
        "(2) Prénoms",
    ],
)
def test_numbered_label_variants_are_labels(text: str) -> None:
    assert label_field(text) == ("given_names", "")


@pytest.mark.parametrize("text", ["(2)", "(6Z)", "{2}", "[3]", "(2", "2)", " ( 2 ) "])
def test_numbering_only_is_not_a_value(text: str) -> None:
    assert is_numbering_only(text)


@pytest.mark.parametrize("text", ["P", "DUPONT", "ANNA, MARIA", "12", "FRA", "(Jean)"])
def test_regular_texts_are_not_numbering(text: str) -> None:
    assert not is_numbering_only(text)


def test_isolated_numbering_below_label_is_skipped() -> None:
    rows = (
        ("Nom (1)", 500, 100),
        ("DURANDAL", 500, 135),
        ("Prénoms (2)", 500, 200),
        ("(2)", 500, 235),  # numéro détaché de son libellé
        ("ANNA, MARIA", 500, 270),
    )
    ex = extract(viz(*rows), None, today=TODAY)
    assert ex.fields["given_names"].value == "ANNA, MARIA"
    assert ex.fields["surname"].value == "DURANDAL"


def test_garbled_numbering_never_becomes_given_names() -> None:
    rows = (("Prénoms {2}", 500, 200), ("(6Z)", 500, 235))
    ex = extract(viz(*rows), None, today=TODAY)
    assert "given_names" not in ex.fields


# --- Valeurs invraisemblables ------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "text", "ok"),
    [
        ("surname", "P", False),
        ("surname", "ID", False),
        ("surname", "Passeport", False),
        ("surname", "DURANDAL", True),
        ("given_names", "(6Z)", False),
        ("given_names", "e napes 12", False),
        ("given_names", "ANNA, MARIA", True),
        ("given_names", "Prénoms (2)", False),
        ("given_names", "Marie-Hélène", True),
        ("surname", "Nom", False),  # un libellé
        ("nationality", "Nationalité (4) / Nationality (4)", False),
        ("nationality", "Française / French", True),
        ("place_of_birth", "(3)", False),
        ("place_of_birth", "SAINT-ÉTIENNE", True),
    ],
)
def test_plausible_value(field: str, text: str, ok: bool) -> None:
    assert plausible_value(field, text) is ok


def test_document_type_code_is_not_a_surname() -> None:
    rows = (("Nom / Surname", 100, 100), ("P", 100, 135), ("DURANDAL", 100, 170))
    ex = extract(viz(*rows), None, today=TODAY)
    assert ex.fields["surname"].value == "DURANDAL"


def test_type_label_and_its_value_are_excluded() -> None:
    rows = (
        ("Type / Type", 100, 20),
        ("P", 100, 55),
        ("Nom / Surname", 100, 110),
        ("DURANDAL", 100, 145),
    )
    ex = extract(viz(*rows), None, today=TODAY)
    assert ex.fields["surname"].value == "DURANDAL"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Type / Type", True),
        ("Type : P", True),
        ("Type: P", True),
        ("Code pays / Country code", True),
        ("Nom / Surname", False),
        ("Type de document", False),
    ],
)
def test_non_field_labels(text: str, expected: bool) -> None:
    assert is_non_field_label(text) is expected


def test_inline_type_slot_is_never_a_value() -> None:
    rows = (("Nom / Surname", 100, 100), ("Type : P", 100, 135), ("DURANDAL", 100, 170))
    ex = extract(viz(*rows), None, today=TODAY)
    assert ex.fields["surname"].value == "DURANDAL"


# --- Marques de nom d'usage ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "base", "usage"),
    [
        ("DURANDAL ép. MARCHETON", "DURANDAL", "MARCHETON"),
        ("DURANDAL EP. MARCHETON", "DURANDAL", "MARCHETON"),
        ("DURANDAL épouse MARCHETON", "DURANDAL", "MARCHETON"),
        ("DURANDAL usage MARCHETON", "DURANDAL", "MARCHETON"),
        ("DURANDAL vve MARCHETON", "DURANDAL", "MARCHETON"),
        ("ép. MARCHETON", "", "MARCHETON"),
        ("DURANDAL", "DURANDAL", None),
        ("REPETTO", "REPETTO", None),  # « EP » à l'intérieur d'un mot : pas une marque
    ],
)
def test_split_usage(text: str, base: str, usage: str | None) -> None:
    assert split_usage(text) == (base, usage)


def test_usage_mark_on_surname_line_goes_to_usage_name() -> None:
    rows = (
        ("Nom / Surname", 500, 100),
        ("DURANDAL ép. MARCHETON", 500, 135),
        ("Prénoms / Given names", 500, 200),
        ("ANNA, MARIA", 500, 235),
    )
    ex = extract(viz(*rows), None, today=TODAY)
    assert ex.fields["surname"].value == "DURANDAL"
    assert ex.fields["usage_name"].value == "MARCHETON"
    assert ex.fields["given_names"].value == "ANNA, MARIA"


def test_usage_line_below_given_names_label_is_not_given_names() -> None:
    rows = (
        ("Prénoms / Given names", 500, 200),
        ("DURANDAL usage MARCHETON", 500, 235),  # ligne du nom, plus proche du libellé
        ("ANNA, MARIA", 500, 270),
    )
    ex = extract(viz(*rows), None, today=TODAY)
    assert ex.fields["given_names"].value == "ANNA, MARIA"
    assert ex.fields["usage_name"].value == "MARCHETON"


def test_usage_name_label_with_value() -> None:
    rows = (("Nom d'usage / Used name", 500, 300), ("MARCHETON", 500, 335))
    ex = extract(viz(*rows), None, today=TODAY)
    assert ex.fields["usage_name"].value == "MARCHETON"


def test_mrz_confirms_surname_even_with_usage_mark() -> None:
    rows = (
        ("Nom / Surname", 500, 100),
        ("ÉRIKSSON ép. MARCHETON", 500, 135),
        ("Prénoms / Given names", 500, 200),
        ("ANNA, MARIA", 500, 235),
    )
    ex = extract(viz(*rows), mrz(TD3), today=TODAY)
    assert ex.fields["surname"].value == "ÉRIKSSON" and ex.fields["surname"].confirms_mrz
    assert ex.fields["usage_name"].value == "MARCHETON"


# --- Majuscules ---------------------------------------------------------------------------


def test_names_are_uppercased() -> None:
    rows = (
        ("Nom / Surname", 500, 100),
        ("Durandal", 500, 135),
        ("Prénoms / Given names", 500, 200),
        ("Marie-Hélène, Jeanne", 500, 235),
    )
    ex = extract(viz(*rows), None, today=TODAY)
    assert ex.fields["surname"].value == "DURANDAL"
    assert ex.fields["given_names"].value == "MARIE-HÉLÈNE, JEANNE"


def test_mrz_guided_given_names_in_mixed_case_are_uppercased() -> None:
    rows = (("Prénoms / Given names", 500, 200), ("Anna, Maria", 500, 235))
    ex = extract(viz(*rows), mrz(TD3), today=TODAY)
    assert ex.fields["given_names"].value == "ANNA, MARIA"
    assert ex.fields["given_names"].confirms_mrz
