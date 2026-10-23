"""Tagebuchstudie: enrolment.settings (Ruhezeiten der Person für zufällige Abfragen)

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-11 18:10:00

What a person sets in the app, for now the quiet hours that random prompts keep out of (app/diary.py). A column that
may stay empty: it only adds, so the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0048'
down_revision = '0047'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('enrolment')}
    if 'settings' not in have:
        with op.batch_alter_table('enrolment', schema=None) as batch_op:
            batch_op.add_column(sa.Column('settings', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('enrolment', schema=None) as batch_op:
        batch_op.drop_column('settings')
