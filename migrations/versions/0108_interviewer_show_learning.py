"""Interviewlink: Spalte interviewer_access.show_learning

Revision ID: 0108
Revises: 0107
Create Date: 2026-10-29 13:45:00

The right "Lernhilfen zeigen" of an interviewer link (app/interviewer.py, app/learning.py, Ralf 2026-10-29): with it
the sessions of the link see the learning aids (folds "Warum?" and the "?" at statistics) in the interview and coding
views, as a researcher with the switch on. On for every link, the existing ones too (Ralf). Only adds: a column with a
server_default; token, revision, devices, expiry and the other rights stay untouched, so every link and session works
on as before.
"""
from alembic import op
import sqlalchemy as sa


revision = '0108'
down_revision = '0107'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('interviewer_access')}
    if 'show_learning' not in columns:
        op.add_column('interviewer_access', sa.Column('show_learning', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    op.drop_column('interviewer_access', 'show_learning')
