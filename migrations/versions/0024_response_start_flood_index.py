"""Index für die Startgrenze je Befragung: ix_response_start_flood

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-09 12:00:00

limits.survey_start_flood asks, at the first "Weiter" of every new participation, when the SURVEY_START_LIMIT-th
newest real participation of the survey started (survey_id = ? AND source = 'real' ORDER BY id DESC OFFSET 2999).
With only ix_response_survey_id the database read up to 3000 whole rows (5 KB each) for source and started_at:
16 ms at 5000 participations on SQLite. This index answers it alone (0.08 ms; N-7 of the audit 2026-10-09).
It only adds, so the deploy rolls. An index already there (built by create_all from the models of this version)
is left as it is.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None
# True only when the migration just adds (a table, a column that may stay empty or has a server_default, an index):
# then a deploy restarts the participants' containers one after the other, and the one not yet restarted runs the
# old code against the new schema for a moment (deploy/docker/restart.sh, CLAUDE.md). Dropping or renaming: False.
ROLLING = True

COLUMNS = ['survey_id', 'source', 'id', 'started_at']


def upgrade():
    if any(i['name'] == 'ix_response_start_flood' for i in sa.inspect(op.get_bind()).get_indexes('response')):
        return
    op.create_index('ix_response_start_flood', 'response', COLUMNS, unique=False)


def downgrade():
    op.drop_index('ix_response_start_flood', table_name='response')
