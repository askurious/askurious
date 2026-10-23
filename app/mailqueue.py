"""The queue of mails of a series (mailversand-konzept.md M3): invitations to a survey, their reminders, the links
of "Eigene Links für Personen" by mail. A page puts the mails in the table mail_outbox and returns at once; they go
out at MAIL_PER_MINUTE in a thread of the process that asked (kick), and the housekeeping (lifecycle.close_due_surveys,
about once a minute) starts that again after a restart and puts due reminders in the queue (survey_invites.due).

Each account may send MAIL_PER_ACCOUNT_DAY mails within 24 hours across all its surveys (room_for): series, their
reminders (counted for the account that sent the invitations), invitations to researchers and the notices of
app/invites.py (those go out at once and leave a sent row, record()). A sent row keeps only sender and time for that
day, then the housekeeping removes it. Whoever queues takes lock_account (and the survey's lock) first and counts
afterwards, so two requests at the same moment cannot both use the same room (audit 2026-10-09b, E1).

One process sends at a time (hold "mail_sender", a row of site_setting taken with a conditional UPDATE), and the rate
counts the rows sent within the last minute in the database, so MAIL_PER_MINUTE holds for the whole platform, not per
process (E4). Each row is still claimed by itself (claimed_at, its own time) in an UPDATE only one can win; a row
claimed CLAIM_MINUTES ago and still there was lost with its process and is taken again. A row goes once sent; after
MAX_ATTEMPTS refusals of that mail (its address refused, 5xx) it goes too and the invitation is marked "failed"; between
refusals it waits retry_wait. A mail server that cannot be reached counts for no mail: the pass ends and the rows wait
for the next one (audit 2026-10-10, C1). With MAIL_SYNC (tests) everything is sent within the request.
"""
import threading
import time
import uuid
from datetime import timedelta

from flask import current_app
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import MailOutbox, SiteSetting, Survey, SurveyInvite, utcnow

MAX_ATTEMPTS = 5
POISON_WAIT = timedelta(minutes=2)  # how long the row at which a pass broke off waits (AU1)
CLAIM_MINUTES = 10
SENDER_SECONDS = 180   # how long the sending process holds "mail_sender" without renewing it
RENEW_SECONDS = 30
DUE_SECONDS = 600      # the same for "mail_due" (survey_invites.due)
UNSENT_DAYS = 7        # mail switched off: waiting rows (address, text with a personal link) go after this long
# pg_advisory_xact_lock keys (any numbers, only used here; app/contact.py 4711, extensions 4712, migrations 4713, live/audience 4716)
ACCOUNT_LOCK = 4714
SURVEY_LOCK = 4715
_lock = threading.Lock()
_running = set()  # apps (by id) with a sending thread in this process


# --- locks and claims ----------------------------------------------------------------------------------------------


def _lock_db(key, ident):
    """Until this transaction ends, nobody else takes the same lock. PostgreSQL: an advisory lock on (key, ident), no
    row lock, so participations that lock the survey row (lifecycle.close_if_sample_complete) do not wait for it.
    SQLite: a write that changes nothing takes the database's one write lock now, so whoever comes second waits and
    then reads what the first committed."""
    if db.engine.dialect.name == "postgresql":
        db.session.execute(text("SELECT pg_advisory_xact_lock(:key, :ident)"), {"key": key, "ident": int(ident)})
    else:
        db.session.execute(db.delete(MailOutbox).where(db.false()))


def lock_account(user_id):
    """Before counting room_for and queuing: one request of the account at a time. Take it before lock_survey."""
    if user_id:
        _lock_db(ACCOUNT_LOCK, user_id)


def lock_survey(survey_id):
    _lock_db(SURVEY_LOCK, survey_id)


def _stamp(moment):
    return moment.isoformat(timespec="seconds")


def _row(key):
    if db.session.get(SiteSetting, key) is None:
        try:
            db.session.add(SiteSetting(key=key, value=""))
            db.session.commit()
        except IntegrityError:  # another process made it in the same moment
            db.session.rollback()


def hold(key, holder, seconds):
    """Take or renew the named claim (a row of site_setting: "<until>|<holder>") if it is free, run out or ours; True
    when we hold it now. A conditional UPDATE, so of several processes only one wins."""
    _row(key)
    now = utcnow()
    done = db.session.execute(db.update(SiteSetting).where(
        SiteSetting.key == key, db.or_(SiteSetting.value < _stamp(now), SiteSetting.value.like(f"%|{holder}")))
        .values(value=f"{_stamp(now + timedelta(seconds=seconds))}|{holder}"))
    db.session.commit()
    return done.rowcount == 1


def release(key, holder):
    db.session.execute(db.update(SiteSetting).where(SiteSetting.key == key, SiteSetting.value.like(f"%|{holder}"))
                       .values(value=""))
    db.session.commit()


def once_a_day(key, day):
    """True for the one process that first asks on `day` (a date as text): the daily steps of the housekeeping."""
    _row(key)
    done = db.session.execute(db.update(SiteSetting).where(SiteSetting.key == key, SiteSetting.value != day)
                              .values(value=day))
    db.session.commit()
    return done.rowcount == 1


# --- queuing and counting ------------------------------------------------------------------------------------------


def enqueue(to, subject, body, kind="invite", survey_id=None, invite_id=None, reply_to=None, sender_id=None):
    db.session.add(MailOutbox(to=to, subject=subject[:300], body=body, kind=kind, survey_id=survey_id,
                              invite_id=invite_id, reply_to=reply_to, sender_id=sender_id))


def record(sender_id, kind):
    """A mail that went out at once (an invitation to researchers, a notice; app/invites.py) counts for the account
    like a sent row of a series: a row without address and text, kept for the day (E2). A row of its own, so
    withdrawing or replacing the invitation does not give the room back."""
    if sender_id:
        now = utcnow()
        db.session.add(MailOutbox(to="", subject="", body="", kind=kind, sender_id=sender_id, claimed_at=now,
                                  sent_at=now))


def sent_by(user_id, kind, since):
    """Mails of this kind the account sent since then (app/invites.py: invitations within 24 hours)."""
    return db.session.scalar(db.select(db.func.count(MailOutbox.id)).where(
        MailOutbox.sender_id == user_id, MailOutbox.kind == kind, MailOutbox.sent_at >= since)) or 0


def by_account(user_id):
    """Mails this account queued or sent within 24 hours: waiting and sent."""
    since = utcnow() - timedelta(days=1)
    return db.session.scalar(db.select(db.func.count(MailOutbox.id)).where(
        MailOutbox.sender_id == user_id, db.or_(MailOutbox.sent_at.is_(None), MailOutbox.sent_at >= since))) or 0


def room_for(user_id):
    return max(0, int(current_app.config.get("MAIL_PER_ACCOUNT_DAY") or 2000) - by_account(user_id))


def announce(user, count, survey):
    """Slack hears of the first series an account sends within 24 hours (called before it is queued)."""
    if not count or by_account(user.id):
        return
    from app import notify

    try:
        notify.slack(f":envelope: {notify.person(user)} verschickt {count} Serienmails zur Befragung "
                     f"{notify.survey_ref(survey)}.")
    except Exception as e:  # noqa: BLE001 - the note must not break anything
        current_app.logger.warning("Series not reported in Slack: %s", type(e).__name__)


def sweep(now):
    """Sent rows older than a day go (they were kept for the limit per account)."""
    db.session.execute(db.delete(MailOutbox).where(MailOutbox.sent_at.is_not(None),
                                                   MailOutbox.sent_at < now - timedelta(days=1)))


def drop_unsendable(now):
    """Mail is switched off: waiting rows older than UNSENT_DAYS go, with their address and the text with a personal
    link; their invitations count as failed (audit 2026-10-10, L-11). Returns how many went."""
    old = db.select(MailOutbox.invite_id).where(MailOutbox.sent_at.is_(None),
                                                MailOutbox.created_at < now - timedelta(days=UNSENT_DAYS))
    db.session.execute(db.update(SurveyInvite).where(SurveyInvite.id.in_(old.where(MailOutbox.invite_id.is_not(None))))
                       .values(failed=True).execution_options(synchronize_session=False))
    return db.session.execute(db.delete(MailOutbox).where(
        MailOutbox.sent_at.is_(None), MailOutbox.created_at < now - timedelta(days=UNSENT_DAYS))).rowcount


def waiting(survey_id=None):
    query = db.select(db.func.count(MailOutbox.id)).where(MailOutbox.sent_at.is_(None))
    if survey_id is not None:
        query = query.where(MailOutbox.survey_id == survey_id)
    return db.session.scalar(query) or 0


def _claim(row_id):
    now = utcnow()  # each row its own time: a long pass must not make its last rows look abandoned (E4)
    stale = now - timedelta(minutes=CLAIM_MINUTES)
    done = db.session.execute(db.update(MailOutbox).where(
        MailOutbox.id == row_id, MailOutbox.sent_at.is_(None),
        db.or_(MailOutbox.claimed_at.is_(None), MailOutbox.claimed_at < stale))
        .values(claimed_at=now))
    db.session.commit()
    return done.rowcount == 1


def sent_last_minute():
    return db.session.scalar(db.select(db.func.count(MailOutbox.id)).where(
        MailOutbox.sent_at >= utcnow() - timedelta(minutes=1))) or 0


def send_some(limit):
    """Send up to `limit` waiting mails, fewer as the platform sent some within the last minute, and none while
    another process sends; returns how many went out."""
    from app import mail

    if not mail.enabled():
        return 0
    holder = uuid.uuid4().hex[:16]
    if not hold("mail_sender", holder, SENDER_SECONDS):
        return 0
    try:
        return _send_held(limit, holder)
    finally:
        db.session.rollback()
        release("mail_sender", holder)


def _send_held(limit, holder):
    from app import mail

    limit = max(0, limit - sent_last_minute())
    if not limit:
        return 0
    stale = utcnow() - timedelta(minutes=CLAIM_MINUTES)
    ids = list(db.session.scalars(db.select(MailOutbox.id).where(
        MailOutbox.sent_at.is_(None), db.or_(MailOutbox.claimed_at.is_(None), MailOutbox.claimed_at < stale))
        .order_by(MailOutbox.id).limit(limit)))
    sent = 0
    renewed = time.monotonic()
    with mail.Connection() as connection:  # one SMTP connection for the whole pass (E11)
        for row_id in ids:
            if time.monotonic() - renewed > RENEW_SECONDS:
                if not hold("mail_sender", holder, SENDER_SECONDS):
                    break  # held up so long that another process took over
                renewed = time.monotonic()
            if not _claim(row_id):
                continue
            row = db.session.get(MailOutbox, row_id)
            if row is None:
                continue
            invite = db.session.get(SurveyInvite, row.invite_id) if row.invite_id else None
            if invite is not None and (invite.status == SurveyInvite.OPTOUT or not invite.email):
                db.session.delete(row)  # opted out while waiting
                db.session.commit()
                continue
            try:
                msg = mail.message(row.to, row.subject, row.body, reply_to=row.reply_to, headers=_headers(row, invite))
                connection.send(msg)
            except Exception as e:  # noqa: BLE001 - the reason goes to the log only
                current_app.logger.warning("Mail of a series not sent: %s", type(e).__name__)
                if not _refused(e):
                    # the mail server cannot be reached or refuses everyone: no attempt of this mail; the pass ends
                    # and the rows wait for the next one (audit 2026-10-10, C1). Was the server reached in this pass,
                    # this row waits a little longer (POISON_WAIT), so the next pass starts with the next row: one row
                    # taken for a server failure by mistake can never hold up the whole queue (audit 2026-10-11, AU1)
                    if getattr(connection, "reached", False):
                        row.claimed_at = utcnow() - timedelta(minutes=CLAIM_MINUTES) + POISON_WAIT
                    else:
                        row.claimed_at = None
                    db.session.commit()
                    break
                _failed(row, invite)
                continue
            if invite is not None:
                invite.sends += 1
                invite.last_sent_at = utcnow()
                invite.failed = False
            # kept a day for the limit per account, without address and text
            row.sent_at, row.to, row.subject, row.body, row.reply_to = utcnow(), "", "", "", None
            db.session.commit()
            sent += 1
    return sent


def _refused(error):
    """True when the error concerns this one mail: it counts as an attempt of the mail (retry_wait, MAX_ATTEMPTS).
    Only a failure of the connection or of the server for everyone says nothing about the mail: no connection, a server
    going away, the greeting, the login, the sender address, a temporary 4xx after DATA. Everything else is this mail's:
    the address refused, a 5xx for the mail, a server without SMTPUTF8 for an address that needs it
    (SMTPNotSupportedError, audit 2026-10-11 AU1), a mail that cannot be built (audit 2026-10-10, C1)."""
    import smtplib

    if isinstance(error, (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, smtplib.SMTPAuthenticationError,
                          smtplib.SMTPHeloError, smtplib.SMTPSenderRefused)):
        return False
    if isinstance(error, smtplib.SMTPResponseException) and 400 <= error.smtp_code < 500             and not isinstance(error, smtplib.SMTPRecipientsRefused):
        return False  # temporary for everyone (a full disk, greylisting of the connection)
    if isinstance(error, smtplib.SMTPException):  # SMTPException is an OSError: before the check below
        return True
    if isinstance(error, (OSError, RuntimeError)):  # no connection, a timeout; RuntimeError: mail switched off
        return False
    return True


def retry_wait(attempts):
    """How long a refused mail waits before it is tried again: 2, 4, 8, 16 minutes."""
    return timedelta(minutes=2 ** attempts)


def _failed(row, invite):
    """One more refusal of this mail: tried again after retry_wait (as a claim that runs out then), after MAX_ATTEMPTS
    it goes and its invitation counts as failed."""
    row.attempts += 1
    # a row counts as claimed until CLAIM_MINUTES after claimed_at: set back so the claim runs out after the wait
    row.claimed_at = utcnow() - timedelta(minutes=CLAIM_MINUTES) + retry_wait(row.attempts)
    if row.attempts >= MAX_ATTEMPTS:
        if invite is not None:
            invite.failed = True
        db.session.delete(row)
    db.session.commit()


def _headers(row, invite=None):
    """List-Unsubscribe for invitations: mail programs show their own button for opting out (RFC 2369). Only our own
    link: the last line of the mail, which compose() writes after the researchers' text, and only when it leads to
    this very entry; a link in the researchers' text never gets there (audit 2026-10-09b, E8)."""
    if row.kind not in ("invite", "reminder") or invite is None:
        return None
    from app import survey_invites

    survey = db.session.get(Survey, row.survey_id) if row.survey_id else None
    words = (row.body.rstrip().splitlines() or [""])[-1].split()
    if survey is None or not words:
        return None
    link, tail = words[-1], f"/s/{survey.public_key}/no-mail/"
    if not link.startswith(("https://", "http://")) or tail not in link:
        return None
    found = survey_invites.find(survey, link.rsplit(tail, 1)[1])
    if found is None or found.id != invite.id:
        return None
    return {"List-Unsubscribe": f"<{link}>", "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}


def _loop(app):
    with app.app_context():
        try:
            while True:
                per_minute = max(1, int(app.config.get("MAIL_PER_MINUTE") or 60))
                started = time.monotonic()
                if not send_some(per_minute) and not waiting():
                    break
                left = 60 - (time.monotonic() - started)
                if waiting() and left > 0:
                    time.sleep(left)
                elif not waiting():
                    break
        except Exception as e:  # noqa: BLE001 - the housekeeping starts it again
            app.logger.warning("Mail queue stopped: %s", type(e).__name__)
        finally:
            db.session.remove()
            with _lock:
                _running.discard(id(app))


def kick():
    """Send what waits: in the request with MAIL_SYNC (tests), else in a thread of this process unless one runs."""
    from app import mail

    if not mail.enabled() or mail.queued_here():
        return  # the participants' side leaves sending to the housekeeping (mail.queued_here, CFG-04)
    app = current_app._get_current_object()
    if app.config.get("MAIL_SYNC"):
        send_some(10_000)
        return
    with _lock:
        if id(app) in _running:
            return
        _running.add(id(app))
    threading.Thread(target=_loop, args=(app,), daemon=True).start()


def run(now):
    """From the housekeeping: once a day the states of the invitation lists, then due reminders into the queue (one
    process at a time), then sending (again) if anything waits."""
    from app import mail, survey_invites
    from app.timeutil import to_local

    sweep(now)
    db.session.commit()
    if once_a_day("invite_states_day", to_local(now).date().isoformat()):
        survey_invites.apply_states()  # "begonnen"/"teilgenommen" only once a day (Ralf, 2026-10-09; E7)
    if not mail.enabled():
        if drop_unsendable(now):
            db.session.commit()
        return
    holder = uuid.uuid4().hex[:16]
    if hold("mail_due", holder, DUE_SECONDS):
        try:
            survey_invites.due(now)
        finally:
            db.session.rollback()
            release("mail_due", holder)
    if waiting():
        kick()
