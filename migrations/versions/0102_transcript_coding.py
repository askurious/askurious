"""Interviews kodieren: Tabellen coding_project, coding_category, coding_event, coding_member, coding_link

Revision ID: 0102
Revises: 0101
Create Date: 2026-10-27 21:30:00

The analyses of an interview guide's transcripts with their category systems, history, members and coding links
(app/transcript_coding.py, experimental "transcript_coding"). Five new tables without foreign keys, like
interviewer_access; no existing table changes. It only adds, so the deploy rolls; the old code never reads them.
"""
from alembic import op
import sqlalchemy as sa


revision = '0102'
down_revision = '0101'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def _indexes(table, columns):
    for column in columns:
        op.create_index(f'ix_{table}_{column}', table, [column])


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if 'coding_project' not in tables:
        op.create_table(
            'coding_project',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('survey_id', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=120), nullable=False),
            sa.Column('question', sa.Text(), nullable=False),
            sa.Column('visibility', sa.String(length=10), nullable=False),
            sa.Column('mains_fixed', sa.Boolean(), nullable=False),
            sa.Column('created_by_id', sa.Integer(), nullable=True),
            sa.Column('created_by_name', sa.String(length=200), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        _indexes('coding_project', ('survey_id', 'created_by_id'))
    if 'coding_category' not in tables:
        op.create_table(
            'coding_category',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('project_id', sa.Integer(), nullable=False),
            sa.Column('parent_id', sa.Integer(), nullable=True),
            sa.Column('kind', sa.String(length=10), nullable=False),
            sa.Column('guid', sa.String(length=36), nullable=False),
            sa.Column('source', sa.String(length=40), nullable=True),
            sa.Column('name', sa.String(length=120), nullable=False),
            sa.Column('definition', sa.Text(), nullable=False),
            sa.Column('delimitation', sa.Text(), nullable=False),
            sa.Column('anchor', sa.JSON(), nullable=True),
            sa.Column('colour', sa.String(length=7), nullable=False),
            sa.Column('position', sa.Integer(), nullable=False),
            sa.Column('author_key', sa.String(length=40), nullable=False),
            sa.Column('author_id', sa.Integer(), nullable=True),
            sa.Column('author_name', sa.String(length=200), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('updated_at', sa.DateTime(), nullable=False),
            sa.Column('removed_at', sa.DateTime(), nullable=True),
            sa.Column('merged_into', sa.Integer(), nullable=True),
            sa.Column('former_names', sa.JSON(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
        _indexes('coding_category', ('project_id', 'parent_id', 'author_id', 'merged_into'))
    if 'coding_event' not in tables:
        op.create_table(
            'coding_event',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('project_id', sa.Integer(), nullable=False),
            sa.Column('category_id', sa.Integer(), nullable=True),
            sa.Column('kind', sa.String(length=20), nullable=False),
            sa.Column('reply_to', sa.Integer(), nullable=True),
            sa.Column('status', sa.String(length=10), nullable=False),
            sa.Column('reason', sa.Text(), nullable=False),
            sa.Column('data', sa.JSON(), nullable=True),
            sa.Column('actor_key', sa.String(length=40), nullable=False),
            sa.Column('actor_id', sa.Integer(), nullable=True),
            sa.Column('actor_name', sa.String(length=200), nullable=False),
            sa.Column('merge', sa.String(length=80), nullable=True),
            sa.Column('at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        _indexes('coding_event', ('project_id', 'category_id', 'reply_to', 'actor_id', 'at'))
    if 'coding_member' not in tables:
        op.create_table(
            'coding_member',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('project_id', sa.Integer(), nullable=False),
            sa.Column('kind', sa.String(length=12), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=True),
            sa.Column('group_id', sa.Integer(), nullable=True),
            sa.Column('access_id', sa.Integer(), nullable=True),
            sa.Column('role', sa.String(length=10), nullable=False),
            sa.Column('access_ids', sa.JSON(), nullable=True),
            sa.Column('added_by_id', sa.Integer(), nullable=True),
            sa.Column('added_by_name', sa.String(length=200), nullable=False),
            sa.Column('added_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        _indexes('coding_member', ('project_id', 'user_id', 'group_id', 'access_id', 'added_by_id'))
    if 'coding_link' not in tables:
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
        _indexes('coding_link', ('project_id', 'survey_id', 'created_by_id'))


def downgrade():
    for table in ('coding_link', 'coding_member', 'coding_event', 'coding_category', 'coding_project'):
        op.drop_table(table)
