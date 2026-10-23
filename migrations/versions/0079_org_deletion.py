"""Accounts an organisation admin marked for deletion (app/org_delete.py).

A new table only; the old code never reads it.
"""
import sqlalchemy as sa
from alembic import op

revision = "0079"
down_revision = "0078"
branch_labels = None
depends_on = None
ROLLING = True


FOREIGN = (("user_id", "CASCADE"), ("requested_by_id", "SET NULL"))


def upgrade():
    bind = op.get_bind()
    if "org_deletion" in sa.inspect(bind).get_table_names():
        # made from the models already: an old schema repair (0014) may have removed its keys, as 0057 and 0071 say
        known = {tuple(fk["constrained_columns"]) for fk in sa.inspect(bind).get_foreign_keys("org_deletion")}
        missing = [(col, ondelete) for col, ondelete in FOREIGN if (col,) not in known]
        if missing:
            with op.batch_alter_table("org_deletion") as batch:
                for col, ondelete in missing:
                    batch.create_foreign_key(f"org_deletion_{col}_fkey", "user", [col], ["id"], ondelete=ondelete)
        if "ix_org_deletion_requested_by_id" not in {i["name"] for i in sa.inspect(bind).get_indexes("org_deletion")}:
            op.create_index("ix_org_deletion_requested_by_id", "org_deletion", ["requested_by_id"])
        return
    op.create_table(
        "org_deletion",
        sa.Column("user_id", sa.Integer(), primary_key=True),
        sa.Column("org_id", sa.Integer(), nullable=False),
        sa.Column("requested_by_id", sa.Integer(), nullable=True),
        sa.Column("requested_at", sa.DateTime(), nullable=False),
        sa.Column("due_at", sa.DateTime(), nullable=False),
        sa.Column("choices", sa.JSON(), nullable=False),
        sa.Column("open_points", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="org_deletion_user_id_fkey", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["user.id"], name="org_deletion_requested_by_id_fkey",
                                ondelete="SET NULL"),
    )
    op.create_index("ix_org_deletion_org_id", "org_deletion", ["org_id"])
    op.create_index("ix_org_deletion_due_at", "org_deletion", ["due_at"])
    op.create_index("ix_org_deletion_requested_by_id", "org_deletion", ["requested_by_id"])


def downgrade():
    op.drop_table("org_deletion")
