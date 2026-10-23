"""An address becomes a sign-in name only after mailbox proof in the same account (AU-11)."""
import json
import secrets
from datetime import timedelta

from flask import url_for
from werkzeug.security import generate_password_hash

from app.extensions import db
from app.lang import de_en
from app.models import AuthToken, utcnow

PURPOSE = "email_confirm"


def begin(user, email, password=None):
    from app import brand, limits, mail, mailqueue, passwords, secrets_box

    if not mail.enabled():
        return de_en("Für eine neue Anmeldeadresse muss der Mailversand eingerichtet sein. Wenden Sie sich an einen Plattform-Admin.",
                     "Mail delivery must be configured for a new sign-in address. Contact a platform admin.")
    if not limits._take_shared("email_confirm", str(user.id), 3, 3600):
        return de_en("Bitte warten Sie eine Stunde, bevor Sie einen weiteren Bestätigungslink anfordern.",
                     "Please wait an hour before requesting another confirmation link.")
    row = passwords.login_row(user, create=True)
    db.session.flush()
    db.session.execute(db.delete(AuthToken).where(AuthToken.user_id == user.id, AuthToken.purpose == PURPOSE))
    from app.models import MailOutbox

    db.session.execute(db.delete(MailOutbox).where(MailOutbox.sender_id == user.id, MailOutbox.kind == "emailcheck",
                                                 MailOutbox.sent_at.is_(None)))
    token = secrets.token_urlsafe(32)
    payload = {"hash": generate_password_hash(password) if password is not None else row.password_hash,
               "activate": password is not None, "version": row.session_version or 0, "user": user.id, "email": email}
    db.session.add(AuthToken(user_id=user.id, email=email, purpose=PURPOSE, token_hash=passwords._hash(token),
                             secret_box=secrets_box.encrypt(json.dumps(payload)), expires_at=utcnow() + timedelta(hours=24)))
    link = brand.public_base() + url_for("accounts.confirm_email", token=token)
    subject = de_en("Askurious: Anmeldeadresse bestätigen", "Askurious: confirm your sign-in address")
    body = de_en("Sie haben diese Adresse als Anmeldeadresse angefragt. Melden Sie sich mit dem zugehörigen Konto an "
                 "und bestätigen Sie sie auf dieser Seite (24 Stunden gültig):\n\n{link}\n\n"
                 "Haben Sie nichts angefragt, ignorieren Sie diese E-Mail. Es wird kein Zugang angelegt.",
                 "You requested this address as your sign-in address. Sign in with the associated account "
                 "and confirm it on this page (valid for 24 hours):\n\n{link}\n\n"
                 "If you did not request this, ignore this email. No access will be created.", link=link)
    mailqueue.enqueue(email, subject, body + mail.signature(), kind="emailcheck", sender_id=user.id)
    db.session.commit()
    mailqueue.kick()
    return None


def taken(user, email, password=None):
    """The answer to an address another account already signs in with: the same as begin's, so the page does not
    tell whether the address has an account (audit 2026-10-18, AUTH-5). The owner of the address hears of it by mail,
    without a link; nothing changes. The same limits as begin (mail on, three requests an hour per account)."""
    from app import limits, mail, mailqueue

    if not mail.enabled():
        return de_en("Für eine neue Anmeldeadresse muss der Mailversand eingerichtet sein. Wenden Sie sich an einen Plattform-Admin.",
                     "Mail delivery must be configured for a new sign-in address. Contact a platform admin.")
    if not limits._take_shared("email_confirm", str(user.id), 3, 3600):
        return de_en("Bitte warten Sie eine Stunde, bevor Sie einen weiteren Bestätigungslink anfordern.",
                     "Please wait an hour before requesting another confirmation link.")
    if password is not None:
        generate_password_hash(password)  # as long as begin takes, so the time does not tell either
    subject = de_en("Askurious: Anmeldeadresse schon vergeben", "Askurious: sign-in address already in use")
    body = de_en("Jemand hat diese Adresse als Anmeldeadresse für ein weiteres Konto angefragt. Mit ihr meldet sich "
                 "aber schon ein Konto an, deshalb ändert sich nichts.\n\nWaren Sie das, melden Sie sich mit dem "
                 "vorhandenen Konto an. Sonst ignorieren Sie diese E-Mail.",
                 "Someone requested this address as the sign-in address of another account. An account already signs "
                 "in with it, so nothing changes.\n\nIf that was you, sign in with the existing account. Otherwise "
                 "ignore this email.")
    mailqueue.enqueue(email, subject, body + mail.signature(), kind="emailcheck", sender_id=user.id)
    db.session.commit()
    mailqueue.kick()
    return None


def pending(user, token):
    from app import passwords

    if len(token) > 100:
        return None
    return db.session.scalar(db.select(AuthToken).where(AuthToken.user_id == user.id, AuthToken.purpose == PURPOSE,
        AuthToken.token_hash == passwords._hash(token), AuthToken.used_at.is_(None), AuthToken.expires_at > utcnow()))


def accept(user, token):
    from app import accounts, passwords, secrets_box, security_log
    from app.models import UserLogin

    proof = pending(user, token)
    if proof is None or accounts.is_blocked(user):
        return False
    raw = secrets_box.decrypt(proof.secret_box)
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return False
    if payload.get("user") != user.id or payload.get("email") != proof.email:
        return False
    # Match the lock order of password changes/blocking: account first, then its tokens.
    row = db.session.scalar(db.select(UserLogin).where(UserLogin.user_id == user.id).with_for_update()
                            .execution_options(populate_existing=True))
    if (row is None or row.status == UserLogin.BLOCKED or (row.session_version or 0) != payload.get("version")
            or not accounts.email_free(proof.email, user)):
        db.session.rollback()
        return False
    claimed = db.session.execute(db.update(AuthToken).where(AuthToken.id == proof.id, AuthToken.used_at.is_(None),
        AuthToken.expires_at > utcnow()).values(used_at=utcnow())).rowcount
    if claimed != 1:
        db.session.rollback()
        return False
    row.email, row.email_verified_at = proof.email, utcnow()
    if payload.get("activate"):
        passwords.set_password_hash(user, payload["hash"], first=True)  # revokes nothing (T01-N2)
    else:
        db.session.delete(proof)
    from sqlalchemy.exc import IntegrityError

    try:
        db.session.commit()
    except IntegrityError:  # another confirmation/admin claimed the address; do not replace that account
        db.session.rollback()
        return False
    security_log.event("email_confirmed", account=user.id)
    return True
