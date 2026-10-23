"""Two indexes of response that others make redundant go: ix_response_survey_id and ix_response_enrolment_id

Revision ID: 0088
Revises: 0087
Create Date: 2026-10-18 23:55:00

Every insert and every save of a participation also wrote these two indexes (performance audit 2026-10-18, PERF-10):
- ix_response_survey_id (survey_id): ix_response_counts and ix_response_start_flood begin with survey_id, so every
  look-up by survey (and the foreign key) uses those;
- ix_response_enrolment_id (enrolment_id): ix_response_diary_started (enrolment_id, part, started_at, partial: only
  rows with an enrolment) holds every row a look-up by person can find; `enrolment_id = …` and `IN (…)` imply
  IS NOT NULL, on SQLite and PostgreSQL. tests/platform/test_response_indexes.py checks the plans.

Rolling: dropping an index changes nothing the old code reads or writes (it names no index; its queries take the
remaining ones, as the new code does), so the participant container not yet restarted runs on. Only dropping a
table or column breaks old code (CLAUDE.md, tests/platform/test_rolling_restart.py).
"""
import sqlalchemy as sa
from alembic import op

revision = "0088"
down_revision = "0087"
branch_labels = None
depends_on = None
ROLLING = True

GONE = (("ix_response_survey_id", "survey_id"), ("ix_response_enrolment_id", "enrolment_id"))


def upgrade():
    have = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("response")}
    for name, _ in GONE:
        if name in have:
            op.drop_index(name, table_name="response")


def downgrade():
    have = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("response")}
    for name, column in GONE:
        if name not in have:
            op.create_index(name, "response", [column], unique=False)
