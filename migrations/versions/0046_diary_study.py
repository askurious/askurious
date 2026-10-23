"""Tagebuchstudie: survey.sampling, Tabelle enrolment, response.enrolment_id, response.occasion

Revision ID: 0046
Revises: 0045
Create Date: 2026-10-11 17:00:00

The fourth kind of survey, the diary study (app/diary.py, concepts/experiencesampling.md): the schedule in
survey.sampling, one row per person in enrolment, and each entry a participation that names its person and its part.
A new table and columns that may stay empty: it only adds, so the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0046'
down_revision = '0045'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if 'enrolment' not in inspector.get_table_names():
        op.create_table(
            'enrolment',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('survey_id', sa.Integer(), nullable=False),
            sa.Column('secret_hash', sa.String(length=64), nullable=False),
            sa.Column('pid', sa.String(length=40), nullable=False),
            sa.Column('source', sa.String(length=10), server_default='real', nullable=False),
            sa.Column('status', sa.String(length=10), server_default='pending', nullable=False),
            sa.Column('language', sa.String(length=8), nullable=False),
            sa.Column('tz', sa.String(length=64), server_default='Europe/Berlin', nullable=False),
            sa.Column('first_day', sa.Date(), nullable=True),
            sa.Column('last_day', sa.Date(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('last_sync_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id', name=op.f('enrolment_pkey')),
            sa.UniqueConstraint('pid', name=op.f('enrolment_pid_key')),
            sa.UniqueConstraint('secret_hash', name=op.f('enrolment_secret_hash_key')),
        )
        with op.batch_alter_table('enrolment', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_enrolment_survey_id'), ['survey_id'], unique=False)
    have = {c['name'] for c in inspector.get_columns('survey')}
    if 'sampling' not in have:
        with op.batch_alter_table('survey', schema=None) as batch_op:
            batch_op.add_column(sa.Column('sampling', sa.JSON(), nullable=True))
    have = {c['name'] for c in inspector.get_columns('response')}
    with op.batch_alter_table('response', schema=None) as batch_op:
        if 'enrolment_id' not in have:
            batch_op.add_column(sa.Column('enrolment_id', sa.Integer(), nullable=True))
            batch_op.create_index(batch_op.f('ix_response_enrolment_id'), ['enrolment_id'], unique=False)
        if 'occasion' not in have:
            batch_op.add_column(sa.Column('occasion', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('response', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_response_enrolment_id'))
        batch_op.drop_column('occasion')
        batch_op.drop_column('enrolment_id')
    with op.batch_alter_table('survey', schema=None) as batch_op:
        batch_op.drop_column('sampling')
    with op.batch_alter_table('enrolment', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_enrolment_survey_id'))
    op.drop_table('enrolment')
