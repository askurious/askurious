"""Rights per survey type: what an account may see or edit of questionnaires, live polls, interview guides and diary
studies (konzept-rechte-befragungstypen-2026-10-15.md, Ralf 2026-10-15 "alle wie empfohlen").

Each account has one of four levels per type: "off" (the type is nowhere: no button, no filter, no template, no
example, no help section, and surveys of the type shared with the person are hidden), "view" (shared surveys and
examples of the type open, at most in the role "Ansehen"), "edit" (works on shared surveys of the type, creates none)
and "create" (also creates surveys of the type). The level is a cap on the role of app/access.py, so every page, the
export, data tokens and the AI agents follow it. "create" took the place of the separate right to create surveys
(UserLogin.may_create) on 2026-10-18 (konzept-stufe-anlegen-2026-10-18.md, Ralf "wie empfohlen"): what needs no type
(groups, designs, mail invitations) needs "create" for any type (auth.may_create_surveys).

Stored are only the levels that differ from "edit" (UserLogin.kinds): accounts from before, and every account
without an entry, keep all four types to edit (Ralf: existing users have all four rights, they can be taken away
later); migration 0080 wrote "create" where the former right to create held. A group with "darf Befragungen anlegen"
raises "edit" to "create" once, when someone joins it (joined), not for as long as they are a member (E3).
Site admins (ADMIN_ORCIDS) are never capped. The diary study stays experimental besides (experimental "diary").

New accounts start with the default of Administration > Personen (SiteSetting "new_account_kinds"), or with what
"Person hinzufügen" chose for an iD on the sign-in list (SignInOrcid.kinds), or with the preset of a group they come
in through (WorkGroup.kinds_preset); an invitation to a survey raises its type as far as the role needs (start_new).
"""
from flask import current_app, g, has_app_context

from app.extensions import db, json_given, json_missing
from app.lang import de_en

KINDS = ("survey", "live", "interview", "diary")  # Survey.kind
OFF, VIEW, EDIT, CREATE = "off", "view", "edit", "create"
LEVELS = (OFF, VIEW, EDIT, CREATE)
_RANK = {OFF: 0, VIEW: 1, EDIT: 2, CREATE: 3}
SETTING = "new_account_kinds"  # SiteSetting: the default for new accounts, "live=view,diary=off"


def label(kind):
    return {"survey": de_en("Fragebogen", "Questionnaire"), "live": de_en("Live-Abstimmung", "Live poll"),
            "interview": de_en("Interviewleitfaden", "Interview guide"),
            "diary": de_en("Tagebuchstudie", "Diary survey")}[kind]


def plural(kind):
    return {"survey": de_en("Fragebögen", "Questionnaires"), "live": de_en("Live-Abstimmungen", "Live polls"),
            "interview": de_en("Interviewleitfäden", "Interview guides"),
            "diary": de_en("Tagebuchstudien", "Diary surveys")}[kind]


def level_label(level):
    return {OFF: de_en("Aus", "Off"), VIEW: de_en("Ansehen", "View"), EDIT: de_en("Bearbeiten", "Edit"),
            CREATE: de_en("Anlegen", "Create")}[level]


def clean(levels):
    """Only the known types with a known level other than "edit", or None when nothing differs (the stored form)."""
    out = {k: v for k, v in (levels or {}).items() if k in KINDS and v in (OFF, VIEW, CREATE)}
    return out or None


def full(stored):
    """All four types with their level, from the stored form."""
    stored = stored if isinstance(stored, dict) else {}
    return {k: stored.get(k) if stored.get(k) in LEVELS else EDIT for k in KINDS}


def from_form(form, prefix="kind_"):
    """The four selects of a form (kind_survey=edit, ...) in the stored form; a missing field counts as "edit"."""
    return clean({k: form.get(prefix + k) for k in KINDS})


# the cap of an organisation (Organisation.kinds_cap): stored are the levels below "create", a missing type has no cap
# (caps from before 2026-10-18 knew no "create" and stored nothing for "edit", which then meant no cap either)

def cap_clean(levels):
    out = {k: v for k, v in (levels or {}).items() if k in KINDS and v in (OFF, VIEW, EDIT)}
    return out or None


def cap_full(stored):
    stored = stored if isinstance(stored, dict) else {}
    return {k: stored.get(k) if stored.get(k) in LEVELS else CREATE for k in KINDS}


def cap_from_form(form, prefix="kind_"):
    return cap_clean({k: form.get(prefix + k) for k in KINDS})


def encode(levels):
    return ",".join(f"{k}={v}" for k, v in (clean(levels) or {}).items())


def decode(text):
    pairs = (part.split("=", 1) for part in (text or "").split(",") if "=" in part)
    return clean({k.strip(): v.strip() for k, v in pairs})


def _cache():
    return g.setdefault("kind_levels", {}) if has_app_context() else {}


def forget():
    """Read the levels anew in this request (after an admin changed them)."""
    if has_app_context():
        g.pop("kind_levels", None)


def _is_site_admin(orcid):
    return bool(orcid) and orcid in current_app.config["ADMIN_ORCIDS"]


def levels_of_orcid(orcid):
    """{type: level} of the account with this key (an iD, "u<id>" or "q-…"); all "edit" for a key without an
    account. See levels_of_user."""
    from app import people_keys

    user = people_keys.account(orcid, create=False) if orcid else None
    if user is None:
        return {k: CREATE for k in KINDS} if _is_site_admin(orcid) else full(None)
    return levels_of_user(user)


def levels_of_user(user):
    """{type: level} of the account; all "create" for site admins, all "edit" for an account without an entry. Read
    once per request and account (also an account without ORCID iD, since 2b)."""
    cache = _cache()
    key = ("account", user.id)
    if key not in cache:
        if _is_site_admin(user.orcid):
            levels_ = {k: CREATE for k in KINDS}
        else:
            from app.models import UserLogin

            stored = db.session.scalar(db.select(UserLogin.kinds).filter(UserLogin.user_id == user.id))
            levels_ = full(stored)
            capped = home_cap(user)  # the home organisation's cap (A16): the lower of both counts
            levels_ = {k: v if _RANK[v] <= _RANK[capped[k]] else capped[k] for k, v in levels_.items()}
        cache[key] = levels_
    return cache[key]


def home_cap(person):
    """The highest level per type the home organisation allows this iD: its kinds_cap, and "edit" at most when it lets
    nobody create surveys (Organisation.may_create False). The guests' organisation says False as well, but a guest
    creates when a site admin set "create" on the account (A5), so it caps nothing there."""
    from app import orgs

    return org_cap(orgs._home_org(person))  # a key or a User


def org_cap(org):
    """The highest level per type an organisation allows its accounts (home_cap); all "create" without one."""
    from app import orgs

    if org is None:
        return cap_full(None)
    capped = cap_full(org.kinds_cap)
    if org.may_create is False and org.key != orgs.GUEST:
        capped = {k: v if _RANK[v] <= _RANK[EDIT] else EDIT for k, v in capped.items()}
    return capped


def capped(levels_, cap_):
    """Per type the lower of a full set of levels and a cap (both full)."""
    return {k: v if _RANK[v] <= _RANK[cap_[k]] else cap_[k] for k, v in levels_.items()}


def levels(user):
    if user is None or not getattr(user, "is_authenticated", True) or getattr(user, "id", None) is None:
        return full(None)
    return levels_of_user(user)


def level(user, kind):
    return levels(user).get(kind, EDIT)


def restricted(user):
    """Whether any type is below "edit" for this person (else nothing needs filtering)."""
    return any(v != EDIT for v in levels(user).values())


def sees(user, kind):
    return level(user, kind) != OFF


def edits(user, kind):
    return level(user, kind) in (EDIT, CREATE)


def may_create(user, kind):
    """May this person create a survey of this type: "create" for the type."""
    return level(user, kind) == CREATE


def creates_any(user):
    """"create" for at least one type: the right for what needs no type (groups, designs, mail invitations)."""
    return any(v == CREATE for v in levels(user).values())


def visible_kinds(user):
    return [k for k in KINDS if sees(user, k)]


def cap(role, lvl):
    """The role on a survey under the level of its type: none at "off", at most "viewer" at "view"."""
    if role is None or lvl in (EDIT, CREATE):
        return role
    if lvl == OFF:
        return None
    return "viewer"


def kind_clause(kind):
    """SQL: the surveys of this type (as Survey.kind tells them apart)."""
    from app.models import Survey

    if kind == "live":
        return json_given(Survey.live)
    if kind == "interview":
        return db.and_(json_missing(Survey.live), json_given(Survey.interview))
    if kind == "diary":
        return db.and_(json_missing(Survey.live), json_missing(Survey.interview), json_given(Survey.sampling))
    return db.and_(json_missing(Survey.live), json_missing(Survey.interview), json_missing(Survey.sampling))


def hidden_clause(user):
    """SQL: the surveys this person sees, by type; None when every type is visible (no filter needed)."""
    off = [k for k in KINDS if level(user, k) == OFF]
    if not off:
        return None
    return db.not_(db.or_(*(kind_clause(k) for k in off)))


def below_edit_clause():
    """SQL: accounts with a type at "off" or "view" (the filter "Nur eingeschränkte Befragungstypen"); "create" is no
    restriction."""
    from app.models import UserLogin

    text = db.cast(UserLogin.kinds, db.String)
    return db.or_(text.like(f'%"{OFF}"%'), text.like(f'%"{VIEW}"%'))


def refuse(kind):
    """The answer to a request for a type the person may not create or open: 404, as for an unknown page."""
    from flask import abort

    abort(404)


def require_create(user, kind):
    """A type the person cannot even edit is not there (404); that it may not create one the caller says."""
    if not edits(user, kind):
        refuse(kind)


def creation_refused(user, kind):
    """None when the person may create a survey of this type; else 404 for a type it does not even edit, or the
    dashboard with the reason (the pages "neue Befragung" of each type)."""
    if may_create(user, kind):
        return None
    require_create(user, kind)
    from flask import flash, redirect, url_for

    # "Admin der Plattform" and the way to them, as on the dashboard (UI/UX audit 2026-10-09, X72)
    flash(de_en("Ihr Konto ist für die Mitarbeit in Befragungen und Gruppen freigeschaltet, zu denen Sie eingeladen "
                "wurden. Für eigene {kinds} schreiben Sie über „Rückmeldung geben“ (🐞 oben) an die Admins der Plattform.",
                "Your account is enabled for working on surveys and groups you were invited to. For {kinds} of your own, "
                "write to the platform admins via “Give feedback” (🐞 at the top).", kinds=plural(kind)), "error")
    return redirect(url_for("dashboard.index"))


# --- setting the levels ---------------------------------------------------------------------------------------------

def stored(user):
    from app.models import UserLogin

    row = db.session.get(UserLogin, user.id)
    return clean(row.kinds) if row is not None else None


def store(user, levels_, by=None, log=True):
    """Keep these levels for the account (None/all "edit" = all four to edit)."""
    from app import passwords

    db.session.flush()  # a login row added just before (auth._sign_in) is found, not made twice
    row = passwords.login_row(user, create=True)
    before = full(row.kinds)
    row.kinds = clean(levels_)
    forget()
    if log and before != full(row.kinds):
        from app import security_log

        security_log.event("kinds_changed", account=user.id, by=by.id if by is not None else None,
                           kind=encode(row.kinds) or "all")


def set_level(user, kind, lvl, by):
    if kind not in KINDS or lvl not in LEVELS:
        raise ValueError(kind)
    now = full(stored(user))
    now[kind] = lvl
    store(user, now, by)


def default():
    """The levels new accounts get: those of the user type for new accounts (app/account_types.py, N3); before the
    types exist, the former setting of Administration > Personen. None = all four types."""
    from app import account_types

    t = account_types.default_type()
    if t is not None:
        return clean(t.kinds)
    from app import signin

    return decode(signin.setting(SETTING))


def set_default(levels_, by):
    """Kept for the time before the user types: the survey types of new accounts now sit on the type for new
    accounts (Administration > Personen > Nutzertypen)."""
    from app import account_types

    t = account_types.default_type()
    if t is not None:
        t.kinds = clean(levels_)
        return
    from app import signin

    signin._set(SETTING, encode(levels_), by)


def higher(a, b):
    """Per type the higher of both levels (stored forms)."""
    fa, fb = full(a), full(b)
    return clean({k: fa[k] if _RANK[fa[k]] >= _RANK[fb[k]] else fb[k] for k in KINDS})


def give_create(user, by=None, log=True):
    """"edit" becomes "create" for every type (what "darf anlegen" set before): new accounts with
    NEW_ACCOUNTS_MAY_CREATE, the tick of "Person hinzufügen" and of the sign-in list, joining a group with "darf
    Befragungen anlegen". Types at "off" or "view" stay. True when something changed."""
    now = full(stored(user))
    if not any(v == EDIT for v in now.values()):
        return False
    store(user, {k: CREATE if v == EDIT else v for k, v in now.items()}, by, log=log)
    return True


def take_create(user, by=None):
    """"create" becomes "edit" for every type: the person keeps working on what is shared. Kept as an admin's "no"
    (UserLogin.may_create False), which the sign-in list does not overturn (signin._may_create, S17)."""
    from app import passwords

    now = full(stored(user))
    if any(v == CREATE for v in now.values()):
        store(user, {k: EDIT if v == CREATE else v for k, v in now.items()}, by)
    passwords.login_row(user, create=True).may_create = False


def said_no(user):
    """An admin took "create" away ("Nirgends anlegen", or "darf nicht anlegen" before 2026-10-18)."""
    from app.models import UserLogin

    row = db.session.get(UserLogin, user.id)
    return row is not None and row.may_create is False


def joined(group, user, by=None):
    """Someone joined `group` (lead, join link, invitation, list): with "darf Befragungen anlegen" their "edit" becomes
    "create" once, and stays when they leave (E3). Without an account yet, start_new does it at the first sign-in."""
    if user is not None and group.may_create:
        give_create(user, by)


def group_allowed(group, by=None):
    """A site admin or organisation admin switched "darf Befragungen anlegen" on for `group`: its members with an
    account get "create" now, once (switching it off takes nothing back, E3)."""
    if not group.may_create:
        return
    for m in group.members:
        user = m.user
        if user is not None:
            give_create(user, by)


def raised(levels_, kind, role):
    """`levels_` with `kind` at least as high as `role` on a survey of that type needs."""
    need = EDIT if role in ("editor", "admin") else VIEW
    now = full(levels_)
    if _RANK[now[kind]] < _RANK[need]:
        now[kind] = need
    return clean(now)


_DEFAULT = object()


def start_new(user, base=_DEFAULT):
    """The levels of a new account at its first sign-in (auth._sign_in, before the commit): what "Person hinzufügen"
    chose for its iD; else the preset of the groups it is already in (join link or invitation before the account),
    the higher per type of several; else `base`, the survey types of its user type (app/account_types.py), or the
    default. Then raised by the type of each survey it was invited to, as far as its role needs (F8)."""
    from app.models import SignInOrcid, Survey, SurveyMember, WorkGroup, WorkGroupMember

    listed = db.session.get(SignInOrcid, user.orcid) if user.orcid else None
    presets = list(db.session.scalars(db.select(WorkGroup.kinds_preset).join(WorkGroupMember).filter(
        WorkGroupMember.user_id == user.id, WorkGroup.kinds_preset.is_not(None))))
    base = default() if base is _DEFAULT else clean(base)
    if listed is not None and listed.kinds is not None:
        levels_ = clean(listed.kinds)
    elif presets:
        levels_ = clean(presets[0])
        for preset in presets[1:]:
            levels_ = higher(levels_, preset)
    else:
        levels_ = base
    if levels_ is not base:
        # the list's choice or a group's preset says how far each type goes; "create" still comes from the user type
        # where the choice leaves "edit" (as the type's right to create did before 2026-10-18)
        chosen, typed = full(levels_), full(base)
        levels_ = clean({k: CREATE if v == EDIT and typed[k] == CREATE else v for k, v in chosen.items()})
    for survey, role in db.session.execute(db.select(Survey, SurveyMember.role).join(SurveyMember)
                                           .filter(SurveyMember.user_id == user.id)):
        levels_ = raised(levels_, survey.kind, role)
    if levels_:
        store(user, levels_, log=False)
    if db.session.scalar(db.select(WorkGroup.id).join(WorkGroupMember).filter(
            WorkGroupMember.user_id == user.id, WorkGroup.may_create.is_(True)).limit(1)) is not None:
        give_create(user, log=False)  # a member of a group with "darf Befragungen anlegen" before the account (E3)


def affected(user, kind, lvl):
    """The surveys a lower level would take something from: [(survey, role, running, only_admin)] of the surveys of
    this type the person has a role on (own entry or group) above what `lvl` leaves (F4)."""
    from app import access
    from app.models import Survey

    if lvl in (EDIT, CREATE):
        return []
    rows = db.session.scalars(db.select(Survey).filter(Survey.id.in_(access.survey_ids(user, every_kind=True)),
                                                       kind_clause(kind)).order_by(Survey.title))
    out = []
    for survey in rows:
        role = access.own_role_of_user(user, survey)
        if role is None or cap(role, lvl) == role:
            continue
        admins = survey.admins()
        only = role == "admin" and len(admins) == 1 and admins[0].user_id == user.id
        out.append((survey, role, survey.status == Survey.RUNNING, only))
    return out
