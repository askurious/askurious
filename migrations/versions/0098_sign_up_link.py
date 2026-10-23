"""Einladungslinks der Plattform: Tabelle sign_up_link und sign_in_orcid.link_id

Revision ID: 0098
Revises: 0097
Create Date: 2026-10-23 19:00:00

A registration link of the platform (app/signup_links.py): a new table without foreign keys, and a column that may
stay empty naming the link an iD on the sign-in list came by. It only adds, so the deploy rolls; the old code never
reads them.
"""
from alembic import op
import sqlalchemy as sa


revision = '0098'
down_revision = '0097'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if 'sign_up_link' not in inspector.get_table_names():
        op.create_table(
            'sign_up_link',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('token_hash', sa.String(length=64), nullable=False),
            sa.Column('org_id', sa.Integer(), nullable=True),
            sa.Column('account_type_id', sa.Integer(), nullable=True),
            sa.Column('valid_spec', sa.String(length=20), nullable=True),
            sa.Column('max_uses', sa.Integer(), nullable=False),
            sa.Column('uses', sa.Integer(), nullable=False),
            sa.Column('note', sa.String(length=200), nullable=False),
            sa.Column('created_by_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('token_hash'),
        )
        op.create_index('ix_sign_up_link_org_id', 'sign_up_link', ['org_id'])
        op.create_index('ix_sign_up_link_account_type_id', 'sign_up_link', ['account_type_id'])
        op.create_index('ix_sign_up_link_created_by_id', 'sign_up_link', ['created_by_id'])
    columns = {c['name'] for c in inspector.get_columns('sign_in_orcid')}
    if 'link_id' not in columns:
        op.add_column('sign_in_orcid', sa.Column('link_id', sa.Integer(), nullable=True))
        op.create_index('ix_sign_in_orcid_link_id', 'sign_in_orcid', ['link_id'])


def downgrade():
    op.drop_index('ix_sign_in_orcid_link_id', table_name='sign_in_orcid')
    op.drop_column('sign_in_orcid', 'link_id')
    op.drop_table('sign_up_link')
