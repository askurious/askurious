"""Tagebuchstudie: response.part (der Teil eines Eintrags als eigene Spalte, aus occasion["part"])

Revision ID: 0049
Revises: 0048
Create Date: 2026-10-12 10:00:00

The part of a diary entry ("entry", a module key or "exit") was read from the JSON occasion of every entry on every
page of a diary study (Survey.tally, timeline, diary.entry_tally; audit 2026-10-11, M10). Now a column of its own
with an index, filled here from occasion. A column that may stay empty and an index: it only adds, so the deploy
rolls. An entry the old program writes meanwhile has no part in the column; Response.part_expr reads the JSON then.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0049'
down_revision = '0048'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True

BATCH = 1000


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    have = {c['name'] for c in inspector.get_columns('response')}
    if 'part' not in have:
        with op.batch_alter_table('response', schema=None) as batch_op:
            batch_op.add_column(sa.Column('part', sa.String(length=8), nullable=True))
    if 'ix_response_part' not in {i['name'] for i in sa.inspect(bind).get_indexes('response')}:
        op.create_index('ix_response_part', 'response', ['survey_id', 'part'], unique=False,
                        sqlite_where=sa.text('part IS NOT NULL'), postgresql_where=sa.text('part IS NOT NULL'))
    # fill it from occasion, for the entries of diary studies only (enrolment_id set)
    response = sa.table('response', sa.column('id', sa.Integer), sa.column('enrolment_id', sa.Integer),
                        sa.column('occasion', sa.JSON), sa.column('part', sa.String))
    last = 0
    while True:
        rows = bind.execute(sa.select(response.c.id, response.c.occasion)
                            .where(response.c.id > last, response.c.occasion.is_not(None), response.c.part.is_(None))
                            .order_by(response.c.id).limit(BATCH)).all()
        if not rows:
            break
        last = rows[-1][0]
        values = [{"rid": rid, "p": str(occ["part"])[:8]} for rid, occ in rows
                  if isinstance(occ, dict) and occ.get("part")]
        if values:
            bind.execute(sa.update(response).where(response.c.id == sa.bindparam("rid"))
                         .values(part=sa.bindparam("p")), values)


def downgrade():
    op.drop_index('ix_response_part', table_name='response')
    with op.batch_alter_table('response', schema=None) as batch_op:
        batch_op.drop_column('part')
