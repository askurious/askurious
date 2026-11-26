"""Selbst eingetippte, unbestätigte Anmeldeadresse: user_login.email_self_chosen

Revision ID: 0129
Revises: 0128
Create Date: 2026-11-26 09:00:00

An account made on a link's page while mail is off signs in with an address the person typed and no mail confirmed.
The people search finds an account by its full address only when someone else entered it (an admin, an inviter) or a
mail confirmed it; this column marks the others (app/surveys/people.py). A new column that may stay empty: it only
adds, so the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


revision = '0129'
down_revision = '0128'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    if 'email_self_chosen' not in [c['name'] for c in sa.inspect(op.get_bind()).get_columns('user_login')]:
        op.add_column('user_login', sa.Column('email_self_chosen', sa.Boolean(), nullable=True))


def downgrade():
    op.drop_column('user_login', 'email_self_chosen')
