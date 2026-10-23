"""Several supervisors per account (Ralf 2026-10-18: "als Betreuung mehr als eine Person"): a table account_supervisor,
one row per account and supervising person, filled with the supervisor each account has today.

user_login.supervisor_id stays and holds the first supervisor (app/account_types.set_supervisors), so the code before
keeps reading one; it is dropped in a later deploy.

Rolling: a new table only, and the code before never reads it.
"""
import sqlalchemy as sa
from alembic import op

revision = "0081"
down_revision = "0080"
branch_labels = None
depends_on = None
ROLLING = True

login = sa.table("user_login", sa.column("user_id", sa.Integer), sa.column("supervisor_id", sa.Integer))
supervisor = sa.table("account_supervisor", sa.column("user_id", sa.Integer), sa.column("supervisor_id", sa.Integer),
                      sa.column("created_at", sa.DateTime))


FOREIGN = ("user_id", "supervisor_id")


def upgrade():
    bind = op.get_bind()
    if "account_supervisor" in sa.inspect(bind).get_table_names():
        # made from the models already: an old schema repair (0014) may have removed its keys, as 0079 says
        known = {tuple(fk["constrained_columns"]) for fk in sa.inspect(bind).get_foreign_keys("account_supervisor")}
        missing = [col for col in FOREIGN if (col,) not in known]
        if missing:
            with op.batch_alter_table("account_supervisor") as batch:
                for col in missing:
                    batch.create_foreign_key(f"account_supervisor_{col}_fkey", "user", [col], ["id"], ondelete="CASCADE")
    else:
        op.create_table(
            "account_supervisor",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), nullable=False),
            sa.Column("supervisor_id", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="account_supervisor_user_id_fkey", ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["supervisor_id"], ["user.id"], name="account_supervisor_supervisor_id_fkey",
                                    ondelete="CASCADE"),
            sa.UniqueConstraint("user_id", "supervisor_id", name="uq_account_supervisor"),
        )
        op.create_index("ix_account_supervisor_user_id", "account_supervisor", ["user_id"])
        op.create_index("ix_account_supervisor_supervisor_id", "account_supervisor", ["supervisor_id"])
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    have = {(r.user_id, r.supervisor_id) for r in bind.execute(sa.select(supervisor.c.user_id, supervisor.c.supervisor_id))}
    rows = [{"user_id": r.user_id, "supervisor_id": r.supervisor_id, "created_at": now}
            for r in bind.execute(sa.select(login.c.user_id, login.c.supervisor_id).where(login.c.supervisor_id.is_not(None)))
            if (r.user_id, r.supervisor_id) not in have and r.user_id != r.supervisor_id]
    if rows:
        op.bulk_insert(supervisor, rows)


def downgrade():
    op.drop_table("account_supervisor")
