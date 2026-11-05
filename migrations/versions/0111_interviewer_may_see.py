"""Interviewende: Spalte interviewer_access.may_see (Sichtrecht)

Revision ID: 0111
Revises: 0110
Create Date: 2026-11-05 11:10:00

What an interviewer link sees besides its own interviews (Ralf 2026-11-05): "own" (only them, as before), "all" (all
interviews of the guide, others' only to read) or "results" (also the guide's results page). Only adds a column with a
server_default, so every link keeps "own".
"""
from alembic import op
import sqlalchemy as sa


revision = '0111'
down_revision = '0110'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('interviewer_access')}
    if 'may_see' not in columns:
        op.add_column('interviewer_access', sa.Column('may_see', sa.String(length=8), nullable=False, server_default='own'))


def downgrade():
    op.drop_column('interviewer_access', 'may_see')
