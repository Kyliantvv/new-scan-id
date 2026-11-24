from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import APIRouter, Depends
from sqlalchemy import select
from typer.testing import CliRunner

from idreader.api.deps import require_admin
from idreader.cli import app as cli_app
from idreader.core.db import utcnow
from idreader.core.models import AuditLog, User, UserSession
from idreader.core.security import (
    check_password_policy,
    hash_password,
    hash_token,
    verify_password,
)
from tests.conftest import PASSWORD, login, make_user


def test_password_hash_is_argon2id() -> None:
    h = hash_password("un-mot-de-passe-long")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "un-mot-de-passe-long")
    assert not verify_password(h, "autre-mot-de-passe")
    assert not verify_password("pas-un-hash", "x")


def test_password_policy(settings) -> None:
    with pytest.raises(ValueError, match="12"):
        check_password_policy("court", settings)
    check_password_policy("a" * 12, settings)


def test_token_hash_depends_on_secret(settings) -> None:
    h = hash_token("abc", settings)
    assert len(h) == 64
    assert h != "abc"


def test_login_sets_httponly_strict_cookie(app, client) -> None:
    make_user(app)
    r = client.post(
        "/api/auth/login", json={"email": "Operateur@Exemple.fr ", "password": PASSWORD}
    )
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert r.json()["role"] == "operator"
    assert client.get("/api/me").status_code == 200


def test_cookie_secure_flag(migrated, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from idreader.api.app import create_app

    secure = migrated.model_copy(update={"cookie_secure": True})
    application = create_app(secure)
    make_user(application)
    with TestClient(application, base_url="https://testserver") as c:
        r = c.post("/api/auth/login", json={"email": "operateur@exemple.fr", "password": PASSWORD})
        assert "secure" in r.headers["set-cookie"].lower()
    application.state.engine.dispose()


def test_session_token_not_stored_in_clear(app, client) -> None:
    make_user(app)
    login(client)
    token = client.cookies.get("idr_session")
    with app.state.session_factory() as db:
        stored = db.scalars(select(UserSession.token_hash)).all()
    assert token not in stored
    assert hash_token(token, app.state.settings) in stored


def test_me_requires_session(client) -> None:
    r = client.get("/api/me")
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "NOT_AUTHENTICATED"


def test_wrong_password_gives_generic_error(app, client) -> None:
    make_user(app)
    r = client.post("/api/auth/login", json={"email": "operateur@exemple.fr", "password": "faux"})
    assert r.status_code == 401
    r2 = client.post("/api/auth/login", json={"email": "inconnu@exemple.fr", "password": "faux"})
    assert r2.status_code == 401
    assert r.json() == r2.json()


def test_lockout_after_five_failures(app, client) -> None:
    make_user(app)
    for _ in range(5):
        client.post("/api/auth/login", json={"email": "operateur@exemple.fr", "password": "faux"})
    r = client.post("/api/auth/login", json={"email": "operateur@exemple.fr", "password": PASSWORD})
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "ACCOUNT_LOCKED"
    # fin du verrouillage
    with app.state.session_factory() as db:
        user = db.scalar(select(User))
        assert user is not None
        user.locked_until = utcnow() - timedelta(seconds=1)
        db.commit()
    assert login(client)


def test_disabled_account_refused(app, client) -> None:
    make_user(app)
    with app.state.session_factory() as db:
        user = db.scalar(select(User))
        assert user is not None
        user.active = False
        db.commit()
    r = client.post("/api/auth/login", json={"email": "operateur@exemple.fr", "password": PASSWORD})
    assert r.status_code == 403


def test_idle_timeout(app, client) -> None:
    make_user(app)
    login(client)
    with app.state.session_factory() as db:
        sess = db.scalar(select(UserSession))
        assert sess is not None
        sess.last_seen_at = utcnow() - timedelta(minutes=31)
        db.commit()
    assert client.get("/api/me").status_code == 401


def test_absolute_timeout(app, client) -> None:
    make_user(app)
    login(client)
    with app.state.session_factory() as db:
        sess = db.scalar(select(UserSession))
        assert sess is not None
        sess.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.get("/api/me").status_code == 401


def test_csrf_required_for_mutations(app, client) -> None:
    make_user(app)
    login(client)
    del client.headers["X-CSRF-Token"]
    r = client.post("/api/auth/logout")
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "CSRF_FAILED"
    r = client.post("/api/auth/logout", headers={"X-CSRF-Token": "faux"})
    assert r.status_code == 403


def test_logout_revokes_session(operator_client) -> None:
    assert operator_client.post("/api/auth/logout").status_code == 204
    assert operator_client.get("/api/me").status_code == 401


def test_admin_role_required(app, client) -> None:
    router = APIRouter()

    @router.get("/api/test-admin", dependencies=[Depends(require_admin)])
    def admin_only() -> dict[str, bool]:
        return {"ok": True}

    app.include_router(router)
    make_user(app)
    make_user(app, "admin@exemple.fr", admin=True)
    login(client)
    assert client.get("/api/test-admin").status_code == 403
    client.post("/api/auth/logout")
    login(client, "admin@exemple.fr")
    assert client.get("/api/test-admin").status_code == 200


def test_login_audited_without_personal_data(app, client) -> None:
    uid = make_user(app)
    login(client)
    client.post("/api/auth/login", json={"email": "operateur@exemple.fr", "password": "faux"})
    with app.state.session_factory() as db:
        rows = db.scalars(select(AuditLog).order_by(AuditLog.id)).all()
    actions = [(r.action, r.entity_id) for r in rows]
    assert ("login", uid) in actions
    assert ("login_failed", None) in actions


def test_security_headers(client) -> None:
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_validation_error_does_not_echo_input(client) -> None:
    r = client.post("/api/auth/login", json={"email": "ERIKSSON-secret"})
    assert r.status_code == 422
    assert "ERIKSSON" not in r.text


def test_cli_create_user(migrated) -> None:
    runner = CliRunner()
    r = runner.invoke(
        cli_app, ["create-user", "--admin", "chef@exemple.fr"], input=f"{PASSWORD}\n{PASSWORD}\n"
    )
    assert r.exit_code == 0, r.output
    assert "admin" in r.output
    r2 = runner.invoke(
        cli_app, ["create-user", "chef@exemple.fr"], input=f"{PASSWORD}\n{PASSWORD}\n"
    )
    assert r2.exit_code == 1
    r3 = runner.invoke(cli_app, ["create-user", "x@exemple.fr"], input="court\ncourt\n")
    assert r3.exit_code == 1


def test_cli_refuses_unmigrated_db(settings) -> None:
    r = CliRunner().invoke(cli_app, ["create-user", "x@exemple.fr"], input="a\na\n")
    assert r.exit_code == 2
