"""Index für die Zählungen der Teilnahmen: ix_response_counts

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-06 12:00:00

The counts of a survey (Survey.tally and completed_count, export.completion_seconds, the admin list, the footer of
the researcher pages, lifecycle.maybe_close in the write path) read the index instead of the rows of response, whose
answers make them 5 KB on average (audit 2026-10-06, P3: a count over 5000 participations 23 ms on SQLite, 0.3 ms with
the index). Columns: what these queries filter and group by, survey_id first. An index already there (built by
create_all from the models of this version) is left as it is.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None

COLUMNS = ['survey_id', 'source', 'is_preview', 'status', 'completed_at', 'started_at']


def upgrade():
    if any(i['name'] == 'ix_response_counts' for i in sa.inspect(op.get_bind()).get_indexes('response')):
        return
    op.create_index('ix_response_counts', 'response', COLUMNS, unique=False)


def downgrade():
    op.drop_index('ix_response_counts', table_name='response')
