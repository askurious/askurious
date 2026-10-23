"""Shared counters of the limits against load: table rate_count

Revision ID: 0089
Revises: 0088
Create Date: 2026-10-18 22:00:00

The limits against scripts that start participations (START_LIMIT, SURVEY_SENDER_LIMIT), against live votes and
against complaints counted in the memory of each server process: with two participants' containers of 12 processes
each, up to 24 times the configured number (audit 2026-10-18, BIZ-01). They count here now, for all processes together
(app/limits.py, _take_counted): one row per kind, sender and time slot. A new table only, without foreign keys: it only
adds, so the deploy rolls (the container not yet restarted keeps counting in its memory for that moment).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0089'
down_revision = '0088'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if 'rate_count' in inspector.get_table_names():
        return
    op.create_table(
        'rate_count',
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('key', sa.String(length=32), nullable=False),
        sa.Column('slot', sa.Integer(), nullable=False),
        sa.Column('n', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('kind', 'key', 'slot', name=op.f('rate_count_pkey')),
    )
    with op.batch_alter_table('rate_count', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_rate_count_slot'), ['slot'], unique=False)


def downgrade():
    with op.batch_alter_table('rate_count', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_rate_count_slot'))
    op.drop_table('rate_count')
