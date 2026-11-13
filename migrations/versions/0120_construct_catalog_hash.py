"""Konstrukt-Katalog: Fingerabdruck dessen, was der Katalog geschrieben hat (construct.catalog_hash)

Revision ID: 0120
Revises: 0119
Create Date: 2026-11-09 15:50:00

constructs.row_fingerprint of a construct as the catalogue wrote it last: a construct changed on this installation
since then differs, and a newer version of the catalogue does not overwrite it (Administration > Konstrukte >
Katalog: keep it as a construct of its own or take the catalogue's version). Filled at the first start from the
catalogue's version then. Only adds a column that may stay empty; the old code does not read it.
"""
from alembic import op
import sqlalchemy as sa


revision = '0120'
down_revision = '0119'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('construct')}
    if 'catalog_hash' not in columns:
        op.add_column('construct', sa.Column('catalog_hash', sa.String(64), nullable=True))


def downgrade():
    op.drop_column('construct', 'catalog_hash')
