"""Organisation: Namen der Mitglieder verbergen (names_mode, names_hidden_own, Tabelle org_alias)

Revision ID: 0099
Revises: 0098
Create Date: 2026-10-23 20:00:00

Two columns that may stay empty (empty: names are shown, as before) and a new table for the numbers of the aliases
(app/names.py), without foreign keys like group_join_link. It only adds, so the deploy rolls; the old code never reads them.
"""
from alembic import op
import sqlalchemy as sa


revision = '0099'
down_revision = '0098'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {c['name'] for c in inspector.get_columns('organisation')}
    if 'names_mode' not in columns:
        op.add_column('organisation', sa.Column('names_mode', sa.String(length=10), nullable=True))
    if 'names_hidden_own' not in columns:
        op.add_column('organisation', sa.Column('names_hidden_own', sa.Boolean(), nullable=True))
    if 'org_alias' not in inspector.get_table_names():
        op.create_table(
            'org_alias',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('org_id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('number', sa.Integer(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('org_id', 'user_id', name='uq_org_alias_person'),
            sa.UniqueConstraint('org_id', 'number', name='uq_org_alias_number'),
        )
        op.create_index('ix_org_alias_org_id', 'org_alias', ['org_id'])
        op.create_index('ix_org_alias_user_id', 'org_alias', ['user_id'])


def downgrade():
    op.drop_table('org_alias')
    with op.batch_alter_table('organisation') as batch:
        batch.drop_column('names_hidden_own')
        batch.drop_column('names_mode')
