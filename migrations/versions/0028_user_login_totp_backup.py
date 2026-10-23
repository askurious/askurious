"""Ersatzcodes für den zweiten Faktor: user_login.totp_backup

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-09 17:00:00

Site admins who sign in with a password give a code from their authenticator app as well (Ralf 2026-10-09, app/
twofactor.py); when the phone is lost, one of eight backup codes shown once at the setup takes its place. The column
keeps their SHA-256 hashes (JSON list). A column that may stay empty: it only adds, so the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0028'
down_revision = '0027'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('user_login')}
    if 'totp_backup' not in have:
        with op.batch_alter_table('user_login', schema=None) as batch_op:
            batch_op.add_column(sa.Column('totp_backup', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('user_login', schema=None) as batch_op:
        batch_op.drop_column('totp_backup')
