"""Testlauf-Kommentare: Fremdschlüssel author_id und handled_by_id, wo sie fehlen

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04 21:55:00

The two columns of preview_comment came into older databases through add_missing_columns, which adds a column
without its foreign key (Ralf's server showed both under "Abweichungen vom Schema des Codes"). Databases built by
0001 have them already; there nothing happens. Before adding one, references to accounts that no longer exist are
set to NULL, as the key's ON DELETE SET NULL would have done.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

KEYS = (("author_id", "preview_comment_author_id_fkey"), ("handled_by_id", "preview_comment_handled_by_id_fkey"))


def _missing():
    have = {tuple(fk["constrained_columns"]) for fk in sa.inspect(op.get_bind()).get_foreign_keys("preview_comment")}
    return [(column, name) for column, name in KEYS if (column,) not in have]


def upgrade():
    missing = _missing()
    if not missing:
        return
    for column, _ in missing:
        op.execute(f'UPDATE preview_comment SET {column} = NULL WHERE {column} IS NOT NULL '
                   f'AND {column} NOT IN (SELECT id FROM "user")')
    with op.batch_alter_table('preview_comment', schema=None) as batch_op:
        for column, name in missing:
            batch_op.create_foreign_key(name, 'user', [column], ['id'], ondelete='SET NULL')


def downgrade():
    pass  # the keys belong to the schema of 0001: going back to 0001 keeps them
