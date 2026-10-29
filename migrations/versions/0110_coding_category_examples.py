"""Interviews kodieren: Spalte coding_category.examples (weitere Beispiele)

Revision ID: 0110
Revises: 0109
Create Date: 2026-10-29 14:30:00

Up to two further examples of a category besides its anchor example (Ralf 2026-10-29), chosen in the coding view like
the anchor example: [{"interview", "coding", "quote", "label"}]. Without a choice the coding guide and the Excel export
take the first coded passages. Only adds a column that may stay empty.
"""
from alembic import op
import sqlalchemy as sa


revision = '0110'
down_revision = '0109'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('coding_category')}
    if 'examples' not in columns:
        op.add_column('coding_category', sa.Column('examples', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('coding_category', 'examples')
