"""Application des migrations Alembic depuis le code (CLI, tests, installation)."""

from __future__ import annotations

from alembic import command
from alembic.config import Config

from idreader.core.config import REPO_ROOT, Settings

ALEMBIC_INI = REPO_ROOT / "backend" / "alembic.ini"


def alembic_config() -> Config:
    return Config(str(ALEMBIC_INI))


def upgrade_head(settings: Settings) -> None:
    """Met la base désignée par `settings` à la dernière version du schéma."""
    from idreader.core.db import make_engine

    engine = make_engine(settings)
    try:
        with engine.begin() as conn:
            cfg = alembic_config()
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
    finally:
        engine.dispose()
