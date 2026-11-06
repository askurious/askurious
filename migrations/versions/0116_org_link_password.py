"""Organisationen: Schalter link_password (Link für eine Person erlaubt Konto mit Passwort)

Revision ID: 0116
Revises: 0115
Create Date: 2026-11-06 19:00:00

Whether a link for one person made by an organisation's admins also lets the person create an account with an email
address and a password instead of ORCID (app/signup_links.py). Only adds a column with a server_default (off).
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
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('organisation')}
    if 'link_password' not in columns:
        op.add_column('organisation', sa.Column('link_password', sa.Boolean(), nullable=False, server_default='0'))


def downgrade():
    op.drop_column('organisation', 'link_password')
