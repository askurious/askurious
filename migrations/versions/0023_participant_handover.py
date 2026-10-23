"""Übergabe an die Teilnahmeadresse: participant_handover

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-08 18:00:00

The handover from the main address to the participants' address (app/hosts.py, code review 2026-10-08 H1/H2):
only a random id goes into the address, its content lies in this table and is taken once. A table that is there
already (built by create_all from the models of this version) is left as it is.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None


def upgrade():
    if 'participant_handover' in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table('participant_handover',
        sa.Column('id', sa.String(length=64), nullable=False),
        sa.Column('survey_id', sa.Integer(), nullable=False),
        sa.Column('fingerprint', sa.String(length=64), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('used', sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('participant_handover_pkey')),
        sa.UniqueConstraint('fingerprint', name=op.f('participant_handover_fingerprint_key')))
    with op.batch_alter_table('participant_handover', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_participant_handover_created_at'), ['created_at'], unique=False)


def downgrade():
    op.drop_table('participant_handover')
