"""Founding links (konzept-gruendungslink-organisation-2026-11-06.md): a new person creates an organisation of their
own and becomes its admin.

A site admin makes a link under Administration > Organisationen: a note, how long it works (28 days unless chosen
otherwise) and the number of accounts the organisation may have. Whoever opens it names the organisation and signs in
with ORCID; account and organisation come in one step. Only for people without an account: an account that exists
already stays as it is and the link stays open ("Neue Organisation" is the way for those). The home of nobody moves.

The form comes before the sign-in, so breaking off at ORCID leaves nothing behind and the link stays open. It is used
when the organisation is created, in the database (two people cannot both use it). The iD goes on the sign-in list with
the new organisation: that lets it in while sign-in needs an invitation (being an organisation's admin alone does not),
and the first sign-in takes home, default type and "create" from there (auth._sign_in, orgs.start_new).

The new organisation starts with max_accounts of the link and "only its admins bring in people from outside"; a site
admin changes both on its page. A link rests once its maker is no site admin any more or is blocked (AU-3). Only the
hash of a token is stored; the link is shown once."""
import hashlib
import json
import secrets
import time
from datetime import timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user

from app import brand, orgs
from app.extensions import db
from app.lang import de_en
from app.models import OrgFoundLink, SignInOrcid, User, utcnow

bp = Blueprint("org_links", __name__, url_prefix="/joinorg")

DAYS = (7, 14, 28, 56, 90)
DEFAULT_DAYS = 28
DEFAULT_ACCOUNTS = 100
MAX_ACCOUNTS = 100000       # as the field on the organisation's page
MAX_OPEN = 20
SCOPE = "admins"            # orgs.SCOPES: who brings in people from outside
SESSION_KEY = "found_link"  # token and names while the person signs in at ORCID
DRAFT_KEY = "found_draft"   # the names once more, after a name was taken in the meantime
SESSION_SECONDS = 30 * 60
SHOW_KEY = "found_link_new"  # a new link, shown once on the next page


def _hash(token):
    return hashlib.sha256((token or "").encode()).hexdigest()


def sweep():
    """Links go 30 days after they expired or were used; until then they stay listed."""
    line = utcnow() - timedelta(days=30)
    db.session.execute(db.delete(OrgFoundLink).where(db.or_(OrgFoundLink.expires_at < line, OrgFoundLink.used_at < line)))


def listed():
    sweep()
    return list(db.session.scalars(db.select(OrgFoundLink).order_by(OrgFoundLink.created_at.desc())))


def is_open(link):
    return link.used_at is None and link.expires_at >= utcnow()


def open_links():
    return list(db.session.scalars(db.select(OrgFoundLink).where(OrgFoundLink.used_at.is_(None),
                                                                 OrgFoundLink.expires_at >= utcnow())))


def _rests(link):
    from app import accounts

    maker = db.session.get(User, link.created_by_id) if link.created_by_id else None
    return maker is None or accounts.is_blocked(maker) or not orgs._site_admin(maker)


def valid(token):
    row = db.session.scalar(db.select(OrgFoundLink).filter_by(token_hash=_hash(token))) if token else None
    return row if row is not None and is_open(row) and not _rests(row) else None


def create(by, days, max_accounts, note=""):
    """A new link: (address, None) or (None, message). The token is not kept."""
    sweep()
    if len(open_links()) >= MAX_OPEN:
        return None, de_en("Mehr als {n} offene Gründungslinks sind nicht möglich; bitte zuerst einen widerrufen.",
                           "More than {n} open founding links are not possible; please revoke one first.", n=MAX_OPEN)
    token = secrets.token_urlsafe(32)
    link = OrgFoundLink(token_hash=_hash(token), note=(note or "").strip()[:200], max_accounts=max_accounts,
                        created_by_id=by.id, expires_at=utcnow() + timedelta(days=days))
    db.session.add(link)
    db.session.commit()
    from app import security_log

    security_log.event("org_found_link_created", by=by.id, target=f"link {link.id}")
    return brand.public_base() + url_for("org_links.page", token=token), None


def show_once(address):
    from app import secrets_box

    session[SHOW_KEY] = secrets_box.encrypt(address)


def take_shown():
    from app import secrets_box

    held = session.pop(SHOW_KEY, None)
    return secrets_box.decrypt(held) if held else None


def revoke(link_id):
    link = db.session.get(OrgFoundLink, link_id)
    if link is not None and link.used_at is None:
        link.expires_at = min(link.expires_at, utcnow())  # stays listed as ended
        return link
    return None


def _invalid():
    flash(de_en("Dieser Link gilt nicht mehr: Er ist abgelaufen, wurde schon benutzt oder widerrufen. Bitte fragen Sie "
                "die Person, die ihn Ihnen gegeben hat.",
                "This link is no longer valid: it has expired, was used already or has been revoked. Please ask the "
                "person who gave it to you."), "error")


def _have_account():
    return de_en("Sie haben schon ein Konto. Dieser Link ist für neue Konten; an Ihrem Konto ändert er nichts, und er "
                 "bleibt offen. Bitte fragen Sie die Person, die ihn Ihnen gegeben hat.",
                 "You already have an account. This link is for new accounts; it changes nothing about yours and stays "
                 "open. Please ask the person who gave it to you.")


def _name_taken(name):
    return de_en("Eine Organisation „{name}“ gibt es schon. Bitte einen anderen Namen wählen.",
                 "An organisation “{name}” exists already. Please choose another name.", name=name)


def _names(form):
    return " ".join((form.get("name") or "").split())[:orgs.MAX_NAME], " ".join((form.get("long_name") or "").split())[:200]


@bp.route("/<token>", methods=["GET", "POST"])
def page(token):
    """The link itself: the name of the organisation, then ORCID."""
    from app import limits, secrets_box

    if not limits.allow_invite_try(count=False):
        abort(429)
    link = valid(token)
    if link is None:
        limits.allow_invite_try()
        _invalid()
        return redirect(url_for("public.index"))
    if current_user.is_authenticated:
        flash(_have_account(), "info")
        return redirect(url_for("dashboard.index"))
    form = request.form if request.method == "POST" else (session.pop(DRAFT_KEY, None) or {})
    orcid_on = bool(current_app.config.get("ORCID_CLIENT_ID"))
    if request.method == "POST" and orcid_on:
        name, long_name = _names(form)
        if not name:
            flash(de_en("Bitte einen Namen für die Organisation angeben.", "Please give the organisation a name."), "error")
        elif orgs.name_taken(name):
            flash(_name_taken(name), "error")
        else:
            held = json.dumps({"token": token, "name": name, "long_name": long_name})
            session[SESSION_KEY] = {"box": secrets_box.encrypt(held), "at": time.time()}
            return redirect(url_for("auth.login"))
    response = current_app.make_response(render_template(
        "org_links/link.html", link=link, form=form, orcid_on=orcid_on, platform_name=brand.name(),
        max_name=orgs.MAX_NAME))
    response.headers["Cache-Control"] = "no-store"  # the address of the page is the token
    return response


def _take(link):
    """The one use, in the database (two people cannot both create an organisation)."""
    done = db.session.execute(db.update(OrgFoundLink).where(
        OrgFoundLink.id == link.id, OrgFoundLink.used_at.is_(None), OrgFoundLink.expires_at >= utcnow())
        .values(used_at=utcnow()).execution_options(synchronize_session=False)).rowcount
    db.session.refresh(link)
    return done == 1


def accept_after_orcid(orcid):
    """Called by the ORCID callback before it asks whether this iD may sign in. Returns (organisation, None) when it
    was created, (None, address) when the person goes back to the form, else (None, None): the sign-in goes on as
    without a link. Does not sign in; the iD is on the sign-in list then."""
    from app import auth, secrets_box, security_log

    held = session.pop(SESSION_KEY, None)
    if not isinstance(held, dict) or time.time() - float(held.get("at") or 0) > SESSION_SECONDS:
        return None, None
    try:
        data = json.loads(secrets_box.decrypt(held.get("box")) or "")
    except (TypeError, ValueError):
        return None, None
    link = valid(data.get("token"))
    if link is None:
        _invalid()
        return None, None
    if db.session.scalar(db.select(User.id).filter_by(orcid=orcid, pending=False)) is not None:
        flash(_have_account(), "info")
        return None, None
    name, long_name = _names(data)
    if not name or orgs.name_taken(name):  # taken while the person was at ORCID: nothing used, back to the form
        flash(_name_taken(name), "error")
        session[DRAFT_KEY] = {"name": name, "long_name": long_name}
        return None, url_for("org_links.page", token=data.get("token"))
    if not _take(link):
        db.session.rollback()
        _invalid()
        return None, None
    maker = db.session.get(User, link.created_by_id)
    org = orgs.make(name, maker, long_name=long_name)
    org.max_accounts, org.invite_scope = link.max_accounts, SCOPE
    row = orgs.add(org, orcid, role="admin", by=maker)
    link.org_id, link.used_by_id = org.id, row.user_id
    note = de_en("Gründungslink", "Founding link") + (f": {link.note}" if link.note else "")
    listed_row = db.session.get(SignInOrcid, orcid)
    if listed_row is None:
        db.session.add(SignInOrcid(orcid=orcid, note=note[:200], added_by_id=maker.id, org_id=org.id))
    else:  # listed before for another way in: this link decides, or that entry would give the home
        listed_row.org_id, listed_row.account_type_id, listed_row.valid_spec = org.id, None, None
        listed_row.link_id, listed_row.added_by_id = None, maker.id
    db.session.commit()
    auth.forget_sign_in_yes()
    security_log.event("organisation_created", by=maker.id, target=f"org {org.id}", reason=f"link {link.id}")
    flash(de_en("Organisation „{o}“ angelegt. Sie sind ihr Admin.", "Organisation “{o}” created. You are its admin.",
                o=org.name), "success")
    return org, None
