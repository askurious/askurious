"""Teilindex für Löschcodes: ix_response_erase_hash

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-08 21:00:00

The page "Antworten löschen" of the platform (public.erase) looks a deletion code up in all surveys
(erasure.anywhere: erase_hash = ...). Without an index every try read the whole table response, all answers
included. A partial index holds only the rows that were shown a code; SQLite and PostgreSQL both know partial
indexes. An index already there (built by create_all from the models of this version) is left as it is.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None

WHERE = sa.text('erase_hash IS NOT NULL')


def upgrade():
    if any(i['name'] == 'ix_response_erase_hash' for i in sa.inspect(op.get_bind()).get_indexes('response')):
        return
    op.create_index('ix_response_erase_hash', 'response', ['erase_hash'], unique=False, sqlite_where=WHERE,
                    postgresql_where=WHERE)


def downgrade():
    op.drop_index('ix_response_erase_hash', table_name='response', sqlite_where=WHERE, postgresql_where=WHERE)
