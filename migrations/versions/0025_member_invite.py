"""Einladungen per E-Mail-Adresse: member_invite

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-09 11:00:00

Invitations by e-mail address to a survey or a group (app/invites.py, mailversand-konzept.md M5), only used with
mail switched on. A new table without foreign keys (its rows go in code with the survey, the group and the account
that invited): it only adds, so the deploy rolls. A table that is there already (built by create_all from the models
of this version) is left as it is.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    if 'member_invite' in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table('member_invite',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('survey_id', sa.Integer(), nullable=True),
        sa.Column('group_id', sa.Integer(), nullable=True),
        sa.Column('role', sa.String(length=10), nullable=False),
        sa.Column('email', sa.String(length=254), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('member_invite_pkey')),
        sa.UniqueConstraint('token_hash', name=op.f('member_invite_token_hash_key')))
    with op.batch_alter_table('member_invite', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_member_invite_survey_id'), ['survey_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_member_invite_group_id'), ['group_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_member_invite_email'), ['email'], unique=False)
        batch_op.create_index(batch_op.f('ix_member_invite_created_by_id'), ['created_by_id'], unique=False)


def downgrade():
    op.drop_table('member_invite')
