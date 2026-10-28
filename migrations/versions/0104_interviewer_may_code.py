"""Interviewlink: Spalten interviewer_access.may_code und may_interview

Revision ID: 0104
Revises: 0103
Create Date: 2026-10-28 04:05:00

The right "darf kodieren" of an interviewer link (none, own, all; app/interviewer.py, app/transcript_coding.py),
"none" by default. The links that already had the tick "darf eigene Interviews kodieren" in an analysis (a row of
coding_member) get "own", so they code on as before. Only adds: a column with a server_default, filled for rows the
old code never reads it of; token, revision, devices, expiry and the other rights stay untouched. may_interview
("darf Interviews führen") is true for every link, as all of them did until now; a link without it only codes.
"""
from alembic import op
import sqlalchemy as sa


revision = '0104'
down_revision = '0103'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('interviewer_access')}
    if 'may_interview' not in columns:
        op.add_column('interviewer_access', sa.Column('may_interview', sa.Boolean(), nullable=False, server_default=sa.true()))
    if 'may_code' not in columns:
        op.add_column('interviewer_access', sa.Column('may_code', sa.String(8), nullable=False, server_default='none'))
    access = sa.table('interviewer_access', sa.column('id', sa.Integer), sa.column('may_code', sa.String))
    member = sa.table('coding_member', sa.column('kind', sa.String), sa.column('access_id', sa.Integer))
    ticked = sa.select(member.c.access_id).where(member.c.kind == 'interviewer', member.c.access_id.is_not(None))
    op.execute(access.update().where(access.c.may_code == 'none', access.c.id.in_(ticked)).values(may_code='own'))


def downgrade():
    op.drop_column('interviewer_access', 'may_code')
    op.drop_column('interviewer_access', 'may_interview')
