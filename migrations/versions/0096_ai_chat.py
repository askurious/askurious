"""KI-Chat für Teilnehmende: Tabellen ai_chat_access und ai_chat_turn

Revision ID: 0096
Revises: 0095
Create Date: 2026-10-23 16:00:00

The model access of a survey with the element "KI-Chat" and the messages of its chats (app/ai_chat.py, experimental
"ai_chat"; concept ki-assistent-tagebuch-konzept.md). Two new tables without foreign keys, like live_ink: their rows
go in code with the survey and the participation. It only adds, so the deploy rolls; the old code never reads them.
"""
from alembic import op
import sqlalchemy as sa


revision = '0096'
down_revision = '0095'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    tables = sa.inspect(op.get_bind()).get_table_names()
    if 'ai_chat_access' not in tables:
        op.create_table(
            'ai_chat_access',
            sa.Column('survey_id', sa.Integer(), autoincrement=False, nullable=False),
            sa.Column('provider', sa.String(length=20), nullable=False),
            sa.Column('base_url', sa.String(length=300), nullable=False),
            sa.Column('key_enc', sa.Text(), nullable=False),
            sa.Column('key_hint', sa.String(length=8), nullable=False),
            sa.Column('model', sa.String(length=120), nullable=False),
            sa.Column('version', sa.Integer(), nullable=False),
            sa.Column('per_day', sa.Integer(), nullable=False),
            sa.Column('valid_until', sa.Date(), nullable=True),
            sa.Column('set_by', sa.String(length=300), nullable=False),
            sa.Column('set_at', sa.DateTime(), nullable=False),
            sa.Column('history', sa.JSON(), nullable=True),
            sa.PrimaryKeyConstraint('survey_id', name=op.f('ai_chat_access_pkey')),
        )
    if 'ai_chat_turn' not in tables:
        op.create_table(
            'ai_chat_turn',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('survey_id', sa.Integer(), nullable=False),
            sa.Column('response_id', sa.Integer(), nullable=False),
            sa.Column('item_id', sa.Integer(), nullable=False),
            sa.Column('seq', sa.Integer(), nullable=False),
            sa.Column('role', sa.String(length=10), nullable=False),
            sa.Column('text', sa.Text(), nullable=False),
            sa.Column('masked', sa.Text(), nullable=True),
            sa.Column('status', sa.String(length=12), nullable=False),
            sa.Column('model', sa.String(length=120), nullable=False),
            sa.Column('access_version', sa.Integer(), nullable=False),
            sa.Column('prompt_hash', sa.String(length=16), nullable=False),
            sa.Column('tokens_in', sa.Integer(), nullable=True),
            sa.Column('tokens_out', sa.Integer(), nullable=True),
            sa.Column('millis', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id', name=op.f('ai_chat_turn_pkey')),
        )
        with op.batch_alter_table('ai_chat_turn', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_ai_chat_turn_response_id'), ['response_id'], unique=False)
            batch_op.create_index('ix_ai_chat_turn_survey_at', ['survey_id', 'created_at'], unique=False)


def downgrade():
    with op.batch_alter_table('ai_chat_turn', schema=None) as batch_op:
        batch_op.drop_index('ix_ai_chat_turn_survey_at')
        batch_op.drop_index(batch_op.f('ix_ai_chat_turn_response_id'))
    op.drop_table('ai_chat_turn')
    op.drop_table('ai_chat_access')
