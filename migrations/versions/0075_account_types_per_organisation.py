"""User types per organisation (app/account_types.py, Ralf 2026-10-16): the organisation a type belongs to.

Additive only: a column that may stay empty (empty = the organisation everything started in) with an index, no foreign
key; older containers ignore it. Each organisation's own "Mitarbeitende" is made by the code at start-up
(account_types.ensure_per_org), as are the removal of unused types and the templates.
"""
import sqlalchemy as sa
from alembic import op

revision = "0075"
down_revision = "0074"
branch_labels = None
depends_on = None
ROLLING = True


def upgrade():
    bind = op.get_bind()
    if "org_id" not in {c["name"] for c in sa.inspect(bind).get_columns("account_type")}:
        op.add_column("account_type", sa.Column("org_id", sa.Integer(), nullable=True))
    if "ix_account_type_org_id" not in {i["name"] for i in sa.inspect(bind).get_indexes("account_type")}:
        op.create_index("ix_account_type_org_id", "account_type", ["org_id"])


def downgrade():
    op.drop_index("ix_account_type_org_id", table_name="account_type")
    with op.batch_alter_table("account_type") as batch:
        batch.drop_column("org_id")
