"""Transcription jobs to cancel, queued by the participants' side: table whisper_cancel

Revision ID: 0093
Revises: 0092
Create Date: 2026-10-22 11:00:00

A deletion on the participants' side (a deletion code, the diary app) that takes answers with an interview recording
along cancelled the running transcription through the researchers' container (WHISPER_CANCEL_URL). The participants'
containers get a network of their own without that way (audit 2026-10-22, E-01, stage 2b), so they write the job to
this table and the housekeeping hands it on (app/whisper_jobs.py). A new table only, without foreign keys: it only
adds, so the deploy rolls (the participants' container not yet restarted still uses the relay for that moment).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0093'
down_revision = '0092'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if 'whisper_cancel' in inspector.get_table_names():
        return
    op.create_table(
        'whisper_cancel',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('job_id', sa.String(length=32), nullable=False),
        sa.Column('service', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('whisper_cancel_pkey')),
    )
    with op.batch_alter_table('whisper_cancel', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_whisper_cancel_created_at'), ['created_at'], unique=False)


def downgrade():
    with op.batch_alter_table('whisper_cancel', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_whisper_cancel_created_at'))
    op.drop_table('whisper_cancel')
