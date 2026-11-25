"""Étape J (appariement recto/verso) et doublons de document.

Scores calculés sur des faces construites à la main ; parcours complet sur une carte UTO
synthétique (recto et verso dans deux fichiers), ignoré si les modèles sont absents.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

import pytest
from sqlalchemy import select

from idreader.core import models as m
from idreader.pipeline.mrz.decode import MrzResult, decode
from idreader.pipeline.mrz.vote import BandReading, vote
from idreader.pipeline.pairing import AUTO_MIN, Side, pair_sides, score

TODAY = date(2026, 11, 24)
TD1 = [
    "I<UTOD231458907<<<<<<<<<<<<<<<",
    "7408122F1204159UTO<<<<<<<<<<<6",
    "ERIKSSON<<ANNA<MARIA<<<<<<<<<<",
]


def _mrz() -> MrzResult:
    voted = vote([BandReading(engine="ppocr", lines=TD1)])
    assert voted is not None
    r = decode(voted, today=TODAY)
    assert r is not None
    return r


def side(
    key: str, order: int, page: str, *, mrz: bool = False, texts: tuple[str, ...] = ()
) -> Side:
    return Side(key, order, page, "ID1", _mrz() if mrz else None, texts)


FRONT_TEXTS = ("Nom / Surname", "ERIKSSON", "N° / No", "D23145890", "12.08.1974")


def test_score_components() -> None:
    back = side("b", 2, "p2", mrz=True)
    assert score(back, side("f", 1, "p1", texts=FRONT_TEXTS)).hints == (
        "consecutive_files",
        "number",
        "name_or_dob",
    )
    assert score(back, side("f", 1, "p2")).score == pytest.approx(0.6)
    assert score(back, side("f", 5, "p5")).score == 0


def test_auto_pair_needs_clear_winner() -> None:
    back = side("back", 2, "p2", mrz=True)
    front = side("front", 1, "p1", texts=FRONT_TEXTS)
    other = side("other", 3, "p3", texts=("AUTRE",))
    auto, suggested = pair_sides([back, front, other])
    assert [(p.mrz_key, p.other_key) for p in auto] == [("back", "front")]
    assert auto[0].score >= AUTO_MIN
    assert suggested == []


def test_close_competitors_are_only_suggested() -> None:
    back = side("back", 2, "p2", mrz=True)
    a = side("a", 1, "p1", texts=FRONT_TEXTS)
    b = side("b", 3, "p3", texts=FRONT_TEXTS)  # même nom, même numéro : ambigu
    auto, suggested = pair_sides([back, a, b])
    assert auto == []
    assert len(suggested) == 1 and suggested[0].mrz_key == "back"


def test_consecutive_files_alone_is_only_a_suggestion() -> None:
    auto, suggested = pair_sides([side("back", 2, "p2", mrz=True), side("f", 1, "p1")])
    assert auto == [] and [p.other_key for p in suggested] == ["f"]


def test_two_mrz_sides_are_never_paired() -> None:
    auto, suggested = pair_sides([side("a", 1, "p1", mrz=True), side("b", 2, "p2", mrz=True)])
    assert auto == [] and suggested == []


# --- Doublons -----------------------------------------------------------------------------


def test_duplicate_documents_get_alert_and_link(migrated) -> None:
    from idreader.core.db import make_engine, make_session_factory
    from idreader.worker.store import mark_duplicates

    engine = make_engine(migrated)
    factory = make_session_factory(engine)
    with factory() as db:
        batch = m.Batch(name="Lot")
        db.add(batch)
        db.flush()
        ids = []
        for i in range(3):
            doc = m.Document(batch_id=batch.id, status="to_review", issuing_state="UTO")
            db.add(doc)
            db.flush()
            number = "D23145890" if i < 2 else "X00000000"
            db.add(
                m.DocumentField(
                    document_id=doc.id, name="document_number", value=number, status="ok"
                )
            )
            db.add(
                m.DocumentField(
                    document_id=doc.id, name="date_of_birth", value="1974-08-12", status="ok"
                )
            )
            ids.append(doc.id)
        assert mark_duplicates(db, batch.id) == 1
        docs = {d.id: d for d in db.scalars(select(m.Document))}
        assert docs[ids[1]].duplicate_of_id == ids[0]
        assert "DUPLICATE_DOCUMENT" in docs[ids[1]].alerts_json
        assert docs[ids[0]].duplicate_of_id is None and docs[ids[2]].duplicate_of_id is None
        # Correction du numéro : l'alerte disparaît au calcul suivant.
        f = db.scalars(select(m.DocumentField).where(
            m.DocumentField.document_id == ids[1], m.DocumentField.name == "document_number"
        )).one()  # fmt: skip
        f.value = "Y11111111"
        assert mark_duplicates(db, batch.id) == 0
        assert docs[ids[1]].alerts_json == [] and docs[ids[1]].duplicate_of_id is None
    engine.dispose()


# --- Parcours complet ---------------------------------------------------------------------

from tests.unit.pipeline.test_analyze import FONTS, VIZ  # noqa: E402


@pytest.mark.skipif(VIZ is None or not FONTS, reason="modèles absents")
def test_card_front_and_back_files_are_paired(migrated, monkeypatch, tmp_path) -> None:
    from idreader.bench.documents import generate_documents
    from idreader.core.config import get_settings
    from idreader.core.db import make_engine, make_session_factory
    from idreader.worker.main import Worker
    from idreader.worker.queue import enqueue, schedule_pairing
    from idreader.worker.store import unpair_document

    monkeypatch.setenv("IDR_DUMMY_PIPELINE", "false")
    monkeypatch.setenv("IDR_ALLOW_SPECIMEN", "true")
    get_settings.cache_clear()
    settings = get_settings()
    cases = generate_documents(
        tmp_path / "gen", count=4, seed=3, models_dir=settings.models_dir,
        today=date.today(), a4_share=0,
    )  # fmt: skip
    case = next(c for c in cases if c.kind == "id_card" and c.quality == "good")
    engine = make_engine(settings)
    factory = make_session_factory(engine)
    with factory() as db:
        batch = m.Batch(name="Cartes")
        db.add(batch)
        db.flush()
        for order, name in enumerate(case.images, start=1):
            content = (tmp_path / "gen" / name).read_bytes()
            f = m.File(
                batch_id=batch.id, original_name=f"face{order}.jpg", mime="image/jpeg",
                sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content),
                storage_path="", upload_order=order,
            )  # fmt: skip
            db.add(f)
            db.flush()
            f.storage_path = f"batches/{batch.id}/files/{f.id}/original.jpg"
            path = settings.data_dir / f.storage_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            enqueue(db, m.JobType.PROCESS_FILE, ref_id=f.id, batch_id=batch.id)
        db.commit()
        batch_id = batch.id
    Worker(settings, engine, "w").run(exit_when_idle=True)
    with factory() as db:
        assert len(db.scalars(select(m.Document)).all()) == 2
        job = db.scalars(select(m.Job).where(m.Job.type == m.JobType.PAIR_BATCH)).one()
        assert job.run_after > job.created_at + timedelta(seconds=60)  # attente de 2 min
        schedule_pairing(db, batch_id, timedelta(0))
        db.commit()
    Worker(settings, engine, "w").run(exit_when_idle=True)
    with factory() as db:
        docs = db.scalars(select(m.Document)).all()
        assert len(docs) == 1, [d.status for d in docs]
        doc = docs[0]
        assert doc.front_crop_id and doc.back_crop_id and doc.mrz_format == "TD1"
        fields = {
            f.name: f
            for f in db.scalars(
                select(m.DocumentField).where(m.DocumentField.document_id == doc.id)
            )
        }
        assert fields["date_of_birth"].value == case.fields["date_of_birth"]
        # Le recto apporte le lieu de naissance ; son cadre est sur le recto.
        assert fields["place_of_birth"].value == case.fields["place_of_birth"]
        assert fields["place_of_birth"].bbox_json["side"] == "front"
        restored = unpair_document(db, settings, doc, user_id=None)
        db.commit()
        assert len(restored) == 2
        assert {bool(d.front_crop_id) for d in restored} == {True, False}
    engine.dispose()
    get_settings.cache_clear()
