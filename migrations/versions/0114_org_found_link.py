"""Gründungslinks: Tabelle org_found_link

Revision ID: 0114
Revises: 0113
Create Date: 2026-11-06 14:00:00

A link for one new person who creates an organisation and becomes its admin (app/org_links.py). Only adds a table.
"""
from alembic import op
import sqlalchemy as sa


revision = '0114'
down_revision = '0113'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if 'org_found_link' not in inspector.get_table_names():
        op.create_table(
            'org_found_link',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('token_hash', sa.String(length=64), nullable=False),
            sa.Column('note', sa.String(length=200), nullable=False, server_default=''),
            sa.Column('max_accounts', sa.Integer(), nullable=False),
            sa.Column('created_by_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.Column('used_at', sa.DateTime(), nullable=True),
            sa.Column('org_id', sa.Integer(), nullable=True),
            sa.Column('used_by_id', sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('token_hash'),
        )
        op.create_index('ix_org_found_link_created_by_id', 'org_found_link', ['created_by_id'])


def downgrade():
    op.drop_index('ix_org_found_link_created_by_id', table_name='org_found_link')
    op.drop_table('org_found_link')
