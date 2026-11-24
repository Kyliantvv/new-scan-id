from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError

from idreader.core.config import Settings
from idreader.core.db import immediate_connection, make_engine, make_session_factory, utcnow
from idreader.core.migrate import alembic_config, upgrade_head
from idreader.core.models import (
    FIELD_NAMES,
    Base,
    Batch,
    Document,
    DocumentField,
    Job,
    User,
)


@pytest.fixture
def engine(settings: Settings):
    upgrade_head(settings)
    eng = make_engine(settings)
    yield eng
    eng.dispose()


def test_pragmas(engine) -> None:
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert conn.exec_driver_sql("PRAGMA busy_timeout").scalar() == 5000


def test_migration_matches_models(engine) -> None:
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_all_tables_and_view(engine) -> None:
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    expected = {
        "users",
        "sessions",
        "batches",
        "files",
        "pages",
        "crops",
        "documents",
        "fields",
        "jobs",
        "audit_log",
        "settings",
    }
    assert expected <= tables
    assert "documents_flat" in insp.get_view_names()


def test_downgrade_then_upgrade(settings: Settings, engine) -> None:
    with engine.begin() as conn:
        cfg = alembic_config()
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "base")
    assert "users" not in inspect(engine).get_table_names()
    upgrade_head(settings)
    assert "users" in inspect(engine).get_table_names()


def test_documents_flat_pivots_fields(engine) -> None:
    factory = make_session_factory(engine)
    with factory() as s:
        batch = Batch(name="Lot test")
        s.add(batch)
        s.flush()
        doc = Document(batch_id=batch.id, issuing_state="UTO")
        s.add(doc)
        s.flush()
        # Valeurs du spécimen ICAO 9303 (données fictives publiques)
        s.add_all(
            [
                DocumentField(document_id=doc.id, name="surname", value="ERIKSSON", status="ok"),
                DocumentField(document_id=doc.id, name="given_names", value="ANNA MARIA"),
            ]
        )
        s.commit()
        row = s.execute(text("SELECT * FROM documents_flat")).mappings().one()
    assert row["surname"] == "ERIKSSON"
    assert row["given_names"] == "ANNA MARIA"
    assert row["issuing_state"] == "UTO"
    for name in FIELD_NAMES:
        assert name in row


def test_foreign_keys_enforced(engine) -> None:
    factory = make_session_factory(engine)
    with factory() as s:
        s.add(Document(batch_id="inexistant"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_check_constraint_on_job_status(engine) -> None:
    factory = make_session_factory(engine)
    with factory() as s:
        s.add(Job(type="process_file", status="bogus"))
        with pytest.raises(IntegrityError):
            s.commit()


def test_cascade_delete_batch(engine) -> None:
    factory = make_session_factory(engine)
    with factory() as s:
        batch = Batch(name="Lot")
        s.add(batch)
        s.flush()
        s.add(Job(type="process_file", batch_id=batch.id))
        s.commit()
        s.delete(batch)
        s.commit()
        assert s.scalars(select(Job)).all() == []


def test_datetimes_roundtrip_as_utc(engine) -> None:
    factory = make_session_factory(engine)
    paris = timezone(timedelta(hours=1))
    with factory() as s:
        u = User(
            email="a@exemple.fr",
            password_hash="x",
            locked_until=datetime(2030, 1, 1, 13, 0, tzinfo=paris),
        )
        s.add(u)
        s.commit()
        uid = u.id
    with factory() as s:
        u2 = s.get(User, uid)
        assert u2 is not None
        assert u2.locked_until == datetime(2030, 1, 1, 12, 0, tzinfo=UTC)
        assert u2.created_at.tzinfo is UTC


def test_naive_datetime_refused(engine) -> None:
    factory = make_session_factory(engine)
    with factory() as s:
        s.add(User(email="b@exemple.fr", password_hash="x", locked_until=datetime(2030, 1, 1)))
        with pytest.raises(Exception, match="fuseau"):
            s.commit()


def test_immediate_connection_takes_write_lock(engine) -> None:
    with immediate_connection(engine) as conn:
        conn.execute(text("SELECT 1"))
        # Une deuxième transaction IMMEDIATE doit attendre : on vérifie avec un délai nul.
        other = engine.raw_connection()
        try:
            other.execute("PRAGMA busy_timeout=0")
            with pytest.raises(Exception, match="locked"):
                other.execute("BEGIN IMMEDIATE")
        finally:
            other.close()
    assert utcnow().tzinfo is UTC
