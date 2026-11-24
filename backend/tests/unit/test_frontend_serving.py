from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from idreader.api.app import create_app


def test_frontend_build_served_same_origin(migrated, tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    application = create_app(migrated.model_copy(update={"frontend_dist": dist}))
    with TestClient(application) as c:
        assert "id=root" in c.get("/").text
        assert "id=root" in c.get("/lots/abc").text  # routes du front : index.html
        assert c.get("/assets/app.js").status_code == 200
        r = c.get("/api/inconnu")
        assert r.status_code == 404
        assert r.json()["detail"]["code"] == "NOT_FOUND"
        assert c.get("/../../etc/passwd").status_code in (200, 404)
        assert "root:" not in c.get("/../../etc/passwd").text
    application.state.engine.dispose()


def test_no_frontend_build_is_fine(app, client) -> None:
    assert client.get("/").status_code == 404
