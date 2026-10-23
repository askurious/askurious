"""Teilindex für die Haushaltung: ix_response_outside

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-07 21:00:00

The housekeeping looks once a minute for participations outside in an external section past their return link
(external.sweep_params: outside_since IS NOT NULL AND outside_since < ...). Without an index SQLite read the whole
table response, all answers included (code review 2026-10-07, D6: 20 000 participations, 60 to 116 ms per minute and
process). A partial index holds only the rows with outside_since; SQLite and PostgreSQL both know partial indexes. An
index already there (built by create_all from the models of this version) is left as it is.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None

WHERE = sa.text('outside_since IS NOT NULL')


def upgrade():
    if any(i['name'] == 'ix_response_outside' for i in sa.inspect(op.get_bind()).get_indexes('response')):
        return
    op.create_index('ix_response_outside', 'response', ['outside_since'], unique=False, sqlite_where=WHERE,
                    postgresql_where=WHERE)


def downgrade():
    op.drop_index('ix_response_outside', table_name='response', sqlite_where=WHERE, postgresql_where=WHERE)
