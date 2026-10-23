"""The setup assistant after the first sign-in (konto-einrichtung-konzept.md, all as recommended, Ralf 2026-10-18).

A new account (User.setup_due: first sign-in with ORCID, an account a site admin or an organisation admin made, an
invitation or a join link with a password) is led once through four short steps before anything else: details and
language, the second way to sign in (e-mail and password beside ORCID, or ORCID beside the password), the own AI
access (the form and storage of the page "KI-Nutzung", app/ai_access.py) and the learning aids. Every step can be
skipped, "Einrichtung beenden" ends the whole assistant; both end it for good. Accounts from before never see it, and
there is no way to run it again (Ralf, 2026-10-18): everything in it is also on the page "Konto".

The gate (`gate`, after the terms of use) sends every page called with GET to the assistant while it is due; forms
that are sent (POST) go through, so the forms of the steps keep their own routes. Where the person was going (the
survey of an invitation, the group of a join link) is kept in the session and reached after the last step.
"""
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.lang import de_en
from app.links import local_path

bp = Blueprint("account_setup", __name__, url_prefix="/account/setup")

STEPS = ("details", "sign_in", "ai", "learning")
NEXT_KEY = "setup_next"  # in the session: where the person was going when the assistant took over
# what may be called with GET while the assistant is due: the assistant, signing in and out, the terms, the public and
# participants' pages, files, the confirmation link of a new sign-in address (it comes by mail), machines
OPEN_ENDPOINTS = ("static", "account_setup.", "auth.", "terms.", "public.", "participate.", "media.", "healthz",
                  "cron.", "accounts.confirm_email", "twofactor.", "board_share.", "quota_share.", "preview_share.",
                  "data_access.api_", "mcp.")


def due(user=None):
    from flask import current_app

    user = current_user if user is None else user
    return bool(current_app.config.get("ACCOUNT_SETUP", True) and getattr(user, "is_authenticated", False)
                and getattr(user, "setup_due", False))


def gate():
    """before_request: an account with the assistant due goes there first (pages called with GET only)."""
    if request.method != "GET" or not due():
        return None
    endpoint = request.endpoint or ""
    if any(endpoint == e or endpoint.startswith(e) for e in OPEN_ENDPOINTS):
        return None
    goal = local_path(request.full_path.rstrip("?"))
    # only a page the person opened is a goal, not what a script of a page fetches in the background
    opened = request.headers.get("Sec-Fetch-Mode", "navigate") == "navigate"
    if goal and opened and endpoint not in ("dashboard.index", "accounts.index"):
        session[NEXT_KEY] = goal
    return redirect(url_for("account_setup.step", name=_current()))


def back(step):
    """The way back for a form of the assistant that has its own route (the password, the own AI access): to its
    step while the assistant is due, else None (the route goes to its own page)."""
    if not due():
        return None
    session["setup_step"] = step
    return redirect(url_for("account_setup.step", name=step))


def _current():
    step = session.get("setup_step")
    return step if step in STEPS else STEPS[0]


def _finish():
    from app.models import User

    user = db.session.get(User, current_user.id)
    user.setup_due = False
    db.session.commit()
    session.pop("setup_step", None)
    goal = local_path(session.pop(NEXT_KEY, "") or "")
    return redirect(goal or url_for("dashboard.index"))


def _go(step):
    index = STEPS.index(step)
    if index + 1 >= len(STEPS):
        flash(de_en("Ihr Konto ist eingerichtet. Alles lässt sich später auf der Seite „Konto“ ändern.",
                    "Your account is set up. You can change everything later on the page “Account”."), "success")
        return _finish()
    session["setup_step"] = STEPS[index + 1]
    return redirect(url_for("account_setup.step", name=STEPS[index + 1]))


@bp.route("/")
@login_required
def start():
    if not due():
        return redirect(url_for("dashboard.index"))
    return redirect(url_for("account_setup.step", name=_current()))


@bp.route("/<name>", methods=["GET", "POST"])
@login_required
def step(name):
    if name not in STEPS:
        abort(404)
    if not due():
        return redirect(url_for("dashboard.index"))
    session["setup_step"] = name
    if request.method == "POST":
        if request.form.get("skip") != "1":
            error = _save(name)
            if error:
                flash(error, "error")
                return redirect(url_for("account_setup.step", name=name))
        return _go(name)
    return render_template("accounts/setup.html", **_context(name))


@bp.route("/end", methods=["POST"])
@login_required
def end():
    """"Einrichtung beenden": the rest is on the page "Konto"."""
    if not due():
        return redirect(url_for("dashboard.index"))
    flash(de_en("Einrichtung beendet. Alles Weitere finden Sie auf der Seite „Konto“.",
                "Setup ended. You find everything else on the page “Account”."), "success")
    return _finish()


def _save(name):
    """What the button "Weiter" of a step saves; an error text or None. The steps "sign_in" and "ai" save with
    the forms of their own routes, "Weiter" only goes on."""
    from app.models import User

    user = db.session.get(User, current_user.id)
    if name == "details":
        from app import lang

        if not user.has_orcid:
            typed = " ".join(request.form.get("name", "").split())[:200]
            if not typed:
                return de_en("Bitte einen Namen angeben: Unter ihm stehen Sie in den Listen der Beteiligten.",
                             "Please enter a name: it is how you appear in the lists of people involved.")
            user.name = typed
        chosen = request.form.get("lang")
        if chosen in lang.LANGUAGES:
            user.display = dict(user.display or {}, lang=chosen)
            from flask import g

            g.ui_lang = chosen
    elif name == "learning":
        user.learning_aids = request.form.get("learning_aids") == "1"
    db.session.commit()
    return None


def _context(name):
    from app import accounts, ai_access, lang, passwords, twofactor
    import sys

    r = ai_access.row(current_user)
    return dict(step=name, steps=STEPS, number=STEPS.index(name) + 1, total=len(STEPS),
                login=passwords.login_row(current_user), has_password=passwords.has_password(current_user),
                totp=twofactor.enabled(current_user),
                fresh=accounts._fresh(), minutes=accounts.FRESH_SECONDS // 60, min_length=passwords.MIN_LENGTH,
                ui_languages=lang.LANGUAGES, ai_access=r, ai_access_days=ai_access.days_left(r),
                ai_mod=sys.modules["app.ai_access"], here=url_for("account_setup.step", name=name))


def init_app(app):
    app.register_blueprint(bp)
    app.before_request(gate)
