"""Lernhilfen am Konto und Studienplan einer Befragung

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-05 07:00:00

user.learning_aids: the switch "Lernhilfen" on the page "Konto" (app/learning.py). Accounts that exist already get
false (they know the app as it is); new accounts get true from the model's default (concept "Askurious für
Studierende und Berufseinsteiger", S2). survey.study_plan: the study plan of a survey (app/study_plan.py). A column
that is there already (added by add_missing_columns) is left as it is.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def _has(table, column):
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if not _has('user', 'learning_aids'):
        with op.batch_alter_table('user', schema=None) as batch_op:
            batch_op.add_column(sa.Column('learning_aids', sa.Boolean(), server_default='0', nullable=False))
    if not _has('survey', 'study_plan'):
        with op.batch_alter_table('survey', schema=None) as batch_op:
            batch_op.add_column(sa.Column('study_plan', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('survey', schema=None) as batch_op:
        batch_op.drop_column('study_plan')
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('learning_aids')
