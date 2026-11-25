"""Ligne de commande `idreader` (Typer)."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from idreader import __version__
from idreader.core.config import Settings, get_settings
from idreader.core.logging import setup_logging

app = typer.Typer(help="IDReader : lecture automatique de pièces d'identité.", no_args_is_help=True)


def _require_schema(settings: Settings) -> None:
    from idreader.core.migrate import schema_is_current

    if not schema_is_current(settings):
        typer.echo("Base absente ou pas à jour : lancer `uv run alembic upgrade head`.", err=True)
        raise typer.Exit(2)


@app.command()
def version() -> None:
    """Affiche la version d'IDReader."""
    typer.echo(__version__)


@app.command("show-config")
def show_config() -> None:
    """Affiche la configuration effective (sans les secrets)."""
    settings = get_settings()
    data = settings.model_dump(mode="json", exclude={"secret_key"})
    data["secret_key_set"] = bool(settings.secret_key.get_secret_value())
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False))


@app.command("create-user")
def create_user_cmd(
    email: Annotated[str, typer.Argument(help="Adresse e-mail du compte")],
    admin: Annotated[bool, typer.Option("--admin", help="Rôle administrateur")] = False,
) -> None:
    """Crée un compte nominatif (mot de passe demandé, 12 caractères minimum)."""
    from idreader.core import audit
    from idreader.core.db import make_engine, make_session_factory
    from idreader.core.models import Role
    from idreader.core.security import create_user

    settings = get_settings()
    _require_schema(settings)
    password = typer.prompt("Mot de passe", hide_input=True, confirmation_prompt=True)
    engine = make_engine(settings)
    try:
        with make_session_factory(engine)() as db:
            try:
                user = create_user(
                    db, email, password, Role.ADMIN if admin else Role.OPERATOR, settings
                )
            except ValueError as exc:
                typer.echo(str(exc), err=True)
                raise typer.Exit(1) from None
            audit.record(db, "user_created", user_id=None, entity="user", entity_id=user.id)
            db.commit()
            typer.echo(f"Compte créé ({user.role}) : {user.id}")
    finally:
        engine.dispose()


@app.command()
def serve() -> None:
    """Lance l'API (par défaut sur 127.0.0.1:8000, voir IDR_BIND)."""
    import uvicorn

    from idreader.api.app import create_app

    setup_logging()
    settings = get_settings()
    _require_schema(settings)
    if not settings.secret_key.get_secret_value():
        typer.echo("IDR_SECRET_KEY non défini (voir .env).", err=True)
        raise typer.Exit(2)
    host, _, port = settings.bind.rpartition(":")
    # access_log=False : le journal d'accès d'uvicorn contient la chaîne de requête.
    uvicorn.run(create_app(settings), host=host, port=int(port), access_log=False, log_config=None)


def _log_helper_status(settings: Settings) -> None:
    """`idvision doctor` au démarrage du worker (docs/02_ARCHITECTURE.md § Helper Swift)."""
    import logging

    from idreader.engines.apple_vision import probe_helper

    log = logging.getLogger("idreader.worker")
    info = probe_helper(settings)
    if info is None:
        log.warning("idvision indisponible : moteur Apple Vision désactivé")
        return
    langs = info.get("languages", {})
    log.info(
        "idvision ok version=%s macos=%s revision=%s languages_accurate=%d",
        info.get("version"),
        ".".join(str(v) for v in info.get("macos_version", [])),
        info.get("recognize_text_revision"),
        len(langs.get("accurate", [])),
    )


@app.command()
def worker(
    exit_when_idle: Annotated[
        bool, typer.Option("--exit-when-idle", help="S'arrêter quand la file est vide")
    ] = False,
) -> None:
    """Lance le worker d'analyse (un processus ; reprend les jobs abandonnés)."""
    import signal

    from idreader.core.db import make_engine
    from idreader.worker.main import Worker

    setup_logging()
    settings = get_settings()
    _require_schema(settings)
    _log_helper_status(settings)
    engine = make_engine(settings)
    w = Worker(settings, engine)

    def _stop(_signum: int, _frame: object) -> None:
        w.stop()  # termine le job en cours puis s'arrête

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    try:
        w.run(exit_when_idle=exit_when_idle)
    finally:
        engine.dispose()


@app.command()
def bench(
    set_name: Annotated[
        str, typer.Option("--set", help="Jeu : synthetic (golden en phase 1b)")
    ] = "synthetic",
    count: Annotated[int, typer.Option(help="Nombre de cas synthétiques")] = 300,
    seed: Annotated[int, typer.Option(help="Graine du générateur")] = 2026,
    engines: Annotated[
        str, typer.Option(help="Moteurs (séparés par des virgules), défaut : tous les disponibles")
    ] = "",
    compare: Annotated[
        bool, typer.Option("--compare/--no-compare", help="Écart avec le dernier résultat")
    ] = True,
) -> None:
    """Banc d'essai. `synthetic` : étape E (MRZ) ; `documents` : pipeline complet sur des
    documents UTO synthétiques. Rapport dans bench/results/."""
    from idreader.bench.runner import bench_synthetic
    from idreader.engines.registry import available_engines, available_viz_engines

    settings = get_settings()
    only = [e.strip() for e in engines.split(",") if e.strip()] or None
    if set_name == "documents":
        from idreader.bench.doc_runner import bench_documents
        from idreader.pipeline.analyze import Engines

        eng = Engines(
            mrz=available_engines(settings, only), viz=available_viz_engines(settings, only)
        )
        typer.echo(f"Moteurs actifs : {', '.join(eng.names) or 'aucun'}")
        _, md_path, payload = bench_documents(
            settings, eng, count=count, seed=seed, compare=compare
        )
        s = payload["summary"]
        typer.echo(
            f"Champs obligatoires tous justes : {s['mandatory_all_correct_pct']} % · noms "
            f"accentués : {s['accented_names_correct_pct']} % · auto-validation : "
            f"{s['automation_pct']} % · erreurs auto-validées : {len(s['silent_errors'])}"
        )
        if payload["warning"]:
            typer.echo("ATTENTION : résultats non représentatifs (voir le rapport).")
        typer.echo(f"Rapport : {md_path}")
        return
    if set_name != "synthetic":
        typer.echo("Jeux disponibles : synthetic, documents (golden en phase 1b).", err=True)
        raise typer.Exit(2)
    active = available_engines(settings, only)
    typer.echo(f"Moteurs actifs : {', '.join(e.name for e in active) or 'aucun'}")
    _, md_path, payload = bench_synthetic(settings, active, count=count, seed=seed, compare=compare)
    s = payload["summary"]
    typer.echo(
        f"MRZ entièrement correcte : {s['mrz_exact_pct']} % · auto-validation : "
        f"{s['automation_pct']} % (précision {s['auto_precision_pct']} %) · "
        f"erreurs auto-validées : {len(s['silent_errors'])}"
    )
    if payload["warning"]:
        typer.echo("ATTENTION : résultats non représentatifs (voir le rapport).")
    typer.echo(f"Rapport : {md_path}")


@app.command()
def analyze(
    path: Annotated[str, typer.Argument(help="Fichier PDF, JPG ou PNG")],
    debug: Annotated[
        bool, typer.Option("--debug", help="Écrire recadrages et artefacts dans data/debug/")
    ] = False,
    show_values: Annotated[
        bool, typer.Option("--show-values", help="Afficher les valeurs lues (données personnelles)")
    ] = False,
    engines: Annotated[str, typer.Option(help="Moteurs (séparés par des virgules)")] = "",
) -> None:
    """Pipeline A→L sur un fichier, sans base ni file de jobs."""
    from datetime import date, datetime
    from pathlib import Path

    from idreader.engines.registry import available_engines, available_viz_engines
    from idreader.pipeline.analyze import Engines, analyze_file
    from idreader.worker.process import _write_json, _write_png

    settings = get_settings()
    setup_logging()
    only = [e.strip() for e in engines.split(",") if e.strip()] or None
    eng = Engines(mrz=available_engines(settings, only), viz=available_viz_engines(settings, only))
    typer.echo(f"Moteurs actifs : {', '.join(eng.names) or 'aucun'}")
    fa = analyze_file(
        Path(path),
        eng,
        today=date.today(),
        threshold=settings.auto_validate_threshold,
        allow_specimen=settings.allow_specimen,
    )
    out = settings.data_dir / "debug" / f"{datetime.now():%Y%m%d-%H%M%S}"
    for ca in fa.crops:
        r = ca.result
        typer.echo(
            f"\nPage {ca.page_index + 1}, document {ca.index + 1} : {r.status.value}"
            f" · {r.doc_type.value} · {ca.physical_format} · face {ca.side}"
            f" · confiance {r.confidence:.2f}"
        )
        for reason in r.reasons:
            typer.echo(f"  - {reason.label}" + (f" ({reason.field})" if reason.field else ""))
        for name, f in r.fields.items():
            if f.status.value == "missing":
                continue
            value = f" = {f.value}" if show_values else ""
            typer.echo(f"    {name:<16} {f.status.value:<9} {f.confidence:.2f}{value}")
        if debug:
            d = out / f"p{ca.page_index + 1}_d{ca.index + 1}"
            _write_png(d / "crop.png", ca.image)
            for stage, model in (
                ("mrz", ca.mrz),
                ("viz", ca.viz),
                ("extract", ca.extraction),
                ("classify", ca.classification),
                ("result", ca.result),
            ):
                _write_json(d / f"{stage}.json", model)
    if not fa.crops:
        typer.echo("Aucun document détecté.")
    if debug:
        typer.echo(f"\nArtefacts : {out}")
