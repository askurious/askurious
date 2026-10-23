"""Anmeldung: Fehlversuche in Folge (user_login.failed_streak, hard_locked_at)

Revision ID: 0051
Revises: 0050
Create Date: 2026-10-12 10:00:00

Wrong passwords and codes in a row since the last right one; at 100 the sign-in with password and code is locked
until a site admin releases it (NIST SP 800-63B-4, audit 2026-10-11 AU3/N11). Two columns that may stay empty or have
a server_default: they only add, so the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0051'
down_revision = '0050'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('user_login')}
    with op.batch_alter_table('user_login', schema=None) as batch_op:
        if 'failed_streak' not in have:
            batch_op.add_column(sa.Column('failed_streak', sa.Integer(), nullable=False, server_default='0'))
        if 'hard_locked_at' not in have:
            batch_op.add_column(sa.Column('hard_locked_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('user_login', schema=None) as batch_op:
        batch_op.drop_column('hard_locked_at')
        batch_op.drop_column('failed_streak')
