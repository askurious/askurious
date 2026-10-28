"""Kodierlinks: Tabelle coding_link löschen

Revision ID: 0106
Revises: 0105
Create Date: 2026-10-28 10:05:00

The coding links of 2026-10-27 were only made to try out (Ralf 2026-10-28); a person without an account codes by an
interviewer link with "darf kodieren". The code of 0105 no longer maps the table, so it goes now, in a deploy of its
own after that code runs everywhere (DROPS_UNUSED_SINCE). downgrade brings the table back empty, as 0102 made it.
"""
from alembic import op
import sqlalchemy as sa


revision = '0106'
down_revision = '0105'
branch_labels = None
depends_on = None
ROLLING = False
DROPS_UNUSED_SINCE = "0105"
DROPS = ("coding_link",)


def upgrade():
    if 'coding_link' in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table('coding_link')


def downgrade():
    if 'coding_link' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'coding_link',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('survey_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('revision', sa.String(length=32), nullable=False),
        sa.Column('blocked', sa.Boolean(), nullable=False),
        sa.Column('max_devices', sa.Integer(), nullable=False),
        sa.Column('devices', sa.JSON(), nullable=True),
        sa.Column('access_ids', sa.JSON(), nullable=True),
        sa.Column('hints_version', sa.String(length=20), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_by_name', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    for column in ('project_id', 'survey_id', 'created_by_id'):
        op.create_index(f'ix_coding_link_{column}', 'coding_link', [column])
