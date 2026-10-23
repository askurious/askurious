"""Names hidden within an organisation (konzept-organisation-sichtbarkeit-einladungen-2026-10-23.md, E15-E23, Ralf
2026-10-23 "wie empfohlen").

An organisation can be set so that its members do not see each other's names: site admins choose on its page
"sichtbar", "verborgen" or "Admins der Organisation entscheiden" (Organisation.names_mode), and in the last case its
admins switch it under Verwaltung > Personen (names_hidden_own). Then a person at home there is shown to others as
"Mitglied 12" (a number per organisation that stays, OrgAlias), without ORCID iD (E16, E22, E23).

Who still sees the name (E17, E18): the person themselves, site admins, admins of that organisation, and leads of a
group of that organisation the person is a member of. Participants always see the names of those responsible for a
survey (E21): the pages of participants are left out, and so are documents that name the responsible people for the
record (with real()).

It works where names are shown: User.display_name and the display_name of rows naming a person ask shown() with the
signed-in person as viewer, so pages, lists, mails sent in a request and exports follow. While no organisation hides
names, shown() returns the name at once (one cached query per request).
"""
from contextlib import contextmanager

from flask import g, has_request_context, request

from app.extensions import db
from app.lang import de_en

MODES = ("shown", "hidden", "admins")  # names_mode of an organisation; None = "shown"
# blueprints of participants and public pages: the names of those responsible stay (E21)
REAL_BLUEPRINTS = {"participate", "public", "diary_api", "interview_remote", "interview_screen_public", "live_public",
                   "board_share", "preview_share", "quota_share", "signup_links", "joinlinks", "invites"}


def mode_label(mode):
    return {"shown": de_en("sichtbar", "visible"), "hidden": de_en("verborgen", "hidden"),
            "admins": de_en("Admins der Organisation entscheiden", "organisation admins decide")}[mode]


def hides(org):
    """Whether this organisation hides the names of its members from each other."""
    if org is None:
        return False
    mode = org.names_mode or "shown"
    return mode == "hidden" or (mode == "admins" and bool(org.names_hidden_own))


def _cache():
    if not has_request_context():
        return None
    if "names_cache" not in g:
        g.names_cache = {}
    return g.names_cache


def _hiding_ids():
    """The ids of the organisations that hide names (cached per request)."""
    cache = _cache()
    if cache is not None and "hiding" in cache:
        return cache["hiding"]
    from app.models import Organisation

    rows = db.session.scalars(db.select(Organisation).where(Organisation.names_mode.in_(("hidden", "admins"))))
    found = {o.id for o in rows if hides(o)}
    if cache is not None:
        cache["hiding"] = found
    return found


@contextmanager
def real():
    """Real names inside this block (documents for the record, the participants' view)."""
    held = g.get("names_real", False) if has_request_context() else False
    if has_request_context():
        g.names_real = True
    try:
        yield
    finally:
        if has_request_context():
            g.names_real = held


def _viewer():
    """The signed-in person whose eyes count, or None when names are not hidden here."""
    if not has_request_context() or g.get("names_real") or request.blueprint in REAL_BLUEPRINTS:
        return None
    from flask_login import current_user

    try:
        if not current_user.is_authenticated:
            return None
        return current_user._get_current_object()
    except Exception:  # noqa: BLE001 - outside a sign-in (CLI, tests without a user)
        return None


def _site_admin(user):
    from flask import current_app

    return getattr(user, "orcid", None) in current_app.config.get("ADMIN_ORCIDS", ())


def hidden_org(person, viewer=None):
    """The organisation whose setting hides this person from the viewer (the signed-in person by default), or None."""
    if person is None or not getattr(person, "id", None):
        return None
    viewer = viewer if viewer is not None else _viewer()  # first: participants' pages never ask the database
    if viewer is None or viewer.id == person.id or _site_admin(viewer):
        return None
    hiding = _hiding_ids()
    if not hiding:
        return None
    from app import orgs

    home = orgs.home_id(person)
    if home not in hiding:
        return None
    cache = _cache()
    key = ("free", viewer.id, person.id)
    if cache is not None and key in cache:
        return None if cache[key] else orgs.get(home)
    free = orgs.is_admin_of(viewer, home) or _leads(viewer, person, home)
    if cache is not None:
        cache[key] = free
    return None if free else orgs.get(home)


def _leads(viewer, person, org_id):
    """Whether the viewer leads a group of this organisation the person is a member of (E17)."""
    from app import orgs
    from app.models import WorkGroup, WorkGroupMember

    led = db.select(WorkGroupMember.group_id).where(WorkGroupMember.user_id == viewer.id, WorkGroupMember.role == "lead")
    return db.session.scalar(db.select(WorkGroupMember.id).join(WorkGroup, WorkGroup.id == WorkGroupMember.group_id).where(
        WorkGroupMember.user_id == person.id, WorkGroupMember.group_id.in_(led),
        orgs.column(WorkGroup.org_id) == org_id).limit(1)) is not None


def number(org_id, user_id):
    """The person's number in the organisation, given once and kept (E16)."""
    from app.models import OrgAlias

    cache = _cache()
    key = ("nr", org_id, user_id)
    if cache is not None and key in cache:
        return cache[key]
    found = db.session.scalar(db.select(OrgAlias.number).filter_by(org_id=org_id, user_id=user_id))
    if found is None:
        # in a transaction of its own, so a page that only reads keeps the number, and the request's own changes are
        # not committed with it; two requests at once: the unique keys let one win, the other reads it
        from sqlalchemy.exc import IntegrityError

        for _ in range(3):
            try:
                with db.engine.begin() as conn:
                    last = conn.execute(db.select(db.func.max(OrgAlias.number)).where(OrgAlias.org_id == org_id)).scalar()
                    conn.execute(db.insert(OrgAlias).values(org_id=org_id, user_id=user_id, number=(last or 0) + 1))
            except IntegrityError:
                pass
            except Exception:  # noqa: BLE001 - a database busy with this request's own writes: no number this time
                break
            found = db.session.scalar(db.select(OrgAlias.number).filter_by(org_id=org_id, user_id=user_id))
            if found is not None:
                break
    if cache is not None and found is not None:
        cache[key] = found
    return found or 0


def alias(person, org, viewer=None):
    """"Mitglied 12", with the short name of the organisation for viewers outside it (E22)."""
    from app import orgs

    n = number(org.id, person.id)
    viewer = viewer if viewer is not None else _viewer()
    word = de_en("Mitglied {n}", "Member {n}", n=n) if n else de_en("Mitglied", "Member")
    if viewer is not None and org.id in orgs.ids_of(viewer):
        return word
    return f"{word} ({org.name})"


def shown(person, real_name, viewer=None):
    """The name the viewer sees: the real one, or the alias when the person's organisation hides names."""
    org = hidden_org(person, viewer)
    return real_name if org is None else alias(person, org, viewer)


def for_outsider(person):
    """The name of `person` for someone without an account (an invitation by e-mail): the alias with the
    organisation's name when it hides names, else the name."""
    real_name = (person.name if person is not None else "") or "ORCID user"
    if person is None or not _hiding_ids():
        return real_name
    from app import orgs

    org = orgs.get(orgs.home_id(person))
    if org is None or org.id not in _hiding_ids():
        return real_name
    n = number(org.id, person.id)
    word = de_en("Mitglied {n}", "Member {n}", n=n) if n else de_en("Mitglied", "Member")
    return f"{word} ({org.name})"


def is_hidden(person, viewer=None):
    return hidden_org(person, viewer) is not None


def of_id(user_id, fallback=""):
    """For names stored as text beside the account (comments, notes): the name the viewer sees."""
    if not user_id:
        return fallback
    from app.models import User

    person = db.session.get(User, user_id)
    if person is None:
        return fallback
    return shown(person, fallback or person.name or "ORCID user")


def orcid_hidden(orcid):
    """Whether the ORCID iD of a hidden person must not be shown (links next to names, E23)."""
    if not orcid or not _hiding_ids():
        return False
    from app.models import User

    person = db.session.scalar(db.select(User).filter_by(orcid=orcid))
    return is_hidden(person)


def own_note(user):
    """The line on "Konto" (E18): how others see this person, or None."""
    from app import orgs

    home = orgs.home_id(user)
    org = orgs.get(home)
    if not hides(org):
        return None
    return de_en("Andere Mitglieder von „{o}“ sehen Sie als „Mitglied {n}“; Ihren Namen sehen nur die Admins der "
                 "Organisation und der Plattform und die Leitungen Ihrer Gruppen.",
                 "Other members of “{o}” see you as “Member {n}”; only the admins of the organisation and of the "
                 "platform and the leads of your groups see your name.", o=org.name, n=number(org.id, user.id))
