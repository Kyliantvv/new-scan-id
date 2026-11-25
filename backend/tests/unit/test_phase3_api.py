"""Phase 3 : détail, corrections journalisées, actions, images, exports, administration.

Documents écrits directement en base (valeurs fictives du pays UTO) : pas de pipeline ici.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from idreader.core import models as m
from tests.conftest import PASSWORD, login, make_user
from tests.helpers import TINY_PNG


def _seed(app, *, status: str = "to_review") -> dict[str, str]:
    """Lot, fichier, page, recadrage (PNG 1×1), document et champs fictifs."""
    settings = app.state.settings
    with app.state.session_factory() as db:
        batch = m.Batch(name="Lot test")
        db.add(batch)
        db.flush()
        f = m.File(
            batch_id=batch.id, original_name="scan-001.png", mime="image/png", sha256="0" * 64,
            size_bytes=len(TINY_PNG), storage_path="", upload_order=1,
        )  # fmt: skip
        db.add(f)
        db.flush()
        f.storage_path = f"batches/{batch.id}/files/{f.id}/original.png"
        page = m.Page(file_id=f.id, page_index=0, storage_path="", width=1, height=1)
        db.add(page)
        db.flush()
        page.storage_path = f"pages/{page.id}.png"
        crop = m.Crop(page_id=page.id, storage_path="", width=1, height=1, side="front")
        db.add(crop)
        db.flush()
        crop.storage_path = f"crops/{crop.id}.png"
        base = settings.data_dir / "batches" / batch.id
        for rel in (crop.storage_path, page.storage_path, f"files/{f.id}/original.png"):
            (base / rel).parent.mkdir(parents=True, exist_ok=True)
            (base / rel).write_bytes(TINY_PNG)
        doc = m.Document(
            batch_id=batch.id, status=status, doc_type="passport", issuing_state="UTO",
            mrz_valid=True, confidence=0.75, front_crop_id=crop.id, alerts_json=["EXPIRED"],
            status_reasons_json=[{"code": "FIELD_UNVERIFIED", "field": "surname",
                                  "label": "Valeur lue par un seul moteur, sans preuve"}],
        )  # fmt: skip
        db.add(doc)
        db.flush()
        for name, value, st in (
            ("surname", "ÉRIKSSON", "uncertain"),
            ("given_names", "ANNA MARIA", "ok"),
            ("date_of_birth", "1974-08-12", "ok"),
            ("document_number", "L898902C3", "ok"),
            ("place_of_birth", "=CMD()", "uncertain"),
        ):
            db.add(
                m.DocumentField(
                    document_id=doc.id,
                    name=name,
                    value=value,
                    status=st,
                    confidence=0.9,
                    evidence_json=[{"sources": ["mrz", "viz"], "reasons": []}],
                    candidates_json=["ERIKSSON"] if name == "surname" else [],
                    bbox_json={"x": 0, "y": 0, "w": 1, "h": 1} if name == "surname" else None,
                )
            )
        db.commit()
        return {"batch": batch.id, "doc": doc.id, "crop": crop.id, "page": page.id, "file": f.id}


@pytest.fixture
def admin_client(app, client):
    make_user(app, "admin@exemple.fr", admin=True)
    login(client, "admin@exemple.fr")
    return client


def test_list_rows_have_source_and_parsed_json(operator_client, app) -> None:
    ids = _seed(app)
    rows = operator_client.get(f"/api/documents?batch={ids['batch']}").json()
    assert len(rows) == 1
    row = rows[0]
    assert row["source_file"] == "scan-001.png" and row["page_index"] == 0
    assert row["preview_crop_id"] == ids["crop"]
    assert row["alerts_json"] == ["EXPIRED"]
    assert row["surname"] == "ÉRIKSSON"
    assert operator_client.get("/api/documents?q=ériks").json()[0]["id"] == ids["doc"]
    assert operator_client.get("/api/documents?q=zzz").json() == []


def test_detail(operator_client, app) -> None:
    ids = _seed(app)
    d = operator_client.get(f"/api/documents/{ids['doc']}").json()
    assert d["front"]["id"] == ids["crop"] and d["front"]["file_name"] == "scan-001.png"
    fields = {f["name"]: f for f in d["fields"]}
    assert fields["surname"]["label"] == "Nom"
    assert fields["surname"]["candidates"] == ["ERIKSSON"]
    assert fields["surname"]["bbox"] == {"x": 0, "y": 0, "w": 1, "h": 1}
    assert fields["surname"]["side"] == "front"
    assert fields["authority"]["status"] == "missing"
    assert d["alert_labels"] == ["Document expiré"]
    assert d["reasons"][0]["field"] == "surname"


def test_inline_correction_is_audited_and_validates(operator_client, app) -> None:
    ids = _seed(app)
    r = operator_client.patch(
        f"/api/documents/{ids['doc']}/fields", json={"fields": {"surname": "eriksson "}}
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["status"] == "manual_validated"
    surname = next(f for f in d["fields"] if f["name"] == "surname")
    assert surname["value"] == "ERIKSSON" and surname["status"] == "manual"
    assert surname["sources"] == ["human"]
    with app.state.session_factory() as db:
        entry = db.scalars(select(m.AuditLog).where(m.AuditLog.action == "field_corrected")).one()
        assert entry.before_json == {"field": "surname", "value": "ÉRIKSSON"}
        assert entry.after_json == {"field": "surname", "value": "ERIKSSON"}


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("date_of_birth", "12/08/1974", "INVALID_DATE"),
        ("sex", "Z", "INVALID_SEX"),
        ("status", "auto_validated", "FIELD_NOT_EDITABLE"),
    ],
)
def test_invalid_corrections(operator_client, app, field, value, code) -> None:
    ids = _seed(app)
    r = operator_client.patch(
        f"/api/documents/{ids['doc']}/fields", json={"fields": {field: value}}
    )
    assert r.status_code == 422 and r.json()["detail"]["code"] == code


def test_validate_reject_reprocess_delete(operator_client, app) -> None:
    ids = _seed(app)
    doc = ids["doc"]
    r = operator_client.post(
        f"/api/documents/{doc}/validate", json={"fields": {"date_of_birth": "1974-08-13"}}
    )
    assert r.json()["status"] == "manual_validated"
    r = operator_client.post(f"/api/documents/{doc}/reject")
    assert r.json()["status"] == "failed"
    assert r.json()["reasons"][0]["code"] == "NOT_AN_ID_DOCUMENT"
    assert operator_client.post(f"/api/documents/{doc}/reprocess").status_code == 202
    with app.state.session_factory() as db:
        assert db.scalar(select(m.Job).where(m.Job.ref_id == ids["file"])) is not None
    assert operator_client.delete(f"/api/documents/{doc}").status_code == 204
    assert operator_client.get(f"/api/documents/{doc}").status_code == 404
    assert operator_client.get(f"/api/crops/{ids['crop']}/image").status_code == 404


def test_images_are_authenticated_and_not_cached(operator_client, client, app) -> None:
    ids = _seed(app)
    for url in (f"/api/crops/{ids['crop']}/image", f"/api/pages/{ids['page']}/image",
                f"/api/files/{ids['file']}/original"):  # fmt: skip
        r = operator_client.get(url)
        assert r.status_code == 200 and r.content == TINY_PNG
        assert r.headers["cache-control"] == "no-store"
    operator_client.post("/api/auth/logout")
    assert client.get(f"/api/crops/{ids['crop']}/image").status_code == 401


def test_export_xlsx_has_real_dates(operator_client, app) -> None:
    ids = _seed(app)
    r = operator_client.post("/api/exports", json={"format": "xlsx", "batch": ids["batch"]})
    assert r.status_code == 200
    assert "idreader_" in r.headers["content-disposition"]
    ws = load_workbook(io.BytesIO(r.content)).active
    header = [c.value for c in ws[1]]
    assert header[0] == "Statut" and "Date de naissance" in header
    row = dict(zip(header, [c.value for c in ws[2]], strict=True))
    assert row["Date de naissance"] == datetime(1974, 8, 12)
    assert ws.cell(2, header.index("Date de naissance") + 1).number_format == "DD/MM/YYYY"
    assert row["Statut"] == "À vérifier" and row["Alertes"] == "Expiré"
    assert row["Lieu de naissance"] == "'=CMD()"  # pas de formule exécutée
    assert ws.freeze_panes == "A2" and ws.auto_filter.ref
    with app.state.session_factory() as db:
        entry = db.scalars(select(m.AuditLog).where(m.AuditLog.action == "export")).one()
        assert entry.after_json["rows"] == 1


def test_export_csv_for_french_excel(operator_client, app) -> None:
    ids = _seed(app)
    r = operator_client.post(
        "/api/exports",
        json={"format": "csv", "ids": [ids["doc"]], "columns": ["surname", "date_of_birth"],
              "include_status": False},
    )  # fmt: skip
    assert r.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig")), delimiter=";"))
    assert rows == [["Nom", "Date de naissance"], ["ÉRIKSSON", "12/08/1974"]]


def test_admin_only(operator_client) -> None:
    assert operator_client.get("/api/admin/settings").status_code == 403
    assert operator_client.get("/api/admin/stats").status_code == 403


def test_settings_and_golden(admin_client, app) -> None:
    ids = _seed(app, status="manual_validated")
    doc = ids["doc"]
    assert admin_client.post(f"/api/documents/{doc}/golden").status_code == 403
    s = admin_client.get("/api/admin/settings").json()
    assert s["golden_enabled"] is False and s["auto_validate_threshold"] == 0.95
    s["golden_enabled"] = True
    assert admin_client.put("/api/admin/settings", json=s).json()["golden_enabled"] is True
    assert admin_client.post(f"/api/documents/{doc}/golden").status_code == 201
    case = app.state.settings.data_dir / "golden" / "cases" / doc
    truth = json.loads((case / "truth.json").read_text(encoding="utf-8"))
    assert truth["fields"]["document_number"] == "L898902C3"
    assert (case / "front.png").is_file()
    bad = {**s, "auto_validate_threshold": 0.5}
    assert admin_client.put("/api/admin/settings", json=bad).status_code == 422


def test_users_admin(admin_client, app, client) -> None:
    r = admin_client.post(
        "/api/admin/users",
        json={"email": "Nouveau@Exemple.fr", "password": PASSWORD, "role": "operator"},
    )
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    assert r.json()["email"] == "nouveau@exemple.fr"
    short = admin_client.post(
        "/api/admin/users", json={"email": "x@exemple.fr", "password": "court"}
    )
    assert short.status_code == 422
    assert (
        admin_client.patch(f"/api/admin/users/{uid}", json={"is_active": False}).json()["is_active"]
        is False
    )
    me = next(
        u for u in admin_client.get("/api/admin/users").json() if u["email"] == "admin@exemple.fr"
    )
    assert (
        admin_client.patch(f"/api/admin/users/{me['id']}", json={"is_active": False}).status_code
        == 409
    )


def test_stats_learn_from_corrections(admin_client, app) -> None:
    ids = _seed(app)
    admin_client.patch(
        f"/api/documents/{ids['doc']}/fields", json={"fields": {"document_number": "L898902C8"}}
    )
    s = admin_client.get("/api/admin/stats").json()
    assert s["documents"] == 1
    assert s["most_corrected_fields"] == [["document_number", 1]]
    assert s["mrz_confusions"] == [["3", "8", 1]]
    assert s["by_country"] == {"UTO": 1}


def test_confusions_helper() -> None:
    from idreader.api.admin import confusions

    assert confusions("L898", "L8B8") == [("9", "B")]
    assert confusions("AB", "ABC") == []
