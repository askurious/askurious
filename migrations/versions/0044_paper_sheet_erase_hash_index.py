"""Teilindex für Löschcodes auf Papierbögen: ix_paper_sheet_erase_hash

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-11 09:00:00

A deletion code typed on the privacy page that fits no participation is looked up in paper_sheet (paper.erase_unscanned,
security audit 2026-10-10, P-S10). Without an index every try read the whole table. A partial index like
ix_response_erase_hash (0021) holds only the sheets printed with a code. It only adds, so the deploy rolls. An index
already there (built by create_all from the models) is left as it is.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0044'
down_revision = '0043'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True

WHERE = sa.text('erase_hash IS NOT NULL')


def upgrade():
    if any(i['name'] == 'ix_paper_sheet_erase_hash' for i in sa.inspect(op.get_bind()).get_indexes('paper_sheet')):
        return
    op.create_index('ix_paper_sheet_erase_hash', 'paper_sheet', ['erase_hash'], unique=False, sqlite_where=WHERE,
                    postgresql_where=WHERE)


def downgrade():
    op.drop_index('ix_paper_sheet_erase_hash', table_name='paper_sheet', sqlite_where=WHERE, postgresql_where=WHERE)
