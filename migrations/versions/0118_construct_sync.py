"""Konstrukt-Katalog: Abgleich der Instanz (Tabelle construct_sync)

Revision ID: 0118
Revises: 0117
Create Date: 2026-11-09 14:00:00

Each look at the construct catalogue (app/construct_sync.py): nightly or manual sync, uploaded manifest, going back.
The last row with status "applied" holds the active manifest. A new table only, nothing the old code reads.
"""
from alembic import op
import sqlalchemy as sa


revision = '0118'
down_revision = '0117'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    if 'construct_sync' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'construct_sync',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('trigger', sa.String(20), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('release', sa.String(40), nullable=True),
        sa.Column('commit', sa.String(40), nullable=True),
        sa.Column('source', sa.String(500), nullable=False),
        sa.Column('signature', sa.String(300), nullable=False),
        sa.Column('changes', sa.JSON(), nullable=True),
        sa.Column('manifest', sa.JSON(), nullable=True),
        sa.Column('error', sa.Text(), nullable=False),
    )
    op.create_index('ix_construct_sync_created_at', 'construct_sync', ['created_at'])
    op.create_index('ix_construct_sync_status', 'construct_sync', ['status'])


def downgrade():
    op.drop_table('construct_sync')
