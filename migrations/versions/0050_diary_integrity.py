"""Tagebuchstudie: Eingangszeit, Abfragezeit und uuid je Eintrag, Tabelle diary_invite

Revision ID: 0050
Revises: 0049
Create Date: 2026-10-12 12:00:00

Integrity of the entries of a diary study (audit 2026-10-11, M1, M2, M5, N4): response.received_at (when the server
received an entry), response.due_at (the prompt's time to the minute, unique per person and part) and response.uuid
(the app's id of an answer, unique per person), and the personal invitation codes in a table of their own instead
of a list in survey.sampling. Columns that may stay empty, partial indexes and a new table, filled from what is
there (the list in sampling stays, the old program reads it while the deploy rolls): it only adds, so the deploy
rolls. An entry that would break the new key (the same prompt twice, which the old rule let through) keeps an
empty due_at.
"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0050'
down_revision = '0049'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True

PROMPT = sa.text('due_at IS NOT NULL')
UUID = sa.text('uuid IS NOT NULL')


def upgrade():
    bind = op.get_bind()
    have = {c['name'] for c in sa.inspect(bind).get_columns('response')}
    with op.batch_alter_table('response', schema=None) as batch_op:
        if 'received_at' not in have:
            batch_op.add_column(sa.Column('received_at', sa.DateTime(), nullable=True))
        if 'due_at' not in have:
            batch_op.add_column(sa.Column('due_at', sa.DateTime(), nullable=True))
        if 'uuid' not in have:
            batch_op.add_column(sa.Column('uuid', sa.String(length=36), nullable=True))
    _fill_due(bind)
    indexes = {i['name'] for i in sa.inspect(bind).get_indexes('response')}
    if 'ix_response_prompt' not in indexes:
        op.create_index('ix_response_prompt', 'response', ['enrolment_id', 'part', 'due_at'], unique=True,
                        sqlite_where=PROMPT, postgresql_where=PROMPT)
    if 'ix_response_uuid' not in indexes:
        op.create_index('ix_response_uuid', 'response', ['enrolment_id', 'uuid'], unique=True,
                        sqlite_where=UUID, postgresql_where=UUID)
    if 'diary_invite' not in sa.inspect(bind).get_table_names():
        op.create_table(
            'diary_invite',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('survey_id', sa.Integer(), nullable=False),
            sa.Column('code', sa.String(length=8), nullable=False),
            sa.Column('used_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id', name=op.f('diary_invite_pkey')),
            sa.UniqueConstraint('survey_id', 'code', name=op.f('diary_invite_survey_id_code_key')),
        )
        _fill_invites(bind)


def _fill_due(bind):
    """due_at of the existing entries from occasion["due"], to the minute; a completed entry first, so that of two
    entries of one prompt the answered one keeps it."""
    response = sa.table('response', sa.column('id', sa.Integer), sa.column('enrolment_id', sa.Integer),
                        sa.column('part', sa.String), sa.column('status', sa.String), sa.column('occasion', sa.JSON),
                        sa.column('due_at', sa.DateTime))
    rows = bind.execute(sa.select(response.c.id, response.c.enrolment_id, response.c.part, response.c.status,
                                  response.c.occasion).where(response.c.enrolment_id.is_not(None))).all()
    taken, values = set(), []
    for rid, eid, part, status, occ in sorted(rows, key=lambda r: (r[3] != 'completed', r[0])):
        if not isinstance(occ, dict) or not occ.get('due'):
            continue
        try:
            due = datetime.fromisoformat(str(occ['due'])).replace(second=0, microsecond=0, tzinfo=None)
        except ValueError:
            continue
        key = (eid, part or occ.get('part') or 'entry', due)
        if key in taken:
            continue
        taken.add(key)
        values.append({'rid': rid, 'd': due})
    if values:
        bind.execute(sa.update(response).where(response.c.id == sa.bindparam('rid')).values(due_at=sa.bindparam('d')),
                     values)


def _fill_invites(bind):
    """The invitation codes of every diary study, from the list in survey.sampling."""
    survey = sa.table('survey', sa.column('id', sa.Integer), sa.column('sampling', sa.JSON))
    invite = sa.table('diary_invite', sa.column('survey_id', sa.Integer), sa.column('code', sa.String),
                      sa.column('used_at', sa.DateTime), sa.column('created_at', sa.DateTime))
    now = datetime.utcnow().replace(microsecond=0)
    rows = []
    for sid, sampling in bind.execute(sa.select(survey.c.id, survey.c.sampling).where(survey.c.sampling.is_not(None))):
        seen = set()
        for i in (sampling or {}).get('invites') or [] if isinstance(sampling, dict) else []:
            code = str((i or {}).get('c') or '')[:8] if isinstance(i, dict) else ''
            if code and code not in seen:
                seen.add(code)
                rows.append({'survey_id': sid, 'code': code, 'used_at': now if i.get('used') else None, 'created_at': now})
    if rows:
        bind.execute(sa.insert(invite), rows)


def downgrade():
    op.drop_table('diary_invite')
    op.drop_index('ix_response_uuid', table_name='response', sqlite_where=UUID, postgresql_where=UUID)
    op.drop_index('ix_response_prompt', table_name='response', sqlite_where=PROMPT, postgresql_where=PROMPT)
    with op.batch_alter_table('response', schema=None) as batch_op:
        batch_op.drop_column('uuid')
        batch_op.drop_column('due_at')
        batch_op.drop_column('received_at')
