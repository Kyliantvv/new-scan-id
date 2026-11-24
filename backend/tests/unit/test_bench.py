"""`idreader bench --set synthetic` sur un petit jeu."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from idreader.bench.runner import bench_synthetic, markdown, representativeness
from idreader.cli import app
from idreader.core.config import Settings
from tests.unit.pipeline.test_mrz_stage import FONT, TD3, ScriptedEngine

needs_font = pytest.mark.skipif(not FONT.is_file(), reason="police OCR-B absente")


@needs_font
def test_bench_report_lists_engines_and_warning(settings: Settings, tmp_path: Path) -> None:
    s = settings.model_copy(update={"models_dir": FONT.parents[1]})
    engine = ScriptedEngine("tesseract", TD3)  # lit toujours le spécimen : tout est faux
    json_path, md_path, payload = bench_synthetic(
        s, [engine], count=3, seed=1, cases_dir=tmp_path / "cases", results_dir=tmp_path / "res"
    )
    assert payload["environment"]["engines_active"] == ["tesseract"]
    assert "vision" in payload["environment"]["engines_missing"]
    assert payload["warning"] and "non représentatifs" in payload["warning"]
    assert payload["summary"]["cases"] == 3
    assert payload["summary"]["mrz_exact_pct"] == 0.0
    md = md_path.read_text(encoding="utf-8")
    assert "Moteurs actifs : tesseract" in md and "aucun poids ni seuil" in md
    assert json.loads(json_path.read_text(encoding="utf-8"))["set"] == "synthetic"
    # le rapport ne contient que des identifiants et des indicateurs, aucune ligne MRZ
    assert "<<" not in md


def test_representativeness() -> None:
    env = {
        "system": "Darwin",
        "engines_active": ["vision", "tesseract", "ppocr"],
        "engines_missing": [],
    }
    assert representativeness(env) is None
    env = {
        "system": "Windows",
        "engines_active": ["ppocr"],
        "engines_missing": ["vision", "tesseract"],
    }
    text = representativeness(env)
    assert text is not None and "Windows" in text and "Apple Vision" in text and "tesseract" in text


def test_markdown_comparison() -> None:
    base = {
        "summary": {"mrz_exact_pct": 50.0},
        "git": "abc",
        "started_at": "2026-11-01T00:00:00+00:00",
    }
    summary = {
        "mrz_found_pct": 100.0, "mrz_decoded_pct": 100.0, "mrz_exact_pct": 60.0,
        "mrz_exact_before_repair_pct": 10.0, "checksums_valid_pct": 90.0, "automation_pct": 0.0,
        "auto_precision_pct": None, "silent_errors": [], "p50_ms": 1.0, "p95_ms": 2.0,
    }  # fmt: skip
    payload = {
        "summary": summary, "environment": {"platform": "x", "python": "3.12", "engines_active": [],
        "engines_missing": []}, "warning": None, "started_at": "2026-11-25T00:00:00+00:00",
        "git": "def", "seed": 1, "count": 1, "reference_date": "2026-11-25",
        "auto_validate_threshold": 0.95, "by_quality": {}, "by_format": {},
        "field_accuracy_pct": {}, "failures": {}, "reasons": {}, "confusions": [],
        "stage_ms_p50": {}, "peak_rss_mb": None,
    }  # fmt: skip
    md = markdown(payload, base)
    assert "60,0 (+10,0)" in md and "≥ 0,95" in md


def test_cli_bench_rejects_golden_in_phase_1a(settings: Settings) -> None:
    result = CliRunner().invoke(app, ["bench", "--set", "golden"])
    assert result.exit_code == 2
