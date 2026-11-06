"""Interviews kodieren: Tabelle coding_key (feste Tasten 1 bis 9)

Revision ID: 0116
Revises: 0115
Create Date: 2026-11-06 20:00:00

The keys 1 to 9 of each person in an analysis (app/transcript_coding_view.py, keys; feedback 2026-11-06, P10). A new
table only, nothing the old code reads.
"""
from alembic import op
import sqlalchemy as sa


revision = '0116'
down_revision = '0115'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    if 'coding_key' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'coding_key',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('actor_key', sa.String(40), nullable=False),
        sa.Column('keys', sa.JSON(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
    )
    op.create_index('ix_coding_key_project_id', 'coding_key', ['project_id'])


def downgrade():
    op.drop_table('coding_key')
