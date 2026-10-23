"""Index diary admissions by person, module and start time.

Additive only: older participant containers can keep using the new schema.
"""
import sqlalchemy as sa
from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None
ROLLING = True


def upgrade():
    if "ix_response_diary_started" not in {
            index["name"] for index in sa.inspect(op.get_bind()).get_indexes("response")}:
        op.create_index("ix_response_diary_started", "response", ["enrolment_id", "part", "started_at"],
                        sqlite_where=sa.text("enrolment_id IS NOT NULL"),
                        postgresql_where=sa.text("enrolment_id IS NOT NULL"))


def downgrade():
    op.drop_index("ix_response_diary_started", table_name="response")
