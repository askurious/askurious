"""Which ORCID iDs may sign in by themselves, kept in the database (Ralf, 2026-10-06): the sign-in list (SignInOrcid).
ADMIN_ORCIDS stay in the configuration: the way in when the database says no to everyone (app/auth.py on_whitelist).

The list is filled by "Person hinzufügen" (Administration > Personen, Verwaltung > Personen) and by the registration
links (app/signup_links.py); it keeps the choices for the first sign-in (survey types, user type, organisation). It is
no longer shown as a list of its own: an iD without an account yet is an open invitation of its organisation
(app/org_invites.py), an account is taken out by blocking it.

Next to it one switch of the platform (Ralf, 2026-10-29): sign-in with ORCID "only with an invitation" or "every
person" (SiteSetting "all_orcids" = "1"). The middle state "up to a limit", with its waiting list, went then
(migration 0107 turned it off and gave the accounts that came in by it an admin's yes, SignInGrant); whoever should
come into a particular organisation gets a registration link.

The list is about signing in only. Creating surveys is the level "create" on the account (app/kind_access.py): a new
account gets it at its first sign-in (NEW_ACCOUNTS_MAY_CREATE), an account that is there already gets it when its
iD is added; a site admin can take it back on the account's page."""
import re

from flask import current_app, g, has_request_context

from app.extensions import db
from app.models import ORCID_RE, SignInOrcid, SiteSetting, User, utcnow

EVERYONE = "all_orcids"  # "1": every ORCID holder may sign in; anything else: only with an invitation

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


def everyone():
    """Every ORCID holder may sign in. Asked on every page (auth.on_whitelist): once per request."""
    if has_request_context():
        if "_signin_everyone" not in g:
            g._signin_everyone = setting(EVERYONE) == "1"
        return g._signin_everyone
    return setting(EVERYONE) == "1"


def set_everyone(on, by):
    _set(EVERYONE, "1" if on else "0", by)
    if has_request_context():
        g.pop("_signin_everyone", None)


def came_in(user):
    """An account signing in with ORCID (auth._sign_in, before the commit) while "every person" is on, and with no
    other reason to be let in, gets an admin's yes (SignInGrant), so it keeps its way in when the switch goes to
    "only with an invitation" (Ralf, 2026-10-29). Accounts with another reason (ADMIN_ORCIDS, the list, a survey, a
    group) need none."""
    from app.models import SignInGrant, SurveyMember, WorkGroupMember

    if not everyone() or not user.orcid or user.orcid in current_app.config["ADMIN_ORCIDS"] or listed(user.orcid):
        return
    if db.session.get(SignInGrant, user.id) is not None:
        return
    for model in (SurveyMember, WorkGroupMember):
        if db.session.scalar(db.select(model.id).filter_by(user_id=user.id).limit(1)) is not None:
            return
    db.session.add(SignInGrant(user_id=user.id))
    db.session.flush()


def listed(orcid):
    return db.session.get(SignInOrcid, orcid) is not None


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
