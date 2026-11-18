"""Rückmeldungen: Bildschirmfoto (feedback.shot, shot_w, shot_h)

Revision ID: 0125
Revises: 0124
Create Date: 2026-11-18 11:00:00

A piece of feedback may carry one image (app/feedback_shots.py; concept "Bildschirmfotos an Rückmeldungen", Ralf
2026-11-18): its file name in instance/feedback-shots/ and its size. Only adds columns that may stay empty; the old
code does not read them.
"""
from alembic import op
import sqlalchemy as sa


revision = '0125'
down_revision = '0124'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('feedback')}
    for name, kind in (('shot', sa.String(length=40)), ('shot_w', sa.Integer()), ('shot_h', sa.Integer())):
        if name not in columns:
            op.add_column('feedback', sa.Column(name, kind, nullable=True))


def downgrade():
    with op.batch_alter_table('feedback') as batch:
        for name in ('shot_h', 'shot_w', 'shot'):
            batch.drop_column(name)
