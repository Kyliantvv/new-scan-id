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
