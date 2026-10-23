"""Anfrage „Passwort vergessen?“

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-07 12:00:00

user_login.reset_requested_at: when a person with e-mail sign-in last asked on the sign-in page for a new one-time
link (auth.forgot_password). Empty for every account that exists. A column that is there already is left as it is.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0016'
down_revision = '0015'
branch_labels = None
depends_on = None


def _has(table, column):
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if not _has('user_login', 'reset_requested_at'):
        with op.batch_alter_table('user_login', schema=None) as batch_op:
            batch_op.add_column(sa.Column('reset_requested_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('user_login', schema=None) as batch_op:
        batch_op.drop_column('reset_requested_at')
