"""Interviews kodieren: Tabelle coding_memo (Memos)

Revision ID: 0109
Revises: 0108
Create Date: 2026-10-29 14:00:00

Memos of an analysis (app/transcript_coding_memos.py, after Kuckartz & Rädiker; Ralf 2026-10-29): notes at a coded
passage, at a category or at an interview, with author and time. A new table only, nothing the old code reads.
"""
from alembic import op
import sqlalchemy as sa


revision = '0109'
down_revision = '0108'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    if 'coding_memo' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'coding_memo',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('project_id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(10), nullable=False),
        sa.Column('response_id', sa.Integer(), nullable=True),
        sa.Column('coding_id', sa.String(24), nullable=True),
        sa.Column('category_id', sa.Integer(), nullable=True),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('author_key', sa.String(40), nullable=False),
        sa.Column('author_id', sa.Integer(), nullable=True),
        sa.Column('author_name', sa.String(200), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
    )
    for column in ('project_id', 'response_id', 'category_id', 'author_id'):
        op.create_index(f'ix_coding_memo_{column}', 'coding_memo', [column])


def downgrade():
    op.drop_table('coding_memo')
