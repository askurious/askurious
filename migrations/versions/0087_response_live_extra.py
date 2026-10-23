"""response.live_extra: a further word of a live vote as a column of its own, in the counting index

Revision ID: 0087
Revises: 0085
Create Date: 2026-10-18 23:50:00

Every count of participations leaves out the further words of a live vote (params["live_extra"], app/live/audience.py,
T-19). Read from the JSON, the counts read every row instead of the index ix_response_counts: 50 ms instead of 1.4 ms
for a survey with 10 000 participations, and the platform's counts (Administration, dashboard) a full scan of the table
(performance audit 2026-10-18, PERF-02). Now a column of its own, filled here from params, and the last column of
ix_response_counts, so that the counts read only the index again.

Rolling: it only adds. The column has a server_default (false): the participant container not yet restarted keeps
inserting rows without it, and its further words of a live vote get false. The housekeeping sets those
(models.mark_live_extra, hourly; only the rows of live votes are read). The index is built anew with one more column;
the old code reads no index by name.
"""
import sqlalchemy as sa
from alembic import op

revision = "0087"
down_revision = "0085"
branch_labels = None
depends_on = None
ROLLING = True

OLD = ["survey_id", "source", "is_preview", "status", "completed_at", "started_at"]
NEW = OLD + ["live_extra"]


def upgrade():
    bind = op.get_bind()
    if "live_extra" not in {c["name"] for c in sa.inspect(bind).get_columns("response")}:
        # ALTER TABLE ADD COLUMN on SQLite too: a batch would copy the table and lose its triggers (data_revision)
        op.add_column("response", sa.Column("live_extra", sa.Boolean(), nullable=False, server_default=sa.false()))
    response = sa.table("response", sa.column("id", sa.Integer), sa.column("survey_id", sa.Integer),
                        sa.column("params", sa.JSON), sa.column("live_extra", sa.Boolean))
    survey = sa.table("survey", sa.column("id", sa.Integer), sa.column("live", sa.JSON))
    # only the rows of live votes can be further words (the column survey.live is set for them)
    bind.execute(sa.update(response).where(
        response.c.survey_id.in_(sa.select(survey.c.id).where(survey.c.live.is_not(None))),
        sa.func.coalesce(response.c.params["live_extra"].as_boolean(), sa.false()))
        .values(live_extra=sa.true()))
    indexes = {i["name"]: i["column_names"] for i in sa.inspect(bind).get_indexes("response")}
    if indexes.get("ix_response_counts") != NEW:
        if "ix_response_counts" in indexes:
            op.drop_index("ix_response_counts", table_name="response")
        op.create_index("ix_response_counts", "response", NEW, unique=False)


def downgrade():
    op.drop_index("ix_response_counts", table_name="response")
    op.create_index("ix_response_counts", "response", OLD, unique=False)
    op.drop_column("response", "live_extra")  # SQLite 3.35+: ALTER TABLE DROP COLUMN, the triggers stay
