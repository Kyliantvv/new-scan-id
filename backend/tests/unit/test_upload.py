from __future__ import annotations

import hashlib
import io
import logging

import pytest
from sqlalchemy import select

from idreader.core.db import make_engine
from idreader.core.models import AuditLog, File, Job, JobStatus
from idreader.core.storage import (
    StorageError,
    clean_original_name,
    detect_type,
    save_upload,
)
from idreader.worker.main import Worker
from tests.helpers import TINY_PNG

PDF = b"%PDF-1.4\n%synthetique\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def test_detect_type_by_magic_bytes() -> None:
    assert detect_type(PDF) == ("application/pdf", "pdf")
    assert detect_type(JPEG) == ("image/jpeg", "jpg")
    assert detect_type(TINY_PNG) == ("image/png", "png")
    assert detect_type(b"GIF89a") is None
    assert detect_type(b"MZ\x90\x00") is None


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("scan.pdf", "scan.pdf"),
        ("C:\\Users\\x\\passeport.jpg", "passeport.jpg"),
        ("../../etc/passwd", "passwd"),
        ("", "sans-nom"),
        (None, "sans-nom"),
        ("a" * 300, "a" * 255),
    ],
)
def test_clean_original_name(raw, clean) -> None:
    assert clean_original_name(raw) == clean


def test_save_upload_hashes_and_names_by_uuid(tmp_path) -> None:
    stored = save_upload(io.BytesIO(PDF), tmp_path / "f", 1000)
    assert stored.path == tmp_path / "f" / "original.pdf"
    assert stored.sha256 == hashlib.sha256(PDF).hexdigest()
    assert stored.size_bytes == len(PDF)


@pytest.mark.parametrize(
    ("content", "limit", "code"),
    [
        (b"", 100, "EMPTY_FILE"),
        (b"GIF89a....", 100, "UNSUPPORTED_FORMAT"),
        (PDF, 10, "FILE_TOO_LARGE"),
    ],
)
def test_save_upload_rejects_and_cleans(tmp_path, content, limit, code) -> None:
    with pytest.raises(StorageError) as exc:
        save_upload(io.BytesIO(content), tmp_path / "f", limit)
    assert exc.value.code == code
    assert not (tmp_path / "f").exists()


def _new_batch(client, name: str = "Salon test — novembre") -> str:
    r = client.post("/api/batches", json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_create_and_list_batches(operator_client) -> None:
    bid = _new_batch(operator_client)
    r = operator_client.get("/api/batches")
    assert r.status_code == 200
    assert [b["id"] for b in r.json()] == [bid]
    assert operator_client.post("/api/batches", json={"name": ""}).status_code == 422


def test_batches_require_auth(client) -> None:
    assert client.get("/api/batches").status_code == 401


def test_upload_multiple_files_creates_jobs(operator_client, app) -> None:
    bid = _new_batch(operator_client)
    r = operator_client.post(
        f"/api/batches/{bid}/files",
        files=[
            ("files", ("ERIKSSON_passeport.pdf", PDF, "application/pdf")),
            ("files", ("carte.png", TINY_PNG, "image/png")),
            ("files", ("note.txt", b"bonjour", "text/plain")),
        ],
    )
    assert r.status_code == 201, r.text
    results = r.json()
    assert [x["status"] for x in results] == ["queued", "queued", "rejected"]
    assert results[2]["error_code"] == "UNSUPPORTED_FORMAT"
    settings = app.state.settings
    with app.state.session_factory() as db:
        files = db.scalars(select(File).order_by(File.upload_order)).all()
        jobs = db.scalars(select(Job)).all()
        audits = db.scalars(select(AuditLog.action)).all()
    assert [f.upload_order for f in files] == [1, 2]
    assert files[0].original_name == "ERIKSSON_passeport.pdf"
    # nom stocké = UUID, jamais le nom d'origine
    for f in files:
        assert (
            f.storage_path
            == f"batches/{bid}/files/{f.id}/original.{'pdf' if f.mime.endswith('pdf') else 'png'}"
        )
        assert "ERIKSSON" not in f.storage_path
        assert (settings.data_dir / f.storage_path).is_file()
    assert {j.ref_id for j in jobs} == {f.id for f in files}
    assert all(j.status == JobStatus.QUEUED and j.batch_id == bid for j in jobs)
    assert audits.count("file_uploaded") == 2
    detail = operator_client.get(f"/api/batches/{bid}").json()
    assert detail["file_count"] == 2
    assert detail["job_counts"] == {"queued": 2}
    assert detail["files"][0]["job"]["status"] == "queued"


def test_identical_file_is_duplicate_without_job(operator_client, app) -> None:
    bid = _new_batch(operator_client)
    first = operator_client.post(
        f"/api/batches/{bid}/files", files=[("files", ("a.pdf", PDF))]
    ).json()
    again = operator_client.post(
        f"/api/batches/{bid}/files", files=[("files", ("b.pdf", PDF))]
    ).json()
    assert again[0]["status"] == "duplicate"
    assert again[0]["duplicate_of_id"] == first[0]["file_id"]
    with app.state.session_factory() as db:
        assert len(db.scalars(select(Job)).all()) == 1
        dup = db.get(File, again[0]["file_id"])
        assert (
            dup is not None and dup.storage_path == db.get(File, first[0]["file_id"]).storage_path
        )
    # un autre lot n'est pas concerné
    bid2 = _new_batch(operator_client, "Autre lot")
    other = operator_client.post(
        f"/api/batches/{bid2}/files", files=[("files", ("a.pdf", PDF))]
    ).json()
    assert other[0]["status"] == "queued"


def test_upload_size_limit(operator_client, app) -> None:
    app.state.settings = app.state.settings.model_copy(update={"max_upload_mb": 0})
    bid = _new_batch(operator_client)
    r = operator_client.post(f"/api/batches/{bid}/files", files=[("files", ("a.pdf", PDF))])
    assert r.json()[0]["error_code"] == "FILE_TOO_LARGE"


def test_upload_requires_csrf(operator_client) -> None:
    bid = _new_batch(operator_client)
    r = operator_client.post(
        f"/api/batches/{bid}/files",
        files=[("files", ("a.pdf", PDF))],
        headers={"X-CSRF-Token": "faux"},
    )
    assert r.status_code == 403


def test_upload_unknown_batch(operator_client) -> None:
    r = operator_client.post("/api/batches/inconnu/files", files=[("files", ("a.pdf", PDF))])
    assert r.status_code == 404


def test_uploaded_job_runs_to_done(operator_client, app) -> None:
    bid = _new_batch(operator_client)
    operator_client.post(f"/api/batches/{bid}/files", files=[("files", ("a.pdf", PDF))])
    engine = make_engine(app.state.settings)
    Worker(app.state.settings, engine, "w").run(exit_when_idle=True)
    engine.dispose()
    detail = operator_client.get(f"/api/batches/{bid}").json()
    assert detail["files"][0]["job"]["status"] == "done"


def test_documents_table_empty(operator_client) -> None:
    bid = _new_batch(operator_client)
    r = operator_client.get(f"/api/documents?batch={bid}")
    assert r.status_code == 200
    assert r.json() == []


def test_no_personal_data_in_logs(operator_client, app, caplog) -> None:
    """docs/05_SECURITE_RGPD.md : aucun nom de fichier d'origine ni valeur dans les logs."""
    caplog.set_level(logging.DEBUG)
    bid = _new_batch(operator_client)
    operator_client.post(
        f"/api/batches/{bid}/files", files=[("files", ("ERIKSSON_ANNA_L898902C3.pdf", PDF))]
    )
    operator_client.get(f"/api/documents?batch={bid}&q=ERIKSSON")
    engine = make_engine(app.state.settings)
    Worker(app.state.settings, engine, "w").run(exit_when_idle=True)
    engine.dispose()
    # Le logger « httpx » est celui du client de test (côté navigateur), pas de l'application.
    app_logs = " | ".join(r.getMessage() for r in caplog.records if r.name != "httpx")
    assert "file stored" in app_logs
    for secret in ("ERIKSSON", "ANNA", "L898902C3", "operateur@exemple.fr"):
        assert secret not in app_logs
