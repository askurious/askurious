"""Beitrittslinks für Gruppen: group_join_link, workgroup_member.via

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-10 10:30:00

Links to join a group (app/joinlinks.py, gruppen-freischaltung-konzept.md): a lead makes one, whoever opens it and
signs in becomes a member. Only the hash of the token is stored. A new table without foreign keys (its rows go in code
with the group and the account) and a column that may stay empty: it only adds, so the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0036'
down_revision = '0035'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'group_join_link' not in set(inspector.get_table_names()):
        op.create_table('group_join_link',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('group_id', sa.Integer(), nullable=False),
            sa.Column('role', sa.String(length=10), nullable=False),
            sa.Column('token_hash', sa.String(length=64), nullable=False),
            sa.Column('max_uses', sa.Integer(), nullable=False),
            sa.Column('uses', sa.Integer(), nullable=False),
            sa.Column('created_by_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.Column('expires_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id', name=op.f('group_join_link_pkey')),
            sa.UniqueConstraint('token_hash', name=op.f('group_join_link_token_hash_key')))
        with op.batch_alter_table('group_join_link', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_group_join_link_group_id'), ['group_id'], unique=False)
            batch_op.create_index(batch_op.f('ix_group_join_link_created_by_id'), ['created_by_id'], unique=False)
    if 'via' not in {c['name'] for c in inspector.get_columns('workgroup_member')}:
        with op.batch_alter_table('workgroup_member', schema=None) as batch_op:
            batch_op.add_column(sa.Column('via', sa.String(length=10), nullable=True))


def downgrade():
    with op.batch_alter_table('workgroup_member', schema=None) as batch_op:
        batch_op.drop_column('via')
    op.drop_table('group_join_link')
