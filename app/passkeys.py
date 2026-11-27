"""Signing in with a passkey (WebAuthn): a third way in besides ORCID and e-mail with password (Ralf 2026-11-27).

A passkey is a key pair made on the person's device or in its key store: the private key stays there and is
released by fingerprint, face recognition, device PIN or a security key; the server keeps the public key and checks
a signature over a one-time value (models.Passkey). The library fido2 reads and checks the browser's answers; the
page talks to the browser with app/static/passkey.js (navigator.credentials), no further script.

What holds (decisions E1 to E11):
- Every account may add passkeys on "Konto > Profil und Anmeldung", at most MAX_PER_ACCOUNT, always in addition to
  ORCID or a password: a passkey never counts as the other way in when one of those is removed.
- Adding one asks for the proof connecting ORCID asks for (auth._link_refused): the current password, and a code
  where a second factor is set up; an account without a password needs a fresh sign-in.
- User verification is required at both steps, so a passkey sign-in stands for two factors: it counts like
  "password+code" (twofactor.VIA_PASSKEY), also for site admins, and as a fresh sign-in.
- The sign-in starts only with the button of the sign-in page: no suggestion in the address field, nothing by
  itself. The one-time value lies in the session (no further cookie), holds CHALLENGE_SECONDS and counts once.
- The passkey belongs to the main address alone (RP ID = its host, the origin must be exactly it): a page of the
  participants' address cannot use it, and Permissions-Policy allows the browser's dialog on researchers' pages only.
- No attestation: neither make nor model of the device is asked for or kept.
- A blocked account does not get in; a new password, a one-time link and "Überall abmelden" leave passkeys as they
  are; site admins can remove all passkeys of an account on its page (admin.account, remove_all).
- The counter of signatures: synchronised passkeys report 0; one that goes back is noted in the security log.

A passkey as proof (stage 2, Ralf 2026-11-27): where a page asks a signed-in account to prove itself (the current
password, a code from the authenticator app, a fresh sign-in), an account with a passkey may confirm with it instead
("Mit Passkey bestätigen", confirm_options and confirm). The answer must belong to a passkey of exactly the signed-in
account, with user verification and the origin as at the sign-in. The one-time value is bound to its purpose
(PURPOSES): a proof for one place counts at no other. "fresh" marks the session as freshly signed in (in place of
signing in again, accounts.need_fresh); "admin" counts as the code before the administration (twofactor.ask); the
others leave a proof in the session that the form sent right after takes once (proved). Never for setting or
changing a password: a passkey must not make a password (accounts.fresh_for_password).
"""
import secrets
import time
from urllib.parse import urlsplit

from flask import Blueprint, current_app, flash, jsonify, redirect, request, session, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.lang import de_en
from app.links import local_path

bp = Blueprint("passkeys", __name__, url_prefix="/account/passkeys")

MAX_PER_ACCOUNT = 10
NAME_LENGTH = 60
CHALLENGE_SECONDS = 300
SIGN_IN_KEY, ADD_KEY = "passkey_get", "passkey_add"  # the one-time value of a sign-in / of adding, in the session
CONFIRM_KEY, PROOF_KEY = "passkey_confirm", "passkey_proof"  # the one-time value of a proof / the proof given
PROOF_SECONDS = 120  # the form that asked for the proof is sent right after it; a proof left lying counts no more
FRESH, ADMIN = "fresh", "admin"
# what a passkey may confirm: a fresh sign-in, the code before the administration, and the forms that ask for the
# current password (deleting the account, connecting and disconnecting ORCID, removing the password, a further passkey)
PURPOSES = (FRESH, ADMIN, "delete", "orcid_link", "orcid_remove", "password_remove", "add")
FRESH_BY = "fresh_by"  # in the session: "passkey" when a passkey proof made the sign-in fresh (accounts.fresh_for_password)


# --- the relying party ---------------------------------------------------------------------------------------------


def origin():
    """The origin a passkey answer must name: the main address of this request's brand, exactly (scheme, host and a
    port other than the scheme's own). The participants' address is another origin and is refused."""
    from app import brand

    parts = urlsplit(brand.public_base())
    host = (parts.hostname or "").lower()
    if parts.port and (parts.scheme, parts.port) not in (("https", 443), ("http", 80)):
        host = f"{host}:{parts.port}"
    return f"{parts.scheme}://{host}"


def rp_id():
    return urlsplit(origin()).hostname or ""


def _server():
    from fido2.server import Fido2Server
    from fido2.webauthn import PublicKeyCredentialRpEntity

    from app import brand

    expected = origin()
    server = Fido2Server(PublicKeyCredentialRpEntity(id=rp_id(), name=brand.name() or "Askurious"), attestation="none",
                         verify_origin=lambda given: given == expected)
    server.timeout = CHALLENGE_SECONDS * 1000
    return server


def _state(kept):
    """The library's state for the one-time value kept in the session, or None once it is too old or missing."""
    from fido2.webauthn import UserVerificationRequirement

    if not isinstance(kept, dict) or not kept.get("c") or time.time() - float(kept.get("at") or 0) > CHALLENGE_SECONDS:
        return None
    return {"challenge": kept["c"], "user_verification": UserVerificationRequirement.REQUIRED}


def _encode(data):
    from fido2.utils import websafe_encode

    return websafe_encode(bytes(data))


def _decode(text):
    from fido2.utils import websafe_decode

    return websafe_decode(text)


def _credential(row):
    """The stored passkey as the library checks a signature against it (no make or model: the all-zero AAGUID)."""
    from fido2 import cbor
    from fido2.cose import CoseKey
    from fido2.webauthn import Aaguid, AttestedCredentialData

    return AttestedCredentialData.create(Aaguid.NONE, _decode(row.credential_id),
                                         CoseKey.parse(cbor.decode(_decode(row.public_key))))


def _descriptor(row):
    from fido2.webauthn import PublicKeyCredentialDescriptor, PublicKeyCredentialType

    return PublicKeyCredentialDescriptor(type=PublicKeyCredentialType.PUBLIC_KEY, id=_decode(row.credential_id))


# --- the account's passkeys ----------------------------------------------------------------------------------------


def of(user):
    """The passkeys of the account, the oldest first."""
    from app.models import Passkey

    return db.session.scalars(db.select(Passkey).filter_by(user_id=user.id).order_by(Passkey.created_at, Passkey.id)).all()


def count(user):
    from app.models import Passkey

    return db.session.scalar(db.select(db.func.count(Passkey.id)).filter_by(user_id=user.id)) or 0


def clean_name(text):
    return " ".join(str(text or "").split())[:NAME_LENGTH]


def _handle(user):
    """The account's random id for the device: the one its passkeys carry, or a new one (32 bytes)."""
    rows = of(user)
    return rows[0].user_handle if rows else _encode(secrets.token_bytes(32))


def remove_all(user, by=None):
    """Remove every passkey of the account (a site admin on the account's page; accounts.delete). Returns how many
    went; the caller commits."""
    from app.models import Passkey

    gone = db.session.execute(db.delete(Passkey).where(Passkey.user_id == user.id)
                              .execution_options(synchronize_session=False)).rowcount or 0
    if gone:
        _event("passkey_removed", account=user.id, by=getattr(by, "id", None), kind="all")
    return gone


def _event(name, **fields):
    """The security log (app/security_log.py): numbers and fixed words only, never a key or a name."""
    from app import security_log

    security_log.event(name, **fields)


def mail_changed(user, added):
    """With mail on, a mail to the account's sign-in address that a passkey was added or removed: whoever did not do
    it learns at once (as twofactor._mail_factor_on)."""
    from app import brand, lang, mail, passwords

    row = passwords.login_row(user)
    if not mail.enabled() or row is None or not row.email:
        return
    with mail.in_language(lang.of(user)):
        if added:
            subject = de_en("Askurious: Passkey hinzugefügt", "Askurious: passkey added")
            text = de_en("Guten Tag {name},\n\nfür Ihr Konto in Askurious wurde soeben ein Passkey hinzugefügt: Mit ihm "
                         "kann man sich bei Ihrem Konto anmelden. Waren Sie das nicht, entfernen Sie ihn unter „Konto › "
                         "Profil und Anmeldung“ und melden Sie sich bitte sofort bei den Admins der Plattform.\n\n{link}",
                         "Hello {name},\n\na passkey has just been added to your account in Askurious: it can be used "
                         "to sign in to your account. If this was not you, remove it under “Account › Profile and "
                         "sign-in” and please tell the platform admins at once.\n\n{link}",
                         name=user.name, link=brand.public_base() + "/account/")
        else:
            subject = de_en("Askurious: Passkey entfernt", "Askurious: passkey removed")
            text = de_en("Guten Tag {name},\n\nvon Ihrem Konto in Askurious wurde soeben ein Passkey entfernt: Mit ihm kann "
                         "man sich nicht mehr anmelden. Waren Sie das nicht, melden Sie sich bitte sofort bei den Admins "
                         "der Plattform.\n\n{link}",
                         "Hello {name},\n\na passkey has just been removed from your account in Askurious: it can no "
                         "longer be used to sign in. If this was not you, please tell the platform admins at once."
                         "\n\n{link}", name=user.name, link=brand.public_base() + "/account/")
        mail.send(row.email, subject, text + mail.signature())


# --- answers to the script -----------------------------------------------------------------------------------------


def _answer(payload, status=200):
    response = jsonify(payload)
    response.status_code = status
    response.headers["Cache-Control"] = "no-store"
    return response


def _refused(text, status=400):
    return _answer({"ok": False, "error": text}, status)


def _options(options):
    """The options for navigator.credentials as JSON (bytes as base64url, as the library writes them)."""
    return _answer({"ok": True, "publicKey": dict(options)["publicKey"]})


NOT_POSSIBLE = ("Anmeldung mit Passkey nicht möglich. Bitte erneut versuchen oder einen anderen Weg nehmen.",
                "Sign-in with a passkey is not possible. Please try again or take another way.")


# --- signing in ----------------------------------------------------------------------------------------------------


@bp.route("/sign-in/options", methods=["POST"])
def sign_in_options():
    """The one-time value for a sign-in. No account is named: the device offers the passkeys it holds for this
    address (discoverable credentials), so the page learns nothing about which accounts exist."""
    from fido2.webauthn import UserVerificationRequirement

    options, state = _server().authenticate_begin(user_verification=UserVerificationRequirement.REQUIRED)
    session[SIGN_IN_KEY] = {"c": state["challenge"], "at": time.time()}
    return _options(options)


def _assertion(kept, credential, owner=None):
    """(row, authenticator data) of a valid answer to the one-time value `kept`; else (None, why) with a fixed word
    for the security log. owner: the account the passkey must belong to (a proof of the signed-in account)."""
    from fido2.webauthn import AuthenticationResponse

    from app.models import Passkey

    state = _state(kept)
    if state is None:
        return None, "challenge"
    try:
        answer = AuthenticationResponse.from_dict(credential)
        row = db.session.scalar(db.select(Passkey).filter_by(credential_id=_encode(answer.raw_id)))
        if row is None:
            return None, "unknown"
        if owner is not None and row.user_id != owner.id:
            return None, "other"
        handle = answer.response.user_handle
        if handle and _encode(handle) != row.user_handle:
            return None, "handle"
        _server().authenticate_complete(state, [_credential(row)], answer)
    except Exception:  # noqa: BLE001 - whatever the browser sent that does not read or check: one answer for all
        return None, "invalid"
    return row, answer.response.authenticator_data


@bp.route("/sign-in", methods=["POST"])
def sign_in():
    """The device's answer: signed in when the signature fits a stored passkey, with the checks of the sign-in with
    a password (auth.sign_in): the limit per network, a blocked account, an account not allowed to sign in. An
    unknown passkey, a wrong origin, a used or expired one-time value and a missing user verification all get the
    same answer."""
    from app import accounts, auth, invites, joinlinks, limits, twofactor
    from app.models import User, utcnow

    data = request.get_json(silent=True)
    data = data if isinstance(data, dict) else {}
    kept = session.pop(SIGN_IN_KEY, None)  # counts once, whatever follows
    nxt = local_path(str(data.get("next") or "")) or ""
    session["remember_signin"] = data.get("remember") is True
    if not limits.allow_login():
        return _refused(de_en("Von diesem Netzwerk gab es gerade zu viele Anmeldeversuche. Bitte versuchen Sie es in "
                              "einigen Minuten erneut.",
                              "There have just been too many sign-in attempts from this network. Please try again in "
                              "a few minutes."), 429)
    row, found = _assertion(kept, data.get("credential"))
    if row is None:
        _event("passkey_failed", reason=found)
        return _refused(de_en(*NOT_POSSIBLE), 401)
    limits.login_succeeded_here()  # only failed attempts count toward the limit per sender
    user = db.session.get(User, row.user_id)
    if user is None:
        return _refused(de_en(*NOT_POSSIBLE), 401)
    if accounts.is_blocked(user):
        return _refused(accounts.blocked_said(user), 403)
    if not auth._may_sign_in(user.orcid):
        return _refused(de_en("Ihr Konto ist (noch) nicht freigeschaltet. Die Admins der Plattform schalten es frei und "
                              "helfen bei Fragen (siehe Impressum).",
                              "Your account is not (yet) allowed to sign in. A platform admin allows it and helps with "
                              "questions (see the legal notice)."), 403)
    _used(row, found)
    session.pop(invites.SESSION_KEY, None)  # an invitation is taken only by the ORCID way it was opened for
    session.pop(joinlinks.SESSION_KEY, None)
    user.is_admin = user.orcid in current_app.config["ADMIN_ORCIDS"]
    user.last_login_at = utcnow()
    db.session.commit()
    auth.remember(user, via=twofactor.VIA_PASSKEY)  # fresh: the person has just proved who they are
    return _answer({"ok": True, "next": nxt or url_for("dashboard.index")})


def _used(row, found):
    """Note a valid use of the passkey: counter, backup state, time. The caller commits."""
    from app.models import utcnow

    if (found.counter or row.sign_count) and found.counter <= row.sign_count:
        # the counter went back or stood still: a copy of the key may be in use. Noted, the higher value stays
        _event("passkey_counter", account=row.user_id)
    else:
        row.sign_count = found.counter
    row.backed_up, row.last_used_at = bool(found.is_backed_up()), utcnow()


# --- a passkey as proof of the signed-in account ---------------------------------------------------------------------


def offered(user=None):
    """Whether pages offer "Mit Passkey bestätigen" to this account (the signed-in one): it has a passkey."""
    user = current_user if user is None else user
    return bool(getattr(user, "is_authenticated", False) and count(user))


@bp.route("/confirm/options", methods=["POST"])
@login_required
def confirm_options():
    """The one-time value of a proof, bound to its purpose and to the signed-in account; the device is told which
    passkeys count (those of the account)."""
    from fido2.webauthn import UserVerificationRequirement

    user = current_user._get_current_object()
    data = request.get_json(silent=True)
    purpose = str((data if isinstance(data, dict) else {}).get("purpose") or "")
    session.pop(CONFIRM_KEY, None)
    rows = of(user)
    if purpose not in PURPOSES or not rows:
        return _refused(de_en("Für dieses Konto ist kein Passkey hinterlegt.", "This account has no passkey."))
    options, state = _server().authenticate_begin(credentials=[_descriptor(row) for row in rows],
                                                  user_verification=UserVerificationRequirement.REQUIRED)
    session[CONFIRM_KEY] = {"c": state["challenge"], "at": time.time(), "user": user.id, "purpose": purpose}
    return _options(options)


@bp.route("/confirm", methods=["POST"])
@login_required
def confirm():
    """The device's answer to a proof. Wrong tries count toward the limit per network as at the sign-in and stand
    in the security log with their purpose."""
    from app import limits, twofactor

    user = current_user._get_current_object()
    data = request.get_json(silent=True)
    data = data if isinstance(data, dict) else {}
    purpose = str(data.get("purpose") or "")
    kept = session.pop(CONFIRM_KEY, None)  # counts once, whatever follows
    session.pop(PROOF_KEY, None)
    failed = _refused(de_en("Die Bestätigung mit Passkey hat nicht geklappt. Bitte erneut versuchen.",
                            "Confirming with the passkey did not work. Please try again."), 401)
    if not limits.allow_login():
        return _refused(de_en("Von diesem Netzwerk gab es gerade zu viele Versuche. Bitte versuchen Sie es in einigen "
                              "Minuten erneut.",
                              "There have just been too many attempts from this network. Please try again in a few "
                              "minutes."), 429)
    if (purpose not in PURPOSES or not isinstance(kept, dict) or kept.get("user") != user.id
            or kept.get("purpose") != purpose):
        _event("passkey_failed", account=user.id, reason="purpose", kind=purpose[:20])
        return failed
    row, found = _assertion(kept, data.get("credential"), owner=user)
    if row is None:
        _event("passkey_failed", account=user.id, reason=found, kind=purpose)
        return failed
    limits.login_succeeded_here()
    _used(row, found)
    db.session.commit()
    _event("passkey_confirmed", account=user.id, kind=purpose)
    if purpose == FRESH:
        session["signed_in_at"], session[FRESH_BY] = time.time(), "passkey"
    elif purpose == ADMIN:
        twofactor._passed(by=twofactor.VIA_PASSKEY)  # counts as the code before the administration
    else:
        session[PROOF_KEY] = {"purpose": purpose, "user": user.id, "at": time.time()}
    nxt = local_path(str(data.get("next") or "")) or ""
    return _answer({"ok": True, "next": nxt} if nxt else {"ok": True})


def proved(purpose, user=None):
    """True once: the signed-in account has just confirmed this purpose with a passkey (confirm). The proof is
    taken with the look, whatever it says: for another purpose, another account or too old it counts nowhere."""
    user = current_user if user is None else user
    kept = session.pop(PROOF_KEY, None)
    return bool(isinstance(kept, dict) and kept.get("purpose") == purpose and kept.get("user") == user.id
                and time.time() - float(kept.get("at") or 0) <= PROOF_SECONDS)


@bp.route("/confirm")
@login_required
def confirm_page():
    """Where accounts.need_fresh sends an account with a passkey: confirm with it and go on, or sign in again."""
    from flask import render_template

    from app import accounts

    nxt = local_path(request.args.get("next", "")) or url_for("accounts.index")
    if accounts._fresh() or not offered():
        return redirect(nxt)
    again = (url_for("auth.login", fresh=1, next=nxt) if current_user.has_orcid else url_for("auth.sign_in", next=nxt))
    response = current_app.make_response(render_template("accounts/passkey_confirm.html", nxt=nxt, again=again,
                                                         minutes=accounts.FRESH_SECONDS // 60))
    response.headers["Cache-Control"] = "no-store"
    return response


# --- adding, renaming, removing on the page "Konto" -----------------------------------------------------------------


def _back():
    return url_for("accounts.index") + "#passkeys"


def _proof_refused(user, data):
    """None if the account proved itself for a further way in, as for connecting ORCID (auth._link_refused): the
    current password where it has one, else a fresh sign-in, and a code of the second factor where it is set up.
    Else the answer to the script."""
    from app import accounts, passwords, twofactor

    if data.get("proof") is True:
        if proved("add", user):
            return None  # confirmed with a passkey the account has already: it stands for the password and the code
        return _refused(de_en(*accounts.PROOF_GONE))
    if passwords.has_password(user):
        if not passwords.confirm(user, str(data.get("current") or "")):
            ended = accounts._too_many_wrong(user)  # says so itself on the page that follows
            if ended is None:
                return _refused(de_en("Das aktuelle Passwort stimmt nicht.", "The current password is not correct."))
            return _answer({"ok": False, "redirect": url_for("auth.sign_in") if ended else _back()}, 400)
    elif not accounts._fresh():
        return _refused(de_en("Die Anmeldung liegt zu lange zurück. Bitte melden Sie sich neu an und versuchen Sie es "
                              "dann erneut.",
                              "The sign-in was too long ago. Please sign in again and then try once more."))
    if twofactor.enabled(user) and not twofactor.check_any(user, str(data.get("code") or "")):
        db.session.commit()  # the wrong try counts
        return _refused(de_en("Der Code stimmt nicht.", "The code is not correct."))
    db.session.commit()
    return None


def _full():
    return _refused(de_en("Ein Konto kann höchstens {n} Passkeys haben. Bitte entfernen Sie zuerst einen.",
                          "An account can have at most {n} passkeys. Please remove one first.", n=MAX_PER_ACCOUNT))


@bp.route("/add/options", methods=["POST"])
@login_required
def add_options():
    """The first step of "Passkey hinzufügen": the name and the proof are checked, then the device gets the one-time
    value and the passkeys the account has already (so the same device is not added twice)."""
    from fido2.webauthn import PublicKeyCredentialUserEntity, ResidentKeyRequirement, UserVerificationRequirement

    from app import passwords

    user = current_user._get_current_object()
    data = request.get_json(silent=True)
    data = data if isinstance(data, dict) else {}
    session.pop(ADD_KEY, None)
    name = clean_name(data.get("name"))
    if not name:
        return _refused(de_en("Bitte einen Namen für den Passkey angeben.", "Please enter a name for the passkey."))
    rows = of(user)
    if len(rows) >= MAX_PER_ACCOUNT:
        return _full()
    refused = _proof_refused(user, data)
    if refused is not None:
        return refused
    login = passwords.login_row(user)
    handle = _handle(user)
    # what the device shows when it offers the passkey: the sign-in address where there is one, else the name
    shown = (login.email if login is not None and login.email else None) or user.name or "Askurious"
    options, state = _server().register_begin(
        PublicKeyCredentialUserEntity(id=_decode(handle), name=shown[:64], display_name=(user.name or shown)[:64]),
        credentials=[_descriptor(row) for row in rows],
        resident_key_requirement=ResidentKeyRequirement.REQUIRED,
        user_verification=UserVerificationRequirement.REQUIRED)
    session[ADD_KEY] = {"c": state["challenge"], "at": time.time(), "user": user.id, "name": name, "handle": handle}
    return _options(options)


@bp.route("/add", methods=["POST"])
@login_required
def add():
    """The second step: the device's answer to the one-time value becomes a passkey of the account."""
    from sqlalchemy.exc import IntegrityError

    from fido2 import cbor

    from app import twofactor
    from app.models import Passkey

    user = current_user._get_current_object()
    data = request.get_json(silent=True)
    data = data if isinstance(data, dict) else {}
    kept = session.pop(ADD_KEY, None)  # counts once
    state = _state(kept)
    failed = _refused(de_en("Der Passkey konnte nicht hinzugefügt werden. Bitte erneut versuchen.",
                            "The passkey could not be added. Please try again."))
    if state is None or kept.get("user") != user.id:
        return failed
    try:
        found = _server().register_complete(state, data.get("credential"))
        credential = found.credential_data
        key = _encode(cbor.encode(credential.public_key))
        credential_id = _encode(credential.credential_id)
    except Exception:  # noqa: BLE001 - whatever the browser sent that does not read or check
        _event("passkey_failed", account=user.id, reason="add")
        return failed
    if count(user) >= MAX_PER_ACCOUNT:
        return _full()
    known = _refused(de_en("Dieser Passkey ist schon hinterlegt.", "This passkey has already been added."))
    if db.session.scalar(db.select(Passkey.id).filter_by(credential_id=credential_id)) is not None:
        return known
    db.session.add(Passkey(user_id=user.id, credential_id=credential_id, public_key=key, user_handle=kept["handle"],
                           sign_count=found.counter, name=clean_name(kept.get("name")),
                           backed_up=bool(found.is_backed_up())))
    try:
        db.session.commit()
    except IntegrityError:  # the same passkey in two tabs at the same moment
        db.session.rollback()
        return known
    _event("passkey_added", account=user.id)
    mail_changed(user, added=True)
    twofactor.tell("passkey_added", user)  # Slack hears of a new way into a site admin's account
    flash(de_en("Passkey hinzugefügt. Sie können sich ab jetzt auch mit ihm anmelden.",
                "Passkey added. From now on you can also sign in with it."), "success")
    return _answer({"ok": True, "redirect": _back()})


def _own(passkey_id):
    from flask import abort

    from app.models import Passkey

    row = db.session.get(Passkey, passkey_id)
    if row is None or row.user_id != current_user.id:
        abort(404)
    return row


@bp.route("/<int:passkey_id>/rename", methods=["POST"])
@login_required
def rename(passkey_id):
    row = _own(passkey_id)
    name = clean_name(request.form.get("name"))
    if not name:
        flash(de_en("Bitte einen Namen für den Passkey angeben.", "Please enter a name for the passkey."), "error")
    else:
        row.name = name
        db.session.commit()
        flash(de_en("Name gespeichert.", "Name saved."), "success")
    return redirect(_back())


@bp.route("/<int:passkey_id>/remove", methods=["POST"])
@login_required
def remove(passkey_id):
    """Remove one passkey. ORCID or the password stay as the way in, so nobody locks themself out with it."""
    row = _own(passkey_id)
    user = current_user._get_current_object()
    db.session.delete(row)
    db.session.commit()
    _event("passkey_removed", account=user.id)
    mail_changed(user, added=False)
    flash(de_en("Passkey entfernt. Mit ihm kann man sich nicht mehr anmelden.",
                "Passkey removed. It can no longer be used to sign in."), "success")
    return redirect(_back())
