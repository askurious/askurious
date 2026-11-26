"""Aufräumschritte nach Löschungen auf der Teilnahmeseite: table participant_cleanup

Revision ID: 0130
Revises: 0129
Create Date: 2026-11-26 10:00:00

Under the participants' own database role (app/participant_grants.py) a deletion with the code cannot write the
coding guide, the analyses of interview transcripts or the field packs of an interview guide. It notes these steps
here, and the housekeeping carries them out (app/participant_cleanup.py). A row holds the kind, the survey and
numbers or digests, no answer. A new table only, without foreign keys: it only adds, so the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


revision = '0130'
down_revision = '0129'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    if 'participant_cleanup' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'participant_cleanup',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('survey_id', sa.Integer(), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('participant_cleanup_pkey')),
    )
    with op.batch_alter_table('participant_cleanup', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_participant_cleanup_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_participant_cleanup_survey_id'), ['survey_id'], unique=False)


def downgrade():
    with op.batch_alter_table('participant_cleanup', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_participant_cleanup_survey_id'))
        batch_op.drop_index(batch_op.f('ix_participant_cleanup_created_at'))
    op.drop_table('participant_cleanup')
