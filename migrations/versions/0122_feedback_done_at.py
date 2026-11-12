"""Beschwerden: Zeitpunkt der Erledigung (feedback.done_at)

Revision ID: 0122
Revises: 0121
Create Date: 2026-11-12 11:50:00

Name and address of a complaint go three years after it was settled (Ralf 2026-11-12; app/feedback.py,
forget_contacts in the hourly sweep). The time it was set to "done" is kept from now on; a complaint settled before
gets it at the sweep's first look. Only adds a column that may stay empty; the old code does not read it.
"""
from alembic import op
import sqlalchemy as sa


revision = '0122'
down_revision = '0121'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('feedback')}
    if 'done_at' not in columns:
        op.add_column('feedback', sa.Column('done_at', sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column('feedback', 'done_at')
