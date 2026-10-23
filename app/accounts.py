"""Accounts: the page "Konto", deleting an account (by the person or by a site admin) and blocking one.

Deleting removes the person, not the work of others. Surveys with further co-owners stay with them, groups
stay, and surveys that used a design keep their look. What belonged to this person alone goes: designs, drafts,
groups without another member. `plan` says beforehand what will happen and what must be settled first;
`delete` does exactly that, in one transaction.

SQLite does not enforce the foreign keys here, so every table that knows the person is handled explicitly.

Deleting is not a ban: who is still allowed to sign in comes back with an empty account. Keeping someone
out is "sperren" (UserLogin.status), which leaves everything as it is.
"""
import glob
import os
import re
import shutil
import time
from urllib.parse import urlsplit

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, logout_user

from app import audit, autosave
from app.lang import de_en
from app.extensions import db
from app.models import (LOCAL_PREFIX, AccountSupervisor, AiUsage, AuthToken, Construct, ConstructPick, Contact, DeletedAccount, OrganisationMember, ResearchProject, Design, DesignEditor,
                        ConstructCitation, ConstructShare, ConstructSubmission, DesignShare, EditMark, EditStep, ExperimentGrant, Feedback, Item, PreviewComment, Response, SignInGrant, SignInOrcid, SiteSetting, Survey,
                        SurveyGroup, SurveyHide, SurveyLabel, SurveyMember, SurveyPin, TrashedItem, User,
                        UserLogin, WorkGroup, WorkGroupMember, ZenodoConnection, CoscineConnection, AiAccess, AiCall, utcnow)
from app.models import LoginSession

bp = Blueprint("accounts", __name__, url_prefix="/account")

FRESH_SECONDS = 15 * 60  # deleting the own account needs a sign-in not older than this


# --- blocking -------------------------------------------------------------------------------------


def is_blocked(user):
    """Blocked by an admin, or its period ended (app/account_types.py), also before the hourly sweep marked it."""
    from app import account_types

    row = db.session.get(UserLogin, user.id)
    return row is not None and (row.status == UserLogin.BLOCKED or account_types.is_expired(row))


def blocked_said(user):
    """The message at a sign-in of a blocked account: an ended period names its end and whom to ask (N12)."""
    from app import account_types

    if account_types.expired_by_period(db.session.get(UserLogin, user.id)):
        return account_types.ended_said(user)
    from app import org_delete

    marked = org_delete.blocked_said(user)  # marked by an organisation admin: when it goes, whom to ask
    if marked:
        return marked
    return de_en("Dieses Konto ist gesperrt. Bitte wenden Sie sich an einen Admin der Plattform.",
                 "This account is blocked. Please contact a platform admin.")


def set_blocked(user, blocked, by, reason=None):
    """reason: "expired" when the period ended (account_types.expire_due, by None); an admin's block leaves it empty."""
    row = db.session.get(UserLogin, user.id)
    if row is None:
        row = UserLogin(user_id=user.id)
        db.session.add(row)
    row.status = UserLogin.BLOCKED if blocked else UserLogin.ACTIVE
    row.block_reason = reason if blocked else None
    row.status_changed_at, row.status_changed_by_id = utcnow(), by.id if by is not None else None
    if blocked:
        # every sign-in ends now, not only while the block lasts: unblocking must not bring a copied cookie back
        # to life (security audit 2026-10-06, S13)
        from app import sessions

        row.session_version = (row.session_version or 0) + 1
        sessions.end_all(user.id)
        from app import account_access

        account_access.revoke(user.id)


def local_account(key):
    """The account without ORCID that has this key, or None (also for ORCID iDs and unknown keys). Adding
    someone to a survey, a group or a design takes an ORCID iD or such a key of an existing account."""
    from app import people_keys

    if not key or not key.startswith((LOCAL_PREFIX, people_keys.ACCOUNT_PREFIX)):
        return None
    user = people_keys.account(key, create=False)
    return user if user is not None and not user.has_orcid else None


def email_free(email, user=None):
    owner = db.session.scalar(db.select(UserLogin.user_id).filter_by(email=email))
    return owner is None or (user is not None and owner == user.id)


def create_local(name, email, affiliation, may_create, by):
    """An account without ORCID, made by a site admin: that is the admin's yes to this person. It signs in with
    this address once the password is set over the one-time link (app/passwords.py). may_create: "create" for every
    type (kind_access.give_create); a caller that sets the survey types afterwards passes False and raises them
    itself."""
    from sqlalchemy.exc import IntegrityError

    from app.auth import new_account_ids

    for attempt in range(2):
        # no key any more: without ORCID the column stays empty (konto-schluessel-2b-vorschlag.md, E3 A)
        user = User(orcid=None, name=name, affiliation=affiliation, setup_due=True, **new_account_ids())
        db.session.add(user)
        try:
            db.session.flush()
            break
        except IntegrityError:
            # two accounts made at the same moment (two admins, or a first ORCID sign-in): id and number are the
            # highest so far plus one, and the other insert took them. Read again once, as auth._sign_in does
            # (audit 2026-10-05, L-13)
            db.session.rollback()
            if attempt:
                raise
    db.session.add(UserLogin(user_id=user.id, email=email, status_changed_at=utcnow(),
                             status_changed_by_id=by.id if by else None))
    if may_create:
        from app import kind_access

        kind_access.give_create(user, by, log=False)
    return user


def _rekey(doc, old, new):
    """`doc` (JSON) with `new` in place of the key `old` in every list named "researchers" (the people a consent
    names, by key), or without it (new None). Returns (the document, whether it held the key)."""
    if isinstance(doc, list):
        rows = [_rekey(v, old, new) for v in doc]
        return [v for v, _ in rows], any(found for _, found in rows)
    if not isinstance(doc, dict):
        return doc, False
    out, found = {}, False
    for name, value in doc.items():
        if name == "researchers" and isinstance(value, list) and old in value:
            out[name], found = list(dict.fromkeys(new if o == old else o for o in value if new or o != old)), True
        else:
            out[name], inner = _rekey(value, old, new)
            found = found or inner
    return out, found


def _rekey_stored(old, new=None):
    """A consent names its researchers by key, and a consent is also kept where no author list reaches it: in the
    trash (TrashedItem.data) and in the steps other people can take back (EditStep.data), for days. Put `new`
    in place of `old` there and in the consents themselves, or take the key out (new None: the account goes)."""
    if not old:
        return  # an account made without a key (since 2b) is named by "u<id>" only, which stays
    for model, column in ((Item, Item.config), (TrashedItem, TrashedItem.data), (EditStep, EditStep.data)):
        rows = db.select(model).filter(db.cast(column, db.Text).contains(old, autoescape=True))
        for row in db.session.scalars(rows.filter(Item.type == "consent") if model is Item else rows):
            doc, found = _rekey(getattr(row, column.key), old, new)
            if found:
                setattr(row, column.key, doc)


def _rekey_projects(old_id, new_id=None, owner_id=None):
    """Person shares and comments of AInstein projects (ResearchProject.shares, comments) of the account `old_id`: with
    `new_id` (a pending account taken over by connecting its iD) they move to that account; a share already there keeps
    the higher role, and nothing stays shared with the projects of `owner_id` (the account itself). Without `new_id`
    (the account goes) the shares end and the comments keep their text without name, as PreviewComment."""
    from app.models import ResearchProject

    if not old_id:
        return
    found = [db.cast(column, db.Text).contains(f'"user_id": {old_id}', autoescape=True)
             for column in (ResearchProject.shares, ResearchProject.comments)]
    found += [db.cast(column, db.Text).contains(f'"user_id":{old_id}', autoescape=True)
              for column in (ResearchProject.shares, ResearchProject.comments)]
    for project in db.session.scalars(db.select(ResearchProject).filter(db.or_(*found))):
        shares, changed = [], False
        for share in project.shares or []:
            if share.get("kind") == "person" and share.get("user_id") == old_id:
                changed = True
                if new_id is None or project.user_id == owner_id:
                    continue
                share = {**share, "user_id": new_id}
            shares.append(share)
        if changed:
            merged = {}  # one share per person: the higher role wins
            for share in shares:
                if share.get("kind") != "person":
                    continue
                first = merged.setdefault(share.get("user_id"), share)
                if share is not first and share.get("role") == "edit":
                    first["role"] = "edit"
            project.shares = [x for x in shares if x.get("kind") != "person" or merged.get(x.get("user_id")) is x] or None
        comments = project.comments or []
        if any(c.get("user_id") == old_id for c in comments):
            project.comments = [({**c, "user_id": new_id} if new_id else {**c, "user_id": None, "name": ""})
                                if c.get("user_id") == old_id else c for c in comments]


def link_orcid(user, orcid, name=""):
    """Connect an ORCID iD to an account without one: the iD replaces the account's internal key wherever a
    person is known by key. Where the iD was already entered (someone added it to a survey or a group before
    this), the two entries become one, with the higher role. The caller made sure that no account has the iD,
    and commits. The account keeps its sign-in with e-mail and password, and may sign in as before."""
    from app import people_keys

    if name:
        user.name = name[:200]
    # the account kept for the iD since someone added it (B2): its rows are the entries made before this
    pending = db.session.scalar(db.select(User).filter_by(orcid=orcid, pending=True))
    pid = pending.id if pending is not None else None

    def merge(model, parent, better):
        """Entries of the iD (made before its first sign-in) and of the account: one per survey or group."""
        mine = {getattr(m, parent): m for m in db.session.scalars(db.select(model).filter_by(user_id=user.id))}
        for invited in list(db.session.scalars(db.select(model).filter_by(user_id=pid))) if pid else []:
            own = mine.get(getattr(invited, parent))
            if own is None:
                invited.user_id = user.id
            else:
                if better(invited.role, own.role):
                    own.role = invited.role
                db.session.delete(invited)
        db.session.flush()
        if hasattr(model, "name"):
            for m in mine.values():
                m.name = user.name

    merge(SurveyMember, "survey_id", lambda a, b: SurveyMember.LEVEL[a] > SurveyMember.LEVEL[b])
    merge(WorkGroupMember, "group_id", lambda a, b: a == "lead" and b != "lead")
    # organisations too (Ralf 2026-10-18: the row of the old key stayed, counted, in no list); the account keeps its
    # home, a home the iD was given before only counts when the account had none (at most one per person)
    had_home = db.session.scalar(db.select(OrganisationMember.id).filter_by(user_id=user.id, is_home=True)) is not None
    for invited in db.session.scalars(db.select(OrganisationMember).filter_by(user_id=pid, is_home=True)) if pid else []:
        if had_home:
            invited.is_home = False
        else:  # the home given to the iD survives when the account's row in that organisation is the one kept
            own = db.session.scalar(db.select(OrganisationMember).filter_by(user_id=user.id, org_id=invited.org_id))
            if own is not None:
                own.is_home = True
    merge(OrganisationMember, "org_id", lambda a, b: a == "admin" and b != "admin")
    db.session.flush()
    db.session.expire_all()
    for m in db.session.scalars(db.select(SurveyMember).filter_by(user_id=user.id)):
        for n, other in enumerate(m.survey.members):
            other.position = n

    # shares of designs and entries in address books: the iD's own entry (of the pending account) wins
    def of_id(model):
        return model.user_id == pid if pid else db.false()

    for model, parent in ((DesignShare, "design_id"), (DesignEditor, "design_id"), (ConstructShare, "construct_id"),
                          (Contact, "owner_id")):
        taken = {getattr(r, parent) for r in db.session.scalars(db.select(model).where(of_id(model)))}
        for row in list(db.session.scalars(db.select(model).filter_by(user_id=user.id))):
            if getattr(row, parent) in taken:
                db.session.delete(row)
        db.session.flush()
        db.session.execute(db.update(model).where(of_id(model)).values(user_id=user.id)
                           .execution_options(synchronize_session=False))
    db.session.flush()
    # nothing is shared with oneself: what others' entries of the iD pointed at may be the account's own
    own_designs = db.select(Design.id).filter_by(owner_id=user.id)
    for model in (DesignShare, DesignEditor):
        db.session.execute(db.delete(model).where(model.user_id == user.id, model.design_id.in_(own_designs)))
    db.session.execute(db.delete(Contact).where(Contact.user_id == user.id, Contact.owner_id == user.id))
    if pid:  # consents and AInstein projects that named the pending account name this account now
        _rekey_stored(f"u{pid}", f"u{user.id}")
        _rekey_projects(pid, user.id, user.id)
    # what is left of the pending account goes to this account, then it goes
    people_keys.take_over(user, pending)
    db.session.flush()
    user.orcid = orcid
    if db.session.get(SignInGrant, user.id) is None:
        # the yes is that of the site admin who made the account or last set its status (create_local)
        login = db.session.get(UserLogin, user.id)
        db.session.add(SignInGrant(user_id=user.id, granted_by_id=login.status_changed_by_id if login else None))
    db.session.flush()
    db.session.expire_all()


def unlink_orcid(user):
    """Disconnect the ORCID iD (konto-schluessel-plan.md B5): the account keeps everything and signs in with e-mail
    and password only; the key columns and the keys stored in consents and AInstein projects take a new internal key.
    The caller made sure a password is set and commits."""
    from app import people_keys

    old, new = user.orcid, None  # without ORCID the column stays empty (2b, E3 A)
    del old  # consents and AInstein projects name the account by its number: nothing to change there
    user.orcid = new
    user.is_admin = False  # site admins come from ADMIN_ORCIDS, by iD (the caller refuses them)
    db.session.flush()
    db.session.expire_all()


def managed_by_config(user):
    """Site admins come from ADMIN_ORCIDS in the server's configuration: no other admin blocks or deletes them."""
    return user.orcid in current_app.config["ADMIN_ORCIDS"]


# --- what deleting would do -------------------------------------------------------------------------


def _real_count(survey):
    return db.session.scalar(db.select(db.func.count(Response.id)).filter(
        Response.survey_id == survey.id, Response.source == "real", Response.not_live_extra()))


def plan(user):
    """Everything deleting this account touches, sorted by what happens to it.

    Surveys (each entry: survey, member = the person's author entry, real = number of real participations):
      leave      another admin exists: the person leaves
      successor  further co-owners, but no other admin: one of `candidates` becomes admin
      drafts     nobody else, no real participation, not running: deleted
      data       nobody else, real participations, not running: deleted (own account) or taken over (admin)
      running    nobody else, running (with or without participations): must be ended first (own account) or taken over
    In surveys with real participations that stay, the name stays as an author entry without account.
    Candidates are the co-owners whose accounts can take over (S20: signed in before, not blocked); only if none
    can, all of them, and the entry says so (`unproven`).
    Groups: group_leave, group_successor (the only lead, with `candidates`), group_delete (the only member).
    linked: the linked elements the person made, each {"element", "left": uses outside the drafts that go,
    "left_all": also outside the surveys with data (which go when the person deletes the own account)}; those
    without a use left go too, the others stay without maker. live_contact: live votes that stay and name the
    e-mail of this account on their privacy page (no address of their own); live_contact_taken: the same among
    those a site admin takes over.
    """
    p = {"leave": [], "successor": [], "drafts": [], "data": [], "running": [],
         "group_leave": [], "group_successor": [], "group_delete": []}
    rows = db.session.scalars(db.select(SurveyMember).filter_by(user_id=user.id)).all()
    for m in sorted(rows, key=lambda m: (m.survey.title.lower(), m.survey_id)):
        s = m.survey
        others = [o for o in s.members if o is not m and not o.is_former]
        entry = {"survey": s, "member": m, "real": _real_count(s)}
        if not others:
            # a running survey never goes mid-field, also without a stored participation yet: people may be reading
            # its first page (nothing is stored before the first submit), and declines are only counted
            key = "running" if s.status == Survey.RUNNING else "data" if entry["real"] else "drafts"
        elif m.role != "admin" or any(o.role == "admin" for o in others):
            key = "leave"
        else:
            able = [o for o in others if _can_take_over(o)]
            key, entry["candidates"], entry["unproven"] = "successor", able or others, not able
        p[key].append(entry)
    rows = db.session.scalars(db.select(WorkGroupMember).filter_by(user_id=user.id)).all()
    for m in sorted(rows, key=lambda m: (m.group.name.lower(), m.group_id)):
        others = [o for o in m.group.members if o is not m]
        entry = {"group": m.group, "member": m}
        if not others:
            key = "group_delete"
        elif m.role != "lead" or any(o.role == "lead" for o in others):
            key = "group_leave"
        else:  # longest in the group first; accounts that can take over first of all (S20)
            key, entry["candidates"] = "group_successor", sorted(others, key=lambda o: (not _can_take_over(o), o.id))
        p[key].append(entry)
    p["designs"] = list(db.session.scalars(db.select(Design).filter_by(owner_id=user.id).order_by(Design.name)))
    p["designs_shared"] = sum(1 for d in p["designs"] if d.shares or d.group_shares)
    p["zenodo"] = list(db.session.scalars(db.select(ZenodoConnection).filter_by(user_id=user.id)))
    p["coscine"] = db.session.scalar(db.select(CoscineConnection).filter_by(user_id=user.id)) is not None
    p["ai_access"] = db.session.get(AiAccess, user.id) is not None
    p["contacts"] = db.session.scalar(db.select(db.func.count(Contact.id)).filter_by(owner_id=user.id))
    p["data_total"] = sum(e["real"] for e in p["data"])
    from app import linked, live

    drafts = {e["survey"].id for e in p["drafts"]}
    data = {e["survey"].id for e in p["data"]}
    p["linked"] = []
    for c in db.session.scalars(db.select(Construct).filter_by(created_by_id=user.id, status=linked.STATUS)
                                .order_by(Construct.name)):
        ids = [s.id for _, s in linked.uses(c)]
        p["linked"].append({"element": c, "left": sum(1 for i in ids if i not in drafts),
                            "left_all": sum(1 for i in ids if i not in drafts and i not in data)})
    login = db.session.get(UserLogin, user.id)

    def contact(entries):
        return [e["survey"] for e in entries if login is not None and login.email and e["survey"].live is not None
                and e["survey"].created_by_id == user.id and not live.state(e["survey"]).get("contact_email")]

    p["live_contact"], p["live_contact_taken"] = contact(p["leave"] + p["successor"]), contact(p["data"] + p["running"])
    return p


def _can_take_over(member):
    """An account that can be the next admin (or lead): signed in at least once and neither blocked nor waiting for
    approval (S20 of the audit 2026-10-01). Entries of people who never signed in have a pending account only."""
    user = member.account
    if user is None or user.last_login_at is None:
        return False
    login = db.session.get(UserLogin, user.id)
    return login is None or login.status == UserLogin.ACTIVE


# --- deleting -----------------------------------------------------------------------------------------


class DeleteError(Exception):
    pass


def leave_survey(survey, m):
    """An author entry leaves the survey (removed by an admin or leaving): the same rule as deleting the account,
    so the consent participants gave keeps naming the person (decisions.md)."""
    _leave_survey({"survey": survey, "member": m, "real": survey.has_real_responses()}, m.user)


def _leave_survey(entry, user):
    """The person's author entry: removed, or (survey with real participations) kept as a name without account,
    because participants consented under this name. The consent's list of responsible researchers follows. An
    automatic upload to Coscine that runs with the person's token goes off (audit 2026-10-05, L-9)."""
    survey, m = entry["survey"], entry["member"]
    if user is not None:
        from app import invites

        invites.revoke_by(user.id, survey_id=survey.id)
    if user is not None:
        from app import coscine

        coscine.stop_auto(survey, user.id)
    if not entry["real"]:
        survey.members.remove(m)
        for n, other in enumerate(survey.members):
            other.position = n
        return
    # the consent names the account's key (rule of this stage: JSON keeps account keys); the entry gets a key "x-…"
    ref = f"u{m.user_id}" if m.user_id else None  # items/consent.member_ref; the entry is "m<id>" from now on
    m.user_id, m.role, m.name = None, "viewer", (user and user.name) or m.name
    from app import orgs

    m.affiliation = m.affiliation or orgs.affiliation_of(user) or ""
    for item in (i for b in survey.blocks for i in b.items if i.type == "consent"):
        chosen = (item.config or {}).get("researchers") or []
        if ref and ref in chosen:
            item.config = {**item.config, "researchers": list(dict.fromkeys(f"m{m.id}" if o == ref else o for o in chosen))}


def _chosen(candidates, value):
    """The candidate with this id, else the first one (the proposal the page shows)."""
    return next((c for c in candidates if str(c.id) == str(value)), candidates[0])


def delete(user, by, choices=None, admin=None, heirs=None):
    """Delete the account. by: "self" | "admin" | "org". choices (form values):
      survey_<id>   id of the co-owner who becomes admin        (successor; default: next in author order)
      group_<id>    id of the member who becomes lead, or "delete" (group_successor; default: longest member)
      data_<id>     "delete" instead of taking the survey over   (admin only, not for running surveys)
      confirm_data  "1": surveys with real participations go too (own account, if there are any)
    admin: the site admin who deletes (takes over surveys that belong to nobody else).
    heirs (by "org", app/org_delete.py): {("s", survey id) | ("d", design id): User who takes it over, or None to
    delete}; what belongs to the person alone and is not named goes. Commits."""
    from app import designs, groups, lifecycle
    from app.media import upload_folder

    choices = choices or {}
    p = plan(user)
    if by == "self":
        if p["running"]:
            raise DeleteError(de_en("Bitte beenden Sie zuerst die laufenden Befragungen, die nur Ihnen gehören.",
                                  "Please first end the running surveys that belong to you alone."))
        if p["data"] and choices.get("confirm_data") != "1":
            raise DeleteError(de_en("Bitte bestätigen Sie, dass die Befragungen mit echten Teilnahmen gelöscht werden "
                                  "sollen.", "Please confirm that the surveys with real participations are to be "
                                  "deleted."))
    uid, key = user.id, user.orcid

    # Comments from test runs keep who wrote and who handled them as a name (PreviewComment). In the surveys of
    # others the name goes with the account: the lists and comments.csv then show "Testperson" and "?". By the
    # account (author_id, handled_by_id), whatever its name was then; rows from before those columns by the
    # current name: names are not unique, so only in surveys this person could open (looked up before the author
    # entries go), and only comments from the editor preview (on the phone link anyone types any name).
    from app import access

    db.session.execute(db.update(PreviewComment).where(PreviewComment.author_id == uid)
                       .values(author="", author_id=None).execution_options(synchronize_session=False))
    db.session.execute(db.update(PreviewComment).where(PreviewComment.handled_by_id == uid)
                       .values(handled_by="", handled_by_id=None).execution_options(synchronize_session=False))
    name, could_open = user.name, PreviewComment.survey_id.in_(access.survey_ids(user, every_kind=True))
    if name:  # an account without a name wrote as "ORCID user", like every other one without
        db.session.execute(db.update(PreviewComment).where(could_open, PreviewComment.via == "editor",
                                                           PreviewComment.author_id.is_(None),
                                                           PreviewComment.author == name.strip()[:120])
                           .values(author="").execution_options(synchronize_session=False))
        db.session.execute(db.update(PreviewComment).where(could_open, PreviewComment.handled_by_id.is_(None),
                                                           PreviewComment.handled_by == name[:120])
                           .values(handled_by="").execution_options(synchronize_session=False))

    # an automatic upload to Coscine with the person's token goes off with its name, also in surveys the person had
    # left before that rule (L-11); the names in the lists of uploads and versions stay, like the author list
    from app import coscine

    for survey in db.session.scalars(db.select(Survey).filter(Survey.coscine.is_not(None))):
        coscine.stop_auto(survey, uid)

    # interviews of an interview guide (app/interview_data.py) keep who held them as a name, not as an account
    # only the interviews this person held, not every interview of the platform (code review 2026-10-15, H10)
    for row in db.session.scalars(db.select(Response).filter(Response.interview.is_not(None),
                                                             Response.interview["by"].as_integer() == uid)):
        if (row.interview or {}).get("by") == uid:
            row.interview = {**row.interview, "by": None}

    for entry in p["leave"]:
        _leave_survey(entry, user)
    for entry in p["successor"]:
        _chosen(entry["candidates"], choices.get(f"survey_{entry['survey'].id}")).role = "admin"
        _leave_survey(entry, user)
    heirs = heirs or {}
    for entry in p["data"] + p["running"] + (p["drafts"] if by == "org" else []):
        survey = entry["survey"]
        heir = heirs.get(("s", survey.id)) if by == "org" else None
        if heir is not None:  # an organisation admin chose who takes it over (never himself: he sees no answers)
            mine = survey.member_of(heir)
            if mine is None:
                mine = SurveyMember(user=heir, name=heir.name, affiliation=_org_of(heir),
                                    position=len(survey.members))
                survey.members.append(mine)
            mine.role = "admin"
            _leave_survey(entry, user)
        elif by == "admin" and not (survey.status != Survey.RUNNING and choices.get(f"data_{survey.id}") == "delete"):
            # nobody deletes research data of others in passing: the admin takes the survey over
            mine = survey.member_of(admin)
            if mine is None:
                mine = SurveyMember(user=admin, name=admin.name, affiliation=_org_of(admin),
                                    position=len(survey.members))
                survey.members.append(mine)
            mine.role = "admin"
            if survey.status != Survey.RUNNING:
                survey.archived = True
            _leave_survey(entry, user)
        else:
            lifecycle.delete(survey, force=True, hide_title=True)  # nor its title with a seal chain (S7)
    for entry in p["drafts"] if by != "org" else []:
        lifecycle.delete(entry["survey"], force=True, hide_title=True)
    from app import linked

    db.session.flush()
    for entry in p["linked"]:  # an element no survey uses any more would belong to nobody and reach nobody
        if not linked.uses(entry["element"]):
            linked.dissolve(entry["element"])

    folders = []
    for design in p["designs"]:  # surveys that used a design keep their copy
        if heirs.get(("d", design.id)) is not None:  # passes on with its files (app/org_delete.py)
            design.owner_id = heirs[("d", design.id)].id
            continue
        folders.append(upload_folder(designs.folder_uid(design)))
        db.session.delete(design)
    for entry in p["group_delete"]:
        groups.remove(entry["group"])
    for entry in p["group_successor"]:
        choice = choices.get(f"group_{entry['group'].id}")
        if choice == "delete":
            groups.remove(entry["group"])
            continue
        _chosen(entry["candidates"], choice).role = "lead"
        groups.end_shares_of_user(entry["group"], user)  # as when leaving the group
        entry["group"].members.remove(entry["member"])
    for entry in p["group_leave"]:
        groups.end_shares_of_user(entry["group"], user)
        entry["group"].members.remove(entry["member"])
    db.session.flush()
    # the key in consents of surveys that stay (an author entry that was removed), in their trash and in the steps
    # of others; where the entry stays as a name without account, _leave_survey gave the consent its new key
    _rekey_stored(f"u{uid}")  # the reference by account (items/consent.member_ref)
    _rekey_projects(uid)  # AInstein projects of others shared with the person; their comments there lose the name

    # everything else that knows the person
    db.session.execute(db.delete(DesignShare).where(DesignShare.user_id == uid))
    db.session.execute(db.delete(DesignEditor).where(DesignEditor.user_id == uid))
    db.session.execute(db.delete(ConstructShare).where(ConstructShare.user_id == uid))  # linked elements shared with them
    db.session.execute(db.delete(Contact).where(Contact.user_id == uid))  # the cached name in address books of others
    # supervising and supervised (several since 2026-10-18; the foreign keys cascade only where the database enforces them)
    db.session.execute(db.delete(AccountSupervisor).where(db.or_(AccountSupervisor.user_id == uid,
                                                                 AccountSupervisor.supervisor_id == uid)))
    # memberships and the home in organisations: the iD went on standing there and was counted (Ralf 2026-10-18)
    db.session.execute(db.delete(OrganisationMember).where(OrganisationMember.user_id == uid))
    from app.models import OrgAlias

    db.session.execute(db.delete(OrgAlias).where(OrgAlias.user_id == uid))  # the number of the alias (app/names.py)
    from app.models import AgentToken, BlogPost, DataToken, InterviewerAccess, MailOutbox, MemberInvite, OrgDeletion

    # mails it queued and that wait still go with it, address and text (audit 2026-10-09b, E10); sent rows keep no
    # address and only lose the sender
    db.session.execute(db.delete(MailOutbox).where(MailOutbox.sender_id == uid, MailOutbox.sent_at.is_(None)))
    db.session.execute(db.update(MailOutbox).where(MailOutbox.sender_id == uid).values(sender_id=None))
    # invitations by e-mail the person sent, and those waiting for the address they signed in with (app/invites.py)
    login = db.session.get(UserLogin, uid)
    db.session.execute(db.delete(MemberInvite).where(MemberInvite.created_by_id == uid))
    from app.models import GroupJoinLink

    db.session.execute(db.delete(GroupJoinLink).where(GroupJoinLink.created_by_id == uid))  # join links it made
    from app.models import SignInWait
    from app.models import PaperPage, PaperRun, PaperSheet

    db.session.execute(db.delete(SignInWait).where(SignInWait.orcid == key))  # the waiting list (app/signin.py)
    if login is not None and login.email:
        db.session.execute(db.delete(MemberInvite).where(MemberInvite.email == login.email))
    for model in (ZenodoConnection, CoscineConnection, AiAccess, AiUsage, SurveyPin, SurveyHide, EditMark, EditStep, UserLogin, AuthToken, SignInGrant, LoginSession,
                  ConstructPick, ResearchProject, ExperimentGrant, DataToken,  # DataToken: read tokens (app/data_access.py)
                  AgentToken,  # tokens of the AI agent interface (app/agent_access.py)
                  OrgDeletion):  # a mark of an organisation admin (app/org_delete.py)
        db.session.execute(db.delete(model).where(model.user_id == uid))
    for column in (Feedback.user_id, Survey.created_by_id, SurveyMember.invited_by_id, SurveyMember.user_id,
                   WorkGroup.created_by_id, WorkGroupMember.invited_by_id, WorkGroupMember.user_id,
                   SurveyGroup.added_by_id, SurveyLabel.created_by_id,
                   Construct.created_by_id, UserLogin.status_changed_by_id, AuthToken.created_by_id,
                   SignInGrant.granted_by_id, ConstructSubmission.user_id, ConstructCitation.added_by_id,
                   ExperimentGrant.created_by_id, SignInOrcid.added_by_id, SiteSetting.changed_by_id,
                   BlogPost.created_by_id, BlogPost.updated_by_id, AiCall.user_id,  # AiCall: "KI-Nutzung" (E6)
                   PaperRun.created_by, PaperSheet.checked_by, PaperPage.uploaded_by,  # paper questionnaires
                   InterviewerAccess.created_by_id, OrgDeletion.requested_by_id):  # interviewer links stay with the guide (app/interviewer.py)
        db.session.execute(db.update(column.class_).where(column == uid).values({column.key: None}))
    db.session.add(DeletedAccount(user_id=uid, number=user.number, by=by))
    db.session.expire_all()
    db.session.delete(db.session.get(User, uid))  # own contacts go with it
    db.session.commit()

    for folder in folders:
        shutil.rmtree(folder, ignore_errors=True)
    for path in glob.glob(os.path.join(current_app.config["UPLOAD_FOLDER"], "progress", f"{uid}_*.json")):
        try:
            os.remove(path)
        except OSError:
            pass
    from app import transfer

    transfer.sweep_pending(user_id=uid)  # a survey file put aside for an import
    from app import panel

    panel.sweep_pastes(user_id=uid)  # a pasted panel link list


# --- pages ------------------------------------------------------------------------------------------------


def _fresh():
    return time.time() - session.get("signed_in_at", 0) <= FRESH_SECONDS


def need_fresh(back):
    """None if this browser signed in within FRESH_SECONDS; else the way to sign in again, back to `back` (a path
    of this site) afterwards. For actions that hand out or remove everything at once (security audit 2026-10-04,
    N3): a computer left signed in must not be enough for them. What the form held is kept for the way back
    (keep_entries), so nothing has to be typed twice."""
    if _fresh():
        return None
    from flask import g

    g.audit_skip = True  # nothing was done: no entry in the access log
    from app import autosave

    if autosave.wanted():  # a form that saves by itself cannot follow the way to the sign-in: it says "not saved"
        flash(de_en("Nicht gespeichert: Die letzte Anmeldung ist älter als {n} Minuten. Bitte die Seite neu laden, neu "
                    "anmelden und die Änderung wiederholen.",
                    "Not saved: the last sign-in is older than {n} minutes. Please reload the page, sign in again and "
                    "repeat the change.", n=FRESH_SECONDS // 60), "error")
        return redirect(back)
    if keep_entries(back):
        flash(de_en("Für diese Aktion bitte neu anmelden: Die letzte Anmeldung ist älter als {n} Minuten. "
                    "Danach geht es hier weiter, Ihre Eingaben stehen wieder im Formular; die Aktion bitte noch einmal "
                    "auslösen.",
                    "Please sign in again for this action: the last sign-in is older than {n} minutes. "
                    "You will come back here afterwards with your entries filled in again; please start the action "
                    "once more.", n=FRESH_SECONDS // 60), "warning")
    else:
        flash(de_en("Für diese Aktion bitte neu anmelden: Die letzte Anmeldung ist älter als {n} Minuten. "
                    "Danach geht es hier weiter; die Aktion bitte noch einmal auslösen.",
                    "Please sign in again for this action: the last sign-in is older than {n} minutes. "
                    "You will come back here afterwards; please start the action once more.", n=FRESH_SECONDS // 60),
              "warning")
    if current_user.has_orcid:
        return redirect(url_for("auth.login", fresh=1, next=back))
    return redirect(url_for("auth.sign_in", next=back))


REFILL_KEY = "fresh_refill"  # in the session, encrypted: the entries of a form sent without a fresh sign-in
REFILL_MAX = 2000           # characters of the kept entries; a longer form is not kept (the session is a cookie)
_NOT_KEPT = re.compile(r"csrf|password|passwort|code|token|secret|key", re.I)  # never in the cookie, never refilled


def keep_entries(back):
    """need_fresh sends to the sign-in: keep what the sent form held, encrypted in this browser's session, for
    the page `back` the sign-in returns to (refill). Passwords, codes and tokens are left out. True if kept."""
    import json

    from app import secrets_box

    session.pop(REFILL_KEY, None)
    if request.method != "POST" or not current_user.is_authenticated:
        return False
    fields = [[k, v] for k, v in request.form.items(multi=True) if not _NOT_KEPT.search(k)]
    if not any(v.strip() for k, v in fields if k != "action"):
        return False  # nothing typed or chosen: nothing to keep
    entry = json.dumps({"user": current_user.get_id(), "at": time.time(), "action": request.path,
                        "page": urlsplit(back).path, "fields": fields}, ensure_ascii=False)
    if len(entry) > REFILL_MAX:
        return False
    session[REFILL_KEY] = secrets_box.encrypt(entry)
    return True


def refill():
    """For base.html: the entries keep_entries kept, once, on the page they belong to and only after the fresh
    sign-in; app.js puts them back into the form that sent them. None otherwise."""
    import json

    from app import secrets_box

    box = session.get(REFILL_KEY)
    if not box or request.method != "GET" or not current_user.is_authenticated:
        return None
    try:
        entry = json.loads(secrets_box.decrypt(box) or "{}")
    except ValueError:
        entry = {}
    if entry.get("user") != current_user.get_id() or time.time() - float(entry.get("at") or 0) > 2 * FRESH_SECONDS:
        session.pop(REFILL_KEY, None)  # another account, or left for too long
        return None
    if request.path != entry.get("page") or not _fresh():
        return None  # on the way back (the sign-in, its callback): stays for the page it belongs to
    session.pop(REFILL_KEY, None)
    return {"action": entry.get("action"), "fields": entry.get("fields") or []}


def _name_matches(user, typed):
    return " ".join((typed or "").split()).casefold() == " ".join(user.display_name.split()).casefold()


@bp.route("/")
@login_required
def index():
    """The page "Profil und Anmeldung", the first of the account pages (Konto-Seiten B2, Ralf 2026-10-20): details,
    access, sign-in and security, the experimental features, deleting the account. The connections and the display
    have pages of their own (connections, appearance), all four under "Konto" in the menu at the name."""
    from app import account_types, passwords, sessions, twofactor
    from app import experimental as experimental_mod

    my_period = account_types.summary(passwords.login_row(current_user))
    return render_template("accounts/index.html",
                           my_period=my_period, my_supervisors=account_types.supervisors_of(current_user),
                           my_contact=account_types.contact_of(current_user) if my_period["until"] else "",
                           supervised=account_types.supervised_by(current_user), login=passwords.login_row(current_user),
                           has_password=passwords.has_password(current_user), fresh=_fresh(),
                           config_admin=managed_by_config(current_user),
                           totp=twofactor.enabled(current_user),
                           # Voluntary setup for everyone; ORCID admins remain exempt from an extra code.
                           totp_on=True, backup_left=twofactor.backup_left(current_user),
                           sessions=sessions.active(current_user), this_sid=sessions.current_sid(),
                           session_label=sessions.shown_label,
                           minutes=FRESH_SECONDS // 60, min_length=passwords.MIN_LENGTH, orcid_fresh=orcid_fresh,
                           experimental_features=experimental_mod.features(on_account=True),
                           experimental_sources=experimental_mod.source_groups)


@bp.route("/connections")
@login_required
def connections():
    return connections_page()


def connections_page(new_agent_token=None):
    """The page "Verbindungen": everything with a stored key or token (data access tokens, Zenodo, Coscine, the own
    AI access, the AI agent interface). new_agent_token: a token of the AI agent interface just made, shown this once
    (agent_access.create)."""
    from app import agent_access, ai_access, coscine, zenodo_auth
    from app import experimental as experimental_mod
    from app.models import DataToken

    # the targets of this server, connected or not (a connection left from a target no longer set up still shows)
    zenodo = [(target, label, zenodo_auth.connection(current_user, target) is not None)
              for target, label in (("zenodo", "Zenodo"), ("sandbox", "Zenodo-Sandbox (Test)"))]
    zenodo = [z for z in zenodo if z[2] or zenodo_auth.available(z[0])]
    data_tokens = db.session.execute(db.select(DataToken, Survey).join(Survey, Survey.id == DataToken.survey_id)
        .where(DataToken.user_id == current_user.id, DataToken.expires_at > utcnow()).order_by(DataToken.created_at.desc())).all()
    agent_on = experimental_mod.enabled(agent_access.FEATURE)
    ai_row = ai_access.row(current_user)
    return render_template("accounts/connections.html", data_tokens=data_tokens, zenodo=zenodo,
                           agent_on=agent_on, agent_tokens=agent_access.listing(current_user),
                           new_agent_token=new_agent_token,
                           agent_surveys=agent_access.choosable(current_user) if agent_on else [],
                           agent_days=agent_access.DAYS, agent_url=agent_access.endpoint_url() if agent_on else "",
                           ai_access=ai_row, ai_access_days=ai_access.days_left(ai_row), ai_mod=ai_access,
                           coscine=coscine.connection(current_user),
                           # asked first: an end Coscine lists differently replaces the stored one (K7)
                           coscine_check=coscine.checked(current_user),
                           coscine_days=coscine.days_left(coscine.connection(current_user)), coscine_docs=coscine.DOCS,
                           coscine_tokens=coscine.TOKEN_PAGE)


@bp.route("/appearance")
@login_required
def appearance():
    """The page "Anzeige und Sprache": light or dark, style, text size and language of these pages, the emails,
    the learning aids. Called "Anzeige", not "Darstellung", so that it is not taken for "Designs" (Ralf, 2026-10-20)."""
    from app import display, lang, passwords

    return render_template("accounts/appearance.html", login=passwords.login_row(current_user),
                           display_settings=display.of(current_user), display_modes=display.modes(),
                           display_looks=display.looks_for(current_user), display_sizes=display.SIZES,
                           display_dropped=display.dropped_look(current_user), ui_languages=lang.LANGUAGES)


def _me():
    return db.session.get(User, current_user.id)


@bp.route("/details", methods=["POST"])
@login_required
@autosave.aware
def details():
    """The name, for accounts without ORCID (ORCID accounts get it from their public record)."""
    if current_user.has_orcid:
        abort(403)
    name = " ".join(request.form.get("name", "").split())[:200]
    if not name:
        flash(de_en("Bitte einen Namen angeben: Unter ihm stehen Sie in den Listen der Beteiligten.",
                    "Please enter a name: it is how you appear in the lists of people involved."), "error")
    else:
        user = _me()
        user.name = name
        db.session.commit()
        flash(de_en("Angaben gespeichert.", "Details saved."), "success")
    return redirect(url_for("accounts.index"))


@bp.route("/learning-aids", methods=["POST"])
@login_required
@autosave.aware
def learning_aids():
    """The switch "Lernhilfen" (app/learning.py)."""
    user = _me()
    user.learning_aids = request.form.get("on") == "1"
    db.session.commit()
    flash(de_en("Lernhilfen eingeschaltet.", "Learning aids switched on.") if user.learning_aids
          else de_en("Lernhilfen ausgeschaltet.", "Learning aids switched off."), "success")
    return redirect(url_for("accounts.appearance") + "#lernhilfen")


@bp.route("/mail-notices", methods=["POST"])
@login_required
@autosave.aware
def mail_notices():
    """The switch "Hinweise per E-Mail" (app/invites.py, M5a); shown only with mail switched on."""
    user = _me()
    user.display = dict(user.display or {}, mail_notices=request.form.get("on") == "1")
    db.session.commit()
    flash(de_en("Hinweise per E-Mail eingeschaltet.", "Notices by email switched on.") if user.display["mail_notices"]
          else de_en("Hinweise per E-Mail ausgeschaltet.", "Notices by email switched off."), "success")
    return redirect(url_for("accounts.appearance") + "#mail")


@bp.route("/expiry-seen", methods=["POST"])
@login_required
def expiry_seen():
    """"Verstanden" on the hint before the end of a limited account (app/account_types.py, N11): it stays away until
    the next step (14 or 3 days before)."""
    from app.links import local_path

    step = request.form.get("step", "")
    if step.isdigit():
        session["expiry_seen"] = int(step)
    return redirect(local_path(request.form.get("next", "")) or url_for("dashboard.index"))


@bp.route("/display", methods=["POST"])
@login_required
@autosave.aware
def display_settings():
    """The panel "Darstellung" (app/display.py): light or dark, colour style, text size."""
    from app import display

    user = _me()
    data = display.clean(request.form)
    if not display.allowed(user, data["look"]):
        flash(de_en("Diesen Farbstil können Sie nicht wählen.", "You cannot choose this colour style."), "error")
        return redirect(url_for("accounts.appearance") + "#anzeige")
    user.display = display.kept(user, data)
    db.session.commit()
    flash(de_en("Darstellung gespeichert.", "Appearance saved."), "success")
    return redirect(url_for("accounts.appearance") + "#anzeige")


@bp.route("/display/mode", methods=["POST"])
@login_required
def display_mode():
    """"Hell / Dunkel" in the menu at the name (app.js): the new mode, kept with the account."""
    from app import display

    user = _me()
    mode = request.form.get("mode")
    if mode not in ("light", "dark"):
        abort(400)
    user.display = display.kept(user, dict(display.of(user), mode=mode))
    db.session.commit()
    return "", 204


@bp.route("/language", methods=["POST"])
@login_required
@autosave.aware
def language():
    """The panel "Sprache" (app/lang.py): German or English for these pages, kept beside the look in User.display."""
    from flask import g

    from app import lang

    user = _me()
    chosen = request.form.get("lang")
    if chosen not in lang.LANGUAGES:
        abort(400)
    user.display = dict(user.display or {}, lang=chosen)
    db.session.commit()
    g.ui_lang = chosen  # the message already in the new language
    flash(de_en("Sprache gespeichert.", "Language saved."), "success")
    return redirect(url_for("accounts.appearance") + "#sprache")


@bp.route("/password", methods=["POST"])
@login_required
def password():
    """Change the password (with the current one), or add the sign-in with e-mail and password to an ORCID
    account (with a fresh sign-in, so nobody does it at a computer left open)."""
    from app import auth, passwords

    from app import account_setup

    user = _me()
    back = account_setup.back("sign_in") or redirect(url_for("accounts.index") + "#passwort")
    # site admins may add a password as well (Ralf 2026-10-09, in place of H1 of the audit 2026-10-06): a way in when
    # ORCID is not at hand; the administration itself asks for the second factor while ADMIN_TOTP is on (twofactor.py)
    new = request.form.get("password", "")
    had_password = passwords.has_password(user)
    if had_password and request.form.get("forgot") == "1":
        # a forgotten password of an account with ORCID: a fresh sign-in with ORCID proves the person instead of the
        # current password (Ralf 2026-10-18, also for site admins, who get no admin link: AU-6)
        email = passwords.login_row(user).email
        if not orcid_fresh(user):
            flash(de_en("Bitte erst neu mit ORCID anmelden; danach können Sie hier ein neues Passwort setzen.",
                        "Please sign in again with ORCID first; then you can set a new password here."), "error")
            return back
    elif had_password:
        email = passwords.login_row(user).email
        if not passwords.confirm(user, request.form.get("current", "")):
            ended = _too_many_wrong(user)
            if ended:
                return ended
            if ended is None:
                flash(de_en("Das aktuelle Passwort stimmt nicht.", "The current password is not correct."), "error")
            return back
    else:
        email = passwords.normal_email(request.form.get("email"))
        if not _fresh():
            flash(de_en("Die Anmeldung liegt zu lange zurück. Bitte melden Sie sich neu an und versuchen Sie es dann erneut.",
                        "The sign-in was too long ago. Please sign in again and then try once more."), "error")
            return back
        if not email:
            flash(de_en("Bitte eine gültige E-Mail-Adresse angeben.", "Please enter a valid email address."), "error")
            return back
    error = passwords.problem(new, user.name, email)
    if not error and new != request.form.get("repeat", ""):
        error = de_en("Die beiden Eingaben stimmen nicht überein.", "The two entries do not match.")
    if error:
        flash(error, "error")
        return back
    if not had_password:
        from app import email_confirmation

        # an address of another account gets the same answer as a free one; its owner hears of it by mail (AUTH-5)
        error = (email_confirmation.begin(user, email, new) if email_free(email, user)
                 else email_confirmation.taken(user, email, new))
        flash(error or de_en("Bestätigen Sie die Anmeldeadresse über den Link in der E-Mail. Bis dahin bleibt ORCID Ihr Zugang.",
                             "Confirm the sign-in address using the link in the email. Until then, ORCID remains your access."),
              "error" if error else "success")
        return back
    passwords.login_row(user, create=True).email = email
    passwords.set_password(user, new)
    db.session.commit()
    # every other session of the account ended with the new password; this one counts as fresh only if the
    # current password was given (adding a password needed a fresh sign-in anyway and does not renew it)
    auth.remember(user, fresh=had_password, keep_age=not had_password)
    flash(de_en("Passwort gespeichert. Alle anderen Sitzungen dieses Kontos wurden beendet.",
                "Password saved. All other sessions of this account were ended."), "success")
    return back


@bp.route("/password/remove", methods=["POST"])
@login_required
def remove_password():
    """Remove the sign-in with e-mail and password, only while ORCID stays as the way in, with the current password
    (konto-einrichtung-konzept.md, 2 A; connecting ORCID cannot be undone by the person)."""
    from app import auth, passwords

    user = _me()
    back = redirect(url_for("accounts.index") + "#anmeldung")
    if not user.has_orcid or not passwords.has_password(user):
        abort(403)
    if not passwords.confirm(user, request.form.get("current", "")):
        ended = _too_many_wrong(user)
        if ended:
            return ended
        if ended is None:
            flash(de_en("Das aktuelle Passwort stimmt nicht.", "The current password is not correct."), "error")
        return back
    passwords.remove(user)
    db.session.commit()
    auth.remember(user, fresh=False, keep_age=True)
    flash(de_en("Die Anmeldung mit E-Mail-Adresse und Passwort ist entfernt. Sie melden sich jetzt nur noch mit ORCID an; "
                "alle anderen Sitzungen dieses Kontos wurden beendet.",
                "Sign-in with email address and password is removed. You now sign in with ORCID only; all other "
                "sessions of this account were ended."), "success")
    return back


@bp.route("/orcid/remove", methods=["POST"])
@login_required
def remove_orcid():
    """Disconnect the ORCID iD, only while e-mail and password stay as the way in, with the current password
    (konto-schluessel-plan.md B5). Site admins named by iD in the configuration keep it."""
    from app import auth, passwords

    user = _me()
    back = redirect(url_for("accounts.index") + "#anmeldung")
    if not user.has_orcid or not passwords.has_password(user) or managed_by_config(user):
        abort(403)
    if not passwords.confirm(user, request.form.get("current", "")):
        ended = _too_many_wrong(user)
        if ended:
            return ended
        if ended is None:
            flash(de_en("Das aktuelle Passwort stimmt nicht.", "The current password is not correct."), "error")
        return back
    unlink_orcid(user)
    db.session.commit()
    from app import security_log

    security_log.event("orcid_removed", account=user.id)
    auth.remember(_me(), fresh=False, keep_age=True)
    flash(de_en("Die ORCID iD ist von Ihrem Konto getrennt. Sie melden sich jetzt mit E-Mail-Adresse und Passwort an; "
                "Befragungen, Gruppen und Freigaben bleiben.",
                "The ORCID iD is disconnected from your account. You now sign in with email address and password; "
                "surveys, groups and shares stay."), "success")
    return back


def orcid_fresh(user=None):
    """True if this browser signed in with ORCID within FRESH_SECONDS, ORCID asking for the password (auth.remember
    with fresh=True after such a round trip): it may set a new password without the forgotten one."""
    from app import twofactor

    user = current_user if user is None else user
    return bool(user.has_orcid and _fresh() and session.get(twofactor.SIGNED_IN_VIA) == "orcid")


@bp.route("/verify-email", methods=["POST"])
@login_required
def verify_email():
    from app import email_confirmation, passwords

    user = _me()
    row = passwords.login_row(user)
    if row is None or not row.email or not row.password_hash or not _fresh():
        abort(403)
    error = email_confirmation.begin(user, row.email)
    flash(error or de_en("Bestätigen Sie Ihre bestehende Anmeldeadresse über den Link in der E-Mail.",
                         "Confirm your existing sign-in address using the link in the email."),
          "error" if error else "success")
    return redirect(url_for("accounts.index") + "#passwort")


@bp.route("/confirm-email/<token>", methods=["GET", "POST"])
@login_required
def confirm_email(token):
    from app import auth, email_confirmation

    user = _me()
    proof = email_confirmation.pending(user, token)
    if proof is None:
        abort(404)
    from app import secrets_box
    import json

    try:
        payload = json.loads(secrets_box.decrypt(proof.secret_box))
    except (ValueError, TypeError):
        abort(404)
    if request.method == "POST":
        if not _fresh():
            flash(de_en("Melden Sie sich vor der Bestätigung erneut an.",
                        "Sign in again before confirming."), "error")
        elif email_confirmation.accept(user, token):
            auth.remember(user, fresh=False, keep_age=True)
            flash(de_en("Anmeldeadresse bestätigt und Passwort aktiviert.",
                        "Sign-in address confirmed and password activated.") if payload.get("activate") else
                  de_en("Anmeldeadresse bestätigt.", "Sign-in address confirmed."), "success")
        else:
            flash(de_en("Der Link ist nicht mehr gültig oder die Adresse wurde inzwischen belegt. Fordern Sie einen neuen Link an.",
                        "The link is no longer valid or the address has since been taken. Request a new link."), "error")
        return redirect(url_for("accounts.index") + "#passwort")
    return render_template("accounts/confirm_email.html", email=proof.email, activate=payload.get("activate", False))


def _too_many_wrong(user):
    """After MAX_FAILURES wrong current passwords (per sender, as at the sign-in) this session ends: whoever guesses
    at an open computer has to sign in, which pauses as well (security audit 2026-10-06, S14). None while it may
    go on, False if the account pauses because of wrong passwords from elsewhere: then the session stays and the
    message says so, so whoever only knows the address cannot sign its owner out (code review 2026-10-15, H3)."""
    from app import passwords, sessions

    if not passwords.paused_here(user):
        if not passwords.paused(user):
            return None
        flash(de_en("Die Anmeldung mit Passwort pausiert gerade wegen zu vieler falscher Passwörter von anderswo. "
                    "Sie bleiben angemeldet; versuchen Sie es in 15 Minuten erneut.",
                    "Signing in with a password is paused because of too many wrong passwords from elsewhere. "
                    "You stay signed in; try again in 15 minutes."), "error")
        return False
    sessions.end(user.id, sessions.current_sid())
    logout_user()
    session.pop("signed_in_at", None)
    flash(de_en("Das Passwort war {n}-mal falsch. Sie wurden abgemeldet; die Anmeldung mit Passwort pausiert für 15 Minuten.",
                "The password was wrong {n} times. You were signed out; signing in with a password is paused for 15 minutes.",
                n=passwords.MAX_FAILURES), "error")
    return redirect(url_for("auth.sign_in"))


@bp.route("/sessions/<sid>/end", methods=["POST"])
@login_required
def end_session(sid):
    """End one sign-in of this account on another device (the list on this page)."""
    from app import sessions

    if sid == sessions.current_sid():
        return redirect(url_for("accounts.index") + "#sitzungen")  # this one: the "Abmelden" in the menu
    if sessions.end(current_user.id, sid):
        from app import passwords

        passwords._event("session_ended", account=current_user.id)  # Abgleich 2026-10-15, AU-7
        flash(de_en("Die Sitzung wurde beendet; dort ist niemand mehr angemeldet.",
                    "The session was ended; nobody is signed in there any more."), "success")
    return redirect(url_for("accounts.index") + "#sitzungen")


@bp.route("/sign-out-everywhere", methods=["POST"])
@login_required
def sign_out_everywhere():
    from app import auth, passwords

    from app import sessions

    user = _me()
    row = passwords.login_row(user, create=True)
    row.session_version = (row.session_version or 0) + 1
    sessions.end_all(user.id)
    from app import account_access

    gone = account_access.revoke(user.id)
    db.session.commit()
    auth.remember(user, fresh=False, keep_age=True)  # stays signed in; how long ago the sign-in was does not change
    passwords._event("signed_out_everywhere", account=user.id)  # Abgleich 2026-10-15, AU-7
    flash(de_en("Alle anderen Sitzungen und Datenzugänge sowie offene Einladungs-, Beitritts- und Pretest-Links wurden widerrufen. In diesem Browser bleiben Sie angemeldet.",
                "All other sessions and data access tokens, pending invitations, join links and pretest links were revoked. You stay signed in in this browser."), "success")
    account_access.tell(gone)
    return redirect(url_for("accounts.index"))


@bp.route("/delete", methods=["GET", "POST"])
@login_required
def delete_own():
    from app import passwords

    by_password = passwords.has_password(current_user)  # else: a fresh ORCID sign-in and the typed name
    if request.method == "POST":
        if by_password and not passwords.confirm(current_user, request.form.get("password", "")):
            ended = _too_many_wrong(current_user)
            if ended:
                return ended
            if ended is None:
                flash(de_en("Das Passwort stimmt nicht. Es wurde nichts gelöscht.", "The password is not correct. Nothing was deleted."), "error")
        elif not by_password and not _fresh():
            flash(de_en("Die Anmeldung liegt zu lange zurück. Bitte melden Sie sich neu an und löschen Sie das Konto dann.",
                        "The sign-in was too long ago. Please sign in again and then delete the account."), "error")
        elif not by_password and not _name_matches(current_user, request.form.get("name")):
            flash(de_en("Der eingegebene Name stimmt nicht. Es wurde nichts gelöscht.", "The name entered does not match. Nothing was deleted."), "error")
        else:
            user = db.session.get(User, current_user.id)
            audit.done(user)  # noted while the account is still there; the access log writes it after the sign-out (S16)
            try:
                delete(user, "self", request.form)
            except DeleteError as e:
                audit.done(None, worked=False)
                flash(str(e), "error")
            else:
                logout_user()  # also takes back the "stay signed in" cookie of this browser
                session.pop("signed_in_at", None)
                flash(de_en("Ihr Konto wurde gelöscht.", "Your account was deleted."), "success")
                return redirect(url_for("public.index"))
        return redirect(url_for("accounts.delete_own"))
    return render_template("accounts/delete.html", user=current_user, plan=plan(current_user), admin=False,
                           fresh=_fresh(), minutes=FRESH_SECONDS // 60, role_names=SurveyMember.roles(),
                           by_password=by_password)


# for site admins (app/admin.py registers the routes under /admin, behind its admins-only check)


def name_matches(user, typed):
    return _name_matches(user, typed)


def admin_target(user_id):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    if managed_by_config(user):
        flash(de_en("Admins der Plattform werden in der Konfiguration des Servers verwaltet (ADMIN_ORCIDS) und lassen sich "
                    "hier weder sperren noch löschen.",
                    "Platform admins are managed in the server configuration (ADMIN_ORCIDS) and can be neither blocked "
                    "nor deleted here."), "error")
        abort(redirect(url_for("admin.researchers")))
    return user


def _org_of(user):
    from app import orgs

    return orgs.affiliation_of(user)
