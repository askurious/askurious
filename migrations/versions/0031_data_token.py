"""Lese-Token für R und Python: data_token

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-09 20:00:00

Read tokens for the data of a survey (app/data_access.py, export page "Datenzugang für R und Python"): R or Python
fetch the CSV, the .rds file or the codebook with a token in the Authorization header. Only the hash of the token is
stored. A new table without foreign keys (its rows go in code with the survey and the account): it only adds, so the
deploy rolls. A table that is there already (built by create_all from the models of this version) is left as it is.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0031'
down_revision = '0030'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    if 'data_token' in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table('data_token',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('survey_id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=80), nullable=False),
        sa.Column('token_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('expires_at', sa.DateTime(), nullable=False),
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id', name=op.f('data_token_pkey')),
        sa.UniqueConstraint('token_hash', name=op.f('data_token_token_hash_key')))
    with op.batch_alter_table('data_token', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_data_token_survey_id'), ['survey_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_data_token_user_id'), ['user_id'], unique=False)


def downgrade():
    op.drop_table('data_token')
