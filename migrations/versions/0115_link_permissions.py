"""„Person hinzufügen“: Befragungstypen und „darf anlegen“ bei allen drei Wegen

Revision ID: 0115
Revises: 0114
Create Date: 2026-11-06 15:00:00

sign_up_link.kinds and may_create: what the dialog chose for a one-time link. sign_in_orcid.may_create: the tick for an
iD; None keeps what the entries had (NEW_ACCOUNTS_MAY_CREATE). Only adds columns that may stay empty.
"""
from alembic import op
import sqlalchemy as sa


revision = '0115'
down_revision = '0114'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('sign_up_link')}
    if 'kinds' not in columns:
        op.add_column('sign_up_link', sa.Column('kinds', sa.JSON(), nullable=True))
    if 'may_create' not in columns:
        op.add_column('sign_up_link', sa.Column('may_create', sa.Boolean(), nullable=True))
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('sign_in_orcid')}
    if 'may_create' not in columns:
        op.add_column('sign_in_orcid', sa.Column('may_create', sa.Boolean(), nullable=True))


def downgrade():
    op.drop_column('sign_in_orcid', 'may_create')
    op.drop_column('sign_up_link', 'may_create')
    op.drop_column('sign_up_link', 'kinds')
