"""Letzte Prüfung der Siegelkette: seal_status

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-10 12:00:00

The seal chain is checked against the participations stored now every hour, at "Jetzt prüfen" and for /verify/
(app/seal.py, siegelkette-konzept.md E1, E2); the last result of each survey is kept here. A new table without foreign
keys (kept by the survey's uid like the chain): it only adds, so the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0039'
down_revision = '0038'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    bind = op.get_bind()
    if 'seal_status' not in set(sa.inspect(bind).get_table_names()):
        op.create_table('seal_status',
            sa.Column('survey_uid', sa.String(length=16), nullable=False),
            sa.Column('checked_at', sa.DateTime(), nullable=False),
            sa.Column('chain_ok', sa.Boolean(), nullable=False),
            sa.Column('data_ok', sa.Boolean(), nullable=False),
            sa.Column('present', sa.Integer(), nullable=False),
            sa.Column('missing', sa.Integer(), nullable=False),
            sa.Column('changed', sa.Integer(), nullable=False),
            sa.Column('unsealed', sa.Integer(), nullable=False),
            sa.Column('problems', sa.JSON(), nullable=True),
            sa.Column('notified', sa.String(length=64), nullable=True),
            sa.PrimaryKeyConstraint('survey_uid', name=op.f('seal_status_pkey')))


def downgrade():
    op.drop_table('seal_status')
