"""Every member and share points to an account (konto-schluessel-plan.md, all as recommended, Ralf 2026-10-18):
B1 the account number (user_id) beside the key of before (`orcid`: the ORCID iD or the internal key of an account
without ORCID), B2 an account kept for an ORCID iD that was added before its first sign-in (user.pending), B3 the
entries of deleted accounts (keys "x-…") keep their name and point to no account.

Adds user.pending and user_id on design_share, design_editor, construct_share and contact; makes a pending account
for every ORCID iD in a member or share table that has none; fills user_id wherever it is empty and the key belongs to
an account. The key columns stay and are kept in step by the code (app/people_keys.py) until a later migration drops
them.

Rolling: only additions and rows the code before reads as before (a pending account looks to it like an account that
never signed in, and participants' containers list no accounts). downgrade takes the pending accounts out again.
"""
import re
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None
ROLLING = True

ORCID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]\Z")
NEW_USER_ID = ("design_share", "design_editor", "construct_share", "contact")
# every table that knows a person by key, with the column of a name it may hold for a person without account
PERSON_TABLES = (("survey_member", "name"), ("workgroup_member", "name"), ("organisation_member", None),
                 ("design_share", None), ("design_editor", None), ("construct_share", None), ("contact", "name"))

user = sa.table("user", sa.column("id", sa.Integer), sa.column("orcid", sa.String), sa.column("name", sa.String),
                sa.column("affiliation", sa.String), sa.column("is_admin", sa.Boolean),
                sa.column("created_at", sa.DateTime), sa.column("experimental", sa.Boolean),
                sa.column("pending", sa.Boolean))
deleted = sa.table("deleted_account", sa.column("user_id", sa.Integer))


def _columns(bind, table):
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _person(table, name=None):
    cols = [sa.column("orcid", sa.String), sa.column("user_id", sa.Integer)]
    if name:
        cols.append(sa.column(name, sa.String))
    return sa.table(table, *cols)


def upgrade():
    bind = op.get_bind()
    if "pending" not in _columns(bind, "user"):
        op.add_column("user", sa.Column("pending", sa.Boolean(), nullable=False, server_default=sa.false()))
    for table in NEW_USER_ID:
        inspector = sa.inspect(bind)
        has_column = "user_id" in _columns(bind, table)
        has_key = any(fk["constrained_columns"] == ["user_id"] for fk in inspector.get_foreign_keys(table))
        has_index = any(ix["column_names"] == ["user_id"] for ix in inspector.get_indexes(table))
        if has_column and has_key and has_index:
            continue
        with op.batch_alter_table(table) as batch:  # also a database whose tables were made from newer models
            if not has_column:
                batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
            if not has_key:
                batch.create_foreign_key(f"{table}_user_id_fkey", "user", ["user_id"], ["id"], ondelete="CASCADE")
            if not has_index:
                batch.create_index(f"ix_{table}_user_id", ["user_id"])

    if "orcid" not in _columns(bind, "survey_member"):
        return  # a schema made from models after 0085: the key columns are gone, nothing to carry over
    known = {o for (o,) in bind.execute(sa.select(user.c.orcid))}
    names = {}
    for table, name in PERSON_TABLES:
        if table == "contact":
            continue  # an address book entry alone makes no pending account (app/people_keys.py)
        t = _person(table, name)
        rows = bind.execute(sa.select(t.c.orcid, t.c[name] if name else sa.literal(""))).all()
        for orcid, shown in rows:
            if orcid and ORCID_RE.match(orcid) and orcid not in known:
                names.setdefault(orcid, "")
                names[orcid] = names[orcid] or (shown or "")
    top = max(bind.scalar(sa.select(sa.func.max(user.c.id))) or 0, bind.scalar(sa.select(sa.func.max(deleted.c.user_id))) or 0)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for n, orcid in enumerate(sorted(names), start=1):
        bind.execute(user.insert().values(id=top + n, orcid=orcid, name=names[orcid][:200], affiliation="",
                                          is_admin=False, created_at=now, experimental=False, pending=True))

    for table, name in PERSON_TABLES:
        t = _person(table)
        match = sa.select(user.c.id).where(user.c.orcid == t.c.orcid).scalar_subquery()
        bind.execute(t.update().where(t.c.user_id.is_(None), t.c.orcid.in_(sa.select(user.c.orcid))).values(user_id=match))


def downgrade():
    bind = op.get_bind()
    pending = sa.select(user.c.id).where(user.c.pending.is_(True))
    for table in ("survey_member", "workgroup_member", "organisation_member"):
        t = _person(table)
        bind.execute(t.update().where(t.c.user_id.in_(pending)).values(user_id=None))
    for table in NEW_USER_ID:
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_user_id")
            batch.drop_constraint(f"{table}_user_id_fkey", type_="foreignkey")
            batch.drop_column("user_id")
    bind.execute(user.delete().where(user.c.pending.is_(True)))
    with op.batch_alter_table("user") as batch:
        batch.drop_column("pending")
