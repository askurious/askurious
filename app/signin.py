"""Which ORCID iDs may sign in by themselves, kept in the database and changed in Administration > Personen
(Ralf, 2026-10-06): a list of iDs (SignInOrcid) and a switch "every ORCID holder" (SiteSetting "all_orcids").
Until then both were ALLOWED_ORCIDS in the server's configuration ("*" for the switch); adopt_config takes them
over once at the start of the server and the configuration is not read for them after that. ADMIN_ORCIDS stay in
the configuration: the way in when the database says no to everyone (app/auth.py on_whitelist).

The list is about signing in only. Creating surveys is the level "create" on the account (app/kind_access.py): a new
account gets it at its first sign-in (NEW_ACCOUNTS_MAY_CREATE), an account that is there already gets it when its
iD is added, and when the configuration was taken over; a site admin can take it back on the account's page.

"Every ORCID holder" has three states since 2026-10-10 (gruppen-freischaltung-konzept.md, Stufe 3, E9-E15 as
recommended): off, up to a limit, without a limit. Up to the limit, new accounts that came in only by this switch
(UserLogin.came_via "open") count; a deleted account gives its place back, a blocked one does not. Accounts that are
there already, from the list, invitations and groups do not count and are always let in. When the limit is reached,
a new person goes on the waiting list (SignInWait); raising the limit, or a place that becomes free, lets the first
of them in (admit_waiting). At 80 % and when full the operators hear of it in Slack, once per limit.
Whoever goes on the waiting list sees its own page (auth.waiting_list) with the place in the queue; with e-mail on,
they can leave an address there and get one mail when they are let in (Philipp, 2026-10-13)."""
import re

from flask import current_app, g, has_request_context

from app.extensions import db
from app.models import ORCID_RE, SignInOrcid, SignInWait, SiteSetting, User, UserLogin, is_orcid, utcnow

EVERYONE = "all_orcids"  # "1": every ORCID holder may sign in; "cap": up to the limit LIMIT; else off
TAKEN_OVER = "allowed_orcids_taken_over"  # when ALLOWED_ORCIDS of the configuration was taken over
WAIT_DAYS = 90  # an entry of the waiting list (ORCID iD and name) goes after this long (audit 2026-10-10, L-10)

_URL = re.compile(r"^(?:https?://)?(?:www\.)?orcid\.org/", re.I)


def clean(text):
    """An ORCID iD as people paste it (with the address of the profile, spaces, a small x), or "" if it is none."""
    value = _URL.sub("", (text or "").strip()).strip().strip("/").upper()
    return value if ORCID_RE.match(value) else ""


def setting(key):
    row = db.session.get(SiteSetting, key)
    return row.value if row is not None else None


def _set(key, value, by=None):
    row = db.session.get(SiteSetting, key)
    if row is None:
        row = SiteSetting(key=key)
        db.session.add(row)
    row.value, row.changed_by_id, row.changed_at = value, by.id if by is not None else None, utcnow()
    return row


MODE_OFF, MODE_CAP, MODE_ALL = "off", "cap", "all"
LIMIT = "open_limit"        # the limit for MODE_CAP, as text
SAID = "open_limit_said"    # "<limit>:80" or "<limit>:full": what Slack was told last
STEPS = (50, 100, 200, 500, 1000)
WARN_SHARE = 0.8


def mode():
    """off, cap or all. Asked on every page (auth.on_whitelist): once per request."""
    if has_request_context():
        if "_signin_mode" not in g:
            g._signin_mode = _mode()
        return g._signin_mode
    return _mode()


def _mode():
    value = setting(EVERYONE)
    return MODE_ALL if value == "1" else MODE_CAP if value == "cap" else MODE_OFF


def everyone():
    """Every ORCID holder may sign in, without a limit."""
    return mode() == MODE_ALL


def set_everyone(on, by):
    set_mode(MODE_ALL if on else MODE_OFF, by)


def limit():
    try:
        return max(1, int(setting(LIMIT) or STEPS[0]))
    except ValueError:
        return STEPS[0]


def set_mode(value, by, new_limit=None):
    _set(EVERYONE, {MODE_ALL: "1", MODE_CAP: "cap"}.get(value, "0"), by)
    if new_limit is not None:
        _set(LIMIT, str(int(new_limit)), by)
    g.pop("_signin_mode", None)
    g.pop("_signin_everyone", None)


def open_count():
    """Accounts that came in by "every ORCID holder" alone."""
    return db.session.scalar(db.select(db.func.count(UserLogin.user_id)).where(UserLogin.came_via == "open")) or 0


def _admitted():
    return db.session.scalar(db.select(db.func.count(SignInWait.orcid)).where(SignInWait.admitted_at.is_not(None))) or 0


def _queued():
    return db.session.scalar(db.select(db.func.count(SignInWait.orcid)).where(SignInWait.admitted_at.is_(None))) or 0


def free_places():
    """Places left under the limit; those let in from the waiting list but not signed in yet hold theirs."""
    return limit() - open_count() - _admitted()


PLACES_LOCK = 4716  # pg_advisory_xact_lock key (mailqueue.py has 4714 and 4715)


def lock_places():
    """Before counting the free places for a new account: one first sign-in at a time until its transaction ends
    (the account is made and committed in the same request, auth._sign_in), so two at the same moment cannot both
    take the last place (audit 2026-10-10, A4). The pattern of mailqueue.lock_account."""
    from app import mailqueue

    mailqueue._lock_db(PLACES_LOCK, 1)


def admit_waiting():
    """Let the first of the waiting list in, as many as there are places. Returns how many."""
    free = free_places()
    if free <= 0:
        return 0
    rows = list(db.session.scalars(db.select(SignInWait).where(SignInWait.admitted_at.is_(None))
                                   .order_by(SignInWait.asked_at, SignInWait.orcid).limit(free)))
    for row in rows:
        row.admitted_at = utcnow()
    if rows:
        db.session.commit()
        _tell_admitted(rows)
    return len(rows)


def _tell_admitted(rows):
    """A mail to each of these, let in just now, who left an address on the page of the waiting list; the address
    goes once the mail is handed on (it was kept for this mail alone)."""
    from flask import url_for

    from app import brand, mail
    from app.lang import de_en

    told = [row for row in rows if row.email]
    if not told:
        return
    if mail.enabled() and has_request_context():
        link = brand.public_base() + url_for("auth.sign_in")
        for row in told:
            with mail.in_language(row.lang or "de"):
                subject = de_en("Askurious: Ihr Platz ist frei", "Askurious: your place is free")
                text = de_en("Guten Tag,\n\nSie standen auf der Warteliste von Askurious. Jetzt ist ein Platz für Sie frei: "
                             "Melden Sie sich mit Ihrer ORCID iD {orcid} an, innerhalb von {d} Tagen:\n\n{link}",
                             "Hello,\n\nyou were on the waiting list of Askurious. A place is now free for you: sign in "
                             "with your ORCID iD {orcid} within {d} days:\n\n{link}",
                             orcid=row.orcid, d=WAIT_DAYS, link=link) + mail.signature()
            mail.send(row.email, subject, text)
    for row in told:
        row.email = None
    db.session.commit()


def open_lets_in(orcid):
    """Whether the switch "every ORCID holder" lets this iD in (auth.on_whitelist)."""
    state = mode()
    if state == MODE_ALL:
        return True
    if state != MODE_CAP:
        return False
    if db.session.scalar(db.select(User.id).filter_by(orcid=orcid)) is not None:
        return True  # there already: up to the limit means new accounts only
    row = db.session.get(SignInWait, orcid)
    if row is not None and row.admitted_at is not None:
        return True
    lock_places()
    if free_places() <= 0:
        return False
    if _queued():
        admit_waiting()  # a place became free: it belongs to the first of the list
        row = db.session.get(SignInWait, orcid)
        return row is not None and row.admitted_at is not None
    return True


def wait(orcid, name):
    """Put this iD on the waiting list (once). Returns its place in the queue."""
    row = db.session.get(SignInWait, orcid)
    if row is None:
        row = SignInWait(orcid=orcid, name=(name or "")[:200])
        db.session.add(row)
        db.session.commit()
    return place(row)


def place(row):
    """The place of this entry in the queue, 0 once it was let in."""
    if row.admitted_at is not None:
        return 0
    return db.session.scalar(db.select(db.func.count(SignInWait.orcid)).where(
        SignInWait.admitted_at.is_(None), SignInWait.asked_at <= row.asked_at)) or 1


def leave_email(row, email, lang):
    """The address for the mail when a place is free (the page of the waiting list); None takes it away."""
    row.email, row.lang = email, (lang if email else None)
    db.session.commit()


def waiting():
    return list(db.session.scalars(db.select(SignInWait).order_by(SignInWait.admitted_at.is_(None), SignInWait.asked_at)))


def sweep_waiting(now):
    """Housekeeping (lifecycle._rare_sweeps): entries of the waiting list older than WAIT_DAYS go, also those let in
    that did not sign in within that time (their place is free again). The privacy page names the period (L-10)."""
    from datetime import timedelta

    cutoff = now - timedelta(days=WAIT_DAYS)
    gone = db.session.execute(db.delete(SignInWait).where(
        SignInWait.asked_at < cutoff, db.or_(SignInWait.admitted_at.is_(None), SignInWait.admitted_at < cutoff))
        .execution_options(synchronize_session=False)).rowcount
    if gone:
        db.session.commit()
    return gone


def unwait(orcid):
    row = db.session.get(SignInWait, orcid)
    if row is not None:
        db.session.delete(row)
        db.session.commit()


def came_in(user):
    """A new account at its first sign-in (auth._sign_in, before the commit): note whether it came by "every ORCID
    holder" alone, which counts for the limit, and take it off the waiting list."""
    from app.models import SignInGrant, SurveyMember, WorkGroupMember

    orcid = user.orcid
    row = db.session.get(SignInWait, orcid)
    if row is not None:
        db.session.delete(row)
    if mode() == MODE_OFF or orcid in current_app.config["ADMIN_ORCIDS"] or listed(orcid):
        return
    if (db.session.scalar(db.select(SurveyMember.id).filter_by(user_id=user.id).limit(1)) is not None
            or db.session.scalar(db.select(WorkGroupMember.id).filter_by(user_id=user.id).limit(1)) is not None
            or db.session.scalar(db.select(SignInGrant.user_id).filter_by(user_id=user.id)) is not None):
        return
    login = db.session.get(UserLogin, user.id)
    if login is None:
        login = UserLogin(user_id=user.id)
        db.session.add(login)
    login.came_via = "open"
    db.session.flush()
    if mode() == MODE_CAP:
        _tell_operators()


def level():
    """None, "80" or "full" for the limit now (the hint in the administration)."""
    if mode() != MODE_CAP:
        return None
    used, top = open_count(), limit()
    return "full" if used >= top else "80" if used >= WARN_SHARE * top else None


def _tell_operators():
    """Slack at 80 % and when full, once per limit and level."""
    now = level()
    if now is None:
        return
    key = f"{limit()}:{now}"
    if setting(SAID) in (key, f"{limit()}:full"):
        return
    _set(SAID, key)
    try:
        from app import notify

        notify.signup_limit(open_count(), limit(), now)
    except Exception as e:  # noqa: BLE001 - the note to the operators must not break the sign-in
        current_app.logger.warning("Sign-up limit not reported in Slack: %s", type(e).__name__)


def listed(orcid):
    return db.session.get(SignInOrcid, orcid) is not None


def entries():
    """The list for the administration, newest first, with the account of each iD where there is one."""
    rows = list(db.session.scalars(db.select(SignInOrcid).order_by(SignInOrcid.created_at.desc(), SignInOrcid.orcid)))
    accounts = {u.orcid: u for u in db.session.scalars(db.select(User).filter(User.orcid.in_([r.orcid for r in rows]),
                                                                              User.pending.is_(False)))}
    return [(row, accounts.get(row.orcid)) for row in rows]


def _may_create(users):
    """"create" for these accounts (what being on the list meant until now); an admin's "no" stays (security audit
    2026-10-06, S17: kind_access.said_no)."""
    from app import kind_access

    for user in users:
        if not kind_access.said_no(user):
            kind_access.give_create(user)


def add(orcid, note, by):
    """Let this iD in. Returns the account of the iD if it has one and got the tick to create surveys now."""
    row = db.session.get(SignInOrcid, orcid)
    if row is None:
        db.session.add(SignInOrcid(orcid=orcid, note=(note or "").strip()[:200], added_by_id=by.id))
    elif note:
        row.note = note.strip()[:200]
    user = db.session.scalar(db.select(User).filter_by(orcid=orcid, pending=False))
    ticked = None
    if row is None and user is not None and current_app.config["NEW_ACCOUNTS_MAY_CREATE"]:
        from app import kind_access

        if not kind_access.said_no(user) and kind_access.give_create(user):
            ticked = user
    db.session.commit()
    return ticked


def remove(orcid):
    row = db.session.get(SignInOrcid, orcid)
    if row is not None:
        db.session.delete(row)
        db.session.commit()


def leftover():
    """What still stands in ALLOWED_ORCIDS of the configuration although it is not read any more."""
    return list(current_app.config.get("ALLOWED_ORCIDS") or ())


def adopt_config():
    """Take ALLOWED_ORCIDS over into the database, once, at the start of the server (create_app). The iDs go on
    the list, "*" turns the switch on; accounts the configuration let create surveys (every ORCID account with "*")
    get the tick, so nobody loses anything. Returns how many iDs were taken over, None if done before. Never raises."""
    try:
        if setting(TAKEN_OVER) is not None:
            return None
        values = leftover()
        orcids = list(dict.fromkeys(v for v in (clean(v) for v in values) if v))
        for orcid in orcids:
            if db.session.get(SignInOrcid, orcid) is None:
                db.session.add(SignInOrcid(orcid=orcid, note="aus ALLOWED_ORCIDS übernommen"))
        if "*" in values:
            _set(EVERYONE, "1")
            users = [u for u in db.session.scalars(db.select(User).where(User.pending.is_(False))) if is_orcid(u.orcid)]
        else:
            users = db.session.scalars(db.select(User).filter(User.orcid.in_(orcids), User.pending.is_(False))) if orcids else []
        _may_create(list(users))
        _set(TAKEN_OVER, utcnow().strftime("%Y-%m-%d %H:%M"))
        db.session.commit()
        if values:
            current_app.logger.warning("ALLOWED_ORCIDS taken over into the database (%d iDs%s); it is not read any more",
                                       len(orcids), ", every ORCID holder" if "*" in values else "")
        return len(orcids)
    except Exception:
        db.session.rollback()
        current_app.logger.exception("signin.adopt_config: ALLOWED_ORCIDS was not taken over")
        return 0

