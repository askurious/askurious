"""Konstrukte der Organisation: Vorschläge an eine Organisation (construct_submission.org_id)

Revision ID: 0119
Revises: 0118
Create Date: 2026-11-09 15:30:00

A member proposes a linked element to an organisation for a construct of its own (app/org_constructs.py): the
submission names the organisation; None as before is a submission to the operator. Constructs of an organisation use
the columns construct.org_id, visibility ("org") and source ("org") that exist already. Only adds a column that may
stay empty and its index; the old code does not read it.
"""
from alembic import op
import sqlalchemy as sa


revision = '0119'
down_revision = '0118'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('construct_submission')}
    if 'org_id' not in columns:
        op.add_column('construct_submission', sa.Column('org_id', sa.Integer(), nullable=True))
        op.create_index('ix_construct_submission_org_id', 'construct_submission', ['org_id'])


def downgrade():
    op.drop_index('ix_construct_submission_org_id', 'construct_submission')
    op.drop_column('construct_submission', 'org_id')
