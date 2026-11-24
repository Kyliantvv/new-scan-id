"""`idreader bench --set synthetic` : étape E sur le jeu synthétique, rapport JSON + Markdown.

Les rapports ne contiennent que des identifiants de cas, des noms de champs et des
indicateurs (04_EVALUATION.md). Le jeu synthétique ne contient aucune donnée réelle.
"""

from __future__ import annotations

import json
import platform
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from idreader import __version__
from idreader.bench.synthetic import Case, generate, load_cases
from idreader.core.config import REPO_ROOT, Settings
from idreader.engines.base import OcrEngine
from idreader.pipeline.mrz.decode import MrzFieldStatus, MrzResult, decode
from idreader.pipeline.mrz.stage import run_mrz
from idreader.pipeline.mrz.vote import BandReading, vote

MANDATORY = (
    "surname",
    "given_names",
    "date_of_birth",
    "sex",
    "nationality",
    "document_number",
    "date_of_expiry",
    "issuing_state",
)
COMPARED_FIELDS = (*MANDATORY, "personal_number")
NOT_REPRESENTATIVE = (
    "**Résultats non représentatifs** : banc lancé {why}. Ce chiffre sert de référence pour "
    "suivre les évolutions du code ; **aucun poids ni seuil définitif ne doit être calibré "
    "dessus** (docs/04_EVALUATION.md, docs/07_ROADMAP.md § Phase 1a)."
)


@dataclass
class CaseResult:
    case_id: str
    format: str
    quality: str
    found: bool
    decoded: bool
    exact: bool
    exact_read: bool  # lignes votées exactes avant réparation
    checksums_valid: bool
    auto_validated: bool
    fields_correct: dict[str, bool]
    failure: str | None
    reasons: list[str]
    ms_total: float
    ms_stage: dict[str, float]
    confusions: list[tuple[str, str]] = field(default_factory=list)


def truth_values(lines: list[str], today: date) -> dict[str, str | None]:
    voted = vote([BandReading(engine="a", lines=lines), BandReading(engine="b", lines=lines)])
    result = decode(voted, today=today) if voted else None
    if result is None:
        raise ValueError("vérité illisible")
    return {k: f.value for k, f in result.fields.items()}


def would_auto_validate(result: MrzResult, threshold: float) -> bool:
    """Approximation de l'étape L (phase 1b) à partir de la MRZ seule."""
    if not result.checksums_valid or result.reasons:
        return False
    for name in MANDATORY:
        f = result.fields.get(name)
        if f is None or f.value in (None, ""):
            return False
        if f.status not in (MrzFieldStatus.VALID, MrzFieldStatus.REPAIRED):
            return False
        if f.confidence < threshold:
            return False
    return True


def _confusions(read: list[str], truth: list[str]) -> list[tuple[str, str]]:
    if len(read) != len(truth) or any(len(a) != len(b) for a, b in zip(read, truth, strict=True)):
        return []
    return [
        (t, r)
        for a, b in zip(read, truth, strict=True)
        for r, t in zip(a, b, strict=True)
        if r != t
    ]


def run_case(
    case: Case, cases_dir: Path, engines: list[OcrEngine], today: date, threshold: float
) -> CaseResult:
    bgr = cv2.imread(str(cases_dir / case.image))
    if bgr is None:
        raise FileNotFoundError(case.image)
    img = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), dtype=np.uint8)
    t0 = time.perf_counter()
    stage = run_mrz(img, engines, today=today)
    ms = (time.perf_counter() - t0) * 1000
    r = stage.result
    truth = truth_values(case.lines, today)
    correct = {
        k: bool(r is not None and k in r.fields and r.fields[k].value == truth.get(k))
        for k in COMPARED_FIELDS
        if k in truth
    }
    return CaseResult(
        case_id=case.case_id,
        format=case.format,
        quality=case.quality,
        found=stage.band is not None,
        decoded=r is not None,
        exact=bool(r and r.lines == case.lines),
        exact_read=bool(r and r.lines_read == case.lines),
        checksums_valid=bool(r and r.checksums_valid),
        auto_validated=bool(r and would_auto_validate(r, threshold)),
        fields_correct=correct,
        failure=stage.failure,
        reasons=r.reasons if r else [],
        ms_total=ms,
        ms_stage=stage.ms,
        confusions=_confusions(r.lines_read, case.lines) if r else [],
    )


def _pct(n: int, d: int) -> float | None:
    return round(100 * n / d, 1) if d else None


def summarize(results: list[CaseResult]) -> dict[str, Any]:
    n = len(results)
    auto = [r for r in results if r.auto_validated]
    times = sorted(r.ms_total for r in results)
    return {
        "cases": n,
        "mrz_found_pct": _pct(sum(r.found for r in results), n),
        "mrz_decoded_pct": _pct(sum(r.decoded for r in results), n),
        "mrz_exact_pct": _pct(sum(r.exact for r in results), n),
        "mrz_exact_before_repair_pct": _pct(sum(r.exact_read for r in results), n),
        "checksums_valid_pct": _pct(sum(r.checksums_valid for r in results), n),
        "automation_pct": _pct(len(auto), n),
        "auto_precision_pct": _pct(sum(r.exact for r in auto), len(auto)),
        "silent_errors": [r.case_id for r in auto if not r.exact],
        "p50_ms": round(statistics.median(times), 1) if times else None,
        "p95_ms": round(times[min(len(times) - 1, int(0.95 * len(times)))], 1) if times else None,
    }


def _group(results: list[CaseResult], key: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[CaseResult]] = defaultdict(list)
    for r in results:
        groups[str(getattr(r, key))].append(r)
    return {k: summarize(v) for k, v in sorted(groups.items())}


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        sha = out.stdout.strip() or "nogit"
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", "backend/src", "scripts"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        ).stdout.strip()
        return sha + ("-dirty" if dirty else "")
    except OSError:
        return "nogit"


def _peak_rss_mb() -> float | None:
    if sys.platform == "win32":
        return None
    import resource  # POSIX seulement

    peak = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)  # macOS : octets


def environment(engines: list[OcrEngine], settings: Settings) -> dict[str, Any]:
    env: dict[str, Any] = {
        "platform": platform.platform(),
        "system": platform.system(),
        "macos": platform.mac_ver()[0] or None,
        "python": platform.python_version(),
        "idreader": __version__,
        "engines_active": [e.name for e in engines],
        "engines_missing": [
            n for n in ("vision", "tesseract", "ppocr") if n not in {e.name for e in engines}
        ],
    }
    for e in engines:
        if e.name == "tesseract":
            cmd = getattr(e, "command", settings.tesseract_cmd)
            try:
                out = subprocess.run(  # noqa: S603
                    [cmd, "--version"], capture_output=True, text=True, check=False, timeout=10
                )
                env["tesseract_version"] = (out.stdout or out.stderr).splitlines()[0]
            except (OSError, IndexError):
                env["tesseract_version"] = None
        if e.name == "ppocr":
            env["ppocr_model"] = "PP-OCRv6 small rec (ONNX)"
    return env


def representativeness(env: dict[str, Any]) -> str | None:
    reasons = []
    if env["system"] != "Darwin":
        reasons.append(f"sous {env['system']}, pas sur le Mac mini")
    if "vision" not in env["engines_active"]:
        reasons.append("sans Apple Vision (moteur n° 1)")
    missing = [m for m in env["engines_missing"] if m != "vision"]
    if missing:
        reasons.append("sans " + ", ".join(missing))
    return NOT_REPRESENTATIVE.format(why=" et ".join(reasons)) if reasons else None


def bench_synthetic(
    settings: Settings,
    engines: list[OcrEngine],
    *,
    count: int,
    seed: int,
    cases_dir: Path | None = None,
    results_dir: Path | None = None,
    compare: bool = True,
) -> tuple[Path, Path, dict[str, Any]]:
    cases_dir = cases_dir or settings.data_dir / "bench" / "synthetic" / f"seed{seed}_n{count}"
    results_dir = results_dir or REPO_ROOT / "bench" / "results"
    if not (cases_dir / "truth.json").is_file():
        generate(
            cases_dir,
            count=count,
            seed=seed,
            font_path=settings.models_dir / "fonts" / "ocrb10.otf",
        )
    cases = load_cases(cases_dir)
    today = date.fromisoformat(
        json.loads((cases_dir / "truth.json").read_text(encoding="utf-8")).get(
            "today", "2026-11-25"
        )
    )
    threshold = settings.auto_validate_threshold
    started = datetime.now(UTC)
    results = [run_case(c, cases_dir, engines, today, threshold) for c in cases]
    env = environment(engines, settings)

    field_acc = {
        k: _pct(
            sum(r.fields_correct.get(k, False) for r in results if k in r.fields_correct),
            sum(k in r.fields_correct for r in results),
        )
        for k in COMPARED_FIELDS
    }
    confusions = Counter(pair for r in results for pair in r.confusions)
    stage_ms: dict[str, list[float]] = defaultdict(list)
    for r in results:
        for k, v in r.ms_stage.items():
            stage_ms[k].append(v)
    payload: dict[str, Any] = {
        "set": "synthetic",
        "started_at": started.isoformat(timespec="seconds"),
        "git": _git_sha(),
        "seed": seed,
        "count": len(cases),
        "reference_date": today.isoformat(),
        "auto_validate_threshold": threshold,
        "environment": env,
        "warning": representativeness(env),
        "summary": summarize(results),
        "by_quality": _group(results, "quality"),
        "by_format": _group(results, "format"),
        "field_accuracy_pct": field_acc,
        "failures": dict(Counter(r.failure for r in results if r.failure)),
        "reasons": dict(Counter(x for r in results for x in r.reasons)),
        "confusions": [[t, r, n] for (t, r), n in confusions.most_common(30)],
        "stage_ms_p50": {k: round(statistics.median(v), 1) for k, v in stage_ms.items()},
        "peak_rss_mb": _peak_rss_mb(),
        "cases_detail": [asdict(r) for r in results],
    }
    results_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{started:%Y-%m-%d}_{payload['git']}_synthetic"
    previous = sorted(p for p in results_dir.glob("*_synthetic.json") if p.stem != stem)
    json_path = results_dir / f"{stem}.json"
    json_path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    baseline = (
        json.loads(previous[-1].read_text(encoding="utf-8")) if compare and previous else None
    )
    md_path = results_dir / f"{stem}.md"
    md_path.write_text(markdown(payload, baseline), encoding="utf-8")
    return json_path, md_path, payload


def _fmt(v: Any) -> str:
    return (
        "—" if v is None else (f"{v}" if not isinstance(v, float) else f"{v:.1f}".replace(".", ","))
    )


def _delta(cur: Any, base: Any) -> str:
    if not isinstance(cur, (int, float)) or not isinstance(base, (int, float)):
        return ""
    d = cur - base
    return f" ({'+' if d >= 0 else ''}{d:.1f})".replace(".", ",")


def markdown(p: dict[str, Any], baseline: dict[str, Any] | None = None) -> str:
    s, env = p["summary"], p["environment"]
    b = baseline["summary"] if baseline else {}
    out = [f"# Banc synthétique — étape E (MRZ) — {p['started_at'][:10]}", ""]
    if p["warning"]:
        out += [f"> ⚠️ {p['warning']}", ""]
    out += [
        f"- Commit : `{p['git']}` · graine {p['seed']} · {p['count']} cas"
        f" · date de référence {p['reference_date']}",
        f"- Plate-forme : {env['platform']} · Python {env['python']}"
        + (f" · macOS {env['macos']}" if env.get("macos") else ""),
        f"- **Moteurs actifs : {', '.join(env['engines_active']) or 'aucun'}**"
        + (f" · absents : {', '.join(env['engines_missing'])}" if env["engines_missing"] else ""),
    ]
    if env.get("tesseract_version"):
        out.append(f"- Tesseract : {env['tesseract_version']}")
    if env.get("ppocr_model"):
        out.append(f"- PP-OCR : {env['ppocr_model']}")
    if baseline:
        out.append(
            f"- Comparaison : `{baseline['git']}` du {baseline['started_at'][:10]}"
            " (écart entre parenthèses)"
        )
    out += ["", "## Indicateurs", "", "| Indicateur | Valeur |", "|---|---|"]
    rows = [
        ("MRZ localisée (%)", "mrz_found_pct"),
        ("MRZ décodée (%)", "mrz_decoded_pct"),
        ("**MRZ entièrement correcte (%)**", "mrz_exact_pct"),
        ("MRZ correcte avant réparation (%)", "mrz_exact_before_repair_pct"),
        ("Sommes de contrôle toutes valides (%)", "checksums_valid_pct"),
        ("Automatisation, MRZ seule (%)", "automation_pct"),
        ("**Précision de l'auto-validation (%)**", "auto_precision_pct"),
        ("Temps p50 par cas (ms)", "p50_ms"),
        ("Temps p95 par cas (ms)", "p95_ms"),
    ]
    for label, key in rows:
        out.append(f"| {label} | {_fmt(s[key])}{_delta(s[key], b.get(key))} |")
    out.append(f"| Erreurs auto-validées (doit être 0) | {len(s['silent_errors'])} |")
    out += [
        "",
        "L'automatisation est une approximation de l'étape L (phase 1b) à partir de la MRZ seule : "
        "toutes les sommes valides, aucun motif « À vérifier », champs obligatoires `valid` ou "
        f"`repaired` avec une confiance ≥ {p['auto_validate_threshold']:.2f}.".replace(".", ",", 1),
        "",
    ]
    if len(env["engines_active"]) < 2:
        out += [
            "Avec un seul moteur actif, aucune lecture ne peut confirmer un caractère d'une classe "
            "indiscernable (angle mort, § E.5) : l'automatisation est nulle par construction et "
            "le motif `MRZ_BLIND_SPOT` domine. C'est le comportement voulu (« À vérifier » plutôt "
            "qu'une erreur validée).",
            "",
        ]
    if s["silent_errors"]:
        out += ["### Erreurs auto-validées", "", *[f"- `{c}`" for c in s["silent_errors"]], ""]
    for title, key in (("Par qualité d'image", "by_quality"), ("Par format", "by_format")):
        out += [
            f"## {title}",
            "",
            "| | Cas | Localisée | Correcte | Sommes valides | Auto | Précision auto | p50 ms |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for k, g in p[key].items():
            out.append(
                f"| {k} | {g['cases']} | {_fmt(g['mrz_found_pct'])} | {_fmt(g['mrz_exact_pct'])} | "
                f"{_fmt(g['checksums_valid_pct'])} | {_fmt(g['automation_pct'])} | "
                f"{_fmt(g['auto_precision_pct'])} | {_fmt(g['p50_ms'])} |"
            )
        out.append("")
    out += ["## Exactitude par champ (%)", "", "| Champ | Exact |", "|---|---|"]
    out += [f"| {k} | {_fmt(v)} |" for k, v in p["field_accuracy_pct"].items()]
    out += ["", "## Échecs et motifs « À vérifier »", ""]
    out += [f"- échec `{k}` : {v}" for k, v in p["failures"].items()] or ["- aucun échec"]
    out += [f"- motif `{k}` : {v}" for k, v in sorted(p["reasons"].items(), key=lambda x: -x[1])]
    out += [
        "",
        "## Confusions de caractères (vérité → lu, avant réparation)",
        "",
        "| Vérité | Lu | N |",
        "|---|---|---|",
    ]
    out += [f"| `{t}` | `{r}` | {n} |" for t, r, n in p["confusions"]] or ["| — | — | 0 |"]
    out += ["", "## Temps par étape (p50, ms)", ""]
    out += [f"- {k} : {_fmt(v)}" for k, v in p["stage_ms_p50"].items()]
    out.append(
        f"- mémoire maximale du processus : {_fmt(p['peak_rss_mb'])} Mo"
        if p["peak_rss_mb"]
        else "- mémoire maximale : non mesurée sur cette plate-forme"
    )
    return "\n".join(out) + "\n"
