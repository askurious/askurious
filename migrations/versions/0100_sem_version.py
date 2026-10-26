"""Versionen des Strukturgleichungsmodells (Tabelle sem_version)

Revision ID: 0100
Revises: 0099
Create Date: 2026-10-26 13:30:00

A new table for the versions of the structural equation model of a survey (app/sem_versions.py): settings and the
figures as they were, no answers and no people. No foreign key, like live_ink (the rows go in code with the survey,
lifecycle.delete). It only adds, so the deploy rolls; the old code never reads it.
"""
from alembic import op
import sqlalchemy as sa


revision = '0100'
down_revision = '0099'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if 'sem_version' not in inspector.get_table_names():
        op.create_table(
            'sem_version',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('survey_id', sa.Integer(), nullable=False),
            sa.Column('number', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=80), nullable=False),
            sa.Column('key', sa.String(length=32), nullable=False),
            sa.Column('settings', sa.JSON(), nullable=True),
            sa.Column('figures', sa.JSON(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_sem_version_survey_id', 'sem_version', ['survey_id'])


def downgrade():
    op.drop_index('ix_sem_version_survey_id', table_name='sem_version')
    op.drop_table('sem_version')
