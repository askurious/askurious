"""Serienmails je Konto zählen: mail_outbox.sender_id, sent_at

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-09 15:00:00

The daily limit of series mails per account across all surveys (app/mailqueue.py, Ralf 2026-10-09): who put a mail
in the queue, and when it went out. A sent row keeps only these two (address and text are emptied) for a day.
Two columns that may stay empty: it only adds, so the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0027'
down_revision = '0026'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    have = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('mail_outbox')}
    with op.batch_alter_table('mail_outbox', schema=None) as batch_op:
        if 'sender_id' not in have:
            batch_op.add_column(sa.Column('sender_id', sa.Integer(), nullable=True))
            batch_op.create_index(batch_op.f('ix_mail_outbox_sender_id'), ['sender_id'], unique=False)
        if 'sent_at' not in have:
            batch_op.add_column(sa.Column('sent_at', sa.DateTime(), nullable=True))


def downgrade():
    with op.batch_alter_table('mail_outbox', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_mail_outbox_sender_id'))
        batch_op.drop_column('sent_at')
        batch_op.drop_column('sender_id')
