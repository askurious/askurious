"""Merging runs of a live vote (app/live/control.py, _merge_run; Ralf 2026-10-16): the run a question from the audience
was first asked in, kept when its run is merged into another one, so that the export still shows it.

Additive only: a column that may stay empty (empty = the run it stands in); older containers ignore it.
"""
import sqlalchemy as sa
from alembic import op

revision = "0076"
down_revision = "0075"
branch_labels = None
depends_on = None
ROLLING = True


def upgrade():
    if "origin_run_id" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("live_question")}:
        op.add_column("live_question", sa.Column("origin_run_id", sa.BigInteger(), nullable=True))


def downgrade():
    with op.batch_alter_table("live_question") as batch:
        batch.drop_column("origin_run_id")
