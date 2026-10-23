"""Geplanter Start der Feldphase: survey.start_at, survey.start_plan

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-09 20:00:00

"Start planen" on the page "Datenerhebung": a draft or paused survey starts by itself at a set date and time
(lifecycle.start_due_surveys). start_at holds the moment (UTC), start_plan the end criteria of that start, or the note
of a planned start that did not go through. Two columns that may stay empty: it only adds, so the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0030'
down_revision = '0029'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('survey')}
    with op.batch_alter_table('survey', schema=None) as batch_op:
        if 'start_at' not in have:
            batch_op.add_column(sa.Column('start_at', sa.DateTime(), nullable=True))
        if 'start_plan' not in have:
            batch_op.add_column(sa.Column('start_plan', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('survey', schema=None) as batch_op:
        batch_op.drop_column('start_plan')
        batch_op.drop_column('start_at')
