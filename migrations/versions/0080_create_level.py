"""The level "create" per survey type takes the place of the right to create surveys (konzept-stufe-anlegen-2026-10-18.md,
Ralf "wie empfohlen", E7).

Each account keeps what it had: where the former auth.may_create_surveys said yes (the tick on the account, else a
group with "darf Befragungen anlegen", within the home organisation's "nobody creates"), every type at "edit" becomes
"create" in user_login.kinds; nothing else changes. User types with "Eigene Befragungen anlegen: ja" get "create"
the same way in account_type.kinds. The columns user_login.may_create and account_type.may_create stay as they were
(no longer read; dropped in a later deploy), so the step back to the code before keeps working.

Rolling: the code before reads a level it does not know as "edit" (kind_access.full) and still reads may_create, so
a participant container not yet restarted sees exactly what it saw before.
"""
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0080"
down_revision = "0079"
branch_labels = None
depends_on = None
ROLLING = True

KINDS = ("survey", "live", "interview", "diary")
LOWER = ("off", "view")

user = sa.table("user", sa.column("id", sa.Integer), sa.column("orcid", sa.String))
login = sa.table("user_login", sa.column("user_id", sa.Integer), sa.column("kinds", sa.JSON),
                 sa.column("may_create", sa.Boolean), sa.column("status", sa.String),
                 sa.column("failed_count", sa.Integer), sa.column("failed_streak", sa.Integer),
                 sa.column("session_version", sa.Integer),
                 sa.column("created_at", sa.DateTime))
group = sa.table("workgroup", sa.column("id", sa.Integer), sa.column("may_create", sa.Boolean))
member = sa.table("workgroup_member", sa.column("group_id", sa.Integer), sa.column("orcid", sa.String))
org = sa.table("organisation", sa.column("id", sa.Integer), sa.column("key", sa.String),
               sa.column("may_create", sa.Boolean))
org_member = sa.table("organisation_member", sa.column("org_id", sa.Integer), sa.column("orcid", sa.String),
                      sa.column("is_home", sa.Boolean))
account_type = sa.table("account_type", sa.column("id", sa.Integer), sa.column("kinds", sa.JSON),
                        sa.column("may_create", sa.Boolean))


def raised(kinds):
    """The stored levels with "create" for every type at "edit" (a missing type is "edit")."""
    kinds = kinds if isinstance(kinds, dict) else {}
    out = {k: v for k, v in kinds.items() if k in KINDS and v in LOWER}
    for k in KINDS:
        if k not in out:
            out[k] = "create"
    return out


def upgrade():
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if "user_login" not in tables or "orcid" not in {c["name"] for c in sa.inspect(bind).get_columns("workgroup_member")}:
        return  # no table yet, or a schema made from models after 0085 (the key columns are gone): nothing to carry over
    granting = set(bind.execute(sa.select(member.c.orcid).join(group, group.c.id == member.c.group_id)
                                .where(group.c.may_create.is_(True))).scalars())
    orgs = {row.id: row for row in bind.execute(sa.select(org.c.id, org.c.key, org.c.may_create))} \
        if "organisation" in tables else {}
    homes = dict(bind.execute(sa.select(org_member.c.orcid, org_member.c.org_id).where(org_member.c.is_home.is_(True)))
                 .all()) if "organisation_member" in tables else {}
    main = next((o for o in orgs.values() if o.key == "main"), None)
    rows = {row.user_id: row for row in bind.execute(sa.select(login.c.user_id, login.c.kinds, login.c.may_create))}
    for uid, orcid in bind.execute(sa.select(user.c.id, user.c.orcid)).all():
        row = rows.get(uid)
        own = row.may_create if row is not None else None
        home = orgs.get(homes.get(orcid)) if orcid in homes else main
        capped = home is not None and (home.key == "guest" or home.may_create is False)
        if capped and not (home.key == "guest" and own is True):
            continue
        creates = own if own is not None else orcid in granting
        if not creates:
            continue
        if row is None:
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            bind.execute(login.insert().values(user_id=uid, kinds=raised(None), status="active", failed_count=0,
                                               failed_streak=0, session_version=0, created_at=now))
        else:
            bind.execute(login.update().where(login.c.user_id == uid).values(kinds=raised(row.kinds)))
    if "account_type" in tables:
        for row in bind.execute(sa.select(account_type.c.id, account_type.c.kinds)
                                .where(account_type.c.may_create.is_(True))).all():
            bind.execute(account_type.update().where(account_type.c.id == row.id).values(kinds=raised(row.kinds)))


def downgrade():
    # "create" read by the code before as "edit"; may_create was kept, so nothing has to go back
    pass
