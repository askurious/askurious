"""KI-Agenten-Schnittstelle: Spalte agent_token.images (Häkchen „Bilder erzeugen“)

Revision ID: 0112
Revises: 0111
Create Date: 2026-11-05 14:50:00

Whether the agent of a token may generate images (tool generate_image of app/agent_tools.py, the only one that sends
something to a model of the platform). Only adds a column with a server_default, so every existing token stays without.
"""
from alembic import op
import sqlalchemy as sa


revision = '0112'
down_revision = '0111'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('agent_token')}
    if 'images' not in columns:
        op.add_column('agent_token', sa.Column('images', sa.Boolean(), nullable=False, server_default='0'))


def downgrade():
    op.drop_column('agent_token', 'images')
