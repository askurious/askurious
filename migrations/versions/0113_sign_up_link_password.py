"""Registrierungslinks: Spalte password_ok (Einladung „Nur Link“)

Revision ID: 0113
Revises: 0112
Create Date: 2026-11-06 12:00:00

A link for one person who chooses how to sign in: with ORCID, or with an email address and a new password
(app/signup_links.py). Only adds a column with a server_default, so every existing link stays ORCID only.
"""
from alembic import op
import sqlalchemy as sa


revision = '0113'
down_revision = '0112'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('sign_up_link')}
    if 'password_ok' not in columns:
        op.add_column('sign_up_link', sa.Column('password_ok', sa.Boolean(), nullable=False, server_default='0'))


def downgrade():
    op.drop_column('sign_up_link', 'password_ok')
