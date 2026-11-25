"""Lignes du tableau des documents (vue `documents_flat` + fichier source), partagées par
l'API et les exports. Clauses fixes, valeurs toujours liées (jamais concaténées)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

JSON_COLUMNS = ("status_reasons_json", "alerts_json")
SEARCHED = ("surname", "given_names", "usage_name", "document_number", "personal_number")
SORTABLE = {"created_at", "status", "surname", "date_of_birth", "issuing_state", "confidence"}

BASE_SQL = """
SELECT df.*, d.front_crop_id, d.back_crop_id,
  COALESCE(d.front_crop_id, d.back_crop_id) AS preview_crop_id,
  f.original_name AS source_file, p.page_index AS page_index
FROM documents_flat df
JOIN documents d ON d.id = df.id
LEFT JOIN crops c ON c.id = COALESCE(d.front_crop_id, d.back_crop_id)
LEFT JOIN pages p ON p.id = c.page_id
LEFT JOIN files f ON f.id = p.file_id
"""


def document_rows(
    db: Session,
    *,
    batch: str | None = None,
    status: str | None = None,
    ids: Sequence[str] | None = None,
    q: str | None = None,
    sort: str | None = None,
    offset: int = 0,
    limit: int = 1000,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if batch is not None:
        clauses.append("df.batch_id = :batch")
        params["batch"] = batch
    if status is not None:
        clauses.append("df.status = :status")
        params["status"] = status
    if ids is not None:
        clauses.append("df.id IN :ids")
        params["ids"] = list(ids) or [""]
    if q:
        clauses.append("(" + " OR ".join(f"df.{c} LIKE :q" for c in SEARCHED) + ")")
        # Valeurs stockées en majuscules ; LIKE de SQLite ignore la casse en ASCII seulement.
        params["q"] = f"%{q.strip().upper()}%"
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    desc = bool(sort and sort.startswith("-"))
    key = (sort or "created_at").lstrip("-")
    order = f"df.{key if key in SORTABLE else 'created_at'} {'DESC' if desc else 'ASC'}, df.id"
    stmt = text(f"{BASE_SQL} {where} ORDER BY {order} LIMIT :limit OFFSET :offset")
    if ids is not None:
        stmt = stmt.bindparams(bindparam("ids", expanding=True))
    rows = []
    for r in db.execute(stmt, params).mappings():
        row = dict(r)
        for col in JSON_COLUMNS:
            if isinstance(row.get(col), str):
                row[col] = json.loads(row[col])
        rows.append(row)
    return rows
