"""Offene Antworten kategorisieren: survey.codings, response.coding

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-10 08:00:00

Category schemes of open answers per survey (survey.codings) and the categories assigned to each participation
(response.coding), page "Kategorisieren" of the results (app/coding.py). Two columns that may stay empty: they only
add, so the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0034'
down_revision = '0033'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    inspect = sa.inspect(op.get_bind())
    if 'codings' not in {c['name'] for c in inspect.get_columns('survey')}:
        with op.batch_alter_table('survey', schema=None) as batch_op:
            batch_op.add_column(sa.Column('codings', sa.JSON(), nullable=True))
    if 'coding' not in {c['name'] for c in inspect.get_columns('response')}:
        with op.batch_alter_table('response', schema=None) as batch_op:
            batch_op.add_column(sa.Column('coding', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('response', schema=None) as batch_op:
        batch_op.drop_column('coding')
    with op.batch_alter_table('survey', schema=None) as batch_op:
        batch_op.drop_column('codings')
