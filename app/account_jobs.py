"""Identical reset submission for known/unknown addresses; bounded durable work (AU-10)."""
import json
import threading
import uuid
from datetime import timedelta

from flask import current_app

from app.extensions import db
from app.models import AccountJob, AuthToken, MailOutbox, User, UserLogin, utcnow

TTL = timedelta(hours=1)
_lock = threading.Lock()
_running = set()


def enqueue_reset(email, base):
    from app import secrets_box

    db.session.add(AccountJob(payload_box=secrets_box.encrypt(json.dumps({"email": email, "base": base}))))
    db.session.commit()
    kick()


def run(limit=25):
    from app import accounts, mail, mailqueue, notify, passwords, secrets_box
    from flask import url_for

    holder = uuid.uuid4().hex
    if not mailqueue.hold("account_jobs", holder, 180):
        return
    try:
        db.session.execute(db.delete(AccountJob).where(AccountJob.created_at < utcnow() - TTL))
        db.session.execute(db.delete(AuthToken).where(AuthToken.purpose == "email_confirm", AuthToken.expires_at < utcnow()))
        db.session.execute(db.delete(MailOutbox).where(MailOutbox.sent_at.is_(None), db.or_(
            db.and_(MailOutbox.kind == "reset", MailOutbox.created_at < utcnow() - timedelta(hours=1)),
            db.and_(MailOutbox.kind == "emailcheck", MailOutbox.created_at < utcnow() - timedelta(hours=24)))))
        db.session.commit()
        jobs = list(db.session.scalars(db.select(AccountJob).order_by(AccountJob.id).limit(limit)))
        for job in jobs:
            if not mailqueue.hold("account_jobs", holder, 180):
                return
            job_id = job.id
            try:
                raw = secrets_box.decrypt(job.payload_box)
                payload = json.loads(raw) if raw else None
                if not isinstance(payload, dict) or not isinstance(payload.get("email"), str) \
                        or not isinstance(payload.get("base"), str):
                    raise ValueError("unreadable")
            except Exception:
                # it never becomes readable: logged and dropped, and the requests behind it go on; before, it stopped
                # every pass at itself for up to an hour (code review 2026-10-15, E6)
                db.session.rollback()
                current_app.logger.warning("Unreadable account work %s dropped", job_id)
                db.session.execute(db.delete(AccountJob).where(AccountJob.id == job_id))
                db.session.commit()
                continue
            try:
                if payload:
                    row = db.session.scalar(db.select(UserLogin).filter_by(email=payload["email"]))
                    user = db.session.get(User, row.user_id) if row is not None else None
                    if user is not None and row.password_hash and not accounts.is_blocked(user):
                        if mail.enabled():
                            if not passwords.recently_mailed(user):
                                token = passwords.new_link_token(user, None, hours=passwords.RESET_HOURS)
                                # Queued mail is durable too; no network operation in the reset HTTP handler.
                                with current_app.test_request_context():
                                    passwords.mail_link(user, payload["base"] + url_for("auth.set_password", token=token),
                                                        reset=True, queued=True)
                        elif row.reset_requested_at is None:
                            row.reset_requested_at = utcnow()
                            db.session.commit()
                            with current_app.test_request_context(base_url=payload["base"]):
                                notify.reset_request(user)
                db.session.delete(job)
                db.session.commit()
            except Exception as error:  # no address, token or provider error text in the log
                db.session.rollback()
                current_app.logger.warning("Account work failed: %s", type(error).__name__)
                continue  # retained until the next pass or the one-hour expiry; the others still go (E6)
    finally:
        db.session.rollback()
        mailqueue.release("account_jobs", holder)


def kick():
    app = current_app._get_current_object()
    if app.testing and app.config.get("ACCOUNT_JOBS_SYNC"):
        run()
        return
    if not app.config.get("LIFECYCLE_IN_REQUEST", True):
        return  # Docker's dedicated housekeeping owns the work
    with _lock:
        if id(app) in _running:
            return
        _running.add(id(app))
    def work():
        try:
            with app.app_context():
                run()
        finally:
            with _lock:
                _running.discard(id(app))
    threading.Thread(target=work, daemon=True, name="account-work").start()
