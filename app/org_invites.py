"""Open invitations of an organisation (Verwaltung > Personen, block "Offene Einladungen"; Ralf 2026-10-20).

Open is what lets someone in who has not come in yet:
- an ORCID iD taken in with "Person hinzufügen" (SignInOrcid of the organisation) without an account yet,
- an iD named before its first sign-in in the organisation or one of its groups (a pending account, the "invited" of
  orgs.counts on Administration > Organisationen),
- the one-time link of an account without ORCID at home here that has never signed in, while it is valid.
An invitation to join for an existing account (org_admin.people_action) is signed and not stored, so it is not listed.

Withdrawing takes back only what is listed: the iD can no longer sign in into the organisation, the pending account
leaves the organisation and its groups (org_delete.remove), the one-time link ends; an account without ORCID stays and
is deleted on its own page."""
from flask_login import current_user

from app import orgs
from app.extensions import db
from app.lang import de_en
from app.models import AuthToken, OrganisationMember, SignInOrcid, User, WorkGroup, WorkGroupMember, utcnow
from app.tables import ROW_SIZES, Listing, text_key

SORTS = {"name": "asc", "kind": "asc", "since": "desc", "by": "asc", "until": "asc"}


def _listed_in(org):
    """Entries of the sign-in list that come into `org`: those of the organisation everything started in carry no
    organisation (SignInOrcid.org_id None, "Person hinzufügen" of the administration), and since the list is no
    longer shown on its own (2026-10-29) they are open invitations of that organisation."""
    if org.key == orgs.MAIN:
        return db.or_(SignInOrcid.org_id == org.id, SignInOrcid.org_id.is_(None))
    return SignInOrcid.org_id == org.id


def open_of(org):
    """The open invitations of `org`, one per person: dicts with key, name, orcid, kinds (labels), since, by (a User or
    None), until (None: no end) and may (may the signed-in admin withdraw it)."""
    items = {}
    known = db.select(User.orcid).where(User.pending.is_(False), User.orcid.is_not(None))
    for s in db.session.scalars(db.select(SignInOrcid).where(_listed_in(org), SignInOrcid.orcid.not_in(known))):
        items[s.orcid] = _item(s.orcid, s.note, s.orcid, de_en("Anmeldung mit ORCID iD", "Sign-in with ORCID iD"),
                               s.created_at, s.added_by_id)
    here = orgs.column(WorkGroup.org_id) == org.id
    for user in db.session.scalars(db.select(User).where(User.id.in_(orgs.user_ids_in([org.id])), User.pending.is_(True))):
        key = user.orcid or f"u{user.id}"
        item = items.setdefault(key, _item(key, user.name, user.orcid, None, None, None))
        item["user"] = user
        row = orgs.row_of(org.id, user.id)
        if row is not None:
            item["kinds"].append(de_en("Admin", "Admin") if row.role == "admin" else de_en("Mitglied", "Member"))
            _merge(item, row.created_at, row.added_by_id, row.valid_until)
            item["admin"] = row.role == "admin"
        for m in db.session.scalars(db.select(WorkGroupMember).join(WorkGroup).where(WorkGroupMember.user_id == user.id,
                                                                                    here)):
            item["kinds"].append(de_en("Gruppe {g}", "Group {g}", g=m.group.name))
            item["name"] = item["name"] or m.name
            _merge(item, m.created_at, m.invited_by_id, None)
    links = (db.select(AuthToken, User).join(User, User.id == AuthToken.user_id)
             .where(AuthToken.purpose == "setup", AuthToken.used_at.is_(None), AuthToken.expires_at > utcnow(),
                    AuthToken.created_by_id.is_not(None), User.pending.is_(False), User.last_login_at.is_(None),
                    User.id.in_(db.select(OrganisationMember.user_id).filter_by(org_id=org.id, is_home=True))))
    for token, user in db.session.execute(links):
        item = _item(f"l{user.id}", user.name, None, de_en("Einmal-Link ohne ORCID", "One-time link without ORCID"),
                     token.created_at, token.created_by_id)
        item["until"], item["user"] = token.expires_at, user
        items[item["key"]] = item
    out = list(items.values())
    by = {u.id: u for u in db.session.scalars(db.select(User).where(User.id.in_({i["by_id"] for i in out} - {None})))}
    for item in out:
        item["by"] = by.get(item["by_id"])
        item["may"] = _may(org, item)
    return out


def _item(key, name, orcid, kind, since, by_id):
    return {"key": key, "name": name or "", "orcid": orcid, "kinds": [kind] if kind else [], "since": since,
            "by_id": by_id, "until": None, "user": None, "admin": False}


def _merge(item, since, by_id, until):
    if since is not None and (item["since"] is None or since < item["since"]):
        item["since"] = since
    item["by_id"] = item["by_id"] or by_id
    item["until"] = item["until"] or until


def _may(org, item):
    """Admins of the organisation are taken out by a platform admin (as in people_action); a one-time link only by
    whoever manages the account."""
    if item["admin"] and not current_user.is_admin:
        return False
    if item["key"].startswith("l"):
        return orgs.manages(current_user, item["user"], org)
    return True


def page_of(org):
    """The open invitations as a sorted page (prefix "e", 10/20/50 rows): (Listing, items of the page). Sorted here,
    not by the database: the rows come from three tables and are few."""
    page = Listing("e", {k: ([db.literal(0)], d) for k, d in SORTS.items()}, default="since", anchor="einladungen",
                   sizes=ROW_SIZES)
    items = open_of(org)
    keys = {"name": lambda i: text_key(i["name"] or i["orcid"] or ""), "kind": lambda i: text_key(", ".join(i["kinds"])),
            "since": lambda i: i["since"], "by": lambda i: text_key(i["by"].display_name) if i["by"] else None,
            "until": lambda i: i["until"]}[page.sort]
    filled = sorted((i for i in items if keys(i) is not None), key=keys, reverse=page.dir == "desc")
    items = filled + [i for i in items if keys(i) is None]  # empty values last, as nulls_last in Listing
    page.total = len(items)
    page.pages = max(-(-page.total // page.per_page), 1)
    page.page = min(page.page, page.pages)
    return page, items[(page.page - 1) * page.per_page:page.page * page.per_page]


def withdraw(org, key):
    """Take back the open invitation `key` of `org`: the name it went to, or None (not open, or not the admin's to
    withdraw). Does not commit."""
    item = next((i for i in open_of(org) if i["key"] == key), None)
    if item is None or not item["may"]:
        return None
    user = item["user"]
    if key.startswith("l"):
        db.session.execute(db.delete(AuthToken).where(AuthToken.user_id == user.id, AuthToken.purpose == "setup"))
    else:
        if item["orcid"]:
            db.session.execute(db.delete(SignInOrcid).where(SignInOrcid.orcid == item["orcid"], _listed_in(org)))
        if user is not None:
            from app import org_delete

            org_delete.remove(org, user)
    orgs.forget()
    return item["name"] or item["orcid"] or "–"
