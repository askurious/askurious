"""Stage 2b, second deploy (konto-schluessel-2b-vorschlag.md, all as recommended, Ralf 2026-10-18): the key columns of
before go. People are known by account only: user.orcid holds an ORCID iD or nothing.

- consents ("researchers" in Item.config, TrashedItem.data, EditStep.data): every key of before becomes the reference
  by account ("u<id>"; "m<row id>" for the entry of a deleted account), duplicates and keys of nobody go;
- AInstein projects (shares and comments): an entry keeps "user_id" and loses "orcid";
- address book entries of an iD without account get a pending account (as members do since 0083), so that they keep
  the person; an entry that names nobody goes;
- the columns `orcid` of survey_member, workgroup_member, organisation_member, design_share, design_editor,
  construct_share and contact go, their unique keys by orcid become unique keys by account;
- "q-…" keys of accounts without ORCID become empty.

Rolling once the database is at 0084 (DROPS_UNUSED_SINCE): the code of 0084 reads the references by account and
writes the key columns only as an addition. downgrade brings the columns back (filled from the accounts) and leaves
the references as they are: the code of 0084 reads them.
"""
import json
import re
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op

revision = "0085"
down_revision = "0084"
branch_labels = None
depends_on = None
ROLLING = False
DROPS_UNUSED_SINCE = "0084"

ORCID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]\Z")
REF_RE = re.compile(r"^[um]\d+\Z")
# table, the column of the thing a row belongs to
TABLES = (("survey_member", "survey_id"), ("workgroup_member", "group_id"), ("organisation_member", "org_id"),
          ("design_share", "design_id"), ("design_editor", "design_id"), ("construct_share", "construct_id"),
          ("contact", "owner_id"))

user = sa.table("user", sa.column("id", sa.Integer), sa.column("orcid", sa.String), sa.column("name", sa.String),
                sa.column("affiliation", sa.String), sa.column("is_admin", sa.Boolean),
                sa.column("created_at", sa.DateTime), sa.column("experimental", sa.Boolean),
                sa.column("pending", sa.Boolean))
deleted = sa.table("deleted_account", sa.column("user_id", sa.Integer))


def _load(value):
    if isinstance(value, (dict, list)) or value is None:
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _refs(doc, by_key):
    """`doc` with every list named "researchers" in account references; (doc, changed)."""
    if isinstance(doc, list):
        rows = [_refs(v, by_key) for v in doc]
        return [v for v, _ in rows], any(c for _, c in rows)
    if not isinstance(doc, dict):
        return doc, False
    out, changed = {}, False
    for name, value in doc.items():
        if name == "researchers" and isinstance(value, list):
            refs = []
            for o in value:
                ref = o if isinstance(o, str) and REF_RE.match(o) else by_key.get(o)
                if ref and ref not in refs:
                    refs.append(ref)
            out[name], changed = refs, changed or refs != value
        else:
            out[name], inner = _refs(value, by_key)
            changed = changed or inner
    return out, changed


def _entries(entries, user_of):
    """AInstein shares or comments: "user_id" from the key of before, "orcid" gone; (list, changed)."""
    if not isinstance(entries, list):
        return entries, False
    out, changed = [], False
    for e in entries:
        if isinstance(e, dict) and "orcid" in e:
            e = dict(e)
            key = e.pop("orcid")
            if not e.get("user_id") and key in user_of:
                e["user_id"] = user_of[key]
            if e.get("kind") == "person" and not e.get("user_id"):
                changed = True
                continue  # a share of nobody
            changed = True
        out.append(e)
    return out, changed


def _json_column(bind, table, key, column, where=None):
    t = sa.table(table, sa.column(key, sa.Integer), sa.column(column, sa.JSON))
    query = sa.select(t.c[key], sa.cast(t.c[column], sa.Text)).where(t.c[column].is_not(None))
    if where is not None:
        query = query.where(where(t))
    return t, bind.execute(query).all()


def upgrade():
    bind = op.get_bind()
    if "orcid" not in {c["name"] for c in sa.inspect(bind).get_columns("survey_member")}:
        bind.execute(user.update().where(user.c.orcid.like("q-%")).values(orcid=None))
        return  # a schema made from models after this migration: the columns are gone already
    by_key = {o: f"u{i}" for i, o in bind.execute(sa.select(user.c.id, user.c.orcid)).all() if o}
    sm = sa.table("survey_member", sa.column("id", sa.Integer), sa.column("orcid", sa.String),
                  sa.column("user_id", sa.Integer))
    by_key.update({o: f"m{i}" for i, o in bind.execute(sa.select(sm.c.id, sm.c.orcid).where(sm.c.user_id.is_(None))).all()
                   if o and o.startswith("x-")})
    user_of = {o: i for i, o in bind.execute(sa.select(user.c.id, user.c.orcid)).all() if o}

    # consents, and where they are kept for taking back
    for table, key, column, where in (("item", "id", "config", lambda t: sa.text("type = 'consent'")),
                                      ("trashed_item", "id", "data", None), ("edit_step", "id", "data", None)):
        t, rows = _json_column(bind, table, key, column, where)
        for row_id, text in rows:
            if "researchers" not in (text or ""):
                continue
            doc, changed = _refs(_load(text), by_key)
            if changed:
                bind.execute(t.update().where(t.c[key] == row_id).values({column: doc}))

    # AInstein projects: a share of an iD that never signed in gets a pending account, as members do (0083)
    project = sa.table("research_project", sa.column("id", sa.Integer), sa.column("shares", sa.JSON),
                       sa.column("comments", sa.JSON))
    projects = bind.execute(sa.select(project.c.id, sa.cast(project.c.shares, sa.Text),
                                      sa.cast(project.c.comments, sa.Text))).all()
    top = max(bind.scalar(sa.select(sa.func.max(user.c.id))) or 0, bind.scalar(sa.select(sa.func.max(deleted.c.user_id))) or 0)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for _pid, shares, _comments in projects:
        for share in _load(shares) or []:
            key = share.get("orcid") if isinstance(share, dict) and share.get("kind") == "person" else None
            if key and ORCID_RE.match(key) and key not in user_of:
                top += 1
                bind.execute(user.insert().values(id=top, orcid=key, name=(share.get("name") or "")[:200],
                                                  affiliation="", is_admin=False, created_at=now, experimental=False,
                                                  pending=True))
                user_of[key] = top
    for pid, shares, comments in projects:
        new_shares, c1 = _entries(_load(shares), user_of)
        new_comments, c2 = _entries(_load(comments), user_of)
        if c1 or c2:
            bind.execute(project.update().where(project.c.id == pid)
                         .values(shares=new_shares or None, comments=new_comments or None))

    # address book entries of an iD without account: a pending account, as for members (0083)
    contact = sa.table("contact", sa.column("id", sa.Integer), sa.column("orcid", sa.String),
                       sa.column("user_id", sa.Integer), sa.column("name", sa.String))
    loose = bind.execute(sa.select(contact.c.id, contact.c.orcid, contact.c.name).where(contact.c.user_id.is_(None))).all()
    for cid, orcid, name in loose:
        if orcid and ORCID_RE.match(orcid):
            if orcid not in user_of:
                top += 1
                bind.execute(user.insert().values(id=top, orcid=orcid, name=(name or "")[:200], affiliation="",
                                                  is_admin=False, created_at=now, experimental=False, pending=True))
                user_of[orcid] = top
            bind.execute(contact.update().where(contact.c.id == cid).values(user_id=user_of[orcid]))
        else:
            bind.execute(contact.delete().where(contact.c.id == cid))

    # one row per thing and account (the unique keys by orcid said that before); then the columns go
    for table, parent in TABLES:
        t = sa.table(table, sa.column("id", sa.Integer), sa.column(parent, sa.Integer), sa.column("user_id", sa.Integer))
        keep = sa.select(sa.func.min(t.c.id)).where(t.c.user_id.is_not(None)).group_by(t.c[parent], t.c.user_id)
        bind.execute(t.delete().where(t.c.user_id.is_not(None), t.c.id.not_in(keep)))
    inspector = sa.inspect(bind)
    for table, parent in TABLES:
        uniques = [u["name"] for u in inspector.get_unique_constraints(table) if "orcid" in u["column_names"]]
        indexes = [i["name"] for i in inspector.get_indexes(table) if i["column_names"] == ["orcid"]]
        with op.batch_alter_table(table) as batch:
            for name in uniques:
                batch.drop_constraint(name, type_="unique")
            for name in indexes:
                batch.drop_index(name)
            batch.drop_column("orcid")
            batch.create_unique_constraint(f"{table}_{parent}_user_id_key", [parent, "user_id"])

    bind.execute(user.update().where(user.c.orcid.like("q-%")).values(orcid=None))


def downgrade():
    bind = op.get_bind()
    for table, parent in TABLES:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"{table}_{parent}_user_id_key", type_="unique")
            batch.add_column(sa.Column("orcid", sa.String(19), nullable=True))
            if table != "contact":
                batch.create_index(f"ix_{table}_orcid", ["orcid"])
            batch.create_unique_constraint(f"{table}_{parent}_orcid_key", [parent, "orcid"])
        t = sa.table(table, sa.column("orcid", sa.String), sa.column("user_id", sa.Integer))
        match = sa.select(user.c.orcid).where(user.c.id == t.c.user_id).scalar_subquery()
        bind.execute(t.update().where(t.c.user_id.is_not(None)).values(orcid=match))
