"""Banc `documents` : pipeline complet (A→L) sur les documents synthétiques UTO.

Mesure, par champ, l'exactitude des valeurs finales (accents compris), la détection des
documents, les statuts et la précision de l'auto-validation. Les documents UTO sont des
spécimens : le banc lève le motif SPECIMEN_DOCUMENT (`allow_specimen`) pour mesurer le reste.

Tant que l'appariement recto/verso (étape J, phase 4) n'existe pas, la valeur retenue pour
un champ est celle du recadrage le plus confiant parmi ceux du cas (appariement idéal).
"""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from idreader.bench.documents import DocCase, generate_documents, load_doc_cases
from idreader.bench.runner import (
    _delta,
    _fmt,
    _git_sha,
    _pct,
    environment,
    representativeness,
    write_lf,
)
from idreader.core.config import REPO_ROOT, Settings
from idreader.pipeline.analyze import CropAnalysis, Engines, analyze_file
from idreader.pipeline.consolidate import MANDATORY, OUTPUT_FIELDS, DocState, FieldState

PROVEN = {FieldState.OK, FieldState.REPAIRED}


@dataclass
class DocResult:
    case_id: str
    kind: str
    quality: str
    layout: str
    crops_expected: int
    crops_found: int
    statuses: list[str]
    auto_validated: bool
    silent_errors: list[str]  # champs faux dans un recadrage validé automatiquement
    fields_correct: dict[str, bool] = field(default_factory=dict)
    accented: dict[str, bool] = field(default_factory=dict)  # noms avec diacritiques
    names_plain: dict[str, bool] = field(default_factory=dict)  # noms justes, accents ignorés
    reasons: list[str] = field(default_factory=list)
    ms: float = 0.0


def _merged(crops: list[CropAnalysis]) -> dict[str, str | None]:
    best: dict[str, tuple[float, str | None]] = {}
    for ca in crops:
        for name, f in ca.result.fields.items():
            if f.value and (name not in best or f.confidence > best[name][0]):
                best[name] = (f.confidence, f.value)
    return {k: v for k, (_, v) in best.items()}


def _plain(text: str | None) -> str:
    """Majuscules sans diacritiques, espaces simples (04_EVALUATION.md : compte sans accents)."""
    import unicodedata

    base = unicodedata.normalize("NFKD", text or "")
    return " ".join("".join(c for c in base if not unicodedata.combining(c)).upper().split())


def run_doc_case(
    case: DocCase, cases_dir: Path, engines: Engines, today: date, threshold: float
) -> DocResult:
    t0 = time.perf_counter()
    crops: list[CropAnalysis] = []
    for name in case.images:
        fa = analyze_file(
            cases_dir / name, engines, today=today, threshold=threshold, allow_specimen=True
        )
        crops.extend(fa.crops)
    ms = (time.perf_counter() - t0) * 1000
    merged = _merged(crops)
    truth = case.fields
    correct = {k: merged.get(k) == truth.get(k) for k in OUTPUT_FIELDS if truth.get(k)}
    accented = {
        k: correct[k]
        for k in ("surname", "given_names")
        if truth.get(k) and not str(truth[k]).isascii()
    }
    names_plain = {
        k: _plain(merged.get(k)) == _plain(truth.get(k))
        for k in ("surname", "given_names")
        if truth.get(k)
    }
    silent: list[str] = []
    for ca in crops:
        if ca.result.status is not DocState.AUTO_VALIDATED:
            continue
        for name, f in ca.result.fields.items():
            if f.status in PROVEN and f.value and truth.get(name) and f.value != truth[name]:
                silent.append(name)
    return DocResult(
        case_id=case.case_id,
        kind=case.kind,
        quality=case.quality,
        layout=case.layout,
        crops_expected=1 if case.kind == "passport" else 2,
        crops_found=len(crops),
        statuses=[ca.result.status.value for ca in crops],
        auto_validated=any(ca.result.status is DocState.AUTO_VALIDATED for ca in crops),
        silent_errors=silent,
        fields_correct=correct,
        accented=accented,
        names_plain=names_plain,
        reasons=sorted({r.code for ca in crops for r in ca.result.reasons}),
        ms=ms,
    )


def summarize_docs(results: list[DocResult]) -> dict[str, Any]:
    n = len(results)
    mandatory = [r for r in results if all(r.fields_correct.get(k, True) for k in MANDATORY)]
    auto = [r for r in results if r.auto_validated]
    times = sorted(r.ms for r in results)
    accented = [v for r in results for v in r.accented.values()]
    return {
        "cases": n,
        "detection_ok_pct": _pct(sum(r.crops_found == r.crops_expected for r in results), n),
        "mandatory_all_correct_pct": _pct(len(mandatory), n),
        "accented_names_correct_pct": _pct(sum(accented), len(accented)),
        "names_correct_no_accents_pct": _pct(
            sum(v for r in results for v in r.names_plain.values()),
            sum(len(r.names_plain) for r in results),
        ),
        "automation_pct": _pct(len(auto), n),
        "auto_precision_pct": _pct(sum(not r.silent_errors for r in auto), len(auto)),
        "silent_errors": [r.case_id for r in results if r.silent_errors],
        "p50_ms": round(statistics.median(times), 1) if times else None,
        "p95_ms": round(times[int(0.95 * (len(times) - 1))], 1) if times else None,
    }


def bench_documents(
    settings: Settings,
    engines: Engines,
    *,
    count: int,
    seed: int,
    cases_dir: Path | None = None,
    results_dir: Path | None = None,
    compare: bool = True,
) -> tuple[Path, Path, dict[str, Any]]:
    cases_dir = cases_dir or settings.data_dir / "bench" / "documents" / f"seed{seed}_n{count}"
    results_dir = results_dir or REPO_ROOT / "bench" / "results"
    if not (cases_dir / "truth.json").is_file():
        generate_documents(
            cases_dir,
            count=count,
            seed=seed,
            models_dir=settings.models_dir,
            today=date(2026, 11, 25),
        )
    cases, today = load_doc_cases(cases_dir)
    threshold = settings.auto_validate_threshold
    started = datetime.now(UTC)
    results = [run_doc_case(c, cases_dir, engines, today, threshold) for c in cases]
    env = environment([*engines.mrz, *engines.viz], settings)
    env["engines_active"] = engines.names
    env["engines_missing"] = [n for n in ("vision", "tesseract", "ppocr") if n not in engines.names]
    field_acc = {
        k: _pct(
            sum(r.fields_correct[k] for r in results if k in r.fields_correct),
            sum(k in r.fields_correct for r in results),
        )
        for k in OUTPUT_FIELDS
    }
    groups: dict[str, dict[str, Any]] = {}
    for key in ("kind", "quality", "layout"):
        values = sorted({getattr(r, key) for r in results})
        for v in values:
            groups[f"{key}={v}"] = summarize_docs([r for r in results if getattr(r, key) == v])
    payload: dict[str, Any] = {
        "set": "documents",
        "started_at": started.isoformat(timespec="seconds"),
        "git": _git_sha(),
        "seed": seed,
        "count": len(cases),
        "reference_date": today.isoformat(),
        "auto_validate_threshold": threshold,
        "environment": env,
        "warning": representativeness(env),
        "summary": summarize_docs(results),
        "groups": groups,
        "field_accuracy_pct": field_acc,
        "statuses": dict(Counter(s for r in results for s in r.statuses)),
        "reasons": dict(Counter(x for r in results for x in r.reasons)),
        "cases_detail": [asdict(r) for r in results],
    }
    results_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{started:%Y-%m-%d}_{payload['git']}_documents"
    previous = sorted(p for p in results_dir.glob("*_documents.json") if p.stem != stem)
    json_path = results_dir / f"{stem}.json"
    write_lf(json_path, json.dumps(payload, indent=1, ensure_ascii=False))
    baseline = (
        json.loads(previous[-1].read_text(encoding="utf-8")) if compare and previous else None
    )
    md_path = results_dir / f"{stem}.md"
    write_lf(md_path, markdown_docs(payload, baseline))
    return json_path, md_path, payload


FIELD_LABELS = {
    "surname": "Nom",
    "given_names": "Prénoms",
    "usage_name": "Nom d'usage",
    "sex": "Sexe",
    "date_of_birth": "Date de naissance",
    "place_of_birth": "Lieu de naissance",
    "nationality": "Nationalité",
    "document_number": "N° de document",
    "date_of_issue": "Date de délivrance",
    "date_of_expiry": "Date d'expiration",
    "authority": "Autorité",
    "personal_number": "N° personnel",
    "issuing_state": "État émetteur",
}


def markdown_docs(p: dict[str, Any], baseline: dict[str, Any] | None = None) -> str:
    s, env = p["summary"], p["environment"]
    b = baseline["summary"] if baseline else {}
    out = [f"# Banc documents — pipeline complet — {p['started_at'][:10]}", ""]
    if p["warning"]:
        out += [f"> ⚠️ {p['warning']}", ""]
    out += [
        f"- Commit : `{p['git']}` · graine {p['seed']} · {p['count']} documents UTO synthétiques"
        f" · date de référence {p['reference_date']}",
        f"- Plate-forme : {env['platform']} · Python {env['python']}",
        f"- **Moteurs actifs : {', '.join(env['engines_active']) or 'aucun'}**"
        + (f" · absents : {', '.join(env['engines_missing'])}" if env["engines_missing"] else ""),
        "- Spécimens UTO : motif SPECIMEN_DOCUMENT levé pour la mesure. Sans appariement"
        " (phase 4), chaque champ prend la valeur du recadrage le plus confiant du cas.",
    ]
    if baseline:
        out.append(f"- Comparaison : `{baseline['git']}` du {baseline['started_at'][:10]}")
    out += ["", "## Indicateurs", "", "| Indicateur | Valeur |", "|---|---|"]
    rows = [
        ("Documents détectés au bon nombre (%)", "detection_ok_pct"),
        ("**Champs obligatoires tous justes (%)**", "mandatory_all_correct_pct"),
        ("Noms accentués justes, accents compris (%)", "accented_names_correct_pct"),
        ("Noms justes sans tenir compte des accents (%)", "names_correct_no_accents_pct"),
        ("Automatisation (%)", "automation_pct"),
        ("**Précision de l'auto-validation (%)**", "auto_precision_pct"),
        ("Temps p50 par document (ms)", "p50_ms"),
        ("Temps p95 par document (ms)", "p95_ms"),
    ]
    for label, key in rows:
        out.append(f"| {label} | {_fmt(s[key])}{_delta(s[key], b.get(key))} |")
    out.append(f"| Erreurs auto-validées (doit être 0) | {len(s['silent_errors'])} |")
    out += ["", "## Exactitude par champ", "", "| Champ | Juste (%) |", "|---|---|"]
    for k, v in p["field_accuracy_pct"].items():
        if v is not None:
            out.append(f"| {FIELD_LABELS.get(k, k)} | {_fmt(v)} |")
    out += [
        "",
        "## Par groupe",
        "",
        "| Groupe | Cas | Obligatoires justes (%) | Accents (%) | Auto (%) |",
        "|---|---|---|---|---|",
    ]
    for g, v in p["groups"].items():
        out.append(
            f"| {g} | {v['cases']} | {_fmt(v['mandatory_all_correct_pct'])} |"
            f" {_fmt(v['accented_names_correct_pct'])} | {_fmt(v['automation_pct'])} |"
        )
    out += ["", "## Statuts des recadrages", ""]
    out += [f"- {k} : {n}" for k, n in sorted(p["statuses"].items())]
    out += ["", "## Motifs « À vérifier » les plus fréquents", ""]
    top = sorted(p["reasons"].items(), key=lambda kv: -kv[1])[:15]
    out += [f"- `{k}` : {n}" for k, n in top]
    if s["silent_errors"]:
        out += ["", "## Erreurs auto-validées", ""]
        out += [f"- {c}" for c in s["silent_errors"]]
    return "\n".join(out) + "\n"
