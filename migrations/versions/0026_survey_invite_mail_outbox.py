"""Einladungsmails an Teilnehmende: survey_invite, mail_outbox

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-09 14:00:00

The address lists for invitation mails to participants (app/survey_invites.py) and the queue of mails of a series
(app/mailqueue.py), mailversand-konzept.md M6 to M8; only used with mail switched on. New tables without foreign
keys (their rows go in code with the survey): it only adds, so the deploy rolls. Tables that are there already
(built by create_all from the models of this version) are left as they are.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0026'
down_revision = '0025'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    tables = set(sa.inspect(op.get_bind()).get_table_names())
    if 'survey_invite' not in tables:
        op.create_table('survey_invite',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('survey_id', sa.Integer(), nullable=False),
            sa.Column('email', sa.String(length=254), nullable=False),
            sa.Column('email_hash', sa.String(length=64), nullable=False),
            sa.Column('name', sa.String(length=200), nullable=False),
            sa.Column('lang', sa.String(length=10), nullable=False),
            sa.Column('token_hash', sa.String(length=64), nullable=False),
            sa.Column('status', sa.String(length=10), nullable=False),
            sa.Column('sends', sa.Integer(), nullable=False),
            sa.Column('last_sent_at', sa.DateTime(), nullable=True),
            sa.Column('failed', sa.Boolean(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id', name=op.f('survey_invite_pkey')),
            sa.UniqueConstraint('token_hash', name=op.f('survey_invite_token_hash_key')))
        with op.batch_alter_table('survey_invite', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_survey_invite_survey_id'), ['survey_id'], unique=False)
            batch_op.create_index(batch_op.f('ix_survey_invite_email_hash'), ['email_hash'], unique=False)
    if 'mail_outbox' not in tables:
        op.create_table('mail_outbox',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('survey_id', sa.Integer(), nullable=True),
            sa.Column('invite_id', sa.Integer(), nullable=True),
            sa.Column('kind', sa.String(length=10), nullable=False),
            sa.Column('to', sa.String(length=254), nullable=False),
            sa.Column('subject', sa.String(length=300), nullable=False),
            sa.Column('body', sa.Text(), nullable=False),
            sa.Column('reply_to', sa.String(length=254), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('claimed_at', sa.DateTime(), nullable=True),
            sa.Column('attempts', sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint('id', name=op.f('mail_outbox_pkey')))
        with op.batch_alter_table('mail_outbox', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_mail_outbox_survey_id'), ['survey_id'], unique=False)
            batch_op.create_index(batch_op.f('ix_mail_outbox_invite_id'), ['invite_id'], unique=False)


def downgrade():
    op.drop_table('mail_outbox')
    op.drop_table('survey_invite')
