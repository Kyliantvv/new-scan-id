"""Statut de champ « uncertain » (lu par un seul moteur ou sans preuve, étape L)

Révision : 0002
Précédente : 0001
Créée le : 2026-11-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# La vue référence `fields` : SQLite refuse de renommer la table recopiée tant qu'elle existe.
DOCUMENTS_FLAT_SQL = """
CREATE VIEW documents_flat AS
SELECT d.id, d.batch_id, d.status, d.status_reasons_json, d.doc_type, d.doc_code,
  d.issuing_state, d.mrz_format, d.mrz_valid, d.confidence, d.alerts_json,
  d.duplicate_of_id, d.created_at, d.updated_at,
  MAX(CASE WHEN f.name = 'surname' THEN f.value_native END) AS surname_native,
  MAX(CASE WHEN f.name = 'surname' THEN f.value END) AS surname,
  MAX(CASE WHEN f.name = 'given_names' THEN f.value END) AS given_names,
  MAX(CASE WHEN f.name = 'usage_name' THEN f.value END) AS usage_name,
  MAX(CASE WHEN f.name = 'sex' THEN f.value END) AS sex,
  MAX(CASE WHEN f.name = 'date_of_birth' THEN f.value END) AS date_of_birth,
  MAX(CASE WHEN f.name = 'place_of_birth' THEN f.value END) AS place_of_birth,
  MAX(CASE WHEN f.name = 'nationality' THEN f.value END) AS nationality,
  MAX(CASE WHEN f.name = 'document_number' THEN f.value END) AS document_number,
  MAX(CASE WHEN f.name = 'date_of_issue' THEN f.value END) AS date_of_issue,
  MAX(CASE WHEN f.name = 'date_of_expiry' THEN f.value END) AS date_of_expiry,
  MAX(CASE WHEN f.name = 'authority' THEN f.value END) AS authority,
  MAX(CASE WHEN f.name = 'personal_number' THEN f.value END) AS personal_number
FROM documents d LEFT JOIN fields f ON f.document_id = d.id
GROUP BY d.id
"""

OLD = "status IN ('ok', 'repaired', 'conflict', 'missing', 'manual')"
NEW = "status IN ('ok', 'repaired', 'uncertain', 'conflict', 'missing', 'manual')"


def _swap(check: str) -> None:
    op.execute("DROP VIEW IF EXISTS documents_flat")
    with op.batch_alter_table('fields') as batch_op:
        batch_op.drop_constraint('ck_fields_status', type_='check')
        batch_op.create_check_constraint('ck_fields_status', check)
    op.execute(DOCUMENTS_FLAT_SQL)


def upgrade() -> None:
    _swap(NEW)


def downgrade() -> None:
    op.execute("UPDATE fields SET status = 'conflict' WHERE status = 'uncertain'")
    _swap(OLD)
