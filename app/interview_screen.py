"""The interviewee's own screen for the tasks of an interview ("Teilnehmerbildschirm"; concept
konzept-interview-formate.html, option B, stage 2; Ralf 2026-10-20: E2 video in stage 2, E4 experimental, E8 the
interviewer may change a result, marked).

Stage 1 (app/interview_tasks.py) shows a task on the interviewer's device. Here the interviewed person opens a link
or QR code on a device of their own, also in a video interview (the link goes into the chat). That device shows only
the task the interviewer releases ("Auf dem Teilnehmerbildschirm zeigen"), nothing else of the guide: no notes, no
recording, no clock, no names. The person solves it there; the result comes back into the interview view at once and
is stored like a result entered in the view (Response.answers[item id], time on the interview clock in
Response.interview["tasks"]), marked as given on the screen. The interviewer may change it afterwards (E8): then it
is marked "vom Interviewenden geändert" in the case overview and the notes.

How the device is bound (like the recorder on a second device, app/interview_remote.py: one device, one interview, a
reduced view; and like the interviewer link, app/interviewer.py: the key after "#", only its hash stored):

- "Verbinden" in the interview view makes a key of 256 random bits for this one interview; the link is
  <participant address>/screen/<guide>#<key>, so the key reaches no server or proxy log and no referrer. Only its
  SHA-256 is stored, in Response.interview["screen"]; the link and QR code are shown once.
- The device's page sends the key once in the body of a POST (interview-screen.js) and takes it out of the address
  bar. The first device that opens it is bound: a random device id goes into its session cookie and into the entry;
  every later request must bring both, and the hash must still be the one stored. A second device with the same
  link is refused. An unopened link holds OPEN_MINUTES, a connection at most HOURS, and both end with the interview
  ("Interview beenden"), with "Bildschirm trennen" and with a new link ("Neuen Link erzeugen").
- The device may read only the task released now (its page and a state key every POLL_SECONDS) and send only the
  answer to that task, while it is released. Wrong keys count like other wrong secrets (limits.allow_screen_try),
  answers per sender like live votes (limits.allow_screen_answer).
- No device data: the entry holds the hash, the times of the link and of the device's last request ("seen", at most
  every SEEN_EVERY seconds), the device id and the released task. Nothing about the device (no kind, no address).

The routes of the device are on the participants' side (app/hosts.py, "interview_screen_public"), those of the
interview view on the researchers' side ("interview_screen"). Regular since 2026-10-22 (experimental before): every
interview view of a stored interview with tasks offers it (`offered`).

  Response.interview["screen"] = {"h": SHA-256 of the key, "made": ISO time of the link, "by": user id who made it,
                                  "d": device id ("" until opened), "seen": ISO time of the device's last request,
                                  "show": item id released or None, "rev": count of releases, "s": clock when shown}
  Response.interview["tasks"][item id] = {"t": seconds, "by": "screen", "s": seconds when shown, "changed": True}"""
import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from flask import Blueprint, abort, jsonify, redirect, render_template, request, session, url_for

from app.extensions import db
from app.interviewer import interviewer_or_signed_in
from app.models import Response, utcnow

bp = Blueprint("interview_screen", __name__, url_prefix="/guide")  # the interview view (researchers' side)
public_bp = Blueprint("interview_screen_public", __name__, url_prefix="/screen")  # the device (participants' side)

KEY = "screen"  # Response.interview[KEY]
SESSION_KEY = "ivs"  # the device's session: {survey id: {"r": interview id, "d": device id, "h": start of the hash}}
OPEN_MINUTES = 30  # an unopened link holds this long
HOURS = 6  # a connection holds at most this long
POLL_SECONDS = 2  # the device and the interview view ask this often (as the live vote, app/live/)
SEEN_EVERY = 15  # the device's last request is stored at most this often (fewer writes on the interview's row)
ALIVE = 45  # seconds without a request after which the device counts as gone


def _digest(key):
    return hashlib.sha256(key.encode()).hexdigest()


def _time(iso):
    try:
        return datetime.fromisoformat(iso) if iso else None
    except (TypeError, ValueError):
        return None


def _now():
    return utcnow().isoformat(timespec="seconds")


def entry(row):
    return dict((row.interview or {}).get(KEY) or {})


def _store(row, value):
    meta = dict(row.interview or {})
    if value:
        meta[KEY] = value
    else:
        meta.pop(KEY, None)
    row.interview = meta


# --- who may use it ------------------------------------------------------------------------------------------------


def offered(survey):
    """Whether the interview view offers the screen: to everyone who conducts an interview of the guide (regular
    since 2026-10-22, Ralf; experimental before). Kept as one place to ask."""
    return survey is not None


def used(survey):
    """Whether a guide uses the screen: an interview connected now, or a result given on the screen
    (experimental.in_use: the team sees it from then on)."""
    from app import interview_data, interview_tasks

    for row in interview_data.interviews(survey):
        meta = row.interview or {}
        if meta.get(KEY) or any(isinstance(v, dict) and v.get("by") == "screen"
                                for v in (meta.get(interview_tasks.TIME_KEY) or {}).values()):
            return True
    return False


def _require(survey):
    if not offered(survey):
        abort(404)


# --- the connection -------------------------------------------------------------------------------------------------


def connect(row, by=None):
    """A new link for this interview; returns the key (shown once). An older link and its device end with it."""
    key = secrets.token_urlsafe(32)
    _store(row, {"h": _digest(key), "made": _now(), "by": by, "d": "", "seen": "", "show": None, "rev": 0, "s": None})
    return key


def disconnect(row):
    _store(row, None)


def valid(row, now=None):
    """Whether the connection of the interview still holds: a link, not older than HOURS (OPEN_MINUTES while no
    device opened it), the interview not ended."""
    s, now = entry(row), now or utcnow()
    made = _time(s.get("made"))
    if not s.get("h") or made is None or row.status == Response.COMPLETED:
        return False
    return made > now - (timedelta(hours=HOURS) if s.get("d") else timedelta(minutes=OPEN_MINUTES))


SHORT = "short"  # what "show" holds while the Kurzfragebogen is out on the screen (Ralf 2026-10-22, A)
SHORT_KEY = "short_screen"  # Response.interview[SHORT_KEY] = {"by": "screen", "s": clock when shown, "changed": bool}


def short_items(survey):
    """The questions of the Kurzfragebogen the screen shows (all at once, as one page)."""
    from app import interview_data

    return [i for i in interview_data.form_items(survey, "short") if i.type != "page_break"]


def short_out(row):
    return entry(row).get("show") == SHORT


def note_short(row, by):
    """Who gave the answers of the Kurzfragebogen: "screen" marks them as given on the interviewee's screen; a later
    change in the interview view keeps that and adds "changed" (E2, as the tasks: "vom Interviewenden geändert")."""
    meta = dict(row.interview or {})
    mark = meta.get(SHORT_KEY) if isinstance(meta.get(SHORT_KEY), dict) else None
    if by == "screen":
        shown = entry(row).get("s")
        meta[SHORT_KEY] = {"by": "screen", **({"s": shown} if isinstance(shown, int) else {})}
    elif mark and mark.get("by") == "screen":
        meta[SHORT_KEY] = {**mark, "changed": True}
    else:
        return
    row.interview = meta


def released(survey, row):
    """The task released to the screen now (an Item of the guide's tasks), or None."""
    from app import interview_tasks

    show = entry(row).get("show")
    return next((i for i in interview_tasks.tasks(survey) if i.id == show), None) if show else None


def release(survey, row, item):
    """Show `item` (a task of the guide, or SHORT: the whole Kurzfragebogen) on the screen, or nothing (None)."""
    from app import interview_audio

    s = entry(row)
    if not s:
        abort(409)
    show = SHORT if item == SHORT else item.id if item is not None else None
    s.update(show=show, rev=int(s.get("rev") or 0) + 1, s=interview_audio.clock(row) if item is not None else None)
    _store(row, s)


def status(survey, row, now=None):
    """What the interview view needs: {"on": a link exists and holds, "paired": a device opened it, "alive": it asked
    lately, "show": item id released, "results": {item id: {"text", "by", "changed"}}} (all tasks with a result)."""
    from app import interview_tasks

    s, now = entry(row), now or utcnow()
    seen = _time(s.get("seen"))
    on = valid(row, now)
    marks = (row.interview or {}).get(interview_tasks.TIME_KEY) or {}
    lang, results = survey.default_language, {}
    for item in interview_tasks.tasks(survey):
        answer = (row.answers or {}).get(str(item.id))
        if answer in (None, "", [], {}):
            continue
        mark = marks.get(str(item.id)) if isinstance(marks.get(str(item.id)), dict) else {}
        results[str(item.id)] = {"text": interview_tasks.result_text(item, answer, lang),
                                 "by": "screen" if mark.get("by") == "screen" else "interviewer",
                                 "changed": bool(mark.get("changed"))}
    items = short_items(survey)
    given = [i for i in items if (row.answers or {}).get(str(i.id)) not in (None, "", [], {})]
    mark = (row.interview or {}).get(SHORT_KEY) if isinstance((row.interview or {}).get(SHORT_KEY), dict) else {}
    if given:
        from app.lang import de_en

        results[SHORT] = {"text": de_en("{n} von {m} Fragen beantwortet", "{n} of {m} questions answered", n=len(given), m=len(items)),
                          "by": "screen" if mark.get("by") == "screen" else "interviewer", "changed": bool(mark.get("changed"))}
    return {"on": on, "paired": on and bool(s.get("d")), "alive": on and seen is not None and (now - seen).total_seconds() <= ALIVE,
            "show": s.get("show") if on else None, "results": results}


def source(row, item):
    """Who gave the result of a task: "" (none), "interviewer", "screen" or "changed" (given on the screen and
    changed by the interviewer, E8)."""
    from app import interview_tasks

    if (row.answers or {}).get(str(item.id)) in (None, "", [], {}):
        return ""
    mark = ((row.interview or {}).get(interview_tasks.TIME_KEY) or {}).get(str(item.id))
    if not isinstance(mark, dict) or mark.get("by") != "screen":
        return "interviewer"
    return "changed" if mark.get("changed") else "screen"


def seconds_taken(row, item):
    """Seconds from showing the task on the screen to its result, or None."""
    from app import interview_tasks

    mark = ((row.interview or {}).get(interview_tasks.TIME_KEY) or {}).get(str(item.id))
    if isinstance(mark, dict) and mark.get("by") == "screen" and isinstance(mark.get("s"), int) and isinstance(mark.get("t"), int):
        return max(0, mark["t"] - mark["s"])
    return None


SOURCE_WORDS = {"interviewer": ("Interviewende", "interviewer"), "screen": ("Teilnehmerbildschirm", "participant screen"),
                "changed": ("Teilnehmerbildschirm, vom Interviewenden geändert", "participant screen, changed by the interviewer")}


# --- the interview view (researchers' side) --------------------------------------------------------------------------


def _row(code, rid):
    from app import interviewer

    survey, row = interviewer.row_for(code, rid)
    _require(survey)
    return survey, row


def link(survey, key):
    from app.hosts import participant_base

    return participant_base() + url_for("interview_screen_public.page", code=survey.code) + "#" + key


@bp.route("/<code>/interviews/<int:rid>/screen/connect", methods=["POST"])
@interviewer_or_signed_in
def connect_view(code, rid):
    """A new link and its QR code (shown once). A link made again ends the device of the old one."""
    from flask_login import current_user

    from app import interview_audio, interviewer, security_log
    from app.links import qr

    survey, row = _row(code, rid)
    if row.status == Response.COMPLETED:
        abort(409)
    access = interviewer.actor(survey)
    by = access.created_by_id if access is not None else current_user.id
    key = interview_audio.retried(lambda: connect(row, by))
    url = link(survey, key)
    security_log.event("interview_screen_link", target=survey.code)
    return jsonify(ok=True, link=url, qr="data:image/svg+xml;base64," + base64.b64encode(qr(url)).decode(),
                   **status(survey, row))


@bp.route("/<code>/interviews/<int:rid>/screen/disconnect", methods=["POST"])
@interviewer_or_signed_in
def disconnect_view(code, rid):
    from app import interview_audio

    survey, row = _row(code, rid)
    interview_audio.retried(lambda: disconnect(row))
    return jsonify(ok=True, **status(survey, row))


@bp.route("/<code>/interviews/<int:rid>/screen/release", methods=["POST"])
@interviewer_or_signed_in
def release_view(code, rid):
    """Show a task on the screen ("task": its id) or take it back ("task" empty)."""
    from app import interview_audio, interview_tasks

    survey, row = _row(code, rid)
    if not valid(row):
        return jsonify(ok=False, error="gone", **status(survey, row)), 409
    data = request.get_json(silent=True)
    wanted = str(data.get("task") or "") if isinstance(data, dict) else request.form.get("task", "")
    item = None
    if wanted == SHORT:  # the Kurzfragebogen as a whole (Ralf 2026-10-22, A)
        if not short_items(survey):
            abort(404)
        item = SHORT
    elif wanted:
        item = next((i for i in interview_tasks.tasks(survey) if str(i.id) == wanted), None)
        if item is None:
            abort(404)
    interview_audio.retried(lambda: release(survey, row, item))
    return jsonify(ok=True, **status(survey, row))


@bp.route("/<code>/interviews/<int:rid>/screen/state")
@interviewer_or_signed_in
def state_view(code, rid):
    survey, row = _row(code, rid)
    return jsonify(status(survey, row))


@bp.route("/<code>/interviews/<int:rid>/screen/task/short")
@interviewer_or_signed_in
def edit_short_view(code, rid):
    """The Kurzfragebogen to change in the interview view after the screen sent it (E2), drawn fresh in a frame."""
    from app import interview_data

    survey, row = _row(code, rid)
    items = short_items(survey)
    if not items:
        abort(404)
    return render_template("interview/screen_edit.html", survey=survey, row=row, items=items, part=SHORT, task=None,
                           form_answers=interview_data.form_answers(survey, row))


@bp.route("/<code>/interviews/<int:rid>/screen/task/<int:iid>")
@interviewer_or_signed_in
def edit_view(code, rid, iid):
    """The result of one task to change in the interview view (E8), drawn fresh in a frame of the task's card: the
    form of the interview view would still hold what it held before the screen sent its result."""
    from app import interview_data, interview_tasks

    survey, row = _row(code, rid)
    item = next((i for i in interview_tasks.tasks(survey) if i.id == iid), None)
    if item is None:
        abort(404)
    return render_template("interview/screen_edit.html", survey=survey, row=row, items=[item], part="task", task=item,
                           form_answers=interview_data.form_answers(survey, row))


# --- the device (participants' side) --------------------------------------------------------------------------------


def _guide(code):
    from app.models import Survey

    survey = db.session.scalar(db.select(Survey).filter_by(code=code))
    if survey is None or survey.interview is None or survey.archived:
        abort(404)
    return survey


def _bound(survey):
    """The interview this device is bound to, or None (no session, the link renewed or ended, another device)."""
    mine = (session.get(SESSION_KEY) or {}).get(str(survey.id))
    if not isinstance(mine, dict) or not isinstance(mine.get("r"), int):
        return None
    row = db.session.get(Response, mine["r"])
    if row is None or row.survey_id != survey.id or row.interview is None:
        return None
    s = entry(row)
    if not (s.get("d") and isinstance(mine.get("d"), str) and hmac.compare_digest(s["d"], mine["d"])
            and isinstance(mine.get("h"), str) and hmac.compare_digest(s.get("h", "")[:16], mine["h"]) and valid(row)):
        return None
    return row


def _forget(survey):
    mine = dict(session.get(SESSION_KEY) or {})
    if mine.pop(str(survey.id), None) is not None:
        session[SESSION_KEY] = mine


def _seen(row):
    """Note the device's request, at most every SEEN_EVERY seconds."""
    from app import interview_audio

    seen = _time(entry(row).get("seen"))
    if seen is None or (utcnow() - seen).total_seconds() >= SEEN_EVERY:
        def note():
            s = entry(row)
            if s:
                s["seen"] = _now()
                _store(row, s)
        interview_audio.retried(note)


def _key(survey, row):
    """What the device shows now, as a key that changes whenever its page has to change."""
    if row is None:
        return "ended"
    if short_out(row):
        return f"short-{entry(row).get('rev', 0)}"
    item = released(survey, row)
    return f"task-{item.id}-{entry(row).get('rev', 0)}" if item is not None else "wait"


@public_bp.route("/<code>")
def page(code):
    survey = _guide(code)
    row = _bound(survey)
    item = released(survey, row) if row is not None else None
    short = row is not None and short_out(row)
    if row is not None:
        _seen(row)
    shown = short_items(survey) if short else [item] if item is not None else []
    answers = {i.id: row.answers[str(i.id)] for i in shown if (row.answers or {}).get(str(i.id)) not in (None, "", [], {})}
    return render_template("interview/screen.html", survey=survey, lang=survey.default_language, bound=row is not None,
                           item=item, items=shown, short=short, answers=answers, key=_key(survey, row) if row is not None else "open",
                           rev=entry(row).get("rev", 0) if row is not None else 0, poll=POLL_SECONDS,
                           problem=request.args.get("problem", ""))


def _refused(survey, reason):
    from app import security_log

    security_log.event("interview_screen_refused", target=survey.code, reason=reason)
    return redirect(url_for("interview_screen_public.page", code=survey.code, problem=reason))


@public_bp.route("/<code>/open", methods=["POST"])
def open_link(code):
    """The key from the address's "#" (interview-screen.js): binds this device to the interview of the key."""
    from app import interview_audio, interview_data, limits

    survey = _guide(code)
    if not limits.allow_screen_try(count=False):
        return _refused(survey, "many")
    key = request.form.get("key", "").strip()[:100]
    digest = _digest(key) if key else ""
    row = next((r for r in interview_data.interviews(survey)
                if digest and hmac.compare_digest(entry(r).get("h", ""), digest)), None)
    if row is None:
        limits.allow_screen_try()
        return _refused(survey, "unknown")
    if not valid(row):
        return _refused(survey, "ended")
    mine = (session.get(SESSION_KEY) or {}).get(str(survey.id))
    s = entry(row)
    if s.get("d"):  # one device only: the same browser again is welcome, another one is refused
        if isinstance(mine, dict) and mine.get("r") == row.id and isinstance(mine.get("d"), str) and hmac.compare_digest(mine["d"], s["d"]):
            return redirect(url_for("interview_screen_public.page", code=survey.code))
        return _refused(survey, "taken")
    device = secrets.token_urlsafe(16)

    def bind():
        current = entry(row)
        if current.get("h") != digest or current.get("d"):
            return False
        current.update(d=device, seen=_now())
        _store(row, current)
        return True

    if not interview_audio.retried(bind):
        return _refused(survey, "taken")
    session[SESSION_KEY] = {**(session.get(SESSION_KEY) or {}), str(survey.id): {"r": row.id, "d": device, "h": digest[:16]}}
    from app import security_log

    security_log.event("interview_screen_opened", target=survey.code)
    return redirect(url_for("interview_screen_public.page", code=survey.code))


@public_bp.route("/<code>/state")
def state(code):
    survey = _guide(code)
    row = _bound(survey)
    if row is None:
        _forget(survey)
        return jsonify(key="ended")
    _seen(row)
    return jsonify(key=_key(survey, row))


@public_bp.route("/<code>/answer", methods=["POST"])
def answer(code):
    """The answer to the task released now, nothing else: stored as a result of the interview, given on the screen."""
    from app import interview_audio, interview_data, limits
    from app.ui_i18n import tr

    survey = _guide(code)
    row = _bound(survey)
    if row is None:
        return jsonify(ok=False, error="ended"), 409
    short = short_out(row)
    item = released(survey, row)
    wanted = SHORT if short else str(item.id) if item is not None else None
    if wanted is None or request.form.get("task") != wanted or request.form.get("rev") != str(entry(row).get("rev", 0)):
        return jsonify(ok=False, error="closed"), 409  # taken back or another task meanwhile
    if not limits.allow_screen_answer():
        return jsonify(ok=False, error="busy"), 429
    form = request.form.copy()
    if short:  # the Kurzfragebogen: stored as the view stores it; its required questions hold here (E1)
        form["part"] = "short"
        form.pop("task", None)

        def take_short():
            errors = interview_data.save_forms(survey, row, form, by="screen")
            note_short(row, "screen")
            return errors or interview_data.missing_required(survey, row, "short")

        errors = interview_audio.retried(take_short)
        if errors:
            return jsonify(ok=False, error="invalid", errors={str(k): v for k, v in errors.items()}), 400
        return jsonify(ok=True)
    form["part"], form["task"] = "task", str(item.id)

    def take():
        form["t"] = str(interview_audio.clock(row))
        return interview_data.save_forms(survey, row, form, by="screen")

    errors = interview_audio.retried(take)
    if errors:
        return jsonify(ok=False, error="invalid", errors={str(k): v for k, v in errors.items()}), 400
    if (row.answers or {}).get(str(item.id)) in (None, "", [], {}):
        return jsonify(ok=False, error="empty", message=tr(survey.default_language, "Bitte zuerst die Aufgabe bearbeiten.",
                                                           "Please work on the task first.")), 400
    return jsonify(ok=True)
