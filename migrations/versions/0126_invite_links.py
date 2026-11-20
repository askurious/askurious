"""Einladungslinks je Person: Name und Zugangsende (member_invite.name, member_invite.access_until,
survey_member.access_until)

Revision ID: 0126
Revises: 0125
Create Date: 2026-11-20 12:00:00

Admins of a survey make one-time links for several people at once (app/invite_links.py, Ralf 2026-11-20): an
invitation keeps the name it is meant for and the day the access ends; a member who came in that way keeps that day,
and the hourly sweep ends the membership then. Only adds columns that may stay empty and an index; the old code does
not read them.
"""
from alembic import op
import sqlalchemy as sa


revision = '0126'
down_revision = '0125'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    bind = op.get_bind()
    columns = {c['name'] for c in sa.inspect(bind).get_columns('member_invite')}
    if 'name' not in columns:
        op.add_column('member_invite', sa.Column('name', sa.String(length=200), nullable=True))
    if 'access_until' not in columns:
        op.add_column('member_invite', sa.Column('access_until', sa.DateTime(), nullable=True))
    columns = {c['name'] for c in sa.inspect(bind).get_columns('survey_member')}
    if 'access_until' not in columns:
        op.add_column('survey_member', sa.Column('access_until', sa.DateTime(), nullable=True))
    indexes = {i['name'] for i in sa.inspect(bind).get_indexes('survey_member')}
    if 'ix_survey_member_access_until' not in indexes:
        op.create_index('ix_survey_member_access_until', 'survey_member', ['access_until'])


def downgrade():
    op.drop_index('ix_survey_member_access_until', table_name='survey_member')
    with op.batch_alter_table('survey_member') as batch:
        batch.drop_column('access_until')
    with op.batch_alter_table('member_invite') as batch:
        batch.drop_column('access_until')
        batch.drop_column('name')
