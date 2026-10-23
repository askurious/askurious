"""SplitSession counts the changes of its row (version_id_col), as Response does: two requests of the separated
section that overlap no longer both store its answers as a contact entry (code review 2026-10-15, F5).

Additive only: a column with a server default; older participant containers ignore it.
"""
import sqlalchemy as sa
from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None
ROLLING = True


def upgrade():
    if "version" not in {c["name"] for c in sa.inspect(op.get_bind()).get_columns("split_session")}:
        op.add_column("split_session", sa.Column("version", sa.Integer(), nullable=False, server_default="1"))


def downgrade():
    # the table has no rowid on SQLite (its rows say nothing about their order): the copy keeps that
    with op.batch_alter_table("split_session", table_kwargs={"sqlite_with_rowid": False}) as batch:
        batch.drop_column("version")
