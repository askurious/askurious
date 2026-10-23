"""Second factor for the site admins: a six-digit code from an authenticator app (TOTP, RFC 6238), asked before the
pages of the administration (security audit 2026-10-04, H5).

Ralf, 2026-10-13: site admins sign in with ORCID without mandatory app TOTP, or with password plus code.
Only a session explicitly marked as an ORCID sign-in gets the exemption, regardless of ADMIN_TOTP. Unknown/link
sessions still need a code for admin pages and rights. ADMIN_TOTP offers the factor to other accounts; admins can
always set it up for their password sign-in. Re-authenticate with ORCID after losing the phone.

The secret (160 bits, base32) is kept encrypted with the app's key box (secrets_box) in UserLogin.totp_box; a code
counts once (UserLogin.totp_step), and the time step before and after the current one is accepted (30 seconds, the
clocks of phone and server may differ a little). Five wrong codes within five minutes pause the tries for that
account in this server process; besides, every wrong code counts in the database (UserLogin.failed_count, the
counter of wrong passwords, code_weight() each), so CODE_FAILURES wrong codes pause the account for 15 minutes in
all server processes and after a restart. The pause does not reset the count of wrong tries in a row
(UserLogin.failed_streak): at passwords.HARD_LIMIT (100, NIST SP 800-63B-4) the factor locks until a site admin
releases it (audit 2026-10-11, AU3). For an account that needs the code at the sign-in a right password does
not reset that counter, only a right code does (audit 2026-10-09b, T2). A code and a backup code are taken with a
conditional UPDATE, so two requests with the same code at the same moment do not both get in (T4); backup codes are
kept as salted scrypt hashes like passwords (T5).

Lost phone: sign in with ORCID; renew the factor with a backup code or have the operator reset it. ADMIN_TOTP off does not
bypass mandatory password TOTP. An unreadable secret stays enabled; only a verified ORCID session is exempt.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import threading
import time
from collections import deque
from datetime import timedelta
from urllib.parse import quote

from flask import Blueprint, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required
from markupsafe import Markup

from app.extensions import db
from app.lang import de_en
from app.links import local_path

bp = Blueprint("twofactor", __name__, url_prefix="/account/second-factor")

STEP = 30
DIGITS = 6
ISSUER = "Askurious"
TRIES, TRIES_WINDOW = 5, 300
_tries, _lock = {}, threading.Lock()
CODE_FAILURES = 10  # wrong codes (in the database, all server processes) until the account pauses
ALERT_GAP = timedelta(hours=1)  # at most one Slack message about wrong codes per account in this time

# how this browser signed in (session key SIGNED_IN_VIA, set by auth.remember): a password session without a code
# never acts as a site admin and never sets the factor up (audit 2026-10-09b, T3)
SIGNED_IN_VIA = "signed_in_via"
VIA_PASSWORD, VIA_CODE, VIA_ORCID, VIA_LINK = "password", "password+code", "orcid", "link"


# --- the code ------------------------------------------------------------------------------------------------


def active():
    """Whether ADMIN_TOTP is set on this server. Only gate() reads it, for accounts that do not need a code anyway;
    the factor itself is open to every account (T01-N4)."""
    return bool(current_app.config.get("ADMIN_TOTP", False))


def required_at_signin(user):
    """Site admins (ADMIN_ORCIDS) who sign in with a password give a code as well (Ralf 2026-10-09): ORCID or
    password plus code. Other accounts choose whether to set up a factor; once enabled it protects password sign-in."""
    return _site_admin(user) or enabled(user)


def _only_when_active():
    """Voluntary setup is available to every signed-in account (Ralf, 2026-10-13)."""
    from flask import abort

    if not current_user.is_authenticated:
        abort(404)


def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret, step):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** DIGITS).zfill(DIGITS)


def matching_step(secret, code, now=None, last=None):
    """The time step the code belongs to (now and one either side), or None; never one at or before `last`."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS or not secret:  # an empty key would give codes anyone can compute
        return None
    current = int((now if now is not None else time.time()) // STEP)
    for step in (current - 1, current, current + 1):
        if (last is None or step > last) and hmac.compare_digest(code_at(secret, step), code):
            return step
    return None


def uri(secret, user):
    from app import brand

    issuer = brand.name() or ISSUER  # the name of the domain it was set up under (app/brand.py)
    label = quote(f"{issuer}:{user.display_name or user.orcid}", safe="")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits={DIGITS}&period={STEP}"


# --- backup codes: eight, each once, shown once at the setup (Ralf 2026-10-09) ---------------------------------

BACKUP_COUNT, BACKUP_LENGTH = 8, 8
_BACKUP_LETTERS = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i


def _backup_plain(code):
    return "".join(ch for ch in str(code or "").lower() if ch.isalnum())


def _backup_hash(code):
    """A new backup code as kept: salted scrypt like a password (app/passwords.py; ASVS 5.0 V6.5.2, T5). The
    value says its kind ("scrypt:...$salt$hash"); the unsalted SHA-256 of before (64 hex digits) is still taken
    until new codes replace it."""
    from werkzeug.security import generate_password_hash

    return generate_password_hash(_backup_plain(code))


def _backup_matches(kept, code):
    if "$" in str(kept):
        from werkzeug.security import check_password_hash

        return check_password_hash(kept, _backup_plain(code))
    return hmac.compare_digest(str(kept), hashlib.sha256(_backup_plain(code).encode()).hexdigest())


def new_backup_codes(user):
    """Eight fresh backup codes (the ones before end); only their hashes are kept, the codes are shown once."""
    import json

    codes = ["".join(secrets.choice(_BACKUP_LETTERS) for _ in range(4)) + "-"
             + "".join(secrets.choice(_BACKUP_LETTERS) for _ in range(4)) for _ in range(BACKUP_COUNT)]
    _row(user, create=True).totp_backup = json.dumps([_backup_hash(c) for c in codes])
    return codes


def backup_left(user):
    import json

    row = _row(user)
    try:
        return len(json.loads(row.totp_backup)) if row is not None and row.totp_backup else 0
    except ValueError:
        return 0


def use_backup(user, code):
    """True and the code is gone, if it is one of the account's backup codes. Taken with a conditional UPDATE on
    the list as it was read: of two requests with the same code at the same moment only one gets in (T4)."""
    import json

    from sqlalchemy import update
    from sqlalchemy.orm.attributes import set_committed_value

    from app.models import UserLogin

    row = _row(user)
    if row is None or not row.totp_backup or _paused(user):
        return False
    before = row.totp_backup
    try:
        hashes = json.loads(before)
    except ValueError:
        return False
    if len(_backup_plain(code)) == BACKUP_LENGTH:
        for index, kept in enumerate(hashes):
            if _backup_matches(kept, code):
                left = json.dumps(hashes[:index] + hashes[index + 1:])
                db.session.flush()
                done = db.session.execute(
                    update(UserLogin).where(UserLogin.user_id == user.id, UserLogin.totp_backup == before)
                    .values(totp_backup=left, failed_count=0, failed_streak=0).execution_options(synchronize_session=False)).rowcount
                if done != 1:
                    break  # another request took a code in between: this one counts as wrong
                set_committed_value(row, "totp_backup", left)
                set_committed_value(row, "failed_count", 0)
                set_committed_value(row, "failed_streak", 0)
                with _lock:
                    _tries.pop(user.id, None)
                return True
    _failed(user)
    return False


def check_any(user, code):
    """A code from the app (six digits), or else one of the backup codes (eight letters and digits)."""
    text = str(code or "")
    if len("".join(ch for ch in text if ch.isalnum())) == BACKUP_LENGTH:
        return use_backup(user, text)
    return check(user, text)


# --- the account's factor ----------------------------------------------------------------------------------------


def _row(user, create=False):
    from app import passwords

    return passwords.login_row(user, create=create)


def secret_of(user):
    from app import secrets_box

    row = _row(user)
    return secrets_box.decrypt(row.totp_box) if row is not None and row.totp_box else ""


def enabled(user):
    """Set up: a secret is stored, also when it can no longer be read (unreadable(); K1)."""
    row = _row(user)
    return bool(row is not None and row.totp_box)


def unreadable(user):
    """A stored secret the key box cannot open (SECRET_KEY changed without TOKEN_KEY): no code is right then."""
    return enabled(user) and not secret_of(user)


def _paused(user):
    """Too many wrong codes: in this server process (TRIES in TRIES_WINDOW), or the account pauses (locked_until in
    the database, after CODE_FAILURES wrong codes or many wrong passwords)."""
    from app.models import utcnow

    from app import passwords

    row = _row(user)
    if row is not None and row.locked_until is not None and row.locked_until > utcnow():
        return True
    if passwords.hard_locked(row, passwords.known_device(row)):  # HARD_LIMIT wrong tries in a row (AU3, AU-9 rest)
        return True
    now = time.monotonic()
    with _lock:
        times = _tries.get(user.id) or deque()
        while times and now - times[0] > TRIES_WINDOW:
            times.popleft()
        return len(times) >= TRIES


def code_weight():
    """What a wrong code adds to the account's counter of wrong passwords: CODE_FAILURES of them reach the
    account's pause (passwords.ACCOUNT_FAILURES), which so stays as it was decided for passwords."""
    from app import passwords

    return max(1, passwords.ACCOUNT_FAILURES // CODE_FAILURES)


def _failed(user, count=True):
    """A wrong code: in this process, and (count) in the database for all processes (T2). Added in the database
    itself, so parallel wrong codes all count; the caller commits."""
    with _lock:
        _tries.setdefault(user.id, deque()).append(time.monotonic())
    row = _row(user)
    if not count or row is None:
        return
    from sqlalchemy import update

    from app import passwords
    from app.models import UserLogin, utcnow

    db.session.flush()
    db.session.execute(update(UserLogin).where(UserLogin.user_id == user.id)
                       .values(failed_count=db.func.coalesce(UserLogin.failed_count, 0) + code_weight())
                       .execution_options(synchronize_session=False))
    db.session.refresh(row, ["failed_count", "locked_until"])
    if row.failed_count >= passwords.ACCOUNT_FAILURES:
        row.failed_count, row.locked_until = 0, utcnow() + passwords.PAUSE
    # and in the row of wrong tries the pause does not reset: at passwords.HARD_LIMIT the factor locks (AU3)
    passwords.note_failure(row)


def too_many_said(user):
    """What the code page adds after a wrong code when no code is taken now: a pause of some minutes, or the lock
    after passwords.HARD_LIMIT wrong tries in a row (AU3)."""
    from app import passwords

    row = _row(user)
    if passwords.hard_locked(row, passwords.known_device(row)):
        return de_en(" Nach {n} falschen Versuchen in Folge ist die Anmeldung mit Passwort und Code gesperrt. Die Admins "
                     "der Plattform können sie freigeben; sonst endet die Sperre nach {h} Stunden.",
                     " After {n} wrong attempts in a row, signing in with password and code is locked. A platform admin "
                     "can release it; otherwise the lock ends after {h} hours.",
                     n=passwords.HARD_LIMIT, h=int(passwords.HARD_LOCK.total_seconds() // 3600))
    if _paused(user):
        return de_en(" Zu viele Versuche: bitte in einigen Minuten erneut.", " Too many attempts: please try again in a few minutes.")
    return ""


def check(user, code, secret=None):
    """True if the code is right for the account (or for `secret` while setting it up); notes the step used.
    The step is taken with a conditional UPDATE (only a later one than the last used): of two requests with the
    same code at the same moment only one gets in (T4). A right code resets the counter of wrong tries."""
    if _paused(user):
        return False
    row = _row(user, create=True)
    step = matching_step(secret or secret_of(user), code, last=row.totp_step if secret is None else None)
    if step is None:
        _failed(user, count=secret is None)  # a typo while setting up does not pause the account
        return False
    if secret is not None:
        row.totp_step = step
    else:
        from sqlalchemy import or_, update
        from sqlalchemy.orm.attributes import set_committed_value

        from app.models import UserLogin

        db.session.flush()
        done = db.session.execute(
            update(UserLogin).where(UserLogin.user_id == user.id,
                                    or_(UserLogin.totp_step.is_(None), UserLogin.totp_step < step))
            .values(totp_step=step, failed_count=0, failed_streak=0).execution_options(synchronize_session=False)).rowcount
        if done != 1:
            _failed(user)  # the same code was used by another request in between
            return False
        set_committed_value(row, "totp_step", step)
        set_committed_value(row, "failed_count", 0)
        set_committed_value(row, "failed_streak", 0)
    with _lock:
        _tries.pop(user.id, None)
    return True


def alert_wrong_code(user):
    """Slack, once per account and ALERT_GAP: a wrong code after the right password means the password is known
    (T2). Kept in SiteSetting so it holds across server processes; the caller has committed before."""
    from sqlalchemy.exc import IntegrityError

    from app.models import SiteSetting, utcnow

    if not _site_admin(user):
        return None  # T01-N1: the security log has it (auth.sign_in_code)
    key = f"2fa_alert:{user.id}"
    row = db.session.get(SiteSetting, key)
    if row is not None and row.changed_at and row.changed_at > utcnow() - ALERT_GAP:
        return None
    if row is None:
        row = SiteSetting(key=key)
        db.session.add(row)
    row.value, row.changed_at = "wrong code after password", utcnow()
    try:
        db.session.commit()
    except IntegrityError:  # another server process noted it at the same moment and sends the message
        db.session.rollback()
        return None
    return tell("wrong_code", user)


# the messages that name an admin account: for any other account (a voluntary second factor) they stay in the
# security log only, with no name in Slack (Abgleich 2026-10-15, T01-N1)
ADMIN_ONLY = {"wrong_code", "backup_code_used", "admin_password_reset", "factor_off"}


def _site_admin(user):
    return getattr(user, "orcid", None) in current_app.config["ADMIN_ORCIDS"]


def tell(what, user):
    """A message to Slack about a site admin's account (app/notify.py, function `what`); never breaks the page."""
    from app import notify

    if what in ADMIN_ONLY and not _site_admin(user):
        return None
    try:
        return getattr(notify, what)(user)
    except Exception as e:  # noqa: BLE001
        current_app.logger.warning("Second factor not reported in Slack: %s", type(e).__name__)
        return None


def via():
    """How this browser signed in (SIGNED_IN_VIA), or None for a session from before it was kept."""
    return session.get(SIGNED_IN_VIA)


def _passed():
    """Mark this browser as having given a code, for this account and its sign-in version."""
    session["totp_ok"] = {"user": current_user.get_id(), "at": time.time()}


def verified():
    entry = session.get("totp_ok")
    hours = current_app.config.get("ADMIN_TOTP_HOURS", 12)
    return (isinstance(entry, dict) and entry.get("user") == current_user.get_id()
            and time.time() - float(entry.get("at") or 0) <= hours * 3600)


def gate():
    """For pages of the administration: None if the admin may go on; else the way to the setup or to the code,
    back to this page afterwards."""
    if not current_user.is_authenticated or via() == VIA_ORCID:
        return None  # Ralf, 2026-10-13: an ORCID sign-in exempts admins from mandatory app TOTP
    if not active() and not required_at_signin(current_user):
        return None
    back = local_path(request.full_path.rstrip("?")) if request.method == "GET" else None
    if not enabled(current_user):
        flash(de_en("Für die Administration braucht Ihr Konto einen zweiten Faktor (Code aus einer Authenticator-App). "
                    "Bitte jetzt einrichten.",
                    "For the administration your account needs a second factor (a code from an authenticator app). "
                    "Please set it up now."), "warning")
        return redirect(url_for("twofactor.setup", next=back))
    if not verified():
        return redirect(url_for("twofactor.ask", next=back))
    return None


def org_gate():
    """For the pages of an organisation's admins (app/org_admin.py, A20): as for the administration, after a sign-in
    with a password a code from the authenticator app, set up first if need be; an ORCID sign-in is enough."""
    if not current_user.is_authenticated or via() == VIA_ORCID:
        return None
    back = local_path(request.full_path.rstrip("?")) if request.method == "GET" else None
    if not enabled(current_user):
        flash(de_en("Für die Verwaltung einer Organisation braucht Ihr Konto einen zweiten Faktor (Code aus einer "
                    "Authenticator-App). Bitte jetzt einrichten.",
                    "To manage an organisation your account needs a second factor (a code from an authenticator app). "
                    "Please set it up now."), "warning")
        return redirect(url_for("twofactor.setup", next=back))
    if not verified():
        return redirect(url_for("twofactor.ask", next=back))
    return None


def admin_page():
    """For a page or action of site admins outside the administration (complaints, pooled construct data): 403 for
    everyone else, else as gate(): None if the admin may go on, or the way to the code (security audit 2026-10-06,
    H2)."""
    from flask import abort

    if not current_user.is_authenticated or not current_user.is_admin:
        abort(403)
    return gate()


def admin_rights(user):
    """Whether `user` acts with the rights of a site admin on an ordinary page (linked elements, short labels): after
    an ORCID sign-in, else only with a code given in this browser, like the administration (H2 of the audit
    2026-10-06; ADMIN_TOTP plays no part since #984, Abgleich 2026-10-15 T01-N4). Without the code the account acts
    like any other; nothing is asked on the way."""
    if not getattr(user, "is_admin", False):
        return False
    from flask import has_request_context

    own = has_request_context() and current_user.is_authenticated and current_user.id == user.id
    if own and via() == VIA_PASSWORD:
        return False  # a password without a code is never enough for a site admin (T3; auth.load_user ends it too)
    if own and via() == VIA_ORCID:
        return True
    return own and enabled(user) and verified()


# --- pages -----------------------------------------------------------------------------------------------------


def _qr(text):
    import segno

    return Markup(segno.make(text, error="m").svg_inline(scale=5, border=2, dark="#1c1c1a", omitsize=True))


@bp.route("/set-up", methods=["GET", "POST"])
@login_required
def setup():
    """Switch the factor on: a new secret as QR code, confirmed with a first code."""
    _only_when_active()
    nxt = local_path(request.values.get("next", "")) or url_for("accounts.index")
    if enabled(current_user):
        return redirect(nxt)
    if getattr(current_user, "orcid", None) in current_app.config["ADMIN_ORCIDS"] and via() == VIA_PASSWORD:
        # a password alone never sets the factor up: only a session an ORCID sign-in or a one-time link opened (T3)
        flash(de_en("Den zweiten Faktor richten Admins der Plattform nach einer Anmeldung mit ORCID ein. Bitte abmelden "
                    "und mit ORCID anmelden.",
                    "Platform admins set up the second factor after signing in with ORCID. Please sign out and sign "
                    "in with ORCID."), "error")
        return redirect(url_for("accounts.index"))
    from app import secrets_box

    # in the session cookie only encrypted: it is signed, not encrypted (audit 2026-10-09, A6)
    secret = secrets_box.decrypt(session.get("totp_setup"))
    if not secret or request.method == "GET":
        secret = new_secret()  # a fresh one for every visit of the page
        session["totp_setup"] = secrets_box.encrypt(secret)
    if request.method == "POST":
        if check(current_user, request.form.get("code"), secret=secret):
            _row(current_user, create=True).totp_box = secrets_box.encrypt(secret)
            codes = new_backup_codes(current_user)
            db.session.commit()
            _event("second_factor_on")
            session.pop("totp_setup", None)
            if via() != VIA_ORCID:
                from app import auth

                auth.remember(current_user._get_current_object(), fresh=False, via=VIA_CODE)
            _passed()
            flash(de_en("Zweiter Faktor eingerichtet. Künftig fragt die App nach einem Code aus der Authenticator-App.",
                        "Second factor set up. From now on the app asks for a code from the authenticator app."), "success")
            return _show_backup(codes, nxt)
        flash(de_en("Der Code stimmt nicht. Bitte den aktuellen Code aus der App eingeben (er wechselt alle 30 Sekunden).",
                    "The code is not correct. Please enter the current code from the app (it changes every 30 seconds)."), "error")
    response = current_app.make_response(render_template(
        "accounts/twofactor_setup.html", qr=_qr(uri(secret, current_user)), secret=secret, nxt=nxt))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/code", methods=["GET", "POST"])
@login_required
def ask():
    """The code before the administration."""
    _only_when_active()
    nxt = local_path(request.values.get("next", "")) or url_for("admin.surveys_page")
    if not enabled(current_user):
        return redirect(url_for("twofactor.setup", next=nxt))
    if unreadable(current_user):
        flash(de_en("Der gespeicherte zweite Faktor ist nicht mehr lesbar (der Schlüssel des Servers wurde gewechselt). "
                    "Kein Code passt mehr. Die Admins der Plattform müssen ihn zurücksetzen.",
                    "The stored second factor can no longer be read (the server's key was changed). No code fits "
                    "any more. A platform admin has to reset it."), "error")
        return render_template("accounts/twofactor_code.html", nxt=nxt)
    if request.method == "POST":
        backups = backup_left(current_user)
        if check_any(current_user, request.form.get("code")):
            db.session.commit()
            _passed()
            if required_at_signin(current_user) and backup_left(current_user) < backups:
                tell("backup_code_used", current_user)
            return redirect(nxt)
        db.session.commit()
        _event("second_factor_failed", via="administration")
        flash(de_en("Der Code stimmt nicht oder wurde schon verwendet.", "The code is not correct or was already used.")
              + too_many_said(current_user), "error")
    return render_template("accounts/twofactor_code.html", nxt=nxt)


def _event(name, **fields):
    """The security log of the signed-in person (audit 2026-10-13, DP-7/AU-7): never a code or a secret."""
    from app import security_log

    security_log.event(name, account=current_user.id, **fields)


def _show_backup(codes, nxt):
    response = current_app.make_response(render_template("accounts/twofactor_backup.html", codes=codes, nxt=nxt))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/backup-codes", methods=["POST"])
@login_required
def backup_codes():
    """Eight new backup codes, with a current code from the app (the ones before end)."""
    _only_when_active()
    if enabled(current_user) and check(current_user, request.form.get("code")):
        codes = new_backup_codes(current_user)
        db.session.commit()
        _event("backup_codes_new")
        return _show_backup(codes, url_for("accounts.index") + "#zweiter-faktor")
    db.session.commit()
    flash(de_en("Der Code stimmt nicht; die bisherigen Ersatzcodes bleiben gültig.",
                "The code is not correct; the backup codes so far stay valid."), "error")
    return redirect(url_for("accounts.index") + "#zweiter-faktor")


@bp.route("/off", methods=["POST"])
@login_required
def disable():
    """Switch the factor off, with a current code (a site admin then sets it up again before the administration)."""
    _only_when_active()
    if enabled(current_user) and check(current_user, request.form.get("code")):
        row = _row(current_user)
        row.totp_box, row.totp_step, row.totp_backup = None, None, None
        db.session.commit()
        _event("second_factor_off")
        session.pop("totp_ok", None)
        if required_at_signin(current_user):
            tell("factor_off", current_user)
        flash(de_en("Zweiter Faktor ausgeschaltet.", "Second factor switched off."), "success")
    else:
        db.session.commit()
        flash(de_en("Der Code stimmt nicht; der zweite Faktor bleibt eingeschaltet.",
                    "The code is not correct; the second factor stays switched on."), "error")
    return redirect(url_for("accounts.index") + "#zweiter-faktor")
