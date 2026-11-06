"""Administration > Einladungen: what is still open on the platform, by what it leads to (2026-11-06).

Organisations: open founding links (app/org_links.py) and people named as admin of an organisation who have not signed
in yet. Groups: lead links, join links and
invitations by mail to a group. People: the open invitations of every organisation (app/org_invites.py), registration
links and invitations by mail to a survey.

Only a list: each row leads to the page where the invitation is withdrawn. Expired rows are left out here and go with
the sweeps of their modules. Tokens are stored as hashes, so no address can be shown again."""
from flask import url_for

from app import org_invites, org_links, orgs, signup_links
from app.extensions import db
from app.lang import de_en
from app.models import GroupJoinLink, MemberInvite, SignUpLink, Survey, User, WorkGroup, utcnow


def _row(kind, who, target, since, by_id, until, href=None):
    return {"kind": kind, "who": who, "target": target, "since": since, "by_id": by_id, "until": until, "href": href}


def _of_organisations():
    """Open invitations of the organisations' lists, one row per person: (admins of an organisation, everyone else)."""
    admins, people = {}, {}
    for org in orgs.all_orgs():
        for item in org_invites.open_of(org):
            if item["key"].startswith("s"):
                continue  # a link for one person is a registration link, listed from its own table
            part = admins if item["admin"] else people
            row = part.get(item["key"])
            if row is None:
                kinds = ", ".join(item["kinds"]) or de_en("Anmeldung", "Sign-in")
                part[item["key"]] = _row(kinds, item["name"] or item["orcid"] or "–", org.name, item["since"],
                                         item["by_id"], item["until"], url_for("org.people", org_id=org.id) + "#einladungen")
            elif org.name not in row["target"]:
                row["target"] += f", {org.name}"
    return list(admins.values()), list(people.values())


def _of_groups():
    now = utcnow()
    rows = []
    names = {}

    def group_name(group_id):
        if group_id not in names:
            group = db.session.get(WorkGroup, group_id)
            names[group_id] = group.name if group is not None else "–"
        return names[group_id]

    href = url_for("admin.groups") + "#gruppen"
    for link in db.session.scalars(db.select(GroupJoinLink).where(GroupJoinLink.expires_at >= now,
                                                                  GroupJoinLink.uses < GroupJoinLink.max_uses)):
        lead = link.role == "lead"
        rows.append(_row(de_en("Leitungslink", "Lead link") if lead else de_en("Beitrittslink", "Join link"),
                         de_en("{u} von {n} genutzt", "{u} of {n} used", u=link.uses, n=link.max_uses),
                         group_name(link.group_id), link.created_at, link.created_by_id, link.expires_at, href))
    for invite in db.session.scalars(db.select(MemberInvite).where(MemberInvite.group_id.is_not(None),
                                                                   MemberInvite.expires_at >= now)):
        rows.append(_row(de_en("Einladung per E-Mail", "Invitation by email"), invite.email, group_name(invite.group_id),
                         invite.created_at, invite.created_by_id, invite.expires_at, href))
    return rows


def _of_links_and_surveys():
    now = utcnow()
    rows = []
    href = url_for("admin.researchers") + "#einladungslinks"
    for link in db.session.scalars(db.select(SignUpLink).where(SignUpLink.expires_at >= now,
                                                               SignUpLink.uses < SignUpLink.max_uses)):
        org = orgs.get(link.org_id)
        kind = de_en("Einmal-Link", "One-time link") if link.password_ok else de_en("Einladungslink", "Invitation link")
        rows.append(_row(kind, link.note or "–", org.name if org is not None else "–", link.created_at,
                         link.created_by_id, None if link.expires_at >= signup_links.NO_END else link.expires_at, href))
    for invite in db.session.scalars(db.select(MemberInvite).where(MemberInvite.survey_id.is_not(None),
                                                                   MemberInvite.expires_at >= now)):
        survey = db.session.get(Survey, invite.survey_id)
        rows.append(_row(de_en("Einladung per E-Mail", "Invitation by email"), invite.email,
                         survey.code if survey is not None else "–", invite.created_at, invite.created_by_id,
                         invite.expires_at))
    return rows


def _of_founding_links():
    href = url_for("admin.organisations") + "#gruendungslinks"
    return [_row(de_en("Gründungslink", "Founding link"), link.note or "–", de_en("neue Organisation", "new organisation"),
                 link.created_at, link.created_by_id, link.expires_at, href) for link in org_links.open_links()]


def overview():
    """[(key, heading, hint, rows)], newest first, each row with `by` (a User or None)."""
    admins, people = _of_organisations()
    parts = [("organisationen", de_en("Organisationen", "Organisations"),
              de_en("Gründungslinks und als Admin Genannte, die sich noch nicht angemeldet haben",
                    "founding links and people named as admin who have not signed in yet"),
              _of_founding_links() + admins),
             ("gruppen", de_en("Gruppen", "Groups"),
              de_en("Leitungslinks, Beitrittslinks und Einladungen per E-Mail", "lead links, join links and invitations by email"),
              _of_groups()),
             ("personen", de_en("Personen", "People"),
              de_en("Einladungslinks, Anmeldungen und Einladungen in Befragungen", "invitation links, sign-ins and invitations to surveys"),
              people + _of_links_and_surveys())]
    ids = {r["by_id"] for _, _, _, rows in parts for r in rows} - {None}
    by = {u.id: u for u in db.session.scalars(db.select(User).where(User.id.in_(ids)))} if ids else {}
    far = utcnow().replace(year=1970)
    for _, _, _, rows in parts:
        for row in rows:
            row["by"] = by.get(row["by_id"])
        rows.sort(key=lambda r: r["since"] or far, reverse=True)
    return parts
