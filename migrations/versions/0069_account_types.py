"""User types with a period (app/account_types.py, konzept-nutzertypen-2026-10-15.md): the table of types, and per
account its type, start, end, the reason of a block, supervisor and course; the type and period an iD on the sign-in
list gets; a course's type and end on its group; invitations whose links the inviter passed on.

Additive only: a new table and columns that may stay empty; older containers ignore them. The five types are seeded
by the code at start-up (account_types.ensure_seed), not here.
"""
import sqlalchemy as sa
from alembic import op

revision = "0069"
down_revision = "0068"
branch_labels = None
depends_on = None
ROLLING = True

COLUMNS = (
    ("user_login", sa.Column("account_type_id", sa.Integer(), nullable=True)),
    ("user_login", sa.Column("valid_spec", sa.String(20), nullable=True)),
    ("user_login", sa.Column("valid_from", sa.DateTime(), nullable=True)),
    ("user_login", sa.Column("valid_until", sa.DateTime(), nullable=True)),
    ("user_login", sa.Column("block_reason", sa.String(10), nullable=True)),
    ("user_login", sa.Column("supervisor_id", sa.Integer(), nullable=True)),
    ("user_login", sa.Column("origin_group_id", sa.Integer(), nullable=True)),
    ("user_login", sa.Column("expiry_noticed", sa.Integer(), nullable=True)),
    ("sign_in_orcid", sa.Column("account_type_id", sa.Integer(), nullable=True)),
    ("sign_in_orcid", sa.Column("valid_spec", sa.String(20), nullable=True)),
    ("member_invite", sa.Column("handed_out", sa.Boolean(), nullable=True)),
    ("workgroup", sa.Column("account_type_id", sa.Integer(), nullable=True)),
    ("workgroup", sa.Column("course_until", sa.DateTime(), nullable=True)),
)
# (table, column, referenced table, on delete)
FOREIGN = (("user_login", "account_type_id", "account_type", "SET NULL"),
           ("user_login", "supervisor_id", "user", "SET NULL"),
           ("user_login", "origin_group_id", "workgroup", "SET NULL"),
           ("sign_in_orcid", "account_type_id", "account_type", "SET NULL"),
           ("workgroup", "account_type_id", "account_type", "SET NULL"))
INDEXED = (("user_login", "account_type_id"), ("user_login", "valid_until"), ("user_login", "supervisor_id"),
           ("user_login", "origin_group_id"), ("sign_in_orcid", "account_type_id"), ("workgroup", "account_type_id"))


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "account_type" not in inspector.get_table_names():
        op.create_table(
            "account_type",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("key", sa.String(20), nullable=True, unique=True),
            sa.Column("name_de", sa.String(80), nullable=False),
            sa.Column("name_en", sa.String(80), nullable=False, server_default=""),
            sa.Column("duration", sa.String(20), nullable=False, server_default=""),
            sa.Column("kinds", sa.JSON(), nullable=True),
            sa.Column("may_create", sa.Boolean(), nullable=True),
            sa.Column("supervised", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("delete_months", sa.Integer(), nullable=True),
            sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
    for table, column in COLUMNS:
        if column.name not in {c["name"] for c in sa.inspect(bind).get_columns(table)}:
            op.add_column(table, column)
    missing = {}
    for table, column, target, ondelete in FOREIGN:
        known = {tuple(fk["constrained_columns"]) for fk in sa.inspect(bind).get_foreign_keys(table)}
        if (column,) not in known:
            missing.setdefault(table, []).append((column, target, ondelete))
    if missing:
        # SQLite adds a foreign key by copying the table (batch); the revision triggers name tables and go first
        # (app/data_revision.py), as in the downgrade of 0067
        from app.data_revision import install, uninstall

        uninstall(bind)
        for table, keys in missing.items():
            with op.batch_alter_table(table) as batch:
                for column, target, ondelete in keys:
                    batch.create_foreign_key(f"{table}_{column}_fkey", target, [column], ["id"], ondelete=ondelete)
        install(bind)
    for table, column in INDEXED:
        name = f"ix_{table}_{column}"
        if name not in {i["name"] for i in sa.inspect(bind).get_indexes(table)}:
            op.create_index(name, table, [column])


def downgrade():
    # SQLite copies the tables for this; the revision triggers name them and go first (app/data_revision.py)
    from app.data_revision import install, uninstall

    uninstall(op.get_bind())
    for table, column in INDEXED:
        op.drop_index(f"ix_{table}_{column}", table_name=table)
    by_table = {}
    for table, column in COLUMNS:
        by_table.setdefault(table, []).append(column.name)
    for table, names in by_table.items():
        with op.batch_alter_table(table) as batch:
            for name in names:
                batch.drop_column(name)
    op.drop_table("account_type")
    install(op.get_bind())
