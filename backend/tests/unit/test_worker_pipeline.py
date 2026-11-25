"""Job `process_file` avec le vrai pipeline : pages, recadrages, artefacts, documents, champs.

Document synthétique UTO uniquement ; ignoré si les modèles ou les polices sont absents.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from idreader.core.config import get_settings
from idreader.core.db import make_engine, make_session_factory
from idreader.core.models import Crop, Document, DocumentField, JobStatus, Page
from idreader.worker import queue
from idreader.worker.main import Worker
from tests.helpers import get_job, make_batch_with_file
from tests.unit.pipeline.test_analyze import FONTS, VIZ

pytestmark = pytest.mark.skipif(VIZ is None or not FONTS, reason="modèles absents")


@pytest.fixture
def real_settings(migrated, monkeypatch):
    monkeypatch.setenv("IDR_DUMMY_PIPELINE", "false")
    monkeypatch.setenv("IDR_ALLOW_SPECIMEN", "true")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def test_process_file_writes_documents(real_settings, tmp_path) -> None:
    from idreader.bench.documents import generate_documents

    (case,) = generate_documents(
        tmp_path / "gen", count=1, seed=3, models_dir=real_settings.models_dir,
        today=date.today(), a4_share=0,
    )  # fmt: skip
    content = (tmp_path / "gen" / case.images[0]).read_bytes()
    engine = make_engine(real_settings)
    factory = make_session_factory(engine)
    batch_id, file_id, jid = make_batch_with_file(factory, real_settings, content)

    assert Worker(real_settings, engine, "w").run(exit_when_idle=True) == 1
    job = get_job(factory, jid)
    assert job.status == JobStatus.DONE, job.error_code

    batch_dir = real_settings.data_dir / "batches" / batch_id
    with factory() as db:
        pages = db.scalars(select(Page).where(Page.file_id == file_id)).all()
        assert len(pages) == 1 and (batch_dir / pages[0].storage_path).is_file()
        crops = db.scalars(select(Crop).where(Crop.page_id == pages[0].id)).all()
        assert len(crops) == 1 and crops[0].has_mrz and crops[0].physical_format == "ID3"
        assert (batch_dir / "artifacts" / crops[0].id / "result.json").is_file()
        doc = db.scalars(select(Document).where(Document.front_crop_id == crops[0].id)).one()
        assert doc.status in ("to_review", "auto_validated")
        assert doc.issuing_state == "UTO" and doc.mrz_format == "TD3"
        fields = {
            f.name: f.value
            for f in db.scalars(select(DocumentField).where(DocumentField.document_id == doc.id))
        }
        assert fields["surname"] == case.fields["surname"]
        assert fields["date_of_birth"] == case.fields["date_of_birth"]
        first_doc = doc.id

    # Rejouer le job remplace les résultats au lieu de les dupliquer.
    with factory() as db:
        queue.enqueue(db, "process_file", ref_id=file_id, batch_id=batch_id)
        db.commit()
    Worker(real_settings, engine, "w").run(exit_when_idle=True)
    with factory() as db:
        docs = db.scalars(select(Document).where(Document.batch_id == batch_id)).all()
        assert len(docs) == 1 and docs[0].id != first_doc
        assert len(db.scalars(select(Page)).all()) == 1
    engine.dispose()
