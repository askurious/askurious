"""Papierfragebogen: paper_run, paper_sheet, paper_page, response.channel, response.paper, survey.paper

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-10 22:00:00

Paper questionnaires (experimental "paper", app/paper.py; papierfragebogen-konzept.md, E1-E17 as recommended, Ralf
2026-10-10): print runs, their numbered sheets and the scanned pages; on a participation how it came in (channel) and
the sheet it came from (paper); on the survey the choices of review and keeping scans. New tables and columns that may
stay empty: it only adds, so the deploy rolls. No foreign keys, like group_join_link: the rows go in code with the
survey.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0043'
down_revision = '0042'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if 'paper_run' not in tables:
        op.create_table('paper_run',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('survey_id', sa.Integer(), nullable=False),
            sa.Column('language', sa.String(length=8), nullable=False),
            sa.Column('qversion', sa.Integer(), nullable=False),
            sa.Column('count', sa.Integer(), nullable=False),
            sa.Column('plans', sa.JSON(), nullable=True),
            sa.Column('created_by', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id', name=op.f('paper_run_pkey')))
        op.create_index(op.f('ix_paper_run_survey_id'), 'paper_run', ['survey_id'], unique=False)
    if 'paper_sheet' not in tables:
        op.create_table('paper_sheet',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('run_id', sa.Integer(), nullable=False),
            sa.Column('survey_id', sa.Integer(), nullable=False),
            sa.Column('key', sa.BigInteger(), nullable=False),
            sa.Column('number', sa.Integer(), nullable=False),
            sa.Column('layout', sa.JSON(), nullable=True),
            sa.Column('plan', sa.String(length=16), nullable=False),
            sa.Column('pages', sa.Integer(), nullable=False),
            sa.Column('state', sa.String(length=10), nullable=False),
            sa.Column('values', sa.JSON(), nullable=True),
            sa.Column('erase_hash', sa.String(length=64), nullable=True),
            sa.Column('pid', sa.String(length=40), nullable=True),
            sa.Column('response_id', sa.Integer(), nullable=True),
            sa.Column('checked_by', sa.Integer(), nullable=True),
            sa.Column('checked_at', sa.DateTime(), nullable=True),
            sa.Column('done_at', sa.DateTime(), nullable=True),
            sa.Column('destroy', sa.Boolean(), nullable=False),
            sa.PrimaryKeyConstraint('id', name=op.f('paper_sheet_pkey')),
            sa.UniqueConstraint('key', name=op.f('paper_sheet_key_key')))
        op.create_index(op.f('ix_paper_sheet_run_id'), 'paper_sheet', ['run_id'], unique=False)
        op.create_index(op.f('ix_paper_sheet_survey_id'), 'paper_sheet', ['survey_id'], unique=False)
    if 'paper_page' not in tables:
        op.create_table('paper_page',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('survey_id', sa.Integer(), nullable=False),
            sa.Column('sheet_id', sa.Integer(), nullable=True),
            sa.Column('page', sa.Integer(), nullable=False),
            sa.Column('filename', sa.String(length=80), nullable=False),
            sa.Column('state', sa.String(length=10), nullable=False),
            sa.Column('reading', sa.JSON(), nullable=True),
            sa.Column('uploaded_by', sa.Integer(), nullable=True),
            sa.Column('uploaded_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id', name=op.f('paper_page_pkey')))
        op.create_index(op.f('ix_paper_page_survey_id'), 'paper_page', ['survey_id'], unique=False)
        op.create_index(op.f('ix_paper_page_sheet_id'), 'paper_page', ['sheet_id'], unique=False)
        op.create_index(op.f('ix_paper_page_uploaded_at'), 'paper_page', ['uploaded_at'], unique=False)
    have = {c['name'] for c in inspector.get_columns('response')}
    with op.batch_alter_table('response', schema=None) as batch_op:
        if 'channel' not in have:
            batch_op.add_column(sa.Column('channel', sa.String(length=10), nullable=True))
        if 'paper' not in have:
            batch_op.add_column(sa.Column('paper', sa.JSON(), nullable=True))
    if 'paper' not in {c['name'] for c in inspector.get_columns('survey')}:
        with op.batch_alter_table('survey', schema=None) as batch_op:
            batch_op.add_column(sa.Column('paper', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('survey', schema=None) as batch_op:
        batch_op.drop_column('paper')
    with op.batch_alter_table('response', schema=None) as batch_op:
        batch_op.drop_column('paper')
        batch_op.drop_column('channel')
    op.drop_table('paper_page')
    op.drop_table('paper_sheet')
    op.drop_table('paper_run')
