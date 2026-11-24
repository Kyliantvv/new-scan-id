"""Journal d'audit en base (docs/05_SECURITE_RGPD.md § Journal d'audit).

Contenu : qui, quand, quoi (type d'entité + identifiant). L'avant/après des modifications
reste en base et suit la purge des données ; il n'est jamais écrit dans le journal technique.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from idreader.core.db import utcnow
from idreader.core.models import AuditLog


def record(
    db: Session,
    action: str,
    *,
    user_id: str | None,
    entity: str | None = None,
    entity_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            entity=entity,
            entity_id=entity_id,
            before_json=before,
            after_json=after,
            at=utcnow(),
        )
    )
