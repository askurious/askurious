"""The diary API for the Android app (askurious-android) and the participant pages around it (app/diary.py,
concepts/experiencesampling.md 7.5). On the participants' address (hosts.BLUEPRINT_SIDES).

No account: joining returns a random secret, the app keeps it and sends it as a bearer token; the row keeps only its
hash (Enrolment.secret_hash). The app sends no id of the device, and it clears the WebView's cookies before every
entry, so two studies on one phone cannot be linked.

    GET  /api/diary/v1/join/<code>   what the join screen shows (title, period, modules); "rejoin": true for a
                                     personal code that connects its participation again
    POST /api/diary/v1/enrol         {"code", "lang"} -> {"secret", "url"}: a new person; url opens the entry part.
                                     With a used personal code that may connect again (diary.rejoin):
                                     {"secret", "url", "rejoined": true, "status"}, the same person with a new secret
                                     (the earlier phone's no longer works), url only while the entry part is open
    POST /api/diary/v1/study         {"tz", "lang", "quiet"} -> the study (status, period, modules, seed, quiet hours);
                                     also notes the time zone and the quiet hours
    POST /api/diary/v1/entry         {"module", "due", "notified", "tz", "uuid"} -> {"url"}: a short-lived address of
                                     one entry
    POST /api/diary/v1/leave         no more prompts; the answers stay unless the person deletes them
    POST /api/diary/v1/erase         deletes all answers of the person and the person (as their deletion code does)
    POST /api/diary/v1/offline/<m>   the page of module m to keep on the phone ({"offline", "html", "version"})
    POST /api/diary/v1/offline       an entry answered without a connection, sent later (form fields, times, "uuid")

"uuid" (optional, 36 characters): the app's random id of one answer. The server keeps it (unique per person); the
same uuid again gets the answer of the first time and stores nothing (audit 2026-10-11, M5). Apps without it work
as before.

    GET  /j/<code>                   the join link behind the QR code: opens the app (App Link), else this page
    GET  /s/<uid>/e/<token>          the address of an entry (ENTRY_SECONDS): the WebView continues on /s/<uid>

A test join code (diary.test_code) makes a test person (source "test") who may also join a draft. Answers:
401 an unknown secret (the study or the person was deleted), 429 too many wrong secrets or codes from this address or
too many entries of this person in a short time (the app keeps the study then and tries later; a valid secret or code
is never refused by the count of wrong ones, audit 2026-10-11, M4), 409 not possible now ("closed", "ended", "done",
"daily_limit": the daily limit of a self-started module, the app does not try again; "used": a personal code
someone joined with, which may not connect again now), 410 a prompt past its time,
400 a body that is no JSON object.
"""
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, session, url_for

from app import diary
from app.extensions import csrf, db
from app.models import Enrolment, Response, Survey, new_pid, utcnow

bp = Blueprint("diary_api", __name__)

API = "/api/diary/v1"
PURPOSE = "diary-entry"  # the box of the entry addresses (secrets_box.seal)
ENTRY_SECONDS = 12 * 3600  # an entry address opens the entry within this time, again only in the browser that opened
# it first (the app's WebView after the system ended it; diary_access.REOPEN_SECONDS, Abgleich 2026-10-15, KR-12)
LATE_SECONDS = 5 * 60  # a prompt "due" this far in the future is still taken (clocks differ)


def _hash(secret):
    return hashlib.sha256(secret.encode()).hexdigest()


def _refuse(error, status):
    out = jsonify(error=error)
    out.status_code = status
    out.headers["Cache-Control"] = "no-store"
    if status == 401:
        out.headers["WWW-Authenticate"] = "Bearer"
    return out


def _ok(**values):
    out = jsonify(**values)
    out.headers["Cache-Control"] = "no-store"
    return out


def _lang(survey, wanted):
    if wanted in (survey.languages or []):
        return wanted
    if wanted == "de" and "de-du" in (survey.languages or []):
        return "de-du"
    return survey.default_language


def _person():
    """(survey, enrolment) of the bearer token, or a refusal. The secret is looked up first: a valid one always goes
    through, whatever other phones behind the same address (mobile networks, university WLAN) sent (audit
    2026-10-11, M4). Only wrong ones count, in a counter of their own; past the limit they answer 429, not 401: the
    app ends a study only on a lasting 401 (review 2026-10-11, B3). Authorised API requests hold the person's
    transaction lock and revalidate the secret after waiting: sync/settings writes cannot restore a revoked
    revision, and an in-flight old credential cannot act after reconnecting."""
    from app import limits

    value = request.headers.get("Authorization", "")
    supplied = _hash(value[7:].strip()) if value.startswith("Bearer ") else None
    row = db.session.scalar(db.select(Enrolment).filter_by(secret_hash=supplied)) \
        if value.startswith("Bearer ") else None
    survey = db.session.get(Survey, row.survey_id) if row else None
    if row is not None and diary.is_diary(survey):
        from app import diary_access

        row = diary_access.lock_person(row)
        if row is None or not secrets.compare_digest(row.secret_hash, supplied):
            # Credentials may have rotated, or the person may have been erased while this request waited.
            return None, _refuse("unauthorised", 401)
        return survey, row
    if not limits.allow_diary_token_try(count=False):
        return None, _refuse("limit", 429)
    limits.allow_diary_token_try()
    return None, _refuse("unauthorised", 401)


def _body():
    """The JSON body as a dict; {} without a body; None (answered 400) for a body that is no JSON object: a list, a
    number, a text, or JSON nested too deeply (audit 2026-10-11, N9, N10: these gave 500)."""
    data = request.get_json(silent=True)
    if data is None:
        return None if request.get_data(cache=True) else {}
    return data if isinstance(data, dict) else None


def _wrong_code(why=None):
    """A code that leads nowhere: 404, counted against this address; past CODE_LIMIT 429. A valid code is never
    refused by this count (audit 2026-10-11, M4). why="limit": the study had too many wrong test or invitation codes
    (diary.by_code): 429, as for a sender over its limit (the app says "try again later")."""
    from app import limits

    if why == "limit" or not limits.allow_code_try(count=False):
        return _refuse("limit", 429)
    limits.allow_code_try()
    return _refuse("unknown", 404)


@bp.route(API + "/join/<code>")
def join(code):
    survey, test = diary.by_code(code)
    if survey is None:
        return _wrong_code(test)
    if test == "used":  # a personal code someone joined with, which may not connect again now
        return _refuse("used", 409)
    if not diary.accepting(survey, test):
        return _refuse("closed", 409)
    return _ok(**diary.public_json(survey, _lang(survey, request.args.get("lang"))), test=test in ("test", "fast"),
               **({"rejoin": True} if test == "rejoin" else {}))


@bp.route(API + "/enrol", methods=["POST"])
@csrf.exempt
def enrol():
    """A new person. The entry part is its first participation; until it is completed the person is "pending"."""
    from app import limits

    data = _body()
    if data is None:
        return _refuse("unknown", 400)
    survey, test = diary.by_code(data.get("code") if isinstance(data.get("code"), str) else "")
    if survey is None:
        return _wrong_code(test)
    if test == "used":
        return _refuse("used", 409)
    if not diary.accepting(survey, test):
        return _refuse("closed", 409)
    if not limits.allow_start() or not limits.allow_survey_sender(survey):
        return _refuse("limit", 429)
    if test == "rejoin":
        return _rejoin(survey, data["code"])
    secret = secrets.token_urlsafe(32)
    person = Enrolment(survey_id=survey.id, secret_hash=_hash(secret), pid=new_pid(survey.code),
                       source="test" if test in ("test", "fast") else "real",
                       status=Enrolment.PENDING, language=_lang(survey, data.get("lang")), tz=_tz(data.get("tz")),
                       created_at=utcnow())
    if test == "fast":  # a study day in ten minutes, from now (diary.virtual; the app gets the clock with the study)
        person.settings = {"clock": {"t0": utcnow().replace(microsecond=0).isoformat(), "factor": diary.FAST_FACTOR}}
    db.session.add(person)
    db.session.flush()
    if test == "invite" and not diary.use_invite(survey, data["code"], person):  # a personal code joins one person (N4)
        db.session.rollback()
        return _refuse("used", 409)
    url = _entry_url(survey, _new_entry(survey, person, diary.ENTRY))
    db.session.commit()
    return _ok(secret=secret, url=url)


def _rejoin(survey, code):
    """A phone connects again to the participation its personal code joined (diary.rejoin; Philipp, 2026-10-13): the
    person gets a new secret, the earlier phone's no longer works, and everything the person did stays. "rejoined"
    tells the app not to begin anew; the address of the entry part only while it is not completed."""
    person = diary.rejoin(survey, code)
    if person is None:  # another phone used the release at the same moment
        return _refuse("used", 409)
    secret = secrets.token_urlsafe(32)
    from app import diary_access

    if diary_access.lock_person(person) is None or person.status == Enrolment.LEFT:
        return _refuse("used", 409)
    diary_access.revoke(person)
    person.secret_hash = _hash(secret)
    person.settings = {**(person.settings or {}), "rejoined_at": utcnow().replace(microsecond=0).isoformat()}
    status = diary.refresh(survey, person)
    first = diary.entry_part(person)
    url = None
    if status == Enrolment.PENDING and (first is None or first.status != Response.COMPLETED):
        response = first if first is not None and first.status == Response.ACTIVE else \
            _new_entry(survey, person, diary.ENTRY)
        url = _entry_url(survey, response) if response is not None else None
    db.session.commit()
    return _ok(secret=secret, url=url, rejoined=True, status=status)


def _tz(name):
    return name if isinstance(name, str) and diary.zone(name).key == name else "Europe/Berlin"


@bp.route(API + "/study", methods=["POST"])
@csrf.exempt
def study():
    survey, person = _person()
    if survey is None:
        return person
    if not _person_may_sync(person):
        return _refuse("limit", 429)
    data = _body()
    if data is None:
        return _refuse("unknown", 400)
    if data.get("tz"):  # from the person's next day (diary.set_tz, decision 2a)
        diary.set_tz(person, _tz(data["tz"]))
    if data.get("lang"):
        person.language = _lang(survey, data["lang"])
    if "quiet" in data:  # the quiet hours set in the app, from the person's next day (random prompts keep out of them)
        diary.set_quiet(person, data["quiet"])
    person.last_sync_at = utcnow()
    out = diary.study_json(survey, person, person.language)
    db.session.commit()
    return _ok(**out)


@bp.route(API + "/entry", methods=["POST"])
@csrf.exempt
def entry():
    """A one-time address for one entry. The entry part may be continued until it is completed; a module only while
    the person is active, a prompt only before it expires, a self-started report only up to its daily limit."""
    survey, person = _person()
    if survey is None:
        return person
    data = _body()
    if data is None:
        return _refuse("unknown", 400)
    if not _lock_entries(person):
        return _refuse("unauthorised", 401)
    if not _person_may_send(person):  # also before a repeated uuid: each answer gives a new address (G6)
        return _refuse("limit", 429)
    uid = _uuid(data)
    known = _by_uuid(person, uid)
    if known is not None:  # retry after a lost reply: same logical entry only
        if not _same_identity(known, data):
            return _refuse("unknown", 409)
        from app import diary_access

        if not diary_access.allowed(survey, person, known):
            return _refuse("paused" if person.status == Enrolment.PAUSED else "ended", 409)
        url = _entry_url(survey, known)
        db.session.commit()
        return _ok(url=url)
    if survey.status == Survey.CLOSED:  # data collection ended: no new entries (audit 2026-10-11, N7)
        return _refuse("closed", 409)
    if data.get("tz"):
        diary.set_tz(person, _tz(data["tz"]))
    status = diary.refresh(survey, person)
    part = data.get("module")
    if part == diary.ENTRY:
        first = diary.entry_part(person)
        if first is not None and first.status == Response.COMPLETED:
            return _refuse("done", 409)
        if status != Enrolment.PENDING:
            return _refuse("ended", 409)
        response = first if first is not None and first.status == Response.ACTIVE else \
            _new_entry(survey, person, diary.ENTRY, uid=uid)
        if response is None:  # the same answer at the same moment from another request
            return _again(survey, person, uid, None, None)
        url = _entry_url(survey, response)
        db.session.commit()
        return _ok(url=url)
    if part == diary.EXIT:  # the exit questionnaire: once, after the period (diary.exit_info)
        info = diary.exit_info(survey, person, status, person.language)
        if not info or not info["open"]:
            return _refuse("ended", 409)
        open_exit = diary.part_entry(person, diary.EXIT, Response.ACTIVE)
        response = open_exit or _new_entry(survey, person, diary.EXIT, {"tz": person.tz}, uid=uid)
        if response is None:
            return _again(survey, person, uid, None, None)
        url = _entry_url(survey, response)
        db.session.commit()
        return _ok(url=url)
    checked = _check_module(survey, person, status, part, data, diary.virtual(person))
    if not isinstance(checked, dict):
        return checked
    if checked["same"] is not None:
        if checked["same"].status != Response.ACTIVE:
            return _refuse("done", 409)
        url = _entry_url(survey, checked["same"])
        db.session.commit()
        return _ok(url=url)
    if checked["due"] is None:  # a report on an event: one still open today is continued, not a new one begun
        open_report = _open_report(person, part, diary.local_date(person, diary.virtual(person)))
        if open_report is not None:
            url = _entry_url(survey, open_report)
            db.session.commit()
            return _ok(url=url)
    response = _new_entry(survey, person, part, checked["occasion"], uid=uid)
    if response is None:  # the same prompt or answer at the same moment from another request (M2)
        return _again(survey, person, uid, part, checked["due"])
    url = _entry_url(survey, response)
    db.session.commit()
    return _ok(url=url)


def _again(survey, person, uid, part, due):
    """After a write refused by a unique key: what the other request stored. The same answer (uuid): its address;
    the same prompt: its address while it is open, else "done"."""
    db.session.expire_all()
    known = _by_uuid(person, uid)
    if known is None and due is not None:
        known = db.session.scalar(db.select(Response).filter_by(enrolment_id=person.id, part=part, due_at=due))
    if known is None or (uid is None and known.status != Response.ACTIVE):
        return _refuse("done", 409)
    url = _entry_url(survey, known)
    db.session.commit()
    return _ok(url=url)


def _check_module(survey, person, status, part, data, at, entries=None):
    """May this person begin an entry of module `part` at `at` (naive UTC; the time the person answered, for an
    entry sent later from the phone)? A refusal, or {"occasion", "same"}: the occasion of the new entry, and an
    entry already made for the same prompt (or None). An optional supplied history is for bulk callers;
    app requests use targeted queries under the admission lock."""
    m = diary.module(survey, part)
    if m is None:
        return _refuse("unknown", 404)
    if status == Enrolment.PAUSED:
        return _refuse("paused", 409)
    if status not in (Enrolment.ACTIVE, "finished") or (survey.status == Survey.DRAFT and person.source != "test"):
        return _refuse("ended", 409)
    trigger = (m.get("trigger") or {}).get("t")
    due, same = _millis(data.get("due")), None
    if due is not None:  # to the minute before every check: one prompt is one entry (audit 2026-10-11, M2)
        due = due.replace(second=0)
    started = diary.self_start(m)
    # a prompt belongs to the day it was due on, not to the day of the answer: the prompt of 23:30 on the last day
    # answered at 00:10 is still in the period (review 2026-10-15, G5)
    today = diary.prompt_day(person, due) if trigger != "self" and due is not None else diary.local_date(person, at)
    day = diary.day_index(person, today)
    if not diary.active_on(m, day) or (person.last_day and today > person.last_day):
        return _refuse("ended", 409)
    if trigger == "self":
        due = None
        # "0 = ohne Grenze" still has a ceiling (audit 2026-10-11, N5)
        limit = int((started or {}).get("max_per_day") or 0) or SELF_CEILING
        if _today_count(person, part, today, entries) >= limit:
            return _refuse("daily_limit", 409)  # not 429: the app does not try again (audit 2026-10-11, M4)
    else:
        # a time of the person's schedule, to the minute: the app cannot make entries of its own (review 2026-10-11, B2)
        if due is None or due > at + timedelta(seconds=LATE_SECONDS) or not diary.is_prompt(survey, person, part, due):
            return _refuse("unknown", 400)
        # over after its expiry, or without one at the end of its day, online as offline (review 2026-10-15, G4)
        if at > diary.prompt_over(m, due, today, diary.zone_on(person, today)):
            return _refuse("expired", 410)
        same = _same_prompt(part, due, entries, person=person)
    occasion = {"day": day, "due": due.isoformat() if due else None, "notified": _iso(_millis(data.get("notified"))),
                "tz": person.tz, "trigger": trigger if trigger in diary.TRIGGERS else "fixed"}  # random too (D-13)
    if data.get("reminded") is True and trigger != "self":  # opened from the reminder (diary.reminder_of)
        occasion["reminded"] = True
    return {"occasion": occasion, "same": same, "due": due, "module": m}


SELF_CEILING = 50  # entries a day of a self-started module whose limit is "0 = ohne Grenze" (the form's highest)


OFFLINE_DAYS = 7  # an entry answered without a connection is taken within this time


@bp.route(API + "/offline/<part>", methods=["POST"])
@csrf.exempt
def offline_form(part):
    """The page of a module to keep on the phone for answering without a connection (diary.offline_ok), as the
    participant page draws it, with the version it was drawn from; {"offline": false} for a module that needs the
    server while it is answered. The app sends the filled form later (offline_entry)."""
    from werkzeug.datastructures import MultiDict

    from app import runtime

    survey, person = _person()
    if survey is None:
        return person
    if not _person_may_sync(person):
        return _refuse("limit", 429)
    if diary.module(survey, part) is None:
        return _refuse("unknown", 404)
    if not diary.offline_ok(survey, part) or not _may_answer(survey, person):
        # a person who left, ended, has not finished the entry part, or a study closed: no page to keep (AN-6)
        return _ok(offline=False)
    lang = person.language
    import random

    from app import offline_answers

    # drawn once a day per module: fetching again gives the same page and its receipt (audit 2026-10-15, NB-2)
    seed = offline_answers.layout_seed(person, part, survey.qversion, diary.virtual(person).date().isoformat())
    secret = person.secret_hash
    response = _new_entry(survey, person, part, {"tz": person.tz}, rng=random.Random(seed))  # drawn, never kept (rolled back below)
    try:
        result = runtime.step(survey, response, MultiDict(), lang, "GET")
        html = render_template("participant/page.html", survey=survey, lang=lang, in_app=True, offline=True,
                               **result.context)
        import copy
        drawn = copy.deepcopy(response.layout)
        qversion = response.qversion
    finally:
        db.session.rollback()
    # the rollback ended the transaction and with it the person's lock (_person): take it again before writing, so a
    # pause, an end or a leave set meanwhile is not overwritten, and a secret replaced meanwhile no longer counts
    # (review 2026-10-15, G3)
    from app import diary_access

    person = diary_access.lock_person(person)
    if person is None or not secrets.compare_digest(person.secret_hash or "", secret or ""):
        return _refuse("unauthorised", 401)
    # when the phone last fetched the page: an answer sent later cannot have begun before it (audit 2026-10-11, M1)
    person.settings = {**(person.settings or {}), "offline_at": utcnow().replace(microsecond=0).isoformat()}
    snapshot = SimpleNamespace(part=part, layout=drawn, qversion=qversion, language=lang)
    items = [i for b in survey.blocks if diary.part_of(b) == part for i in b.items]
    version = offline_answers.capture(survey, person, snapshot, items)
    db.session.commit()
    return _ok(offline=True, html=html, version=version)


@bp.route(API + "/offline", methods=["POST"])
@csrf.exempt
def offline_entry():
    """Read an offline entry against its immutable person-scoped page definition and presentation order.
    Compatible valid values become completed answers; invalid, legacy or incompatible values retain their
    originals as excluded review cases. Schedule and time limits still apply, and every refusal leaves the
    Android queue intact. The phone supplies start/end times; received_at records the server's arrival time.
    """
    from app import runtime

    survey, person = _person()
    if survey is None:
        return person
    data = _body()
    if data is None:
        return _refuse("unknown", 400)
    if not _lock_entries(person):
        return _refuse("unauthorised", 401)
    uid = _uuid(data)
    known = _by_uuid(person, uid)
    if known is not None:
        if not _same_identity(known, data):
            return _refuse("unknown", 409)
        if known.status != Response.ACTIVE:
            return _ok(status="stored" if known.status == Response.COMPLETED else known.status)
    if not _person_may_send(person):
        return _refuse("limit", 429)
    if survey.status == Survey.CLOSED:  # data collection ended: no new entries (N7)
        return _refuse("closed", 409)
    received = utcnow()
    part, now = data.get("module"), diary.virtual(person, received)  # the app sends the times in the person's time
    started, completed = _millis(data.get("started")), _millis(data.get("completed"))
    if not started or not completed or completed < started or completed > now + timedelta(seconds=LATE_SECONDS) \
            or now - started > timedelta(days=OFFLINE_DAYS):
        return _refuse("unknown", 400)
    from app import offline_answers

    receipt = offline_answers.page(survey, person, data.get("version"), part)
    # the phone's clock may lag behind the server's by LATE_SECONDS, as in every other time check (audit 2026-10-21, F1)
    if receipt and started < diary.virtual(person, datetime.fromisoformat(receipt["at"])) - timedelta(seconds=LATE_SECONDS):
        return _refuse("expired", 410)
    if receipt is None and not diary.offline_ok(survey, part):
        return _refuse("unknown", 400)
    if receipt is None and data.get("version") != runtime._signature(survey):
        return _refuse("changed", 409)  # the questionnaire changed since the page was kept: the app fetches it anew
    checked = _check_module(survey, person, diary.refresh(survey, person), part, data, started)
    if not isinstance(checked, dict):
        return checked
    due = checked["due"]
    if due is not None:  # begun before the prompt was over (its expiry, or without one the end of its day)
        day = diary.prompt_day(person, due)
        if started > diary.prompt_over(checked["module"], due, day, diary.zone_on(person, day)):
            return _refuse("expired", 410)
    if checked["same"] is not None and checked["same"].status == Response.COMPLETED:
        return _refuse("done", 409)
    if checked["same"] is not None and checked["same"].status == offline_answers.REVIEW:
        # A second queued entry for this prompt must not overwrite the preserved original.
        return _refuse("done", 409)
    if uid is None and due is None and db.session.scalar(db.select(db.exists().where(
            Response.enrolment_id == person.id, diary.part_filter(part), Response.started_at == started,
            Response.completed_at == completed, Response.occasion["offline"].as_boolean().is_(True)))):
        # a self-started entry of an older app without uuid, sent again after a lost reply: no prompt names it, the
        # phone's two times do (audit 2026-10-12, D-6)
        return _ok(status="stored")
    try:
        fields = offline_answers.fields(data)
    except ValueError:
        return _refuse("invalid", 422)
    response = known or checked["same"] or _new_entry(survey, person, part, checked["occasion"], uid=uid)
    if response is None:  # the same prompt or answer at the same moment from another request
        db.session.expire_all()
        known = _by_uuid(person, uid)
        if known is not None:
            return _ok(status="stored" if known.status == Response.COMPLETED else known.status)
        return _refuse("done", 409)
    items = offline_answers.frozen_items(survey, receipt) if receipt else [
        i for b in survey.blocks if diary.part_of(b) == part for i in b.items]
    result = runtime.evaluate(items, fields, receipt["language"] if receipt else person.language)
    compatible = offline_answers.compatible(survey, items)
    reason = "invalid" if result.errors else "changed" if not compatible else "unverified" if receipt is None else None
    refusal = offline_answers.review_refusal(survey, person, data, receipt) if reason else None
    if refusal:  # too large or too many open review cases (audit 2026-10-15, NB-3): nothing stored, the app keeps it
        db.session.rollback()
        return _refuse(refusal, 422 if refusal == "invalid" else 429)
    if reason:
        offline_answers.review(response, data, receipt, reason, result.errors)
    else:
        runtime.store_answers(response, result.answers)
    if receipt:
        response.layout = receipt["layout"]
        response.qversion = receipt["qversion"]
        response.language = receipt["language"]
    response.occasion = {**(response.occasion or {}), "offline": True}
    response.started_at, response.last_active_at = started, completed
    response.completed_at = None if reason else completed
    response.received_at = received
    if uid and not response.uuid:
        response.uuid = uid
    response.status = offline_answers.REVIEW if reason else Response.COMPLETED
    db.session.commit()
    return _ok(status="review" if reason else "stored", errors={str(k): str(v) for k, v in result.errors.items()})


@bp.route(API + "/leave", methods=["POST"])
@csrf.exempt
def leave():
    survey, person = _person()
    if survey is None:
        return person
    if not _person_may_sync(person):
        return _refuse("limit", 429)
    from app import diary_access

    if diary_access.lock_person(person) is None:
        return _refuse("unauthorised", 401)
    diary.leave(person)  # no prompts from now, no new entries (audit 2026-10-11, N7)
    status = person.status
    db.session.commit()
    return _ok(status=status)


@bp.route(API + "/erase", methods=["POST"])
@csrf.exempt
def erase():
    """"Delete my answers" in the app: every entry of the person and the person, as their deletion code does
    (erasure.erase, counted as deleted by the participants themselves). The secret is unknown afterwards. A new
    phone gets the secret of a participation only with the person's personal code after the researchers released it
    (diary.rejoin), so a card seen by someone else no longer leads here (Abgleich 2026-10-15, NB-1)."""
    from app import erasure

    survey, person = _person()
    if survey is None:
        return person
    if not _person_may_sync(person):
        return _refuse("limit", 429)
    entries = diary._entries(person)
    if entries:
        erasure.erase(survey, entries, "self")  # takes the person's row along (diary.whole_persons) and commits
    else:
        from app import diary_access

        if diary_access.lock_person(person) is not None:
            diary_access.revoke(person)
            db.session.delete(person)
        db.session.commit()
    return _ok(status="erased")


def _millis(value):
    """A time the app sends as epoch milliseconds, as naive UTC like the database; None for anything else."""
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc).replace(tzinfo=None, microsecond=0)
    except (OverflowError, OSError, ValueError):
        return None


def _iso(value):
    return value.isoformat() if value else None


def _person_may_send(person):
    """The brake per person on /entry and /offline (limits.allow_diary_person): a phone in a loop, or a script with
    the person's key, cannot keep the server busy (audit 2026-10-11, H2). 429 like the other rate limits; the app
    tries again later."""
    from app import limits

    return limits.allow_diary_person(person.pid)


def _person_may_sync(person):
    """The brake per person on the other requests with the person's key: /study, /offline/<module>, /leave, /erase
    (limits.allow_diary_sync; security audit 2026-10-13, AN-2, TN-11). /offline/<module> draws the whole participant
    page, /study reads all entries of the person; the app asks them now and then, a script in a loop no longer
    keeps the participants' processes busy. 429: the app tries again later."""
    from app import limits

    return limits.allow_diary_sync(person.pid)


def _may_answer(survey, person):
    """Whether the person may still answer modules now, as _check_module asks before an entry (security audit
    2026-10-13, AN-6): active (or finished, for entries sent later), not in a draft unless a test person, and the study
    not closed. A person who left, ended or has not finished the entry part gets no page to keep."""
    status = diary.refresh(survey, person)
    if status not in (Enrolment.ACTIVE, "finished") or survey.status == Survey.CLOSED:
        return False
    return not (survey.status == Survey.DRAFT and person.source != "test")


def _lock_entries(person):
    """Serialise admission, daily counting and insertion until the request commits or rolls back.

    PostgreSQL locks the person's row, including against leave/erase updates. SQLite's harmless
    write takes its database write lock before any entries are read. Refresh the person after waiting;
    another request may have changed their status in the meantime. No schema or new retention data.
    """
    with db.session.no_autoflush:
        if db.engine.dialect.name == "sqlite":
            db.session.execute(db.delete(Enrolment).where(db.false()))
        locked = db.session.scalar(db.select(Enrolment).where(Enrolment.id == person.id)
                                   .with_for_update().execution_options(populate_existing=True))
        return locked is not None


def _today_count(person, part, today, entries=None):
    """The reports this person started themselves (no prompt) of module `part` on their local today and sent off; the
    answered prompts do not count (a module is either timed or started on an event, diary.self_start). A report counts
    only once it is no longer open (completed, or kept for review): opening one and leaving it does not use up the
    limit, so another app that opens the entry screen again and again cannot spend it (Abgleich 2026-10-15, T07-N-1);
    a new start continues the open one instead (_open_report)."""
    if entries is None:
        starts = db.session.scalars(_self_reports(person, part, today, [Response.started_at],
                                                  Response.status != Response.ACTIVE))
        return sum(diary.local_date(person, diary.virtual(person, at)) == today for at in starts)
    n = 0
    for r in entries:
        occ = r.occasion or {}
        if occ.get("part") == part and not occ.get("due") and r.started_at and r.status != Response.ACTIVE and                 diary.local_date(person, diary.virtual(person, r.started_at)) == today:
            n += 1
    return n


def _self_reports(person, part, today, columns, *where):
    """A select of `columns` over this person's self-started reports of `part` begun around their local `today` (and
    `where`).
    Every IANA offset is within 24 hours: narrow in UTC first, the caller keeps the exact zone_at semantics (travel,
    DST and fast test clocks) on these few rows."""
    clock = (person.settings or {}).get("clock")
    lo = datetime.combine(today - timedelta(days=1), datetime.min.time())
    hi = lo + timedelta(days=3)
    if clock:
        t0 = datetime.fromisoformat(clock["t0"])
        factor = int(clock.get("factor") or 1)
        lo, hi = t0 + (lo - t0) / factor, t0 + (hi - t0) / factor
        if factor < 0:
            lo, hi = hi, lo
    query = db.select(*columns).where(
        Response.enrolment_id == person.id,
        Response.started_at >= lo, Response.started_at <= hi,
        db.func.coalesce(Response.occasion["due"].as_string(), "") == "", *where)
    # Separate disjoint branches keep (person, part, started_at) usable as a range
    # index even when a rolling-deploy row has only the JSON part.
    return query.where(Response.part == part).union_all(
        query.where(Response.part.is_(None), Response.occasion["part"].as_string() == part))


def _open_report(person, part, today):
    """The report of `part` this person started themselves today and has not sent off yet, or None: a new start opens
    it again instead of a new empty entry (Abgleich 2026-10-15, T07-N-1)."""
    rows = db.session.execute(_self_reports(person, part, today, [Response.id, Response.started_at],
                                            Response.status == Response.ACTIVE)).all()
    ids = [i for i, at in rows if diary.local_date(person, diary.virtual(person, at)) == today]
    return db.session.get(Response, max(ids)) if ids else None


UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _uuid(data):
    """The app's id of an answer (M5): a UUID text of 36 characters, else None (an older app, or something else)."""
    value = data.get("uuid")
    return value.lower() if isinstance(value, str) and UUID_RE.match(value) else None


def _by_uuid(person, uid):
    return db.session.scalar(db.select(Response).filter_by(enrolment_id=person.id, uuid=uid)) if uid else None


def _same_identity(response, data):
    due = _millis(data.get("due"))
    due = due.replace(second=0) if due is not None else None
    return (response.occasion or {}).get("part") == data.get("module") and response.due_at == due


def _same_prompt(part, due, entries=None, person=None):
    key = due.isoformat()
    if entries is None:
        return db.session.scalar(db.select(Response).where(
            Response.enrolment_id == person.id, diary.part_filter(part),
            db.or_(Response.due_at == due, db.and_(Response.due_at.is_(None),
                                                  Response.occasion["due"].as_string() == key)))
            .order_by(Response.id.desc()).limit(1))
    return next((r for r in entries
                 if (r.occasion or {}).get("part") == part and (r.occasion or {}).get("due") == key), None)


def _new_entry(survey, person, part, occasion=None, entries=None, uid=None, rng=None):
    """A stored participation of one part, laid out with the blocks of that part only (runtime.build_layout).
    entries: optional history for bulk callers; otherwise count the part in SQL. uid: the app's answer id. None
    when a unique key refuses it: another request stored the same prompt (person, part, due) or the same answer
    (uuid) at the same moment (audit 2026-10-11, M2, M5); the request's transaction is rolled back then (no
    savepoint: SQLite's driver would commit an outermost one), and the caller reads what the other one stored."""
    from sqlalchemy.exc import IntegrityError

    from app import runtime

    n = (db.session.scalar(db.select(db.func.count(Response.id)).where(
        Response.enrolment_id == person.id, diary.part_filter(part))) if entries is None else
        sum(1 for r in entries if (r.occasion or {}).get("part") == part)) + 1
    now = utcnow()
    occasion = {"part": part, "n": n, **(occasion or {"tz": person.tz})}
    due = datetime.fromisoformat(occasion["due"]) if occasion.get("due") else None
    response = Response(survey_id=survey.id, language=person.language, answers={}, qversion=survey.qversion,
                        pid=new_pid(survey.code), started_at=now, last_active_at=now, status=Response.ACTIVE,
                        position=0, source=person.source, is_preview=False, enrolment_id=person.id,
                        occasion=occasion, due_at=due, uuid=uid)
    runtime.ensure_layout(survey, response, rng)
    db.session.add(response)
    try:
        db.session.flush()
    except IntegrityError:
        db.session.rollback()
        return None
    return response


def _entry_url(survey, response):
    from app import hosts, secrets_box

    stamp = int(utcnow().replace(tzinfo=timezone.utc).timestamp())
    # AES-SIV is deterministic. Random JSON whitespace supplies 128 nonce bits while preserving
    # the existing two-field payload understood by older participant containers during rolling deploys.
    padding = "".join("\t" if byte & (1 << bit) else " " for byte in secrets.token_bytes(16) for bit in range(8))
    token = secrets_box.seal(PURPOSE, (json.dumps([response.pid, stamp], separators=(",", ":")) + padding).encode(),
                             secrets_box.PARTICIPANT)
    from app import diary_access

    diary_access.issue(survey, response, token)
    return hosts.url_for("diary_api.open_entry", uid=survey.code, token=token, _external=True)


@bp.route("/s/<uid>/e/<token>")
def open_entry(uid, token):
    """The entry address the app opens in its WebView: this browser continues the participation, then on the usual
    pages of /s/<uid> (participate.page). Good for ENTRY_SECONDS in the browser that opened it first; opened again
    later or in another browser, it shows a neutral page."""
    from app import participate, secrets_box

    # not participate._survey: a test person opens a draft, which that page shows only to whoever may see it (below)
    survey = db.session.scalar(db.select(Survey).filter_by(code=(uid or "").lower()))
    if not diary.is_diary(survey):
        abort(404)
    raw = secrets_box.unseal(PURPOSE, token, secrets_box.PARTICIPANT) if len(token) < 400 else None
    try:
        pid, stamp = json.loads(raw) if raw else (None, 0)
    except (ValueError, TypeError):
        pid, stamp = None, 0
    fresh = utcnow().replace(tzinfo=timezone.utc).timestamp() - int(stamp or 0) <= ENTRY_SECONDS
    response = db.session.scalar(db.select(Response).filter_by(pid=pid, survey_id=survey.id)) if pid and fresh else None
    if response is None or response.enrolment_id is None:
        abort(404)
    from app import diary_access

    if not diary_access.open_link(survey, response, token):
        abort(404)
    db.session.commit()  # the link is now this browser's (diary_access.REOPEN_SECONDS, KR-12)
    if response.source == "test":  # a test person may answer a draft, like whoever opened its pretest link
        session[participate.PRETEST_KEY] = list(dict.fromkeys((session.get(participate.PRETEST_KEY) or []) + [survey.id]))
    session.permanent = True
    session[participate._session_key(survey)] = response.id
    session[participate._pid_key(survey)] = response.pid
    for key in (participate._pending_key(survey), participate._declined_key(survey)):
        session.pop(key, None)
    return redirect(url_for("participate.page", uid=uid))


@bp.route("/.well-known/assetlinks.json")
def assetlinks():
    """Digital Asset Links: the app de.askurious.app may open the join links /j/... of this address (an App Link). Empty
    without DIARY_APP_FINGERPRINTS (config.py); the links then open the join page in the browser."""
    prints = current_app.config.get("DIARY_APP_FINGERPRINTS") or []
    statements = [{"relation": ["delegate_permission/common.handle_all_urls"],
                   "target": {"namespace": "android_app", "package_name": "de.askurious.app",
                              "sha256_cert_fingerprints": prints}}] if prints else []
    out = jsonify(statements)
    out.headers["Cache-Control"] = "public, max-age=3600"
    return out


@bp.route("/j/<code>")
def join_page(code):
    """The join link behind the QR code. With the app installed the phone opens the app instead (App Link); without it,
    this page says how to join: install the app, then scan the code again or type the join code. Wrong codes count
    against the address like on /join (CODE_LIMIT); the title shows only while the study takes this person (not of a
    draft or an ended study; audit 2026-10-11, N1)."""
    from app import limits

    survey, test = diary.by_code(code)
    if survey is None:
        if test == "limit" or not limits.allow_code_try(count=False):
            abort(429)
        limits.allow_code_try()
        abort(404)
    lang = participate_lang(survey)
    return render_template("diary/join.html", survey=survey, lang=lang, code=code.strip().lower() if test else survey.code,
                           accepting=diary.accepting(survey, test),
                           store_url=current_app.config["DIARY_APP_URL"])


def participate_lang(survey):
    from app import participate

    return participate._pick_language(survey)
