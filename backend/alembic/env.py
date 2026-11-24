"""Environnement Alembic : base désignée par IDR_DATA_DIR, PRAGMA identiques à l'application."""

from __future__ import annotations

from alembic import context
from sqlalchemy import Connection

from idreader.core.config import get_settings
from idreader.core.db import make_engine
from idreader.core.models import Base

target_metadata = Base.metadata


def _run(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # SQLite : ALTER TABLE limité, migrations par recopie
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = make_engine(get_settings())
    try:
        with engine.connect() as conn:
            _run(conn)
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise SystemExit("Mode hors ligne d'Alembic non pris en charge.")
run_migrations_online()
