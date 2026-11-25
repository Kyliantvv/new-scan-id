"""Export du tableau en XLSX ou CSV (journalisé : qui, quand, combien de lignes).

POST plutôt que GET : la sélection peut compter des milliers d'identifiants (docs/DECISIONS.md).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from idreader.api.deps import AuthDep, DbDep
from idreader.core import audit
from idreader.core.rows import document_rows
from idreader.exports.tabular import choose_columns, to_csv, to_xlsx

log = logging.getLogger(__name__)
router = APIRouter(tags=["exports"])

MAX_ROWS = 50_000
MEDIA = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
}


class ExportIn(BaseModel):
    format: Literal["xlsx", "csv"]
    batch: str | None = None
    status: str | None = None
    ids: list[str] | None = Field(default=None, max_length=MAX_ROWS)
    columns: list[str] | None = None
    include_status: bool = True


@router.post("/exports")
def export(body: ExportIn, auth: AuthDep, db: DbDep) -> Response:
    rows = document_rows(db, batch=body.batch, status=body.status, ids=body.ids, limit=MAX_ROWS)
    columns = choose_columns(body.columns, body.include_status)
    content = to_xlsx(rows, columns) if body.format == "xlsx" else to_csv(rows, columns)
    audit.record(
        db, "export", user_id=auth.user_id, entity="batch", entity_id=body.batch,
        after={"format": body.format, "rows": len(rows), "columns": len(columns)},
    )  # fmt: skip
    db.commit()
    log.info("export format=%s rows=%d", body.format, len(rows))
    # Nom neutre : ni nom de lot ni donnée personnelle.
    name = f"idreader_{datetime.now():%Y-%m-%d_%H%M}.{body.format}"
    return Response(
        content,
        media_type=MEDIA[body.format],
        headers={
            "Content-Disposition": f'attachment; filename="{name}"',
            "Cache-Control": "no-store",
        },
    )
