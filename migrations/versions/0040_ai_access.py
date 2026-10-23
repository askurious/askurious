"""Eigener KI-Schlüssel: ai_access, ai_call.source und ai_call.provider

Revision ID: 0040
Revises: 0039
Create Date: 2026-10-10 18:00:00

A researcher's own AI access (experimental "own_ai_key", app/ai_access.py; eigener-ki-schluessel-konzept.md, E1-E7 as
recommended, Ralf 2026-10-10): one row per person with provider, base URL, the encrypted key and the text model. The
AI statistics learn whose access a call used (source, provider). A new table and two columns that may stay empty: it
only adds, so the deploy rolls. No foreign key, like group_join_link: the row goes in code with the account.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0040'
down_revision = '0039'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'ai_access' not in set(inspector.get_table_names()):
        op.create_table('ai_access',
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('provider', sa.String(length=20), nullable=False),
            sa.Column('base_url', sa.String(length=300), nullable=False),
            sa.Column('key_enc', sa.Text(), nullable=False),
            sa.Column('key_hint', sa.String(length=8), nullable=False),
            sa.Column('text_model', sa.String(length=120), nullable=False),
            sa.Column('models', sa.JSON(), nullable=True),
            sa.Column('valid_until', sa.Date(), nullable=True),
            sa.Column('checked_at', sa.DateTime(), nullable=True),
            sa.Column('last_error', sa.String(length=40), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('user_id', name=op.f('ai_access_pkey')))
    have = {c['name'] for c in inspector.get_columns('ai_call')}
    with op.batch_alter_table('ai_call', schema=None) as batch_op:
        if 'source' not in have:
            batch_op.add_column(sa.Column('source', sa.String(length=10), nullable=True))
        if 'provider' not in have:
            batch_op.add_column(sa.Column('provider', sa.String(length=20), nullable=True))


def downgrade():
    with op.batch_alter_table('ai_call', schema=None) as batch_op:
        batch_op.drop_column('provider')
        batch_op.drop_column('source')
    op.drop_table('ai_access')
