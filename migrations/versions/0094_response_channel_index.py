"""An index for the check whether a survey holds paper questionnaires: ix_response_channel

Revision ID: 0094
Revises: 0093
Create Date: 2026-10-22 15:00:00

export.has_paper asks with every export and every computation of the results whether a survey holds participations
from paper questionnaires (channel "paper"). No index covered channel: without a hit (the usual case) the database read
every participation of the survey, 50 ms at 10 000 (audit 2026-10-22, G-03). A partial index on (survey_id, channel) holds only
the rows with a channel (only paper questionnaires set it). Rolling: a new index changes nothing the old code reads or
writes; on PostgreSQL it is built concurrently, so the table stays writable (CLAUDE.md, BIG_TABLES).
"""
import sqlalchemy as sa
from alembic import op

revision = "0094"
down_revision = "0093"
branch_labels = None
depends_on = None
ROLLING = True

NAME = "ix_response_channel"


def upgrade():
    if NAME in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("response")}:
        return
    with op.get_context().autocommit_block():
        op.create_index(NAME, "response", ["survey_id", "channel"], unique=False, postgresql_concurrently=True,
                        postgresql_where=sa.text("channel IS NOT NULL"), sqlite_where=sa.text("channel IS NOT NULL"))


def downgrade():
    if NAME not in {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("response")}:
        return
    with op.get_context().autocommit_block():
        op.drop_index(NAME, table_name="response", postgresql_concurrently=True)
