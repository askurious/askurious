"""Seite „KI-Nutzung“: user.ai_enabled und ai_call.user_id

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-10 20:00:00

The switch "Generative KI nutzen" per person (ki-nutzung-seite-konzept.md E4, Ralf 2026-10-10: all as recommended):
on for everyone, also for the accounts there are. The person's own statistics of the last 30 days (E6) need to know
who clicked an AI call: ai_call.user_id, empty for the calls before (and emptied when an account is deleted). A
column with a server_default and a column that may stay empty: it only adds, so the deploy rolls. No foreign key,
like ai_access: the account deletion empties it in code.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0042'
down_revision = '0041'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'ai_enabled' not in {c['name'] for c in inspector.get_columns('user')}:
        with op.batch_alter_table('user', schema=None) as batch_op:
            batch_op.add_column(sa.Column('ai_enabled', sa.Boolean(), server_default='1', nullable=False))
    if 'user_id' not in {c['name'] for c in inspector.get_columns('ai_call')}:
        with op.batch_alter_table('ai_call', schema=None) as batch_op:
            batch_op.add_column(sa.Column('user_id', sa.Integer(), nullable=True))
            batch_op.create_index(batch_op.f('ix_ai_call_user_id'), ['user_id'], unique=False)


def downgrade():
    with op.batch_alter_table('ai_call', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_ai_call_user_id'))
        batch_op.drop_column('user_id')
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('ai_enabled')
