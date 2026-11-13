"""Löschen auf der Teilnahmeseite mit Nachweis; Löschmarke der Dashboard-Links in eigener Tabelle

Revision ID: 0123
Revises: 0122
Create Date: 2026-11-12 21:30:00

Access audit 2026-11-11, B2 stage 2, part a (only adds):
- board_erase_floor: the count of the data pool the dashboard links wait for after a deletion, so far the key
  "_public_after_erase" in survey.data_pool. No foreign key, like sem_version (lifecycle.delete removes the row).
  The marks there are copied over; they stay in data_pool, where the old
  code reads them during the rolling restart (the new code reads both and takes the larger).
- the function participant_erase_response (PostgreSQL): deletes participations with a proof (app/participant_erase.py).
  No trigger, nothing of the old code changes: the old code does not call it; nobody but the participants' role may
  (EXECUTE revoked from PUBLIC, granted by the migration job's grant_participant).
"""
from alembic import op
import sqlalchemy as sa


revision = '0123'
down_revision = '0122'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other (deploy/docker/restart.sh, CLAUDE.md).
ROLLING = True


def upgrade():
    bind = op.get_bind()
    if 'board_erase_floor' not in sa.inspect(bind).get_table_names():
        op.create_table(
            'board_erase_floor',
            sa.Column('survey_id', sa.Integer(), autoincrement=False, nullable=False),
            sa.Column('public_after', sa.Integer(), server_default='0', nullable=False),
            sa.PrimaryKeyConstraint('survey_id', name=op.f('board_erase_floor_pkey')),
        )
    survey = sa.table('survey', sa.column('id', sa.Integer()), sa.column('data_pool', sa.JSON()))
    floor = sa.table('board_erase_floor', sa.column('survey_id', sa.Integer()), sa.column('public_after', sa.Integer()))
    have = set(bind.execute(sa.select(floor.c.survey_id)).scalars())
    rows = []
    for sid, pool in bind.execute(sa.select(survey.c.id, survey.c.data_pool).where(survey.c.data_pool.is_not(None))):
        mark = pool.get('_public_after_erase') if isinstance(pool, dict) else None
        if isinstance(mark, int) and mark > 0 and sid not in have:
            rows.append({'survey_id': sid, 'public_after': mark})
    if rows:
        op.bulk_insert(floor, rows)
    from app.participant_erase import install

    install(bind)


def downgrade():
    from app.participant_erase import uninstall

    uninstall(op.get_bind())
    op.drop_table('board_erase_floor')
