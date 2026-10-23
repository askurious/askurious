"""Revocable diary WebView grants, separate from questionnaire data and compatible with existing link payloads."""
import hashlib
import secrets
from datetime import datetime, timedelta

from flask import session

from app.extensions import db
from app.models import BrowserReceipt, Enrolment, Response, Survey, utcnow

LINK_HOURS = 12
MAX_LINKS = 100
# Once opened, a link belongs to that browser; another one may still open it this long after the first, for a first
# reply lost on the way (the app then loads the address again). Abgleich 2026-10-15, KR-12.
REOPEN_SECONDS = 120


def scope(person):
    return f"diary-web:{person.id}"


def session_key(survey):
    return f"da{survey.id}"


def revision(person):
    value = f"{person.secret_hash}:{(person.settings or {}).get('web_revision', '')}"
    return hashlib.sha256(value.encode()).hexdigest()


def lock_person(person):
    """Read current rights while holding the same row lock as admission, until commit/rollback."""
    with db.session.no_autoflush:
        if db.engine.dialect.name == "sqlite":
            db.session.execute(db.delete(Enrolment).where(db.false()))
        return db.session.scalar(db.select(Enrolment).where(Enrolment.id == person.id)
                                 .with_for_update().execution_options(populate_existing=True))


def revoke(person):
    """Called under the person's lock before a state change commits; retained answers never change."""
    person.settings = {**(person.settings or {}), "web_revision": secrets.token_urlsafe(24)}
    db.session.execute(db.delete(BrowserReceipt).where(BrowserReceipt.survey_id == person.survey_id,
                                                       BrowserReceipt.scope == scope(person)))


def allowed(survey, person, response):
    from app import diary

    if person is None or survey.status == Survey.CLOSED or (
            survey.status == Survey.DRAFT and person.source != "test"):
        return False
    status = diary.refresh(survey, person)
    part = (response.occasion or {}).get("part", diary.ENTRY)
    if part == diary.EXIT:
        info = diary.exit_info(survey, person, status, person.language)
        return bool(info and (info["open"] or (response.status == Response.COMPLETED
                                               and status in ("finished", Enrolment.ENDED))))
    if part == diary.ENTRY:
        return status == Enrolment.PENDING or (status == Enrolment.ACTIVE and response.status == Response.COMPLETED)
    return status == Enrolment.ACTIVE or (status == "finished" and _last_prompt(person, response))


def _last_prompt(person, response):
    """A prompt of the person's last day answered after midnight (its expiry reaches into the next day) may still be
    answered on that next day (review 2026-10-15, G5)."""
    from app import diary

    try:
        due = datetime.fromisoformat((response.occasion or {}).get("due") or "")
    except (TypeError, ValueError):
        return False
    return bool(person.last_day) and diary.prompt_day(person, due) <= person.last_day         and diary.local_today(person) <= person.last_day + timedelta(days=1)


def issue(survey, response, token):
    """Issue twelve-hour grants, bounded per person; caller commits with its entry, housekeeping sweeps expiry."""
    from app.browser_receipts import digest

    person = db.session.get(Enrolment, response.enrolment_id)
    now = utcnow()
    old = db.select(BrowserReceipt.key).where(BrowserReceipt.survey_id == survey.id,
                                              BrowserReceipt.scope == scope(person)) \
        .order_by(BrowserReceipt.expires_at.desc(), BrowserReceipt.key).offset(MAX_LINKS - 1)
    db.session.execute(db.delete(BrowserReceipt).where(BrowserReceipt.survey_id == survey.id,
                                                       BrowserReceipt.scope == scope(person),
                                                       db.or_(BrowserReceipt.expires_at <= now, BrowserReceipt.key.in_(old))))
    db.session.add(BrowserReceipt(survey_id=survey.id, scope=scope(person), key=digest(token),
                                 expires_at=now + timedelta(hours=LINK_HOURS),
                                 data={"response": response.id, "revision": revision(person)}))


def open_link(survey, response, token):
    from app.browser_receipts import digest

    person = db.session.get(Enrolment, response.enrolment_id)
    person = lock_person(person) if person else None
    if not allowed(survey, person, response):
        return False
    grant = db.session.get(BrowserReceipt, (survey.id, scope(person), digest(token)))
    now = utcnow()
    data = grant.data if grant is not None and isinstance(grant.data, dict) else {}
    if grant is None or grant.expires_at <= now or data.get("response") != response.id             or data.get("revision") != revision(person):
        return False
    if not _may_open(survey, grant, data, now):
        return False
    if not data.get("opened"):
        grant.data = {**data, "opened": now.replace(microsecond=0).isoformat()}
    session[session_key(survey)] = {"person": person.id, "response": response.id, "grant": grant.key,
                                  "revision": revision(person), "until": grant.expires_at.isoformat()}
    return True


def _may_open(survey, grant, data, now):
    """A link not opened yet, or opened before in this browser, or by another one less than REOPEN_SECONDS ago: the
    address of an entry no longer works in any number of browsers for twelve hours (Abgleich 2026-10-15, KR-12). The
    app reloads it in the same WebView (same session cookie) after the system ended it, which still works."""
    opened = data.get("opened")
    if not opened:
        return True
    known = session.get(session_key(survey))
    if isinstance(known, dict) and known.get("grant") == grant.key:
        return True
    try:
        return now - datetime.fromisoformat(opened) <= timedelta(seconds=REOPEN_SECONDS)
    except (TypeError, ValueError):
        return False


def current(survey, response):
    person = db.session.get(Enrolment, response.enrolment_id)
    person = lock_person(person) if person else None
    known = session.get(session_key(survey))
    if person is None or not isinstance(known, dict) or known.get("person") != person.id \
            or known.get("response") != response.id or known.get("revision") != revision(person):
        return False
    try:
        if datetime.fromisoformat(known.get("until", "")) <= utcnow():
            return False
    except (TypeError, ValueError):
        return False
    db.session.refresh(response)
    return allowed(survey, person, response)
