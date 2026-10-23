"""User types with a period (konzept-nutzertypen-2026-10-15.md, Ralf 2026-10-15 "alle wie empfohlen").

A user type (AccountType) is a template site admins keep under Administration > Personen: a name, a period (without
end, months or weeks from the start, or a fixed end date for courses), the survey types it opens (app/kind_access.py),
whether it may create surveys, whether it has a supervisor, and how many months after its end the account is kept.
It is taken over once when an account comes in (N2): afterwards what the account says holds; the type stays on the
account as its label.

The period starts when the account comes in through its link (N4): accepting an invitation, the first sign-in with
ORCID, setting the password over the one-time link of an account a site admin made. The end is the end of the last day
in German time. At the end the account is blocked with the reason "expired" (N12): accounts.set_blocked ends its
sessions, tokens and links; accounts.is_blocked and auth.load_user read the end themselves, so it holds at once even
before the hourly sweep. "verlängern" opens it again. After the type's months (N13) the sweep deletes it, but only
when nothing has to be handed over; else it is listed as "Löschung fällig" for a site admin.

Supervisor (N10): an account of a supervised type has a person (by default who brought it in) who becomes admin of
every survey it creates, so the data of a thesis stay reachable after its end.

Courses (N6-N8): a group a site admin marks with a type and an end date. New accounts that come in through the group
(join link, invitation, list) take that type, valid until the course ends; accounts that are there already keep
theirs, except an expired one of the same type, which opens again with the new end.
"""
import re
from datetime import date, datetime, timedelta

from flask import current_app

from app.extensions import db
from app.lang import de_en
from app.models import AccountSupervisor, AccountType, SignInOrcid, User, UserLogin, WorkGroup, WorkGroupMember, utcnow
from app.timeutil import end_of_local_day_utc, fmt, local_today, to_local

EXPIRED = "expired"  # UserLogin.block_reason of an account whose period ended
NOTICE_DAYS = (30, 14, 3)  # advance notice in the app; the mail goes at 14 days (N11)
MAIL_DAYS = 14
MAX_MONTHS, MAX_WEEKS = 120, 520
_SPEC = re.compile(r"^(?:|[mw]:\d{0,3}|d:(?:\d{4}-\d{2}-\d{2})?)$")

# the five types to start with (N1); rights and periods are suggestions an admin changes on the type. "create" in
# kinds is the right to create surveys of the type (app/kind_access.py); the column may_create is no longer read
# (2026-10-18, konzept-stufe-anlegen-2026-10-18.md) and stays None
SEED = (
    {"key": "staff", "name_de": "Mitarbeitende", "name_en": "Staff", "duration": "", "kinds": None, "may_create": None,
     "supervised": False, "delete_months": None, "is_default": True},
    {"key": "bachelor", "name_de": "Bachelorarbeit", "name_en": "Bachelor's thesis", "duration": "m:4",
     "kinds": {"survey": "create", "live": "view", "interview": "view", "diary": "view"}, "may_create": None,
     "supervised": True, "delete_months": 6},
    {"key": "master", "name_de": "Masterarbeit", "name_en": "Master's thesis", "duration": "m:7",
     "kinds": {"survey": "create", "live": "view", "interview": "view", "diary": "view"}, "may_create": None,
     "supervised": True, "delete_months": 6},
    {"key": "internship", "name_de": "Praktikum", "name_en": "Internship", "duration": "w:",
     "kinds": {"survey": "create", "live": "create", "interview": "create", "diary": "create"}, "may_create": None,
     "supervised": True, "delete_months": 6},
    {"key": "course", "name_de": "Lehrveranstaltung", "name_en": "Course", "duration": "d:",
     "kinds": {"survey": "create", "live": "create", "interview": "off", "diary": "off"}, "may_create": None,
     "supervised": False, "delete_months": 3},
)


# Types per organisation (Ralf 2026-10-16): every organisation starts with "Mitarbeitende" only; the others are
# templates its admins (or site admins) add when the organisation needs them
TEMPLATES = tuple(seed for seed in SEED if seed["key"] != "staff")
PER_ORG_DONE = "types_per_org"  # SiteSetting: unused types of the start removed once


# --- the types --------------------------------------------------------------------------------------------------------

def ensure_seed():
    """"Mitarbeitende" of the organisation everything started in, once, on an empty table (at the start of the server,
    app/__init.py). Its survey types come from the former default for new accounts (kind_access.SETTING)."""
    if db.session.scalar(db.select(db.func.count(AccountType.id))):
        return
    from app import kind_access, signin

    values = dict(SEED[0])
    values["kinds"] = kind_access.decode(signin.setting(kind_access.SETTING))
    db.session.add(AccountType(position=0, **values))
    db.session.commit()


def ensure_per_org():
    """Every organisation (not the guests) has its own types, at least "Mitarbeitende" as the type of its new accounts
    (a copy of the start's); once, the types of the start no account, group or sign-in entry uses are removed
    (Ralf 2026-10-16: each organisation starts with "Mitarbeitende" only). At the start of the server."""
    from app import orgs, signin
    from app.models import Organisation

    if signin.setting(PER_ORG_DONE) != "1":
        used = set(db.session.scalars(db.select(UserLogin.account_type_id).where(UserLogin.account_type_id.is_not(None))))
        used |= set(db.session.scalars(db.select(WorkGroup.account_type_id).where(WorkGroup.account_type_id.is_not(None))))
        used |= set(db.session.scalars(db.select(SignInOrcid.account_type_id).where(SignInOrcid.account_type_id.is_not(None))))
        for t in list(db.session.scalars(db.select(AccountType).where(AccountType.org_id.is_(None)))):
            if t.key != "staff" and not t.is_default and t.id not in used:
                for org in db.session.scalars(db.select(Organisation).where(Organisation.default_type_id == t.id)):
                    org.default_type_id = None
                db.session.delete(t)
        signin._set(PER_ORG_DONE, "1", None)
        db.session.commit()
    start = default_type(None)
    for org in orgs.all_orgs(guests=False):
        if org.key == orgs.MAIN:
            continue
        if db.session.scalar(db.select(AccountType.id).where(AccountType.org_id == org.id).limit(1)) is None:
            db.session.add(_copy(start, org) if start is not None else AccountType(org_id=org.id, **{
                k: v for k, v in SEED[0].items() if k != "key"}))
    db.session.commit()


def _copy(t, org, default=True):
    """A type of another organisation as the first type of `org` (no key: keys belong to the start's types)."""
    return AccountType(org_id=org.id, name_de=t.name_de, name_en=t.name_en, duration=t.duration, kinds=t.kinds,
                       may_create=t.may_create, supervised=t.supervised, delete_months=t.delete_months,
                       is_default=default, position=0)


def template(key):
    return next((seed for seed in TEMPLATES if seed["key"] == key), None)


def from_template(key, org=None):
    """Add the template `key` (Bachelorarbeit, Masterarbeit, Praktikum, Lehrveranstaltung) as a type of `org`."""
    seed = template(key)
    if seed is None:
        return None
    from app import orgs

    org_id = orgs.key(getattr(org, "id", org))
    values = {k: v for k, v in seed.items() if k not in ("key", "is_default")}
    t = AccountType(org_id=None if org_id == orgs.default_id() else org_id,
                    position=len(all_types(org_id=org_id)), **values)
    db.session.add(t)
    db.session.flush()
    return t


_ALL = object()


def all_types(hidden=True, org_id=_ALL):
    """The types, ordered; with `org_id` those of that organisation (None: the start's)."""
    query = db.select(AccountType).order_by(AccountType.position, AccountType.id)
    if org_id is not _ALL:
        from app import orgs

        query = query.where(orgs.column(AccountType.org_id) == orgs.key(org_id))
    if not hidden:
        query = query.where(AccountType.hidden.is_(False))
    return list(db.session.scalars(query))


def org_of(t):
    from app import orgs

    return orgs.key(t.org_id) if t is not None else None


def get(type_id):
    try:
        return db.session.get(AccountType, int(type_id)) if type_id not in (None, "") else None
    except (TypeError, ValueError):
        return None


def by_key(key):
    return db.session.scalar(db.select(AccountType).filter_by(key=key))


def default_type(org_id=None):
    """The type of new accounts of an organisation (N3; per organisation since 2026-10-16), else of the start; None
    while there is no table content yet (then nothing changes)."""
    from app import orgs

    query = db.select(AccountType).where(AccountType.is_default.is_(True)).order_by(AccountType.id)
    found = db.session.scalar(query.where(orgs.column(AccountType.org_id) == orgs.key(org_id)))
    return found or db.session.scalar(query.where(orgs.column(AccountType.org_id) == orgs.default_id()))


def name(t):
    if t is None:
        return de_en("ohne Typ", "no type")
    from app import lang

    return (t.name_en or t.name_de) if lang.english() else t.name_de


def of(user):
    row = db.session.get(UserLogin, user.id) if user is not None else None
    return db.session.get(AccountType, row.account_type_id) if row is not None and row.account_type_id else None


def count_of(t):
    return db.session.scalar(db.select(db.func.count(UserLogin.user_id)).filter_by(account_type_id=t.id)) or 0


# --- periods ----------------------------------------------------------------------------------------------------------

def valid_spec(spec):
    return isinstance(spec, str) and bool(_SPEC.match(spec))


def parse(spec):
    """("", None) without end; ("m", 4) / ("w", 6); ("d", date); ("m"/"w"/"d", None): to be given at the invitation."""
    spec = spec or ""
    if not valid_spec(spec) or not spec:
        return "", None
    unit, _, value = spec.partition(":")
    if not value:
        return unit, None
    if unit == "d":
        try:
            return unit, date.fromisoformat(value)
        except ValueError:
            return unit, None
    return unit, int(value)


def needs_input(spec):
    """The type leaves the number or the date to the invitation (Praktikum, Lehrveranstaltung)."""
    unit, value = parse(spec)
    return bool(unit) and value is None


def describe(spec):
    """"4 Monate", "6 Wochen", "bis 31.03.2027", "unbefristet"; for a type without number "Wochen, bei der Einladung"."""
    unit, value = parse(spec)
    if not unit:
        return de_en("unbefristet", "without end")
    if value is None:
        return {"m": de_en("Monate, bei der Einladung anzugeben", "months, given at the invitation"),
                "w": de_en("Wochen, bei der Einladung anzugeben", "weeks, given at the invitation"),
                "d": de_en("festes Enddatum, bei der Einladung anzugeben", "a fixed end date, given at the invitation")}[unit]
    if unit == "d":
        return de_en("bis {d}", "until {d}", d=_date_text(value))
    if unit == "m":
        return de_en("{n} Monat" if value == 1 else "{n} Monate", "{n} month" if value == 1 else "{n} months", n=value)
    return de_en("{n} Woche" if value == 1 else "{n} Wochen", "{n} week" if value == 1 else "{n} weeks", n=value)


def _date_text(d):
    from app import lang

    return fmt(end_of_local_day_utc(d), with_time=False, lang=lang.current())


def end_for(spec, start):
    """The end (naive UTC, 23:59 German time of the last day) of a period starting at `start` (naive UTC); None without
    end or while the number is missing. Months as the retention periods count them (31.1. + 1 month = 28./29.2.)."""
    unit, value = parse(spec)
    if not unit or value is None:
        return None
    if unit == "d":
        return end_of_local_day_utc(value)
    first = to_local(start).date()
    if unit == "w":
        return end_of_local_day_utc(first + timedelta(weeks=value))
    from app.retention import _add_months

    return end_of_local_day_utc(_add_months(first, value))


def spec_from_form(form, t, prefix="valid_"):
    """The period of a form: (spec, error). Fields: <prefix>mode "type" (as the type says), "m", "w", "d", "none";
    <prefix>n the number; <prefix>date the end date (YYYY-MM-DD)."""
    mode = form.get(prefix + "mode") or "type"
    if mode == "type":
        spec = t.duration if t is not None else ""
        if needs_input(spec):
            unit, _ = parse(spec)
            return None, {"m": de_en("Bitte die Laufzeit in Monaten angeben.", "Please give the period in months."),
                          "w": de_en("Bitte die Laufzeit in Wochen angeben.", "Please give the period in weeks."),
                          "d": de_en("Bitte das Enddatum angeben.", "Please give the end date.")}[unit]
        return spec, None
    if mode == "none":
        return "", None
    if mode in ("m", "w"):
        try:
            n = int(form.get(prefix + "n") or "")
        except ValueError:
            n = 0
        if not 1 <= n <= (MAX_MONTHS if mode == "m" else MAX_WEEKS):
            return None, de_en("Bitte eine Laufzeit von 1 bis {n} angeben.", "Please give a period from 1 to {n}.",
                               n=MAX_MONTHS if mode == "m" else MAX_WEEKS)
        return f"{mode}:{n}", None
    if mode == "d":
        try:
            d = date.fromisoformat(form.get(prefix + "date") or "")
        except ValueError:
            d = None
        if d is None or d < local_today():
            return None, de_en("Bitte ein Enddatum ab heute angeben.", "Please give an end date from today on.")
        return f"d:{d.isoformat()}", None
    return None, de_en("Bitte eine Laufzeit wählen.", "Please choose a period.")


# --- an account's type and period -----------------------------------------------------------------------------------

def _row(user):
    from app import passwords

    db.session.flush()
    return passwords.login_row(user, create=True)


def assign(user, t, spec, by=None, supervisor=None, start=True, origin_group=None, now=None, log=True):
    """Give the account the type `t` with the period `spec`: started now (start) or waiting for its start (an account a
    site admin made, until its password is set). The survey types, "create" among them, go through
    app/kind_access.py (the caller)."""
    row = _row(user)
    row.account_type_id = t.id if t is not None else None
    set_supervisors(user, [supervisor] if (t is not None and t.supervised and supervisor is not None
                                           and supervisor.id != user.id) else [])
    if origin_group is not None:
        row.origin_group_id = origin_group.id
    spec = spec or ""
    if start:
        begin(row, spec, now)
    else:
        row.valid_spec, row.valid_from, row.valid_until = spec or None, None, None
    row.expiry_noticed = None
    if log:
        from app import security_log

        security_log.event("account_type_set", account=user.id, by=by.id if by is not None else None,
                           kind=t.key or str(t.id) if t is not None else "none", reason=spec or "open")


def begin(row, spec, now=None):
    now = now or utcnow()
    row.valid_spec, row.valid_from, row.valid_until = None, now, end_for(spec, now)


def start_pending(user, now=None):
    """The period of an account a site admin made starts when its password is set (auth.set_password)."""
    row = db.session.get(UserLogin, user.id)
    if row is not None and row.valid_from is None and row.valid_spec is not None:
        begin(row, row.valid_spec, now)


def _course_of(user):
    """The course (group with a type) the new account came in through: a member entry made before the account."""
    return db.session.scalar(db.select(WorkGroup).join(WorkGroupMember).filter(
        WorkGroupMember.user_id == user.id, WorkGroup.account_type_id.is_not(None)).order_by(WorkGroupMember.id.desc()))


def course_spec(group):
    if group.course_until is not None:
        return f"d:{to_local(group.course_until).date().isoformat()}"
    t = db.session.get(AccountType, group.account_type_id) if group.account_type_id else None
    return t.duration if t is not None and not needs_input(t.duration) else ""


def start_new(user, now=None):
    """Type and period of a new account (auth._sign_in, invites.accept, joinlinks): what "Person hinzufügen" chose for
    its iD; else the course it came in through; else the type for new accounts of its home organisation, else of the
    platform. Returns the type (its survey types are the base of kind_access.start_new). Its home organisation first
    (app/orgs.py: the sign-in list's choice, the organisation of the group it came in through, or a guest)."""
    from app import orgs

    orgs.start_new(user)
    listed = db.session.get(SignInOrcid, user.orcid) if user.orcid else None
    if listed is not None and listed.account_type_id is not None:
        t = db.session.get(AccountType, listed.account_type_id)
        if t is not None:
            supervisor = db.session.get(User, listed.added_by_id) if listed.added_by_id else None
            assign(user, t, listed.valid_spec if listed.valid_spec is not None else t.duration, supervisor=supervisor,
                   now=now, log=False)
            return t
    course = _course_of(user)
    if course is not None:
        t = db.session.get(AccountType, course.account_type_id)
        if t is not None:
            lead = next((m.account for m in course.members if m.role == "lead" and m.account is not None), None)
            assign(user, t, course_spec(course), supervisor=lead, origin_group=course, now=now, log=False)
            return t
    t = default_type(orgs.home_id(user))  # the type for new accounts of its home organisation (2026-10-16)
    if t is not None and (t.duration and not needs_input(t.duration) or t.supervised):
        # only when the type for new accounts sets something: an ORCID account nobody changed anything about keeps
        # having no UserLogin row (models.UserLogin), and an account without a type counts as the default one
        assign(user, t, t.duration, now=now, log=False)
    return t


def new_account_levels(user, t):
    """The survey types of a new account: kind_access.start_new with the type's survey types as base."""
    from app import kind_access

    kind_access.start_new(user, base=t.kinds if t is not None else kind_access.default())


def beyond_type_org(user, t):
    """Whether a change of the type by the organisation's admins stops short of this account (audit 2026-10-18,
    follow-up of AUTHZ-01): the account is not one they manage as a whole (orgs.manages) because it is a site admin's,
    an admin's of the organisation, at home elsewhere or has roles beyond the organisation. Carrying the type over
    (apply_to_all) and the deletion after its end (delete_due) leave it as it is until a site admin acts."""
    from app import orgs

    org = orgs.get(org_of(t))
    if org is None or orgs._site_admin(user) or orgs.home_id(user) != org.id or orgs.is_admin_of(user, org.id):
        return True
    return bool(orgs.outside_roles(user, org))


def exempt_of(t, by=None):
    """The accounts of the type a change by `by` (an admin of its organisation) leaves out: [user]. A site admin's
    change leaves out nobody; `by` itself is never left out."""
    from app import orgs

    if by is not None and orgs._site_admin(by):
        return []
    users = db.session.scalars(db.select(User).join(UserLogin, UserLogin.user_id == User.id)
                               .where(UserLogin.account_type_id == t.id))
    return [u for u in users if (by is None or u.id != by.id) and beyond_type_org(u, t)]


def apply_to_all(t, by):
    """N2: carry a changed type over to the accounts that have it (survey types, "create" among them); by an admin of
    the organisation only to the accounts they manage (exempt_of). Returns (carried over, left out)."""
    from app import kind_access

    left_out = {u.id for u in exempt_of(t, by)}
    n = 0
    for row in db.session.scalars(db.select(UserLogin).filter_by(account_type_id=t.id)):
        user = db.session.get(User, row.user_id)
        if user is None or user.id in left_out:
            continue
        kind_access.store(user, t.kinds, by)
        n += 1
    return n, len(left_out)


# --- end and reopening ----------------------------------------------------------------------------------------------

def is_expired(row, now=None):
    return row is not None and row.valid_until is not None and row.valid_until < (now or utcnow())


def expired_by_period(row):
    """Blocked because its period ended (and not by an admin)."""
    return row is not None and (row.block_reason == EXPIRED or (row.status == UserLogin.ACTIVE and is_expired(row)))


def days_left(row, now=None):
    if row is None or row.valid_until is None:
        return None
    return (to_local(row.valid_until).date() - local_today(now)).days


def contact_of(user):
    """Who the person asks once the account ended: supervisor, the lead of its course, or the platform's admins."""
    row = db.session.get(UserLogin, user.id)
    sups = supervisors_of(user)
    if sups:
        return de_en(" oder ", " or ").join(s.display_name for s in sups)
    if row is not None and row.origin_group_id:
        group = db.session.get(WorkGroup, row.origin_group_id)
        lead = next((m for m in group.members if m.role == "lead"), None) if group is not None else None
        if lead is not None:
            return lead.name or de_en("die Leitung von „{g}“", "the lead of “{g}”", g=group.name)
    return de_en("die Admins der Plattform", "the platform admins")


def ended_said(user):
    """The message at a sign-in after the end (N12)."""
    row = db.session.get(UserLogin, user.id)
    end = fmt(row.valid_until, with_time=False, lang=__import__("app.lang", fromlist=["current"]).current()) \
        if row is not None and row.valid_until else ""
    return de_en("Ihr Zugang ist am {d} abgelaufen. Bitte wenden Sie sich an {who}.",
                 "Your access ended on {d}. Please contact {who}.", d=end, who=contact_of(user))


def extend(user, spec, by, now=None):
    """A new end: "verlängern um …", a new date or without end (N5). An account blocked because its period ended
    opens again; one an admin blocked stays blocked."""
    now = now or utcnow()
    row = _row(user)
    unit, value = parse(spec)
    if unit in ("m", "w") and value is not None:
        base = row.valid_until if row.valid_until is not None and row.valid_until > now else now
        row.valid_until = end_for(spec, base)
    else:
        row.valid_until = end_for(spec, now)
    if row.valid_from is None and row.valid_spec is None:
        row.valid_from = now
    if row.valid_spec is not None and row.valid_from is None:
        row.valid_spec = spec or None  # still waiting for its start: the new period waits as well
        row.valid_until = None
    row.expiry_noticed = None
    if row.block_reason == EXPIRED and (row.valid_until is None or row.valid_until > now):
        row.status, row.block_reason = UserLogin.ACTIVE, None
        row.status_changed_at, row.status_changed_by_id = now, by.id if by is not None else None
    from app import security_log

    security_log.event("account_extended", account=user.id, by=by.id if by is not None else None, reason=spec or "open")


def change_type(user, t, by):
    """Another type on the account's page (Masterarbeit becomes Mitarbeitende): its survey types and its right to
    create come along, the period stays (change it beside)."""
    from app import kind_access

    row = _row(user)
    row.account_type_id = t.id if t is not None else None
    if t is None or not t.supervised:  # e.g. a thesis candidate taken on as staff: no supervisors (Ralf 2026-10-18)
        set_supervisors(user, [])
    if t is not None:
        kind_access.store(user, t.kinds, by)
    from app import security_log

    security_log.event("account_type_set", account=user.id, by=by.id if by is not None else None,
                       kind=(t.key or str(t.id)) if t is not None else "none")


def reopen_for_course(user, group, by=None, now=None):
    """N8: an account whose period ended, of the course's type, joins a new course: it opens with the course's end.
    True when it did."""
    row = db.session.get(UserLogin, user.id)
    if (row is None or group.account_type_id is None or row.account_type_id != group.account_type_id
            or not expired_by_period(row)):
        return False
    now = now or utcnow()
    end = end_for(course_spec(group), now)
    if end is not None and end <= now:
        return False
    row.valid_until, row.origin_group_id, row.expiry_noticed = end, group.id, None
    row.status, row.block_reason, row.status_changed_at = UserLogin.ACTIVE, None, now
    row.status_changed_by_id = by.id if by is not None else None
    from app import security_log

    security_log.event("account_extended", account=user.id, by=by.id if by is not None else None, target=f"group {group.id}")
    return True


# --- supervisor ---------------------------------------------------------------------------------------------------

def supervisors_of(user):
    """The people who supervise the account, in the order they were given (several since 2026-10-18, Ralf). A
    supervisor only in user_login.supervisor_id (written by the code before during a rolling deploy) counts too."""
    if user is None or user.id is None:
        return []
    out = list(db.session.scalars(db.select(User).join(AccountSupervisor, AccountSupervisor.supervisor_id == User.id)
                                  .where(AccountSupervisor.user_id == user.id)
                                  .order_by(AccountSupervisor.created_at, AccountSupervisor.id)))
    row = db.session.get(UserLogin, user.id)
    if row is not None and row.supervisor_id and all(u.id != row.supervisor_id for u in out):
        first = db.session.get(User, row.supervisor_id)
        if first is not None:
            out.insert(0, first)
    return out


def supervisor_of(user):
    """The first supervisor, or None."""
    sups = supervisors_of(user)
    return sups[0] if sups else None


def set_supervisors(user, people):
    """The account's supervisors become `people` (never the account itself, none twice); user_login.supervisor_id
    keeps the first for the code before (migration 0081)."""
    wanted = []
    for p in people:
        if p is not None and p.id != user.id and all(p.id != w.id for w in wanted):
            wanted.append(p)
    rows = {r.supervisor_id: r for r in db.session.scalars(db.select(AccountSupervisor).filter_by(user_id=user.id))}
    for sid, r in rows.items():
        if all(p.id != sid for p in wanted):
            db.session.delete(r)
    for p in wanted:
        if p.id not in rows:
            db.session.add(AccountSupervisor(user_id=user.id, supervisor_id=p.id))
    row = _row(user)
    row.supervisor_id = wanted[0].id if wanted else None
    db.session.flush()
    return wanted


def add_supervisor(survey, user):
    """N10: every supervisor of `user` becomes admin of the survey `user` just created (not twice, not oneself).
    Returns the members added."""
    from app.models import SurveyMember

    from app import orgs

    added = []
    for sup in supervisors_of(user):
        if sup.id == user.id or any(m.user_id == sup.id for m in survey.members):
            continue
        m = SurveyMember(user=sup, name=sup.name, affiliation=orgs.affiliation_of(sup),
                         invited_by_id=user.id, position=len(survey.members), role="admin")
        survey.members.append(m)
        added.append(m)
    return added


def supervised_by(user):
    """The accounts this person supervises, with their end (for the hint on the start page and "Konto")."""
    mine = db.select(AccountSupervisor.user_id).where(AccountSupervisor.supervisor_id == user.id)
    rows = db.session.execute(db.select(User, UserLogin).join(UserLogin, UserLogin.user_id == User.id)
                              .filter(db.or_(UserLogin.supervisor_id == user.id, User.id.in_(mine)))
                              .order_by(UserLogin.valid_until, User.name)).all()
    return rows


# --- the hourly sweep -------------------------------------------------------------------------------------------------

def expire_due(now=None):
    """Block the accounts whose period ended (lifecycle._rare_sweeps). Returns how many."""
    from app import accounts

    now = now or utcnow()
    n = 0
    for row in db.session.scalars(db.select(UserLogin).where(UserLogin.valid_until.is_not(None),
                                                             UserLogin.valid_until < now,
                                                             UserLogin.status != UserLogin.BLOCKED)):
        user = db.session.get(User, row.user_id)
        if user is None or user.orcid in current_app.config["ADMIN_ORCIDS"]:
            continue
        accounts.set_blocked(user, True, None, reason=EXPIRED)
        from app import security_log

        security_log.event("account_expired", account=user.id)
        n += 1
    db.session.commit()
    return n


def notify_due(now=None):
    """N11: with mail switched on, one mail MAIL_DAYS before the end, to the person and the supervisor."""
    from app import mail

    if not mail.enabled():
        return 0
    now = now or utcnow()
    soon = now + timedelta(days=MAIL_DAYS + 1)
    n = 0
    for row in list(db.session.scalars(db.select(UserLogin).where(
            UserLogin.valid_until.is_not(None), UserLogin.valid_until > now, UserLogin.valid_until < soon,
            UserLogin.status == UserLogin.ACTIVE,
            db.or_(UserLogin.expiry_noticed.is_(None), UserLogin.expiry_noticed > MAIL_DAYS)))):
        uid = row.user_id
        user = db.session.get(User, uid)
        left = days_left(row, now)
        if user is None or left is None or left > MAIL_DAYS:
            continue
        try:  # one at a time: a mail that cannot go out leaves the others (audit 2026-10-18, FUNC-02)
            row.expiry_noticed = MAIL_DAYS
            _mail_notice(user, row)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            _one_failed(e, "notice", uid)
            continue
        n += 1
    db.session.commit()
    return n


def _one_failed(error, what, user_id):
    """An account the sweep could not handle: logged, the others go on; a busy database puts off the whole run."""
    from app import lifecycle

    if lifecycle._transient(error):
        raise error
    current_app.logger.exception("Account sweep (%s) failed for account %s; the others go on", what, user_id)


def _mail_notice(user, row):
    from app import brand, lang, mail

    end_de = fmt(row.valid_until, with_time=False, lang="de")
    end_en = fmt(row.valid_until, with_time=False, lang="en")
    if row.email:
        with mail.in_language(lang.of(user)):
            mail.send(row.email, de_en("Askurious: Ihr Zugang endet am {d}", "Askurious: your access ends on {d}",
                                       d=end_de if not lang.english() else end_en),
                      de_en("Guten Tag {name},\n\nIhr Zugang zu Askurious endet am {d}. Danach ist das Konto gesperrt. "
                            "Exportieren Sie bis dahin, was Sie brauchen, oder fragen Sie {who} nach einer Verlängerung."
                            "\n\n{link}",
                            "Hello {name},\n\nyour access to Askurious ends on {d}. After that the account is blocked. "
                            "Export what you need before then, or ask {who} for an extension.\n\n{link}",
                            name=user.name, d=end_de if not lang.english() else end_en, who=contact_of(user),
                            link=brand.public_base()) + mail.signature())
    for sup in supervisors_of(user):  # every supervisor (several since 2026-10-18)
        sup_row = db.session.get(UserLogin, sup.id)
        if sup_row is None or not sup_row.email:
            continue
        with mail.in_language(lang.of(sup)):
            mail.send(sup_row.email, de_en("Askurious: Zugang von {name} endet", "Askurious: access of {name} ends",
                                           name=user.name),
                      de_en("Guten Tag {s},\n\nder Zugang von {name}, die oder den Sie in Askurious betreuen, endet am "
                            "{d}. Verlängern kann ihn ein Admin der Plattform.",
                            "Hello {s},\n\nthe access of {name}, whom you supervise in Askurious, ends on {d}. A platform "
                            "admin can extend it.", s=sup.name, name=user.name,
                            d=end_de if not lang.english() else end_en) + mail.signature())


def _type_of_row(row):
    return db.session.get(AccountType, row.account_type_id) if row.account_type_id else None


def deletion_due(row, now=None):
    """The account ended long enough ago for its type's months (N13)."""
    if row is None or row.block_reason != EXPIRED or row.valid_until is None:
        return False
    t = _type_of_row(row)
    if t is None or not t.delete_months:
        return False
    from app.retention import _add_months

    return _add_months(row.valid_until, t.delete_months) < (now or utcnow())


def open_points(plan):
    """What deleting would have to hand over: running surveys or surveys with data nobody else holds, a successor to
    choose in a survey or a group. Without them the sweep deletes by itself."""
    return [k for k in ("running", "data", "successor", "group_successor") if plan.get(k)]


def delete_due(now=None):
    """N13: delete the accounts past their type's months after the end, when nothing has to be handed over. Returns
    how many went; the others stay listed as "Löschung fällig"."""
    from app import accounts
    from app.models import AccessLog

    now = now or utcnow()
    n = 0
    for row in list(db.session.scalars(db.select(UserLogin).where(UserLogin.block_reason == EXPIRED))):
        if not deletion_due(row, now):
            continue
        user = db.session.get(User, row.user_id)
        if user is None or user.orcid in current_app.config["ADMIN_ORCIDS"]:
            continue
        if open_points(accounts.plan(user)):
            continue
        t = _type_of_row(row)
        if t is not None and beyond_type_org(user, t):
            # its deletion period is the organisation's setting, read now: it does not reach an account with roles
            # beyond the organisation; it stays listed as "Löschung fällig" for a site admin
            continue
        uid, number = user.id, user.number or user.id
        try:
            accounts.delete(user, "period")  # commits; DeletedAccount.by "period"
        except accounts.DeleteError:
            db.session.rollback()
            continue
        except Exception as e:  # one account that fails leaves the others (audit 2026-10-18, FUNC-02)
            db.session.rollback()
            _one_failed(e, "delete", uid)
            continue
        # the log of data-protection actions (Administration > Protokoll): the platform deleted it, nobody signed in
        db.session.add(AccessLog(at=utcnow(), user_id=None, who="Askurious", action="accounts.deleted_after_period",
                                 detail=f"user={number}", status=200))
        from app import security_log

        security_log.event("account_deleted_after_period", account=uid)
        db.session.commit()
        n += 1
    return n


def run(now=None):
    """Hourly (lifecycle._rare_sweeps): notices, ends, deletions, each on its own: a notice that keeps failing (the
    mail) no longer stops the ends and deletions after it (audit 2026-10-18, FUNC-02)."""
    from app import lifecycle

    for name, step in (("notify_due", notify_due), ("expire_due", expire_due), ("delete_due", delete_due)):
        lifecycle._step(f"account_types.{name}", step, now)


# --- what a page shows ---------------------------------------------------------------------------------------------

def notice_for(user, now=None):
    """The hint on top of every page for the person (N11): (days left, end, who to ask) from 30 days before, or None.
    Shown again at 14 and 3 days after being closed (the step is part of the key)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    row = db.session.get(UserLogin, user.id)
    left = days_left(row, now)
    if left is None or left > NOTICE_DAYS[0] or left < 0:
        return None
    step = next(d for d in reversed(NOTICE_DAYS) if left <= d)
    return {"left": left, "end": row.valid_until, "who": contact_of(user), "step": step}


def summary(row):
    """Type and end of an account for the lists: {"type": AccountType|None, "until": datetime|None, "pending": spec,
    "expired": bool, "left": days}."""
    if row is None:
        return {"type": None, "until": None, "pending": None, "expired": False, "left": None}
    return {"type": _type_of_row(row), "until": row.valid_until, "pending": row.valid_spec,
            "expired": expired_by_period(row), "left": days_left(row)}


# --- the form of a type (Verwaltung > Nutzertypen, app/org_admin.py) --------------------------------------------------

def exempt_said(n):
    return de_en("{n} Konten dieses Typs sind ausgenommen: Admins der Organisation, Konten mit Heimat anderswo oder mit "
                 "Rollen außerhalb der Organisation. Übertragen und Löschen nach dem Ende gelten für sie nicht; das "
                 "entscheidet ein Admin der Plattform.",
                 "{n} accounts of this type are left out: admins of the organisation, accounts at home elsewhere or "
                 "with roles beyond the organisation. Carrying over and deleting after the end do not apply to them; "
                 "a platform admin decides that.", n=n)


def form_action(form, org_id, by):
    """Save one type of the organisation, add one (empty or from a template), make one the type for new accounts,
    hide, delete (only without accounts and not the type for new accounts), carry a change over to its accounts (N2).
    Returns [(message, category)]; raises 404 for a type of another organisation, 400 for an unknown action."""
    from flask import abort

    from app import kind_access, orgs

    action = form.get("action")
    t = get(form.get("type_id"))
    if t is not None and org_of(t) != orgs.key(org_id):
        abort(404)
    if action not in ("new", "template") and t is None:
        abort(404)
    if action == "template":
        t = from_template(form.get("template"), org_id)
        if t is None:
            abort(400)
        db.session.commit()
        return [(de_en("Nutzertyp „{t}“ aus der Vorlage angelegt.", "User type “{t}” created from the template.",
                       t=name(t)), "success")]
    if action in ("save", "new"):
        name_de = " ".join((form.get("name_de") or "").split())[:80]
        name_en = " ".join((form.get("name_en") or "").split())[:80]
        mode = form.get("duration_mode") or "none"
        n = (form.get("duration_n") or "").strip()
        if not name_de:
            return [(de_en("Bitte einen Namen angeben.", "Please give a name."), "error")]
        if mode in ("m", "w"):
            if n and not (n.isdigit() and 1 <= int(n) <= (MAX_MONTHS if mode == "m" else MAX_WEEKS)):
                return [(de_en("Bitte eine Laufzeit von 1 bis {n} angeben oder das Feld leer lassen.",
                               "Please give a period from 1 to {n} or leave the field empty.",
                               n=MAX_MONTHS if mode == "m" else MAX_WEEKS), "error")]
            duration = f"{mode}:{n}"
        elif mode == "d":
            duration = "d:"
        else:
            duration = ""
        months = (form.get("delete_months") or "").strip()
        if months and not (months.isdigit() and 1 <= int(months) <= 120):
            return [(de_en("Löschen nach: 1 bis 120 Monate, oder leer für nie.",
                           "Delete after: 1 to 120 months, or empty for never."), "error")]
        if t is None:
            key = orgs.key(org_id)
            t = AccountType(org_id=None if key == orgs.default_id() else key, position=len(all_types(org_id=key)))
            db.session.add(t)
        t.name_de, t.name_en, t.duration = name_de, name_en, duration
        t.kinds = kind_access.from_form(form)
        t.supervised = form.get("supervised") == "1"
        t.delete_months = int(months) if months else None
        db.session.commit()
        return [(de_en("Nutzertyp „{t}“ gespeichert. Er gilt für Konten, die ab jetzt mit ihm dazukommen.",
                       "User type “{t}” saved. It applies to accounts that join with it from now on.", t=name(t)),
                 "success")]
    if action == "default":
        for other in all_types(org_id=org_of(t)):
            other.is_default = other.id == t.id
        t.hidden = False
        db.session.commit()
        return [(de_en("Neue Konten bekommen jetzt den Nutzertyp „{t}“.", "New accounts now get the user type “{t}”.",
                       t=name(t)), "success")]
    if action == "hide":
        if t.is_default:
            return [(de_en("Den Typ für neue Konten können Sie nicht ausblenden.",
                           "The type for new accounts cannot be hidden."), "error")]
        t.hidden = not t.hidden
        db.session.commit()
        return []
    if action == "delete":
        if t.is_default or count_of(t):
            return [(de_en("Nur ein Typ ohne Konten, der nicht für neue Konten gilt, lässt sich löschen; sonst ausblenden.",
                           "Only a type without accounts that does not apply to new accounts can be deleted; otherwise "
                           "hide it."), "error")]
        db.session.delete(t)
        db.session.commit()
        return [(de_en("Nutzertyp gelöscht.", "User type deleted."), "success")]
    if action == "apply_all":
        n, left_out = apply_to_all(t, by)
        db.session.commit()
        said = [(de_en("Befragungstypen und Anlegerecht von „{t}“ auf {n} Konten übertragen; die Laufzeiten bleiben.",
                       "Survey types and right to create of “{t}” carried over to {n} accounts; the periods stay.",
                       t=name(t), n=n), "success")]
        if left_out:
            said.append((exempt_said(left_out), "info"))
        return said
    abort(400)
