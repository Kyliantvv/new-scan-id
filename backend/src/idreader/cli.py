"""Ligne de commande `idreader` (Typer)."""

from __future__ import annotations

import json

import typer

from idreader import __version__
from idreader.core.config import get_settings

app = typer.Typer(help="IDReader : lecture automatique de pièces d'identité.", no_args_is_help=True)


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
