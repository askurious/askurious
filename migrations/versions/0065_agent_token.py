"""Tokens of the AI agent interface: agent_token

Revision ID: 0065
Revises: 0064
Create Date: 2026-10-15 12:00:00

Personal tokens with which an AI agent reads questionnaires and edits drafts over MCP (app/agent_access.py,
app/mcp.py, experimental "agent"). Only the hash of a token is stored. A new table without foreign keys (its rows go
in code with the account): it only adds, so the deploy rolls. A table that is there already (built by create_all
from the models of this version) is left as it is.
"""
from alembic import op
import sqlalchemy as sa


revision = '0065'
down_revision = '0064'
branch_labels = None
depends_on = None
# only adds a table (CLAUDE.md, "Migrations and the rolling deploy")
ROLLING = True


def upgrade():
    if 'agent_token' in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table('agent_token',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('scope', sa.String(length=10), nullable=False),
        sa.Column('surveys', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('agent_token_pkey')),
        sa.UniqueConstraint('token_hash', name=op.f('agent_token_token_hash_key')))
    with op.batch_alter_table('agent_token', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_agent_token_user_id'), ['user_id'], unique=False)


def downgrade():
    op.drop_table('agent_token')
