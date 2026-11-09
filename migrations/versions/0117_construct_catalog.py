"""Konstrukt-Katalog: Herkunft, Version und Release je Konstrukt

Revision ID: 0117
Revises: 0116
Create Date: 2026-11-09 12:00:00

construct.source: "catalog" for the seed constructs from the construct catalogue (app/construct_catalog.py),
"instance" for those made on this installation (linked elements, accepted submissions); the seeding sets "catalog"
on the seed constructs at the next start. construct.version: the catalogue's version ("4.0"), catalog_release: the
release of the snapshot it was taken from. Only adds columns that may stay empty or have a server_default; the old
code does not read them.
"""
from alembic import op
import sqlalchemy as sa


revision = '0117'
down_revision = '0116'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('construct')}
    if 'source' not in columns:
        op.add_column('construct', sa.Column('source', sa.String(20), nullable=False, server_default='instance'))
    if 'version' not in columns:
        op.add_column('construct', sa.Column('version', sa.String(20), nullable=True))
    if 'catalog_release' not in columns:
        op.add_column('construct', sa.Column('catalog_release', sa.String(40), nullable=True))


def downgrade():
    op.drop_column('construct', 'catalog_release')
    op.drop_column('construct', 'version')
    op.drop_column('construct', 'source')
