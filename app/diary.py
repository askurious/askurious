"""Diary studies (Tagebuchstudie; experience sampling, concepts/experiencesampling.md): the fourth kind of survey,
experimental ("diary", app/experimental.py) until the Android app is in the store (Ralf, 2026-10-11).

A diary study is a questionnaire with a schedule around it. A person joins in the Android app (QR code or join code,
app/diary_api.py), answers the entry part once (consent, demographics), and then the modules again and again: at
fixed times of the day, or whenever they want to report something. Each answered part is a participation of its own
(a Response with enrolment_id and occasion), grouped by the person's Enrolment; the person is the participation of
this kind on the pages for researchers. The plain participant link /s/<code> of a diary study leads nowhere: every
participation comes from the app (participate._survey).

Where things live:
- survey.sampling: the modules with their triggers and the study period (defaults()); None for every other survey.
  "earlier": the fixed times as they were before a change in the field, each with the moment it ended ("until"), so
  that answered and missed prompts are counted against the schedule that was valid then.
- Block.settings["part"]: the part a block belongs to: "entry" (also when missing) or the key of a module. A
  participation shows only the blocks of its part (runtime.build_layout, `only`); branches, randomisers and loops
  work inside the part as in a questionnaire. Ends, quotas, web services and external sections are left out of a
  diary entry (phase 1).
- The schedule is computed on the phone, in its local time (askurious-android, Schedule.kt); prompts() here gives
  the same times for the export and the counts of answered prompts.
- Test persons: the test join code (test_code) lets the researchers join before the start and while the study runs;
  the person and all their entries are source "test", like a test run of a questionnaire.

Once a study has real answers or runs, its structure is fixed (locked): which block belongs to which part, the
modules themselves, their triggers, the days they run and the study period. Times, weekdays, expiry and the texts may
still change; an earlier set of times is kept in "earlier".

This module also holds the researchers' pages of the kind: creating one, and the tab "Zeitplan" (blocks to parts,
modules, study period, joining with QR code, the persons and their answered prompts).
"""
import copy
import functools
import hashlib
import hmac
import math
import re
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from markupsafe import Markup

from app import autosave
from app.extensions import db
from app.lang import de_en, english
from app.models import CODE_ALPHABET, Block, Enrolment, Response, Survey, utcnow
from app.timeutil import fmt_local
from app.timeutil import local_today as german_today

bp = Blueprint("diary", __name__, url_prefix="/diary")

ENTRY = "entry"
TRIGGERS = ("fixed", "random", "self")
SCHEDULED = ("fixed", "random")  # triggers with prompts the schedule knows (the phone notifies, prompts() counts)
REVIEW = "review"  # the status of an offline answer kept as a review case (offline_answers.REVIEW)
MAX_PER_DAY = 20  # random prompts of one module a day
WEEKDAYS = ("1", "2", "3", "4", "5", "6", "7")
MAX_MODULES = 12
MAX_TIMES = 12  # fixed times of one module a day
MAX_PERIOD = 365
SUMMARY_MAX = 160  # characters of the sentence about the study in the app (summary_of)
MAX_EARLIER = 50  # earlier sets of times kept for the counts
MAX_PERSON_HISTORY = 1000  # entries of a person's quiet hours or time zones: at most one per day, so this only ends a
# study of years; every earlier entry still holds for its days (audit 2026-10-17, T2)
MERGE_SECONDS = 10 * 60  # a set of times replaced within this time was an intermediate state (autosave), not kept
PENDING_DAYS = 7  # a person who joined but never finished the entry part goes after this many days (sweep)
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
MODULE_KEY = re.compile(r"^m\d{1,3}$")
# characters after the hyphen of a test join code: ten of 31 (49 bits), only the server makes them; five (25 bits) were
# found within an hour from the senders of one IPv6 /48 (audit 2026-10-18, BIZ-07). The codes of before lead nowhere:
# the tab shows the new one (the app takes any length)
TEST_SUFFIX = 10
FAST_SUFFIX = 11  # ... of a test join code with fast forward
INVITE_SUFFIX = 8  # ... of a personal invitation code (rows of diary_invite, printed on cards: they stay as they are)
TEST_CODE = re.compile(r"^([a-z0-9]{1,12})-([a-z0-9]{%d,%d})$" % (INVITE_SUFFIX, FAST_SUFFIX))
FAST_FACTOR = 144  # fast forward: a study day passes in ten minutes
EXIT = "exit"  # the part of the exit questionnaire, once after the study period
EXIT_DAYS = 14  # it can be answered this many days after the last day
MAX_INVITES = 1000

# the sources of the advice on the tab "Zeitplan" (APA 7, app/apa.py)
VAN_BERKEL = ("van Berkel, N., Ferreira, D., & Kostakos, V. (2017). The experience sampling method on mobile devices. "
              "ACM Computing Surveys, 50(6), Article 93. https://doi.org/10.1145/3123988")
WRZUS = ("Wrzus, C., & Neubauer, A. B. (2023). Ecological momentary assessment: A meta-analysis on designs, samples, and "
         "compliance across research fields. Assessment, 30(3), 825–846. https://doi.org/10.1177/10731911211067538")


def defaults(lang):
    """The schedule of a new diary study: one module three times a day for 14 days, from the day of joining on (the
    first prompt can come right after the entry survey; Philipp, 2026-10-13); one shared code, and personal codes,
    once there are, connect a new phone again only after the researchers released them (rejoin_mode; Ralf,
    2026-10-15)."""
    return {"modules": [{"key": "m1", "name": {lang: "Moment"},
                         "trigger": {"t": "fixed", "times": ["09:00", "14:00", "19:00"], "weekdays": [1, 2, 3, 4, 5, 6, 7]},
                         "expires": 60, "reminder": 0, "days": [1, None], "notify": {}}],
            "period_days": 14, "start": "same_day", "rejoin": "release"}


# the card "Aus einer Vorlage" of a new diary study (Ralf, 2026-10-12): the three designs of Bolger & Laurenceau (2013,
# BOLGER) and the daily diary; each with the entry survey (age, gender), the short survey "Moment" and the exit survey.
# key: ((name, note) German, (name, note) English, the module's trigger and answer time in minutes, days of the study)
DIARY_TEMPLATES = {
    "fest": (("Feste Zeiten", "dreimal am Tag um 9, 14 und 19 Uhr, 14 Tage (intervallgesteuert)"),
             ("Fixed times", "three times a day at 9, 14 and 19 o'clock, 14 days (interval-contingent)"),
             {"t": "fixed", "times": ["09:00", "14:00", "19:00"], "weekdays": [1, 2, 3, 4, 5, 6, 7]}, 60, 14),
    "signal": (("Zufällige Signale", "fünfmal am Tag zu zufälligen Zeiten zwischen 9 und 21 Uhr, 7 Tage (signalgesteuert, Experience Sampling)"),
               ("Random signals", "five times a day at random times between 9 and 21 o'clock, 7 days (signal-contingent, experience sampling)"),
               {"t": "random", "per_day": 5, "windows": [["09:00", "21:00"]], "min_gap": 60, "weekdays": [1, 2, 3, 4, 5, 6, 7]}, 30, 7),
    "ereignis": (("Ereignis melden", "die Person meldet sich, wenn das Ereignis eintritt, höchstens fünfmal am Tag, 14 Tage (ereignisgesteuert)"),
                 ("Report an event", "the person reports whenever the event happens, at most five times a day, 14 days (event-contingent)"),
                 {"t": "self", "label": {}, "max_per_day": 5}, 0, 14),
    "abend": (("Abends einmal", "jeden Abend um 20 Uhr ein Rückblick auf den Tag, 14 Tage (Tagebuch am Tagesende)"),
              ("Once in the evening", "every evening at 20 o'clock a look back on the day, 14 days (end-of-day diary)"),
              {"t": "fixed", "times": ["20:00"], "weekdays": [1, 2, 3, 4, 5, 6, 7]}, 180, 14),
}


def diary_templates():
    """{key: (name, note)} of DIARY_TEMPLATES in the language of the page."""
    from app.lang import english

    return {k: spec[1 if english() else 0] for k, spec in DIARY_TEMPLATES.items()}


def from_template(key, lang):
    """The schedule of a new diary study made from DIARY_TEMPLATES[key]."""
    _, _, trigger, expires, days = DIARY_TEMPLATES[key]
    out = defaults(lang)
    module = {**out["modules"][0], "trigger": copy.deepcopy(trigger), "expires": expires}
    if trigger["t"] == "self":
        module["trigger"]["label"] = {lang: "Etwas melden" if lang.startswith("de") else "Report something"}
    return {**out, "modules": [module], "period_days": days}


def is_diary(survey):
    return survey is not None and survey.sampling is not None


def modules(survey):
    return list((survey.sampling or {}).get("modules") or [])


def module(survey, key):
    return next((m for m in modules(survey) if m.get("key") == key), None)


def self_start(m):
    """What lets the participants start module `m` themselves (event-contingent, a button in the app):
    {"label", "max_per_day"} of a module whose trigger is "self"; None for a timed module. A module is either timed
    or started on an event, never both; a study can have modules of both kinds (Philipp, 2026-10-15; before, a timed
    module could also carry a button, m["self"], which is no longer read)."""
    t = m.get("trigger") or {}
    return t if t.get("t") == "self" else None


def open_end(survey):
    """The study has no last day: each person takes part until the researchers end it for them (end), they leave, or
    data collection ends (stop_of)."""
    return bool((survey.sampling or {}).get("open_end"))


def text_of(value, lang, survey):
    """A text kept per language ({lang: text}): the language asked for, else the default language, else any."""
    if isinstance(value, str):
        return value
    value = value or {}
    return value.get(lang) or value.get(survey.default_language) or next(iter(value.values()), "")


def part_of(block):
    return (block.settings or {}).get("part") or ENTRY


def blocks_of(survey, part):
    """The ids of the blocks of a part ("entry" or a module key)."""
    return {b.id for b in survey.blocks if part_of(b) == part}


def only_blocks(survey, response):
    """For runtime.build_layout: the blocks this participation may show, or None for a participation of any other
    kind. A diary entry whose part has no blocks gets an empty set (it ends at once)."""
    if not is_diary(survey) or not getattr(response, "occasion", None):
        return None
    return blocks_of(survey, response.occasion.get("part") or ENTRY)


def short_entry(response):
    """Whether a participation is an entry of a short survey (a module) of a diary study: it shows no progress bar
    and ends with a short thanks instead of the end page of a questionnaire (Ralf, 2026-10-11)."""
    if response is None or not getattr(response, "enrolment_id", None):
        return False
    return ((response.occasion or {}).get("part") or ENTRY) not in (ENTRY, EXIT)


def entry_survey(response):
    """Whether a participation is the entry survey of a person in a diary study (its end says the study begins)."""
    if response is None or not getattr(response, "enrolment_id", None):
        return False
    return ((response.occasion or {}).get("part") or ENTRY) == ENTRY


def locked(survey):
    """Whether the structure of the study is fixed: it runs (or ran), or real persons answered (module text)."""
    return not survey.is_editable or survey.has_real_responses()


# --- joining ----------------------------------------------------------------------------------------------------

def test_code(survey, fast=False):
    """The join code for test persons: the survey's code, a hyphen and TEST_SUFFIX characters only the server can make
    (HMAC with the secret key), so that nobody guesses it from the public code. fast: the code of test persons with
    fast forward (FAST_SUFFIX characters; a study day in ten minutes). With a salt in the schedule ("test_salt", set by
    "Testcodes neu erzeugen") both codes are new and the earlier ones lead nowhere (audit 2026-10-11, N2)."""
    key = current_app.secret_key.encode() if isinstance(current_app.secret_key, str) else current_app.secret_key
    salt = (survey.sampling or {}).get("test_salt")
    message = f"diary-{'fast' if fast else 'test'}:{survey.code}" + (f":{salt}" if salt else "")
    digest = hmac.new(key, message.encode(), hashlib.sha256).digest()
    return survey.code + "-" + "".join(CODE_ALPHABET[b % len(CODE_ALPHABET)]
                                       for b in digest[:FAST_SUFFIX if fast else TEST_SUFFIX])


def invites(survey):
    """The personal invitation codes of a study, in the order they were made (table diary_invite; audit 2026-10-11,
    N4): [{"c": code, "used": bool, "pid": the person who joined with it or None, "status": theirs, "released":
    bool (a release still valid), "until": its end (naive UTC) or None, "rejoined": when a phone last connected
    again (naive UTC) or None}]."""
    from app.models import DiaryInvite

    rows = db.session.execute(
        db.select(DiaryInvite.code, DiaryInvite.used_at, DiaryInvite.released_at, Enrolment.pid, Enrolment.status,
                  Enrolment.settings)
        .outerjoin(Enrolment, db.and_(Enrolment.id == DiaryInvite.enrolment_id, Enrolment.survey_id == survey.id,
                                      Enrolment.created_at <= DiaryInvite.used_at))  # never a later person (S-01)
        .where(DiaryInvite.survey_id == survey.id).order_by(DiaryInvite.id))
    out = []
    now = utcnow()
    for c, used, released, pid, status, settings in rows:
        until = released + RELEASE_TIME if released is not None else None
        live = until is not None and until > now
        out.append({"c": c, "used": used is not None, "released": live, "until": until if live else None, "pid": pid,
                    "status": status, "rejoined": _when((settings or {}).get("rejoined_at"))})
    return out


def invite_only(survey):
    """Whether only personal codes let people join ("Persönliche Codes"); otherwise the shared code does too."""
    return bool((survey.sampling or {}).get("invite_only"))


def _when(text):
    try:
        return datetime.fromisoformat(text) if isinstance(text, str) else None
    except ValueError:
        return None


REJOIN = ("release",)
# A release lets the code connect once within this time (Ralf, 2026-10-15, Abgleich NB-1, decision 2a).
RELEASE_TIME = timedelta(hours=2)


def rejoin_mode(survey):
    """How a used personal code connects a phone to its participation again (diary.rejoin): "release", once each
    time the researchers released it, within RELEASE_TIME. The mode "auto" (at any time, Philipp, 2026-10-13) let
    whoever saw a printed card take a participation over and delete it (Abgleich 2026-10-15, NB-1); studies that
    still carry it in their settings connect again only after a release too (Ralf, 2026-10-15)."""
    return _rejoin_of(survey.sampling)


def _rejoin_of(sampling):
    return "release"


def by_code(text):
    """(survey, kind) of a join code as typed or scanned: kind None for the code of a diary study, "test" or "fast"
    for its test codes, "invite" for an unused personal invitation code, "rejoin" for a used one that may connect
    its person again now, "used" for a used one that may not; (None, "limit") while the study had too many wrong codes
    of this kind (limits.allow_study_code_try; audit 2026-10-18, BIZ-07); (None, None) for anything else."""
    text = "".join((text or "").split()).lower()
    m = TEST_CODE.match(text)
    code = m.group(1) if m else text
    if not code or len(code) > 12:
        return None, None
    survey = db.session.scalar(db.select(Survey).filter_by(code=code))
    if not is_diary(survey):
        return None, None
    if not m:
        return survey, None
    from app import limits

    # a personal invitation code that exists gets in also while the study had too many wrong codes: one sender's wrong
    # codes locked every participant of a study "only with an invitation" out (audit 2026-10-22, A-02); guessing one
    # stays held per sender, /64 and /48 (limits.allow_code_try) and by the wrong codes counted for the study
    invite = _invite(survey, m.group(2)) if len(m.group(2)) == INVITE_SUFFIX else None
    if invite is None:
        if not limits.allow_study_code_try(survey.id, count=False):
            return None, "limit"
        if hmac.compare_digest(test_code(survey), text):
            return survey, "test"
        if hmac.compare_digest(test_code(survey, fast=True), text):
            return survey, "fast"
        limits.allow_study_code_try(survey.id)  # one more wrong code for this study, whoever typed it
        return None, None
    if invite.used_at is None:
        return survey, "invite"
    return survey, "rejoin" if _rejoin_person(survey, invite) is not None else "used"


def _invite(survey, code):
    from app.models import DiaryInvite

    return db.session.scalar(db.select(DiaryInvite).filter_by(survey_id=survey.id, code=code))


def _rejoin_person(survey, invite):
    """The person a used code may connect again now, or None: someone joined with it (codes used before
    2026-10-13 know nobody), the person is still there and has not left in the app, and the study lets the code
    connect now (rejoin_mode: a release within RELEASE_TIME)."""
    if invite.enrolment_id is None or invite.released_at is None or invite.released_at + RELEASE_TIME <= utcnow():
        return None
    person = db.session.get(Enrolment, invite.enrolment_id)
    if person is None or person.survey_id != survey.id or person.status == Enrolment.LEFT:
        return None
    if invite.used_at is None or (person.created_at is not None and person.created_at > invite.used_at):
        # someone who joined after the code was used cannot be the person who used it: a reference left from a
        # deletion before the code let go of its person (audit 2026-10-23, S-01)
        return None
    return person


def use_invite(survey, text, person):
    """Mark the invitation code `text` as used by `person` (flushed, it needs its id): each joins one person. One
    UPDATE that only finds the code while it is unused, so of two joins at the same moment only one wins (False for
    the other); the schedule in survey.sampling is not written (audit 2026-10-11, N4)."""
    from app.models import DiaryInvite

    suffix = "".join((text or "").split()).lower().rpartition("-")[2]
    done = db.session.execute(db.update(DiaryInvite).where(
        DiaryInvite.survey_id == survey.id, DiaryInvite.code == suffix, DiaryInvite.used_at.is_(None))
        .values(used_at=utcnow(), enrolment_id=person.id).execution_options(synchronize_session=False))
    return done.rowcount == 1


def rejoin(survey, text):
    """The person whose personal code `text` connects a phone again now, or None. A release is used up by this: one
    UPDATE that only finds the code while its release holds, so of two phones at the same moment only one wins (the
    caller gives the person a new secret; the earlier phone's no longer works)."""
    from app.models import DiaryInvite

    suffix = "".join((text or "").split()).lower().rpartition("-")[2]
    invite = _invite(survey, suffix)
    person = _rejoin_person(survey, invite) if invite is not None and invite.used_at is not None else None
    if person is None:
        return None
    done = db.session.execute(db.update(DiaryInvite).where(
        DiaryInvite.id == invite.id, DiaryInvite.released_at > utcnow() - RELEASE_TIME)
        .values(released_at=None).execution_options(synchronize_session=False))
    return person if done.rowcount == 1 else None


def release_invite(survey, code, on=True):
    """Let the used code `code` connect its person's participation once more within RELEASE_TIME (on), or take that
    back. False for a code nobody joined with."""
    from app.models import DiaryInvite

    done = db.session.execute(db.update(DiaryInvite).where(
        DiaryInvite.survey_id == survey.id, DiaryInvite.code == code, DiaryInvite.enrolment_id.is_not(None))
        .values(released_at=utcnow() if on else None).execution_options(synchronize_session=False))
    return done.rowcount == 1


def make_invites(survey, n):
    """`n` new personal invitation codes (eight characters from CODE_ALPHABET, unique in the study)."""
    import secrets as random_codes

    from app.models import DiaryInvite

    have = {i.get("c") for i in invites(survey)}
    new = []
    while len(new) < n and len(have) < MAX_INVITES:
        c = "".join(random_codes.choice(CODE_ALPHABET) for _ in range(INVITE_SUFFIX))
        if c not in have:
            have.add(c)
            new.append({"c": c, "used": False})
    db.session.add_all([DiaryInvite(survey_id=survey.id, code=i["c"], created_at=utcnow()) for i in new])
    return new


def accepting(survey, kind=None):
    """Whether a new person may join: a running study before its end; test persons also in a draft; with
    "nur mit Einladung" only with a personal code."""
    if kind in ("test", "fast", True):
        return survey.status in (Survey.DRAFT, Survey.RUNNING)
    if kind == "rejoin":  # a person already in: while data collection runs, also after its end date for joining
        return survey.status == Survey.RUNNING
    if invite_only(survey) and kind != "invite":
        return False
    return survey.status == Survey.RUNNING and not (survey.field_end and survey.field_end <= utcnow())


# --- the schedule of one person ---------------------------------------------------------------------------------

def zone(name):
    try:
        return ZoneInfo(name or "Europe/Berlin")
    except Exception:  # an unknown name sent by a phone
        return ZoneInfo("Europe/Berlin")


def virtual(enrolment, real=None):
    """The time of a person (naive UTC): the real one, or for a test person with fast forward (settings["clock"])
    t0 + (real - t0) * factor, so that a study day passes in ten minutes. The app counts the same way."""
    real = real or utcnow()
    clock = (getattr(enrolment, "settings", None) or {}).get("clock")
    if not clock:
        return real
    t0 = datetime.fromisoformat(clock["t0"])
    return t0 + (real - t0) * int(clock.get("factor") or 1)


def tz_history(enrolment):
    """[{"from": "YYYY-MM-DD" or None, "tz": name}] in order: the time zones of the person's phone, each valid from
    the day after the phone reported it (set_tz); empty: always enrolment.tz (decision 2a of the audit 2026-10-11)."""
    return [h for h in (getattr(enrolment, "settings", None) or {}).get("tz") or [] if isinstance(h, dict) and h.get("tz")]


def zone_on(enrolment, day, history=None):
    """The time zone of the person on the local date `day`: the one valid that day (M3: a change of the zone does
    not rewrite the days before it)."""
    history = tz_history(enrolment) if history is None else history
    if not history:
        return zone(enrolment.tz)
    d = day.isoformat()
    valid = [h for h in history if (h.get("from") or "") <= d]
    return zone((valid or history)[-1]["tz"])


def zone_at(enrolment, at):
    """The time zone of the person at the moment `at` (naive UTC, in the person's time): a new zone counts from the
    start of its first day in that zone."""
    history = tz_history(enrolment)
    if not history:
        return zone(enrolment.tz)
    name = history[0]["tz"]
    for h in history[1:]:
        try:
            start = datetime.combine(date.fromisoformat(h["from"]), time(0), zone(h["tz"]))
        except (TypeError, ValueError):
            continue
        if at >= start.astimezone(timezone.utc).replace(tzinfo=None):
            name = h["tz"]
    return zone(name)


def set_tz(enrolment, name, now=None):
    """The time zone the phone reports (a valid name). It applies from the person's next day: the days so far keep
    the zone they had, so answered prompts stay answered and none is counted twice (audit 2026-10-11, M3, decision
    2a). Before the study period (entry part not completed) it simply replaces the zone."""
    history = tz_history(enrolment)
    if name == (history[-1]["tz"] if history else enrolment.tz):
        return
    if not enrolment.first_day:
        enrolment.tz = name
        return
    start = (local_today(enrolment, now) + timedelta(days=1)).isoformat()
    history = history or [{"from": None, "tz": enrolment.tz}]
    history = [h for h in history if h.get("from") != start] + [{"from": start, "tz": name}]
    tidy = []
    for h in history:  # a zone right after the same zone says nothing new
        if not tidy or tidy[-1]["tz"] != h["tz"]:
            tidy.append(h)
    if len(tidy) > MAX_PERSON_HISTORY:  # one entry per day; the days so far keep their zone (audit 2026-10-17, T2)
        tidy = tidy[:1] + tidy[-(MAX_PERSON_HISTORY - 1):]
    enrolment.settings = {**(getattr(enrolment, "settings", None) or {}), "tz": tidy}
    enrolment.tz = name


def local_date(enrolment, at):
    """The local date of the person at `at` (naive UTC, already in the person's time), in the zone of that moment."""
    return at.replace(tzinfo=timezone.utc).astimezone(zone_at(enrolment, at)).date()


def local_today(enrolment, now=None):
    """The person's local today, in their time (virtual)."""
    return local_date(enrolment, virtual(enrolment, now or utcnow()))


def entry_part(enrolment, entries=None):
    """The participation of the entry part of this person (the newest, if there were several tries), or None."""
    if entries is None:
        return part_entry(enrolment, ENTRY)
    return next((r for r in entries
                 if (r.occasion or {}).get("part") == ENTRY), None)


def part_filter(part):
    """Use the indexed part column, with the rolling-deploy fallback for older writers."""
    return db.or_(Response.part == part,
                  db.and_(Response.part.is_(None), Response.occasion["part"].as_string() == part))


def part_entry(enrolment, part, status=None):
    """One newest entry of a part; never materialise the person's full history."""
    query = db.select(Response).where(Response.enrolment_id == enrolment.id, part_filter(part))
    if status is not None:
        query = query.where(Response.status == status)
    return db.session.scalar(query.order_by(Response.id.desc()).limit(1))


# what the schedule and the app's requests read of an entry (not the answers, the layout, the paradata ...)
LIGHT = ("id", "survey_id", "pid", "status", "source", "is_preview", "occasion", "part", "enrolment_id", "started_at",
         "completed_at", "version")


def _light(*more):
    from sqlalchemy.orm import load_only

    return load_only(*(getattr(Response, c) for c in LIGHT + more))


def _entries(enrolment, light=False):
    """The entries of a person, newest first. light: without the answers and the layout (loaded when read; the
    requests of the app read them once, audit 2026-10-11, C8)."""
    query = db.select(Response).where(Response.enrolment_id == enrolment.id)
    if light:
        query = query.options(_light())
    return db.session.scalars(query.order_by(Response.id.desc())).all()


def _entries_by_person(survey, with_answers=False, persons=None):
    """{enrolment id: entries, newest first} of all persons of a study in one query, with the columns the schedule
    reads (audit 2026-10-11, M10, N13); with_answers: also the answers (person_quality compares them). persons: only
    the entries of these enrolment ids (a page of the list "Teilnehmende")."""
    by_person = {}
    which = Response.enrolment_id.is_not(None) if persons is None else Response.enrolment_id.in_(list(persons))
    for r in db.session.scalars(db.select(Response).options(_light(*(("answers",) if with_answers else ())))
                                .where(Response.survey_id == survey.id, which)
                                .order_by(Response.id.desc())):
        by_person.setdefault(r.enrolment_id, []).append(r)
    return by_person


def refresh(survey, enrolment, now=None, entries=None):
    """Brings the stored status up to date: a person whose entry part is completed is active from then on, with the
    study period counted from that day in their time zone. Returns the status as the app sees it, with "finished"
    once the last day is over."""
    if enrolment.status == Enrolment.PENDING:
        first = entry_part(enrolment, entries)
        if first is not None and first.status == Response.COMPLETED:
            done = local_date(enrolment, virtual(enrolment, first.completed_at or utcnow()))
            start = done + timedelta(days=1) if (survey.sampling or {}).get("start") == "next_day" else done
            enrolment.first_day = start
            # the moment the entry part was completed (real time, as "resumed"): prompts of the first day due before
            # it never reached the person and are not counted (review 2026-10-15, G1)
            enrolment.settings = {**(getattr(enrolment, "settings", None) or {}),
                                  "entered_at": (first.completed_at or utcnow()).replace(microsecond=0).isoformat()}
            enrolment.last_day = None if open_end(survey) else \
                start + timedelta(days=max(1, int((survey.sampling or {}).get("period_days") or 1)) - 1)
            enrolment.status = Enrolment.ACTIVE
    if enrolment.status == Enrolment.ACTIVE and enrolment.last_day and local_today(enrolment, now) > enrolment.last_day:
        return "finished"
    return enrolment.status


# --- researchers steer one person: pause, end -------------------------------------------------------------------

def pauses(enrolment):
    """[[from, to or None]] of the person's pauses (local dates, ISO): no prompts on these days."""
    return [p for p in (getattr(enrolment, "settings", None) or {}).get("pauses") or [] if isinstance(p, list) and len(p) == 2]


def paused_on(history, day):
    d = day.isoformat()
    return any(a <= d and (b is None or d <= b) for a, b in history)


def pause(enrolment):
    """Pause an active person from today: no prompts until resumed."""
    if not _lock_web_change(enrolment):
        return False
    if enrolment.status != Enrolment.ACTIVE:
        return False
    enrolment.status = Enrolment.PAUSED
    enrolment.settings = {**(enrolment.settings or {}), "pauses": pauses(enrolment) + [[local_today(enrolment).isoformat(), None]]}
    _revoke_web(enrolment)
    return True


def resume(enrolment, now=None):
    """Go on: the pause (by days, as the app reads it) ends yesterday, and on today it lasts until this moment
    ("resumed"): prompts of today before it are not due, those after it count again. A pause and a resume on the same
    day so cover the day up to the resume (decision 10 of the audit 2026-10-12)."""
    if not _lock_web_change(enrolment):
        return False
    if enrolment.status != Enrolment.PAUSED:
        return False
    history = pauses(enrolment)
    if history and history[-1][1] is None:
        end = (local_today(enrolment, now) - timedelta(days=1)).isoformat()
        history = history[:-1] + ([[history[-1][0], end]] if end >= history[-1][0] else [])
    enrolment.status = Enrolment.ACTIVE
    moment = (now or utcnow()).replace(microsecond=0).isoformat()
    enrolment.settings = {**(enrolment.settings or {}), "pauses": history,
                          "resumed": (resumes(enrolment) + [moment])[-50:]}
    _revoke_web(enrolment)
    return True


def resumes(enrolment):
    """The moments (ISO, naive UTC, real time) at which the person's pauses ended (resume)."""
    return [x for x in (getattr(enrolment, "settings", None) or {}).get("resumed") or [] if isinstance(x, str)]


def _resumed_days(enrolment):
    """{local date: moment of the resume in the person's time (naive UTC)}: on such a day the prompts before the
    moment were still paused (resume)."""
    out = {}
    for x in resumes(enrolment):
        try:
            at = virtual(enrolment, datetime.fromisoformat(x))
        except (TypeError, ValueError):
            continue
        d = local_date(enrolment, at)
        out[d] = max(out.get(d, at), at)
    return out


def _entered(enrolment):
    """The moment the person completed the entry part, in their time (naive UTC), or None for a person who joined
    before it was kept (review 2026-10-15, G1: those keep the earlier rule, every prompt of the first day)."""
    x = (getattr(enrolment, "settings", None) or {}).get("entered_at")
    try:
        return virtual(enrolment, datetime.fromisoformat(x)) if isinstance(x, str) else None
    except ValueError:
        return None


def _lock_web_change(enrolment):
    if isinstance(enrolment, Enrolment) and enrolment.id is not None:
        from app import diary_access

        db.session.flush()
        return diary_access.lock_person(enrolment) is not None
    return True


def _revoke_web(enrolment):
    if isinstance(enrolment, Enrolment) and enrolment.id is not None:
        from app import diary_access

        diary_access.revoke(enrolment)


def leave(enrolment, now=None):
    """The person leaves the study in the app: no more prompts from now (prompts stop at "left_at") and no new
    entries; the answers stay unless the person deletes them (audit 2026-10-11, N7)."""
    if not _lock_web_change(enrolment):
        return False
    if enrolment.status not in (Enrolment.PENDING, Enrolment.ACTIVE, Enrolment.PAUSED, Enrolment.ENDED):
        return False
    enrolment.status = Enrolment.LEFT
    enrolment.settings = {**(getattr(enrolment, "settings", None) or {}),
                          "left_at": (now or utcnow()).replace(microsecond=0).isoformat()}
    _revoke_web(enrolment)
    return True


def end(enrolment, now=None):
    """End the study for this person now: no more prompts from this moment ("ended_at", as "left_at" when the person
    leaves; audit 2026-10-12, D-7); the period ends today (the exit part may follow)."""
    if not _lock_web_change(enrolment):
        return False
    if enrolment.status not in (Enrolment.ACTIVE, Enrolment.PAUSED, Enrolment.PENDING):
        return False
    now = now or utcnow()
    if enrolment.status == Enrolment.PAUSED:
        resume(enrolment, now)
    today = local_today(enrolment, now)
    # an open-ended study (open_end) gets its last day here, so that the exit part opens
    if enrolment.first_day and (not enrolment.last_day or enrolment.last_day > today):
        enrolment.last_day = max(today, enrolment.first_day)
    elif enrolment.last_day and enrolment.last_day > today:
        enrolment.last_day = today
    enrolment.status = Enrolment.ENDED
    enrolment.settings = {**(getattr(enrolment, "settings", None) or {}),
                          "ended_at": now.replace(microsecond=0).isoformat()}
    _revoke_web(enrolment)
    return True


# --- the exit part ----------------------------------------------------------------------------------------------

def exit_info(survey, enrolment, status, lang, entries=None):
    """The exit questionnaire as the app sees it: {"name", "open", "until"}, or None without exit blocks. Open once
    the person's period is over (finished, or ended by the researchers) until EXIT_DAYS later, while not answered."""
    if not blocks_of(survey, EXIT) or not enrolment.last_day:
        return None
    setting = (survey.sampling or {}).get("exit") or {}
    until = enrolment.last_day + timedelta(days=int(setting.get("days") or EXIT_DAYS))
    if entries is None:
        done = db.session.scalar(db.select(db.exists().where(
            Response.enrolment_id == enrolment.id, part_filter(EXIT), Response.status == Response.COMPLETED)))
    else:
        done = any((r.occasion or {}).get("part") == EXIT and r.status == Response.COMPLETED for r in entries)
    open_ = status in ("finished", Enrolment.ENDED) and not done and local_today(enrolment) <= until
    return {"name": text_of(setting.get("name"), lang, survey) if setting.get("name") else "", "open": open_,
            "until": until.isoformat()}


def day_index(enrolment, d):
    return (d - enrolment.first_day).days + 1 if enrolment.first_day else None


def _days_range(m):
    days = m.get("days") or [1, None]
    return int(days[0] or 1), int(days[1]) if len(days) > 1 and days[1] else None


def active_on(m, day):
    first, last = _days_range(m)
    return day is not None and day >= first and (last is None or day <= last)


def _versions(survey):
    """[(modules, valid from, valid until)] as naive UTC datetimes or None: the earlier sets of fixed times, then the
    current one."""
    out, start = [], None
    for old in (survey.sampling or {}).get("earlier") or []:
        until = datetime.fromisoformat(old["until"])
        out.append((old.get("modules") or [], start, until))
        start = until
    out.append((modules(survey), start, None))
    return out


def _fixed(mods, enrolment, zones, until, lo=None, hi=None):
    """The scheduled prompts (fixed and random modules) of these module definitions, on the person's days from `lo`
    to `hi` (local dates, both included; None: the first or last day): [(key, due, local date)]. The day number
    stays counted from the person's first day; zones(d): the person's time zone on day d."""
    out = []
    if not any((m.get("trigger") or {}).get("t") in SCHEDULED for m in mods):
        return out
    d = max(enrolment.first_day, lo) if lo else enrolment.first_day
    # an open-ended person has no last day: _schedule always gives `hi` then
    last = min(x for x in (enrolment.last_day, hi) if x)
    seed, history, stops = None, quiet_history(enrolment), pauses(enrolment)
    while d <= last:
        day = day_index(enrolment, d)
        if paused_on(stops, d):
            d += timedelta(days=1)
            continue
        tz = None
        for m in mods:
            t = m.get("trigger") or {}
            if t.get("t") not in SCHEDULED or not active_on(m, day) or d.isoweekday() not in (t.get("weekdays") or range(1, 8)):
                continue
            if t.get("t") == "random":
                seed = seed_of(enrolment) if seed is None else seed
                times = random_times(seed, m["key"], d, t, quiet_on(history, d))
            else:
                times = t.get("times") or []
            tz = tz or zones(d)
            for hhmm in times:
                h, mi = (int(x) for x in hhmm.split(":"))
                due = datetime.combine(d, time(h, mi), tz).astimezone(timezone.utc).replace(tzinfo=None)
                if until is None or due <= until:
                    out.append((m["key"], due, d))
        d += timedelta(days=1)
    return out


def prompts(survey, enrolment, until=None, around=None):
    """The prompts of the fixed modules for this person from the first day to `until` (UTC, naive) or the last day:
    [(module key, due as naive UTC datetime)], in order. The same rule as the app's Schedule.kt; a prompt counts by
    the set of times that was valid when it was due (survey.sampling["earlier"]). around: a moment (naive UTC);
    then only the prompts of the days around it (is_prompt).

    Each set of times is walked only over the days it was valid, with a day of margin for the time zone, and not past
    `until`: an earlier set used to walk the whole period of the person, so that every set the autosave of the tab
    "Zeitplan" kept multiplied the work of every request of the app (audit 2026-10-11, H2). The result is the same
    (tests/diary/test_diary_schedule.py compares it with the former rule).

    Each day counts in the time zone the person had that day (zone_on, M3). No prompt is due after the person left
    the study in the app or after data collection was ended (N7)."""
    return [(k, due) for k, due, _ in _schedule(survey, enrolment, until, around)]


def stop_of(survey, enrolment):
    """The moment (naive UTC, in the person's time) after which no prompt of this person is due: leaving the study
    in the app, the researchers ending it for the person, or the end of data collection; None while none happened."""
    stops = []
    for name in ("left_at", "ended_at"):  # ended by the researchers (end): from that moment (audit 2026-10-12, D-7)
        at = (getattr(enrolment, "settings", None) or {}).get(name)
        if at:
            try:
                stops.append(virtual(enrolment, datetime.fromisoformat(at)))
            except (TypeError, ValueError):
                pass
    if getattr(survey, "status", None) == Survey.CLOSED and getattr(survey, "closed_at", None):
        stops.append(virtual(enrolment, survey.closed_at))
    return min(stops) if stops else None


def _schedule(survey, enrolment, until=None, around=None, counted=True):
    """prompts() with the local date of each prompt: [(key, due, local date)], in order. counted=False keeps the
    prompts of the first day before the entry part was completed (prompts_from)."""
    if not enrolment.first_day:
        return []
    stop = stop_of(survey, enrolment)
    if stop is not None:
        until = min(until, stop) if until is not None else stop
    history = tz_history(enrolment)
    kept = {}

    def zones(d):
        if not history:
            return kept.setdefault(None, zone(enrolment.tz))
        z = zone_on(enrolment, d, history)
        return kept.setdefault(z.key, z)
    margin = timedelta(days=1)
    bound_lo = around.date() - margin if around is not None else None
    bound_hi = around.date() + margin if around is not None else None
    if until is not None:
        bound_hi = min(bound_hi, until.date() + margin) if bound_hi else until.date() + margin
    if not enrolment.last_day and bound_hi is None:  # open-ended (open_end): up to tomorrow, never without end
        bound_hi = local_today(enrolment) + margin
    out = []
    for mods, start, end in _versions(survey):
        lo = start.date() - margin if start else None
        hi = end.date() + margin if end else None
        if bound_lo is not None:
            lo = max(lo, bound_lo) if lo else bound_lo
        if bound_hi is not None:
            hi = min(hi, bound_hi) if hi else bound_hi
        if lo and hi and lo > hi:
            continue
        out += [(k, due, d) for k, due, d in _fixed(mods, enrolment, zones, until, lo, hi)
                if (start is None or due >= start) and (end is None or due < end)]
    resumed = _resumed_days(enrolment)
    if resumed:  # the day of a resume: paused until its moment (resume)
        out = [(k, due, d) for k, due, d in out if d not in resumed or due >= resumed[d]]
    entered = _entered(enrolment)
    if entered is not None and counted:  # joined in the evening: the prompts of the first day before it were never sent (G1)
        out = [(k, due, d) for k, due, d in out if due >= entered.replace(second=0)]
    return sorted(out, key=lambda p: p[1])


# --- random prompts within time windows (signal-contingent; concept 4.1) ------------------------------------------
# Server and phone draw the same times from the same seed with the same arithmetic (askurious-android, Schedule.kt):
# SplitMix64 over a state from the person's seed, the module key (FNV-1a 64) and the day (days since 1970-01-01).
# The free minutes of the day (the windows without the person's quiet hours) are cut into per_day equal slots; each
# slot draws one minute, kept min_gap/2 from its edges, so two prompts are at least min_gap apart.

MASK64 = (1 << 64) - 1
GOLDEN = 0x9E3779B97F4A7C15
SEED_BITS = 53  # a JSON number every client reads exactly


def _splitmix(state):
    """(next value, next state) of SplitMix64, both 64-bit unsigned."""
    state = (state + GOLDEN) & MASK64
    z = ((state ^ (state >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & MASK64
    return z ^ (z >> 31), state


def _fnv(text):
    """FNV-1a 64 over the UTF-8 bytes of `text`."""
    h = 0xCBF29CE484222325
    for b in text.encode("utf-8"):
        h = ((h ^ b) * 0x100000001B3) & MASK64
    return h


def _minutes(hhmm):
    h, m = (int(x) for x in hhmm.split(":"))
    return h * 60 + m


def _free(windows, quiet):
    """The minutes of the day (0..1439) inside the windows and outside the quiet hours, as [(start, end)] (end
    exclusive), in order. A window or quiet time whose end is before its start runs over midnight. Kept per
    windows and quiet hours: they are the same on every day of a module (audit 2026-10-11, H2)."""
    try:
        key = (tuple(tuple(w) for w in windows or []), tuple(quiet) if quiet else None)
        hash(key)
    except TypeError:  # something odd in a schedule: computed, not kept
        return _free_of(windows, quiet)
    return list(_free_kept(*key))


@functools.lru_cache(maxsize=4096)
def _free_kept(windows, quiet):
    return tuple(_free_of([list(w) for w in windows], list(quiet) if quiet else None))


def _free_of(windows, quiet):
    def spans(a, b):
        return [(a, b)] if a < b else [(a, 1440), (0, b)] if a > b else []

    minutes = set()
    for w in windows or []:
        for a, b in spans(_minutes(w[0]), _minutes(w[1])):
            minutes.update(range(a, b))
    if quiet:
        for a, b in spans(_minutes(quiet[0]), _minutes(quiet[1])):
            minutes.difference_update(range(a, b))
    out, run = [], None
    for x in sorted(minutes):
        if run and x == run[1]:
            run[1] = x + 1
        else:
            run = [x, x + 1]
            out.append(run)
    return [tuple(r) for r in out]


def random_times(seed, key, day, trigger, quiet=None):
    """The times ("HH:MM", in order) of a random module on one day; the same on server and phone (module text)."""
    n, gap = int(trigger.get("per_day") or 0), int(trigger.get("min_gap") or 0)
    free = _free(trigger.get("windows") or [], quiet)
    total = sum(b - a for a, b in free)
    if n <= 0 or total <= 0:
        return []
    state = (int(seed) ^ _fnv(key) ^ ((day - date(1970, 1, 1)).days * GOLDEN)) & MASK64
    out = []
    for i in range(n):
        start, end = i * total // n, (i + 1) * total // n
        lo, hi = start + gap // 2, end - (gap - gap // 2)
        if hi <= lo:  # the slots are shorter than the gap: anywhere in the slot
            lo, hi = start, end
        if hi <= lo:
            continue
        value, state = _splitmix(state)
        index = lo + value % (hi - lo)
        for a, b in free:  # the index-th free minute
            if index < b - a:
                minute = a + index
                break
            index -= b - a
        out.append(f"{minute // 60:02d}:{minute % 60:02d}")
    return sorted(out)


def gap_kept(trigger):
    """Whether random_times keeps the minimum gap of a random module on a day without quiet hours: every slot of the
    windows must be longer than the gap, else a slot draws anywhere in it and two prompts may lie a minute apart
    (audit 2026-10-12, D-5). The person's quiet hours may still shorten the windows."""
    n, gap = int(trigger.get("per_day") or 0), int(trigger.get("min_gap") or 0)
    total = sum(b - a for a, b in _free(trigger.get("windows") or [], None))
    return n <= 0 or total <= 0 or total // n > gap


def seed_of(enrolment):
    """The person's seed of the random prompts: from a lasting key and the person id, never stored. The key is
    DIARY_KEY, without it SECRET_KEY as before (so studies keep their times): a new SECRET_KEY after an incident must
    not move the prompts already answered, or every one of them would count as missed (audit 2026-10-12, D-10)."""
    key = current_app.config.get("DIARY_KEY") or current_app.secret_key
    digest = hmac.new(key.encode() if isinstance(key, str) else key, f"diary-seed:{enrolment.pid}".encode(),
                      hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << SEED_BITS) - 1)


def quiet_history(enrolment):
    """[{"from": "YYYY-MM-DD", "q": ["22:00", "07:00"] or None}] in order: the quiet hours the person set in the app,
    each valid from the day after it was set (enrolment.settings)."""
    return list((getattr(enrolment, "settings", None) or {}).get("quiet") or [])


def quiet_on(history, day):
    """The quiet hours valid on `day`, or None."""
    valid = [h for h in history if h.get("from", "") <= day.isoformat()]
    return valid[-1].get("q") if valid else None


def set_quiet(enrolment, quiet, now=None):
    """The quiet hours the app sends ([start, end] "HH:MM", or None); a change applies from the person's next day."""
    if quiet is not None and not (isinstance(quiet, list) and len(quiet) == 2 and all(isinstance(x, str) and TIME_RE.match(x) for x in quiet)):
        return
    quiet = [f"{_minutes(x) // 60:02d}:{_minutes(x) % 60:02d}" for x in quiet] if quiet else None
    history = quiet_history(enrolment)
    if (history[-1].get("q") if history else None) == quiet:
        return
    start = (local_today(enrolment, now) + timedelta(days=1)).isoformat()
    history = [h for h in history if h.get("from") != start] + [{"from": start, "q": quiet}]
    # one entry per day at most (a change on the same day replaces it), and every entry holds for the days until the
    # next: trimmed after 30 changes, the days of a dropped entry took the quiet hours before it, so prompts already
    # answered were computed anew at other times and counted as missed (audit 2026-10-17, T2)
    if len(history) > MAX_PERSON_HISTORY:
        history = history[:1] + history[-(MAX_PERSON_HISTORY - 1):]
    enrolment.settings = {**(getattr(enrolment, "settings", None) or {}), "quiet": history}


def prompts_from(survey, enrolment):
    """For the app: the moment (epoch milliseconds, the person's time like "completed_prompts") from which the
    prompts of this person count, or None when none of the schedule is left out. The schedule of the first day holds
    prompts due before the entry part was completed (G1): the app computes them from the same definition and offered
    them, and the server refused their answers (audit 2026-10-18, follow-up of AND-02). The value is the minute after
    the last prompt left out, not the moment itself: the app learns no time beyond its schedule, and with the same
    schedule "due >= prompts_from" keeps exactly the prompts that count (G1 stays: entered_at is not sent)."""
    entered = _entered(enrolment)
    if entered is None or not enrolment.first_day:
        return None
    cut = entered.replace(second=0, microsecond=0)
    before = [due for _, due, _ in _schedule(survey, enrolment, around=entered, counted=False) if due < cut]
    if not before:
        return None
    return int(max(before).replace(second=0, microsecond=0, tzinfo=timezone.utc).timestamp() * 1000) + 60_000


def is_prompt(survey, enrolment, key, due):
    """Whether `due` (naive UTC) is a prompt of module `key` for this person, to the minute. Only the days around
    `due` are computed (audit 2026-10-11, H2)."""
    due = due.replace(second=0, microsecond=0)
    return any(k == key and d.replace(second=0, microsecond=0) == due
               for k, d in prompts(survey, enrolment, around=due))


def part_label(part, survey):
    """The name of a part for the researchers' pages: "Eingangsbefragung", "Kurzbefragung: <name>" or
    "Abschlussbefragung" (Ralf, 2026-10-11)."""
    if part == ENTRY:
        return de_en("Eingangsbefragung", "Entry survey")
    if part == EXIT:
        return de_en("Abschlussbefragung", "Exit survey")
    m = module(survey, part)
    return de_en("Kurzbefragung: {name}", "Short survey: {name}", name=text_of(m.get("name"), survey.default_language, survey) if m else part)


STUDY_PASSES = 3  # how often the test run of the whole study takes each short survey (preview, study_run)


def study_run(survey, passes=STUDY_PASSES):
    """The parts of a test run of the whole study, in the order a person gets them: the entry survey, every short
    survey `passes` times (one round after the other), the exit survey; parts without blocks are left out
    (pre-test 2026-10-16: the preview showed every block once, in the order of the flow)."""
    has = {part_of(b) for b in survey.blocks if b.items}
    rounds = [m["key"] for _ in range(max(1, passes)) for m in modules(survey) if m["key"] in has]
    return [p for p in [ENTRY] if p in has] + rounds + [p for p in [EXIT] if p in has]


def part_keys(survey):
    """The parts in the order of a person's study: the entry survey, the modules, the exit survey."""
    return [ENTRY] + [m["key"] for m in modules(survey)] + [EXIT]


def when_asked(part, survey):
    """When a part is asked, in a few words, for the head of the part in the editor."""
    sampling = survey.sampling or {}
    if part == ENTRY:
        return de_en("einmal nach dem Beitritt in der App, mit der Einwilligung",
                     "once after joining in the app, with the consent")
    if part == EXIT:
        n = sampling.get("period_days") or 14
        if open_end(survey):
            if blocks_of(survey, EXIT):
                return de_en("einmal, wenn Sie die Teilnahme der Person beenden", "once, when you end the person's participation")
            return de_en("einmal, wenn Sie die Teilnahme der Person beenden; ohne Blöcke gibt es keine",
                         "once, when you end the person's participation; without blocks there is none")
        if blocks_of(survey, EXIT):
            return de_en("einmal nach dem letzten Tag (Tag {n})", "once after the last day (day {n})", n=n)
        return de_en("einmal nach dem letzten Tag (Tag {n}); ohne Blöcke gibt es keine",
                     "once after the last day (day {n}); without blocks there is none", n=n)
    m = module(survey, part) or {}
    t = m.get("trigger") or {}
    if t.get("t") == "self":
        return de_en("wann die Teilnehmenden etwas melden möchten (Knopf in der App)",
                     "whenever the participants want to report something (button in the app)")
    if t.get("t") == "random":
        windows = ", ".join(f"{a}–{b}" for a, b in t.get("windows") or [])
        timed = de_en("{n}× am Tag zufällig ({w})", "{n}× a day at random ({w})", n=t.get("per_day") or 3, w=windows)
    elif t.get("times"):
        timed = de_en("{n}× am Tag um {t}", "{n}× a day at {t}", n=len(t["times"]), t=", ".join(t["times"]))
    else:
        timed = de_en("noch keine Uhrzeit festgelegt", "no time set yet")
    return timed


def editor_parts(survey):
    """The tab "Fragebogen" of a diary study shows its blocks in sections, one per part (Ralf, 2026-10-11): returns
    (blocks in the order of the parts, {block id: [parts whose head comes before it]}, [parts after the last block]).
    Empty parts get their head too, so that a block can be added there. The order within a part is the flow order."""
    keys = part_keys(survey)
    rank = {k: n for n, k in enumerate(keys)}
    ordered = sorted(survey.blocks, key=lambda b: (rank.get(part_of(b), 0), b.position))
    heads, shown = {}, 0
    for block in ordered:
        n = rank.get(part_of(block), 0)
        if n >= shown:
            heads[block.id] = keys[shown:n + 1]
            shown = n + 1
    return ordered, heads, keys[shown:]


def move_in_part(survey, block, direction):
    """The editor's ↑/↓ in a diary study: the block moves in the flow until it has passed the next block of its own
    part, so that the arrows do something visible in its section; blocks of other parts in between do not count.
    Returns False (and leaves the flow as it was) at the edge of the part or of its flow group."""
    import copy

    from app import flow

    ids = blocks_of(survey, part_of(block))
    order = lambda: [b for b in flow.block_order(flow.tree(survey)) if b in ids]
    start, original = order(), copy.deepcopy(survey.flow)
    for _ in range(len(survey.blocks)):
        if not flow.move_block_among_siblings(survey, block.id, direction):
            break
        if order() != start:
            return True
    survey.flow = original
    flow.normalize(survey)
    return False


def set_part(survey, block, part):
    """Put a block into a part, while the structure may change; the consent stays in the entry survey. Returns
    whether something changed."""
    if locked(survey) or part not in part_keys(survey) or part_of(block) == part:
        return False
    if any(i.type == "consent" for i in block.items) and part != ENTRY:
        return False
    block.settings = {**(block.settings or {}), "part": part}
    return True


def entry_tally(survey, sources=("real",)):
    """The completed entries of the modules (not the entry parts), for the counts beside the persons; counted by the
    database (audit 2026-10-11, M10)."""
    return db.session.scalar(db.select(db.func.count(Response.id)).where(
        Response.survey_id == survey.id, Response.enrolment_id.is_not(None), Response.source.in_(tuple(sources)),
        Response.is_preview.is_(False), Response.status == Response.COMPLETED, Response.part_expr() != ENTRY)) or 0


LATE_SHOWN = 50  # entries sent later listed on the page "Qualitätssicherung"


def late_entries(survey, sources=("real",)):
    """The completed entries answered without a connection and sent later ("nachgereicht"), for the page
    "Qualitätssicherung" (audit 2026-10-11, M1): {"n": how many, "of": completed module entries, "rows": the newest
    LATE_SHOWN with person, part, prompt, start and end as the phone says and the server's time of arrival}."""
    base = (Response.survey_id == survey.id, Response.enrolment_id.is_not(None), Response.source.in_(tuple(sources)),
            Response.is_preview.is_(False), Response.status == Response.COMPLETED, Response.part_expr() != ENTRY)
    late = Response.occasion["offline"].as_boolean().is_(True)
    n = db.session.scalar(db.select(db.func.count(Response.id)).where(*base, late)) or 0
    of = db.session.scalar(db.select(db.func.count(Response.id)).where(*base)) or 0
    rows = []
    if n:
        persons = dict(db.session.execute(db.select(Enrolment.id, Enrolment.pid).filter_by(survey_id=survey.id)).all())
        found = db.session.execute(db.select(Response.enrolment_id, Response.part, Response.due_at, Response.started_at,
                                             Response.completed_at, Response.received_at)
                                   .where(*base, late).order_by(Response.id.desc()).limit(LATE_SHOWN)).all()
        for eid, part, due, started, completed, received in found:
            lag = int((received - completed).total_seconds()) if received and completed else None
            rows.append({"person": (persons.get(eid) or "")[-6:], "part": part_label(part or ENTRY, survey), "due": due,
                         "started": started, "completed": completed, "received": received, "lag": lag})
    return {"n": n, "of": of, "rows": rows}


def person_weights(rows, numbered):
    """Equal weight for every person in every part (results.compute): an entry weighs 1 / the number of entries of
    its person in its part in the pool, so who answered often counts as much as who answered rarely; the entry part
    (once per person) weighs 1. {response id: weight}. `rows`: rows of export.rows, `numbered`: {id: Response}."""
    from collections import Counter

    key = {}
    for r in rows:
        resp = numbered.get(r.get("resp_id"))
        if resp is not None and resp.enrolment_id:
            key[r["resp_id"]] = (resp.enrolment_id, (resp.occasion or {}).get("part", ENTRY))
    counts = Counter(key.values())
    return {rid: 1 / counts[k] for rid, k in key.items()}


def question_weights(rows, numbered, columns, missing=None):
    """person_weights for one question (decision 6 of the audit 2026-10-12, D-4): an entry with a valid value in one of
    `columns` weighs 1 / the number of entries of its person in its part with a valid value in this question, so every
    person counts once in every question, also in an optional one; an entry without one keeps 1 / its person's entries
    in the part (it adds nothing but a missing value). missing: {column: codes that count as missing}. {response id:
    weight}."""
    from collections import Counter

    missing = missing or {}

    def valid(r):
        return any(_given(r.get(c)) and _code(r.get(c)) not in missing.get(c, ()) for c in columns)

    key, ok = {}, set()
    for r in rows:
        resp = numbered.get(r.get("resp_id"))
        if resp is not None and resp.enrolment_id:
            key[r["resp_id"]] = (resp.enrolment_id, (resp.occasion or {}).get("part", ENTRY))
            if valid(r):
                ok.add(r["resp_id"])
    every = Counter(key.values())
    good = Counter(k for rid, k in key.items() if rid in ok)
    return {rid: 1 / (good[k] if rid in ok else every[k]) for rid, k in key.items()}


def _given(v):
    """An answer that counts for the weights: not empty and no number that is none (NaN, infinite), as old answers
    may hold before the number fields refused them; such a value is left out of the statistics, so it must not take
    a share of its person's weight either (audit 2026-10-17, M-R1)."""
    if v in (None, ""):
        return False
    try:
        x = float(str(v).replace(",", ".")) if isinstance(v, (str, int, float)) and not isinstance(v, bool) else None
    except ValueError:
        return True  # a text or a choice code
    return x is None or math.isfinite(x)


def _code(v):
    """A value as the codes that count as missing are compared (results._key)."""
    return str(int(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) and float(v).is_integer() else str(v)


def reminder_of(m):
    """Minutes after a prompt at which the app reminds once, if it is still open; 0: no reminder. Only before the
    prompt expires (a reminder at or after the expiry is none)."""
    if (m.get("trigger") or {}).get("t") not in SCHEDULED:
        return 0
    minutes, expires = int(m.get("reminder") or 0), int(m.get("expires") or 0)
    return minutes if minutes and (not expires or minutes < expires) else 0


# element types a phone can answer without a connection (the page is kept in the app and its form sent later,
# diary_api.offline): no script talks to the server, nothing is uploaded, no answer of another page is read
OFFLINE_TYPES = {"single_choice", "multiple_choice", "rating", "likert", "slider", "semantic", "text_input", "info",
                 "page_break"}


def offline_ok(survey, part):
    """Whether a module can be answered offline: one page, only OFFLINE_TYPES, no conditions or piping on it."""
    ids = blocks_of(survey, part)
    blocks = [b for b in survey.blocks if b.id in ids]
    if not blocks or len(blocks) > 1:
        return False
    items = blocks[0].items
    if any(i.type not in OFFLINE_TYPES for i in items) or any(i.type == "page_break" for i in items):
        return False
    from app import item_condition, rows

    return not any(item_condition.of(i) is not None or "{{" in str(i.texts or "") or any(
        isinstance(value, list) and any(isinstance(row, dict) and (rows.partial(row) or row.get("src"))
                                       for row in value) for value in (i.config or {}).values()) for i in items)


def part_durations(survey):
    """[(part key, estimate)] for the key figures of the editor and the flow: each part of the study on its own,
    parts without blocks left out (app/duration.py)."""
    from app import duration

    used = {part_of(b) for b in survey.blocks}
    # the main figure is the whole part, the range beside it: a condition within a part (shown to some only) made the
    # middle of the range the figure, half the questions of the part (pre-test 2026-10-23, E2)
    return [(k, {**e, "typical": e["high"]}) for k in part_keys(survey) if k in used
            for e in [duration.estimate(survey, part=k)]]


def module_seconds(survey, key):
    """The estimated time of one entry of a module (app/duration.py), in seconds."""
    from app import duration

    lang = survey.default_language
    factor = duration.PHONE_FACTOR if duration.on_phone(key) else 1  # answered on the phone
    return factor * sum(duration.block_seconds(b, lang) for b in survey.blocks if part_of(b) == key)


WARN_PROMPTS = 6  # more prompts a day lower compliance (Wrzus & Neubauer, 2023)
WARN_MINUTES = 2  # a module should take under two minutes (van Berkel et al., 2017)


def week_preview(survey, start=None):
    """The first seven days of an example person who completes the entry part today: [{"date", "day", "prompts":
    [(HH:MM, module name)], "minutes"}] plus the warnings, for the tab "Zeitplan"."""
    lang = survey.default_language
    today = german_today()  # the German day, not the server's UTC one (audit 2026-10-13, CF-3)
    start = start or (today + timedelta(days=1) if (survey.sampling or {}).get("start") == "next_day" else today)
    seconds = {m["key"]: module_seconds(survey, m["key"]) for m in modules(survey)}
    days = []
    for n in range(7 if open_end(survey) else min(7, int((survey.sampling or {}).get("period_days") or 7))):
        d = start + timedelta(days=n)
        prompts = []
        for m in modules(survey):
            t = m.get("trigger") or {}
            if t.get("t") not in SCHEDULED or not active_on(m, n + 1) or d.isoweekday() not in (t.get("weekdays") or range(1, 8)):
                continue
            # random modules: the draw of an example person (seed 0); each person gets other times
            times = random_times(0, m["key"], d, t) if t.get("t") == "random" else t.get("times") or []
            prompts += [(hhmm, text_of(m.get("name"), lang, survey) + (" ~" if t.get("t") == "random" else ""), m["key"])
                        for hhmm in times]
        prompts.sort()
        days.append({"date": d, "day": n + 1, "label": fmt_local(d, lang="en" if english() else "de"), "prompts": [(h, name) for h, name, _ in prompts],
                     "minutes": sum(seconds[k] for _, _, k in prompts) / 60})
    warnings = []
    if any(len(d["prompts"]) > WARN_PROMPTS for d in days):
        warnings.append(de_en("An manchen Tagen mehr als {n} Abfragen: Das senkt die Antwortquote.",
                              "More than {n} prompts on some days: this lowers compliance.", n=WARN_PROMPTS))
    for m in modules(survey):
        t = m.get("trigger") or {}
        if t.get("t") == "random" and not gap_kept(t):
            warnings.append(de_en("„{name}“: Die Zeitfenster sind für {n} Abfragen mit {gap} Minuten Mindestabstand zu "
                                  "kurz; die Abfragen können dann dicht hintereinander liegen. Fenster verlängern, "
                                  "weniger Abfragen oder einen kleineren Abstand wählen.",
                                  "“{name}”: the windows are too short for {n} prompts with a minimum gap of {gap} "
                                  "minutes; the prompts may then come close together. Lengthen the windows, choose "
                                  "fewer prompts or a smaller gap.", name=text_of(m.get("name"), lang, survey),
                                  n=int(t.get("per_day") or 0), gap=int(t.get("min_gap") or 0)))
    long = [text_of(m.get("name"), lang, survey) for m in modules(survey) if seconds[m["key"]] > WARN_MINUTES * 60]
    if long:
        warnings.append(de_en("Länger als {n} Minuten je Eintrag: {names}.", "Longer than {n} minutes per entry: {names}.",
                              n=WARN_MINUTES, names=", ".join(long)))
    return days, warnings


def missed_rows(survey, rows, sources=("real",), now=None):
    """For the long export: a row for every prompt of a real person that was due and is over (expired, or the day is
    over) without its entry in the export, with person, part, day and time but no answers. "abfrage": "verpasst" when
    the person has no entry of the prompt at all, "zur_pruefung" when its offline answer waits as a review case on
    "Offline-Prüffälle" (audit 2026-10-21, F2), "abgebrochen" when the entry was begun and not finished (always so,
    whatever the export takes; decision 10 of the audit 2026-10-12). Whether the person answered is read from all
    their entries in the database, not from the rows of this export: a completed entry the export leaves out (marked
    by quality assurance) gets no stand-in. Only in an export with the real data (`sources`), as the persons are real
    (audit 2026-10-12, E-2, D-2). `rows`: the rows of the export."""
    if "real" not in tuple(sources):
        return []
    now = now or utcnow()
    present = {(r.get("person"), r.get("teil"), r.get("faellig")) for r in rows if r.get("faellig")}
    out = []
    persons = db.session.scalars(db.select(Enrolment).filter_by(survey_id=survey.id, source="real")
                                 .order_by(Enrolment.id)).all()
    by_person = _entries_by_person(survey) if persons else {}
    triggers = {m["key"]: (m.get("trigger") or {}).get("t") for m in modules(survey)}
    for person in persons:
        state = {}
        for r in by_person.get(person.id, []):
            occ = r.occasion or {}
            if r.is_preview or not occ.get("due"):
                continue
            try:
                key = (occ.get("part"), datetime.fromisoformat(occ["due"]).isoformat(timespec="minutes"))
            except (TypeError, ValueError):
                continue
            if r.status == Response.COMPLETED:
                state[key] = "done"
            elif r.status == REVIEW:  # answered offline, kept as a review case: not broken off (audit 2026-10-21, F2)
                state[key] = "zur_pruefung" if state.get(key) != "done" else "done"
            else:
                state.setdefault(key, "abgebrochen")
        for key, due, day in _schedule(survey, person, until=now):
            tz = zone_on(person, day)
            stamp = due.isoformat(timespec="minutes")
            if (person.pid, key, stamp) in present or state.get((key, stamp)) == "done":
                continue
            if prompt_over(module(survey, key) or {}, due, day, tz) > now:
                continue
            local = due.replace(tzinfo=timezone.utc).astimezone(tz)
            out.append({"person": person.pid, "teil": key, "tag": day_index(person, local.date()) or "",
                        "ausloeser": triggers.get(key) if triggers.get(key) in SCHEDULED else "fixed",
                        "faellig": stamp, "abfrage": state.get((key, stamp), "verpasst"),
                        "ortszeit": local.strftime("%H:%M"), "wochentag": local.isoweekday()})
    return out


def prompt_over(m, due, day, tz):
    """When a prompt of module `m` due at `due` (naive UTC) on the local date `day` in zone `tz` is over: after its
    expiry, or without one at the end of its day."""
    expires = int(m.get("expires") or 0)
    if expires:
        return due + timedelta(minutes=expires)
    return datetime.combine(day + timedelta(days=1), time(0), tz).astimezone(timezone.utc).replace(tzinfo=None)


def prompt_day(enrolment, due):
    """The local date of the person's prompt due at `due` (naive UTC): the day whose zone gives that time."""
    for d in (due.date() - timedelta(days=1), due.date(), due.date() + timedelta(days=1)):
        local = due.replace(tzinfo=timezone.utc).astimezone(zone_on(enrolment, d))
        if local.date() == d:
            return d
    return local_date(enrolment, due)


LOW_COMPLIANCE = 0.5  # usable persons answer at least half of their prompts, a common rule (Wrzus & Neubauer, 2023)
SILENT_DAYS = 2  # no answered entry for this many days of an active person
SAME_SHARE = 0.8  # entries that repeat the answers of the one before (careless responding; Jaso et al., 2022)
JASO = ("Jaso, B. A., Kraus, N. I., & Heller, A. S. (2022). Identification of careless responding in ecological momentary "
        "assessment research: From posthoc analyses to real-time data monitoring. Psychological Methods, 27(6), 958–981. "
        "https://doi.org/10.1037/met0000312")


def person_quality(person, status, entries, answered, due, today):
    """What stands out about one person on the tab "Zeitplan": [(key, text)] for a low compliance (at least five
    prompts due), no answered entry for SILENT_DAYS days while active, and entries that keep repeating the answers
    of the one before in the same module (at least five)."""
    out = []
    if due >= 5 and answered / due < LOW_COMPLIANCE:
        out.append(("low", de_en("Antwortquote unter {p} %", "compliance below {p}%", p=int(LOW_COMPLIANCE * 100))))
    done = sorted((r for r in entries if r.status == Response.COMPLETED and (r.occasion or {}).get("part", ENTRY) != ENTRY),
                  key=lambda r: r.completed_at or r.started_at)
    last = done[-1].completed_at if done else None
    if status == Enrolment.ACTIVE and person.first_day and person.first_day <= today:
        since = local_date(person, virtual(person, last)) if last else person.first_day - timedelta(days=1)
        if (today - since).days >= SILENT_DAYS:
            out.append(("silent", de_en("seit {n} Tagen kein Eintrag", "no entry for {n} days", n=(today - since).days)))
    same, pairs, before = 0, 0, {}
    for r in done:
        part = (r.occasion or {}).get("part")
        if part in before and r.answers:
            pairs += 1
            same += r.answers == before[part]
        before[part] = r.answers
    if pairs >= 5 and same / pairs >= SAME_SHARE:
        out.append(("same", de_en("{p} % der Einträge wie der vorige", "{p}% of the entries like the one before",
                                  p=round(100 * same / pairs))))
    return out, last


TIME_TYPES = {"rating", "slider", "likert", "semantic"}  # answers on a numeric scale: means over time make sense
SHROUT = ("Shrout, P. E., & Fleiss, J. L. (1979). Intraclass correlations: Uses in assessing rater reliability. "
         "Psychological Bulletin, 86(2), 420–428. https://doi.org/10.1037/0033-2909.86.2.420")
BOLGER = "Bolger, N., & Laurenceau, J.-P. (2013). Intensive longitudinal methods. Guilford Press."


def icc1(groups):
    """ICC(1) of one-way ANOVA (Shrout & Fleiss, 1979) for {person: [values]}: the share of the variance between
    persons, with the average group size adjusted for unequal groups; None with fewer than two persons with two
    values each."""
    groups = [g for g in groups.values() if len(g) >= 1]
    n, k = sum(len(g) for g in groups), len(groups)
    if k < 2 or n <= k or sum(1 for g in groups if len(g) >= 2) < 2:
        return None
    grand = sum(sum(g) for g in groups) / n
    ssb = sum(len(g) * (sum(g) / len(g) - grand) ** 2 for g in groups)
    ssw = sum(sum((x - sum(g) / len(g)) ** 2 for x in g) for g in groups)
    msb, msw = ssb / (k - 1), ssw / (n - k)
    k0 = (n - sum(len(g) ** 2 for g in groups) / n) / (k - 1)
    whole = msb + (k0 - 1) * msw
    return (msb - msw) / whole if whole > 0 else None


def time_results(survey, sources=("real",), rows=None):
    """The entries of the modules over time, for the results page: per numeric question of a module the mean by day
    of the study and by hour of the day (local time of the person), and ICC(1): how much of the variance lies between
    persons rather than within them. [{"code", "label", "module", "n", "persons", "icc", "by_day": [(day, mean, n)],
    "by_hour": [(hour, mean, n)]}]. rows: the rows of export.rows of these sources when the caller has them (the
    results page), so they are not built twice (audit 2026-10-11, M10)."""
    from app import export

    keys = {m["key"] for m in modules(survey)}
    items = [(i, b) for b in survey.blocks if part_of(b) in keys for i in b.items if i.type in TIME_TYPES]
    if not items:
        return []
    if rows is None:
        rows = export.rows(survey, sources=tuple(sources), with_hb=False, with_quality=False)
    rows = [r for r in rows if r.get("teil") in keys]
    out = []
    for item, block in items:
        for col, label in export._plain_columns(item):
            # the codes the element type names as missing ("weiß nicht" ...), as results.py does; a scale from -10 to
            # 10 has real negative values (audit 2026-10-12, D-1)
            missing = set()
            for code in item.kind.missing_codes(item, col) or ():
                try:
                    missing.add(float(code))
                except (TypeError, ValueError):
                    pass
            by_person, by_day, by_hour = {}, {}, {}
            for r in rows:
                if r.get("teil") != part_of(block):
                    continue
                try:
                    x = float(str(r.get(col)).replace(",", "."))
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(x) or x in missing:  # an old "nan" is no value (audit 2026-10-17, M-R1)
                    continue
                by_person.setdefault(r.get("person"), []).append(x)
                if r.get("tag") not in (None, ""):
                    by_day.setdefault(int(r["tag"]), []).append(x)
                if r.get("ortszeit"):
                    by_hour.setdefault(int(r["ortszeit"][:2]), []).append(x)
            if not by_person:
                continue
            mean = lambda xs: sum(xs) / len(xs)
            out.append({"code": col, "label": label, "module": part_label(part_of(block), survey),
                        "n": sum(len(v) for v in by_person.values()), "persons": len(by_person), "icc": icc1(by_person),
                        "by_day": [(d, mean(v), len(v)) for d, v in sorted(by_day.items())],
                        "by_hour": [(h, mean(v), len(v)) for h, v in sorted(by_hour.items())]})
    return out


def method_facts(survey):
    """What the method section says about a diary study (app/methods.py): the design and the compliance of the real
    persons whose entry part is completed. {"de": text, "en": text for the AI}."""
    lang = survey.default_language
    sampling = survey.sampling or {}
    parts_de, parts_en = [], []
    for m in modules(survey):
        t, name = m.get("trigger") or {}, text_of(m.get("name"), lang, survey)
        days = len(t.get("weekdays") or range(1, 8))
        week_de = "" if days == 7 else f" an {days} Wochentagen"
        week_en = "" if days == 7 else f" on {days} weekdays"
        if t.get("t") == "fixed":
            parts_de.append(f"„{name}“ {len(t.get('times') or [])}-mal täglich zu festen Zeiten ({', '.join(t.get('times') or [])} Uhr){week_de}")
            parts_en.append(f"\"{name}\" {len(t.get('times') or [])} times a day at fixed times ({', '.join(t.get('times') or [])}){week_en}")
        elif t.get("t") == "random":
            windows = ", ".join(f"{a}–{b}" for a, b in t.get("windows") or [])
            if gap_kept(t):  # what the draw really keeps (audit 2026-10-12, D-5)
                gap_de = (f"mindestens {t.get('min_gap') or 0} Minuten auseinander, soweit die Ruhezeiten der Person die "
                          f"Zeitfenster nicht verkürzten")
                gap_en = (f"at least {t.get('min_gap') or 0} minutes apart, as far as the person's quiet hours did not "
                          f"shorten the windows")
            else:
                gap_de = (f"ohne gesicherten Mindestabstand (die Zeitfenster waren für {t.get('per_day')} Abfragen mit "
                          f"{t.get('min_gap') or 0} Minuten Abstand zu kurz)")
                gap_en = (f"without an assured minimum gap (the windows were too short for {t.get('per_day')} prompts "
                          f"{t.get('min_gap') or 0} minutes apart)")
            parts_de.append(f"„{name}“ {t.get('per_day')}-mal täglich zu zufälligen Zeiten zwischen {windows} Uhr, "
                            f"{gap_de} (geschichtet){week_de}")
            parts_en.append(f"\"{name}\" {t.get('per_day')} times a day at random times between {windows}, "
                            f"{gap_en} (stratified){week_en}")
        if t.get("t") not in SCHEDULED:
            parts_de.append(f"„{name}“ von den Teilnehmenden selbst begonnen (ereignisbasiert)")
            parts_en.append(f"\"{name}\" started by the participants themselves (event-contingent)")
    persons = db.session.scalars(db.select(Enrolment).where(Enrolment.survey_id == survey.id, Enrolment.source == "real",
                                                            Enrolment.first_day.is_not(None))).all()
    by_person = _entries_by_person(survey)  # one query, not one per person (audit 2026-10-11, N13)
    rates, in_review = [], 0
    for p in persons:
        answered, due, review = compliance(survey, p, entries=by_person.get(p.id, []), with_review=True)
        in_review += review
        if due:
            rates.append(answered / due)
    entries = entry_tally(survey)
    import statistics

    middle = statistics.median(rates) if rates else None  # the mean of the two middle ones for an even number (D-14)
    rate_de = (f" Die Antwortquote (beantwortete von fälligen Abfragen) lag im Mittel bei {100 * sum(rates) / len(rates):.0f} %"
               f" (Median {100 * middle:.0f} %)." if rates else "")
    rate_en = (f" Compliance (answered of due prompts): mean {100 * sum(rates) / len(rates):.0f}%, median "
               f"{100 * middle:.0f}%." if rates else "")
    if in_review:  # offline answers kept as review cases count neither as answered nor as missed (audit 2026-10-21, F2)
        rate_de += (f" {in_review} offline gesendete Antwort{'en' if in_review != 1 else ''} auf Abfragen "
                    f"{'lagen' if in_review != 1 else 'lag'} noch zur Prüfung vor und "
                    f"{'zählen' if in_review != 1 else 'zählt'} weder als beantwortet noch als verpasst.")
        rate_en += (f" {in_review} answer{'s' if in_review != 1 else ''} to prompts sent offline "
                    f"{'were' if in_review != 1 else 'was'} still under review and count{'' if in_review != 1 else 's'} "
                    f"neither as answered nor as missed.")
    days = int(sampling.get("period_days") or 0)
    span_de = "ohne festes Ende, bis die Forschenden die Teilnahme beendeten" if open_end(survey) else f"{days} Tage"
    span_en = "without a fixed end, until the researchers ended it" if open_end(survey) else f"for {days} days"
    de = (f"Die Daten wurden als Tagebuchstudie (Experience Sampling) mit der App „Askurious“ für Android erhoben. "
          f"Nach einer Eingangsbefragung nahm jede Person {span_de} teil; Kurzbefragungen: {'; '.join(parts_de)}. "
          f"{len(persons)} Personen schlossen die Eingangsbefragung ab und machten zusammen {entries} Einträge.{rate_de} "
          f"Da die App nur für Android verfügbar war, konnten Personen mit einem iPhone nicht teilnehmen.")
    en = (f"Design: diary study (experience sampling) with the app Askurious for Android; after an entry survey "
          f"each person took part {span_en}; short surveys: {'; '.join(parts_en)}. {len(persons)} people completed "
          f"the entry survey and made {entries} entries in total.{rate_en} Restriction: the app was available for Android "
          f"only, so iPhone users could not take part.")
    return {"de": de, "en": en}


def per_day(m):
    """How often a module is due on an average day (fixed modules), for the join screen."""
    t = m.get("trigger") or {}
    if t.get("t") not in SCHEDULED:
        return 0
    n = int(t.get("per_day") or 0) if t.get("t") == "random" else len(t.get("times") or [])
    return n * len(t.get("weekdays") or range(1, 8)) / 7


def compliance(survey, enrolment, now=None, entries=None, with_review=False):
    """(answered, due so far) of the scheduled prompts of one person; an entry counts when completed, each prompt once
    (two entries of one prompt, which the rule before the audit 2026-10-11 let through, count once; M2). A prompt is
    due once it is over (expired, or its day is over), as for the rows "verpasst", or once it is answered: a prompt
    still open does not lower the rate (audit 2026-10-12, D-15). A prompt whose offline answer waits as a review case
    (status "review") is neither answered nor missed: it is left out of both until the case is accepted, and
    with_review=True adds their number as a third value (answered, due, in review) (audit 2026-10-21, F2)."""
    now = virtual(enrolment, now or utcnow())
    # a prompt still open when the person left or was ended never counts as due, also not once it is over later
    stop = stop_of(survey, enrolment)
    over_by = min(now, stop) if stop is not None else now
    done, held = set(), set()
    for r in _entries(enrolment) if entries is None else entries:
        occ = r.occasion or {}
        if r.status in (Response.COMPLETED, REVIEW) and occ.get("due"):
            (done if r.status == Response.COMPLETED else held).add(
                (occ.get("part"), datetime.fromisoformat(occ["due"]).replace(second=0, microsecond=0)))
    answered = due = review = 0
    zones, mods = {}, {}
    for key, at, day in _schedule(survey, enrolment, until=now):
        if (key, at.replace(second=0, microsecond=0)) in done:
            answered += 1
            due += 1
            continue
        if (key, at.replace(second=0, microsecond=0)) in held:
            review += 1
            continue
        if day not in zones:
            zones[day] = zone_on(enrolment, day)
        if key not in mods:
            mods[key] = module(survey, key) or {}
        if prompt_over(mods[key], at, day, zones[day]) <= over_by:
            due += 1
    return (answered, due, review) if with_review else (answered, due)


def study_json(survey, enrolment, lang, entries=None):
    """The study as the app gets it; retain all completed prompts for older Android clients.

    Without a supplied history, read only the two occasion fields needed by that wire contract.
    """
    status = refresh(survey, enrolment, entries=entries)
    if entries is None:
        completed = db.session.execute(db.select(Response.occasion["part"].as_string(),
                                                Response.occasion["due"].as_string()).where(
            Response.enrolment_id == enrolment.id, Response.status == Response.COMPLETED,
            Response.occasion["due"].as_string().is_not(None)).order_by(Response.id.desc()))
    else:
        completed = (((r.occasion or {}).get("part"), (r.occasion or {}).get("due"))
                     for r in entries if r.status == Response.COMPLETED)
    completed_prompts = [f"{part}|{int(datetime.fromisoformat(due).replace(tzinfo=timezone.utc).timestamp() * 1000)}"
                         for part, due in completed if part and due]
    out = []
    for m in modules(survey):
        t = m.get("trigger") or {}
        first, last = _days_range(m)
        trigger = ({"t": "self", "label": text_of(t.get("label"), lang, survey) or text_of(m.get("name"), lang, survey),
                    "max_per_day": int(t.get("max_per_day") or 0)} if t.get("t") == "self"
                   else {"t": "random", "per_day": int(t.get("per_day") or 0), "windows": list(t.get("windows") or []),
                         "min_gap": int(t.get("min_gap") or 0), "weekdays": list(t.get("weekdays") or range(1, 8))}
                   if t.get("t") == "random"
                   else {"t": "fixed", "times": list(t.get("times") or []), "weekdays": list(t.get("weekdays") or range(1, 8))})
        item = {"key": m["key"], "name": text_of(m.get("name"), lang, survey), "trigger": trigger,
                "expires": int(m.get("expires") or 0), "reminder": reminder_of(m), "days": [first, last or 100000],
                "notify": text_of(m.get("notify"), lang, survey), "offline": offline_ok(survey, m["key"])}
        out.append(item)
    return {"code": survey.code, "title": survey.title_in(lang), "summary": summary_of(survey, lang),
            "contact": (survey.consent_item.config or {}).get("contact_email", "") if survey.consent_item else "",
            "completed_prompts": completed_prompts,
            # prompts due before this (the first day before the entry part was completed) do not count (G1)
            "prompts_from": prompts_from(survey, enrolment) if status == Enrolment.ACTIVE else None,
            "open_end": open_end(survey), "status": status, "first_day": enrolment.first_day.isoformat() if enrolment.first_day else None,
            "last_day": enrolment.last_day.isoformat() if enrolment.last_day else None,
            "modules": out if status == Enrolment.ACTIVE else [],
            # the random prompts: the person's seed and quiet hours (random_times, the same on the phone)
            "seed": seed_of(enrolment), "quiet": quiet_history(enrolment), "pauses": pauses(enrolment),
            "tz": enrolment.tz, "tz_history": tz_history(enrolment),
            "exit": exit_info(survey, enrolment, status, lang, entries=entries), "clock": clock_json(enrolment),
            # an app older than this asks the person to update it (config DIARY_MIN_APP_VERSION, 0 = every version)
            "min_app_version": int(current_app.config.get("DIARY_MIN_APP_VERSION") or 0),
            "look": look_json(survey)}


# the colours of the participant pages the app uses for its own screens around them: {key of the app: CSS variable}
LOOK_TOKENS = {"background": "--bg", "surface": "--surface", "surface_2": "--surface-2", "text": "--fg",
               "muted": "--muted", "accent": "--accent", "on_accent": "--on-accent", "accent_soft": "--accent-soft",
               "border": "--border", "error": "--error"}


def look_json(survey):
    """The survey's look for the app, so that its own screens around the pages (the study card, the bar above an
    entry, the join screen) wear the colours of the pages: "colors" (#rrggbb), "colors_dark" only for a look that
    follows the device (scheme "auto"; the app takes it while the phone is dark), "radius" of the corners in px."""
    from app import theme

    look = theme.of(survey)

    def palette(side):
        tokens = theme.tokens(side)
        out = {key: (tokens.get(var) or "").strip().lower() for key, var in LOOK_TOKENS.items()}
        return {key: value for key, value in out.items() if theme.HEX.match(value)}

    radius = re.match(r"\d+", theme.tokens(look).get("--radius") or "")
    return {"colors": palette(look), "colors_dark": palette(theme.dark_side(look)) if theme.is_auto(look) else None,
            "radius": int(radius.group()) if radius else 10}


def clock_json(enrolment):
    """The fast-forward clock of a test person for the app ({"t0": epoch ms, "factor"}), or None."""
    clock = (getattr(enrolment, "settings", None) or {}).get("clock")
    if not clock:
        return None
    t0 = datetime.fromisoformat(clock["t0"]).replace(tzinfo=timezone.utc)
    return {"t0": int(t0.timestamp() * 1000), "factor": int(clock.get("factor") or 1)}


def summary_of(survey, lang):
    """The sentence about the study the app shows on its card and before joining ("" without one)."""
    value = (survey.sampling or {}).get("summary")
    return text_of(value, lang, survey) if value else ""


def public_json(survey, lang):
    """What the join screen of the app shows before someone joins."""
    out = []
    for m in modules(survey):
        t = m.get("trigger") or {}
        n = per_day(m)
        out.append({"name": text_of(m.get("name"), lang, survey), "trigger": t.get("t") or "fixed",
                    "per_day": round(n, 2), "per_week": round(n * 7), "self": self_start(m) is not None})
    return {"code": survey.code, "title": survey.title_in(lang), "description": summary_of(survey, lang),
            "contact": (survey.consent_item.config or {}).get("contact_email", "") if survey.consent_item else "",
            "open_end": open_end(survey),
            "period_days": 0 if open_end(survey) else int((survey.sampling or {}).get("period_days") or 0),
            "languages": list(survey.languages or []), "modules": out, "look": look_json(survey)}


def join_url(survey, code=None):
    from app import hosts

    return hosts.url_for("diary_api.join_page", code=code or survey.code, _external=True)


def qr_svg(url, mask=None):
    """The QR code of `url` as SVG. mask: a fixed mask pattern (0-7) instead of trying all eight and keeping the best;
    the code stays valid and readable, only its penalty score is a little worse. For many codes at once (invitation
    cards): 1000 codes 3.1 s → 0.8 s (audit 2026-10-11, N14)."""
    import io

    import segno

    out = io.BytesIO()
    segno.make(url, error="m", mask=mask).save(out, kind="svg", border=2, xmldecl=False, omitsize=True)
    return Markup(out.getvalue().decode("utf-8"))


# --- copies, files, deletion ------------------------------------------------------------------------------------

def clean(sampling):
    """A schedule from a copy or a file, checked value by value. None if `sampling` is no schedule. Block parts that
    name no module any more are left to fix_parts(), which the callers run once the blocks exist (audit 2026-10-13,
    CF-4)."""
    if not isinstance(sampling, dict):
        return None
    kept = []
    texts = lambda v, n: {str(k)[:8]: str(x)[:n] for k, x in v.items()} if isinstance(v, dict) else {}
    for m in sampling.get("modules") or []:
        if not isinstance(m, dict) or not MODULE_KEY.match(str(m.get("key", ""))) or len(kept) >= MAX_MODULES:
            continue
        t = m.get("trigger") if isinstance(m.get("trigger"), dict) else {}
        if t.get("t") == "self":
            trigger = {"t": "self", "label": texts(t.get("label"), 80), "max_per_day": _int(t.get("max_per_day"), 5, 0, 50)}
        elif t.get("t") == "random":
            trigger = _random_trigger(t.get("per_day"), " ".join(f"{w[0]}-{w[1]}" for w in t.get("windows") or []
                                                                  if isinstance(w, list) and len(w) == 2 and all(isinstance(x, str) for x in w)),
                                      t.get("min_gap"), [str(d) for d in t.get("weekdays") or []])
        else:
            trigger = {"t": "fixed", "times": _parse_times(" ".join(str(x) for x in t.get("times") or [] if isinstance(x, str))),
                       "weekdays": sorted({int(d) for d in t.get("weekdays") or [] if str(d) in WEEKDAYS}) or [1, 2, 3, 4, 5, 6, 7]}
        days = m.get("days") if isinstance(m.get("days"), list) else [1, None]
        module_ = {"key": m["key"], "name": texts(m.get("name"), 80), "trigger": trigger,
                   "expires": _int(m.get("expires"), 60, 0, 24 * 60), "reminder": _int(m.get("reminder"), 0, 0, 24 * 60),
                   "days": [_int(days[0] if days else 1, 1, 1, MAX_PERIOD),
                            _int(days[1], None, 1, MAX_PERIOD) if len(days) > 1 and days[1] else None],
                   "notify": texts(m.get("notify"), 120)}
        kept.append(module_)
    out = {"modules": kept, "period_days": _int(sampling.get("period_days"), 14, 1, MAX_PERIOD),
           "start": "next_day" if sampling.get("start") == "next_day" else "same_day"}
    if sampling.get("open_end"):
        out["open_end"] = True
    if isinstance(sampling.get("summary"), dict):
        out["summary"] = texts(sampling["summary"], SUMMARY_MAX)
    exit_ = sampling.get("exit") if isinstance(sampling.get("exit"), dict) else None
    if exit_:  # the name of the exit survey in the app and how long it stays open
        out["exit"] = {"name": texts(exit_.get("name"), 80), "days": _int(exit_.get("days"), EXIT_DAYS, 1, 90)}
    if sampling.get("invite_only"):
        out["invite_only"] = True
    return out


def copied_state(survey):
    """The schedule for a copy or a file: without the earlier sets of times, which belong to the persons of this one."""
    return clean(survey.sampling)


def fix_parts(survey):
    """Blocks of a module the schedule does not have (any more) belong to the entry survey; the exit survey stays."""
    keys = {m["key"] for m in modules(survey)} | {ENTRY, EXIT}
    for block in survey.blocks:
        if part_of(block) not in keys:
            block.settings = {**(block.settings or {}), "part": ENTRY}


def forget(survey_id, invites=False):
    """The persons of a deleted survey (lifecycle.delete; their entries go with the survey's participations);
    invites: also its invitation codes (the survey itself goes); otherwise the codes let go of their persons."""
    from app.models import keep_floor_before_bulk_delete

    keep_floor_before_bulk_delete(Enrolment, Enrolment.survey_id == survey_id)  # SQLite gives no id twice (S-01)
    if not invites:
        detach_invites(survey_id)
    db.session.execute(db.delete(Enrolment).where(Enrolment.survey_id == survey_id))
    from app.models import BrowserReceipt

    db.session.execute(db.delete(BrowserReceipt).where(BrowserReceipt.survey_id == survey_id,
                                                       db.or_(BrowserReceipt.scope.like("offline:%"),
                                                              BrowserReceipt.scope.like("diary-web:%"))))
    if invites:
        from app.models import DiaryInvite

        db.session.execute(db.delete(DiaryInvite).where(DiaryInvite.survey_id == survey_id))


def detach_invites(survey_id, persons=None):
    """The invitation codes of a study let go of persons who go in one statement past the mapper events (all of the
    study, or the ids `persons`): a code never names a person again who is gone, and its release ends with it
    (audit 2026-10-23, S-01). Call it before the persons are deleted, in the same transaction."""
    from app.models import DiaryInvite

    where = [DiaryInvite.survey_id == survey_id, DiaryInvite.enrolment_id.is_not(None)]
    if persons is not None:
        where.append(DiaryInvite.enrolment_id.in_(persons))
    db.session.execute(db.update(DiaryInvite).where(*where).values(enrolment_id=None, released_at=None)
                       .execution_options(synchronize_session=False))


def detach_dangling_invites():
    """Codes that still name a person who is gone, or someone who joined only after the code was used (a person
    deleted before codes let go of their persons, whose id SQLite gave to the next one): they let go of them. Changes
    nothing a second time; returns how many codes let go (audit 2026-10-23, S-01)."""
    from app.models import DiaryInvite

    fits = db.select(Enrolment.id).where(Enrolment.id == DiaryInvite.enrolment_id,
                                         Enrolment.survey_id == DiaryInvite.survey_id,
                                         Enrolment.created_at <= DiaryInvite.used_at).exists()
    done = db.session.execute(db.update(DiaryInvite).where(DiaryInvite.enrolment_id.is_not(None), ~fits)
                              .values(enrolment_id=None, released_at=None).execution_options(synchronize_session=False))
    return done.rowcount or 0


def whole_persons(responses):
    """For a deletion (app/erasure.py): the participations given, plus every other entry of the same persons, and the
    persons' rows. A person's deletion code deletes all of their answers (concepts/experiencesampling.md 6.4)."""
    ids = {r.enrolment_id for r in responses if getattr(r, "enrolment_id", None)}
    if not ids:
        return list(responses), []
    have = {r.id for r in responses}
    more = db.session.scalars(db.select(Response).where(Response.enrolment_id.in_(ids))).all()
    persons = db.session.scalars(db.select(Enrolment).where(Enrolment.id.in_(ids))).all()
    return list(responses) + [r for r in more if r.id not in have], persons


def sweep(now=None):
    """Hourly (lifecycle._rare_sweeps): persons who joined but never finished the entry part within PENDING_DAYS go,
    with what they began; persons none of whose entries is left (a retention period took them) go too. Commits by
    itself, only if something went."""
    from app import diary_access, erasure

    now = now or utcnow()
    old = db.session.scalars(db.select(Enrolment).where(Enrolment.status == Enrolment.PENDING,
                                                         Enrolment.created_at < now - timedelta(days=PENDING_DAYS))).all()
    gone, files, promoted = 0, [], 0
    for person in old:
        entries = _entries(person, light=True)
        if any(r.status == Response.COMPLETED for r in entries):
            # the entry part is done but the person's app never asked since: brought up to date here as the app
            # would, so the person is no longer read again every hour (code review 2026-10-15, E7)
            if refresh(db.session.get(Survey, person.survey_id), person, now, entries) != Enrolment.PENDING:
                promoted += 1
            continue
        if entries:
            # the way of a deletion (uncounted): the saved states, files, coding, seal chain and quotas follow the
            # begun entry part with its consent and demographics, not only the database (audit 2026-10-12, D-12)
            files.append(erasure.erase_again(db.session.get(Survey, person.survey_id), entries, reason="diary_pending"))
        else:
            diary_access.revoke(person)  # the browsers of its web page lose their access with it
            db.session.delete(person)  # its invitation code lets go of it (models._detach_invites)
        gone += 1
    empty = db.session.scalars(db.select(Enrolment).where(
        Enrolment.status != Enrolment.PENDING,
        ~db.select(Response.id).where(Response.enrolment_id == Enrolment.id).exists())).all()
    for person in empty:
        diary_access.revoke(person)
        db.session.delete(person)
        gone += 1
    let_go = detach_dangling_invites()  # left from deletions before codes let go of their persons (S-01)
    if gone or promoted or let_go:
        db.session.commit()
        for after in files:
            after()
    return gone


# --- the researchers' pages -------------------------------------------------------------------------------------

def _survey(survey_id, need="viewer"):
    from app import experimental
    from app.surveys import get_survey

    survey = get_survey(survey_id, need=need)
    if survey.sampling is None:
        abort(404)
    experimental.require("diary", survey=survey)
    return survey


@bp.route("/new", methods=["GET", "POST"])
@login_required
def new():
    """A new diary study: the consent and the demographics as entry part, one module "Moment" with one question."""
    from app import experimental, flow, kind_access, surveys
    from app.items.consent import ensure_consent_block
    from app.lang import current
    from app.wizard import add_demographics

    experimental.require("diary")
    refused = kind_access.creation_refused(current_user, "diary")
    if refused:
        return refused
    if request.method == "POST":
        lang = request.form.get("language") or ("en" if current() == "en" else "de")
        lang = lang if lang in surveys.LANGUAGE_NAMES else "de"
        title = " ".join(request.form.get("title", "").split())[:300] or de_en("Tagebuchstudie", "Diary survey")
        chosen = request.form.get("template", "")
        chosen = chosen if chosen in DIARY_TEMPLATES else None  # the card "Aus einer Vorlage"
        survey = Survey(title=title, created_by_id=current_user.id, languages=[lang], default_language=lang,
                        sampling=from_template(chosen, lang) if chosen else defaults(lang), repeat_marker=False,
                        setup_step="studienplan")  # on into the setup wizard (app/wizard.py, Ralf 2026-10-27)
        from app import study_plan

        survey.study_plan = study_plan.start_plan(request.form, lang)  # the country from the language (DDI)
        _project_fields(survey, request.form)
        if "join" in request.form:  # the question "Beitreten" of the blank form; a template joins with the shared code
            survey.sampling = {**survey.sampling, **join_settings(request.form, survey.sampling)}
        db.session.add(survey)
        surveys._add_member(survey, current_user, current_user.name)
        ensure_consent_block(survey, current_user)
        # two ticks on the page, both on (Ralf, 2026-10-11): age and gender once in the entry survey, and an exit
        # survey with one open question; the short survey "Moment" asks only how the person feels
        if request.form.get("demographics") or chosen:
            add_demographics(survey)
        block = Block(position=len(survey.blocks), titles={lang: "Moment"}, settings={"part": "m1"})
        block.items = [_mood_item(survey, lang)]
        survey.blocks.append(block)
        if request.form.get("exit") or chosen:
            closing = Block(position=len(survey.blocks), titles={lang: _EXIT_TITLE.get(lang, _EXIT_TITLE["en"])},
                            settings={"part": EXIT})
            closing.items = [_exit_item(survey, lang)]
            survey.blocks.append(closing)
        db.session.flush()
        flow.normalize(survey)
        codes = make_invites(survey, _int(request.form.get("persons"), 20, 1, 500)) if invite_only(survey) else []
        db.session.commit()
        if codes:
            flash(de_en("{n} persönliche Codes angelegt; Sie finden sie mit ihren QR-Codes im Reiter „Datenerhebung“.",
                        "{n} personal codes created; you find them with their QR codes on the tab “Data collection”.",
                        n=len(codes)), "success")
        if chosen:
            flash(de_en("Die Tagebuchstudie „{name}“ ist angelegt, mit Eingangsbefragung, der Kurzbefragung „Moment“ und "
                        "Abschlussbefragung; Zeitplan und Fragen lassen sich ändern.",
                        "The diary survey “{name}” is created, with entry survey, the short survey “Moment” and exit survey; "
                        "you can change the schedule and the questions.",
                        name=diary_templates()[chosen][0]), "success")
        else:
            flash(de_en("Die Tagebuchstudie ist angelegt: Eingangsbefragung, Kurzbefragung „Moment“ und Abschlussbefragung "
                        "stehen im Reiter „Fragebogen“ je für sich.",
                        "The diary survey is created: entry survey, short survey “Moment” and exit survey each stand on "
                        "their own on the tab “Questionnaire”."), "success")

        return redirect(url_for("wizard.step", survey_id=survey.id, key="studienplan"))
    from app import wizard

    return render_template("diary/new.html", languages=surveys.LANGUAGE_NAMES, starts=wizard.starts("tagebuch"),
                           start=wizard.start_of(), templates=diary_templates(), bolger=BOLGER,
                           steps=wizard.steps("diary"), index=0, survey=None, form=request.form)


def _project_fields(survey, form):
    """Description, project, keywords and topic of the first step (the questionnaire's fields, surveys.apply_project),
    without its check of the title: a diary survey or guide gets a title of its own when left empty."""
    from werkzeug.datastructures import MultiDict

    from app import surveys

    fields = MultiDict(form)
    fields["title"] = survey.title
    surveys.apply_project(survey, fields)


def _mood_item(survey, lang):
    """The question of the first module: how the person feels right now (a rating), in the survey's language (German,
    else English: the researchers translate it on the tab "Übersetzung" like every other text)."""
    from app import items
    from app.editor import _unique_code
    from app.models import Item

    kind = items.get("rating")
    texts = {**kind.default_texts(lang),
             "question": {"de": "Wie fühlen Sie sich gerade?", "de-du": "Wie fühlst du dich gerade?"}.get(
                 lang, "How do you feel right now?")}
    return Item(type="rating", answer_mode="optional", code=_unique_code(survey, "M"), config=kind.default_config(),
                texts={lang: texts})


_EXIT_TITLE = {"de": "Abschluss", "de-du": "Abschluss", "en": "Closing"}


def _exit_item(survey, lang):
    """The question of a new exit survey: an open one for anything the person wants to add, optional."""
    from app import items
    from app.editor import _unique_code
    from app.models import Item

    kind = items.get("text_input")
    question = {"de": "Möchten Sie uns zum Abschluss noch etwas mitteilen, etwa wie die Tage mit der Studie für Sie waren?",
                "de-du": "Möchtest du uns zum Abschluss noch etwas mitteilen, etwa wie die Tage mit der Studie für dich waren?"}.get(
        lang, "Is there anything else you would like to tell us, for example how the days with the study were for you?")
    return Item(type="text_input", answer_mode="optional", code=_unique_code(survey, "A"),
                config={**kind.default_config(), "multiline": True}, texts={lang: {**kind.default_texts(lang), "question": question}})


def take_over_sources(survey, user):
    """The questionnaires whose blocks this diary study may take over as entry or exit survey (decision of
    2026-10-11): questionnaires the person may take out (access.may_copy_out: "Bearbeiten" or "Admin", as for
    duplicating; audit 2026-10-18, AUTHZ-05), newest first."""
    from app import access

    rows = db.session.scalars(db.select(Survey).filter(Survey.id.in_(access.survey_ids(user)), Survey.id != survey.id)
                              .order_by(Survey.id.desc()).limit(200))
    return [s for s in rows if s.kind == "survey" and access.may_copy_out(user, s)]


def take_over(source, target, part, user):
    """Copy the blocks of a questionnaire into a part of a diary study, each with its questions, after the blocks
    the part has; without the consent (the diary study has its own). Questions of an experimental type the person
    does not have stay behind, as do conditions on questions of the source and the flow (branches, randomisers).
    Returns (blocks copied, notes for the researcher)."""
    from app import constructs, duplicate, flow

    for block in source.blocks:
        for item in block.items:
            if not constructs.permitted_copy(item, target, user):
                raise ValueError(constructs.permission_notice())

    from app import edit_guard

    used = edit_guard.taken_codes(target)  # read under the target's row
    made, left, notes = 0, [], []
    for block in sorted(source.blocks, key=lambda b: b.position):
        items = [i for i in block.items if i.type != "consent"]
        if not items:
            continue
        twin = Block(position=len(target.blocks), titles={l: t for l, t in (block.titles or {}).items() if l in target.languages}
                     or {target.default_language: target.next_block_title()}, show_title=block.show_title,
                     randomize_items=block.randomize_items,
                     settings={**({"demographics": True} if (block.settings or {}).get("demographics") else {}), "part": part})
        for item in sorted(items, key=lambda i: i.position):
            if duplicate._locked(item, user):
                left.append(item.code)
                continue
            other = duplicate.item_copy(item, target, used, user=user)
            other.position = len(twin.items)
            twin.items.append(other)
            notes += [n for n in (other.media_note, other.cond_note) if n and n not in notes]
        if twin.items:
            target.blocks.append(twin)
            made += 1
    if left:
        notes.append(de_en("Nicht übernommen, weil der Elementtyp für Sie nicht freigeschaltet ist: {codes}.",
                           "Not copied, as the element type is not switched on for you: {codes}.", codes=", ".join(left)))
    if made:
        db.session.flush()
        flow.normalize(target)
    return made, notes


def _parse_times(text):
    out = []
    for part in re.split(r"[,;\s]+", text or ""):
        m = TIME_RE.match(part.strip())
        if m:
            out.append(f"{int(m.group(1)):02d}:{m.group(2)}")
    return sorted(set(out))[:MAX_TIMES]


WINDOW_RE = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)\s*[-–]\s*([01]?\d|2[0-4]):([0-5]\d)")


def _random_trigger(per_day, windows, min_gap, weekdays):
    """A random trigger from what the form or a file says: prompts a day, windows "09:00-12:00, 14:00-21:00" (24:00
    is the end of the day), minimum gap in minutes, weekdays."""
    found = []
    for m in WINDOW_RE.finditer(windows or ""):
        a = f"{int(m.group(1)):02d}:{m.group(2)}"
        b = "00:00" if m.group(3) == "24" else f"{int(m.group(3)):02d}:{m.group(4)}"
        if a != b:
            found.append([a, b])
    return {"t": "random", "per_day": _int(per_day, 3, 1, MAX_PER_DAY), "windows": found[:6] or [["09:00", "21:00"]],
            "min_gap": _int(min_gap, 60, 0, 12 * 60),
            "weekdays": sorted({int(d) for d in weekdays or [] if d in WEEKDAYS}) or [1, 2, 3, 4, 5, 6, 7]}


def _int(value, default, low, high):
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _times_of(mods):
    return {m["key"]: (m.get("trigger") or {}) for m in mods if (m.get("trigger") or {}).get("t") in SCHEDULED}


def _save(survey, form, lang):
    """The form of the tab "Zeitplan": the study period, the modules, and the part of each block. Once the study is
    locked, only times, weekdays, expiry and texts change; a change of the times keeps the old ones in "earlier"."""
    fixed = locked(survey)
    sampling = dict(survey.sampling or {})
    if not fixed:
        sampling["period_days"] = _int(form.get("period_days"), 14, 1, MAX_PERIOD)
        sampling["start"] = "next_day" if form.get("start") == "next_day" else "same_day"
        # "ohne Ende" (open_end): the persons take part until the researchers end it; a form without the field keeps
        # what is stored
        if "period_kind" in form:
            sampling.pop("open_end", None)
            if form.get("period_kind") == "open":
                sampling["open_end"] = True
    if "summary" in form:  # the sentence about the study in the app, changeable at any time
        sampling["summary"] = {**(sampling.get("summary") or {}), lang: form.get("summary", "").strip()[:SUMMARY_MAX]}
    kept = []
    for m in modules(survey):
        key = m["key"]
        p = f"{key}_"
        old = m.get("trigger") or {}
        wanted = form.get(p + "trigger")
        t = old.get("t") or "fixed" if fixed else wanted if wanted in TRIGGERS else "fixed"
        label = old.get("label")
        weekdays = sorted({int(d) for d in form.getlist(p + "weekdays") if d in WEEKDAYS})
        trigger = ({"t": "self", "label": {**(label if isinstance(label, dict) else {}), lang: form.get(p + "label", "").strip()[:80]},
                    "max_per_day": _int(form.get(p + "max_per_day"), 5, 0, 50)} if t == "self"
                   else _random_trigger(form.get(p + "per_day"), form.get(p + "windows"), form.get(p + "min_gap"),
                                        form.getlist(p + "weekdays")) if t == "random"
                   else {"t": "fixed", "times": _parse_times(form.get(p + "times")), "weekdays": weekdays or [1, 2, 3, 4, 5, 6, 7]})
        last = form.get(p + "day_to", "").strip()
        days = (m.get("days") or [1, None]) if fixed else \
            [_int(form.get(p + "day_from"), 1, 1, MAX_PERIOD), _int(last, None, 1, MAX_PERIOD) if last else None]
        kept.append({"key": key, "name": {**(m.get("name") or {}), lang: form.get(p + "name", "").strip()[:80] or key},
                     "trigger": trigger, "expires": _int(form.get(p + "expires"), 60, 0, 24 * 60),
                     "reminder": _int(form.get(p + "reminder"), 0, 0, 24 * 60), "days": days,
                     "notify": {**(m.get("notify") or {}), lang: form.get(p + "notify", "").strip()[:120]}})
    if survey.status != Survey.DRAFT and _times_of(kept) != _times_of(modules(survey)):
        sampling["earlier"] = _with_earlier(sampling.get("earlier"), modules(survey), kept)
    sampling["modules"] = kept
    # the exit questionnaire (name in the app, days it stays open) and "nur mit Einladung": changeable at any time
    sampling["exit"] = {"name": {**((sampling.get("exit") or {}).get("name") or {}), lang: form.get("exit_name", "").strip()[:80]},
                        "days": _int(form.get("exit_days"), EXIT_DAYS, 1, 90)}
    if "join" in form or "invite_only" in form:  # how people join is on "Datenerhebung" since T5 (2026-10-17)
        sampling.update(join_settings(form, sampling))
    if not fixed:
        keys = {m["key"] for m in kept} | {ENTRY, EXIT}
        for block in survey.blocks:
            part = form.get(f"part_{block.id}")
            if part in keys:
                block.settings = {**(block.settings or {}), "part": part}
    survey.sampling = sampling


def _with_earlier(earlier, current, new, now=None):
    """survey.sampling["earlier"] after the times change from the modules `current` to `new` (review 2026-10-15, G2).
    A set of times replaced within MERGE_SECONDS of its start (the autosave of the tab "Zeitplan" while typing) is
    not kept: the new set holds from its start, and a return to the set before ends the excursion altogether. Past
    MAX_EARLIER the shortest-lived set goes, never the first: it holds from the start of the study, and dropping it
    let a later set count for the first days of every person."""
    now = (now or utcnow()).replace(microsecond=0)
    earlier = [e for e in earlier or [] if isinstance(e, dict) and e.get("until")]
    try:
        short = bool(earlier) and now - datetime.fromisoformat(earlier[-1]["until"]) < timedelta(seconds=MERGE_SECONDS)
    except (TypeError, ValueError):
        short = False
    if not short:
        earlier.append({"until": now.isoformat(), "modules": current})
    elif _times_of(earlier[-1].get("modules") or []) == _times_of(new):
        earlier.pop()

    def life(i):
        try:
            return datetime.fromisoformat(earlier[i]["until"]) - datetime.fromisoformat(earlier[i - 1]["until"])
        except (TypeError, ValueError):
            return timedelta(0)
    while len(earlier) > MAX_EARLIER:
        del earlier[min(range(1, len(earlier)), key=life)]
    return earlier


def join_settings(form, sampling=None):
    """How people join, from the form of "Neue Tagebuchstudie" or "Zeitplan": "join" = "shared" (one code for
    everyone, anonymous) or "personal" (a code of its own per person, which connects again after a release), and
    "rejoin" for the personal codes (rejoin_mode, always "release" since 2026-10-15). Forms of before send only the
    tick "invite_only"."""
    personal = form.get("join") == "personal" if "join" in form else bool(form.get("invite_only"))
    return {"invite_only": personal, "rejoin": _rejoin_of(sampling)}


def _new_key(survey):
    used = {m["key"] for m in modules(survey)}
    n = 1
    while f"m{n}" in used:
        n += 1
    return f"m{n}"


def _step(survey, name):
    """Begin a step for "Rückgängig" around a change of the structure (adding or removing a module)."""
    from app import undo

    step = undo.begin(survey)
    return lambda: step.done(name, page="settings", node="diary")


@bp.route("/<sid:survey_id>/schedule", methods=["GET", "POST"])
@login_required
@autosave.aware
def schedule(survey_id):
    """The tab "Zeitplan". The form saves by itself (data-autosave-form), with "Rückgängig" as on the settings page
    (section "diary", edit_guard.SETTINGS)."""
    from app import edit_guard, surveys

    survey = _survey(survey_id, need="editor" if request.method == "POST" else "viewer")
    lang = survey.default_language
    if request.method == "POST":
        conflict = surveys._settings_conflict(survey, "diary")
        _save(survey, request.form, lang)
        if conflict:
            return edit_guard.show_again(lambda: _page(survey))
        surveys._settings_saved(survey, "diary")
        db.session.commit()
        if autosave.wanted():  # the week preview below the form and the prompts a day, drawn anew: the saved times at once
            autosave.part("diary-week", render_template("diary/_week.html", **_week_context(survey)))
            for m in modules(survey):  # "Etwa n Abfragen am Tag" in the form
                autosave.part(f"diary-per-day-{m['key']}", render_template("diary/_per_day.html", m=m, per_day=per_day))
        flash(de_en("Zeitplan gespeichert.", "Schedule saved."), "success")
        return redirect(url_for("diary.schedule", survey_id=survey.id))
    return _page(survey)


@bp.route("/<sid:survey_id>/join-mode", methods=["POST"])
@login_required
@autosave.aware
def join_mode(survey_id):
    """How people join (one shared code or personal codes), from the block "Beitreten" of "Datenerhebung"; changeable
    at any time like on "Zeitplan" before (concept "Befragungstypen im Vergleich" 2026-10-17, T5)."""
    survey = _survey(survey_id, need="editor")
    survey.sampling = {**(survey.sampling or {}), **join_settings(request.form, survey.sampling)}
    db.session.commit()
    flash(de_en("Beitreten gespeichert.", "Joining saved."), "success")
    return redirect(url_for("surveys.field", survey_id=survey.id) + "#links")


REVIEW_PAGE = 20  # offline review cases shown on one page


@bp.route("/<sid:survey_id>/offline-reviews", methods=["GET", "POST"])
@login_required
def offline_review(survey_id):
    """Preserved originals, visible to data editors only; correct compatible cases before accepting them."""
    from app import offline_answers, runtime
    from app.models import Item

    survey = _survey(survey_id, need="editor")
    pending, validation_errors = {}, {}
    if request.method == "POST":
        rid = request.form.get("response", type=int)
        row = db.session.get(Response, rid) if rid else None
        if row is None or row.survey_id != survey.id or row.status != offline_answers.REVIEW:
            abort(404)
        original = (row.occasion or {}).get("review") or {}
        note = request.form.get("clarification", "").strip()
        pending[row.id] = {key: request.form.getlist(key) for key in request.form if key.startswith("i")}
        can_accept = (request.form.get("action") == "check" or bool(note)) and len(note) <= 2000
        if not can_accept:
            flash(de_en("Bitte dokumentieren Sie die Klärung (höchstens 2000 Zeichen).",
                        "Please document the clarification (at most 2000 characters)."), "error")
        receipt = offline_answers.review_page(survey, row)
        items = offline_answers.frozen_items(survey, receipt) if receipt else []
        if can_accept and receipt is None:
            flash(de_en("Ohne gespeicherten Seitenstand lässt sich diese Antwort nicht übernehmen: Der Fragebogen hat "
                        "sich seit dem Senden geändert. Die Originalantwort bleibt zur Prüfung erhalten.",
                        "Without a kept page this answer cannot be accepted: the questionnaire has changed since it was "
                        "sent. The original answer is retained for review."), "error")
        elif can_accept and (not items or not offline_answers.compatible(survey, items)):
            flash(de_en("Diese Fassung passt nicht zum aktuellen Fragebogen. Die Originalantwort bleibt zur Prüfung erhalten.",
                        "This version does not fit the current questionnaire. The original answer is retained for review."), "error")
        elif can_accept:
            from werkzeug.datastructures import MultiDict

            prefixes = [f"i{item.id}" for item in items]
            fields = MultiDict([(key, v) for key in request.form if any(
                key == p or key.startswith(p + "_") for p in prefixes) for v in request.form.getlist(key)])
            result = runtime.evaluate(items, fields, receipt["language"], nudge_acknowledged=True)
            validation_errors[row.id] = {str(k): str(v) for k, v in result.errors.items()}
            if result.errors:
                flash(de_en("Die Antworten sind noch ungültig; das Original bleibt erhalten.",
                            "The answers are still invalid; the original is retained."), "error")
            elif request.form.get("action") == "check":
                flash(de_en("Die vorgeschlagenen Werte sind gültig. Dokumentieren Sie die Klärung und übernehmen Sie die Werte.",
                            "The proposed values are valid. Document the clarification and accept the values."), "success")
            else:
                row.answers = {str(k): v for k, v in result.answers.items()}
                row.status = Response.COMPLETED
                row.completed_at = row.last_active_at
                row.occasion = {**row.occasion, "review": {**original, "resolved_at": utcnow().isoformat(),
                                                          "resolved_by": current_user.id, "clarification": note,
                                                          "accepted_form": fields.to_dict(flat=False)}}
                db.session.commit()
                flash(de_en("Die geprüfte Antwort wurde übernommen.", "The reviewed answer has been accepted."), "success")
        if row.status == Response.COMPLETED:
            return redirect(url_for("diary.offline_review", survey_id=survey.id,
                                    page=request.args.get("page", type=int) or None))
    # a page of REVIEW_PAGE cases at a time: the cases of a whole study at once could take the worker's memory and
    # time (audit 2026-10-15, NB-3)
    open_cases = db.select(Response).filter_by(survey_id=survey.id, status=offline_answers.REVIEW)
    total = db.session.scalar(db.select(db.func.count()).select_from(open_cases.subquery()))
    pages = max(1, -(-total // REVIEW_PAGE))
    page_no = min(max(1, request.args.get("page", 1, type=int)), pages)
    rows = list(db.session.scalars(open_cases.order_by(Response.received_at, Response.id)
                                   .offset((page_no - 1) * REVIEW_PAGE).limit(REVIEW_PAGE)))
    labels, fields, definitions, unpageable = {}, {}, {}, set()
    for row in rows:
        receipt = offline_answers.review_page(survey, row)
        if receipt is None:
            unpageable.add(row.id)
        receipt = receipt or {}
        names = {}
        definitions[row.id] = []
        for definition in receipt.get("items") or []:
            item = offline_answers.frozen_items(survey, {"items": [definition]})[0]
            names[f"i{item.id}"] = f"{item.code} · {item.text('question', row.language)}"
            current = db.session.get(Item, item.id)
            if current is not None and current.block.survey_id != survey.id:
                current = None
            options = []
            for group in ("options", "rows", "statements", "pairs", "levels"):
                for option in item.cfg.get(group) or []:
                    if not isinstance(option, dict):
                        continue
                    labels_of = item.text(group, row.language) or {}
                    oid = option.get("id", "")
                    options.append({"label": labels_of.get(oid, "") if isinstance(labels_of, dict) else "",
                                    "id": oid, "code": option.get("code", "")})
            definitions[row.id].append({"code": item.code, "question": item.text("question", row.language),
                "type": item.type, "options": options, "shape": item.kind.answer_shape(item),
                "current_question": current.text("question", row.language) if current else None,
                "current_shape": current.kind.answer_shape(current) if current else None,
                "bounds": list(item.kind.bounds(item)) if item.type == "rating" else
                          ([1, item.cfg["points"]] if item.type in ("likert", "semantic") else None),
                "scale_labels": list(enumerate(item.kind.labels(item, row.language), start=1)) if item.type == "likert" else []})
            for key in ("rows", "statements", "pairs", "options"):
                for n, field in enumerate(item.cfg.get(key) or []):
                    names[f"i{item.id}_{field['id']}"] = f"{item.code} · " + str(
                        (item.text(key, row.language) or {}).get(field["id"]) or field.get("code") or n + 1)
        labels[row.id] = names
        fields[row.id] = {key: value if isinstance(value, list) else [value]
                          for key, value in ((row.occasion or {}).get("review", {}).get("form") or {}).items()}
        for key in names:
            fields[row.id].setdefault(key, [""])
        if row.id in pending:
            fields[row.id].update(pending[row.id])
    return render_template("diary/offline_review.html", survey=survey, rows=rows, labels=labels, fields=fields,
                           definitions=definitions, validation_errors=validation_errors, part_label=part_label,
                           unpageable=unpageable,
                           page_no=page_no, pages=pages, total=total)


@bp.route("/<sid:survey_id>/schedule/module", methods=["POST"])
@login_required
def add_module(survey_id):
    """A new module (by the participants, five a day), as long as the structure may change."""
    from app import undo

    survey = _survey(survey_id, need="editor")
    if not locked(survey) and len(modules(survey)) < MAX_MODULES:
        lang = survey.default_language
        done = _step(survey, undo.both("Zeitplan: Kurzbefragung hinzugefügt", "Schedule: short survey added"))
        key = _new_key(survey)
        survey.sampling = {**survey.sampling, "modules": modules(survey) + [
            {"key": key, "name": {lang: "Meldung" if lang.startswith("de") else "Report"},
             "trigger": {"t": "self", "label": {}, "max_per_day": 5}, "expires": 0, "days": [1, None], "notify": {}}]}
        done()
        db.session.commit()
    return redirect(url_for("diary.schedule", survey_id=survey.id) + "#modules")


@bp.route("/<sid:survey_id>/schedule/module/<key>/remove", methods=["POST"])
@login_required
def remove_module(survey_id, key):
    """Remove a module; its blocks go to the entry part. Only while the structure may change."""
    from app import undo

    survey = _survey(survey_id, need="editor")
    if not locked(survey) and module(survey, key) is not None:
        done = _step(survey, undo.both("Zeitplan: Kurzbefragung entfernt", "Schedule: short survey removed"))
        survey.sampling = {**survey.sampling, "modules": [m for m in modules(survey) if m["key"] != key]}
        fix_parts(survey)
        done()
        db.session.commit()
    return redirect(url_for("diary.schedule", survey_id=survey.id) + "#modules")


@bp.route("/<sid:survey_id>/block/<int:block_id>/part", methods=["POST"])
@login_required
def block_part(survey_id, block_id):
    """The tab "Fragebogen": put a block into another part (the select in its head), one step for "Rückgängig"."""
    from app import undo

    survey = _survey(survey_id, need="editor")
    block = next((b for b in survey.blocks if b.id == block_id), None)
    if block is None:
        abort(404)
    part = request.form.get("part", "")
    if survey.is_editable and part in part_keys(survey) and part != part_of(block):
        step = undo.begin(survey)
        if set_part(survey, block, part):
            step.done(undo.both("Block in „{part}“ verschoben", "Block moved to “{part}”", part=part_label(part, survey)),
                      block=block.id)
            db.session.commit()
        else:
            flash(de_en("Die Einwilligung bleibt in der Eingangsbefragung.", "The consent stays in the entry survey.")
                  if not locked(survey) else de_en("Die Tagebuchstudie läuft oder hat echte Antworten: Die Teile sind fest.",
                                                   "The diary survey runs or has real answers: the parts are fixed."), "warning")
    return redirect(url_for("editor.index", survey_id=survey.id) + f"#block-{block.id}")


@bp.route("/<sid:survey_id>/take-over", methods=["POST"])
@login_required
def take_over_page(survey_id):
    """The tab "Fragebogen": copy the blocks of a questionnaire into the entry or the exit survey."""
    from app import constructs, undo
    from app.surveys import get_survey

    from app import access

    # the source first: the access log notes the survey checked last, and that is the one changed; taking blocks out
    # needs what duplicating needs (access.may_copy_out; audit 2026-10-18, AUTHZ-05)
    source_id = request.form.get("source", type=int) or 0
    found = db.session.get(Survey, source_id)
    source = get_survey(source_id, need="viewer" if found is not None and access.showcase_only(current_user, found)
                        else "editor")
    survey = _survey(survey_id, need="editor")
    part = request.form.get("part", "")
    if not survey.is_editable or locked(survey) or part not in (ENTRY, EXIT) or source.kind != "survey":
        abort(400)
    if any(not constructs.permitted_copy(item, survey, current_user) for block in source.blocks for item in block.items):
        flash(constructs.permission_notice(), "error")
        return redirect(url_for("editor.index", survey_id=survey.id) + f"#part-{part}")
    step = undo.begin(survey)
    made, notes = take_over(source, survey, part, current_user)
    if made:
        step.done(undo.both("„{title}“ in „{part}“ übernommen", "“{title}” copied into “{part}”", title=source.title[:60],
                            part=part_label(part, survey)))
        db.session.commit()
        flash(" ".join([de_en("{n} Blöcke aus „{title}“ stehen jetzt in der {part}.", "{n} blocks from “{title}” are now in the {part}.",
                              n=made, title=source.title, part=part_label(part, survey))] + notes), "success")
    else:
        db.session.rollback()
        flash(de_en("„{title}“ hat außer der Einwilligung keine Fragen, die sich übernehmen lassen.",
                    "“{title}” has no questions besides the consent that can be copied.", title=source.title), "warning")
    return redirect(url_for("editor.index", survey_id=survey.id) + f"#part-{part}")


@bp.route("/<sid:survey_id>/person/<int:person_id>/<action>", methods=["POST"])
@login_required
def person_action(survey_id, person_id, action):
    """Pause, resume or end the study of one person (role "Bearbeiten")."""
    survey = _survey(survey_id, need="editor")
    person = db.session.get(Enrolment, person_id)
    if person is None or person.survey_id != survey.id or action not in ("pause", "resume", "end"):
        abort(404)
    if action == "end" and not survey.can(current_user, "admin"):
        abort(403)
    from app import diary_access

    if diary_access.lock_person(person) is None:
        abort(404)
    refresh(survey, person)
    done = {"pause": pause, "resume": resume, "end": end}[action](person)
    shown_pid = person.pid[-6:]
    db.session.commit()
    if done:
        flash({"pause": de_en("{pid} ist pausiert: keine Abfragen, bis Sie fortsetzen.", "{pid} is paused: no prompts until you resume."),
               "resume": de_en("{pid} geht weiter, ab jetzt wieder mit Abfragen.", "{pid} goes on, with prompts again from now."),
               "end": de_en("Für {pid} ist die Tagebuchstudie beendet.", "The diary survey has ended for {pid}.")}[action].format(pid=shown_pid),
              "success")
    return redirect(url_for("diary.schedule", survey_id=survey.id) + "#persons")


@bp.route("/<sid:survey_id>/end-all", methods=["POST"])
@login_required
def end_all(survey_id):
    """End the study for every person who is still in it (role "Admin": it cannot be taken back)."""
    survey = _survey(survey_id, need="admin")
    n = 0
    from app import diary_access

    for person in db.session.scalars(db.select(Enrolment).filter_by(survey_id=survey.id).order_by(Enrolment.id)).all():
        if diary_access.lock_person(person) is None:
            continue
        refresh(survey, person)
        n += end(person)
    db.session.commit()
    flash(de_en("Die Tagebuchstudie ist für {n} Personen beendet. Neue Beitritte beenden Sie auf „Datenerhebung“.",
                "The diary survey has ended for {n} people. You stop new joins on “Data collection”.", n=n), "success")
    return redirect(url_for("diary.schedule", survey_id=survey.id) + "#persons")


@bp.route("/<sid:survey_id>/invites", methods=["POST"])
@login_required
def add_invites(survey_id):
    """New personal invitation codes (each joins one person)."""
    survey = _survey(survey_id, need="editor")
    new = make_invites(survey, _int(request.form.get("n"), 10, 1, 500))
    db.session.commit()
    flash(de_en("{n} Einladungscodes angelegt.", "{n} invitation codes created.", n=len(new)), "success")
    return redirect(url_for("surveys.field", survey_id=survey.id) + "#invites")


@bp.route("/<sid:survey_id>/invites/<code>/release", methods=["POST"])
@login_required
def release(survey_id, code):
    """Let a used personal code connect its person's participation once more (rejoin "release"), e.g. on a new phone,
    or take that back ("on" = 0). Whoever types the code next within RELEASE_TIME takes the participation over, so
    only "Bearbeiten"."""
    survey = _survey(survey_id, need="editor")
    on = request.form.get("on") != "0"
    if not release_invite(survey, code, on):
        abort(404)
    db.session.commit()
    if on:
        flash(de_en("Der Code {code} verbindet die Teilnahme in den nächsten {h} Stunden einmal wieder. Das bisherige "
                    "Gerät verliert dann den Zugang.",
                    "The code {code} connects the participation once more within the next {h} hours. The earlier "
                    "device then loses access.", code=f"{survey.code}-{code}", h=int(RELEASE_TIME.total_seconds() // 3600)),
              "success")
    else:
        flash(de_en("Freigabe für {code} zurückgenommen.", "Release of {code} taken back.", code=f"{survey.code}-{code}"),
              "success")
    return redirect(url_for("surveys.field", survey_id=survey.id) + "#invites")


@bp.route("/<sid:survey_id>/test-codes", methods=["POST"])
@login_required
def renew_test_codes(survey_id):
    """New test codes (with and without fast forward): the earlier ones lead nowhere from now; test persons who
    joined with them stay (audit 2026-10-11, N2, decision 6a)."""
    import secrets as random_codes

    survey = _survey(survey_id, need="editor")
    survey.sampling = {**survey.sampling, "test_salt": random_codes.token_hex(8)}
    db.session.commit()
    flash(de_en("Neue Testcodes erzeugt; die bisherigen gelten nicht mehr.",
                "New test codes generated; the earlier ones no longer work."), "success")
    return redirect(url_for("surveys.field", survey_id=survey.id) + "#links")


def show_test_codes(survey, user):
    """Whether the page shows the test codes: only to "Bearbeiten", and never on an example survey, where they would
    let anyone add test persons to someone else's study (audit 2026-10-11, N3, decision 6a)."""
    from app import access

    return survey.can(user, "editor") and not access.showcased(survey, user)


@bp.route("/<sid:survey_id>/invites/print")
@login_required
def print_invites(survey_id):
    """The unused invitation codes with their QR codes, to print and cut (one card per person). Each joins a person,
    so only "Bearbeiten" sees them."""
    survey = _survey(survey_id, need="editor")
    cards = [(f"{survey.code}-{i['c']}", qr_svg(join_url(survey, f"{survey.code}-{i['c']}"), mask=0))
             for i in invites(survey) if not i.get("used")]
    return render_template("diary/invites.html", survey=survey, cards=cards, lang=survey.default_language,
                           rejoin_mode=rejoin_mode(survey))


def join_context(survey):
    """What the block "Beitreten" of "Datenerhebung" shows (diary/_join.html): the join link with its QR code, the test
    codes for those who may see them (computed only then: nothing of them in the page otherwise) and the invitation
    codes. On "Datenerhebung" since 2026-10-12, where a questionnaire has its participation link."""
    codes = {}
    if show_test_codes(survey, current_user):
        test, fast = test_code(survey), test_code(survey, fast=True)
        codes = {"test_code": test, "test_qr": qr_svg(join_url(survey, test)), "fast_code": fast,
                 "fast_qr": qr_svg(join_url(survey, fast))}
    return {"join_url": join_url(survey), "qr": qr_svg(join_url(survey)), "show_tests": bool(codes), **codes,
            "invites": invites(survey), "rejoin_mode": rejoin_mode(survey), "may_edit": survey.can(current_user, "editor"),
            "personal": bool((survey.sampling or {}).get("invite_only")), **persons_context(survey)}


def persons_context(survey):
    """The block "Teilnehmende" (diary/_persons.html) on "Datenerhebung", beside "Beitreten": the persons with status,
    period, entries and answered prompts (moved from "Zeitplan", concept "Befragungstypen im Vergleich" 2026-10-17,
    T5). The counts, not the individual persons, for viewers (AZ-61, audit 2026-10-13).
    In pages of the database (app/tables.py), with the entries of the page's persons only, and without writing: the
    page read every person with every entry and its answers, 1.5 s and 1.4 MB at 1240 persons, and stored the status
    of persons whose entry part had been completed (performance audit 2026-10-18, PERF-05). That status is shown as
    it is now and stored where it changes: by the app's requests and the housekeeping (sweep)."""
    from app.tables import ROW_SIZES, Listing

    review_count = db.session.scalar(db.select(db.func.count(Response.id)).filter_by(
        survey_id=survey.id, status="review")) or 0
    listing = Listing("dp", {"id": [Enrolment.id]}, "id", anchor="persons", sizes=ROW_SIZES)
    persons = [row[0] for row in listing.rows(db.select(Enrolment).filter_by(survey_id=survey.id))]
    by_person = _entries_by_person(survey, with_answers=True, persons=[e.id for e in persons])
    # The counts, not the individual persons, for viewers (AZ-61, audit 2026-10-13).
    persons_shown = survey.can(current_user, "editor")
    rows = []
    with db.session.no_autoflush:
        for e in persons:
            entries = by_person.get(e.id, [])
            status = refresh(survey, e, entries=entries)
            answered, due, in_review = compliance(survey, e, entries=entries, with_review=True)
            flags, last = person_quality(e, status, entries, answered, due, local_today(e))
            rows.append({"id": e.id, "fast": bool((e.settings or {}).get("clock")),
                         "pid": e.pid, "source": e.source, "status": status, "first": e.first_day, "last": e.last_day,
                         "entries": sum(1 for r in entries if r.status == Response.COMPLETED), "answered": answered,
                         "due": due, "in_review": in_review, "flags": flags, "last_entry": last})
            if e in db.session.dirty:
                db.session.expire(e)  # what refresh changed is shown, not stored by a GET
    # "Für alle beenden" while anyone is still in the study, on every page
    still_in = db.session.scalar(db.select(Enrolment.id).filter(
        Enrolment.survey_id == survey.id, Enrolment.status.in_((Enrolment.ACTIVE, Enrolment.PAUSED, Enrolment.PENDING)),
        db.or_(Enrolment.last_day.is_(None), Enrolment.last_day >= utcnow().date() - timedelta(days=1))).limit(1))
    return {"persons_shown": persons_shown, "review_count": review_count, "diary_rows": rows, "diary_list": listing,
            "may_edit": survey.can(current_user, "editor"), "diary_still_in": still_in is not None,
            "flagged": sum(1 for r in rows if r["flags"] and r["source"] == "real"),
            "diary_sources": [VAN_BERKEL, WRZUS, JASO]}


def _weekdays():
    return [(1, de_en("Mo", "Mon")), (2, de_en("Di", "Tue")), (3, de_en("Mi", "Wed")), (4, de_en("Do", "Thu")),
            (5, de_en("Fr", "Fri")), (6, de_en("Sa", "Sat")), (7, de_en("So", "Sun"))]


def _week_context(survey):
    """What diary/_week.html needs on its own: after an autosave of the schedule it is drawn without the page."""
    return {"survey": survey, "lang": survey.default_language, "week": week_preview(survey), "weekdays": _weekdays(),
            "modules": modules(survey), "self_start": self_start, "text_of": text_of}


def _page(survey):
    lang = survey.default_language
    weekdays = _weekdays()
    return render_template("diary/schedule.html",
                           exit=(survey.sampling or {}).get("exit") or {}, exit_days=EXIT_DAYS, survey=survey, lang=lang, modules=modules(survey), part_of=part_of,
                           text_of=text_of, weekdays=weekdays,
                           per_day=per_day, self_start=self_start, open_end=open_end(survey), summary_max=SUMMARY_MAX, rejoin_mode=rejoin_mode(survey),
                           locked=locked(survey), max_modules=MAX_MODULES,
                           week=week_preview(survey), offline={m["key"]: offline_ok(survey, m["key"]) for m in modules(survey)},
                           may_edit=survey.can(current_user, "editor"), sources=[VAN_BERKEL, WRZUS], jaso=JASO)
