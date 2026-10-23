"""Tagebuchstudie: persönliche Codes verbinden wieder (diary_invite.enrolment_id, diary_invite.released_at)

Revision ID: 0054
Revises: 0053
Create Date: 2026-10-13 12:00:00

A personal invitation code of a diary study remembers the person who joined with it (enrolment_id), so that the same
code connects a new phone to that participation again, at any time or once after the researchers released it
(released_at; diary.rejoin). Two columns that may stay empty: they only add, so the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0054'
down_revision = '0053'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('diary_invite')}
    with op.batch_alter_table('diary_invite', schema=None) as batch_op:
        if 'enrolment_id' not in have:
            batch_op.add_column(sa.Column('enrolment_id', sa.Integer(), nullable=True))
        if 'released_at' not in have:
            batch_op.add_column(sa.Column('released_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('diary_invite', schema=None) as batch_op:
        batch_op.drop_column('released_at')
        batch_op.drop_column('enrolment_id')
