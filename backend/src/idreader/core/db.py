"""Accès SQLite : moteur SQLAlchemy, PRAGMA, sessions, horodatages UTC."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Engine, create_engine, event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from idreader.core.config import Settings

BUSY_TIMEOUT_MS = 5000


def utcnow() -> datetime:
    """Instant courant en UTC (avec fuseau)."""
    return datetime.now(UTC)


def new_id() -> str:
    """Identifiant interne (UUID v4). Les noms de fichiers stockés en dérivent."""
    return str(uuid.uuid4())


class UTCDateTime(TypeDecorator[datetime]):
    """Horodatage stocké en UTC naïf, relu avec le fuseau UTC (SQLite ne garde pas le fuseau)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("horodatage sans fuseau refusé : utiliser utcnow()")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


def _on_connect(dbapi_connection: sqlite3.Connection, _record: Any) -> None:
    # Le module sqlite3 gère mal les transactions : on les pilote nous-mêmes (événement « begin »),
    # ce qui permet BEGIN IMMEDIATE pour la prise de job (docs/02_ARCHITECTURE.md § File de jobs).
    dbapi_connection.isolation_level = None
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()


def _on_begin(conn: Connection) -> None:
    mode = conn.get_execution_options().get("sqlite_begin", "DEFERRED")
    if mode not in ("DEFERRED", "IMMEDIATE", "EXCLUSIVE"):
        raise ValueError("mode de transaction SQLite inconnu")
    conn.exec_driver_sql(f"BEGIN {mode}")


def make_engine(settings: Settings) -> Engine:
    """Crée le moteur SQLite (crée le dossier de données si besoin)."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False, "timeout": BUSY_TIMEOUT_MS / 1000},
    )
    event.listen(engine, "connect", _on_connect)
    event.listen(engine, "begin", _on_begin)
    return engine


def make_session_factory(engine: Engine, *, immediate: bool = True) -> sessionmaker[Session]:
    """Fabrique de sessions ORM.

    Par défaut `BEGIN IMMEDIATE` : en WAL, une transaction différée qui lit puis écrit échoue
    sans attendre (SQLITE_BUSY) si un autre processus a écrit entre-temps (docs/DECISIONS.md).
    Garder ces transactions courtes. `immediate=False` pour les lectures seules (SSE).
    """
    bind = engine.execution_options(sqlite_begin="IMMEDIATE") if immediate else engine
    return sessionmaker(bind=bind, expire_on_commit=False)


@contextmanager
def immediate_connection(engine: Engine) -> Iterator[Connection]:
    """Transaction `BEGIN IMMEDIATE` : verrou d'écriture pris dès le début."""
    with engine.connect().execution_options(sqlite_begin="IMMEDIATE") as conn, conn.begin():
        yield conn
