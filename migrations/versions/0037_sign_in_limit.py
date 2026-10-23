"""Obergrenze für die freie Anmeldung: user_login.came_via, sign_in_wait

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-10 12:00:00

"Every ORCID holder" up to a limit (app/signin.py, gruppen-freischaltung-konzept.md Stufe 3): accounts that came in by
the switch alone are marked and counted; who comes when the limit is reached waits on a list. A column that may stay
empty and a new table: it only adds, so the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0037'
down_revision = '0036'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if 'came_via' not in {c['name'] for c in inspector.get_columns('user_login')}:
        with op.batch_alter_table('user_login', schema=None) as batch_op:
            batch_op.add_column(sa.Column('came_via', sa.String(length=10), nullable=True))
    if 'sign_in_wait' not in set(inspector.get_table_names()):
        op.create_table('sign_in_wait',
            sa.Column('orcid', sa.String(length=19), nullable=False),
            sa.Column('name', sa.String(length=200), nullable=False),
            sa.Column('asked_at', sa.DateTime(), nullable=False),
            sa.Column('admitted_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('orcid', name=op.f('sign_in_wait_pkey')))
        with op.batch_alter_table('sign_in_wait', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_sign_in_wait_asked_at'), ['asked_at'], unique=False)


def downgrade():
    op.drop_table('sign_in_wait')
    with op.batch_alter_table('user_login', schema=None) as batch_op:
        batch_op.drop_column('came_via')
