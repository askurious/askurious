"""Organisations: a level above the groups for departments, chairs, institutes and companies
(konzept-abteilungen-2026-10-15.md, Ralf 2026-10-15 "alles wie empfohlen", A23: one organisation to start with, then
groups made into organisations).

An organisation is a fence, not an access (A3). People, groups, surveys, designs, linked elements and research
projects belong to one; being in it opens nothing by itself, what a person sees still comes from their own entry, a
group or a share (app/access.py). Only sharing, searching and "for everyone" end at its edge: a survey is shared only
with groups of its own organisation, a group takes no account of another organisation by being added, the people
search finds the members of one's own organisations (A7, A8, A11). The one gap in the fence is the personal entry in a
survey (A9): who is invited there sees that survey and nothing else of its organisation.

Who belongs to an organisation: whoever has a row in it (OrganisationMember, "admin" or "member"), and every member of
one of its groups. Each account has one home (A4), whose admins will manage it; without a row "is_home" that is the
organisation everything started in (key "main"). Empty org_id columns mean "main" as well, so the code from before
and older containers stay right, and while there is only one organisation nothing anywhere looks different (A24).
"""
from flask import g, has_app_context, has_request_context

from app.extensions import db
from app.lang import de_en
from app.models import (Construct, Design, Organisation, OrganisationMember, ResearchProject, Survey, User, WorkGroup,
                        WorkGroupMember, utcnow)

MAIN = "main"
GUEST = "guest"  # accounts that came in only by a personal invitation to a survey (A5): no organisation of their own
MAX_NAME = 80
ROLES = ("admin", "member")
OWNED = (Survey, WorkGroup, Design, Construct, ResearchProject)  # what belongs to an organisation


def role_label(role):
    return {"admin": de_en("Admin der Organisation", "Organisation admin"),
            "member": de_en("Mitglied", "Member")}[role]


# --- the organisation everything started in ------------------------------------------------------------------------

def ensure_seed():
    """The organisation "main", named like the platform, and the place of guests, once (at the start of the server,
    app/__init__.py)."""
    if db.session.scalar(db.select(Organisation.id).filter_by(key=MAIN)) is None:
        from app import brand

        db.session.add(Organisation(key=MAIN, name=brand.NAME))
        db.session.commit()
    if db.session.scalar(db.select(Organisation.id).filter_by(key=GUEST)) is None:
        db.session.add(Organisation(key=GUEST, name="Gäste", may_create=False, invite_out=False))
        db.session.commit()


def guest_id():
    cache = _cache()
    if "guest" not in cache:
        cache["guest"] = db.session.scalar(db.select(Organisation.id).filter_by(key=GUEST))
    return cache["guest"]


def is_guest(user):
    """An account at home nowhere but in the surveys it was invited to (A5)."""
    gid = guest_id()
    return gid is not None and home_id(user) == gid


def label(org):
    """The name of an organisation as pages show it ("Gäste" in the language of the page)."""
    if org is None:
        return ""
    if org.key == GUEST:
        return de_en("Gäste (ohne Organisation)", "Guests (no organisation)")
    return org.name


def _cache():
    return g.setdefault("orgs", {}) if has_app_context() else {}


def forget():
    if has_app_context():
        g.pop("orgs", None)


def default_id():
    cache = _cache()
    if "main" not in cache:
        cache["main"] = db.session.scalar(db.select(Organisation.id).filter_by(key=MAIN))
    return cache["main"]


def main():
    return db.session.scalar(db.select(Organisation).filter_by(key=MAIN))


def key(org_id):
    """The organisation an org_id column means: empty is "main"."""
    return org_id if org_id is not None else default_id()


def of(thing):
    """The id of the organisation a survey, group, design, linked element or research project belongs to."""
    return key(getattr(thing, "org_id", None))


def same(a, b):
    """Whether two things (or org ids) are in the same organisation."""
    a = of(a) if not isinstance(a, (int, type(None))) else key(a)
    b = of(b) if not isinstance(b, (int, type(None))) else key(b)
    return a == b


def column(col):
    """SQL: the organisation an org_id column means (empty = "main")."""
    d = default_id()
    return col if d is None else db.func.coalesce(col, d)


def get(org_id):
    try:
        return db.session.get(Organisation, int(org_id)) if org_id not in (None, "") else None
    except (TypeError, ValueError):
        return None


def all_orgs(guests=True):
    query = db.select(Organisation).order_by(Organisation.key.is_(None), db.func.lower(Organisation.name))
    if not guests:
        query = query.where(db.or_(Organisation.key.is_(None), Organisation.key != GUEST))
    return list(db.session.scalars(query))


def count():
    cache = _cache()
    if "count" not in cache:
        cache["count"] = db.session.scalar(db.select(db.func.count(Organisation.id))) or 0
    return cache["count"]


def several():
    """More than one organisation besides the guests: only then do pages show them (A24)."""
    return count() - (1 if guest_id() is not None else 0) > 1


def names():
    """{org id: name}, with None for "main" (an empty org_id column)."""
    out = {o.id: o.name for o in all_orgs()}
    out[None] = out.get(default_id(), "")
    return out


def name(org_id):
    org = get(key(org_id))
    return org.name if org is not None else ""


# --- who belongs where ---------------------------------------------------------------------------------------------

def valid(m):
    """A row of organisation_member in force (no end, or an end still to come)."""
    return m.valid_until is None or m.valid_until > utcnow()


def _valid():
    return db.or_(OrganisationMember.valid_until.is_(None), OrganisationMember.valid_until > utcnow())


def account_of(person, create=False):
    """The account a person is given by: a User, an account id, or a key (ORCID iD or "q-...", app/people_keys.py)."""
    if person is None or isinstance(person, User):
        return person
    if isinstance(person, int):
        return db.session.get(User, person)
    if not isinstance(person, str):
        return None
    from app import people_keys

    return people_keys.account(person, create=create)


def _uid(person):
    """The account id of a person given as User, id or key, or None."""
    if person is None or isinstance(person, int):
        return person
    if isinstance(person, str):
        user = account_of(person)
        return user.id if user is not None else None
    return getattr(person, "id", None)


def home_id_of(user_id):
    """The home organisation of the account with this id ("main" without a row is_home)."""
    cache = _cache().setdefault("home", {})
    if user_id not in cache:
        found = db.session.scalar(db.select(OrganisationMember.org_id).filter_by(user_id=user_id, is_home=True)) \
            if user_id else None
        cache[user_id] = found if found is not None else default_id()
    return cache[user_id]


def home_id_of_orcid(orcid):
    """Old form by key; use home_id(user) or home_id_of(user_id)."""
    return home_id_of(_uid(orcid))


def home_id(user):
    return home_id_of(_uid(user))


def ids_of_user_id(user_id):
    """The organisations of this account: rows of their own, the organisations of their groups, and "main" while it is
    their home. Read once per request."""
    cache = _cache().setdefault("ids", {})
    if user_id not in cache:
        found = set()
        if user_id:
            found |= set(db.session.scalars(db.select(OrganisationMember.org_id).where(
                OrganisationMember.user_id == user_id, _valid())))
            found |= set(db.session.scalars(db.select(column(WorkGroup.org_id)).join(WorkGroupMember)
                                            .where(WorkGroupMember.user_id == user_id)))
        found.add(home_id_of(user_id))
        found.discard(None)
        cache[user_id] = found
    return cache[user_id]


def ids_of_orcid(orcid):
    """Old form by key; use ids_of(user) or ids_of_user_id(user_id)."""
    return ids_of_user_id(_uid(orcid))


def ids_of(user):
    return ids_of_user_id(_uid(user))


def searchable_ids(user):
    """The organisations whose people this person finds in the search: their own, never the guests (A11, A5)."""
    return ids_of(user) - {guest_id()}


def affiliation_of(user):
    """What stands where the institution stood (Ralf 2026-10-16: institutions are no longer recorded, organisations
    replace them): the full name of the home organisation (else its short one); empty for guests and while the starting
    organisation still carries the platform's name."""
    if user is None:
        return ""
    org = get(home_id(user))
    if org is None or org.key == GUEST:
        return ""
    if org.key == MAIN:
        from app import brand

        if org.name == brand.NAME and not org.long_name:
            return ""
    return org.long_name or org.name  # the full name for the texts of surveys


def short_of(user):
    """The short name of the home organisation, for the platform's own pages (lists); empty for guests. Unlike
    affiliation_of also the starting organisation under the platform's name: an admin list showed "–" for people at
    home in "Askurious" (Ralf 2026-10-20)."""
    org = get(home_id(user)) if user is not None else None
    if org is None or org.key == GUEST:
        return ""
    return org.name


def display_name(orcid):
    """A name for an iD, also before its first sign-in (from a group, a survey or the sign-in list). An empty key names
    nobody: accounts with a password have none since migration 0085, and asking for it found any such account (the
    admin of CLS shown as another person, Ralf 2026-10-19); name_of(user) names an account."""
    from app.models import SignInOrcid, SurveyMember

    if not orcid:
        return ""
    user = db.session.scalar(db.select(User).filter_by(orcid=orcid))
    if user is not None and user.name:
        return user.name
    for model in (WorkGroupMember, SurveyMember) if user is not None else ():
        found = db.session.scalar(db.select(model.name).where(model.user_id == user.id, model.name != "").limit(1))
        if found:
            return found
    listed = db.session.get(SignInOrcid, orcid)
    return (listed.note if listed is not None and listed.note else "") or orcid


def name_of(user):
    """A name for an account, a pending one too (its name may come from a group, a survey or the sign-in list)."""
    if user is None:
        return ""
    return user.name or (display_name(user.orcid) if user.orcid else "") or user.display_name


# --- caps of an organisation (A16) -------------------------------------------------------------------------------------

def _home_org(person):
    return get(home_id_of(_uid(person)))


def kinds_cap_of(user):
    """The survey types the home organisation allows at most (stored form, as UserLogin.kinds) or None."""
    org = _home_org(user)
    return org.kinds_cap if org is not None and org.kinds_cap else None


def kinds_cap_of_orcid(orcid):
    """Old form by key; use kinds_cap_of(user)."""
    return kinds_cap_of(orcid)


def allowed_types(org):
    """The user types the admins of `org` give: its own (Ralf 2026-10-16: types per organisation)."""
    from app import account_types

    return account_types.all_types(hidden=False, org_id=org.id if org is not None else None)


def accounts_left(org):
    """How many more accounts may have their home here (None: no limit)."""
    if org is None or not org.max_accounts:
        return None
    return max(org.max_accounts - counts(org)["homes"], 0)


# --- admins of an organisation (stage 3) -----------------------------------------------------------------------------

def administered(user):
    """The organisations this person is an admin of (never the guests)."""
    if user is None or not getattr(user, "id", None):
        return []
    return list(db.session.scalars(db.select(Organisation).join(OrganisationMember).where(
        OrganisationMember.user_id == user.id, OrganisationMember.role == "admin", _valid(),
        db.or_(Organisation.key.is_(None), Organisation.key != GUEST)).order_by(db.func.lower(Organisation.name))))


def _site_admin(user):
    from flask import current_app

    return bool(getattr(user, "is_admin", False)) or getattr(user, "orcid", None) in current_app.config["ADMIN_ORCIDS"]


def personal_shares(uid):
    """What other people shared with the account `uid` personally, as (organisation, owner id) pairs: designs it may
    use or edit, linked elements and AInstein projects (audit 2026-10-22, B-01). A one-time link to the account hands
    these over with it, so they count for outside_roles and bulk_invites.beyond_course."""
    from app.models import Construct, ConstructShare, Design, DesignEditor, DesignShare, ResearchProject

    out = set()
    for share in (DesignShare, DesignEditor):
        out |= {(key(o), w) for o, w in db.session.execute(
            db.select(Design.org_id, Design.owner_id).join(share, share.design_id == Design.id).where(share.user_id == uid))}
    out |= {(key(o), w) for o, w in db.session.execute(
        db.select(Construct.org_id, Construct.created_by_id).join(ConstructShare, ConstructShare.construct_id == Construct.id)
        .where(ConstructShare.user_id == uid))}
    for o, w, shares in db.session.execute(db.select(ResearchProject.org_id, ResearchProject.user_id, ResearchProject.shares)
                                           .where(ResearchProject.shares.is_not(None), ResearchProject.user_id != uid)):
        if any(isinstance(x, dict) and x.get("kind") == "person" and x.get("user_id") == uid for x in shares or []):
            out.add((key(o), w))
    return out


def outside_roles(target, org):
    """Roles and memberships of the account beyond `org`, as a set of "org" (a row in another organisation: member or
    admin, never the guests, nor a plain membership of "main"), "group" (a group of another organisation), "survey"
    (an entry in a survey of another organisation) and "share" (a design, linked element or AInstein project of
    another organisation shared with it personally, audit 2026-10-22, B-01). Empty: everything the account holds lies
    within `org` (audit 2026-10-18, AUTHZ-01, AUTH-1)."""
    uid = _uid(target)
    if not uid or org is None:
        return set()
    found = set()
    others = db.select(OrganisationMember.id).where(
        OrganisationMember.user_id == uid, OrganisationMember.org_id != org.id, _valid())
    gid = guest_id()
    if gid is not None:
        others = others.where(OrganisationMember.org_id != gid)
    main_id = default_id()
    if main_id is not None:  # a plain membership of "main", where everyone started, is no tie (move_home keeps it)
        others = others.where(db.or_(OrganisationMember.org_id != main_id, OrganisationMember.role == "admin"))
    if db.session.scalar(others.limit(1)) is not None:
        found.add("org")
    if db.session.scalar(db.select(WorkGroupMember.id).join(WorkGroup).where(
            WorkGroupMember.user_id == uid, column(WorkGroup.org_id) != org.id).limit(1)) is not None:
        found.add("group")
    from app.models import SurveyMember

    if db.session.scalar(db.select(SurveyMember.id).join(Survey, Survey.id == SurveyMember.survey_id).where(
            SurveyMember.user_id == uid, column(Survey.org_id) != org.id).limit(1)) is not None:
        found.add("survey")
    if any(o != org.id for o, _owner in personal_shares(uid)):
        found.add("share")
    return found


def manages(admin, target, org):
    """May `admin` manage the account `target` as a whole in `org`: an admin of `org`, which is the target's home;
    never the account of a site admin, never one's own, never that of another admin of `org` (Ralf 2026-10-18: no
    admin takes rights from another by a user type, survey types, a period, a block or deletion; site admins do), and
    never an account with roles or memberships beyond `org` (another organisation, its groups or surveys): taking it
    over, blocking or deleting it would reach into that organisation (audit 2026-10-18, AUTHZ-01, AUTH-1). Such
    accounts are managed by a site admin; a site admin who is also an admin of `org` keeps that power here."""
    if target is None or _site_admin(target) or target.id == getattr(admin, "id", None):
        return False
    if not (is_admin_of(admin, org.id) and home_id(target) == org.id and not is_admin_of(target, org.id)):
        return False
    return _site_admin(admin) or not outside_roles(target, org)


def may_remove(admin, target, org):
    """May `admin` take `target` out of `org` (org_delete.remove): an admin of `org` (or a site admin), never a site
    admin's account, one's own or another admin's of `org`. Removing reaches nothing beyond `org`: the home moves to
    the person's other organisation, which stays as it is."""
    if target is None or _site_admin(target) or target.id == getattr(admin, "id", None):
        return False
    return (is_admin_of(admin, org.id) or _site_admin(admin)) and not is_admin_of(target, org.id)


def not_managed_said(target, org):
    """Why the account is not managed here, for the list of people."""
    if outside_roles(target, org) and home_id(target) == org.id:
        return de_en("Dieses Konto hat auch Rollen außerhalb der Organisation; es verwaltet ein Admin der Plattform.",
                     "This account also has roles beyond the organisation; a platform admin manages it.")
    return de_en("Dieses Konto verwalten Sie nicht.", "You do not manage this account.")


# --- the organisation new things go into (stage 4, A6) ----------------------------------------------------------------

def creatable(user):
    """The organisations this person may make things in: their own, not the guests."""
    mine = ids_of(user)
    return [o for o in all_orgs(guests=False) if o.id in mine]


def for_new(user):
    """The organisation a new survey, group or design goes into: the home (Ralf 2026-10-16: no choice on the start
    page; a survey can still be moved under "Personen")."""
    return home_id(user)


# --- invitations across the fence (A9, A10) -------------------------------------------------------------------------

def may_invite_across(survey, orcid):
    """May a person (a typed key, or the User) be invited to this survey in person: always within its organisation;
    across the fence only when the person's home lets them be invited in. A person without an account yet comes in as
    a guest. Whether the inviter may bring someone from outside at all is may_bring_in (the scope of the survey's
    organisation, which took over the former switch invite_out)."""
    if not orcid or not several():
        return True
    user = account_of(orcid)
    if user is None or user.pending:
        return True
    if of(survey) in ids_of(user):
        return True
    home = _home_org(user)
    return home is None or home.invite_in or home.key == GUEST


def not_across():
    return de_en("Diese Person gehört zu einer anderen Organisation, und Einladungen über die Organisation hinaus sind "
                 "dort oder hier ausgeschaltet. Ein Admin der Plattform kann das ändern.",
                 "This person belongs to another organisation, and invitations beyond the organisation are switched off "
                 "there or here. A platform admin can change that.")


# --- who brings people in from outside (konzept-organisation-sichtbarkeit-einladungen-2026-10-23.md, E10-E14) --------

# Who may bring in someone who is not yet in the organisation (into it, its groups or its surveys), strictest last:
# "create" whoever may create surveys (auth.may_admit, as before), "admins" only the organisation's admins,
# "platform" only site admins. Site admins set invite_scope; the organisation's admins may only make it stricter
# (invite_scope_own). The former switch invite_out off counts as "admins" at least.
SCOPES = ("create", "admins", "platform")


def scope_label(scope):
    return {"create": de_en("Alle, die Befragungen anlegen dürfen", "Everyone who may create surveys"),
            "admins": de_en("Nur Admins der Organisation", "Only admins of the organisation"),
            "platform": de_en("Nur Admins der Plattform", "Only platform admins")}[scope]


def _stricter(a, b):
    return a if SCOPES.index(a) >= SCOPES.index(b) else b


def platform_scope(org):
    """The scope site admins set (with the old switch invite_out read as "admins" when off)."""
    if org is None:
        return "create"
    scope = org.invite_scope if org.invite_scope in SCOPES else "create"
    return scope if org.invite_out else _stricter(scope, "admins")


def scope_of(org):
    """What holds: the stricter of what site admins and the organisation's admins chose."""
    scope = platform_scope(org)
    own = getattr(org, "invite_scope_own", None)
    return _stricter(scope, own) if org is not None and own in SCOPES else scope


def is_external(person, org_id):
    """Whether a person (a User, a typed key, or None for an address or a link nobody named) is outside the
    organisation: no account yet, a pending one, or an account that does not belong to it."""
    if person is None:
        return True
    user = account_of(person) if not isinstance(person, User) else person
    if user is None or user.pending:
        return True
    return key(org_id) not in ids_of(user)


def may_bring_in(user, org_id, person=None):
    """May `user` bring this person into the organisation, one of its groups or one of its surveys? People already
    in it always; from outside after the scope of the organisation. Site admins always."""
    if not is_external(person, org_id):
        return True
    from flask import current_app

    if user is not None and getattr(user, "orcid", None) in current_app.config.get("ADMIN_ORCIDS", ()):
        return True
    scope = scope_of(get(org_id))
    if scope == "create":
        return True
    return scope == "admins" and is_admin_of(user, org_id)


def open_from_outside(org_id):
    """The open join links of the organisation's groups and invitations by e-mail to its groups and surveys that people
    made who may no longer bring anyone in (they rest: joinlinks.paused, invites.authorised): [(kind, row)]."""
    from app.models import GroupJoinLink, MemberInvite

    org_id = key(org_id)
    now = utcnow()
    groups = db.select(WorkGroup.id).where(column(WorkGroup.org_id) == org_id)
    surveys = db.select(Survey.id).where(column(Survey.org_id) == org_id)
    rows = [("link", r) for r in db.session.scalars(db.select(GroupJoinLink).where(
        GroupJoinLink.group_id.in_(groups), GroupJoinLink.role == "member", GroupJoinLink.expires_at >= now,
        GroupJoinLink.uses < GroupJoinLink.max_uses))]
    rows += [("invite", r) for r in db.session.scalars(db.select(MemberInvite).where(
        db.or_(MemberInvite.group_id.in_(groups), MemberInvite.survey_id.in_(surveys)), MemberInvite.expires_at >= now))]
    makers = {r.created_by_id for _, r in rows}
    users = {u.id: u for u in db.session.scalars(db.select(User).where(User.id.in_(makers)))} if makers else {}
    return [(k, r) for k, r in rows if not may_bring_in(users.get(r.created_by_id), org_id)]


def not_brought_in(org_id=None):
    scope = scope_of(get(org_id)) if org_id is not None else "admins"
    if scope == "platform":
        return de_en("In dieser Organisation holen nur Admins der Plattform Personen von außen herein.",
                     "In this organisation only platform admins bring in people from outside.")
    return de_en("In dieser Organisation holen nur Admins der Organisation Personen von außen herein.",
                 "In this organisation only its admins bring in people from outside.")


def is_member(user, org_id):
    return key(org_id) in ids_of(user)


def is_admin_of(user, org_id):
    if user is None or not getattr(user, "id", None):
        return False
    return db.session.scalar(db.select(OrganisationMember.id).where(
        OrganisationMember.org_id == key(org_id), OrganisationMember.user_id == user.id,
        OrganisationMember.role == "admin", _valid())) is not None


def _homes(but=None):
    """SQL: the accounts with a row is_home (in another organisation than `but`)."""
    query = db.select(OrganisationMember.user_id).where(OrganisationMember.is_home.is_(True),
                                                        OrganisationMember.user_id.is_not(None))
    return query if but is None else query.where(OrganisationMember.org_id != but)


def user_ids_in(org_ids):
    """SQL: the account ids of everyone in these organisations (rows, group members, and those whose home is "main");
    pending accounts (added before their first sign-in) included, as their rows."""
    org_ids = list(org_ids)
    rows = db.select(OrganisationMember.user_id).where(OrganisationMember.org_id.in_(org_ids), _valid(),
                                                       OrganisationMember.user_id.is_not(None))
    in_groups = db.select(WorkGroupMember.user_id).join(WorkGroup).where(column(WorkGroup.org_id).in_(org_ids),
                                                                         WorkGroupMember.user_id.is_not(None))
    parts = [rows, in_groups]
    if default_id() in org_ids:
        parts.append(db.select(User.id).where(User.id.not_in(_homes()), User.pending.is_(False)))
    return parts[0].union(*parts[1:])


def orcids_in(org_ids):
    """Old form by key: SQL of the account keys of everyone in these organisations; use user_ids_in."""
    return db.select(User.orcid).where(User.id.in_(user_ids_in(org_ids)))


def members_of(org):
    """Everyone in the organisation, as users with an account: [(user, row or None)] by name."""
    rows = {m.user_id: m for m in org.members if m.user_id is not None}
    users = list(db.session.scalars(db.select(User).where(User.id.in_(user_ids_in([org.id])), User.pending.is_(False))
                                    .order_by(db.func.lower(User.name), User.id)))
    return [(u, rows.get(u.id)) for u in users]


def counts(org):
    """{"people", "invited", "homes", "groups", "surveys"} of an organisation, for the list of the administration.
    "people" are accounts, as in the lists behind it (members_of, "Personen ansehen"); "invited" are iDs without an
    account yet, e.g. a first admin or group members before their first sign-in (Ralf 2026-10-18: 5 against 0)."""
    is_main = org.key == MAIN
    ids = user_ids_in([org.id]).subquery()
    uid = list(ids.c)[0]
    known = db.select(User.id).where(User.pending.is_(False))  # a pending account counts as invited (B2)
    people = db.session.scalar(db.select(db.func.count()).select_from(ids).where(uid.in_(known))) or 0
    invited = db.session.scalar(db.select(db.func.count()).select_from(ids).where(uid.not_in(known))) or 0
    homes_here = db.session.scalar(db.select(db.func.count(OrganisationMember.id)).filter_by(org_id=org.id, is_home=True)
                                   .where(OrganisationMember.user_id.in_(known)))
    if is_main:
        homes_here = db.session.scalar(db.select(db.func.count(User.id)).where(User.id.not_in(_homes(but=org.id)),
                                                                               User.pending.is_(False)))
    groups = db.session.scalar(db.select(db.func.count(WorkGroup.id)).where(column(WorkGroup.org_id) == org.id))
    surveys = db.session.scalar(db.select(db.func.count(Survey.id)).where(column(Survey.org_id) == org.id))
    return {"people": people, "invited": invited, "homes": homes_here or 0, "groups": groups or 0,
            "surveys": surveys or 0}


def row_of(org_id, user_id):
    """The row of the account in the organisation, or None."""
    if not user_id:
        return None
    return db.session.scalar(db.select(OrganisationMember).filter_by(org_id=org_id, user_id=user_id))


def row(org_id, person):
    """Old form by key (a User works too); use row_of(org_id, user_id)."""
    return row_of(org_id, _uid(person))


def add(org, person, role="member", by=None, home=False):
    """Make the person a member (or admin) of the organisation; with home=True also its home. The person is a User
    (preferred) or a key; an ORCID iD without account gets a pending one (app/people_keys.py). Returns the row, or None
    for a key that names no account."""
    user = account_of(person, create=True)
    if user is None:
        return None
    if user.id is None:
        db.session.flush()  # a pending account made just now gets its id
    m = row_of(org.id, user.id)
    if m is None:
        m = OrganisationMember(org_id=org.id, user_id=user.id, role=role, added_by_id=by.id if by is not None else None)
        db.session.add(m)
    elif role == "admin":
        m.role = "admin"
    if home:
        set_home(user, org)
        m.is_home = True
    forget()
    return m


def set_home(person, org):
    """The home of the person (a User, preferred, or a key) moves to `org` (at most one row is_home per person)."""
    user = account_of(person, create=org.key != MAIN)
    if user is None:
        return
    for other in db.session.scalars(db.select(OrganisationMember).filter_by(user_id=user.id, is_home=True)):
        if other.org_id != org.id:
            other.is_home = False
    m = row_of(org.id, user.id)
    if m is None and org.key != MAIN:
        m = add(org, user)
    if m is not None:
        m.is_home = True
    forget()


def move_refusal(user, org):
    """Why the home of `user` cannot move to `org` on the account's page of the administration, or None."""
    from app.models import OrgDeletion

    if org is None or org.key == GUEST:
        return de_en("Diese Organisation gibt es nicht.", "This organisation does not exist.")
    if home_id(user) == org.id:
        return de_en("Das ist schon die Heimat der Person.", "This is already the person's home.")
    if db.session.get(OrgDeletion, user.id) is not None:
        return de_en("Das Konto ist von einer Organisation zum Löschen vorgemerkt; erst die Vormerkung zurücknehmen.",
                     "The account is marked for deletion by an organisation; take the mark back first.")
    if accounts_left(org) == 0:
        return de_en("{o} hat ihre Höchstzahl an Konten erreicht.", "{o} has reached its maximum number of accounts.",
                     o=org.name)
    return None


def move_home(user, org, by=None):
    """A site admin gives the account another home (Ralf 2026-10-18). The former home stays a membership, as when a
    group becomes an organisation (nobody loses a group, survey or share); a guest's row goes. A user type of another
    organisation becomes the one of the same name in `org`, else its default type; the period, the survey types and
    the right to create stay, within the caps of the new home (kind_access.home_cap). Returns the
    new user type or None. Does not commit."""
    from app import account_types, passwords

    old = get(home_id(user))
    if old is not None and old.key == GUEST:
        guest_row = row_of(old.id, user.id)
        if guest_row is not None:
            db.session.delete(guest_row)
    elif old is not None:
        add(old, user, by=by)
    set_home(user, org)
    login = passwords.login_row(user)
    t = account_types.get(login.account_type_id) if login is not None and login.account_type_id else None
    if t is not None and account_types.org_of(t) != org.id:
        mine = [x for x in account_types.all_types(org_id=org.id) if x.name_de == t.name_de]
        t = mine[0] if mine else account_types.default_type(org.id)
        login.account_type_id = t.id if t is not None else None
        if t is None or not t.supervised:
            account_types.set_supervisors(user, [])
    forget()
    from app import security_log

    security_log.event("organisation_home_moved", account=user.id, by=by.id if by is not None else None,
                       target=f"org {org.id}", reason=f"from org {old.id}" if old is not None else "")
    return t


# --- new things and new accounts -------------------------------------------------------------------------------------

def _current_user():
    if not has_request_context():
        return None
    from flask_login import current_user

    return current_user if getattr(current_user, "is_authenticated", False) else None


def place(thing, user=None):
    """Put a new survey, group, design, linked element or research project into the organisation of who makes it: the
    one chosen on the start page, else the home (A6, A12). "main" stays empty."""
    if getattr(thing, "org_id", None) is not None:
        return
    user = user or _current_user()
    if user is None or not getattr(user, "id", None):
        return
    target = for_new(user)
    if target is not None and target != default_id():
        thing.org_id = target


def _on_flush(session, context, instances):
    for thing in list(session.new):
        if isinstance(thing, OWNED):
            place(thing)
            if isinstance(thing, Survey) and thing.theme_row is None:
                start_design(thing)


def start_design(survey):
    """A new survey starts with the organisation design of its organisation, its light side (it stays light on a
    device set to dark, Ralf 2026-10-23), and with the organisation's logo (Ralf 2026-10-16; 2026-10-19: the organisation
    design is the standard, app/org_designs.py). Without an organisation design it starts with the standard look."""
    import secrets

    from app import org_designs

    new = org_designs.pair(get(of(survey)))
    if new is None:
        return
    if survey.uid is None:
        survey.uid = secrets.token_urlsafe(9)  # the folder the logo is copied into
    try:
        from app.surveys.design import take_org_logo

        if take_org_logo(survey, new):
            return
    except Exception:  # noqa: BLE001  (a missing logo file must never stop a survey from being made)
        pass
    survey.theme = new


def start_new(user):
    """The home of a new account (A21): what "Person hinzufügen" chose for its iD; else the organisation of the group it
    came in through (join link, invitation, course list) when that is not "main"; else "main" (no row)."""
    from app.models import SignInOrcid

    listed = db.session.get(SignInOrcid, user.orcid) if user.orcid else None
    target = get(listed.org_id) if listed is not None and listed.org_id is not None else None
    if target is None:
        group = db.session.scalar(db.select(WorkGroup).join(WorkGroupMember).where(
            WorkGroupMember.user_id == user.id, WorkGroup.org_id.is_not(None)).order_by(WorkGroupMember.id))
        target = get(group.org_id) if group is not None else None
    if target is None and _only_invited(user):
        target = get(guest_id())  # came in only by a personal invitation to a survey: a guest (A5)
    if target is not None and target.key != MAIN:
        add(target, user, home=True)


def _only_invited(user):
    """The new account came in by an invitation to a survey alone: no group, not let in by itself."""
    from app.auth import on_whitelist
    from app.models import SurveyMember

    if db.session.scalar(db.select(WorkGroupMember.id).filter_by(user_id=user.id).limit(1)) is not None:
        return False
    if db.session.scalar(db.select(SurveyMember.id).filter_by(user_id=user.id).limit(1)) is None:
        return False
    return not on_whitelist(user.orcid)


# --- the fence -------------------------------------------------------------------------------------------------------

def group_name_taken(name, org_id, without=None):
    """Whether the organisation has a group of this name already (Ralf 2026-10-16: unique within an organisation, the
    same name may stand in two organisations, apart). Case and spaces do not count."""
    wanted = " ".join((name or "").split()).lower()
    query = db.select(WorkGroup.name, WorkGroup.id).where(column(WorkGroup.org_id) == key(org_id))
    return any(" ".join(n.split()).lower() == wanted and gid != without for n, gid in db.session.execute(query))


def name_taken_said(name):
    return de_en("In dieser Organisation gibt es schon eine Gruppe „{name}“. Bitte einen anderen Namen wählen.",
                 "This organisation already has a group “{name}”. Please choose another name.", name=name)


def fenced_out():
    return de_en("Diese Gruppe gehört zu einer anderen Organisation; teilen lässt sich nur innerhalb derselben "
                 "Organisation.", "This group belongs to another organisation; sharing works only within the same "
                 "organisation.")


def may_join_group(orcid, group):
    """A lead adds a person (a typed key, or the User) directly: someone with an account in another organisation only
    by an admin of the group's organisation (A7). People without an account yet come in with the group's organisation
    as home; a join link or an invitation they accept themselves."""
    if not orcid or not several():
        return True
    user = account_of(orcid)
    if user is None or user.pending:
        return True
    return of(group) in ids_of(user)


def not_in_org():
    return de_en("Diese Person gehört nicht zur Organisation dieser Gruppe. Ein Admin der Organisation kann sie "
                 "aufnehmen; oder schicken Sie ihr einen Beitrittslink der Gruppe.",
                 "This person does not belong to this group's organisation. An admin of the organisation can take them "
                 "in; or send them a join link of the group.")


from sqlalchemy import event as _event  # noqa: E402
from sqlalchemy.orm import Session as _Session  # noqa: E402

_event.listen(_Session, "before_flush", _on_flush)


# --- a group becomes an organisation (A23, Ralf 2026-10-15) ---------------------------------------------------------

def plan(group):
    """What "Gruppe zur Organisation machen" would do, without doing it: who moves home (members whose home is the
    group's organisation), who only joins, which surveys (all their admins are members), designs, linked elements and
    research projects (made by those who move) come along, and which shares become personal entries so nobody loses
    access (a share may not cross the fence)."""
    from app import linked

    source = of(group)
    member_ids = [m.user_id for m in group.members if m.user_id is not None]
    mover_ids = [u for u in member_ids if home_id_of(u) == source]
    users = {u.id: u for u in db.session.scalars(db.select(User).where(User.id.in_(member_ids)))}
    from app.models import SurveyMember

    candidates = db.select(SurveyMember.survey_id).where(SurveyMember.user_id.in_(member_ids),
                                                         SurveyMember.role == "admin")
    surveys = []
    for s in db.session.scalars(db.select(Survey).where(column(Survey.org_id) == source, Survey.id.in_(candidates))
                                .order_by(Survey.title, Survey.id)):
        admins = s.admins()
        if admins and all(a.user_id in member_ids for a in admins):
            surveys.append(s)
    designs = list(db.session.scalars(db.select(Design).where(column(Design.org_id) == source,
                                                              Design.owner_id.in_(mover_ids)).order_by(Design.name)))
    elements = list(db.session.scalars(db.select(Construct).where(
        column(Construct.org_id) == source, Construct.created_by_id.in_(mover_ids),
        Construct.status == linked.STATUS).order_by(Construct.name)))
    projects = list(db.session.scalars(db.select(ResearchProject).where(
        column(ResearchProject.org_id) == source, ResearchProject.user_id.in_(mover_ids)).order_by(ResearchProject.name)))
    out = {"group": group, "source": source, "members": list(group.members), "users": users,
           "movers": set(mover_ids), "joiners": [u for u in member_ids if u not in mover_ids],
           "surveys": surveys, "designs": designs, "elements": elements, "projects": projects}
    out["shares"] = crossing(out, keep_group=True)
    return out


def crossing(p, keep_group):
    """The shares that would cross the fence afterwards: [(kind, thing, group, role)]. Organisations afterwards: the
    things that come along and (if kept) the group in the new one, everything else where it is."""
    group = p["group"]
    new = object()
    moved = ({("survey", s.id) for s in p["surveys"]} | {("design", d.id) for d in p["designs"]}
             | {("element", c.id) for c in p["elements"]} | {("project", r.id) for r in p["projects"]})

    def org_after(kind, thing):
        return new if (kind, thing.id) in moved else of(thing)

    def group_after(g):
        if g.id == group.id:
            return new if keep_group else None
        return of(g)

    found, seen = [], set()

    def check(kind, thing, g, role):
        if (kind, thing.id, g.id) not in seen:
            seen.add((kind, thing.id, g.id))
            if group_after(g) != org_after(kind, thing):
                found.append((kind, thing, g, role))

    for link in list(group.survey_links) + [link for s in p["surveys"] for link in s.group_links]:
        check("survey", link.survey, link.group, link.role)
    for share in list(group.design_shares) + [s for d in p["designs"] for s in d.group_shares]:
        check("design", share.design, share.group, None)
    for share in list(group.construct_shares) + [s for c in p["elements"] for s in c.group_shares]:
        check("element", db.session.get(Construct, share.construct_id), share.group, None)
    moving = {r.id for r in p["projects"]}
    for r in db.session.scalars(db.select(ResearchProject).where(ResearchProject.shares.is_not(None))):
        for share in r.shares if isinstance(r.shares, list) else []:
            if isinstance(share, dict) and share.get("kind") == "group":
                g = db.session.get(WorkGroup, share.get("group_id") or 0)
                if g is not None and (g.id == group.id or r.id in moving):
                    check("project", r, g, share.get("role"))
    return found


def _personal(kind, thing, g, role, by):
    """A share with group `g` becomes personal entries of its members, so nobody loses access. Returns how many."""
    from app.models import ConstructShare, DesignShare, SurveyMember

    n = 0
    for m in g.members:
        if m.user_id is None:
            continue  # the entry of a deleted account: nobody to keep access for
        if kind == "survey":
            if any(x.user_id == m.user_id for x in thing.members):
                continue
            thing.members.append(SurveyMember(user_id=m.user_id, name=m.display_name,
                                              invited_by_id=by.id if by is not None else None, position=len(thing.members),
                                              role=role if role in ("viewer", "editor") else "viewer"))
        elif kind == "design":
            if m.user_id == thing.owner_id or any(x.user_id == m.user_id for x in thing.shares):
                continue
            thing.shares.append(DesignShare(user_id=m.user_id))
        elif kind == "element":
            if m.user_id == thing.created_by_id or any(x.user_id == m.user_id for x in thing.person_shares):
                continue
            thing.person_shares.append(ConstructShare(user_id=m.user_id))
        else:
            shares = list(thing.shares or [])
            from app import people_keys

            if m.user_id == thing.user_id or any(
                    isinstance(s, dict) and s.get("kind") == "person" and people_keys.names(s, m.user) for s in shares):
                continue
            shares.append({"kind": "person", **people_keys.entry_for(m.user), "name": m.display_name,
                           "role": role or "view"})
            thing.shares = shares
        n += 1
    if kind == "survey":
        thing.group_links = [x for x in thing.group_links if x.group_id != g.id]
    elif kind in ("design", "element"):
        thing.group_shares = [x for x in thing.group_shares if x.group_id != g.id]
    else:
        thing.shares = [s for s in thing.shares or [] if not (isinstance(s, dict) and s.get("kind") == "group"
                                                               and s.get("group_id") == g.id)] or None
    return n


def name_taken(name, without=None):
    """Whether an organisation of this name exists already (case and spaces do not count): two organisations of one name
    could not be told apart in the lists and the badge of a person."""
    wanted = " ".join((name or "").split()).lower()
    return any(" ".join(o.name.split()).lower() == wanted and o.id != without for o in all_orgs())


def make(name, by, long_name="", description=""):
    """A new, empty organisation made by a site admin on the tab "Organisationen", without a group (Ralf 2026-10-18).
    Like make_from_group it starts with the platform's default type; its first admin comes with add(). Returns it."""
    org = Organisation(name=" ".join((name or "").split())[:MAX_NAME],
                       long_name=" ".join((long_name or "").split())[:200],
                       description=(description or "").strip()[:500], created_by_id=by.id if by is not None else None)
    db.session.add(org)
    db.session.flush()
    from app import account_types

    start = account_types.default_type(None)
    if start is not None:
        db.session.add(account_types._copy(start, org))
    forget()
    return org


def make_from_group(group, name, by, leads_admin=True, keep_group=True):
    """Make the group an organisation (A23), as plan() shows. Returns the new organisation."""
    p = plan(group)
    shares = crossing(p, keep_group)
    source = get(p["source"])
    org = Organisation(name=" ".join((name or "").split())[:MAX_NAME] or group.name,
                       description=group.description or "", from_group_name=group.name,
                       created_by_id=by.id if by is not None else None)
    db.session.add(org)
    db.session.flush()
    from app import account_types

    start = account_types.default_type(None)  # its first and only type: "Mitarbeitende" (Ralf 2026-10-16)
    if start is not None:
        db.session.add(account_types._copy(start, org))
    for m in list(group.members):
        if m.user is None:
            continue  # the entry of a deleted account
        add(org, m.user, role="admin" if leads_admin and m.role == "lead" else "member", by=by)
        if m.user_id in p["movers"]:
            if source is not None:
                add(source, m.user, by=by)  # stays a member where it was: nobody loses anything
            set_home(m.user, org)
    for kind, thing, g, role in shares:
        _personal(kind, thing, g, role, by)
    for thing in p["surveys"] + p["designs"] + p["elements"] + p["projects"]:
        thing.org_id = org.id
    if keep_group:
        group.org_id = org.id
    else:
        from app import groups as groups_mod

        groups_mod.remove(group)
    forget()
    from app import security_log

    security_log.event("organisation_from_group", by=by.id if by is not None else None, target=f"org {org.id}",
                       reason=f"surveys {len(p['surveys'])} shares {len(shares)}")
    return org


def delete(org):
    """An organisation goes only empty (nobody's home, no group, survey, design, linked element or project), and
    never "main". Returns an error text or None."""
    if org.key == MAIN:
        return de_en("Die Ausgangs-Organisation lässt sich nicht löschen.", "The starting organisation cannot be deleted.")
    c = counts(org)
    things = sum(db.session.scalar(db.select(db.func.count(model.id)).where(model.org_id == org.id)) or 0
                 for model in (Design, Construct, ResearchProject))
    if c["homes"] or c["groups"] or c["surveys"] or things:
        return de_en("Nur eine leere Organisation lässt sich löschen: ohne Personen mit dieser Heimat, ohne Gruppen, "
                     "Befragungen, Designs und Projekte.", "Only an empty organisation can be deleted: without people "
                     "whose home it is, without groups, surveys, designs and projects.")
    from app.models import ConstructShortlist

    # its "Schnellbox" list goes with it (app/shortlist.py; no database key, see migration 0074)
    db.session.execute(db.delete(ConstructShortlist).where(ConstructShortlist.org_id == org.id))
    from app.models import ExperimentGrant

    # its switches of experimental features too (app/experimental.py), also where the database does not cascade
    db.session.execute(db.delete(ExperimentGrant).where(ExperimentGrant.org_id == org.id))
    db.session.delete(org)
    forget()
    return None
