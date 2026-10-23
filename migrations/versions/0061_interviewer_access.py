"""Interviewer-Links: interviewer_access

Revision ID: 0061
Revises: 0060
Create Date: 2026-10-15 07:00:00

Personal links for interviewers without an account (app/interviewer.py, concept konzept-interviewer-zugang-2026-10-15.md):
one row per interviewer and interview guide, only the hash of the link's key. A new table without foreign keys (like
data_token: its rows go in code with the guide): it only adds, so the deploy rolls. A table that is there already
(built by create_all from the models of this version) is left as it is.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0061'
down_revision = '0060'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then the participant containers restart one after the other (CLAUDE.md, "Migrations and the rolling deploy")
ROLLING = True


def upgrade():
    if 'interviewer_access' in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table('interviewer_access',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('survey_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('revision', sa.String(length=32), nullable=False),
        sa.Column('blocked', sa.Boolean(), nullable=False),
        sa.Column('may_record', sa.Boolean(), nullable=False),
        sa.Column('may_transcribe', sa.Boolean(), nullable=False),
        sa.Column('max_devices', sa.Integer(), nullable=False),
        sa.Column('devices', sa.JSON(), nullable=True),
        sa.Column('hints_version', sa.String(length=20), nullable=True),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_by_name', sa.String(length=200), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('interviewer_access_pkey')),
        sa.UniqueConstraint('token_hash', name=op.f('interviewer_access_token_hash_key')))
    with op.batch_alter_table('interviewer_access', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_interviewer_access_survey_id'), ['survey_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_interviewer_access_created_by_id'), ['created_by_id'], unique=False)


def downgrade():
    op.drop_table('interviewer_access')
