"""Signing in: with ORCID (OAuth 2.0, /authenticate scope), or with e-mail address and password.

ORCID returns the iD and the public name directly in the token response,
so no further API call is needed for login. Passwords: app/passwords.py.
"""
import secrets
import time
from urllib.parse import urlencode

import requests
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from app import brand
from app import lang
from app.extensions import db, login_manager
from app.lang import de_en
from app.links import local_path
from app.models import (ORCID_RE, DeletedAccount, SignInGrant, SurveyMember, User, UserLogin, WorkGroup,
                        WorkGroupMember, is_orcid, utcnow)

bp = Blueprint("auth", __name__, url_prefix="/auth")


@login_manager.user_loader
def load_user(user_id):
    """The signed-in user, as long as the account may still sign in (a removed iD is signed out on its next
    request) and the session is of the account's current version (User.get_id)."""
    from flask import g

    from app import sessions

    parts = str(user_id).split(".") + ["", "", ""]
    uid, version, sid = parts[:3]
    if not uid.isdigit() or not (version or "0").isdigit() or not sid:
        return None  # a cookie from before the session list (H6): signing in once more makes a session
    user = db.session.get(User, int(uid))
    if user is None or sessions.open_row(user.id, sid) is None:
        return None  # signed out ("Abmelden", ended on the page Konto): also every copy of the cookie
    g.login_sid = sid
    row = db.session.get(UserLogin, user.id)
    if row is not None and row.status == UserLogin.BLOCKED:
        return None
    if row is not None and row.valid_until is not None and row.valid_until < utcnow():
        return None  # its period ended (app/account_types.py): at once, not only after the hourly sweep blocked it
    if ((row.session_version or 0) if row is not None else 0) != int(version or 0):
        return None
    if not current_app.config["DEV_LOGIN"] and not _may_sign_in_kept(user.orcid):
        return None
    if session.get("signed_in_via") is None and parts[3] in ("orcid", "password", "password+code", "link"):
        session["signed_in_via"] = parts[3]  # authenticated session/remember token, after version and revocation checks
    # ADMIN_ORCIDS decides, not the last sign-in: a new admin need not sign in again, a removed one loses the pages
    admin = user.orcid in current_app.config["ADMIN_ORCIDS"]
    from app import twofactor

    method = session.get("signed_in_via")
    if (admin and method == "password") or (not admin and twofactor.enabled(user) and method not in ("orcid", "password+code")):
        # signed in with the password alone, before the iD came into ADMIN_ORCIDS: a site admin gives a code as well,
        # so this session ends (audit 2026-10-09b, T3; twofactor.SIGNED_IN_VIA)
        return None
    if user.is_admin != admin:
        user.is_admin = admin
        db.session.commit()
    return user


def _redirect_uri():
    return brand.public_base() + url_for("auth.orcid_callback")


def on_whitelist(orcid):
    """This iD is let in by itself, not by an invitation: ADMIN_ORCIDS of the configuration, or the list and the
    switch "every ORCID holder" of the administration (app/signin.py; ALLOWED_ORCIDS until 2026-10-06)."""
    from app import signin

    if not is_orcid(orcid):
        return False
    # "every ORCID holder" with or without a limit (gruppen-freischaltung-konzept.md, Stufe 3)
    return orcid in current_app.config["ADMIN_ORCIDS"] or signin.listed(orcid) or signin.open_lets_in(orcid)


def _may_sign_in(orcid):
    """Admins, whitelisted iDs, and anyone who was invited to a survey or added to a group. An account without
    ORCID was created by a site admin, which is the admin's yes; keeping it out is blocking (app/accounts.py)."""
    if not is_orcid(orcid):
        return True
    if on_whitelist(orcid):
        return True
    uid = db.session.scalar(db.select(User.id).filter_by(orcid=orcid))  # also a pending account (app/people_keys.py)
    if uid is None:
        return False
    if db.session.get(SignInGrant, uid) is not None:
        return True  # an admin's yes for this account, e.g. made by an admin and connected to ORCID later
    if db.session.scalar(db.select(SurveyMember.id).filter_by(user_id=uid).limit(1)) is not None:
        return True
    return db.session.scalar(db.select(WorkGroupMember.id).filter_by(user_id=uid).limit(1)) is not None


def _may_sign_in_kept(orcid):
    """_may_sign_in for the check on every request (load_user): a yes is kept SIGN_IN_SECONDS per server process, as
    balance.cached keeps its counts; it asks up to four tables (code review 2026-10-15, H10). A no is never kept.
    Taking a right away (an entry of a survey or a group, an admin's yes, the sign-in list, the switch "every ORCID
    holder") forgets every kept yes of this process at once (forget_sign_in_yes; audit 2026-10-18, AUTH-3); other
    processes ask again after SIGN_IN_SECONDS at the latest."""
    ttl = current_app.config.get("SIGN_IN_SECONDS", 10)
    store = current_app.extensions.setdefault("may_sign_in", {})
    now = time.monotonic()
    at = store.get(orcid)
    if ttl and at is not None and now - at < ttl:
        return True
    ok = _may_sign_in(orcid)
    if ok and ttl:
        if len(store) > 5000:
            store.clear()
        store[orcid] = now
    else:
        store.pop(orcid, None)
    return ok


def forget_sign_in_yes():
    """Drop the kept yes of _may_sign_in_kept in this process."""
    from flask import has_app_context

    if has_app_context():
        current_app.extensions.get("may_sign_in", {}).clear()


def _takes_rights_away(session):
    """Whether this flush deletes what lets a person sign in, or changes the switch "every ORCID holder"."""
    from app.models import SignInOrcid, SiteSetting

    if any(isinstance(obj, (SurveyMember, WorkGroupMember, SignInGrant, SignInOrcid)) for obj in session.deleted):
        return True
    return any(isinstance(obj, SiteSetting) for obj in list(session.dirty) + list(session.deleted))


def _watch_sign_in_rights():
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from app.models import SignInOrcid, SiteSetting

    tables = {m.__table__.name for m in (SurveyMember, WorkGroupMember, SignInGrant, SignInOrcid, SiteSetting)}

    def after_flush(session, _context):
        if _takes_rights_away(session):
            session.info["sign_in_changed"] = True
            forget_sign_in_yes()

    def on_execute(state):
        # bulk deletes and updates (db.delete(SurveyMember).where(...)) pass no object through the flush
        if state.is_delete or state.is_update:
            table = getattr(state.statement, "table", None)
            if getattr(table, "name", None) in tables:
                state.session.info["sign_in_changed"] = True

    def after_commit(session):
        if session.info.pop("sign_in_changed", False):
            forget_sign_in_yes()  # also a yes another request kept between the flush and the commit

    event.listen(Session, "after_flush", after_flush)
    event.listen(Session, "do_orm_execute", on_execute)
    event.listen(Session, "after_commit", after_commit)


_watch_sign_in_rights()


def may_admit(user, orcid=None):
    """May `user` add the person with this key to a survey or a group (or invite an address by mail: orcid None)?
    Being added lets a person sign in (_may_sign_in), so only who may create surveys brings in someone who cannot
    sign in yet; an account that came by an invitation adds only people who can already sign in (Ralf, 2026-10-09,
    rechte-uebersicht-konzept.md H3). Accounts without ORCID were made by a site admin and can always sign in."""
    if may_create_surveys(user) and not _limited(user):
        return True
    return orcid is not None and _may_sign_in(orcid)


def _limited(user):
    """An account with a period (app/account_types.py: thesis, internship, course) brings in nobody new: an invitation
    or a join link of it would give a new account the type of new accounts, without end. It adds people who can sign
    in already."""
    if user.orcid in current_app.config["ADMIN_ORCIDS"]:
        return False
    row = db.session.get(UserLogin, user.id)
    return row is not None and (row.valid_until is not None or row.valid_spec is not None)


def not_admitted():
    return de_en("Diese Person kann sich noch nicht bei Askurious anmelden. Hinzufügen darf sie nur, wer eigene "
                 "Befragungen anlegen darf; bitte eine Person mit diesem Recht oder einen Admin der Plattform fragen.",
                 "This person cannot sign in to Askurious yet. Only someone who may create their own surveys can add "
                 "them; please ask a person with this right or a platform admin.")


def may_sign_in(orcid):
    """Whether the person with this key can sign in (_may_sign_in), for sharing a design, a linked element or a
    research project with a person: sharing gives no right to sign in, so it is refused for someone who cannot
    (Ralf, 2026-10-09, rechte-uebersicht-konzept.md H4)."""
    return _may_sign_in(orcid)


def cannot_sign_in():
    return de_en("Diese Person kann sich nicht bei Askurious anmelden; Teilen allein gibt kein Anmelderecht. Bitte die "
                 "Person zuerst zu einer Befragung oder Gruppe hinzufügen oder einen Admin der Plattform fragen.",
                 "This person cannot sign in to Askurious; sharing alone gives no right to sign in. Please add the person "
                 "to a survey or group first or ask a platform admin.")


def may_create_surveys(user):
    """May create what needs no survey type (groups, designs, mail invitations, bringing in new people): "create" for
    at least one type (app/kind_access.py). Site admins always. Until 2026-10-18 this was a right of its own
    (UserLogin.may_create, three states with "nach Gruppen"); it is now the fourth level per type
    (konzept-stufe-anlegen-2026-10-18.md), and a new survey of a type asks kind_access.may_create(user, kind).
    New accounts let in by themselves (on_whitelist) get "create" when they are made (_sign_in,
    NEW_ACCOUNTS_MAY_CREATE), not when they came by an invitation (S11 of the audit 2026-10-06)."""
    if user.orcid in current_app.config["ADMIN_ORCIDS"]:
        return True
    from app import kind_access

    return kind_access.creates_any(user)


def new_account_ids():
    """id and registration number for a new account: those of deleted accounts stay taken (DeletedAccount), so
    an old "stay signed in" cookie never fits a new person and a number is never given twice."""
    def top(column):
        return db.session.scalar(db.select(db.func.max(column))) or 0

    return {"id": max(top(User.id), top(DeletedAccount.user_id)) + 1,
            "number": max(top(User.number), top(DeletedAccount.number)) + 1}


def _sign_in(orcid, name, fresh=True):
    for attempt in range(2):
        user = db.session.scalar(db.select(User).filter_by(orcid=orcid))
        created = user is None or user.pending
        if user is not None and user.pending:
            # the account kept since someone added this iD (people_keys, B2) becomes whole: its number now
            user.pending, user.setup_due, user.created_at = False, True, utcnow()
            user.number = new_account_ids()["number"]
            if lang.browser() != lang.STANDARD:
                user.display = {"mode": brand.default_mode(), "lang": lang.browser()}
        elif created:
            user = User(orcid=orcid, setup_due=True, **new_account_ids())  # the setup assistant (account_setup)
            if lang.browser() != lang.STANDARD:  # a new account takes the browser's language once (app/lang.py)
                user.display = {"mode": brand.default_mode(), "lang": lang.browser()}  # the column's default
            db.session.add(user)
        if name:
            user.name = name[:200]  # the column's length; ORCID sets no limit
        user.is_admin = orcid in current_app.config["ADMIN_ORCIDS"]
        user.last_login_at = utcnow()
        try:
            db.session.flush()
            break
        except IntegrityError:
            # two first sign-ins at the same moment (the same iD in two tabs, or two people): id and number are
            # the highest so far plus one, and the other insert took them, or the account itself. Read again once
            # and go on with what is committed now (audit 2026-10-05, L-13)
            db.session.rollback()
            if attempt:
                raise
    # a new account may create surveys of its own from the start; a site admin can take that back on its
    # page (Ralf, 2026-10-05). Only for iDs let in by themselves (list and switch of the administration,
    # ADMIN_ORCIDS):
    # someone who is only here because they were invited to a survey or added to a group works on what is
    # shared until a site admin raises them, or every invitation would hand out the right to invite further
    # people (security audit 2026-10-06, S11). Accounts from before keep what they had.
    may_create = created and current_app.config["NEW_ACCOUNTS_MAY_CREATE"] and on_whitelist(orcid)
    if created:
        from app import signin

        signin.came_in(user)  # counts for the limit of "every ORCID holder" if it came by that alone
        from app import account_types

        # its user type and period (the list's choice, its course or the type for new accounts), then its survey
        # types: the list's choice, a group's preset or the type's, raised by invitations
        account_types.new_account_levels(user, account_types.start_new(user))
        if may_create:
            from app import kind_access

            kind_access.give_create(user, log=False)  # "edit" becomes "create" where the type left "edit"
    # invitations made before this first sign-in already name the account, kept as pending until now (people_keys)
    db.session.commit()
    if not fresh:
        session.pop("signed_in_at", None)  # nor does the time of an earlier sign-in in this browser count for this one
    remember(user, fresh=fresh, via="orcid")
    return user


def remember(user, fresh=True, keep_age=False, via=None):
    """Sign this browser in as the user. fresh: the person has just proved who they are (password, one-time
    link, or ORCID asked to demand the password: fresh=1); deleting the account and adding a password ask for
    that (app/accounts.py). A plain ORCID sign-in is passed through by a browser that is still signed in at
    ORCID and proves nothing about who sits there now; neither does signing the browser in again after "überall
    abmelden", which leaves the time of the sign-in as it is. keep_age: the browser is signed in again without a
    new sign-in ("überall abmelden"); the new session keeps the age of the one before (SESSION_MAX_AGE).
    via: how the person signed in ("orcid", "link", "password", "password+code"; twofactor.SIGNED_IN_VIA, T3 of the
    audit 2026-10-09b); None keeps what this browser's session says (a new password, "überall abmelden")."""
    from app import sessions, twofactor

    before = sessions.end_current()  # a copy of the cookie this browser had does not stay valid (audit 2026-10-09, A5)
    since = before.created_at if keep_age and before is not None and before.user_id == user.id else None
    sessions.start(user, since)  # this sign-in gets its own session: "Abmelden" ends it, copies of its cookie included
    session.pop("csrf_token", None)
    if via is not None:
        session.pop("pw2fa", None)
        session.pop("totp_ok", None)
        session.pop("admin_link", None)
        session[twofactor.SIGNED_IN_VIA] = via
        from app import security_log

        security_log.event("sign_in", account=user.id, via=via)  # audit 2026-10-13, DP-7/AU-7
    keep = bool(session.get("remember_signin", False))
    login_user(user, remember=keep)
    if not keep:
        session["_remember"] = "clear"
    session.permanent = keep
    if fresh:
        session["signed_in_at"] = time.time()


@bp.route("/csrf-token")
def csrf_token():
    """A fresh CSRF token for an open page (app.js): a signed-in researcher's token counts CSRF_HOURS
    (audit 2026-10-18, INJ-4), and an editor stays open longer than that."""
    from flask_wtf.csrf import generate_csrf

    response = current_app.make_response({"token": generate_csrf()})
    response.headers["Cache-Control"] = "no-store"
    return response


def _blocked(orcid):
    from app import accounts

    user = db.session.scalar(db.select(User).filter_by(orcid=orcid))
    return user is not None and accounts.is_blocked(user)


@bp.route("/sign-in", methods=["GET", "POST"])
def sign_in():
    """Both ways in on one page: the ORCID button, and e-mail address with password. Wrong address, wrong
    password and a paused account get the same answer."""
    from app import accounts, limits, passwords, twofactor

    nxt = local_path(request.values.get("next", "")) or ""
    if request.method == "POST":
        session["remember_signin"] = request.form.get("remember") == "1"
        email = request.form.get("email", "")
        allowed = limits.allow_login()
        user = passwords.check(email, request.form.get("password", "")) if allowed else None
        if user is not None:
            limits.login_succeeded_here()  # only failed attempts count toward the limit per sender (H5)
        if not allowed:
            # the limit per sender, not the password: its own message (code review 2026-10-15, H5)
            flash(de_en("Von diesem Netzwerk gab es gerade zu viele Anmeldeversuche. Bitte versuchen Sie es in einigen "
                        "Minuten erneut.",
                        "There have just been too many sign-in attempts from this network. Please try again in a few "
                        "minutes."), "error")
        elif user is None:
            flash(de_en("Anmeldung nicht möglich: E-Mail-Adresse oder Passwort stimmen nicht. Nach {n} Fehlversuchen "
                        "pausiert die Anmeldung für 15 Minuten.",
                        "Sign-in not possible: the email address or the password is not correct. After {n} failed "
                        "attempts, signing in pauses for 15 minutes.", n=passwords.MAX_FAILURES), "error")
        elif accounts.is_blocked(user):
            flash(accounts.blocked_said(user), "error")
        elif not _may_sign_in(user.orcid):
            flash(de_en("Ihr Konto ist (noch) nicht freigeschaltet. Die Admins der Plattform schalten es frei und helfen bei "
                        "Fragen (siehe Impressum).",
                        "Your account is not (yet) allowed to sign in. A platform admin allows it and helps with "
                        "questions (see the legal notice)."), "error")
        elif twofactor.required_at_signin(user) and not twofactor.enabled(user):
            # a password alone never sets up the factor: the setup follows an ORCID sign-in or a one-time link
            flash(de_en("Admins der Plattform brauchen zum Passwort einen Code aus einer Authenticator-App. Bitte einmal mit "
                        "ORCID oder über einen Einmal-Link anmelden und unter „Konto › Profil und Anmeldung“ den zweiten Faktor einrichten.",
                        "Platform admins need a code from an authenticator app besides the password. Please sign in once "
                        "with ORCID or through a one-time link and set up the second factor under “Account › Profile and sign-in”."), "error")
        elif twofactor.required_at_signin(user):
            # the password is right; signed in only after the code (Ralf 2026-10-09)
            session["pw2fa"] = {"user": user.id, "at": time.time(), "next": nxt, "v": _sign_in_version(user.id)}
            return redirect(url_for("auth.sign_in_code"))
        else:
            _signed_in(user)
            return redirect(nxt or url_for("dashboard.index"))
        return render_template("auth/sign_in.html", nxt=nxt, email=email), 401
    return render_template("auth/sign_in.html", nxt=nxt, email="")

def _signed_in(user, via="password"):
    """The end of a sign-in with e-mail and password (and, for site admins, the code: via "password+code")."""
    from app import invites, joinlinks

    session.pop(invites.SESSION_KEY, None)  # an invitation is taken only by the ORCID way it was opened for (T6)
    session.pop(joinlinks.SESSION_KEY, None)  # so is a join link of a group (audit 2026-10-10, A6)
    user.is_admin = user.orcid in current_app.config["ADMIN_ORCIDS"]
    user.last_login_at = utcnow()
    db.session.commit()
    remember(user, via=via)
    from app import passwords

    passwords.remember_device(user)  # the lock after 100 wrong tries does not hold this browser (AU-9 rest)


PW2FA_SECONDS = 300  # the code page after the right password stays open this long


def _sign_in_version(user_id):
    """The account's session_version (0 without a sign-in row). The half sign-in after the right password carries it:
    a new or removed password, a block or "Überall abmelden" before the code ends it (sign_in_code)."""
    return int(db.session.scalar(db.select(UserLogin.session_version).where(UserLogin.user_id == user_id)) or 0)


@bp.route("/sign-in/code", methods=["GET", "POST"])
def sign_in_code():
    """Accounts with an enabled factor (and password admins): an authenticator code or backup code.
    Only then is the browser signed in."""
    from app import accounts, twofactor

    pending = session.get("pw2fa")
    user = db.session.get(User, pending.get("user")) if isinstance(pending, dict) and pending.get("user") else None
    if (user is None or time.time() - float(pending.get("at") or 0) > PW2FA_SECONDS or accounts.is_blocked(user)
            or pending.get("v") != _sign_in_version(user.id) or not twofactor.enabled(user)):
        session.pop("pw2fa", None)
        flash(de_en("Bitte erneut mit E-Mail-Adresse und Passwort anmelden.", "Please sign in again with email address and password."), "warning")
        return redirect(url_for("auth.sign_in"))
    if request.method == "POST":
        backups = twofactor.backup_left(user)
        if twofactor.check_any(user, request.form.get("code")):
            nxt = local_path(pending.get("next") or "")
            session.pop("pw2fa", None)
            _signed_in(user, via="password+code")
            twofactor._passed()  # the administration asks for no second code now
            if twofactor.backup_left(user) < backups:
                twofactor.tell("backup_code_used", user)  # T9
            if twofactor.backup_left(user) <= 2:
                flash(de_en("Nur noch {n} Ersatzcodes übrig: Neue gibt es unter „Konto › Profil und Anmeldung“.",
                            "Only {n} backup codes left: new ones under “Account › Profile and sign-in”.", n=twofactor.backup_left(user)), "warning")
            return redirect(nxt or url_for("dashboard.index"))
        db.session.commit()
        from app import security_log

        security_log.event("second_factor_failed", account=user.id, via="sign_in")
        twofactor.alert_wrong_code(user)  # the password was right: Slack hears of it, once an hour (T2)
        flash(de_en("Der Code stimmt nicht oder wurde schon verwendet.", "The code is not correct or was already used.")
              + twofactor.too_many_said(user), "error")
    response = current_app.make_response(render_template("auth/sign_in_code.html"))
    response.headers["Cache-Control"] = "no-store"
    return response



@bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """"Passwort vergessen?": the person leaves the address they sign in with. With e-mail (app/mail.py) a link valid
    for an hour goes to that address; without, the site admins see the request under Administration > Personen and
    pass a new one-time link on. The answer is the
    same for every address and also once the sender asked too often (limits.allow_reset), so the page says nothing
    about which addresses have an account."""
    from app import limits, mail, passwords

    if request.method == "POST":
        email = passwords.normal_email(request.form.get("email"))
        if email is None:
            flash(de_en("Bitte eine gültige E-Mail-Adresse angeben.", "Please enter a valid email address."), "error")
            return render_template("auth/forgot_password.html", email=request.form.get("email", "")[:254]), 422
        if limits.allow_reset():
            from app import account_jobs

            account_jobs.enqueue_reset(email, brand.public_base())
        if mail.enabled():
            flash(de_en("Gehört die Adresse zu einem Konto mit Passwort, kommt gleich eine E-Mail mit einem Link, mit dem Sie "
                        "ein neues Passwort setzen (gültig {hours} Stunde). Ihr bisheriges Passwort gilt bis dahin weiter.",
                        "If the address belongs to an account with a password, an email with a link to set a new password "
                        "arrives shortly (valid for {hours} hour). Your current password stays valid until then.",
                        hours=passwords.RESET_HOURS), "success")
            return redirect(url_for("auth.sign_in"))
        flash(de_en("Anfrage gesendet. Gehört die Adresse zu einem Konto, sehen die Admins der Plattform Ihre Anfrage und "
                    "geben Ihnen einen Link, mit dem Sie ein neues Passwort setzen. Askurious selbst schreibt Ihnen keine "
                    "E-Mail. Ihr bisheriges Passwort gilt bis dahin weiter.",
                    "Request sent. If the address belongs to an account, the platform admins see your request and give "
                    "you a link to set a new password. Askurious itself does not email you. Your current password stays "
                    "valid until then."), "success")
        return redirect(url_for("auth.sign_in"))
    return render_template("auth/forgot_password.html", email="")


@bp.route("/password/<token>", methods=["GET", "POST"])
def set_password(token):
    """The one-time link a site admin passed on (or "Passwort vergessen?" mailed): set the password, and be signed
    in. A site admin (twofactor.required_at_signin) is not signed in by the link: whoever reads the mailbox must not
    pass the second factor (audit 2026-10-09b, T1; Ralf: the reset leads into the code step). With a factor the code
    page follows, as after the password at the sign-in; without one the password is saved and the factor is set up
    after a sign-in with ORCID."""
    from app import accounts, mail, passwords, twofactor

    row = passwords.link_row(token)
    user = db.session.get(User, row.user_id) if row is not None else None
    if user is None or accounts.is_blocked(user) or (row.created_by_id is not None and user.has_orcid):  # site admins too (Ralf 2026-10-09, in place of H1)
        flash(de_en("Dieser Link ist nicht mehr gültig: Er wurde schon benutzt, ist abgelaufen oder durch einen neueren "
                    "ersetzt. Einen neuen bekommen Sie über „Passwort vergessen?“.",
                    "This link is no longer valid: it was already used, has expired or was replaced by a newer one. "
                    "You get a new one through “Forgot your password?”.") if mail.enabled() else de_en(
                    "Dieser Link ist nicht mehr gültig: Er wurde schon benutzt, ist abgelaufen oder durch einen neueren "
                    "ersetzt. Bitte wenden Sie sich an einen Admin der Plattform.",
                    "This link is no longer valid: it was already used, has expired or was replaced by a newer one. "
                    "Please contact a platform admin."), "error")
        return redirect(url_for("auth.sign_in"))
    login = passwords.login_row(user, create=True)
    if request.method == "POST":
        password = request.form.get("password", "")
        error = passwords.problem(password, user.name, login.email)
        if not error and password != request.form.get("repeat", ""):
            error = de_en("Die beiden Eingaben stimmen nicht überein.", "The two entries do not match.")
        if error:
            flash(error, "error")
        else:
            claimed = db.session.execute(db.update(type(row)).where(
                type(row).id == row.id, type(row).used_at.is_(None), type(row).expires_at > utcnow()
            ).values(used_at=utcnow()).execution_options(synchronize_session=False)).rowcount
            if claimed != 1:
                db.session.rollback()
                return redirect(url_for("auth.sign_in"))
            used_at, created_by = utcnow(), row.created_by_id
            passwords.set_password(user, password)
            from app import account_types

            account_types.start_pending(user, used_at)  # the period of an account an admin made starts now (N4)
            from app import security_log

            security_log.event("one_time_link_used", account=user.id, by=created_by)
            if created_by is not None and mail.enabled() and login.email:
                from app import lang

                with mail.in_language(lang.of(user)):
                    mail.send(login.email, de_en("Askurious: Passwort geändert", "Askurious: password changed"),
                              de_en("Ihr Passwort wurde über einen von einem Admin erzeugten Einmal-Link geändert. Falls Sie das nicht waren, wenden Sie sich bitte sofort an die Plattform-Admins.",
                                    "Your password was changed through a one-time link created by an admin. If this was not you, please contact the platform admins immediately.") + mail.signature())
            if twofactor.required_at_signin(user):
                db.session.commit()
                twofactor.tell("admin_password_reset", user)  # T9
                if twofactor.enabled(user):
                    # read after the commit: the new password raised the version, the old one's half sign-in ended
                    session["pw2fa"] = {"user": user.id, "at": time.time(), "next": "", "v": _sign_in_version(user.id)}
                    flash(de_en("Passwort gespeichert. Bitte jetzt noch den Code aus der Authenticator-App eingeben.",
                                "Password saved. Now please enter the code from the authenticator app."), "success")
                    return redirect(url_for("auth.sign_in_code"))
                flash(de_en("Passwort gespeichert. Admins der Plattform brauchen zum Passwort einen Code aus einer "
                            "Authenticator-App: Bitte mit ORCID anmelden und unter „Konto › Profil und Anmeldung“ den zweiten Faktor einrichten.",
                            "Password saved. Platform admins need a code from an authenticator app besides the password: "
                            "please sign in with ORCID and set up the second factor under “Account › Profile and sign-in”."), "warning")
                return redirect(url_for("auth.sign_in"))
            user.last_login_at = used_at
            db.session.commit()
            remember(user, via="link")
            flash(de_en("Passwort gespeichert. Sie sind angemeldet.", "Password saved. You are signed in."), "success")
            return redirect(url_for("dashboard.index"))
    response = current_app.make_response(render_template(
        "auth/set_password.html", user=user, email=login.email, min_length=passwords.MIN_LENGTH))
    # the address of this page is a secret: not kept by the browser, not passed on to other sites. "same-origin", not
    # "no-referrer": over https the CSRF check wants the Referer of the form's own POST ("The referrer header is
    # missing", 2026-10-09, the first password set on polltergeist.de)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def _pkce_challenge(verifier):
    """The S256 code challenge of a PKCE verifier: base64url of its SHA-256, without padding (RFC 7636, 4.2)."""
    import base64
    import hashlib

    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


@bp.route("/login", methods=["GET", "POST"])
def login():
    # "Angemeldet bleiben" only from the form (POST with the CSRF token): a link on another site must not choose it,
    # nor start a sign-in at ORCID that ends a connection under way (audit 2026-10-22, A-03)
    if request.method == "GET" and request.headers.get("Sec-Fetch-Site") == "cross-site":
        return redirect(url_for("auth.sign_in", next=local_path(request.args.get("next", "")) or None))
    if request.method == "POST":
        session["remember_signin"] = request.form.get("remember") == "1"
    cfg = current_app.config
    if not cfg["ORCID_CLIENT_ID"]:
        # a page that says why and leads to the other way in, not a bare 503 (UI audit 2026-10-12, U58)
        return render_template("error.html", kind="orcid_off", survey_id=None, signed_in=current_user.is_authenticated,
                               nxt=local_path(request.args.get("next", "")) or None), 503
    state = secrets.token_urlsafe(24)
    session["oauth_state"] = state
    # PKCE with S256 (RFC 7636; RFC 9700 asks for it of confidential clients too, against an injected authorisation
    # code; audit 2026-10-11, AU6): the verifier stays in the session, ORCID gets its hash
    verifier = secrets.token_urlsafe(48)
    session["oauth_verifier"] = verifier
    session["next"] = request.args.get("next", "")
    # fresh=1: ORCID asks for the password again instead of passing a signed-in browser through. Kept next to
    # the state (as the time the round trip began), so the callback knows whether it was asked for. Every round
    # trip keeps its start (oauth_began): a plain sign-in for which ORCID did ask for the password counts as fresh
    # too, so an admin who just signed in is not sent to ORCID again for the first protected action (2026-10-15).
    fresh = bool(request.values.get("fresh"))
    session["oauth_fresh"] = time.time() if fresh else 0
    session["oauth_began"] = time.time()
    # link=1 from the form on the page "Konto": the iD that comes back is connected to the signed-in account
    # without ORCID. Only as a POST (with the CSRF token of the page): a link on another site must not start it.
    # The account proves itself first, with its current password and, where set up, a code of the second factor:
    # an open session alone gave whoever sat at the computer a lasting way in with their own iD (audit 2026-10-22,
    # A-01).
    session.pop("orcid_link", None)
    if request.method == "POST" and request.form.get("link") and current_user.is_authenticated             and not current_user.has_orcid:
        refused = _link_refused(current_user)
        if refused is not None:
            return refused
        session["orcid_link"] = {"user": current_user.id, "at": time.time()}
    query = urlencode(
        {
            "client_id": cfg["ORCID_CLIENT_ID"],
            "response_type": "code",
            # openid: ORCID then says in its answer when the person signed in there (auth_time, see _signed_in_since);
            # it grants nothing beyond /authenticate (the iD and the name)
            "scope": "openid",
            "redirect_uri": _redirect_uri(),
            "state": state,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
            **({"prompt": "login"} if fresh else {}),
        }
    )
    return redirect(f"{cfg['ORCID_BASE_URL']}/oauth/authorize?{query}")


@bp.route("/orcid/callback")
def orcid_callback():
    cfg = current_app.config
    state = session.pop("oauth_state", None)
    verifier = session.pop("oauth_verifier", None)
    link = session.pop("orcid_link", None)
    began = session.pop("oauth_fresh", 0) or 0
    started = session.pop("oauth_began", 0) or began
    if not state or request.args.get("state") != state:
        abort(400, "Invalid sign-in state. Please try again.")
    if "error" in request.args or "code" not in request.args:
        flash(de_en("ORCID-Anmeldung abgebrochen.", "ORCID sign-in was cancelled."), "warning")
        return redirect(url_for("accounts.index" if link else "public.index"))

    try:
        resp = requests.post(
            f"{cfg['ORCID_BASE_URL']}/oauth/token",
            data={
                "client_id": cfg["ORCID_CLIENT_ID"],
                "client_secret": cfg["ORCID_CLIENT_SECRET"],
                "grant_type": "authorization_code",
                "code": request.args["code"],
                "redirect_uri": _redirect_uri(),
                # a round trip begun before PKCE (no verifier in the session) goes on without it (AU6)
                **({"code_verifier": verifier} if verifier else {}),
            },
            headers={"Accept": "application/json"},
            timeout=15,
        )
        if resp.status_code != 200:
            current_app.logger.warning("ORCID token exchange failed: %s %s", resp.status_code, resp.text[:300])
            abort(502, "ORCID sign-in failed.")
        data = resp.json()
    except (requests.RequestException, ValueError) as e:  # ORCID unreachable or slow, or an answer that is no JSON
        current_app.logger.warning("ORCID token exchange failed: %s", e)
        abort(502, "ORCID sign-in failed.")
    if not isinstance(data, dict):
        current_app.logger.warning("ORCID token exchange failed: answer is no object")
        abort(502, "ORCID sign-in failed.")
    orcid = data.get("orcid", "")
    if not ORCID_RE.match(orcid):
        abort(502, "ORCID returned no valid iD.")
    # fresh when ORCID asked for the password during this round trip, whether or not fresh=1 asked it to: a browser
    # still signed in at ORCID is passed through with the time of that earlier sign-in, and that does not count
    fresh = bool(started) and _signed_in_since(data.get("id_token"), started, orcid)
    if began and not fresh:
        # asked for a fresh sign-in, but ORCID did not ask for the password (the request was changed on its way, or
        # ORCID sent no time): the person is signed in, the sign-in does not count as fresh
        flash(de_en("ORCID hat das Passwort nicht neu abgefragt; die Anmeldung gilt deshalb nicht als neu. Bitte melden "
                    "Sie sich bei ORCID ab und versuchen Sie es noch einmal.",
                    "ORCID did not ask for the password again, so the sign-in does not count as fresh. Please sign out "
                    "at ORCID and try once more."), "warning")
    if link is not None:
        return _connect(link, orcid, data.get("name", ""), fresh)
    from app import joinlinks

    joinlinks.reopen_expired(orcid)  # an ended account of a course's type joining a new course opens again (N8)
    if _blocked(orcid):
        from app import accounts

        flash(accounts.blocked_said(db.session.scalar(db.select(User).filter_by(orcid=orcid))), "error")
        return redirect(url_for("public.index"))
    from app import invites

    new = db.session.scalar(db.select(User.id).filter_by(orcid=orcid, pending=False)) is None
    invited = invites.accept_after_orcid(orcid, data.get("name", ""))  # an invitation by mail opened before (M5)
    if invited:
        session["next"] = invited
    from app import joinlinks

    joined = joinlinks.accept_after_orcid(orcid, data.get("name", ""))  # a join link of a group opened before
    if joined:
        session["next"] = joined
    from app import signup_links

    signup_links.accept_after_orcid(orcid, data.get("name", ""))  # a registration link opened before: on the list now
    if not _may_sign_in(orcid):
        from app import signin

        if signin.mode() == signin.MODE_CAP:  # up to the limit, and the limit is reached: the waiting list (E12)
            signin.wait(orcid, data.get("name", ""))
            # the iD ORCID just confirmed: only it may be on the list, or anyone could take places for others' iDs
            session[WAIT_KEY] = {"orcid": orcid, "at": time.time()}
            return redirect(url_for("auth.waiting_list"))
        flash(de_en("Ihr ORCID-Konto ist (noch) nicht freigeschaltet.", "Your ORCID account is not (yet) allowed to sign in."),
              "error")
        return redirect(url_for("public.index"))

    user = _sign_in(orcid, data.get("name", ""), fresh=fresh)
    if new:  # the first sign-in is the registration: the operators hear of it in Slack
        from app import notify

        notify.new_user(user)
    nxt = session.pop("next", "")
    return redirect(local_path(nxt) or url_for("dashboard.index"))


WAIT_KEY = "waiting_list"   # in the session: the iD ORCID confirmed when it went on the waiting list
WAIT_SECONDS = 24 * 3600    # that long the page of the waiting list knows the person without a new ORCID sign-in


def _waiting_row():
    from app.models import SignInWait

    held = session.get(WAIT_KEY)
    if not isinstance(held, dict) or time.time() - float(held.get("at") or 0) > WAIT_SECONDS:
        session.pop(WAIT_KEY, None)
        return None
    orcid = held.get("orcid")
    return db.session.get(SignInWait, orcid) if ORCID_RE.match(str(orcid or "")) else None


@bp.route("/waiting-list", methods=["GET", "POST"])
def waiting_list():
    """The page after an ORCID sign-in that found no free place (signin.wait): the place in the queue and, with
    e-mail on, an address for one mail when a place is free (Philipp, 2026-10-13). The iD is the one ORCID confirmed
    in this browser; the page takes no typed iD, so nobody puts others on the list. Without it (opened directly,
    or more than a day later) the page asks for a sign-in with ORCID, which finds the entry again."""
    from app import mail, passwords, signin

    row = _waiting_row()
    email = ""
    if request.method == "POST" and row is not None:
        action = request.form.get("action")
        if action == "leave":
            signin.unwait(row.orcid)
            session.pop(WAIT_KEY, None)
            flash(de_en("Sie stehen nicht mehr auf der Warteliste; Ihre Angaben sind gelöscht.",
                        "You are no longer on the waiting list; your details are deleted."), "success")
            return redirect(url_for("public.index"))
        if action == "email" and mail.enabled():
            raw = request.form.get("email", "")
            address = passwords.normal_email(raw) if raw.strip() else None
            if raw.strip() and address is None:
                flash(de_en("Bitte eine gültige E-Mail-Adresse angeben.", "Please enter a valid email address."), "error")
                email = raw[:254]
            else:
                signin.leave_email(row, address, lang.current())
                flash(de_en("Gespeichert. Ist ein Platz frei, bekommen Sie eine E-Mail an {a}.",
                            "Saved. When a place is free, you get an email at {a}.", a=address) if address else
                      de_en("Die E-Mail-Adresse ist gelöscht.", "The email address is deleted."), "success")
                return redirect(url_for("auth.waiting_list"))
    if row is not None and row.admitted_at is None and signin.mode() == signin.MODE_CAP:
        signin.admit_waiting()  # a place became free since: it belongs to the first of the list
    response = current_app.make_response((render_template(
        "auth/waiting_list.html", row=row, place=signin.place(row) if row is not None else None,
        email=email or (row.email if row is not None else "") or "", days=signin.WAIT_DAYS),
        422 if email else 200))
    response.headers["Cache-Control"] = "no-store"
    return response


FRESH_SLACK = 120  # seconds the clocks of ORCID and of this server may differ


def _signed_in_since(id_token, began, orcid=None):
    """True if ORCID says the person signed in there after this round trip began. With the scope "openid" the
    answer of ORCID's token address holds an ID token (a JWT) whose claim auth_time is the moment of the sign-in
    at ORCID. Its signature, issuer, audience, expiry and iD are checked against ORCID's published keys
    (app/orcid_token.py; audit 2026-10-18, AUTH-2): a token that does not pass, or keys that cannot be read, count
    as "not fresh". Without it nothing proves that "prompt=login" reached ORCID: whoever sits at an open computer
    could take it out of the address, and ORCID would pass the signed-in browser through."""
    from app import orcid_token

    claims = orcid_token.claims(id_token, orcid) if id_token else None
    try:
        return claims is not None and float(claims["auth_time"]) >= began - FRESH_SLACK
    except (KeyError, TypeError, ValueError):
        return False


def _link_refused(user):
    """None if the account proved itself for connecting ORCID (A-01): the current password where it has one, else a
    fresh sign-in, and a code of the second factor where it is set up. Else the way back to "Konto" with a message."""
    from app import accounts, passwords, twofactor

    back = redirect(url_for("accounts.index") + "#anmeldung")
    if passwords.has_password(user):
        if not passwords.confirm(user, request.form.get("current", "")):
            ended = accounts._too_many_wrong(user)
            if ended:
                return ended
            if ended is None:
                flash(de_en("Das aktuelle Passwort stimmt nicht.", "The current password is not correct."), "error")
            return back
    elif not accounts._fresh():
        flash(de_en("Die Anmeldung liegt zu lange zurück. Bitte melden Sie sich neu an und versuchen Sie es dann erneut.",
                    "The sign-in was too long ago. Please sign in again and then try once more."), "error")
        return back
    if twofactor.enabled(user) and not twofactor.check_any(user, request.form.get("code", "")):
        db.session.commit()  # the wrong try counts
        flash(de_en("Der Code stimmt nicht.", "The code is not correct."), "error")
        return back
    db.session.commit()
    return None


def _connect(link, orcid, name, fresh=False):
    """The signed-in account without ORCID proved this iD at ORCID: from now on it is the account's key. `link` is
    what login() kept after the account proved itself; the round trip must have begun within FRESH_SECONDS."""
    from app import accounts

    back = redirect(url_for("accounts.index"))
    user_id = link.get("user") if isinstance(link, dict) else None
    began = link.get("at") if isinstance(link, dict) else None
    if (not current_user.is_authenticated or current_user.id != user_id or current_user.has_orcid
            or not isinstance(began, (int, float)) or time.time() - began > accounts.FRESH_SECONDS):
        flash(de_en("Das Verbinden mit ORCID wurde abgebrochen. Bitte erneut versuchen.",
                    "Connecting with ORCID was cancelled. Please try again."), "error")
        return back
    if db.session.scalar(db.select(User.id).filter_by(orcid=orcid, pending=False)) is not None:
        flash(de_en("Diese ORCID iD hat schon ein eigenes Konto bei Askurious. Zwei Konten lassen sich noch nicht "
                    "zusammenführen; bitte wenden Sie sich an einen Admin der Plattform.",
                    "This ORCID iD already has its own account at Askurious. Two accounts cannot be merged yet; please "
                    "contact a platform admin."), "error")
        return back
    user = db.session.get(User, user_id)
    accounts.link_orcid(user, orcid, name)
    user.is_admin = orcid in current_app.config["ADMIN_ORCIDS"]
    db.session.commit()
    if fresh:
        session["signed_in_at"] = time.time()
    from app import mail, passwords, security_log

    security_log.event("orcid_linked", account=user.id)
    login = passwords.login_row(user)
    if mail.enabled() and login is not None and login.email:  # the owner hears of it, also when it was not them
        with mail.in_language(lang.of(user)):
            mail.send(login.email, de_en("Askurious: ORCID iD verbunden", "Askurious: ORCID iD connected"),
                      de_en("Ihr Konto ist jetzt mit der ORCID iD {orcid} verbunden; mit ihr kann man sich bei Ihrem Konto "
                            "anmelden. Falls Sie das nicht waren, trennen Sie die iD unter „Konto“ und wenden Sie sich an "
                            "die Plattform-Admins.",
                            "Your account is now connected with the ORCID iD {orcid}; it can be used to sign in to your "
                            "account. If this was not you, disconnect the iD under “Account” and contact the platform "
                            "admins.", orcid=orcid) + mail.signature())
    flash(de_en("Ihr Konto ist jetzt mit Ihrer ORCID iD verbunden. Sie können sich mit ORCID anmelden oder wie bisher "
                "mit E-Mail-Adresse und Passwort.",
                "Your account is now connected with your ORCID iD. You can sign in with ORCID or, as before, with "
                "email address and password."), "success")
    return back


@bp.route("/dev-login", methods=["GET", "POST"])
def dev_login():
    """Password-less login for local development. Disabled unless DEV_LOGIN=1, and never there for a request
    that came in under another name than this computer's own, or from another computer, with or without a proxy
    (app.is_local_request; audit 2026-10-18, CFG-01): a server that was started with DEV_LOGIN by mistake (create_app can only look at
    PUBLIC_BASE_URL) does not hand out sign-ins to the world."""
    from app import is_local_request

    if not current_app.config["DEV_LOGIN"] or not is_local_request():
        abort(404)
    session["remember_signin"] = request.values.get("remember") == "1"
    orcid = request.values.get("orcid", "0000-0000-0000-0000")
    if not ORCID_RE.match(orcid):
        abort(400)
    if _blocked(orcid):
        abort(403)
    _sign_in(orcid, request.values.get("name", "Dev User"))
    return redirect(local_path(request.values.get("next", "")) or url_for("dashboard.index"))


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    from app import sessions

    sessions.end(current_user.id, sessions.current_sid())  # a copy of this browser's cookies signs in no more
    logout_user()
    session.clear()
    session["_remember"] = "clear"  # Flask-Login must still delete the persistent cookie
    return redirect(url_for("public.index"))
