"""Einzelne „Warum?“-Erklärungen ausblenden

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-06 20:00:00

user.learning_hidden: the keys of the folds "Warum?" (app/learning.REASONS) a person chose "Nicht mehr zeigen" for
(concept "Lernhilfen: Erweiterungen und Verbesserungen", E7). Empty for every account that exists: all folds are
shown as before. A column that is there already is left as it is.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def _has(table, column):
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if not _has('user', 'learning_hidden'):
        with op.batch_alter_table('user', schema=None) as batch_op:
            batch_op.add_column(sa.Column('learning_hidden', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('learning_hidden')
