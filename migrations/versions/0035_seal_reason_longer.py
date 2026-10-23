"""Längerer Grund in der Siegelkette: seal_entry.reason

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-10 09:00:00

A deletion by the platform (app/platform_erase.py) writes its reason in a few words into the deletion record of the
seal chain, where it is part of the hash: seal_entry.reason grows from 20 to 200 characters. A longer text column
only, which old code reads and writes as before: the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0035'
down_revision = '0034'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    with op.batch_alter_table('seal_entry', schema=None) as batch_op:
        batch_op.alter_column('reason', existing_type=sa.String(length=20), type_=sa.String(length=200),
                              existing_nullable=True)


def downgrade():
    with op.batch_alter_table('seal_entry', schema=None) as batch_op:
        batch_op.alter_column('reason', existing_type=sa.String(length=200), type_=sa.String(length=20),
                              existing_nullable=True)
