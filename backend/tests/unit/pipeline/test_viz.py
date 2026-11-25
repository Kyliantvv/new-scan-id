"""Étapes F, G, H, L : zone visuelle, extraction, classification, consolidation.

Entrées construites à la main (lignes OCR synthétiques) et MRZ des spécimens ICAO (UTO).
"""

from __future__ import annotations

from datetime import date

import pytest

from idreader.engines.base import Quad, TextLine
from idreader.pipeline.classify import DocType, classify_mrz, classify_text
from idreader.pipeline.consolidate import DocState, FieldState, consolidate
from idreader.pipeline.dates import matches_mrz, parse_dates
from idreader.pipeline.extract import extract, label_field
from idreader.pipeline.mrz.decode import MrzResult, decode
from idreader.pipeline.mrz.vote import BandReading, vote
from idreader.pipeline.viz_ocr import Slot, VizOcr, VizReading, fuse, languages_for

TODAY = date(2026, 11, 24)
TD3 = [
    "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
    "L898902C36UTO7408122F1204159ZE184226B<<<<<10",
]
# Même titulaire, document encore valide (expiration 2034-04-15).
TD3_VALID = [
    "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
    "L898902C36UTO7408122F3404159<<<<<<<<<<<<<<<4",
]


def mrz(lines: list[str], engines: tuple[str, ...] = ("tesseract", "ppocr")) -> MrzResult:
    voted = vote([BandReading(engine=e, lines=lines) for e in engines])
    assert voted is not None
    result = decode(voted, today=TODAY)
    assert result is not None
    return result


def quad(x: float, y: float, w: float, h: float = 30) -> Quad:
    return Quad(points=((x, y), (x + w, y), (x + w, y + h), (x, y + h)))


def line(text: str, x: float, y: float, w: float = 300, engine: str = "ppocr") -> TextLine:
    return TextLine(text=text, confidence=0.95, quad=quad(x, y, w), engine=engine)


def viz(*rows: tuple[str, float, float], engines: tuple[str, ...] = ("ppocr",)) -> VizOcr:
    """Zone visuelle : chaque (texte, x, y) est lu à l'identique par chaque moteur."""
    lines = [line(t, x, y, engine=e) for t, x, y in rows for e in engines]
    return VizOcr(engines=list(engines), languages=["fr", "en"], slots=fuse(lines))


PASSPORT = (
    ("Nom / Surname", 500, 100),
    ("ÉRIKSSON", 500, 135),
    ("Prénoms / Given names", 500, 200),
    ("ANNA, MARIA", 500, 235),
    ("Date de naissance / Date of birth", 500, 300),
    ("12 AOÛT/AUG 1974", 500, 335),
    ("Lieu de naissance / Place of birth", 1000, 300),
    ("SAINT-ÉTIENNE", 1000, 335),
    ("Date de délivrance / Date of issue", 500, 400),
    ("16 AVR/APR 2024", 500, 435),
    ("Autorité / Authority", 1000, 400),
    ("PRÉFECTURE D'UTOPIA", 1000, 435),
)


# --- H.3 dates ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12 AVR/APR 1987", "1987-04-12"),
        ("12 août 1974", "1974-08-12"),
        ("30.09.1950", "1950-09-30"),
        ("30/09/1950", "1950-09-30"),
        ("1987年04月12日", "1987-04-12"),
        ("1987.04.12", "1987-04-12"),
        ("Le 3 MÄRZ 2001", "2001-03-03"),
    ],
)
def test_parse_dates(text: str, expected: str) -> None:
    assert [d.value.isoformat() for d in parse_dates(text)] == [expected]


def test_invalid_date_is_ignored() -> None:
    assert parse_dates("31.02.2001") == []


def test_date_matches_mrz() -> None:
    (d,) = parse_dates("12 AOÛT 1974")
    assert matches_mrz(d, 74, 8, 12)
    assert not matches_mrz(d, 74, 8, 13)


# --- G fusion -----------------------------------------------------------------------------


def test_fuse_merges_engines_reading_same_box() -> None:
    lines = [line("ÉRIKSSON", 10, 10, engine="ppocr"), line("ÉRIKSSON", 12, 11, engine="vision")]
    (slot,) = fuse(lines)
    assert slot.agreed
    assert {r.engine for r in slot.readings} == {"ppocr", "vision"}


def test_fuse_keeps_separate_boxes_and_disagreement() -> None:
    lines = [
        line("ÉRIKSSON", 10, 10, engine="ppocr"),
        line("ERIKSSON", 10, 10, engine="vision"),
        line("ANNA", 10, 200, engine="ppocr"),
    ]
    slots = fuse(lines)
    assert len(slots) == 2
    assert not slots[0].agreed


def test_one_reading_per_engine_per_slot() -> None:
    lines = [line("A", 10, 10, engine="ppocr"), line("B", 11, 10, engine="ppocr")]
    assert len(fuse(lines)) == 2


def test_languages_follow_country() -> None:
    langs = languages_for("FRA")
    assert langs[0] == "fr" and "en" in langs
    assert "en" in languages_for(None)


# --- H.2 libellés -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "field"),
    [
        ("Nom / Surname", "surname"),
        ("1. Nom", "surname"),
        ("Date of birth", "date_of_birth"),
        ("Geburtsdatum / Date of birth", "date_of_birth"),
        ("Lieu de naissance", "place_of_birth"),
    ],
)
def test_label_field(text: str, field: str) -> None:
    assert label_field(text)[0] == field


def test_label_with_inline_value() -> None:
    name, rest = label_field("Nom: ÉRIKSSON")
    assert name == "surname"
    assert rest.strip() == "ÉRIKSSON"


# --- H.1 recherche guidée par la MRZ --------------------------------------------------------


def test_viz_confirms_mrz_names_and_keeps_accents() -> None:
    ex = extract(viz(*PASSPORT), mrz(TD3_VALID), today=TODAY)
    assert ex.fields["surname"].confirms_mrz
    assert ex.fields["surname"].value == "ÉRIKSSON"
    assert ex.fields["given_names"].confirms_mrz
    dob = ex.fields["date_of_birth"]
    assert dob.confirms_mrz and dob.year4 == 1974
    assert ex.fields["place_of_birth"].value == "SAINT-ÉTIENNE"
    assert ex.fields["date_of_issue"].value == "2024-04-16"
    assert ex.fields["authority"].value == "PRÉFECTURE D'UTOPIA"
    assert ex.conflicts == []


def test_viz_name_different_from_mrz_is_conflict() -> None:
    rows = tuple(("DUPONTEL", x, y) if t == "ÉRIKSSON" else (t, x, y) for t, x, y in PASSPORT)
    ex = extract(viz(*rows), mrz(TD3_VALID), today=TODAY)
    assert "VIZ_MRZ_NAME_CONFLICT" in ex.conflicts
    assert not ex.fields.get("surname") or not ex.fields["surname"].confirms_mrz


def test_close_but_wrong_accent_reading_is_not_confirmed() -> None:
    # « ERIKSSÖN » se translittère en ERIKSSOEN ou ERIKSSON : seule la forme exacte confirme.
    rows = tuple(("ERIKSSAN", x, y) if t == "ÉRIKSSON" else (t, x, y) for t, x, y in PASSPORT)
    ex = extract(viz(*rows), mrz(TD3_VALID), today=TODAY)
    assert not (ex.fields.get("surname") and ex.fields["surname"].confirms_mrz)


def test_labels_without_mrz() -> None:
    ex = extract(viz(*PASSPORT), None, today=TODAY)
    assert ex.fields["surname"].value == "ÉRIKSSON"
    assert ex.fields["date_of_birth"].value == "1974-08-12"
    assert not ex.fields["surname"].confirms_mrz


# --- F classification ---------------------------------------------------------------------


def test_classify_from_mrz() -> None:
    c = classify_mrz(mrz(TD3))
    assert c.doc_type is DocType.PASSPORT
    assert c.physical_format == "ID3"
    assert c.issuing_state == "UTO"
    assert not c.probable


def test_classify_from_text_is_probable() -> None:
    c = classify_text(["RÉPUBLIQUE FRANÇAISE", "CARTE NATIONALE D'IDENTITÉ"], physical_format="ID1")
    assert c.doc_type is DocType.ID_CARD
    assert c.probable


# --- L consolidation ----------------------------------------------------------------------


def _consolidate(m: MrzResult | None, rows: tuple[tuple[str, float, float], ...], **kw: object):  # type: ignore[no-untyped-def]
    ex = extract(viz(*rows), m, today=TODAY)
    cls = classify_mrz(m) if m else classify_text([s.text for s in viz(*rows).slots])
    return consolidate(
        mrz=m,
        extraction=ex,
        classification=cls,
        today=TODAY,
        threshold=0.95,
        **kw,  # type: ignore[arg-type]
    )


def test_proven_document_is_auto_validated_when_specimens_allowed() -> None:
    r = _consolidate(mrz(TD3_VALID), PASSPORT, allow_specimen=True)
    assert r.status is DocState.AUTO_VALIDATED, [x.code for x in r.reasons]
    assert r.fields["surname"].value == "ÉRIKSSON"
    assert r.fields["surname"].status is FieldState.OK
    assert r.fields["date_of_birth"].value == "1974-08-12"


def test_specimen_is_never_auto_validated_by_default() -> None:
    r = _consolidate(mrz(TD3_VALID), PASSPORT)
    assert r.status is DocState.TO_REVIEW
    assert "SPECIMEN_DOCUMENT" in [x.code for x in r.reasons]


def test_single_mrz_engine_stays_to_review() -> None:
    r = _consolidate(mrz(TD3_VALID, ("ppocr",)), PASSPORT, allow_specimen=True)
    assert r.status is DocState.TO_REVIEW


def test_without_mrz_never_auto_validated() -> None:
    r = _consolidate(None, PASSPORT, allow_specimen=True)
    assert r.status is DocState.TO_REVIEW
    codes = [x.code for x in r.reasons]
    assert "NO_MRZ" in codes or "MRZ_NOT_FOUND" in codes
    assert r.fields["surname"].status is FieldState.UNCERTAIN


def test_conflict_blocks_validation() -> None:
    rows = tuple(("DUPONTEL", x, y) if t == "ÉRIKSSON" else (t, x, y) for t, x, y in PASSPORT)
    r = _consolidate(mrz(TD3_VALID), rows, allow_specimen=True)
    assert r.status is DocState.TO_REVIEW
    assert r.fields["surname"].status is FieldState.CONFLICT


def test_expired_document_alert() -> None:
    r = _consolidate(mrz(TD3), PASSPORT, allow_specimen=True)
    assert "EXPIRED" in r.alerts


def test_slot_best_reading() -> None:
    s = Slot(
        id=0,
        quad=quad(0, 0, 10),
        readings=[
            VizReading(engine="a", text="X", confidence=0.4),
            VizReading(engine="b", text="Y", confidence=0.9),
        ],
    )
    assert s.text == "Y"
