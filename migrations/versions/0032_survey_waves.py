"""Wellen einer Längsschnittbefragung: survey.waves

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-10 10:00:00

Surveys linked as waves of one longitudinal design (pre and post), merged on the Export page by the element "Persönlicher
Code" (app/waves.py). The column keeps the list of waves with their labels (JSON). A column that may stay empty: it only
adds, so the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0032'
down_revision = '0031'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('survey')}
    if 'waves' not in have:
        with op.batch_alter_table('survey', schema=None) as batch_op:
            batch_op.add_column(sa.Column('waves', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('survey', schema=None) as batch_op:
        batch_op.drop_column('waves')
