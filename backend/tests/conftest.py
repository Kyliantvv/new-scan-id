"""Fixtures communes. Aucune vraie pièce d'identité : uniquement des données synthétiques."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from idreader.core.config import Settings, get_settings

TEST_SECRET = "test-secret-key-not-for-production"


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Réglages isolés : base et fichiers dans un dossier temporaire."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("IDR_DATA_DIR", str(data_dir))
    monkeypatch.setenv("IDR_SECRET_KEY", TEST_SECRET)
    monkeypatch.setenv("IDR_COOKIE_SECURE", "false")
    monkeypatch.setenv("IDR_DUMMY_JOB_SECONDS", "0")
    monkeypatch.setenv("IDR_SSE_POLL_SECONDS", "0.05")
    monkeypatch.setenv("IDR_IDVISION_PATH", str(tmp_path / "absent-idvision"))
    monkeypatch.setenv("IDR_FRONTEND_DIST", str(tmp_path / "absent-dist"))
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


PASSWORD = "motdepasse-de-test-123"


@pytest.fixture
def migrated(settings: Settings) -> Settings:
    from idreader.core.migrate import upgrade_head

    upgrade_head(settings)
    return settings


@pytest.fixture
def app(migrated: Settings):
    from idreader.api.app import create_app

    application = create_app(migrated)
    yield application
    application.state.engine.dispose()


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


def make_user(app, email: str = "operateur@exemple.fr", *, admin: bool = False) -> str:
    from idreader.core.models import Role
    from idreader.core.security import create_user

    with app.state.session_factory() as db:
        user = create_user(
            db, email, PASSWORD, Role.ADMIN if admin else Role.OPERATOR, app.state.settings
        )
        db.commit()
        return user.id


def login(client, email: str = "operateur@exemple.fr") -> str:
    """Connecte le client et renvoie le jeton CSRF ; le pose aussi en en-tête par défaut."""
    r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200, r.text
    csrf = r.json()["csrf_token"]
    client.headers["X-CSRF-Token"] = csrf
    return csrf


@pytest.fixture
def operator_client(app, client):
    make_user(app)
    login(client)
    return client
