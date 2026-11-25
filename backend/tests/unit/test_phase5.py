"""Phase 5 : purge selon la durée de conservation, purge d'un lot, alerte « macOS a changé ».

Données fictives (pays UTO) écrites directement en base, dates vieillies à la main.
"""

from __future__ import annotations

import json
import os
import time
from datetime import timedelta

import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner

from idreader.core import models as m
from idreader.core.db import utcnow
from idreader.core.purge import BatchBusyError, purge_batch, purge_expired, vacuum
from idreader.core.system import last_macos_bench, macos_alert
from tests.conftest import login, make_user
from tests.unit.test_phase3_api import _seed


@pytest.fixture
def admin_client(app, client):
    make_user(app, "admin@exemple.fr", admin=True)
    login(client, "admin@exemple.fr")
    return client


def _age(app, ids: dict[str, str], days: int) -> None:
    """Vieillit le lot et son fichier de `days` jours."""
    when = utcnow() - timedelta(days=days)
    with app.state.session_factory() as db:
        db.get(m.Batch, ids["batch"]).created_at = when
        db.get(m.File, ids["file"]).created_at = when
        db.commit()


def _paths(app, ids: dict[str, str]) -> dict[str, object]:
    settings = app.state.settings
    base = settings.data_dir / "batches" / ids["batch"]
    with app.state.session_factory() as db:
        crop = db.get(m.Crop, ids["crop"])
        page = db.get(m.Page, ids["page"])
        f = db.get(m.File, ids["file"])
        return {
            "crop": base / crop.storage_path,
            "page": base / page.storage_path,
            "original": settings.data_dir / f.storage_path,
            "artifacts": base / "artifacts" / ids["crop"],
            "batch": base,
        }


def _purge(app, **kw):
    with app.state.session_factory() as db:
        report = purge_expired(db, app.state.settings, images_days=30, data_days=365, **kw)
        db.commit()
    return report


def test_images_purged_after_retention_data_kept(app, admin_client) -> None:
    ids = _seed(app)
    paths = _paths(app, ids)
    paths["artifacts"].mkdir(parents=True)
    (paths["artifacts"] / "viz.json").write_text("{}", encoding="utf-8")
    _age(app, ids, 31)

    report = _purge(app)
    assert report.files_images_purged == 1 and report.batches_deleted == 0
    for key in ("crop", "page", "original", "artifacts"):
        assert not paths[key].exists(), key
    # Les données extraites restent, sans image.
    d = admin_client.get(f"/api/documents/{ids['doc']}").json()
    assert {f["name"]: f["value"] for f in d["fields"]}["surname"] == "ÉRIKSSON"
    assert admin_client.get(f"/api/crops/{ids['crop']}/image").status_code == 404
    r = admin_client.post(f"/api/documents/{ids['doc']}/reprocess")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "IMAGES_PURGED"
    # Idempotent : rien de plus au passage suivant.
    assert _purge(app).files_images_purged == 0


def test_recent_files_untouched_and_dry_run_deletes_nothing(app) -> None:
    ids = _seed(app)
    paths = _paths(app, ids)
    assert _purge(app).files_images_purged == 0
    assert paths["crop"].exists()
    _age(app, ids, 400)
    purge_entries = select(func.count()).where(m.AuditLog.action == "purge")
    with app.state.session_factory() as db:
        before = db.scalar(purge_entries)
    report = _purge(app, dry_run=True)
    assert report.dry_run and report.batches_deleted == 1
    assert paths["crop"].exists() and paths["original"].exists()
    with app.state.session_factory() as db:
        assert db.get(m.Batch, ids["batch"]) is not None
        assert db.scalar(purge_entries) == before


def test_data_purged_after_retention(app) -> None:
    ids = _seed(app)
    paths = _paths(app, ids)
    old, recent = utcnow() - timedelta(days=400), utcnow() - timedelta(days=10)
    with app.state.session_factory() as db:
        db.add(m.AuditLog(action="field_corrected", entity="document", at=old))
        db.add(m.AuditLog(action="field_corrected", entity="document", at=recent))
        db.add(m.Job(type=m.JobType.PURGE, status=m.JobStatus.DONE, finished_at=old))
        db.commit()
    _age(app, ids, 400)

    report = _purge(app)
    assert report.batches_deleted == 1 and report.audit_entries_deleted == 1
    assert report.jobs_deleted == 1 and report.rows_deleted
    assert not paths["batch"].exists()
    with app.state.session_factory() as db:
        assert db.get(m.Batch, ids["batch"]) is None
        assert db.get(m.Document, ids["doc"]) is None
        assert db.scalar(select(func.count()).select_from(m.DocumentField)) == 0
        actions = [a.action for a in db.scalars(select(m.AuditLog))]
        assert actions.count("field_corrected") == 1
        entry = db.scalars(select(m.AuditLog).where(m.AuditLog.action == "purge")).one()
        assert entry.after_json["batches_deleted"] == 1  # compteurs seulement
    vacuum(app.state.engine)


def test_batch_with_active_job_is_skipped(app) -> None:
    ids = _seed(app)
    _age(app, ids, 400)
    with app.state.session_factory() as db:
        db.add(m.Job(type=m.JobType.PROCESS_FILE, batch_id=ids["batch"], ref_id=ids["file"]))
        db.commit()
    report = _purge(app)
    assert report.batches_deleted == 0 and report.batches_skipped_busy >= 1
    with app.state.session_factory() as db:
        assert db.get(m.Batch, ids["batch"]) is not None
        with pytest.raises(BatchBusyError):
            purge_batch(db, app.state.settings, ids["batch"], user_id=None)


def test_debug_dirs_follow_image_retention(app) -> None:
    debug = app.state.settings.data_dir / "debug"
    old, new = debug / "20261001-120000", debug / "20261124-120000"
    for d in (old, new):
        d.mkdir(parents=True)
        (d / "viz.json").write_text("{}", encoding="utf-8")
    past = time.time() - 40 * 86_400
    os.utime(old, (past, past))
    assert _purge(app).debug_dirs_deleted == 1
    assert not old.exists() and new.exists()


def test_admin_purge_endpoint(app, admin_client) -> None:
    ids = _seed(app)
    r = admin_client.post("/api/admin/purge", json={"dry_run": True})
    assert r.status_code == 200 and r.json()["dry_run"] is True
    r = admin_client.post("/api/admin/purge", json={"batch_id": ids["batch"]})
    assert r.status_code == 200 and r.json() == {"purged": True}
    assert admin_client.get(f"/api/batches/{ids['batch']}").status_code == 404
    r = admin_client.post("/api/admin/purge", json={"batch_id": ids["batch"]})
    assert r.status_code == 404
    with app.state.session_factory() as db:
        assert db.scalars(select(m.AuditLog).where(m.AuditLog.action == "batch_purged")).one()


def test_purge_is_admin_only(app, client) -> None:
    make_user(app)
    login(client)
    assert client.post("/api/admin/purge", json={}).status_code == 403


def test_cli_purge_and_retention(app) -> None:
    from idreader.cli import app as cli

    ids = _seed(app)
    _age(app, ids, 31)
    runner = CliRunner()
    r = runner.invoke(cli, ["purge", "--show-retention"])
    assert r.exit_code == 0, r.output
    assert r.stdout.split() == ["30", "365"]
    r = runner.invoke(cli, ["purge"])
    assert r.exit_code == 0, r.output
    assert json.loads(r.stdout)["files_images_purged"] == 1
    r = runner.invoke(cli, ["purge", "--batch", "inconnu"])
    assert r.exit_code == 1


# --- Alerte « macOS a changé depuis le dernier banc » --------------------------------------


def _report(directory, name: str, started: str, macos: str | None, set_: str = "golden") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    data = {"set": set_, "git": "abc1234", "started_at": started, "environment": {"macos": macos}}
    (directory / name).write_text(json.dumps(data), encoding="utf-8")


def test_macos_alert(tmp_path) -> None:
    results = tmp_path / "results"
    # Hors macOS : jamais d'alerte.
    assert macos_alert(None, results).changed is False
    # Aucun banc sur macOS (rapports Windows seulement) : alerte.
    _report(results, "a_windows.json", "2026-11-25T10:00:00+00:00", None, "documents")
    (results / "casse.json").write_text("{", encoding="utf-8")
    a = macos_alert("26.1", results)
    assert a.changed and a.last_bench is None and "26.1" in (a.message or "")
    # Banc sur la même version : pas d'alerte.
    _report(results, "b.json", "2026-12-01T10:00:00+00:00", "26.1")
    assert macos_alert("26.1", results).changed is False
    # macOS mis à jour depuis : alerte avec les deux versions.
    a = macos_alert("26.2", results)
    assert a.changed and a.last_bench and a.last_bench.file == "b.json"
    assert "26.1" in (a.message or "") and "26.2" in (a.message or "")
    # Le banc le plus récent fait foi.
    _report(results, "c.json", "2026-12-05T10:00:00+00:00", "26.2")
    assert last_macos_bench(results).file == "c.json"
    assert macos_alert("26.2", results).changed is False


def test_health_reports_macos_alert(app, admin_client) -> None:
    h = admin_client.get("/api/admin/health").json()
    assert set(h["macos_alert"]) == {"current", "last_bench", "changed", "message"}
