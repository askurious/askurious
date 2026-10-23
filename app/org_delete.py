"""Organisation admins delete people (org-personen-loeschen-konzept.md, Ralf 2026-10-18 "alles wie empfohlen").

Who belongs to this organisation alone is marked for deletion: the account is blocked at once (block_reason
"deletion"), the person is told (the sign-in says so, and a mail goes when mail is on), and the hourly sweep deletes it
DAYS later. Until then an admin of the organisation takes the mark back or changes what becomes of the person's things.
Who also belongs to another organisation is not deleted but removed (org_admin.people_action). Admins of the same
organisation, site admins and oneself are never deleted here.

What belongs to the person alone (surveys nobody else is in, designs) passes to a person of the organisation or goes
with the account: one preset for all ("heir") and each survey and design changeable on its own. Presets: surveys with
real participations and running surveys pass on (deleting one with participations needs its own tick, a running one
never goes), designs pass on when shared, the rest goes. The organisation admin never takes anything over himself: he
sees no answers (privacy concept). Surveys with other admins or co-owners follow the rules of every deletion
(accounts.plan: leave, successor). What cannot be done at the due time (no heir left who can receive it, a draft that
started running) stays listed as "Löschung fällig" for the organisation's admins.
"""
from datetime import timedelta

from flask import current_app

from app import orgs
from app.extensions import db
from app.lang import de_en
from app.models import AccessLog, OrgDeletion, User, UserLogin, utcnow

DAYS = 30
REASON = "deletion"  # UserLogin.block_reason while marked


# --- who ---------------------------------------------------------------------------------------------------------------

def other_orgs(user, org):
    """The organisations besides `org` the person belongs to (never the guests)."""
    return {i for i in orgs.ids_of(user) if i not in (org.id, orgs.guest_id(), None)}


def refusal(admin, user, org):
    """Why `admin` may not mark `user` for deletion in `org`, or None."""
    if not orgs.manages(admin, user, org):
        return de_en("Dieses Konto verwalten Sie nicht.", "You do not manage this account.")
    if orgs.is_admin_of(user, org.id):
        return de_en("Admins der Organisation löscht ein Admin der Plattform.",
                     "A platform admin deletes admins of the organisation.")
    if other_orgs(user, org):
        return de_en("Die Person gehört auch zu einer anderen Organisation; sie wird nicht gelöscht, sondern hier "
                     "entfernt.", "The person also belongs to another organisation; they are not deleted but removed "
                     "here.")
    return None


def _able(user):
    """Can take things over: signed in before, neither blocked, expired nor marked (as accounts._can_take_over)."""
    from app import account_types

    if user is None or user.last_login_at is None:
        return False
    login = db.session.get(UserLogin, user.id)
    return login is None or (login.status == UserLogin.ACTIVE and not account_types.is_expired(login))


def heirs(org, user):
    """The people who can take the person's things over: at home in the organisation, able, not the person."""
    return [u for u, _ in orgs.members_of(org)
            if u.id != user.id and orgs.home_id(u) == org.id and _able(u)]


def marked(user):
    return db.session.get(OrgDeletion, user.id) if user is not None else None


# --- what becomes of the person's things --------------------------------------------------------------------------------

def items(plan):
    """What belongs to the person alone, each {key, kind, title, real, default ("heir" | "delete"), may_delete,
    needs_tick, obj}. kind: "running", "data", "draft" (surveys) or "design"."""
    out = []
    for kind, entries in (("running", plan["running"]), ("data", plan["data"]), ("draft", plan["drafts"])):
        for e in entries:
            s = e["survey"]
            out.append({"key": f"s{s.id}", "kind": kind, "title": s.title, "real": e["real"], "obj": s,
                        "default": "delete" if kind == "draft" else "heir", "may_delete": kind != "running",
                        "needs_tick": kind == "data"})
    for d in plan["designs"]:
        shared = bool(d.shares or d.group_shares or d.org_shared)
        out.append({"key": f"d{d.id}", "kind": "design", "title": d.name, "real": 0, "obj": d, "shared": shared,
                    "default": "heir" if shared else "delete", "may_delete": True, "needs_tick": False})
    return out


def choices_from_form(form, listed, able):
    """The choices of the page: the preset heir, each row ("" = preset, "delete" or a person's id), the ticks."""
    ids = {str(u.id) for u in able}
    heir = form.get("heir") if form.get("heir") in ids else None
    rows = {}
    for it in listed:
        value = form.get("item_" + it["key"]) or ""
        if value == "delete" and it["may_delete"] or value in ids:
            rows[it["key"]] = value
    ticks = [it["key"] for it in listed if it["needs_tick"] and form.get("tick_" + it["key"]) == "1"]
    return {"heir": int(heir) if heir else None, "items": rows, "confirm": ticks}


def resolve(user, org, choices, plan=None):
    """({key: User to pass to, or None to delete}, [open points]) for the choices as they stand now."""
    from app import accounts

    plan = plan or accounts.plan(user)
    able = {u.id: u for u in heirs(org, user)}
    preset = able.get((choices or {}).get("heir"))
    rows, ticks = (choices or {}).get("items") or {}, set((choices or {}).get("confirm") or [])
    decided, problems = {}, []
    for it in items(plan):
        value = rows.get(it["key"], "")
        if value == "delete" or (value == "" and it["default"] == "delete"):
            if not it["may_delete"]:
                problems.append({"key": it["key"], "title": it["title"], "why": "running"})
            elif it["needs_tick"] and value == "delete" and it["key"] not in ticks:
                problems.append({"key": it["key"], "title": it["title"], "why": "tick"})
            else:
                decided[it["key"]] = None
            continue
        heir = able.get(int(value)) if value.isdigit() else preset
        if heir is None:
            problems.append({"key": it["key"], "title": it["title"], "why": "heir"})
        else:
            decided[it["key"]] = heir
    return decided, problems


def why_label(why):
    return {"running": de_en("läuft gerade und lässt sich nur übergeben", "is running and can only be passed on"),
            "tick": de_en("Löschen samt Teilnahmen ist nicht bestätigt", "deleting with participations is not confirmed"),
            "heir": de_en("niemand ist gewählt, der übernehmen kann", "nobody who can receive it is chosen"),
            "org": de_en("das muss ein Admin der Plattform klären", "a platform admin has to settle this")}.get(why, why)


# --- marking, taking back, the sweep ------------------------------------------------------------------------------------

def mark(user, org, by, choices):
    """Mark or change the mark (the due date stays once set); blocks the account and tells the person."""
    from app import accounts, security_log

    row = marked(user)
    new = row is None
    if new:
        row = OrgDeletion(user_id=user.id, org_id=org.id, requested_by_id=by.id, requested_at=utcnow(),
                          due_at=utcnow() + timedelta(days=DAYS))
        db.session.add(row)
    row.choices, row.open_points = choices, None
    login = db.session.get(UserLogin, user.id)
    if login is None or login.status != UserLogin.BLOCKED or login.block_reason != REASON:
        accounts.set_blocked(user, True, by, reason=REASON)
    db.session.commit()
    security_log.event("organisation_deletion_marked" if new else "organisation_deletion_changed", by=by.id,
                       account=user.id, target=f"org {org.id}")
    if new:
        _tell(user, row, org)
    return row


def undo(user, by):
    """Take the mark back: the account opens again (unless it was blocked for another reason meanwhile)."""
    from app import accounts, security_log

    row = marked(user)
    if row is None:
        return False
    login = db.session.get(UserLogin, user.id)
    if login is not None and login.status == UserLogin.BLOCKED and login.block_reason == REASON:
        accounts.set_blocked(user, False, by)
    org_id = row.org_id
    db.session.delete(row)
    db.session.commit()
    security_log.event("organisation_deletion_undone", by=by.id, account=user.id, target=f"org {org_id}")
    return True


def _tell(user, row, org):
    """Mail to the person when mail is on and the account has an address; the sign-in says it always."""
    from app import lang, mail

    login = db.session.get(UserLogin, user.id)
    if not mail.enabled() or login is None or not login.email:
        return
    with mail.in_language(lang.of(user)):
        mail.send(login.email, de_en("{b}: Ihr Konto wird gelöscht", "{b}: your account will be deleted", b=_brand()),
                  de_en("Guten Tag {n},\n\nein Admin der Organisation „{o}“ hat Ihr Konto zur Löschung vorgemerkt. Es ist "
                        "ab sofort gesperrt und wird am {d} gelöscht. Wenn das ein Irrtum ist, wenden Sie sich bitte "
                        "vorher an die Admins der Organisation; sie können die Löschung zurücknehmen.",
                        "Hello {n},\n\nan admin of the organisation “{o}” has marked your account for deletion. It is "
                        "blocked from now on and will be deleted on {d}. If this is a mistake, please contact the "
                        "organisation's admins before then; they can take the deletion back.",
                        n=user.name or "", o=orgs.label(org), d=when(row.due_at)) + mail.signature())


def _brand():
    from app import brand

    return brand.name()


def when(dt):
    """The day of deletion in the language of the page (or of the mail)."""
    from app import lang
    from app.timeutil import fmt

    return fmt(dt, False, "en" if lang.english() else "de")


def blocked_said(user):
    """The sign-in message of a marked account, or None."""
    row = marked(user)
    if row is None:
        return None
    return de_en("Dieses Konto ist zur Löschung vorgemerkt und wird am {d} gelöscht. Wenn das ein Irrtum ist, wenden Sie "
                 "sich bitte an die Admins Ihrer Organisation.", "This account is marked for deletion and will be deleted "
                 "on {d}. If this is a mistake, please contact the admins of your organisation.", d=when(row.due_at))


def execute(row):
    """Delete now as chosen; False (and the open points kept on the row) when something cannot be done."""
    from app import accounts, security_log

    user = db.session.get(User, row.user_id)
    org = orgs.get(row.org_id)
    if user is None:
        db.session.delete(row)
        db.session.commit()
        return True
    if org is None or user.orcid in current_app.config["ADMIN_ORCIDS"]:
        row.open_points = [{"key": "", "title": "", "why": "org"}]
        db.session.commit()
        return False
    plan = accounts.plan(user)
    decided, problems = resolve(user, org, row.choices, plan)
    if problems:
        row.open_points = problems
        db.session.commit()
        return False
    heir_map = {}
    for key, heir in decided.items():
        heir_map[("s" if key[0] == "s" else "d", int(key[1:]))] = heir
    uid, number, by_id, org_id = user.id, user.number or user.id, row.requested_by_id, row.org_id
    try:
        accounts.delete(user, "org", heirs=heir_map)  # commits; takes the mark along
    except accounts.DeleteError:
        db.session.rollback()
        return False
    db.session.add(AccessLog(at=utcnow(), user_id=None, who=_brand(), action="accounts.deleted_by_organisation",
                             detail=f"user={number} org={org_id} marked_by={by_id or '-'}", status=200))
    db.session.commit()
    security_log.event("account_deleted_by_organisation", account=uid, target=f"org {org_id}")
    return True


def run(now=None):
    """Hourly: delete the marked accounts that are due. A mark whose block someone lifted (a site admin unblocked
    the account) is dropped. Returns how many accounts went. An unexpected error with one account is logged and
    that account waits for the next run; the others still go (audit 2026-10-18, FUNC-02)."""
    from app import lifecycle

    now = now or utcnow()
    n = 0
    for user_id in list(db.session.scalars(db.select(OrgDeletion.user_id).where(OrgDeletion.due_at <= now))):
        try:
            row = db.session.get(OrgDeletion, user_id)
            if row is None:
                continue
            login = db.session.get(UserLogin, row.user_id)
            if login is None or login.status != UserLogin.BLOCKED or login.block_reason != REASON:
                db.session.delete(row)
                db.session.commit()
                continue
            if execute(row):
                n += 1
        except Exception as e:
            db.session.rollback()
            if lifecycle._transient(e):
                raise
            current_app.logger.exception("Deletion of account %s for its organisation failed; the others go on", user_id)
    return n


def overdue(org):
    """The marks of the organisation past their date (open points): "Löschung fällig"."""
    return list(db.session.scalars(db.select(OrgDeletion).where(OrgDeletion.org_id == org.id,
                                                                OrgDeletion.due_at <= utcnow())))


def of_org(org):
    """{user id: row} of the organisation's marks, for the list of people."""
    return {r.user_id: r for r in db.session.scalars(db.select(OrgDeletion).where(OrgDeletion.org_id == org.id))}



# --- removing someone who also belongs elsewhere --------------------------------------------------------------------------

def remove(org, person):
    """Take the person (a User, or an account key for the code before) out of `org` (decision 5a): the row goes, the
    groups of the organisation too (else the person still belongs to it, app/orgs.py), and a home here moves to another
    organisation of the person. Their surveys stay. Returns (new home or None, number of groups left). Does not
    commit."""
    from app import accounts, groups
    from app.models import WorkGroup, WorkGroupMember

    user = orgs.account_of(person)
    if user is None:
        return None, 0
    new_home = None
    if orgs.home_id(user) == org.id:
        others = sorted(other_orgs(user, org))
        with_row = [i for i in others if orgs.row_of(i, user.id) is not None]
        new_home = orgs.get((with_row or others or [None])[0])
    left = 0
    for m in list(db.session.scalars(db.select(WorkGroupMember).join(WorkGroup).where(
            WorkGroupMember.user_id == user.id, orgs.column(WorkGroup.org_id) == org.id))):
        group = m.group
        rest = [o for o in group.members if o is not m]
        left += 1
        if not rest:  # as when deleting an account: a group without members goes
            groups.remove(group)
            continue
        if m.role == "lead" and not any(o.role == "lead" for o in rest):  # longest in the group, able first
            sorted(rest, key=lambda o: (not accounts._can_take_over(o), o.id))[0].role = "lead"
        groups.end_shares_of_user(group, user)
        group.members.remove(m)
    db.session.flush()
    if new_home is not None:
        orgs.set_home(user, new_home)
    row = orgs.row_of(org.id, user.id)
    if row is not None:
        db.session.delete(row)
    orgs.forget()
    return new_home, left
