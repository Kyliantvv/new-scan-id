"""`idreader diagnose` : drapeaux, étapes en cause, et surtout l'absence de donnée personnelle.

Documents synthétiques UTO uniquement. Le test de fuite ignore si les modèles sont absents.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

import pytest
from typer.testing import CliRunner

from idreader.cli import app
from idreader.core.config import get_settings
from idreader.core.db import make_engine, make_session_factory
from idreader.diagnose import (
    break_stage,
    diagnose_batch,
    render_text,
    shape_of,
    source_tag,
    value_flags,
)
from idreader.worker.main import Worker
from tests.helpers import make_batch_with_file
from tests.unit.pipeline.test_analyze import FONTS, VIZ

# --- Fonctions pures ----------------------------------------------------------------


def test_shape_hides_letters() -> None:
    assert shape_of("(6Z)") == "(9A)"
    assert shape_of("e napes 12") == "a_a_9"
    assert shape_of("MARTIN-DUPONT") == "A-A"


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("given_names", "(6Z)", {"NUMBERING", "DIGITS", "ODD_CHAR"}),
        ("given_names", "Prénoms (2)", {"NUMBERING", "DIGITS", "LOWERCASE", "ODD_CHAR"}),
        ("surname", "P", {"SHORT", "DOC_CODE"}),
        ("given_names", "e napes 12", {"DIGITS", "LOWERCASE"}),
        ("given_names", "DUPONT ÉP. MARTIN", {"USAGE_MARK", "ODD_CHAR"} - {"ODD_CHAR"}),
        ("surname", "MARTIN", set()),
        ("given_names", "JEAN, PAUL", set()),
        ("date_of_birth", "12/03/1980", {"DATE_FORMAT"}),
        ("date_of_birth", "1980-03-12", set()),
    ],
)
def test_value_flags(name: str, value: str, expected: set[str]) -> None:
    flags = set(value_flags(name, value, "P<"))
    assert expected <= flags
    if not expected:
        assert not flags


def test_label_word_flag() -> None:
    assert "LABEL_WORD" in value_flags("surname", "Nom", None)


def test_source_tag() -> None:
    mrz = {"src": "mrz"}
    viz = {"src": "viz", "method": "mrz_guided"}
    assert source_tag("ok", [mrz, viz]) == "mrz+viz:mrz_guided"
    assert source_tag("uncertain", [viz]) == "viz:mrz_guided"
    assert source_tag("manual", [mrz]) == "human"
    assert source_tag("missing", []) == "none"


def _stage(mrz, viz_slots=10, doc_type="passport", side="front", flags=None) -> str:
    return break_stage(
        mrz=mrz, viz_slots=viz_slots, doc_type=doc_type, side=side, field_flags=flags or {}
    )


def test_break_stage_order() -> None:
    assert _stage(None).startswith("?")
    assert _stage({"failure": "NO_ENGINE"}).startswith("E: aucun moteur")
    assert _stage({"failure": "MRZ_NOT_FOUND"}) == "E.1 MRZ non localisée"  # passeport
    assert "recto" in _stage({"failure": "MRZ_NOT_FOUND"}, doc_type="id_card", side="front")
    assert _stage({"failure": "MRZ_NOT_FOUND"}, doc_type="id_card", side="back") == (
        "E.1 MRZ non localisée"
    )
    assert _stage({"failure": "MRZ_UNREADABLE"}).startswith("E.2-E.4")
    bad = {"failure": None, "result": {"checksums_valid": False}}
    assert _stage(bad) == "E.5 sommes de contrôle"
    ok = {"failure": None, "result": {"checksums_valid": True}}
    assert _stage(ok, viz_slots=0).startswith("G:")
    assert _stage(ok, flags={"surname": ["SHORT"]}) == "H/L: surname"
    assert _stage(ok) == "aucune"


# --- Lot synthétique : aucune donnée personnelle dans la sortie ----------------------

needs_models = pytest.mark.skipif(VIZ is None or not FONTS, reason="modèles absents")


@pytest.fixture
def real_settings(migrated, monkeypatch):
    monkeypatch.setenv("IDR_DUMMY_PIPELINE", "false")
    monkeypatch.setenv("IDR_ALLOW_SPECIMEN", "true")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@needs_models
def test_no_personal_data_in_output(real_settings, tmp_path) -> None:
    from idreader.bench.documents import generate_documents

    cases = generate_documents(
        tmp_path / "gen", count=8, seed=5, models_dir=real_settings.models_dir,
        today=date.today(), a4_share=0,
    )  # fmt: skip
    engine = make_engine(real_settings)
    factory = make_session_factory(engine)
    case = next(c for c in cases if c.kind == "passport" and len(c.images) == 1)
    content = (tmp_path / "gen" / case.images[0]).read_bytes()
    batch_id, _, _ = make_batch_with_file(factory, real_settings, content)
    Worker(real_settings, engine, "w").run(exit_when_idle=True)

    with factory() as db:
        diag = diagnose_batch(db, real_settings, batch_id)
    text = render_text(diag)
    as_json = diag.model_dump_json()

    assert diag.files == 1 and diag.crops >= 1 and len(diag.documents) >= 1
    assert diag.documents[0].issuing_state == "UTO"
    assert diag.documents[0].crops[0].mrz_found
    assert "mrz" in {f.source.split("+")[0] for f in diag.documents[0].fields.values()}

    secrets = {v for v in case.fields.values() if v and len(v) >= 4}
    secrets |= {line for line in case.mrz_lines if line}
    secrets |= {line.replace("<", "") for line in case.mrz_lines if len(line) > 8}
    secrets |= {"synthetique.png", "Lot synthétique"}
    assert secrets
    for secret in secrets:
        assert secret not in text, "valeur personnelle dans la sortie texte"
        assert secret not in as_json, "valeur personnelle dans la sortie JSON"
    # Aucune date ISO (naissance, délivrance, expiration) ni numéro long non plus.
    import re

    assert not re.search(r"\b\d{4}-\d{2}-\d{2}\b", text)
    assert not re.search(r"\b[A-Z0-9]{8,}\b", text.replace(batch_id, ""))

    # Tous les jobs du lot sont listés (l'appariement se programme tout seul).
    assert any(j.type == "pair_batch" for j in diag.jobs)

    # CLI : texte, JSON, lot inconnu.
    runner = CliRunner()
    res = runner.invoke(app, ["diagnose", "--batch", batch_id])
    assert res.exit_code == 0 and "Diagnostic du lot" in res.output
    res = runner.invoke(app, ["diagnose", "--batch", batch_id, "--json"])
    assert res.exit_code == 0 and '"documents"' in res.output
    res = runner.invoke(app, ["diagnose", "--batch", "inconnu"])
    assert res.exit_code == 1
    engine.dispose()


def test_summary_counts_are_aggregates() -> None:
    from idreader.diagnose import summarize

    out = summarize([], Counter({("process_file", "done", None): 2}))
    assert out["job"] == {"process_file/done": 2}
