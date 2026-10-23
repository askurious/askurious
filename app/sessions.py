"""Sign-in sessions kept on the server, so that "Abmelden" ends exactly this one, copies of its cookies included
(security audit 2026-10-04, H6, variant 2).

Every sign-in gets a row (LoginSession) with a random id. The id travels in what Flask-Login stores in the session
cookie and in the "stay signed in" cookie (User.get_id: "<user id>.<session version>.<session id>[.<sign-in method>]"), and
auth.load_user accepts the cookie only while its row is open. Logging out closes the row: the cookie of this
browser is gone anyway, and a copy taken earlier no longer signs anyone in. Other devices stay signed in. The page
"Konto" lists the open sessions with browser and system (from the User-Agent, coarse as for paradata) and ends any
of them.

A row idle for 24 hours (or the shorter cookie lifetime) is over by itself; site-admin sessions last at most
12 hours. Persistent sign-in is opt-in. Other sessions end once older than
SESSION_MAX_AGE however often it is used (audit 2026-10-09, A5: a copied "stay signed in" cookie no longer lasts for
ever); the housekeeping removes them (lifecycle.close_due_surveys -> sweep), as it removes the closed ones after a
day. A new sign-in in a browser ends the session that browser had before.
"""
import secrets
from datetime import timedelta

from flask import current_app, g, has_request_context, request, session

from app.extensions import db

TOUCH = timedelta(minutes=5)  # "zuletzt aktiv" is written at most this often, not on every request
KEEP_CLOSED = timedelta(days=1)


def _label():
    from app import paradata

    ua = request.headers.get("User-Agent", "") if has_request_context() else ""
    browser, system = paradata._browser(ua), paradata._os(ua)
    names = {"other": ""}
    parts = [p for p in (names.get(browser, browser), names.get(system, system)) if p]
    return " · ".join(parts)  # empty when unknown: the page says "Unbekannter Browser" in its language (shown_label)


LEGACY_UNKNOWN = "Unbekannter Browser"  # what sessions stored before the page said it in its language (W116)


def shown_label(label):
    """The name of a session as the page "Konto" shows it: the browser and system, or "Unbekannter Browser" /
    "Unknown browser" in the language of the page (UI/UX audit 2026-10-07, W116)."""
    from app.lang import de_en

    return label if label and label != LEGACY_UNKNOWN else de_en("Unbekannter Browser", "Unknown browser")


def _lifetime():
    return current_app.config.get("REMEMBER_COOKIE_DURATION") or timedelta(days=30)


def _max_age():
    return current_app.config.get("SESSION_MAX_AGE") or timedelta(days=30)


def start(user, since=None):
    """A new session for this browser; its id is used by User.get_id for the cookies Flask-Login writes next.
    `since`: when the sign-in it continues began (the maximum age counts from there)."""
    from app.models import LoginSession, utcnow

    sid = secrets.token_urlsafe(24)
    now = utcnow()
    db.session.add(LoginSession(id=sid, user_id=user.id, created_at=since or now, last_seen_at=now, label=_label()[:60]))
    db.session.commit()
    g.login_sid = sid
    session["sid"] = sid
    return sid


def current_sid():
    if not has_request_context():
        return ""
    return g.get("login_sid") or session.get("sid") or ""


def end_current():
    """End the session this browser had before a new sign-in, whoever it belonged to (audit 2026-10-09, A5: a copy of
    its cookie taken earlier stayed valid next to the new one). Its row, or None."""
    from app.models import LoginSession, utcnow

    sid = current_sid()
    row = db.session.get(LoginSession, sid) if sid else None
    if row is not None and row.ended_at is None:
        row.ended_at = utcnow()
        db.session.commit()
    return row


def open_row(user_id, sid):
    """The open row of this session of this account, or None (closed, unknown, or not seen for too long)."""
    from app.models import LoginSession, utcnow

    if not sid:
        return None
    row = db.session.get(LoginSession, sid)
    if row is None or row.user_id != user_id or row.ended_at is not None:
        return None
    now = utcnow()
    if row.last_seen_at and now - row.last_seen_at > min(_lifetime(), timedelta(hours=24)):
        return None
    from app.models import User

    user = db.session.get(User, user_id)
    maximum = min(_max_age(), timedelta(hours=12)) if user and user.orcid in current_app.config["ADMIN_ORCIDS"] else _max_age()
    if row.created_at and now - row.created_at > maximum:
        return None  # signed in too long ago: sign in again
    if not row.last_seen_at or now - row.last_seen_at > TOUCH:
        row.last_seen_at = now
        db.session.commit()
    return row


def end(user_id, sid):
    from app.models import LoginSession, utcnow

    row = db.session.get(LoginSession, sid) if sid else None
    if row is not None and row.user_id == user_id and row.ended_at is None:
        row.ended_at = utcnow()
        db.session.commit()
        return True
    return False


def end_all(user_id, except_sid=None):
    from app.models import LoginSession, utcnow

    query = db.update(LoginSession).where(LoginSession.user_id == user_id, LoginSession.ended_at.is_(None))
    if except_sid:
        query = query.where(LoginSession.id != except_sid)
    db.session.execute(query.values(ended_at=utcnow()))


def active(user):
    """The open sessions of the account, the most recently used first."""
    from app.models import LoginSession, utcnow

    since = utcnow() - min(_lifetime(), timedelta(hours=24))
    maximum = min(_max_age(), timedelta(hours=12)) if user.orcid in current_app.config["ADMIN_ORCIDS"] else _max_age()
    born = utcnow() - maximum
    return db.session.scalars(db.select(LoginSession).filter(
        LoginSession.user_id == user.id, LoginSession.ended_at.is_(None), LoginSession.last_seen_at >= since,
        LoginSession.created_at >= born)
        .order_by(LoginSession.last_seen_at.desc())).all()


def sweep(now):
    """Remove closed rows after KEEP_CLOSED, rows not seen for the cookie lifetime and rows past the maximum age
    (housekeeping)."""
    from app.models import LoginSession

    gone = db.session.execute(db.delete(LoginSession).where(db.or_(
        LoginSession.ended_at < now - KEEP_CLOSED, LoginSession.last_seen_at < now - min(_lifetime(), timedelta(hours=24)),
        LoginSession.created_at < now - _max_age())).execution_options(
        synchronize_session=False)).rowcount
    if gone:
        db.session.commit()
    return gone
