"""AInstein: Tabelle research_project

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-04 23:30:00

A research project of AInstein (app/ainstein.py): name, idea, canvas, chat and plan of a researcher before there is a
survey; survey_id points at the survey made from it. A database that has the table already (built by create_all
from the models of this version) is left as it is.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    if sa.inspect(op.get_bind()).has_table('research_project'):
        return
    op.create_table('research_project',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('idea', sa.Text(), nullable=False),
    sa.Column('canvas', sa.JSON(), nullable=True),
    sa.Column('chat', sa.JSON(), nullable=True),
    sa.Column('plan', sa.JSON(), nullable=True),
    sa.Column('survey_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('research_project_user_id_fkey'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['survey_id'], ['survey.id'], name=op.f('research_project_survey_id_fkey'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('research_project_pkey'))
    )
    with op.batch_alter_table('research_project', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_research_project_user_id'), ['user_id'], unique=False)


def downgrade():
    with op.batch_alter_table('research_project', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_research_project_user_id'))
    op.drop_table('research_project')
