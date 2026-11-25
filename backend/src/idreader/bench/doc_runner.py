"""Banc `documents` : pipeline complet (A→L) sur les documents synthétiques UTO.

Mesure, par champ, l'exactitude des valeurs finales (accents compris), la détection des
documents, les statuts et la précision de l'auto-validation. Les documents UTO sont des
spécimens : le banc lève le motif SPECIMEN_DOCUMENT (`allow_specimen`) pour mesurer le reste.

Recto et verso sont réunis par l'appariement réel (étape J) appliqué à tout le jeu, les
fichiers étant pris dans l'ordre des cas, comme un lot téléversé : une paire fausse entre
deux cas voisins est comptée.
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
from idreader.pipeline.analyze import CropAnalysis, Engines, analyze_file, consolidate_pair
from idreader.pipeline.consolidate import (
    MANDATORY,
    OUTPUT_FIELDS,
    DocState,
    DocumentResult,
    FieldState,
)
from idreader.pipeline.pairing import Side, pair_sides

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
    paired: bool = False  # carte : recto et verso réunis par l'appariement automatique
    false_pairs: int = 0  # paires automatiques avec un recadrage d'un autre cas
    documents: int = 0  # documents produits pour le cas (1 attendu)


@dataclass
class _Analysed:
    case: DocCase
    crops: list[CropAnalysis]
    crop_files: list[int]  # rang du fichier (dans le cas) de chaque recadrage
    ms: float


@dataclass
class _FinalDoc:
    result: DocumentResult
    crop_keys: tuple[str, ...]  # « case_id#rang » des recadrages du document


def _plain(text: str | None) -> str:
    """Majuscules sans diacritiques, espaces simples (04_EVALUATION.md : compte sans accents)."""
    import unicodedata

    base = unicodedata.normalize("NFKD", text or "")
    return " ".join("".join(c for c in base if not unicodedata.combining(c)).upper().split())


def analyze_case(
    case: DocCase, cases_dir: Path, engines: Engines, today: date, threshold: float
) -> _Analysed:
    t0 = time.perf_counter()
    crops: list[CropAnalysis] = []
    files: list[int] = []
    for i, name in enumerate(case.images):
        fa = analyze_file(
            cases_dir / name, engines, today=today, threshold=threshold, allow_specimen=True
        )
        crops.extend(fa.crops)
        files.extend([i] * len(fa.crops))
    return _Analysed(case, crops, files, (time.perf_counter() - t0) * 1000)


def pair_all(
    analysed: list[_Analysed], today: date, threshold: float
) -> tuple[dict[str, list[_FinalDoc]], list[tuple[str, str]]]:
    """Étape J sur tout le jeu, fichiers dans l'ordre des cas (comme un lot téléversé).

    Renvoie les documents finaux par cas (un document apparié revient au cas de la face
    MRZ) et les paires automatiques formées (clés des deux recadrages).
    """
    sides: list[Side] = []
    by_key: dict[str, tuple[str, CropAnalysis]] = {}
    order = 0
    for a in analysed:
        offset = order
        order += len(a.case.images)
        for i, (ca, f) in enumerate(zip(a.crops, a.crop_files, strict=True)):
            key = f"{a.case.case_id}#{i}"
            by_key[key] = (a.case.case_id, ca)
            sides.append(
                Side(
                    key=key,
                    file_order=offset + f + 1,
                    page_key=f"{a.case.case_id}/{f}/{ca.page_index}",
                    physical_format=ca.physical_format,
                    mrz=ca.mrz.result,
                    texts=tuple(s.text for s in ca.viz.slots),
                )
            )
    auto, _ = pair_sides(sides)
    docs: dict[str, list[_FinalDoc]] = {a.case.case_id: [] for a in analysed}
    used: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for p in auto:
        owner, mrz_ca = by_key[p.mrz_key]
        _, other_ca = by_key[p.other_key]
        res = mrz_ca.mrz.result
        mrz_is_front = res is not None and res.format.value != "TD1"
        front, back = (mrz_ca, other_ca) if mrz_is_front else (other_ca, mrz_ca)
        pa = consolidate_pair(
            front.evidence(),
            back.evidence(),
            today=today,
            threshold=threshold,
            allow_specimen=True,
        )
        docs[owner].append(_FinalDoc(pa.result, (p.mrz_key, p.other_key)))
        used |= {p.mrz_key, p.other_key}
        pairs.append((p.mrz_key, p.other_key))
    for key, (owner, ca) in by_key.items():
        if key not in used:
            docs[owner].append(_FinalDoc(ca.result, (key,)))
    return docs, pairs


def _main(docs: list[_FinalDoc]) -> _FinalDoc | None:
    """Le document qui représente le cas : apparié, sinon avec MRZ, sinon le plus confiant."""
    if not docs:
        return None
    return max(
        docs,
        key=lambda d: (len(d.crop_keys), d.result.mrz_valid is not None, d.result.confidence),
    )


def score_case(a: _Analysed, docs: list[_FinalDoc], pairs: list[tuple[str, str]]) -> DocResult:
    case = a.case
    cid = case.case_id
    main = _main(docs)
    values = {k: f.value for k, f in main.result.fields.items()} if main else {}
    truth = case.fields
    correct = {k: values.get(k) == truth.get(k) for k in OUTPUT_FIELDS if truth.get(k)}
    accented = {
        k: correct[k]
        for k in ("surname", "given_names")
        if truth.get(k) and not str(truth[k]).isascii()
    }
    names_plain = {
        k: _plain(values.get(k)) == _plain(truth.get(k))
        for k in ("surname", "given_names")
        if truth.get(k)
    }
    silent: list[str] = []
    for d in docs:
        if d.result.status is not DocState.AUTO_VALIDATED:
            continue
        for name, f in d.result.fields.items():
            if f.status in PROVEN and f.value and truth.get(name) and f.value != truth[name]:
                silent.append(name)
    prefix = f"{cid}#"
    mine = [p for p in pairs if any(k.startswith(prefix) for k in p)]
    return DocResult(
        case_id=cid,
        kind=case.kind,
        quality=case.quality,
        layout=case.layout,
        crops_expected=1 if case.kind == "passport" else 2,
        crops_found=len(a.crops),
        statuses=[d.result.status.value for d in docs],
        auto_validated=main is not None and main.result.status is DocState.AUTO_VALIDATED,
        silent_errors=silent,
        fields_correct=correct,
        accented=accented,
        names_plain=names_plain,
        reasons=sorted({r.code for d in docs for r in d.result.reasons}),
        ms=a.ms,
        paired=any(all(k.startswith(prefix) for k in p) for p in mine),
        false_pairs=sum(not all(k.startswith(prefix) for k in p) for p in mine),
        documents=len(docs),
    )


def summarize_docs(results: list[DocResult]) -> dict[str, Any]:
    n = len(results)
    mandatory = [r for r in results if all(r.fields_correct.get(k, True) for k in MANDATORY)]
    auto = [r for r in results if r.auto_validated]
    times = sorted(r.ms for r in results)
    accented = [v for r in results for v in r.accented.values()]
    # Cartes dont les deux faces ont été détectées : l'appariement peut les réunir.
    cards = [r for r in results if r.kind != "passport" and r.crops_found == 2]
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
        "pairs_formed_pct": _pct(sum(r.paired for r in cards), len(cards)),
        "false_pairs": sum(r.false_pairs for r in results),
        "one_document_pct": _pct(sum(r.documents == 1 for r in results), n),
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
    analysed = [analyze_case(c, cases_dir, engines, today, threshold) for c in cases]
    final, pairs = pair_all(analysed, today, threshold)
    results = [score_case(a, final[a.case.case_id], pairs) for a in analysed]
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
        "- Spécimens UTO : motif SPECIMEN_DOCUMENT levé pour la mesure. Recto et verso"
        " réunis par l'appariement réel (étape J) sur tout le jeu, fichiers dans l'ordre ;"
        " une carte non appariée est jugée sur son document avec MRZ.",
    ]
    if baseline:
        out.append(f"- Comparaison : `{baseline['git']}` du {baseline['started_at'][:10]}")
    out += ["", "## Indicateurs", "", "| Indicateur | Valeur |", "|---|---|"]
    rows = [
        ("Documents détectés au bon nombre (%)", "detection_ok_pct"),
        ("**Champs obligatoires tous justes (%)**", "mandatory_all_correct_pct"),
        ("Noms accentués justes, accents compris (%)", "accented_names_correct_pct"),
        ("Noms justes sans tenir compte des accents (%)", "names_correct_no_accents_pct"),
        ("Cartes : recto et verso appariés automatiquement (%)", "pairs_formed_pct"),
        ("Un seul document par pièce (%)", "one_document_pct"),
        ("Automatisation (%)", "automation_pct"),
        ("**Précision de l'auto-validation (%)**", "auto_precision_pct"),
        ("Temps p50 par document (ms)", "p50_ms"),
        ("Temps p95 par document (ms)", "p95_ms"),
    ]
    for label, key in rows:
        out.append(f"| {label} | {_fmt(s.get(key))}{_delta(s.get(key), b.get(key))} |")
    out.append(f"| Erreurs auto-validées (doit être 0) | {len(s['silent_errors'])} |")
    out.append(f"| Fausses paires automatiques (doit être 0) | {s.get('false_pairs', 0)} |")
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
