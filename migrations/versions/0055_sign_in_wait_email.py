"""Warteliste der Anmeldung: E-Mail-Adresse für die Nachricht, wenn ein Platz frei ist (sign_in_wait.email, .lang)

Revision ID: 0055
Revises: 0054
Create Date: 2026-10-13 18:00:00

Whoever is put on the waiting list of "every ORCID holder" can leave an e-mail address on the page of the waiting list
(auth.waiting_list); a mail tells them when a place is free (signin.admit_waiting), and the address goes with it. Two
columns that may stay empty: they only add, so the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0055'
down_revision = '0054'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('sign_in_wait')}
    with op.batch_alter_table('sign_in_wait', schema=None) as batch_op:
        if 'email' not in have:
            batch_op.add_column(sa.Column('email', sa.String(length=254), nullable=True))
        if 'lang' not in have:
            batch_op.add_column(sa.Column('lang', sa.String(length=5), nullable=True))


def downgrade():
    with op.batch_alter_table('sign_in_wait', schema=None) as batch_op:
        batch_op.drop_column('lang')
        batch_op.drop_column('email')
