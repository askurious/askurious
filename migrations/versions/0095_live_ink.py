"""Stiftnotizen auf Präsentationsfolien: Tabelle live_ink

Revision ID: 0095
Revises: 0094
Create Date: 2026-10-23 10:00:00

What the presenter writes with a pen on a presentation slide of a live vote, one row per run and slide (app/live/ink.py,
concept stiftnotizen-konzept-2026-10-23.md). A new table without foreign keys, like live_feedback: its rows go in code
with the survey and the run. It only adds, so the deploy rolls; the participants' side never reads it.
"""
from alembic import op
import sqlalchemy as sa


revision = '0095'
down_revision = '0094'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    if 'live_ink' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'live_ink',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('survey_id', sa.Integer(), nullable=False),
        sa.Column('run_id', sa.BigInteger(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('strokes', sa.JSON(), nullable=True),
        sa.Column('laser', sa.JSON(), nullable=True),
        sa.Column('rev', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('live_ink_pkey')),
        sa.UniqueConstraint('survey_id', 'run_id', 'item_id', name='uq_live_ink_slide'),
    )
    with op.batch_alter_table('live_ink', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_live_ink_survey_id'), ['survey_id'], unique=False)


def downgrade():
    with op.batch_alter_table('live_ink', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_live_ink_survey_id'))
    op.drop_table('live_ink')
