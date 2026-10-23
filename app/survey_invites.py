"""Invitation mails to participants (mailversand-konzept.md M6 to M8, all as recommended; only with mail on, app/mail.py).

Researchers with the role "Admin" and the right to create surveys put addresses on a list (pasted or as CSV: e-mail,
optional name and language), write the mail per survey language (placeholders {name}, {link}, {frist}, {titel}),
send a test mail to themselves, and send the invitations once the survey runs. Up to two reminders go to whoever has
not taken part, a set number of days after the invitation (the housekeeping puts them in the queue, due()), or at
once by a button. Every mail ends with a link to opt out (participate.no_mail; also as List-Unsubscribe).

Anonymous (M6): each address gets a personal link with a token (?einladung=…). The participation never stores it:
the session keeps the number of the list entry, and the entry learns only "begonnen" and "teilgenommen", without a
time and without the participation, and the list shows them only after the next daily step (apply_states). So the
answers stay without a link to the address. The token is not stored either: it is derived from a key (TOKEN_KEY, else
SECRET_KEY; _keys) and the entry (token()), only its hash is kept to find the entry.

Limits: INVITES_PER_DAY mails of a survey within 24 hours (Slack hears when it is reached), MAIL_PER_ACCOUNT_DAY
mails one account queues across all its surveys (mailqueue.room_for, also the links per person; Slack hears of the
first series of an account within a day), MAX_LIST addresses per survey. The list goes with the period "Kennungen und Kontaktdaten" (retention.anonymise), by hand and with the
survey; an opted-out address stays as a salted hash only, so it is not added again.

Also here (M7a): the links of "Eigene Links für Personen" by mail. Addresses typed there are not stored; they wait
in the queue only until the mail is sent.
"""
import csv
import hashlib
import hmac
import io
import re
from datetime import timedelta

from flask import Blueprint, abort, current_app, flash, redirect, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.lang import de_en
from app.models import MailOutbox, Survey, SurveyInvite, utcnow

bp = Blueprint("survey_invites", __name__, url_prefix="/surveys")

PARAM = "einladung"   # the parameter of the personal link
SESSION_PREFIX = "iv"  # session key iv<survey id>: the list entry of this participation (app/hosts.py hands it over)
MAX_LIST = 10_000
MAX_REMINDERS = 2
PLACEHOLDERS = ("{name}", "{link}", "{frist}", "{titel}")

DEFAULTS = {
    "de": {"subject": "Einladung zur Befragung „{titel}“",
           "body": "Guten Tag {name},\n\nwir laden Sie herzlich zu unserer Befragung „{titel}“ ein. Ihr persönlicher "
                   "Link:\n\n{link}\n\nDie Befragung läuft bis {frist}. Vielen Dank für Ihre Teilnahme!"},
    "en": {"subject": "Invitation to the survey “{titel}”",
           "body": "Hello {name},\n\nwe warmly invite you to our survey “{titel}”. Your personal link:\n\n{link}\n\n"
                   "The survey runs until {frist}. Thank you for taking part!"},
}
REMINDER_DEFAULTS = {
    "de": {"subject": "Erinnerung: Befragung „{titel}“",
           "body": "Guten Tag {name},\n\nvor einigen Tagen haben wir Sie zu unserer Befragung „{titel}“ eingeladen. "
                   "Falls Sie noch nicht dazu gekommen sind, freuen wir uns, wenn Sie bis {frist} teilnehmen:\n\n{link}"},
    "en": {"subject": "Reminder: survey “{titel}”",
           "body": "Hello {name},\n\na few days ago we invited you to our survey “{titel}”. If you have not found the "
                   "time yet, we would be glad if you took part by {frist}:\n\n{link}"},
}


# --- the list -------------------------------------------------------------------------------------------------------


def _keys():
    """The keys of tokens and address hashes, the one for new ones first. A key of their own from TOKEN_KEY when the
    server has one, which survives a new SECRET_KEY (audit 2026-10-09b, E9, like app/secrets_box.py); else SECRET_KEY
    as before. SECRET_KEY stays second, so what was made with it before TOKEN_KEY was set still counts."""
    config, out = current_app.config, []
    # the TOKEN_KEYs and SECRET_KEYs of before (*_FALLBACKS) after their own: links already sent keep working while a
    # key is changed (audit 2026-10-13, KR-6)
    for key in [config.get("TOKEN_KEY"), *(config.get("TOKEN_KEY_FALLBACKS") or [])]:
        if key:
            out.append(hashlib.sha256(("survey-invite-key:" + key).encode()).digest())
    for key in [config["SECRET_KEY"] or "", *(config.get("SECRET_KEY_FALLBACKS") or [])]:
        if key.encode() not in out:
            out.append(key.encode())
    return out


def email_hash(survey, email, key=None):
    return hmac.new(key or _keys()[0], f"survey-invite-mail:{survey.id}:{email}".encode(), hashlib.sha256).hexdigest()


def _token(invite_id, hashed, key):
    return hmac.new(key, f"survey-invite:{invite_id}:{hashed}".encode(), hashlib.sha256).hexdigest()[:32]


def token(invite, key=None):
    """The token of the personal link: derived, never stored (a copy of the database alone gives no link)."""
    return _token(invite.id, invite.email_hash, key or _keys()[0])


def token_for(invite):
    """The token whose hash the entry keeps: made with the key of the entry's time. If no key makes it any more
    (SECRET_KEY changed without TOKEN_KEY), the entry gets a new one, so the links of the next mail work again and
    opting out with them reaches the entry (E9); the caller commits."""
    keys = _keys()
    for key in keys:
        value = token(invite, key)
        if _hash(value) == invite.token_hash:
            return value
    value = token(invite, keys[0])
    invite.token_hash = _hash(value)
    return value


def _hash(value):
    return hashlib.sha256((value or "").encode()).hexdigest()


def find(survey, value):
    if not value or len(value) > 64:
        return None
    return db.session.scalar(db.select(SurveyInvite).filter_by(survey_id=survey.id, token_hash=_hash(value)))


def rows_of(text):
    """[(email, name, lang)] from pasted lines or a CSV file: separated by ";", "," or tab; a head line with "email"
    (or "e-mail", "mail", "adresse") names the columns, else e-mail, name, language in this order."""
    from app import passwords

    lines = [line for line in (text or "").replace("\r", "").split("\n") if line.strip()]
    if not lines:
        return [], 0
    sample = lines[0]
    sep = max((";", ",", "\t"), key=sample.count) if any(c in sample for c in ";,\t") else ";"
    try:
        reader = list(csv.reader(lines, delimiter=sep))
    except csv.Error:  # a cell beyond the csv module's field limit: nothing to read (audit 2026-10-22, C-03)
        return [], len(lines)
    head = [c.strip().lower() for c in reader[0]]
    cols = {"email": 0, "name": 1, "lang": 2}
    if any(h in ("email", "e-mail", "mail", "adresse", "address", "e-mail-adresse") for h in head):
        cols = {"email": next(i for i, h in enumerate(head) if h in ("email", "e-mail", "mail", "adresse", "address", "e-mail-adresse")),
                "name": next((i for i, h in enumerate(head) if h in ("name", "anrede", "vorname")), None),
                "lang": next((i for i, h in enumerate(head) if h in ("sprache", "language", "lang")), None)}
        reader = reader[1:]
    out, bad = [], 0
    for row in reader:
        def cell(key):
            i = cols.get(key)
            return row[i].strip() if i is not None and i < len(row) else ""
        email = passwords.normal_email(cell("email"))
        if email is None:
            bad += 1
            continue
        out.append((email, " ".join(cell("name").split())[:200], cell("lang").lower()[:10]))
    return out, bad


def add(survey, rows):
    """Put the addresses on the list; returns {"added", "twice", "optout", "full"}. Under the survey's lock (two
    uploads at once neither add an address twice nor pass MAX_LIST, E1; the database refuses a second row of the same
    address as well), in two statements for the whole list instead of two per address (E11, P7)."""
    from app import mailqueue

    mailqueue.lock_survey(survey.id)
    known = {h: s for h, s in db.session.execute(
        db.select(SurveyInvite.email_hash, SurveyInvite.status).where(SurveyInvite.survey_id == survey.id))}
    count = len(known)
    keys = _keys()
    result = {"added": 0, "twice": 0, "optout": 0, "full": 0}
    new = []
    for email, name, lang in rows:
        hashes = [email_hash(survey, email, key) for key in keys]
        seen = next((h for h in hashes if h in known), None)
        if seen is not None:
            result["optout" if known[seen] == SurveyInvite.OPTOUT else "twice"] += 1
            continue
        if count >= MAX_LIST:
            result["full"] += 1
            continue
        h = hashes[0]
        new.append({"survey_id": survey.id, "email": email, "email_hash": h, "name": name,
                    "lang": lang if lang in survey.languages else "", "token_hash": "-" + h[:60]})
        known[h] = SurveyInvite.OPEN
        count += 1
    if new:
        # ids and hashes come back in pairs (the hash is unique within the survey), so their order does not matter
        made = db.session.execute(db.insert(SurveyInvite).returning(SurveyInvite.id, SurveyInvite.email_hash), new).all()
        db.session.execute(db.update(SurveyInvite), [{"id": i, "token_hash": _hash(_token(i, h, keys[0]))}
                                                     for i, h in made])
    result["added"] = len(new)
    db.session.commit()
    return result


def counts(survey):
    rows = dict(db.session.execute(db.select(SurveyInvite.status, db.func.count(SurveyInvite.id))
                                   .where(SurveyInvite.survey_id == survey.id).group_by(SurveyInvite.status)).all())
    unsent = db.session.scalar(db.select(db.func.count(SurveyInvite.id)).filter_by(
        survey_id=survey.id, status=SurveyInvite.OPEN, sends=0)) or 0
    failed = db.session.scalar(db.select(db.func.count(SurveyInvite.id)).filter_by(survey_id=survey.id, failed=True)) or 0
    remindable = db.session.scalar(db.select(db.func.count(SurveyInvite.id)).filter(
        SurveyInvite.survey_id == survey.id, SurveyInvite.status.in_((SurveyInvite.OPEN, SurveyInvite.STARTED)),
        SurveyInvite.sends >= 1, SurveyInvite.sends <= MAX_REMINDERS)) or 0
    from app import mailqueue

    total = sum(rows.values())
    return {"total": total, "open": rows.get(SurveyInvite.OPEN, 0), "started": rows.get(SurveyInvite.STARTED, 0),
            "done": rows.get(SurveyInvite.DONE, 0), "optout": rows.get(SurveyInvite.OPTOUT, 0), "unsent": unsent,
            "failed": failed, "remindable": remindable, "queued": mailqueue.waiting(survey.id)}


def remove_all(survey):
    """The whole list and its waiting mails; opted-out addresses stay as hashes."""
    db.session.execute(db.delete(MailOutbox).where(MailOutbox.survey_id == survey.id, MailOutbox.sent_at.is_(None)))
    db.session.execute(db.delete(SurveyInvite).where(SurveyInvite.survey_id == survey.id,
                                                     SurveyInvite.status != SurveyInvite.OPTOUT))


def forget(survey):
    """Everything of the survey: when it is deleted, or its period "Kennungen und Kontaktdaten" is over. Sent rows of
    the queue hold no address and stay their day for the limit per account."""
    db.session.execute(db.delete(MailOutbox).where(MailOutbox.survey_id == survey.id, MailOutbox.sent_at.is_(None)))
    db.session.execute(db.delete(SurveyInvite).where(SurveyInvite.survey_id == survey.id))


# --- the mail -------------------------------------------------------------------------------------------------------


def settings(survey):
    stored = (survey.panel or {}).get("invite_mail") or {}
    days = [d for d in (stored.get("reminders") or []) if isinstance(d, int) and 1 <= d <= 60][:MAX_REMINDERS]
    return {"texts": stored.get("texts") or {}, "reminders": sorted(days), "only": bool(stored.get("only"))}


def text_for(survey, lang, kind="invite"):
    """(subject, body) for `lang`: what the researchers wrote, else the default of that language (German, English),
    else what they wrote for the default language of the survey."""
    texts = settings(survey)["texts"]
    defaults = REMINDER_DEFAULTS if kind == "reminder" else DEFAULTS
    key = "reminder" if kind == "reminder" else "invite"
    for choice in (lang, survey.default_language):
        own = (texts.get(choice) or {}).get(key) or {}
        if own.get("subject") and own.get("body"):
            return own["subject"], own["body"]
        if choice in defaults:
            return defaults[choice]["subject"], defaults[choice]["body"]
    return defaults["en"]["subject"], defaults["en"]["body"]


def personal_link(survey, value):
    from app import links

    return links.participant_url(survey, [(PARAM, value)])


def no_mail_link(survey, value):
    from app.hosts import participant_base

    return participant_base() + f"/s/{survey.public_key}/no-mail/{value}"


# {link} followed by something that would become part of the address: "{link}&ref={name}" would give the
# participation a parameter that names the person (audit 2026-10-09b, E7; Ralf 2026-10-09). A punctuation mark that
# ends the sentence may follow, then a space or the end.
_LINK_GLUED = re.compile(r"\{link\}(?![.,;:!?)»“”\"']*(?:\s|$))")


def link_glued(text):
    return bool(_LINK_GLUED.search(text or ""))


def _fill(text, survey, lang, name, link):
    from app.ui_i18n import date_text, tr

    end = survey.field_end
    if end is not None:
        from app.timeutil import last_day

        frist = date_text(last_day(end), lang)
    else:
        frist = tr(lang, "Ende der Feldzeit", "the end of fieldwork")
    title = ((survey.title_translations or {}).get(lang) or survey.title) if isinstance(survey.title_translations, dict) else survey.title
    values = {"{name}": name or "", "{link}": link, "{frist}": frist, "{titel}": title}
    out = _LINK_GLUED.sub("{link} ", text)  # a text saved before texts() refused it: nothing joins the link (E7)
    for key, value in values.items():
        out = out.replace(key, value)
    return re.sub(r"[ \t]+,", ",", out)  # "Guten Tag ," without a name


def compose(survey, invite, kind="invite", value=None):
    """(subject, body) of the mail for this entry."""
    from app.ui_i18n import tr

    lang = invite.lang or survey.default_language
    value = value or token_for(invite)
    subject, body = text_for(survey, lang, kind)
    subject = _fill(subject, survey, lang, invite.name, "").strip()[:300]
    body = _fill(body, survey, lang, invite.name, personal_link(survey, value))
    footer = tr(lang, "Keine weiteren E-Mails zu dieser Befragung:", "No further emails about this survey:")
    return subject, f"{body}\n\n-- \n{footer} {no_mail_link(survey, value)}"


def _reply_to(survey):
    from app.models import UserLogin

    row = db.session.get(UserLogin, current_user.id) if current_user and current_user.is_authenticated else None
    return row.email if row is not None and row.email else None


def _sent_today(survey):
    since = utcnow() - timedelta(days=1)
    from app import mailqueue

    return (db.session.scalar(db.select(db.func.count(SurveyInvite.id)).filter(
        SurveyInvite.survey_id == survey.id, SurveyInvite.last_sent_at >= since)) or 0) + mailqueue.waiting(survey.id)


def _room(survey):
    return max(0, int(current_app.config.get("INVITES_PER_DAY") or 2000) - _sent_today(survey))


def _queued_ids(survey):
    return set(db.session.scalars(db.select(MailOutbox.invite_id).filter(MailOutbox.survey_id == survey.id,
                                                                        MailOutbox.sent_at.is_(None),
                                                                        MailOutbox.invite_id.is_not(None))))


def _capped(survey):
    from app import notify

    try:
        notify.slack(f":envelope: Befragung {notify.survey_ref(survey)}: Tagesgrenze der Einladungsmails "
                     f"({current_app.config.get('INVITES_PER_DAY')}) erreicht; der Rest geht in den nächsten Tagen.")
    except Exception as e:  # noqa: BLE001 - the note must not break anything
        current_app.logger.warning("Invitation cap not reported in Slack: %s", type(e).__name__)


def queue(survey, kind, query, reply_to=None):
    """Put the mails of the entries `query` selects in the queue, within the daily limit; returns (queued, left)."""
    from app import mailqueue

    # first the locks, then counting: a second request (double click, two tabs) waits and sees what this one queued
    # (audit 2026-10-09b, E1); the account before the survey, everywhere
    mailqueue.lock_account(current_user.id)
    mailqueue.lock_survey(survey.id)
    room = min(_room(survey), mailqueue.room_for(current_user.id))
    queued_already = _queued_ids(survey)
    entries = [i for i in db.session.scalars(query.order_by(SurveyInvite.id)) if i.id not in queued_already and i.email]
    mailqueue.announce(current_user, min(room, len(entries)), survey)
    if entries[:room]:
        _remember_sender(survey, current_user.id)
    for invite in entries[:room]:
        subject, body = compose(survey, invite, kind)
        mailqueue.enqueue(invite.email, subject, body, kind=kind, survey_id=survey.id, invite_id=invite.id,
                          reply_to=reply_to, sender_id=current_user.id)
    db.session.commit()
    left = max(0, len(entries) - room)
    if left:
        _capped(survey)
    return min(room, len(entries)), left


def _remember_sender(survey, user_id):
    """The account that sent the invitations: its reminders count for it (MAIL_PER_ACCOUNT_DAY, E3)."""
    stored = dict(survey.panel or {})
    cfg = dict(stored.get("invite_mail") or {})
    if cfg.get("sender") != user_id:
        cfg["sender"] = user_id
        stored["invite_mail"] = cfg
        survey.panel = stored


def _sender(survey):
    """Who the reminders of this survey count for: the account that sent the invitations, else the one that made
    the survey; None when neither is there any more (then no reminder goes)."""
    from app.models import User

    for uid in ((((survey.panel or {}).get("invite_mail")) or {}).get("sender"), survey.created_by_id):
        if isinstance(uid, int) and db.session.get(User, uid) is not None:
            return uid
    return None


def due(now):
    """From the housekeeping (one process at a time, mailqueue.run): reminders that are due, for running surveys.
    Each survey under the locks of its sender and itself, like queue(), and each entry claimed with an UPDATE that
    only succeeds while it still has `step` mails: no entry is reminded twice (audit 2026-10-09b, E1). The reminders
    count for the account that sent the invitations (E3)."""
    from app import mailqueue

    ids = list(db.session.scalars(db.select(Survey.id).where(
        Survey.status == Survey.RUNNING, Survey.id.in_(db.select(SurveyInvite.survey_id).distinct()))))
    for survey_id in ids:
        survey = db.session.get(Survey, survey_id)
        days = settings(survey)["reminders"] if survey is not None else []
        sender = _sender(survey) if days else None
        if not days or sender is None:
            continue
        mailqueue.lock_account(sender)
        mailqueue.lock_survey(survey.id)
        queued_already = _queued_ids(survey)
        room = min(_room(survey), mailqueue.room_for(sender))
        for step, day in enumerate(days, start=1):
            # entry with `step` mails so far (the invitation, then reminders) whose last one is the step's gap old
            gap = day - (days[step - 2] if step > 1 else 0)
            for invite in list(db.session.scalars(db.select(SurveyInvite).filter(
                    SurveyInvite.survey_id == survey.id, SurveyInvite.sends == step,
                    SurveyInvite.status.in_((SurveyInvite.OPEN, SurveyInvite.STARTED)),
                    SurveyInvite.last_sent_at <= now - timedelta(days=gap)).order_by(SurveyInvite.id))):
                if invite.id in queued_already or not invite.email:
                    continue
                if room <= 0:
                    _capped(survey)
                    break
                claimed = db.session.execute(db.update(SurveyInvite).where(
                    SurveyInvite.id == invite.id, SurveyInvite.sends == step,
                    SurveyInvite.status.in_((SurveyInvite.OPEN, SurveyInvite.STARTED)))
                    .values(sends=SurveyInvite.sends).execution_options(synchronize_session=False)).rowcount
                if claimed != 1:
                    continue
                subject, body = compose(survey, invite, "reminder")
                mailqueue.enqueue(invite.email, subject, body, kind="reminder", survey_id=survey.id,
                                  invite_id=invite.id, sender_id=sender)
                room -= 1
        db.session.commit()  # ends the locks of this survey


# --- participation (app/participate.py) -------------------------------------------------------------------------------


def session_key(survey):
    return f"{SESSION_PREFIX}{survey.id}"


def capture(survey, args):
    """The personal link was opened: remember the entry for this participation (not the token, nothing stored)."""
    from flask import session

    value = args.get(PARAM)
    if not value:
        return
    invite = find(survey, value)
    if invite is not None and invite.status != SurveyInvite.OPTOUT:
        session[session_key(survey)] = invite.id


def only_invited(survey):
    """Whether the survey takes only people with a personal link, each link for one participation (setting "Nur mit
    persönlichem Link teilnehmen"; audit 2026-10-18, BIZ-09). Only while it has a list: once the list is deleted, the
    links already sent work as an ordinary link again (as the dialog of "Adressliste löschen" says)."""
    if not settings(survey)["only"]:
        return False
    return db.session.scalar(db.select(SurveyInvite.id).filter_by(survey_id=survey.id).limit(1)) is not None


def refusal(survey):
    """Why this browser may not begin a participation (only_invited): "missing" without a personal link of the list,
    "used" once its link began or ended a participation (also in another browser); else None. Nothing about the
    entry goes into the participation, as before (M6)."""
    from flask import session

    if not only_invited(survey):
        return None
    invite = db.session.get(SurveyInvite, session.get(session_key(survey)) or 0)
    if invite is None or invite.survey_id != survey.id or invite.status == SurveyInvite.OPTOUT:
        return "missing"
    if invite.status != SurveyInvite.OPEN or invite.pending_status is not None:
        return "used"
    return None


def started(survey):
    """The participation of this browser was stored: its entry is "begonnen" (noted only; the list shows it after the
    next daily step, apply_states). Returns False if the survey takes only invited people (only_invited) and this
    browser's link is missing or began a participation already: then the caller stores nothing. Claimed in one
    statement, so of two browsers with the same link at the same moment only one goes on."""
    from flask import session

    invite_id = session.get(session_key(survey))
    claimed = 0
    if invite_id:
        claimed = db.session.execute(db.update(SurveyInvite).where(SurveyInvite.id == invite_id,
                                                                   SurveyInvite.survey_id == survey.id,
                                                                   SurveyInvite.status == SurveyInvite.OPEN,
                                                                   SurveyInvite.pending_status.is_(None))
                                     .values(pending_status=SurveyInvite.STARTED)
                                     .execution_options(synchronize_session=False)).rowcount
    return bool(claimed) or not only_invited(survey)


def ended(survey, commit=True):
    """The participation ended (completed, screened out, quota full): no reminder any more. Called where the flow
    ends it (runtime, within its transaction) and again by the end page."""
    from flask import has_request_context, session

    if not has_request_context():
        return
    invite_id = session.pop(session_key(survey), None)
    if invite_id:
        db.session.execute(db.update(SurveyInvite).where(SurveyInvite.id == invite_id,
                                                         SurveyInvite.status.in_((SurveyInvite.OPEN, SurveyInvite.STARTED)))
                           .values(pending_status=SurveyInvite.DONE))
        if commit:
            db.session.commit()


def apply_states():
    """Once a day (mailqueue.run): "begonnen" and "teilgenommen" of the participations since the last time go into
    the list together. Shown at the moment they happened, comparing the list before and after one start would tie the
    address to that participation (audit 2026-10-09b, E7; Ralf 2026-10-09: only once a day). Reminders go by the list
    as shown, so a person who took part today may still get one."""
    db.session.execute(db.update(SurveyInvite).where(
        SurveyInvite.pending_status == SurveyInvite.DONE,
        SurveyInvite.status.in_((SurveyInvite.OPEN, SurveyInvite.STARTED))).values(status=SurveyInvite.DONE))
    db.session.execute(db.update(SurveyInvite).where(
        SurveyInvite.pending_status == SurveyInvite.STARTED, SurveyInvite.status == SurveyInvite.OPEN)
        .values(status=SurveyInvite.STARTED))
    db.session.execute(db.update(SurveyInvite).where(SurveyInvite.pending_status.is_not(None)).values(pending_status=None))
    db.session.commit()


def opt_out(invite):
    invite.status, invite.email, invite.name, invite.pending_status = SurveyInvite.OPTOUT, "", "", None
    db.session.execute(db.delete(MailOutbox).where(MailOutbox.invite_id == invite.id))
    db.session.commit()


# --- pages ----------------------------------------------------------------------------------------------------------


def _survey(survey_id):
    from app import auth, mail
    from app.surveys import get_survey

    survey = get_survey(survey_id, need="admin")
    if not mail.enabled() or survey.kind != "survey" or not auth.may_create_surveys(current_user):
        abort(404)
    return survey


def _back(survey):
    return redirect(url_for("surveys.field", survey_id=survey.id) + "#einladungen")


def may_use(survey):
    """For the template: the block "Einladungen per E-Mail" (mail on, a questionnaire, role Admin, may create)."""
    from app import auth, mail

    return (mail.enabled() and survey.kind == "survey" and survey.role_of(current_user) == "admin"
            and auth.may_create_surveys(current_user))


def state(survey):
    s = settings(survey)
    langs = list(survey.languages or [survey.default_language])
    texts = {lang: {kind: dict(zip(("subject", "body"), text_for(survey, lang, kind))) for kind in ("invite", "reminder")}
             for lang in langs}
    return {"counts": counts(survey), "reminders": s["reminders"], "only": s["only"], "langs": langs, "texts": texts,
            "per_day": current_app.config.get("INVITES_PER_DAY"), "max_list": MAX_LIST}


@bp.route("/<sid:survey_id>/invitations/addresses", methods=["POST"])
@login_required
def addresses(survey_id):
    from app import audit

    survey = _survey(survey_id)
    text = request.form.get("addresses", "")
    upload = request.files.get("file")
    if upload is not None and upload.filename:
        raw = upload.read(5 * 1024 * 1024 + 1)
        if len(raw) > 5 * 1024 * 1024:
            flash(de_en("Die Datei ist größer als 5 MB.", "The file is larger than 5 MB."), "error")
            return _back(survey)
        for encoding in ("utf-8-sig", "cp1252"):
            try:
                text += "\n" + raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
    rows, bad = rows_of(text)
    if not rows:
        flash(de_en("Keine gültige E-Mail-Adresse gefunden.", "No valid email address found."), "error")
        return _back(survey)
    result = add(survey, rows)
    audit.note(f"+{result['added']}")
    parts = [de_en("{n} Adressen aufgenommen", "{n} addresses added", n=result["added"])]
    if result["twice"]:
        parts.append(de_en("{n} standen schon auf der Liste", "{n} were already on the list", n=result["twice"]))
    if result["optout"]:
        parts.append(de_en("{n} haben sich abgemeldet und werden nicht wieder aufgenommen",
                           "{n} opted out and are not added again", n=result["optout"]))
    if bad:
        parts.append(de_en("{n} Zeilen ohne gültige Adresse", "{n} lines without a valid address", n=bad))
    if result["full"]:
        parts.append(de_en("{n} über der Grenze von {m} Adressen", "{n} over the limit of {m} addresses",
                           n=result["full"], m=MAX_LIST))
    flash("; ".join(parts) + ".", "success" if result["added"] else "warning")
    return _back(survey)


@bp.route("/<sid:survey_id>/invitations/texts", methods=["POST"])
@login_required
def texts(survey_id):
    survey = _survey(survey_id)
    stored = dict(survey.panel or {})
    mail_cfg = dict(stored.get("invite_mail") or {})
    own = dict(mail_cfg.get("texts") or {})
    for lang in survey.languages or [survey.default_language]:
        entry = dict(own.get(lang) or {})
        for kind in ("invite", "reminder"):
            subject = " ".join(request.form.get(f"{kind}_subject_{lang}", "").split())[:200]
            body = request.form.get(f"{kind}_body_{lang}", "").replace("\r", "")[:5000].strip()
            if subject or body:
                if body and "{link}" not in body:
                    flash(de_en("Der Text braucht den Platzhalter {link}, sonst fehlt der persönliche Link.",
                                "The text needs the placeholder {link}, otherwise the personal link is missing."), "error")
                    return _back(survey)
                if link_glued(body):
                    flash(de_en("Nach {link} muss ein Leerzeichen oder ein Zeilenende folgen: Am persönlichen Link darf "
                                "nichts angehängt werden, sonst wären die Antworten nicht mehr anonym.",
                                "{link} must be followed by a space or a line break: nothing may be added to the "
                                "personal link, otherwise the answers would no longer be anonymous."), "error")
                    return _back(survey)
                entry[kind] = {"subject": subject, "body": body}
        own[lang] = entry
    days = []
    for name in ("reminder1", "reminder2"):
        value = request.form.get(name, type=int)
        if value and 1 <= value <= 60:
            days.append(value)
    mail_cfg.update(texts=own, reminders=sorted(set(days))[:MAX_REMINDERS], only=request.form.get("only") == "1")
    stored["invite_mail"] = mail_cfg
    survey.panel = stored
    db.session.commit()
    flash(de_en("Gespeichert.", "Saved."), "success")
    return _back(survey)


@bp.route("/<sid:survey_id>/invitations/test", methods=["POST"])
@login_required
def test_mail(survey_id):
    from app import mail
    from app.models import UserLogin

    survey = _survey(survey_id)
    row = db.session.get(UserLogin, current_user.id)
    to = row.email if row is not None and row.email else None
    if to is None:
        flash(de_en("Ihr Konto hat keine E-Mail-Adresse. Hinterlegen Sie auf „Konto › Profil und Anmeldung“ eine Anmeldung mit E-Mail-Adresse.",
                    "Your account has no email address. Add a sign-in with an email address on “Account › Profile and sign-in”."), "error")
        return _back(survey)
    lang = request.form.get("lang") if request.form.get("lang") in (survey.languages or []) else survey.default_language
    kind = "reminder" if request.form.get("kind") == "reminder" else "invite"
    sample = SurveyInvite(survey_id=survey.id, name=current_user.name or "", lang=lang, email_hash="test")
    subject, body = compose(survey, sample, kind, value="beispiel")
    mail.send(to, "[Test] " + subject, body)
    flash(de_en("Test-E-Mail an {e} verschickt. Der Link darin ist ein Beispiel und gilt nicht.",
                "Test email sent to {e}. Its link is an example and does not work.", e=to), "success")
    return _back(survey)


@bp.route("/<sid:survey_id>/invitations/send", methods=["POST"])
@login_required
def send(survey_id):
    from app import audit, mailqueue

    survey = _survey(survey_id)
    if survey.status != Survey.RUNNING:
        flash(de_en("Einladungen gehen erst raus, wenn die Befragung läuft.", "Invitations go out only while the survey runs."),
              "error")
        return _back(survey)
    kind = "reminder" if request.form.get("kind") == "reminder" else "invite"
    if kind == "invite":
        query = db.select(SurveyInvite).filter_by(survey_id=survey.id, status=SurveyInvite.OPEN, sends=0)
    else:
        query = db.select(SurveyInvite).filter(
            SurveyInvite.survey_id == survey.id, SurveyInvite.status.in_((SurveyInvite.OPEN, SurveyInvite.STARTED)),
            SurveyInvite.sends >= 1)
    queued, left = queue(survey, kind, query, reply_to=_reply_to(survey))
    audit.note(f"{kind} {queued}")
    mailqueue.kick()
    if left:
        flash(de_en("{n} E-Mails sind unterwegs; {m} weitere überschreiten die Tagesgrenze ({d} je Befragung, {a} je Konto). "
                    "Senden Sie sie morgen.",
                    "{n} emails are on their way; {m} more exceed the daily limit ({d} per survey, {a} per account). "
                    "Send them tomorrow.", n=queued, m=left, d=current_app.config.get("INVITES_PER_DAY"),
                    a=current_app.config.get("MAIL_PER_ACCOUNT_DAY")), "warning")
    else:
        flash(de_en("{n} E-Mails sind unterwegs.", "{n} emails are on their way.", n=queued), "success")
    return _back(survey)


@bp.route("/<sid:survey_id>/invitations.csv")
@login_required
def export(survey_id):
    from flask import Response as FlaskResponse

    from app.export import safe_text

    survey = _survey(survey_id)
    names = {SurveyInvite.OPEN: de_en("offen", "open"), SurveyInvite.STARTED: de_en("begonnen", "started"),
             SurveyInvite.DONE: de_en("teilgenommen", "took part"), SurveyInvite.OPTOUT: de_en("abgemeldet", "opted out")}
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", lineterminator="\r\n")
    writer.writerow([de_en("E-Mail", "Email"), de_en("Name", "Name"), de_en("Sprache", "Language"),
                     de_en("Stand", "State"), de_en("E-Mails", "Emails"), de_en("Fehler", "Error")])
    for i in db.session.scalars(db.select(SurveyInvite).filter_by(survey_id=survey.id).order_by(SurveyInvite.id)):
        if i.status == SurveyInvite.OPTOUT:
            continue  # the address is gone
        writer.writerow([safe_text(c) for c in (i.email, i.name, i.lang, names.get(i.status, i.status), i.sends,
                                                de_en("ja", "yes") if i.failed else "")])
    filename = re.sub(r"[^A-Za-z0-9_-]+", "_", f"einladungen-{survey.public_key}") + ".csv"
    return FlaskResponse("﻿" + out.getvalue(), mimetype="text/csv; charset=utf-8",
                         headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


@bp.route("/<sid:survey_id>/invitations/delete", methods=["POST"])
@login_required
def delete(survey_id):
    survey = _survey(survey_id)
    remove_all(survey)
    db.session.commit()
    flash(de_en("Adressliste gelöscht; abgemeldete Adressen bleiben nur als Prüfwert, damit sie nicht wieder aufgenommen werden.",
                "Address list deleted; opted-out addresses stay only as a check value, so they are not added again."),
          "success")
    return _back(survey)


# --- links per person by mail (M7a) ------------------------------------------------------------------------------------


@bp.route("/<sid:survey_id>/value-links/mail", methods=["POST"])
@login_required
def value_links_mail(survey_id):
    """Send each person of "Eigene Links für Personen" their participation link (and dashboard link) by mail. The
    addresses come with the form and are not stored beyond the queue."""
    from app import auth, boards, links, mail, mailqueue, panel, passwords
    from app.surveys import get_survey

    survey = get_survey(survey_id, need="admin")
    if not mail.enabled() or not auth.may_create_surveys(current_user):
        abort(404)
    shares = boards.value_share_urls(survey)
    labels = panel.value_labels(survey)
    note = request.form.get("note", "").replace("\r", "").strip()[:2000]
    count = 0
    mailqueue.lock_account(current_user.id)  # a second request at the same moment counts after this one (E1)
    room = mailqueue.room_for(current_user.id)
    wanted = sum(1 for k, v in request.form.items() if k.startswith("mail:") and passwords.normal_email(v))
    mailqueue.announce(current_user, min(room, wanted), survey)
    over, bad = 0, []
    for key, value in request.form.items():
        if not key.startswith("mail:"):
            continue
        _, name, val = key.split(":", 2)
        allowed = next((p for p in panel.config(survey)["params"] if p["name"] == name), None)
        if allowed is None or val not in (allowed.get("allowed") or []):
            continue
        email = passwords.normal_email(value)
        if email is None:
            if value.strip():  # typed but no address: said below, so nobody waits for a mail that never left
                bad.append(labels.get(name, {}).get(val) or val)
            continue
        if count >= room:
            over += 1
            continue
        label = labels.get(name, {}).get(val, "")
        link = links.participant_url(survey, [(name, val)])
        board = shares.get((name, val))
        body = de_en("Guten Tag {n},\n\nIhr eigener Link zur Befragung „{t}“:\n\n{l}\n",
                     "Hello {n},\n\nyour own link to the survey “{t}”:\n\n{l}\n", n=label, t=survey.title, l=link)
        if board:
            body += de_en("\nWie viele über Ihren Link teilgenommen haben, sehen Sie hier:\n\n{b}\n",
                          "\nHow many took part through your link, you see here:\n\n{b}\n", b=board)
        if note:
            body += "\n" + note + "\n"
        mailqueue.enqueue(email, de_en("Ihr Link zur Befragung „{t}“", "Your link to the survey “{t}”", t=survey.title),
                          re.sub(r"[ \t]+,", ",", body) + mail.signature(), kind="links", survey_id=survey.id,
                          reply_to=_reply_to(survey), sender_id=current_user.id)
        count += 1
    db.session.commit()
    mailqueue.kick()
    flash(de_en("{n} E-Mails mit Links sind unterwegs.", "{n} emails with links are on their way.", n=count),
          "success" if count else "warning")
    if bad:
        flash(de_en("Nicht verschickt, die E-Mail-Adresse ist ungültig: {v}.",
                    "Not sent, the email address is not valid: {v}.", v=", ".join(bad)), "error")
    if over:
        flash(de_en("{m} weitere überschreiten die Grenze von {a} E-Mails je Konto und Tag. Senden Sie sie morgen.",
                    "{m} more exceed the limit of {a} emails per account and day. Send them tomorrow.", m=over,
                    a=current_app.config.get("MAIL_PER_ACCOUNT_DAY")), "warning")
    return redirect(url_for("surveys.field", survey_id=survey.id) + "#value-links")
