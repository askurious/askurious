"""Interviews kodieren: Spalten coding_project.ai_allowed und coding_project.settings

Revision ID: 0103
Revises: 0102
Create Date: 2026-10-27 23:40:00

The switch "KI-Vorschläge erlauben" of an analysis (app/transcript_coding_ai.py), off by default, and a JSON field for
further settings of an analysis. Both only add (a server_default, a column that may stay empty), so the deploy rolls;
the old code never reads them.
"""
from alembic import op
import sqlalchemy as sa


revision = '0103'
down_revision = '0102'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('coding_project')}
    if 'ai_allowed' not in columns:
        op.add_column('coding_project', sa.Column('ai_allowed', sa.Boolean(), nullable=False, server_default=sa.false()))
    if 'settings' not in columns:
        op.add_column('coding_project', sa.Column('settings', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('coding_project', 'settings')
    op.drop_column('coding_project', 'ai_allowed')
