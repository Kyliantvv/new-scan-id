"""Apports du catalogue du projet « Scan » : libellés multilingues, motifs de numéros,
nationalité en toutes lettres, date de délivrance déduite de l'expiration.

Données synthétiques uniquement (MRZ spécimens ICAO, noms inventés).
"""

from __future__ import annotations

import re

import pytest

from idreader.pipeline.classify import Classification, DocType, classify_text
from idreader.pipeline.consolidate import (
    DocState,
    FieldResult,
    FieldState,
    _issue_from_old_card_number,
    consolidate,
)
from idreader.pipeline.extract import extract, label_field, nationality_code
from idreader.pipeline.reference import doc_number_patterns, nationality_words, viz_labels
from tests.unit.pipeline.test_viz import PASSPORT, TD3_VALID, TODAY, mrz, viz

# --- Libellés ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "field"),
    [
        ("Efternamn", "surname"),
        ("Fornavn", "given_names"),
        ("Datum narozeni", "date_of_birth"),
        ("Szuletesi hely", "place_of_birth"),
        ("Data wydania", "date_of_issue"),
        ("Gultig bis", "date_of_expiry"),
        ("Kansalaisuus", "nationality"),
        ("Pohlavi", "sex"),
        ("Dzimsanas datums", "date_of_birth"),
    ],
)
def test_labels_of_other_languages(text: str, field: str) -> None:
    assert label_field(text) == (field, "")


def test_every_imported_label_resolves_to_its_own_field() -> None:
    wrong = [
        (label, field)
        for field, labels in viz_labels().items()
        for label in labels
        if len(label) >= 4 and label_field(label)[0] not in (field, None)
    ]
    # Un libellé identique dans deux champs (« Nom », « Datum ») est toléré s'il est déjà connu.
    assert len(wrong) <= 6, wrong


# --- Motifs de numéros -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "code", "good", "bad"),
    [
        ("FRA", "I", "X4RTBPFW4", "X4RTB"),
        ("D", "I", "L01X00T47", "A01X00T47"),  # pas de voyelle
        ("ESP", "I", "BAA000589", "12A000589"),
        ("NLD", "P", "SPECI2014", "SPECO2014"),  # jamais la lettre O
        ("USA", "P", "A12345678", "AB2345678"),
        ("CHN", "P", "E12345678", "G12345678"),
        ("ITA", "C", "CA12345AB", "CA1234AB"),
    ],
)
def test_document_number_patterns(state: str, code: str, good: str, bad: str) -> None:
    pattern = doc_number_patterns()[(state, code)]
    assert pattern.fullmatch(good) and not pattern.fullmatch(bad)


# --- Nationalité en toutes lettres -------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("Française", "FRA"),
        ("FRANÇAISE / FRENCH", "FRA"),
        ("Nederlandse", "NLD"),
        ("Pays-Bas", "NLD"),
        ("UNITED STATES OF AMERICA", "USA"),
        ("Deutsch", "D"),
        ("FRA", "FRA"),
        ("D", "D"),
    ],
)
def test_nationality_code(text: str, code: str) -> None:
    assert nationality_code(text) == code


@pytest.mark.parametrize("text", ["", "12", "Martienne", "Française / Italienne", "XYZ"])
def test_unknown_nationality_is_kept_as_text(text: str) -> None:
    assert nationality_code(text) is None


def test_nationality_table_keys_are_folded() -> None:
    assert all(k == k.lower() and not re.search(r"[^\x00-\x7f]", k) for k in nationality_words())


def test_printed_nationality_becomes_a_code_without_mrz() -> None:
    rows = (("Nationalité / Nationality", 500, 100), ("Française", 500, 135))
    out = extract(viz(*rows), None, today=TODAY)
    assert out.fields["nationality"].value == "FRA"


# --- Date de délivrance déduite ----------------------------------------------------------


def _fra(doc_type: DocType, fmt: str) -> Classification:
    return Classification(doc_type=doc_type, issuing_state="FRA", mrz_format=fmt)


def _consolidate(cls: Classification, rows: tuple[tuple[str, float, float], ...]):  # type: ignore[no-untyped-def]
    m = mrz(TD3_VALID)  # naissance 1974-08-12, expiration 2034-04-15
    return consolidate(
        mrz=m, extraction=extract(viz(*rows), m, today=TODAY), classification=cls,
        today=TODAY, threshold=0.95, allow_specimen=True,
    )  # fmt: skip


NO_ISSUE = tuple(r for r in PASSPORT if "délivrance" not in r[0] and "AVR/APR 2024" not in r[0])


def test_issue_date_deduced_from_expiry_for_french_passport() -> None:
    r = _consolidate(_fra(DocType.PASSPORT, "TD3"), NO_ISSUE)
    issue = r.fields["date_of_issue"]
    assert issue.value == "2024-04-16"  # expire le 15/04/2034 : délivré le 16/04/2024
    assert issue.status is FieldState.UNCERTAIN and issue.sources == ["rule"]
    assert "ISSUE_DATE_DEDUCED" in r.alerts


def test_deduced_issue_date_never_blocks_nor_grants_validation() -> None:
    r = _consolidate(_fra(DocType.PASSPORT, "TD3"), NO_ISSUE)
    assert not any(x.field == "date_of_issue" for x in r.reasons)


def test_printed_issue_date_wins_over_deduction() -> None:
    r = _consolidate(_fra(DocType.PASSPORT, "TD3"), PASSPORT)
    assert r.fields["date_of_issue"].sources == ["viz"]
    assert "ISSUE_DATE_DEDUCED" not in r.alerts


@pytest.mark.parametrize(
    "cls",
    [
        Classification(doc_type=DocType.PASSPORT, issuing_state="UTO", mrz_format="TD3"),
        Classification(doc_type=DocType.ID_CARD, issuing_state="FRA", mrz_format="FRA-ID"),
        Classification(doc_type=DocType.VISA, issuing_state="FRA", mrz_format="MRVA"),
    ],
)
def test_no_deduction_without_a_known_rule(cls: Classification) -> None:
    r = _consolidate(cls, NO_ISSUE)
    assert r.fields["date_of_issue"].status is FieldState.MISSING


# --- Ancienne CNI : mois et année de délivrance dans le n° de carte ---------------------------


def _number(value: str, status: FieldState = FieldState.OK) -> FieldResult:
    return FieldResult(name="document_number", value=value, status=status, confidence=0.99)


@pytest.mark.parametrize(
    ("number", "expected"),
    [("090301312345", "2009-03-01"), ("950701234567", "1995-07-01")],
)
def test_old_card_issue_month_from_number(number: str, expected: str | None) -> None:
    got = _issue_from_old_card_number(_number(number))
    assert (got.value if got else None) == expected
    if got:
        assert got.status is FieldState.UNCERTAIN and got.sources == ["rule"]


@pytest.mark.parametrize("number", ["901301312345", "220101312345", "AB0301312345", ""])
def test_old_card_number_impossible_month_or_year(number: str) -> None:
    assert _issue_from_old_card_number(_number(number)) is None


def test_old_card_number_not_proven_gives_nothing() -> None:
    assert _issue_from_old_card_number(_number("090301312345", FieldState.UNCERTAIN)) is None


# --- État émetteur lu sur l'en-tête (sans MRZ) ------------------------------------------------


@pytest.mark.parametrize(
    ("texts", "state"),
    [
        (["RÉPUBLIQUE FRANÇAISE", "CARTE NATIONALE D'IDENTITÉ"], "FRA"),
        (["PRINCIPAUTÉ DE MONACO", "Passeport"], "MCO"),
        (["Koninkrijk der Nederlanden", "Paspoort"], "NLD"),
        (["BUNDESREPUBLIK DEUTSCHLAND", "PERSONALAUSWEIS"], "D"),
        (["REPUBLIQUE FRANCAISE", "REPUBBLICA ITALIANA"], None),  # deux États : on ne devine pas
        (["Nom", "Prénoms"], None),
    ],
)
def test_issuing_state_from_header(texts: list[str], state: str | None) -> None:
    assert classify_text(texts, physical_format="ID1").issuing_state == state


def test_header_state_fills_the_field_as_uncertain_without_mrz() -> None:
    cls = classify_text(
        ["RÉPUBLIQUE FRANÇAISE", "CARTE NATIONALE D'IDENTITÉ"], physical_format="ID1"
    )
    rows = (("Nom", 500, 100), ("DURANDAL", 500, 135))
    r = consolidate(
        mrz=None, extraction=extract(viz(*rows), None, today=TODAY), classification=cls,
        today=TODAY, threshold=0.95,
    )  # fmt: skip
    state = r.fields["issuing_state"]
    assert state.value == "FRA" and state.status is FieldState.UNCERTAIN
    assert r.status is not DocState.AUTO_VALIDATED


# --- N° de document sans libellé ---------------------------------------------------------------


@pytest.mark.parametrize("text", ["12AB34567", "CA12345AB"])
def test_distinctive_number_read_without_label(text: str) -> None:
    out = extract(viz(("Surname", 500, 100), (text, 500, 300)), None, today=TODAY)
    assert out.fields["document_number"].value == text


@pytest.mark.parametrize("text", ["123456789", "SPECIMEN", "12AB3456"])
def test_ordinary_text_is_not_a_document_number(text: str) -> None:
    out = extract(viz(("Surname", 500, 100), (text, 500, 300)), None, today=TODAY)
    assert "document_number" not in out.fields


def test_mrz_number_is_never_replaced_by_a_format_guess() -> None:
    m = mrz(TD3_VALID)
    out = extract(viz(("12AB34567", 500, 300)), m, today=TODAY)
    assert "document_number" not in out.fields or out.fields["document_number"].method != "format"
