"""Siegelkette: survey.seal, seal_entry, seal_anchor

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-08 09:00:00

The experimental seal chain (app/seal.py): the switch per survey, off for every survey there is; the entries of the
chains and their timestamps of the DFN. Tables and a column that are there already (built by create_all from the
models of this version) are left as they are.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade():
    if 'seal' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('survey')}:
        with op.batch_alter_table('survey', schema=None) as batch_op:
            batch_op.add_column(sa.Column('seal', sa.Boolean(), server_default='0', nullable=False))
    tables = _tables()
    if 'seal_entry' not in tables:
        op.create_table('seal_entry',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('survey_uid', sa.String(length=16), nullable=False),
            sa.Column('seq', sa.Integer(), nullable=False),
            sa.Column('kind', sa.String(length=10), nullable=False),
            sa.Column('ref', sa.Integer(), nullable=True),
            sa.Column('status', sa.String(length=10), nullable=True),
            sa.Column('content', sa.String(length=64), nullable=True),
            sa.Column('salt', sa.String(length=32), nullable=True),
            sa.Column('response_id', sa.Integer(), nullable=True),
            sa.Column('reason', sa.String(length=20), nullable=True),
            sa.Column('at', sa.String(length=32), nullable=False),
            sa.Column('prev', sa.String(length=64), nullable=False),
            sa.Column('digest', sa.String(length=64), nullable=False),
            sa.PrimaryKeyConstraint('id', name=op.f('seal_entry_pkey')),
            sa.UniqueConstraint('survey_uid', 'seq', name='uq_seal_entry_seq'))
        with op.batch_alter_table('seal_entry', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_seal_entry_survey_uid'), ['survey_uid'], unique=False)
            batch_op.create_index(batch_op.f('ix_seal_entry_response_id'), ['response_id'], unique=False)
    if 'seal_anchor' not in tables:
        op.create_table('seal_anchor',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('survey_uid', sa.String(length=16), nullable=False),
            sa.Column('seq', sa.Integer(), nullable=False),
            sa.Column('digest', sa.String(length=64), nullable=False),
            sa.Column('tsa', sa.String(length=200), nullable=False),
            sa.Column('token', sa.LargeBinary(), nullable=False),
            sa.Column('time', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id', name=op.f('seal_anchor_pkey')))
        with op.batch_alter_table('seal_anchor', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_seal_anchor_survey_uid'), ['survey_uid'], unique=False)


def downgrade():
    op.drop_table('seal_anchor')
    op.drop_table('seal_entry')
    with op.batch_alter_table('survey', schema=None) as batch_op:
        batch_op.drop_column('seal')
