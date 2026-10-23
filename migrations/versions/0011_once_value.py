"""Einmal-IDs mit Index: Tabelle once_value

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-06 12:10:00

Whether a value of a once-only URL parameter (a panel ID) took part already was a search with LIKE through the stored
parameters of every participation of the survey, within the write lock (audit 2026-10-06, P5: 41 ms at 5000). Now
every value stored with a real participation stands in once_value as its SHA-256, under an index (app/panel.py). A
partial UNIQUE index over the values claimed as once-only replaces the advisory lock of T-11 (audit 2026-10-05).

The values of the participations already stored are taken over: every string in response.params of a real
participation; per survey and once-only parameter the first participation with a value holds its claim (two that
share one, from before T-11, are both found as before). A table already there (built by create_all from the models
of this version) is left as it is and filled if it is empty.
"""
import hashlib
import json

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def _hash(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()  # as app/panel.py value_hash


def _load(value):
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table('once_value'):
        op.create_table('once_value',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('survey_id', sa.Integer(), nullable=False),
        sa.Column('response_id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=41), nullable=False),
        sa.Column('value_hash', sa.String(length=64), nullable=False),
        sa.Column('once', sa.Boolean(), server_default='0', nullable=False),
        sa.ForeignKeyConstraint(['response_id'], ['response.id'], name=op.f('once_value_response_id_fkey'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['survey_id'], ['survey.id'], name=op.f('once_value_survey_id_fkey'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f('once_value_pkey'))
        )
        op.create_index('ix_once_value_response_id', 'once_value', ['response_id'], unique=False)
        op.create_index('ix_once_value_lookup', 'once_value', ['survey_id', 'name', 'value_hash'], unique=False)
        op.create_index('ix_once_value_claim', 'once_value', ['survey_id', 'name', 'value_hash'], unique=True,
                        sqlite_where=sa.text('once'), postgresql_where=sa.text('once'))
    if bind.execute(sa.text('SELECT 1 FROM once_value LIMIT 1')).first() is not None:
        return
    once = {}  # survey id -> names of its once-only parameters
    for sid, panel in bind.execute(sa.text('SELECT id, panel FROM survey WHERE panel IS NOT NULL')):
        params = (_load(panel) or {}).get('params') or []
        once[sid] = {p.get('name') for p in params if isinstance(p, dict) and p.get('mode') == 'store' and p.get('once')}
    table = sa.table('once_value', sa.column('survey_id'), sa.column('response_id'), sa.column('name'),
                     sa.column('value_hash'), sa.column('once', sa.Boolean()))
    claimed, rows = set(), []
    found = bind.execute(sa.text("SELECT id, survey_id, params FROM response WHERE source = 'real' AND is_preview = :no "
                                 "AND params IS NOT NULL ORDER BY id"), {'no': False}).fetchall()
    for rid, sid, params in found:
        params = _load(params)
        if not isinstance(params, dict):
            continue
        for name, value in params.items():
            if not isinstance(value, str) or not value or len(name) > 41:
                continue
            key = (sid, name, _hash(value))
            first = name in once.get(sid, ()) and key not in claimed
            if first:
                claimed.add(key)
            rows.append({'survey_id': sid, 'response_id': rid, 'name': name, 'value_hash': key[2], 'once': first})
        if len(rows) >= 1000:
            op.bulk_insert(table, rows)
            rows = []
    if rows:
        op.bulk_insert(table, rows)


def downgrade():
    op.drop_index('ix_once_value_claim', table_name='once_value', sqlite_where=sa.text('once'),
                  postgresql_where=sa.text('once'))
    op.drop_index('ix_once_value_lookup', table_name='once_value')
    op.drop_index('ix_once_value_response_id', table_name='once_value')
    op.drop_table('once_value')
