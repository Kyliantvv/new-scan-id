"""E.2 normalisation et E.7 noms (fonctions pures, sans image)."""

from __future__ import annotations

import pytest

from idreader.pipeline.mrz.names import mrz_forms, names_match, split_name
from idreader.pipeline.mrz.normalize import fix_filler_k, normalize_text, realign


def test_normalize_chevrons_spaces_and_accents() -> None:
    assert normalize_text("p«uto eriksson‹‹anna") == "P<UTOERIKSSON<<ANNA"
    assert normalize_text("Ｌ８９８ ９０２") == "L898902"
    assert normalize_text("É|$") == "EIS"
    assert normalize_text("A.B,C*") == "ABC"


def test_filler_k() -> None:
    assert fix_filler_k("ANNA<MARIA<<<<K<<<<") == "ANNA<MARIA<<<<<<<<<"
    assert fix_filler_k("MARIA<<<<<<<<K") == "MARIA<<<<<<<<<"
    # prénom d'une lettre après le séparateur : jamais touché
    assert fix_filler_k("SMITH<<K<<<<<<") == "SMITH<<K<<<<<<"
    assert fix_filler_k("KARL<<KEVIN<<<") == "KARL<<KEVIN<<<"


def test_realign() -> None:
    line = "L898902C36UTO7408122F1204159ZE184226B<<<<<10"
    assert realign(line.replace("<<<<<", "<<<<"), 44) == line
    assert realign(line.replace("<<<<<", "<<<<<<<"), 44) == line
    assert realign(line + "<<<", 44) is None
    assert realign("ABCDEFGHIJ", 12) is None  # pas de remplissage où insérer


def test_split_name() -> None:
    assert split_name("ERIKSSON<<ANNA<MARIA<<<<<<<<") == ("ERIKSSON", "ANNA MARIA", False)
    assert split_name("DE<LA<CRUZ<<JUAN<<<") == ("DE LA CRUZ", "JUAN", False)
    assert split_name("ONLYSURNAME<<<<") == ("ONLYSURNAME", "", False)
    assert split_name("LONGSURNAME<<FIRSTNAMEX") == ("LONGSURNAME", "FIRSTNAMEX", True)


@pytest.mark.parametrize(
    ("mrz", "viz"),
    [
        ("MUELLER", "Müller"),
        ("MULLER", "Müller"),
        ("SOEREN", "Søren"),
        ("STRASSE", "Straße"),
        ("JOSE MARIA", "José-María"),
        ("ONEILL", "O'Neill"),
        ("FRANCOISE", "Françoise"),
        ("LUKASZ", "Łukasz"),
    ],
)
def test_transliteration_variants(mrz: str, viz: str) -> None:
    assert names_match(mrz, viz)


def test_transliteration_mismatch_and_truncation() -> None:
    assert not names_match("MILLER", "Müller")
    assert "AASE" in mrz_forms("Åse") and "ASE" in mrz_forms("Åse")
    assert names_match("WOLFESCHLEGELSTEIN", "Wolfeschlegelsteinhausen", truncated=True)
    assert not names_match("WOLFESCHLEGELSTEIN", "Wolfeschlegelsteinhausen")


def test_non_latin_names() -> None:
    assert names_match("IVANOV", "Иванов")
    assert not names_match("PETROV", "Иванов")
