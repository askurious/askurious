"""Wann Forschende arbeiten: table researcher_activity

Revision ID: 0128
Revises: 0127
Create Date: 2026-11-24 12:15:00

Administration > Befragungen shows next to "Wann Teilnahmen eingehen" when researchers work, to plan maintenance
windows (Ralf 2026-11-24): per hour only the number of sign-in sessions active in it, no account, no session
(app/researcher_activity.py). A new table only, without foreign keys: it only adds, so the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


revision = '0128'
down_revision = '0127'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    if 'researcher_activity' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'researcher_activity',
        sa.Column('hour', sa.DateTime(), nullable=False),
        sa.Column('n', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('hour', name=op.f('researcher_activity_pkey')),
    )


def downgrade():
    op.drop_table('researcher_activity')
