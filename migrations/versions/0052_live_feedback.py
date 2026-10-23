"""Live-Feedback zum Vortrag: Tabelle live_feedback, Spalte live_question.item_id

Revision ID: 0052
Revises: 0051
Create Date: 2026-10-12 18:00:00

Taps on "Unklar" on the phones of a live vote, counted per slide and run (live_feedback), and the slide on the
screen when a question from the audience was asked (live_question.item_id); the review at the end groups both by
slide (concept konzept-live-feedback-praesentation-2026-10-12.md). A new table (without foreign keys, like ai_access:
its rows go in code with the survey and the run) and a column that may stay empty: they only add, so the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0052'
down_revision = '0051'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    inspector = sa.inspect(op.get_bind())
    have = {c['name'] for c in inspector.get_columns('live_question')}
    if 'item_id' not in have:
        with op.batch_alter_table('live_question', schema=None) as batch_op:
            batch_op.add_column(sa.Column('item_id', sa.Integer(), nullable=True))
    if 'live_feedback' not in inspector.get_table_names():
        op.create_table(
            'live_feedback',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('survey_id', sa.Integer(), nullable=False),
            sa.Column('run_id', sa.BigInteger(), nullable=False),
            sa.Column('item_id', sa.Integer(), nullable=True),
            sa.Column('kind', sa.String(length=10), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id', name=op.f('live_feedback_pkey')),
        )
        with op.batch_alter_table('live_feedback', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_live_feedback_survey_id'), ['survey_id'], unique=False)


def downgrade():
    with op.batch_alter_table('live_feedback', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_live_feedback_survey_id'))
    op.drop_table('live_feedback')
    with op.batch_alter_table('live_question', schema=None) as batch_op:
        batch_op.drop_column('item_id')
