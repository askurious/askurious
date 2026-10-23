"""Siegelketten über das Löschen hinaus: seal_chain, seal_entry.rec

Revision ID: 0041
Revises: 0040
Create Date: 2026-10-10 18:00:00

seal_chain names the survey of every seal chain (its number, uid and title) and stays when the survey is deleted, so
that /verify/<code> and seal.json still find the chain (audit 2026-10-10, L-1). Filled here for the chains there are;
a chain whose survey is gone already cannot be named any more (its number went with it). seal_entry.rec says what the
content hash of a case covers (empty = as before; 2 = also the values of external sections and web services, L-15).
A new table and a column that may stay empty: it only adds, so the deploy rolls. The old code writes no rec (= as
before) and no seal_chain row; the new code adds the row when it finds none (seal.remember).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0041'
down_revision = '0040'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if 'rec' not in {c['name'] for c in inspector.get_columns('seal_entry')}:
        with op.batch_alter_table('seal_entry', schema=None) as batch_op:
            batch_op.add_column(sa.Column('rec', sa.Integer(), nullable=True))
    if 'seal_chain' not in set(inspector.get_table_names()):
        op.create_table('seal_chain',
            sa.Column('survey_uid', sa.String(length=16), nullable=False),
            sa.Column('survey_id', sa.Integer(), nullable=False),
            sa.Column('title', sa.String(length=300), nullable=False),
            sa.PrimaryKeyConstraint('survey_uid', name=op.f('seal_chain_pkey')))
        with op.batch_alter_table('seal_chain', schema=None) as batch_op:
            batch_op.create_index(batch_op.f('ix_seal_chain_survey_id'), ['survey_id'], unique=False)
    # the chains there are, with the surveys still there
    bind.execute(sa.text(
        "INSERT INTO seal_chain (survey_uid, survey_id, title) "
        "SELECT s.uid, s.id, s.title FROM survey s "
        "WHERE EXISTS (SELECT 1 FROM seal_entry e WHERE e.survey_uid = s.uid) "
        "AND NOT EXISTS (SELECT 1 FROM seal_chain c WHERE c.survey_uid = s.uid)"))


def downgrade():
    op.drop_table('seal_chain')
    with op.batch_alter_table('seal_entry', schema=None) as batch_op:
        batch_op.drop_column('rec')
