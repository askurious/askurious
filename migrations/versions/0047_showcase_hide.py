"""Beispielbefragungen: survey.showcase, Tabelle survey_hide

Revision ID: 0047
Revises: 0046
Create Date: 2026-10-11 18:00:00

Example surveys every signed-in researcher may view (survey.showcase, app/access.py) and the surveys a researcher
took out of their own list (survey_hide, "Für mich ausblenden"); concept demo-befragungen-konzept.md. A column with
a server_default and a new table (without foreign keys, like ai_access: its rows go in code with the account and
the survey): it only adds, so the deploy rolls.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0047'
down_revision = '0046'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    inspector = sa.inspect(op.get_bind())
    have = {c['name'] for c in inspector.get_columns('survey')}
    if 'showcase' not in have:
        with op.batch_alter_table('survey', schema=None) as batch_op:
            batch_op.add_column(sa.Column('showcase', sa.Boolean(), server_default='0', nullable=False))
    if 'survey_hide' not in inspector.get_table_names():
        op.create_table(
            'survey_hide',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('survey_id', sa.Integer(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id', name=op.f('survey_hide_pkey')),
            sa.UniqueConstraint('user_id', 'survey_id', name=op.f('survey_hide_user_id_survey_id_key')),
        )
        with op.batch_alter_table('survey_hide', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_survey_hide_survey_id'), ['survey_id'], unique=False)
            batch_op.create_index(batch_op.f('ix_survey_hide_user_id'), ['user_id'], unique=False)


def downgrade():
    with op.batch_alter_table('survey_hide', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_survey_hide_user_id'))
        batch_op.drop_index(batch_op.f('ix_survey_hide_survey_id'))
    op.drop_table('survey_hide')
    with op.batch_alter_table('survey', schema=None) as batch_op:
        batch_op.drop_column('showcase')
