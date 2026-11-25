"""Association recto/verso suggérée (étape J)

Révision : 0003
Précédente : 0002
Créée le : 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Colonnes nullables ajoutées sans recopie : la vue documents_flat n'est pas touchée.
    op.add_column('documents', sa.Column('suggested_pair_id', sa.String(length=36), nullable=True))
    op.add_column('documents', sa.Column('pair_score', sa.Float(), nullable=True))


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS documents_flat")
    with op.batch_alter_table('documents') as batch_op:
        batch_op.drop_column('pair_score')
        batch_op.drop_column('suggested_pair_id')
    op.execute(_view_sql())


def _view_sql() -> str:
    """Vue telle que définie à la révision 0002 (fichier voisin)."""
    import importlib.util
    from pathlib import Path

    path = Path(__file__).with_name('0002_field_uncertain.py')
    spec = importlib.util.spec_from_file_location('rev0002', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return str(module.DOCUMENTS_FLAT_SQL)
