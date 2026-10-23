"""Personal tokens of the AI agent interface (experimental "agent"; concept KI-Agenten-Schnittstelle 2026-10-15,
decisions 1b and 2b), made and revoked on the page "Konto"; the endpoint itself is app/mcp.py, the tools app/agent_tools.py.

Modelled on the read tokens of app/data_access.py: the token goes in the header "Authorization: Bearer askat_…", never
in the address (a request that carries one there is refused); only its hash is stored (models.once_hash), and it is
shown once, right after it was made. It works for DAYS days, while the feature is on for its person, their account is
not blocked and may sign in, and they accepted the current terms of use; the role in each survey is checked at every
call (agent_tools._survey), so losing a role stops the token there at once. Its scope is "read" (reading the
questionnaires) or "edit" (reading, and changing drafts); it may be limited to a list of surveys. A token can be
revoked at any time; changing the password or "Überall abmelden" revokes all of them (app/account_access.py), and the
rows go with the account.

Making a token needs a recent sign-in (accounts.need_fresh) and the tick on the hint that the questionnaires' contents
go to the AI service the agent uses: Askurious sends nothing to a model itself, the agent of the person does. Making,
revoking and every call are in the access log ("Agenten-Zugriff"). Calls per token are limited (limits.allow_agent),
wrong tokens per sender like the read tokens (limits.allow_token_try)."""
import secrets
from datetime import timedelta

from flask import Blueprint, abort, current_app, flash, redirect, request, url_for
from flask_login import current_user, login_required

from app import audit, experimental
from app.extensions import db, json_missing
from app.lang import de_en
from app.models import AgentToken, EditMark, Survey, once_hash, utcnow

bp = Blueprint("agent_access", __name__, url_prefix="/account/agent-tokens")

PREFIX = "askat_"       # tells a token apart in a configuration or a secret scanner
DAYS = 90               # a token expires after this many days (concept: 90 days)
PER_PERSON = 10         # valid tokens of one person
KEEP_EXPIRED_DAYS = 30  # an expired token stays listed (as expired) this long, then it goes
QUERY_NAMES = ("token", "access_token", "api_key", "key", "bearer", "auth")  # never accepted in the address
FEATURE = "agent"


def hashed(token):
    return once_hash(token or "")


def owner_may(owner):
    """May the person of a token still use it: the feature on for them, an account that is not blocked and may sign
    in (as auth.load_user asks for a sign-in), and the current terms of use accepted."""
    from app import accounts, auth, terms

    if owner is None or accounts.is_blocked(owner):
        return False
    if not current_app.config["DEV_LOGIN"] and not auth.may_sign_in(owner.orcid):
        return False
    if owner.terms_version != terms.version():
        return False
    return experimental.enabled(FEATURE, user=owner)


def sweep():
    """Tokens expired more than KEEP_EXPIRED_DAYS ago go (before every listing and with the housekeeping)."""
    gone = db.session.execute(db.delete(AgentToken).where(
        AgentToken.expires_at < utcnow() - timedelta(days=KEEP_EXPIRED_DAYS))).rowcount
    if gone:
        db.session.commit()


def listing(user):
    """The person's tokens for the page "Konto", newest first: {"row", "state": "active" | "expired", "surveys":
    [titles] or None}."""
    sweep()
    rows = list(db.session.scalars(db.select(AgentToken).where(AgentToken.user_id == user.id)
                                   .order_by(AgentToken.created_at.desc())))
    ids = {i for r in rows for i in (r.surveys or [])}
    titles = {i: t for i, t in db.session.execute(db.select(Survey.id, Survey.title).where(Survey.id.in_(ids)))}
    now = utcnow()
    return [{"row": r, "state": "expired" if r.expires_at < now else "active",
             "surveys": None if r.surveys is None else [titles.get(i, f"#{i}") for i in r.surveys]} for r in rows]


def choosable(user):
    """The surveys a token can be limited to: the person's own and their groups', newest first, without live polls."""
    from app import access

    return list(db.session.scalars(db.select(Survey).where(Survey.id.in_(access.survey_ids(user)),
                                                           json_missing(Survey.live)).order_by(Survey.id.desc())))


def endpoint_url():
    from app import brand

    return brand.public_base() + url_for("mcp.endpoint")


def setup_command(token):
    """The line that adds the server to Claude Code (README, section "Agenten-Schnittstelle (MCP)")."""
    return f'claude mcp add --transport http askurious {endpoint_url()} --header "Authorization: Bearer {token}"'


def _back():
    return redirect(url_for("accounts.connections") + "#ki-agenten")


@bp.route("/new", methods=["POST"])
@login_required
def create():
    """A new token for the person signed in, shown once on the page "Verbindungen" (drawn as the answer, so the token is
    never in a session cookie or an address)."""
    experimental.require(FEATURE)
    from app import accounts

    again = accounts.need_fresh(url_for("accounts.connections") + "#ki-agenten")
    if again:
        return again
    name = " ".join(request.form.get("name", "").split())[:80]
    if not name:
        flash(de_en("Bitte dem Token einen Namen geben, z. B. „Claude Code Laptop“.",
                    "Please give the token a name, e.g. “Claude Code laptop”."), "error")
        return _back()
    if request.form.get("understood") != "1":
        flash(de_en("Bitte bestätigen Sie den Hinweis: Inhalte Ihrer Fragebögen gehen an den KI-Dienst, den Ihr Agent "
                    "nutzt.", "Please confirm the note: the contents of your questionnaires go to the AI service your "
                    "agent uses."), "error")
        return _back()
    scope = AgentToken.EDIT if request.form.get("scope") == AgentToken.EDIT else AgentToken.READ
    surveys = None
    if request.form.get("limit") == "1":
        allowed = {s.id for s in choosable(current_user)}
        surveys = sorted({i for i in request.form.getlist("survey", type=int) if i in allowed})
        if not surveys:
            flash(de_en("Bitte mindestens eine Befragung wählen oder „alle meine Befragungen“.",
                        "Please choose at least one survey or “all my surveys”."), "error")
            return _back()
    now = utcnow()
    mine = db.session.scalar(db.select(db.func.count(AgentToken.id)).where(
        AgentToken.user_id == current_user.id, AgentToken.expires_at >= now))
    if mine >= PER_PERSON:
        flash(de_en("Mehr als {n} gültige Token je Person sind nicht möglich; bitte ein altes widerrufen.",
                    "More than {n} valid tokens per person are not possible; please revoke an old one.", n=PER_PERSON),
              "error")
        return _back()
    token = PREFIX + secrets.token_urlsafe(32)
    row = AgentToken(user_id=current_user.id, name=name, token_hash=hashed(token), scope=scope, surveys=surveys,
                     created_at=now, expires_at=now + timedelta(days=DAYS))
    db.session.add(row)
    db.session.commit()
    audit.done(detail=f"Token „{name}“, {scope}")
    from app.accounts import connections_page

    return connections_page(new_agent_token={"value": token, "name": name, "expires_at": row.expires_at,
                                 "command": setup_command(token)})


@bp.route("/<int:token_id>/revoke", methods=["POST"])
@login_required
def revoke(token_id):
    """Revoke one of the own tokens; it stops working at once. Also without the feature (a token made before)."""
    row = db.session.get(AgentToken, token_id)
    if row is None or row.user_id != current_user.id:
        abort(404)
    name = row.name
    forget(row)
    db.session.commit()
    audit.done(detail=f"Token „{name}“")
    flash(de_en("Token „{name}“ widerrufen; es funktioniert nicht mehr.", "Token “{name}” revoked; it no longer works.",
                name=name), "success")
    return _back()


def forget(row):
    """Remove a token and its marks in the editors (edit_guard.agent_seen). Part of the caller's transaction."""
    from app.edit_guard import AGENT_KEY

    db.session.execute(db.delete(EditMark).where(EditMark.key == f"{AGENT_KEY}{row.id}"))
    db.session.delete(row)


def names(token_ids):
    """{token id: name} for the editor's presence and banner."""
    if not token_ids:
        return {}
    return {i: n for i, n in db.session.execute(db.select(AgentToken.id, AgentToken.name)
                                                .where(AgentToken.id.in_(token_ids)))}
