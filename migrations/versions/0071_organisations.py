"""Organisations above the groups (app/orgs.py, konzept-abteilungen-2026-10-15.md): the tables of organisations and
their members, and the organisation of surveys, groups, designs, linked elements, research projects and iDs on the
sign-in list.

Additive only: two new tables and columns that may stay empty (empty = the organisation everything started in, key
"main", which the code makes at start-up, orgs.ensure_seed); older containers ignore them. The new columns carry an
index but no foreign key, so SQLite adds them without copying the tables (survey has revision triggers).
"""
import sqlalchemy as sa
from alembic import op

revision = "0071"
down_revision = "0069"
branch_labels = None
depends_on = None
ROLLING = True

# (table, column, referenced table, on delete) of the two new tables
FOREIGN = (("organisation", "default_type_id", "account_type", "SET NULL"),
           ("organisation", "created_by_id", "user", "SET NULL"),
           ("organisation_member", "org_id", "organisation", "CASCADE"),
           ("organisation_member", "user_id", "user", "SET NULL"),
           ("organisation_member", "added_by_id", "user", "SET NULL"))
TABLES = ("survey", "workgroup", "design", "construct", "research_project", "sign_in_orcid")


def upgrade():
    bind = op.get_bind()
    names = sa.inspect(bind).get_table_names()
    if "organisation" not in names:
        op.create_table(
            "organisation",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("key", sa.String(20), nullable=True),
            sa.Column("name", sa.String(80), nullable=False),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("kinds_cap", sa.JSON(), nullable=True),
            sa.Column("may_create", sa.Boolean(), nullable=True),
            sa.Column("max_accounts", sa.Integer(), nullable=True),
            sa.Column("invite_out", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("invite_in", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("basis", sa.String(300), nullable=False, server_default=""),
            sa.Column("default_type_id", sa.Integer(), nullable=True, index=True),
            sa.Column("from_group_name", sa.String(80), nullable=False, server_default=""),
            sa.Column("created_by_id", sa.Integer(), nullable=True, index=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("key", name="organisation_key_key"),
            sa.ForeignKeyConstraint(["default_type_id"], ["account_type.id"], name="organisation_default_type_id_fkey",
                                    ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["created_by_id"], ["user.id"], name="organisation_created_by_id_fkey",
                                    ondelete="SET NULL"),
        )
    if "organisation_member" not in names:
        op.create_table(
            "organisation_member",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("org_id", sa.Integer(), nullable=False, index=True),
            sa.Column("orcid", sa.String(19), nullable=False, index=True),
            sa.Column("user_id", sa.Integer(), nullable=True, index=True),
            sa.Column("role", sa.String(10), nullable=False, server_default="member"),
            sa.Column("is_home", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("valid_until", sa.DateTime(), nullable=True),
            sa.Column("added_by_id", sa.Integer(), nullable=True, index=True),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["org_id"], ["organisation.id"], name="organisation_member_org_id_fkey",
                                    ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["user.id"], name="organisation_member_user_id_fkey",
                                    ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["added_by_id"], ["user.id"], name="organisation_member_added_by_id_fkey",
                                    ondelete="SET NULL"),
            sa.UniqueConstraint("org_id", "orcid", name="organisation_member_org_id_orcid_key"),
        )
    # a database that had the tables already (made from the models) may lack their keys: add what is missing, as 0069
    missing = {}
    for table, col, target, ondelete in FOREIGN:
        known = {tuple(fk["constrained_columns"]) for fk in sa.inspect(bind).get_foreign_keys(table)}
        if (col,) not in known:
            missing.setdefault(table, []).append((col, target, ondelete))
    if missing:
        from app.data_revision import install, uninstall

        uninstall(bind)
        for table, keys in missing.items():
            with op.batch_alter_table(table) as batch:
                for col, target, ondelete in keys:
                    batch.create_foreign_key(f"{table}_{col}_fkey", target, [col], ["id"], ondelete=ondelete)
        install(bind)
    for table in TABLES:
        if "org_id" not in {c["name"] for c in sa.inspect(bind).get_columns(table)}:
            op.add_column(table, sa.Column("org_id", sa.Integer(), nullable=True))
        name = f"ix_{table}_org_id"
        if name not in {i["name"] for i in sa.inspect(bind).get_indexes(table)}:
            op.create_index(name, table, ["org_id"])


def downgrade():
    # SQLite copies the tables for this; the revision triggers name them and go first (app/data_revision.py)
    from app.data_revision import install, uninstall

    uninstall(op.get_bind())
    for table in TABLES:
        op.drop_index(f"ix_{table}_org_id", table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_column("org_id")
    op.drop_table("organisation_member")
    op.drop_table("organisation")
    install(op.get_bind())
