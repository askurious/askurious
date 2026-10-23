"""Gemeinsamer Zähler der Ratenlimits: Tabelle rate_hit

Revision ID: 0053
Revises: 0052
Create Date: 2026-10-13 12:00:00

The rate limits that guard secrets (deletion codes, survey and diary codes, sign-in with a password, "Passwort
vergessen?", /verify, read tokens, secrets of the diary app, invitations and join links) counted in the memory of each
server process: with two participants' containers of 12 processes each, up to 24 times the configured number
(security audit 2026-10-13, TN-2/DP-3/AU-9/KR-8). They count here now, for all processes together (app/limits.py,
_take_shared). A new table only, without foreign keys: it only adds, so the deploy rolls (the container not yet
restarted keeps counting in its memory for that moment).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0053'
down_revision = '0052'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if 'rate_hit' in inspector.get_table_names():
        return
    op.create_table(
        'rate_hit',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('key', sa.String(length=32), nullable=False),
        sa.Column('at', sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('rate_hit_pkey')),
    )
    with op.batch_alter_table('rate_hit', schema=None) as batch_op:
        batch_op.create_index('ix_rate_hit_kind_key_at', ['kind', 'key', 'at'], unique=False)
        batch_op.create_index(batch_op.f('ix_rate_hit_at'), ['at'], unique=False)


def downgrade():
    with op.batch_alter_table('rate_hit', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_rate_hit_at'))
        batch_op.drop_index('ix_rate_hit_kind_key_at')
    op.drop_table('rate_hit')
