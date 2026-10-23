"""Registration links of the platform (konzept-organisation-sichtbarkeit-einladungen-2026-10-23.md, E1-E9, Ralf
2026-10-23 "wie empfohlen").

A site admin makes a link under Administration > Personen ("Einladungslink anlegen …"): an organisation, how many
registrations, how long the link works, the user type and the period of the accounts (as the type says, 30 to 180 days,
12 months, until a date, or without end). Whoever opens it and signs in with ORCID for the first time gets an account
at home in that organisation, with that type and period from this first sign-in on (E4).

It works by the sign-in list: at the moment of use the iD is put on it (SignInOrcid with the link's choices and
link_id), and the sign-in that follows takes over organisation, type and period as for "Person hinzufügen". So the link
lets in past the list, the limit and the waiting list of "every ORCID holder" (E6), but not past the number of
accounts the organisation may have. An account that exists already is not changed (E5). Only ORCID (E7). Only the hash
of a token is stored; the link is shown once (E8).
"""
import hashlib
import secrets
import time
from datetime import date, timedelta

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for)
from flask_login import current_user

from app import brand
from app.extensions import db
from app.lang import de_en
from app.models import SignInOrcid, SignUpLink, User, utcnow

bp = Blueprint("signup_links", __name__, url_prefix="/signup")

USES = (1, 5, 10, 20, 30, 50, 100, 250)
DAYS = (1, 7, 14, 30, 90)
DEFAULT_USES, DEFAULT_DAYS = 20, 14
# the period of the accounts: "type" as the user type says, a spec of app/account_types.py, "date" a chosen end date
PERIODS = ("type", "t:30", "t:60", "t:90", "t:180", "m:12", "date", "")
MAX_OPEN = 50
SESSION_KEY = "signup"      # the token while the person signs in at ORCID
SESSION_SECONDS = 30 * 60
SHOW_KEY = "signup_new"     # a new link, shown once on the next page


def _hash(token):
    return hashlib.sha256((token or "").encode()).hexdigest()


def period_label(value):
    from app import account_types

    if value == "type":
        return de_en("wie der Nutzertyp", "as the user type")
    if value == "date":
        return de_en("bis zu einem Datum", "until a date")
    return account_types.describe(value)


def sweep():
    """Links that expired more than 30 days ago go; until then they stay listed as ended."""
    db.session.execute(db.delete(SignUpLink).where(SignUpLink.expires_at < utcnow() - timedelta(days=30)))


def listed():
    """All links, newest first, for the administration."""
    sweep()
    return list(db.session.scalars(db.select(SignUpLink).order_by(SignUpLink.created_at.desc())))


def is_open(link):
    return link.expires_at >= utcnow() and link.uses < link.max_uses


def valid(token):
    row = db.session.scalar(db.select(SignUpLink).filter_by(token_hash=_hash(token))) if token else None
    return row if row is not None and is_open(row) else None


def came_by(link):
    """The accounts that came in by this link (their iD on the sign-in list names it)."""
    orcids = db.select(SignInOrcid.orcid).where(SignInOrcid.link_id == link.id)
    return list(db.session.scalars(db.select(User).where(User.orcid.in_(orcids), User.pending.is_(False))
                                   .order_by(User.created_at)))


def create(by, org, account_type, period, max_uses, days, note=""):
    """A new link; returns (address, None) or (None, message). The token is not kept."""
    from app import account_types

    sweep()
    if db.session.scalar(db.select(db.func.count(SignUpLink.id)).where(SignUpLink.expires_at >= utcnow())) >= MAX_OPEN:
        return None, de_en("Mehr als {n} offene Einladungslinks sind nicht möglich; bitte zuerst einen widerrufen.",
                           "More than {n} open invitation links are not possible; please revoke one first.", n=MAX_OPEN)
    if account_type is None:
        account_type = account_types.default_type(org.id if org is not None else None)
    if period == "type" and account_type is not None and account_types.needs_input(account_type.duration):
        return None, de_en("Dieser Nutzertyp lässt die Laufzeit offen; bitte eine Laufzeit wählen.",
                           "This user type leaves the period open; please choose a period.")
    token = secrets.token_urlsafe(32)
    db.session.add(SignUpLink(token_hash=_hash(token), org_id=org.id if org is not None else None,
                              account_type_id=account_type.id if account_type is not None else None,
                              valid_spec=None if period == "type" else period, max_uses=max_uses, uses=0,
                              note=(note or "").strip()[:200], created_by_id=by.id,
                              expires_at=utcnow() + timedelta(days=days)))
    db.session.commit()
    from app import security_log

    security_log.event("sign_up_link_created", by=by.id, target=f"org {org.id}" if org is not None else None)
    return brand.public_base() + url_for("signup_links.page", token=token), None


def period_from_form(form):
    """The period chosen in the dialog: (spec or "type", None) or (None, message)."""
    value = form.get("period", "type")
    if value not in PERIODS:
        return None, de_en("Bitte eine Laufzeit wählen.", "Please choose a period.")
    if value != "date":
        return value, None
    from app.timeutil import local_today

    try:
        end = date.fromisoformat(form.get("period_date") or "")
    except ValueError:
        end = None
    if end is None or end < local_today():
        return None, de_en("Bitte ein Enddatum ab heute angeben.", "Please give an end date from today on.")
    return f"d:{end.isoformat()}", None


def show_once(link):
    """The new link waits in the session, encrypted (the cookie is signed, not encrypted), for the next page."""
    from app import secrets_box

    session[SHOW_KEY] = secrets_box.encrypt(link)


def take_shown():
    from app import secrets_box

    held = session.pop(SHOW_KEY, None)
    return secrets_box.decrypt(held) if held else None


def revoke(link_id):
    link = db.session.get(SignUpLink, link_id)
    if link is not None:
        link.expires_at = min(link.expires_at, utcnow())  # stays listed as ended, with who came by it
    return link


def _take_use(link):
    """Count one use only while uses < max_uses, in the database (two people cannot take the last place)."""
    done = db.session.execute(db.update(SignUpLink).where(SignUpLink.id == link.id, SignUpLink.uses < SignUpLink.max_uses)
                              .values(uses=SignUpLink.uses + 1).execution_options(synchronize_session=False)).rowcount
    db.session.refresh(link)
    return done == 1


def _invalid():
    flash(de_en("Dieser Einladungslink gilt nicht mehr: Er ist abgelaufen, aufgebraucht oder wurde widerrufen. Bitte "
                "fragen Sie die Person, die ihn Ihnen gegeben hat.",
                "This invitation link is no longer valid: it has expired, been used up or been revoked. Please ask the "
                "person who gave it to you."), "error")
    return redirect(url_for("public.index"))


def _have_account():
    return de_en("Sie haben schon ein Konto. Der Einladungslink ist für neue Konten; an Ihrem Konto ändert er nichts.",
                 "You already have an account. The invitation link is for new accounts; it changes nothing about yours.")


def _full():
    return de_en("Für diese Organisation sind keine Plätze mehr frei. Bitte fragen Sie die Person, die Ihnen den Link "
                 "gegeben hat.", "This organisation has no places left. Please ask the person who gave you the link.")


@bp.route("/<token>", methods=["GET", "POST"])
def page(token):
    """The link itself: register with ORCID. Signed in already: the account stays as it is (E5)."""
    from app import limits, orgs

    if not limits.allow_invite_try(count=False):
        abort(429)
    link = valid(token)
    if link is None:
        limits.allow_invite_try()
        return _invalid()
    if current_user.is_authenticated:
        flash(_have_account(), "info")
        return redirect(url_for("dashboard.index"))
    if request.method == "POST":
        from app import secrets_box

        session[SESSION_KEY] = {"box": secrets_box.encrypt(token), "at": time.time()}
        return redirect(url_for("auth.login"))
    org = orgs.get(link.org_id)
    response = current_app.make_response(render_template(
        "signup/link.html", link=link, org=org, full=orgs.accounts_left(org) == 0, platform_name=brand.name(),
        orcid_on=bool(current_app.config.get("ORCID_CLIENT_ID"))))
    response.headers["Cache-Control"] = "no-store"  # the address of the page is the token
    return response


def accept_after_orcid(orcid, name):
    """Called by the ORCID callback before it asks whether this iD may sign in: a link opened before going to ORCID puts
    a new iD on the sign-in list with the link's organisation, type and period. True when it did."""
    from app import auth, orgs, secrets_box

    held = session.pop(SESSION_KEY, None)
    if not isinstance(held, dict) or time.time() - float(held.get("at") or 0) > SESSION_SECONDS:
        return False
    link = valid(secrets_box.decrypt(held.get("box")))
    if link is None:
        _invalid()
        return False
    if db.session.scalar(db.select(User.id).filter_by(orcid=orcid, pending=False)) is not None:
        flash(_have_account(), "info")
        return False
    if db.session.get(SignInOrcid, orcid) is not None:
        return False  # on the list already: it comes in as chosen there
    if orgs.accounts_left(orgs.get(link.org_id)) == 0:
        flash(_full(), "error")
        return False
    if not _take_use(link):
        db.session.rollback()
        _invalid()
        return False
    note = de_en("Einladungslink", "Invitation link") + (f": {link.note}" if link.note else "")
    db.session.add(SignInOrcid(orcid=orcid, note=note[:200], added_by_id=link.created_by_id, org_id=link.org_id,
                               account_type_id=link.account_type_id, valid_spec=link.valid_spec, link_id=link.id))
    db.session.commit()
    auth.forget_sign_in_yes()
    from app import security_log

    security_log.event("sign_up_link_used", by=link.created_by_id, target=f"link {link.id}")
    return True
