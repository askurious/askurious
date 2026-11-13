"""Anträge auf mehr Plätze: Tabelle org_seat_request

Revision ID: 0124
Revises: 0123
Create Date: 2026-11-13 10:00:00

An admin of an organisation asks for more places (accounts at home there); a site admin grants or refuses it
(app/org_seats.py). Only adds a table.
"""
from alembic import op
import sqlalchemy as sa


revision = '0124'
down_revision = '0123'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if 'org_seat_request' not in inspector.get_table_names():
        op.create_table(
            'org_seat_request',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('org_id', sa.Integer(), nullable=False),
            sa.Column('by_id', sa.Integer(), nullable=True),
            sa.Column('reason', sa.Text(), nullable=False),
            sa.Column('seats_before', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('status', sa.String(length=10), nullable=False),
            sa.Column('seats_after', sa.Integer(), nullable=True),
            sa.Column('decided_by_id', sa.Integer(), nullable=True),
            sa.Column('decided_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_org_seat_request_org_id', 'org_seat_request', ['org_id'])


def downgrade():
    op.drop_index('ix_org_seat_request_org_id', table_name='org_seat_request')
    op.drop_table('org_seat_request')
