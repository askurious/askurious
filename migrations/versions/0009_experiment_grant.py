"""Experimentelle Funktionen schaltet ein Admin ein: Tabelle experiment_grant

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-05 21:00:00

A site admin's word on an experimental feature (app/experimental.py) beyond one person: on for a group, on for
everyone, or a person kept out. The person's own switch stays user.experiments; from now on only admins set it, and
what people ticked themselves until now stays on as if an admin had switched it (Ralf, 2026-10-05, question 2). A
database that has the table already (built by create_all from the models of this version) is left as it is.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table('experiment_grant'):
        return
    op.create_table('experiment_grant',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('feature', sa.String(length=40), nullable=False),
    sa.Column('group_id', sa.Integer(), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('allowed', sa.Boolean(), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['group_id'], ['workgroup.id'], name=op.f('experiment_grant_group_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('experiment_grant_user_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['created_by_id'], ['user.id'], name=op.f('experiment_grant_created_by_id_fkey'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('experiment_grant_pkey'))
    )
    with op.batch_alter_table('experiment_grant', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_experiment_grant_feature'), ['feature'], unique=False)
        batch_op.create_index(batch_op.f('ix_experiment_grant_group_id'), ['group_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_experiment_grant_user_id'), ['user_id'], unique=False)


def downgrade():
    with op.batch_alter_table('experiment_grant', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_experiment_grant_user_id'))
        batch_op.drop_index(batch_op.f('ix_experiment_grant_group_id'))
        batch_op.drop_index(batch_op.f('ix_experiment_grant_feature'))
    op.drop_table('experiment_grant')
