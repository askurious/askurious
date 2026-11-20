"""Kategoriensystem-Diagramm: Ansicht je Person und Auswertung (coding_key.view)

Revision ID: 0127
Revises: 0126
Create Date: 2026-11-20 16:30:00

The diagram of the category system can be drawn across, in columns from the top or as a tree from the top (Ralf
2026-11-20); what a person last chose (direction, bars, lines, colours) stays for them in that analysis, next to
their keys 1 to 9. Only adds a column that may stay empty; the old code does not read it.
"""
from alembic import op
import sqlalchemy as sa


revision = '0127'
down_revision = '0126'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    bind = op.get_bind()
    columns = {c['name'] for c in sa.inspect(bind).get_columns('coding_key')}
    if 'view' not in columns:
        op.add_column('coding_key', sa.Column('view', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('coding_key') as batch:
        batch.drop_column('view')
