"""Siegelkette: seal_status.fingerprint, seal_status.looked_at

Revision ID: 0045
Revises: 0044
Create Date: 2026-10-11 10:00:00

The hourly check of the seal chains reads a survey's participations again only when something changed since its last
check (app/seal.py, fingerprint and check_due; audit 2026-10-10, S6/P5): the last check keeps what the survey looked
like then, and when the round last looked at it. Two columns that may stay empty: it only adds, so the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0045'
down_revision = '0044'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('seal_status')}
    with op.batch_alter_table('seal_status', schema=None) as batch_op:
        if 'fingerprint' not in have:
            batch_op.add_column(sa.Column('fingerprint', sa.String(length=100), nullable=True))
        if 'looked_at' not in have:
            batch_op.add_column(sa.Column('looked_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('seal_status', schema=None) as batch_op:
        batch_op.drop_column('looked_at')
        batch_op.drop_column('fingerprint')
