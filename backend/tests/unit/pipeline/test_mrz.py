"""Étape E : vecteurs publics de 03_PIPELINE_ANALYSE.md § E.8 et cas de réparation.

Uniquement des spécimens ICAO publiés et des MRZ synthétiques.
"""

from __future__ import annotations

from datetime import date

import pytest

from idreader.pipeline.mrz.checkdigit import (
    check_digit_ok,
    classes,
    compute_check_digit,
    is_blind_spot,
    same_class,
)
from idreader.pipeline.mrz.decode import MrzFieldStatus, MrzResult, birth_date, decode
from idreader.pipeline.mrz.formats import MrzFormat, detect_format
from idreader.pipeline.mrz.synth import MrzData, build
from idreader.pipeline.mrz.vote import BandReading, vote

TODAY = date(2026, 11, 24)

TD3 = [
    "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
    "L898902C36UTO7408122F1204159ZE184226B<<<<<10",
]
TD1 = [
    "I<UTOD231458907<<<<<<<<<<<<<<<",
    "7408122F1204159UTO<<<<<<<<<<<6",
    "ERIKSSON<<ANNA<MARIA<<<<<<<<<<",
]
FRA = [
    "IDFRABERTHIER<<<<<<<<<<<<<<<<<<<<<<<",
    "8806923102858CORINNE<<<<<<<6512068F6",
]


def decode_lines(*readings: tuple[str, list[str]], today: date = TODAY) -> MrzResult:
    voted = vote([BandReading(engine=e, lines=lines) for e, lines in readings])
    assert voted is not None
    result = decode(voted, today=today)
    assert result is not None
    return result


def one(lines: list[str]) -> MrzResult:
    return decode_lines(("tesseract", lines))


def two(lines: list[str]) -> MrzResult:
    return decode_lines(("tesseract", lines), ("ppocr", lines))


def values(r: MrzResult) -> dict[str, str | None]:
    return {k: f.value for k, f in r.fields.items()}


# --- E.5 sommes de contrôle -------------------------------------------------------------


def test_check_digits_of_icao_specimen() -> None:
    assert compute_check_digit("L898902C3") == 6
    assert compute_check_digit("740812") == 2
    assert compute_check_digit("120415") == 9
    assert compute_check_digit("ZE184226B<<<<<") == 1
    assert check_digit_ok("<<<<<<<<<<<<<<", "<", filler_allowed=True)
    assert not check_digit_ok("<<<<<<<<<<<<<<", "<")
    assert not check_digit_ok("ZE184226B<<<<<", "<", filler_allowed=True)


def test_indistinguishable_classes_match_spec_table() -> None:
    assert classes() == {
        0: "0AKU<",
        1: "1BLV",
        2: "2CMW",
        3: "3DNX",
        4: "4EOY",
        5: "5FPZ",
        6: "6GQ",
        7: "7HR",
        8: "8IS",
        9: "9JT",
    }
    for a, b in ("S8", "G6", "B1", "Z5", "U0", "K<"):
        assert same_class(a, b) and is_blind_spot(a, b)
    for a, b in ("O0", "I1", "Z2", "B8", "D0", "Q0", "S5"):
        assert not same_class(a, b)


# --- E.4 formats et vecteurs E.8 --------------------------------------------------------


def test_td3_specimen() -> None:
    r = two(TD3)
    assert r.format is MrzFormat.TD3
    assert r.checksums_valid and r.changes == []
    v = values(r)
    assert v["document_number"] == "L898902C3"
    assert v["nationality"] == "UTO"
    assert v["date_of_birth"] == "1974-08-12"
    assert v["sex"] == "F"
    assert v["date_of_expiry"] == "2012-04-15"
    assert v["personal_number"] == "ZE184226B"
    assert v["surname"] == "ERIKSSON"
    assert v["given_names"] == "ANNA MARIA"
    assert r.fields["document_number"].status is MrzFieldStatus.VALID
    assert r.fields["date_of_birth"].status is MrzFieldStatus.VALID
    # UTO (Utopie) est le code de spécimen ICAO, absent de la liste des pays
    assert (
        "MRZ_UNKNOWN_COUNTRY" not in r.reasons
    )  # UTO : code des spécimens ICAO (motif SPECIMEN à l'étape L)


def test_td1_specimen() -> None:
    r = two(TD1)
    assert r.format is MrzFormat.TD1
    assert r.checksums_valid and r.changes == []
    v = values(r)
    assert v["document_number"] == "D23145890"
    assert v["date_of_birth"] == "1974-08-12"
    assert v["sex"] == "F"
    assert v["date_of_expiry"] == "2012-04-15"
    assert v["surname"] == "ERIKSSON"
    assert v["given_names"] == "ANNA MARIA"


def test_old_french_id_card_specimen() -> None:
    r = two(FRA)
    assert r.format is MrzFormat.FRA_ID
    assert r.checksums_valid and r.changes == []
    v = values(r)
    assert v["document_number"] == "880692310285"
    assert v["given_names"] == "CORINNE"
    assert v["surname"] == "BERTHIER"
    assert v["date_of_birth"] == "1965-12-06"
    assert v["sex"] == "F"
    assert r.fields["document_number"].status is MrzFieldStatus.VALID
    assert r.reasons == []


SYNTH = MrzData(
    document_code="P",
    issuing_state="FRA",
    surname="DUPONT",
    given_names="JEAN",
    document_number="12AB34567",
    nationality="FRA",
    date_of_birth="900131",
    sex="M",
    date_of_expiry="300130",
)


@pytest.mark.parametrize(
    ("fmt", "code", "shape"),
    [
        (MrzFormat.TD3, "P", (2, 44)),
        (MrzFormat.MRVA, "V", (2, 44)),
        (MrzFormat.TD2, "I", (2, 36)),
        (MrzFormat.MRVB, "V", (2, 36)),
        (MrzFormat.TD1, "I", (3, 30)),
    ],
)
def test_synthetic_formats_round_trip(fmt: MrzFormat, code: str, shape: tuple[int, int]) -> None:
    data = MrzData(**{**SYNTH.__dict__, "document_code": code})
    lines = build(fmt, data)
    assert (len(lines), len(lines[0])) == shape
    assert detect_format(lines) is fmt
    r = two(lines)
    assert r.format is fmt and r.checksums_valid
    assert ("composite" in r.checks) is (fmt not in (MrzFormat.MRVA, MrzFormat.MRVB))
    v = values(r)
    assert v["document_number"] == "12AB34567"
    assert v["date_of_birth"] == "1990-01-31"
    assert v["date_of_expiry"] == "2030-01-30"
    assert v["surname"] == "DUPONT" and v["given_names"] == "JEAN"
    assert r.reasons == []


@pytest.mark.parametrize("fmt", [MrzFormat.TD1, MrzFormat.TD2])
def test_long_document_number(fmt: MrzFormat) -> None:
    data = MrzData(**{**SYNTH.__dict__, "document_code": "I", "document_number": "AB1234567890"})
    lines = build(fmt, data)
    r = two(lines)
    assert r.checksums_valid
    assert r.fields["document_number"].value == "AB1234567890"
    assert r.fields["document_number"].status is MrzFieldStatus.VALID


def test_td2_is_not_mistaken_for_old_french_card() -> None:
    lines = build(MrzFormat.TD2, MrzData(**{**SYNTH.__dict__, "document_code": "ID"}))
    assert lines[0].startswith("IDFRA")
    assert detect_format(lines) is MrzFormat.TD2


# --- E.6 réparation ---------------------------------------------------------------------


def test_letter_o_in_date_is_repaired_and_proven() -> None:
    bad = [TD3[0], TD3[1].replace("7408122", "74O8122")]
    r = two(bad)
    assert r.checksums_valid
    assert r.lines == TD3
    dob = r.fields["date_of_birth"]
    assert dob.value == "1974-08-12"
    assert dob.status is MrzFieldStatus.REPAIRED
    assert "check_digit" in dob.evidence
    assert [c.reason for c in r.changes] == ["type_projection"]


def test_blind_spot_s_for_8_stays_to_review() -> None:
    """`8` lu `S` dans le n° : même classe, la somme reste juste, rien d'autre ne tranche."""
    bad = [TD3[0], TD3[1].replace("L898902C3", "LS98902C3")]
    r = one(bad)
    assert r.checksums_valid  # la somme ne voit rien…
    num = r.fields["document_number"]
    assert num.value == "LS98902C3"
    assert num.status is MrzFieldStatus.TO_REVIEW  # … donc « À vérifier »
    assert "MRZ_BLIND_SPOT" in num.reasons
    assert "L898902C3" in num.alternatives
    assert r.needs_review


def test_blind_spot_with_engines_disagreeing_stays_to_review() -> None:
    bad = [TD3[0], TD3[1].replace("L898902C3", "LS98902C3")]
    r = decode_lines(("tesseract", bad), ("ppocr", TD3))  # tesseract l'emporte (poids 0,9)
    assert r.fields["document_number"].value == "LS98902C3"
    assert r.fields["document_number"].status is MrzFieldStatus.TO_REVIEW


def test_blind_spot_confirmed_by_unanimous_engines() -> None:
    """Deux moteurs différents d'accord : « autre preuve » au sens de E.5."""
    r = two(TD3)
    assert r.fields["document_number"].status is MrzFieldStatus.VALID
    assert "vote_unanimous" not in r.fields["document_number"].evidence  # preuve = somme


def test_single_engine_cannot_prove_ambiguous_characters() -> None:
    r = one(TD3)
    assert r.checksums_valid
    assert r.fields["document_number"].status is MrzFieldStatus.TO_REVIEW
    assert r.fields["date_of_birth"].status is MrzFieldStatus.VALID  # chiffres seulement


def test_country_pattern_resolves_blind_spots() -> None:
    lines = build(MrzFormat.TD3, SYNTH)  # passeport FRA : \d{2}[A-Z]{2}\d{5}
    r = one(lines)
    assert r.fields["document_number"].status is MrzFieldStatus.VALID


def test_wrong_digit_repaired_from_vote_alternative() -> None:
    bad = [TD3[0], TD3[1].replace("7408122", "7408172")]
    r = decode_lines(("tesseract", bad), ("ppocr", TD3))
    assert r.checksums_valid and r.lines == TD3
    assert r.fields["date_of_birth"].status is MrzFieldStatus.REPAIRED
    assert [c.reason for c in r.changes] == ["check_repair"]


def test_unrepairable_check_digit_is_invalid() -> None:
    bad = [TD3[0], TD3[1].replace("7408122", "7408132")]
    r = one(bad)
    assert not r.checksums_valid
    assert r.fields["date_of_birth"].status is MrzFieldStatus.INVALID
    assert "MRZ_CHECK_FAILED" in r.reasons


def test_ambiguous_repair_stays_to_review() -> None:
    """`0` lu `D` dans le n° : `0` et `U` valident tous deux la somme."""
    bad = [TD3[0], TD3[1].replace("L898902C3", "L8989D2C3")]
    r = two(bad)
    num = r.fields["document_number"]
    assert num.status is MrzFieldStatus.TO_REVIEW
    assert {"MRZ_REPAIR_AMBIGUOUS", "MRZ_BLIND_SPOT"} & set(num.reasons)


def test_line_short_by_one_filler_is_realigned() -> None:
    short = [TD3[0], TD3[1].replace("B<<<<<1", "B<<<<1")]
    assert len(short[1]) == 43
    r = two(short)
    assert r.lines == TD3 and r.checksums_valid


def test_composite_failure_reported() -> None:
    bad = [TD3[0], TD3[1][:-1] + "7"]
    r = one(bad)
    assert not r.checks["composite"]
    assert "MRZ_COMPOSITE_FAILED" in r.reasons


# --- E.6.5 siècle et contrôles sémantiques ----------------------------------------------


def test_century_rule() -> None:
    assert birth_date("740812", TODAY) == date(1974, 8, 12)
    assert birth_date("261124", TODAY) == date(2026, 11, 24)
    assert birth_date("261125", TODAY) == date(1926, 11, 25)
    assert birth_date("261125", TODAY, viz_year=2026) == date(2026, 11, 25)
    assert birth_date("740231", TODAY) is None


@pytest.mark.parametrize("dob", ["250101", "270101"])
def test_extreme_age_without_viz_is_to_review(dob: str) -> None:
    lines = build(MrzFormat.TD3, MrzData(**{**SYNTH.__dict__, "date_of_birth": dob}))
    r = two(lines)
    assert r.fields["date_of_birth"].status is MrzFieldStatus.TO_REVIEW
    assert "MRZ_CENTURY_UNCERTAIN" in r.reasons


def test_expiry_before_birth() -> None:
    data = MrzData(**{**SYNTH.__dict__, "date_of_birth": "150101", "date_of_expiry": "100101"})
    r = two(build(MrzFormat.TD3, data))
    assert "MRZ_EXPIRY_BEFORE_BIRTH" in r.reasons


def test_invalid_date() -> None:
    data = MrzData(**{**SYNTH.__dict__, "date_of_birth": "901331"})
    r = two(build(MrzFormat.TD3, data))
    assert r.fields["date_of_birth"].status is MrzFieldStatus.INVALID
    assert "MRZ_INVALID_DATE" in r.reasons


def test_truncated_name_alert() -> None:
    data = MrzData(**{**SYNTH.__dict__, "surname": "A" * 30, "given_names": "BERNADETTE"})
    r = two(build(MrzFormat.TD3, data))
    assert "MRZ_NAME_TRUNCATED" in r.alerts


# --- E.3 vote ---------------------------------------------------------------------------


def test_vote_majority_and_unanimity() -> None:
    bad = [TD3[0], TD3[1].replace("L898902C3", "L898902C8")]
    voted = vote(
        [
            BandReading(engine="tesseract", lines=TD3),
            BandReading(engine="ppocr", lines=TD3),
            BandReading(engine="vision", lines=bad, confidence=0.5),
        ]
    )
    assert voted is not None and voted.lines == TD3
    pos = voted.vote_at(1, 8)
    assert pos.char == "3" and "8" in pos.alternatives
    assert pos.engines_against == ["vision"] and not pos.unanimous
    assert voted.vote_at(1, 0).unanimous


def test_vote_ignores_noise_lines_and_picks_bottom_window() -> None:
    noisy = ["PASSPORT PASSEPORT", "UTOPIA REPUBLIC OF NOWHERE", *TD3]
    voted = vote([BandReading(engine="tesseract", lines=noisy)])
    assert voted is not None and voted.lines == TD3


def test_vote_without_mrz() -> None:
    assert vote([BandReading(engine="tesseract", lines=["bonjour"])]) is None
