"""The dashboard of Administration > Befragungen (app/admin_stats.py; Ralf 2026-10-17) counts the participations begun
and completed in a period: an index on response.started_at and one on response.completed_at, so that "12 Monate" stays
quick with many participations.

Additive only: two indexes; older containers do not notice them.
"""
import sqlalchemy as sa
from alembic import op

revision = "0077"
down_revision = "0076"
branch_labels = None
depends_on = None
ROLLING = True

INDEXES = (("ix_response_started_at", "started_at"), ("ix_response_completed_at", "completed_at"))


def upgrade():
    have = {i["name"] for i in sa.inspect(op.get_bind()).get_indexes("response")}
    for name, column in INDEXES:
        if name not in have:
            op.create_index(name, "response", [column])


def downgrade():
    for name, _ in INDEXES:
        op.drop_index(name, table_name="response")
