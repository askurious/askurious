"""Betreuung als Anfrage: account_supervisor.pending

Revision ID: 0121
Revises: 0120
Create Date: 2026-11-12 10:20:00

An admin of an organisation who makes themself the supervisor of an account only asks: the row waits as pending
until the person confirms it on "Konto" (app/account_types.py, request_supervision); a pending supervisor is not
made admin of the person's new surveys. Rows from before are confirmed supervisions (server default false). Only
adds a column with a server default; the old code does not read it and inserts confirmed rows.
"""
from alembic import op
import sqlalchemy as sa


revision = '0121'
down_revision = '0120'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('account_supervisor')}
    if 'pending' not in columns:
        op.add_column('account_supervisor', sa.Column('pending', sa.Boolean(), nullable=False, server_default='0'))


def downgrade():
    op.drop_column('account_supervisor', 'pending')
