"""Interviewers without an account: a personal link per interviewer of an interview guide (concept
konzept-interviewer-zugang-2026-10-15.md, Ralf 2026-10-15: A "only the link", B one guide, C the name stays in the
interviews, D they delete only their own trial interviews, E they open an interview until its transcript is checked,
F 90 days and 3 devices, G "Bearbeiten" and "Admin" give accesses, H for everyone).

The researchers ("Bearbeiten" or "Admin" on the guide) add an interviewer on the page "Interviews" with a name and get
a link and its QR code, shown once (only the hash of its key is stored, InterviewerAccess). Whoever opens the link is
that interviewer: no account, no ORCID, no mail, no password. The key stands after "#" in the link, so that it reaches
no server or proxy log and no referrer; the opening page sends it once in the body of a POST and takes it out of the
address bar (interviewer.js).

An interviewer is no User and never signed in: the session holds {survey id: {"a" access, "r" revision, "d" device,
"t" last request}} under SESSION_KEY, and the researchers' pages see nobody signed in. The routes of one interview
(the interview view, saving, the forms, ending, recording, the recorder on a second device, the transcript) are the
researchers' routes; they ask `row_for`, which gives a researcher with the role what it gave before and an
interviewer only an interview of their own access (Response.interview["access"]) whose transcript is not yet checked
(decision E), else 404. The list of their interviews and starting one are routes of this module.

Ending an access: "Sperren", "Neuer Link" and "Geräte abmelden" give it a new revision, so every session of it ends with
its next request; it also ends with its expiry date, after IDLE minutes without a request, and when the guide is
archived. The interviews stay with the guide and keep the interviewer's name (decision C)."""
import hashlib
import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request, session,
                   url_for)
from flask_login import current_user, login_required

from app.extensions import db
from app.lang import de_en
from app.models import InterviewerAccess, Response, Survey, new_pid, utcnow

bp = Blueprint("interviewer", __name__)

SESSION_KEY = "iv"
DAYS = (30, 90, 180, 365)  # how long a new access holds (decision F: 90 by default)
DEFAULT_DAYS = 90
DEFAULT_DEVICES = 3  # decision F: a tablet, a phone as recorder, a spare
MAX_DEVICES = 10
IDLE = 60  # minutes without a request after which a device has to open the link again
HINTS_VERSION = "2026-10-15"  # the "Hinweise für Interviewende"; a new version is confirmed again


def _digest(key):
    return hashlib.sha256(key.encode()).hexdigest()


def _revision():
    return secrets.token_urlsafe(18)[:24]


def link(survey, key):
    """The link of an access: the key after "#", so that it reaches no log (the configured address, as the
    recorder's QR code, security audit 2026-10-06, H3)."""
    from app import brand

    return brand.public_base() + url_for("interviewer.start", code=survey.code) + "#" + key


def short(name):
    """The prefix of an interviewer's identifiers: the initials of the name ("Anna Becker" → "AB"), at most three."""
    letters = [w[0] for w in name.replace("-", " ").split() if w[:1].isalpha()]
    return "".join(letters[:3]).upper() or "I"


# --- the accesses, for the researchers ---------------------------------------------------------------------------


def accesses(survey):
    return db.session.scalars(db.select(InterviewerAccess).filter_by(survey_id=survey.id)
                              .order_by(InterviewerAccess.created_at, InterviewerAccess.id)).all()


def create(survey, name, days=DEFAULT_DAYS, devices=DEFAULT_DEVICES, may_record=True, may_transcribe=True, by=None):
    """A new access; returns (access, key). The caller commits; the key is shown once."""
    key = secrets.token_urlsafe(32)
    access = InterviewerAccess(survey_id=survey.id, name=" ".join(str(name).split())[:80], token_hash=_digest(key),
                               revision=_revision(), may_record=bool(may_record), may_transcribe=bool(may_transcribe),
                               max_devices=max(1, min(int(devices), MAX_DEVICES)), devices=[],
                               created_by_id=getattr(by, "id", None), created_by_name=(getattr(by, "name", "") or "")[:200],
                               expires_at=utcnow() + timedelta(days=int(days)))
    db.session.add(access)
    return access, key


def new_key(access):
    """A new link: the old one opens nothing any more, and every device has to open the new one."""
    key = secrets.token_urlsafe(32)
    access.token_hash, access.revision, access.devices = _digest(key), _revision(), []
    return key


def sign_out(access):
    access.revision, access.devices = _revision(), []


def state(access, now=None):
    """"active" | "blocked" | "expired" | "invited" (not opened yet)."""
    if access.blocked:
        return "blocked"
    if access.expires_at <= (now or utcnow()):
        return "expired"
    return "active" if access.last_used_at else "invited"


def interview_count(survey):
    """{access id: number of interviews} of the guide."""
    from app import interview_data

    out = {}
    for row in interview_data.interviews(survey):
        aid = (row.interview or {}).get("access")
        if aid:
            out[aid] = out.get(aid, 0) + 1
    return out


# --- who asks: a researcher or an interviewer ----------------------------------------------------------------------


def _entries():
    entries = session.get(SESSION_KEY)
    return entries if isinstance(entries, dict) else {}


def usable(access, survey, now=None):
    """Whether the access opens anything now: not blocked, not expired, the guide there and not archived."""
    return (access is not None and survey is not None and access.survey_id == survey.id and survey.interview is not None
            and not survey.archived and not access.blocked and access.expires_at > (now or utcnow()))


def current(survey):
    """The interviewer of this guide in this browser (InterviewerAccess), or None. Checks at every request what
    can end it: revision, device, expiry, blocking, archiving, idle time."""
    if survey is None:
        return None
    entry = _entries().get(str(survey.id))
    if not isinstance(entry, dict):
        return None
    now = utcnow()
    access = db.session.get(InterviewerAccess, entry.get("a")) if isinstance(entry.get("a"), int) else None
    try:
        last = datetime.fromisoformat(entry.get("t", ""))
    except (TypeError, ValueError):
        last = None
    devices = {d.get("id"): d for d in (access.devices or [])} if access is not None else {}
    valid = usable(access, survey, now) and entry.get("r") == access.revision and entry.get("d") in devices
    if not valid or last is None or last <= now - timedelta(minutes=IDLE):
        if valid:  # idle: the device leaves the list, so that opening the link again does not count it twice
            _drop_device(access, entry["d"])
        _forget(survey)
        return None
    _touch(survey, entry, access, devices[entry["d"]], now)
    return access


def _touch(survey, entry, access, device, now):
    """Note the request: in the session at once (the idle time), on the access and the device at most once a
    minute (the device's own "last", so that a second device in use does not look idle, see _stale)."""
    entries = dict(_entries())
    entries[str(survey.id)] = {**entry, "t": now.isoformat(timespec="seconds")}
    session[SESSION_KEY] = entries
    if (access.last_used_at is None or access.last_used_at <= now - timedelta(minutes=1)
            or _stale(device, now, minutes=1)):
        access.last_used_at = now
        access.devices = [{**d, "last": now.isoformat(timespec="seconds")} if d.get("id") == device.get("id") else d
                          for d in (access.devices or [])]
        db.session.commit()


def _stale(device, now, minutes=IDLE + 1):
    """Whether the device's last request lies `minutes` back. After IDLE minutes (and one more, as "last" is noted at
    most once a minute) its session has ended: it no longer counts towards max_devices (code review 2026-10-15, K1:
    every opening after an idle hour added a device, and on the third or fourth day the tablet was refused)."""
    try:
        last = datetime.fromisoformat(device.get("last") or "")
    except (TypeError, ValueError):
        return True
    return last <= now - timedelta(minutes=minutes)


def _drop_device(access, device_id):
    access.devices = [d for d in (access.devices or []) if d.get("id") != device_id]
    db.session.commit()


def _forget(survey):
    entries = dict(_entries())
    if entries.pop(str(survey.id), None) is not None:
        session[SESSION_KEY] = entries


def any_session():
    return bool(_entries())


def interviewer_or_signed_in(view):
    """Instead of login_required on the routes of one interview: lets through a researcher who is signed in and a
    browser that holds an interviewer's session; `row_for` decides about the interview itself."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user.is_authenticated or any_session():
            return view(*args, **kwargs)
        # a request of a script (the interview view saving, a piece of the recording): 401 instead of the sign-in
        # page, so the page can say "sign in again" and keeps everything until then (Ralf 2026-10-22, offline I)
        if request.headers.get("X-Requested-With") == "fetch":
            from flask import jsonify, make_response

            abort(make_response(jsonify(ok=False, error="signin"), 401))
        return current_app.login_manager.unauthorized()

    return wrapper


def guide(code):
    survey = db.session.scalar(db.select(Survey).filter_by(code=code))
    if survey is None or survey.interview is None:
        abort(404)
    return survey


def visible(row):
    """Whether an interviewer still opens this interview: until its transcript is checked (decision E)."""
    return not ((row.interview or {}).get("transcript") or {}).get("checked")


def mine(access, row):
    return row is not None and (row.interview or {}).get("access") == access.id and visible(row)


def actor(survey):
    """The interviewer asking about this guide, or None when a researcher (or nobody) asks. A researcher with a
    role on the guide is always the researcher, also in a browser that holds an interviewer's session."""
    if current_user.is_authenticated and survey.can(current_user, "viewer"):
        return None
    return current(survey)


def row_for(code, rid, need="editor", right=None):
    """(survey, interview) for a route of one interview. A researcher: as before (`need` on the guide, else the
    usual refusal). An interviewer: only an interview of their own access that they still open, and for `right`
    ("record", "transcribe") only when the access allows it; else 404, so that numbers tell nothing. What only admins
    of the guide may do (need="admin", deleting recordings) no interviewer may: a session as interviewer next to an
    account without that role deleted the recordings of real interviews (audit 2026-10-22, B-02)."""
    from app import interview_data

    survey = guide(code)
    if current_user.is_authenticated and survey.can(current_user, need):
        return survey, interview_data._interview(survey, rid)
    access = current(survey) if need != "admin" else None
    if access is None:
        if current_user.is_authenticated:
            survey = interview_data._survey(code, need=need)  # the refusal a researcher got before
            return survey, interview_data._interview(survey, rid)
        abort(404)
    row = db.session.get(Response, rid)
    if (row is None or row.survey_id != survey.id or row.interview is None or not mine(access, row)
            or (right == "record" and not access.may_record) or (right == "transcribe" and not access.may_transcribe)):
        abort(404)
    return survey, row


def list_url(survey):
    """Where the pages of one interview lead back to: the interviewer's list, or the researchers' page "Interviews"."""
    if actor(survey) is not None:
        return url_for("interviewer.home", code=survey.code)
    return url_for("interview_data.index", code=survey.code)


@bp.app_context_processor
def _helpers():
    return {"interviews_url": list_url, "interviewer_of": actor, "interviewer_batch": batch_token}


# --- the researchers' part (page "Interviews", section "Interviewende") ------------------------------------------


def _manage(code):
    from app import interview_data

    return interview_data._survey(code, need="editor")  # decision G: "Bearbeiten" and "Admin"


def _back(survey):
    return redirect(url_for("interview_data.index", code=survey.code) + "#interviewende")


def _number(name, default, low, high):
    try:
        return max(low, min(int(request.form.get(name, default)), high))
    except (TypeError, ValueError):
        return default


MAX_BATCH = 50  # accesses made at once (a seminar), Ralf 2026-10-15: "direkt x Interviewer hinzufügen"
BATCH_KEY = "iv_batch"  # the last forms sent, so that sending one again (reload) makes nothing twice


def batch_token():
    """A token for one form "Interviewende hinzufügen": sending the same form again makes nothing a second time."""
    return secrets.token_urlsafe(9)


def _names(survey, form):
    """The names to add, as the links for persons on the survey side (surveys.add_value_links): one per line of
    "names" (or the single field "name"), or only a number ("Person 1", "Person 2", … after the highest one there).
    Returns (names, skipped names that already have an access, error)."""
    taken = {a.name.casefold() for a in accesses(survey)}
    lines = (form.get("names") or form.get("name") or "").splitlines()
    names, skipped, seen = [], [], set()
    for line in lines:
        name = " ".join(line.split())[:80]
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        (skipped if name.casefold() in taken else names).append(name)
    if not lines or not (names or skipped):
        try:
            count = int(form.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        if count <= 0:
            return [], [], de_en("Bitte einen Namen je Zeile oder eine Anzahl angeben.", "Please enter one name per line or a number.")
        if count > MAX_BATCH:
            return [], [], de_en("Höchstens {n} auf einmal.", "At most {n} at once.", n=MAX_BATCH)
        numbers = [int(n[7:]) for n in taken if n.startswith("person ") and n[7:].isdigit()]
        first = max(numbers, default=0) + 1
        return [f"Person {first + i}" for i in range(count)], [], None
    if len(names) > MAX_BATCH:
        return [], [], de_en("Höchstens {n} auf einmal, hier sind es {m}.", "At most {n} at once, here there are {m}.", n=MAX_BATCH, m=len(names))
    return names, skipped, None


@bp.route("/guide/<code>/interviewers/new", methods=["POST"])
@login_required
def add(code):
    """One or more interviewers at once. One: its link once on the page "Interviews" (show_once). Several: a page
    with every link and QR code, as cards to print and cut apart, shown only in the answer to this form."""
    from app import security_log

    survey = _manage(code)
    batch = str(request.form.get("batch", ""))[:20]
    if batch and batch in session.get(BATCH_KEY, []):
        flash(de_en("Diese Liste ist schon angelegt; ihre Links stehen nicht noch einmal da. Für eine Person: „Neuer Link“.",
                    "This list has already been created; its links are not shown again. For one person: “New link”."), "error")
        return _back(survey)
    names, skipped, error = _names(survey, request.form)
    if not names:
        error = error or (de_en("Für „{name}“ gibt es schon einen Zugang.", "There is already an access for “{name}”.", name=skipped[0])
                          if len(skipped) == 1 else
                          de_en("Für alle diese Namen gibt es schon einen Zugang.", "There is already an access for all these names."))
    if error:
        flash(error, "error")
        return _back(survey)
    days = _number("days", DEFAULT_DAYS, 1, 365)
    devices = _number("devices", DEFAULT_DEVICES, 1, MAX_DEVICES)
    made = [create(survey, name, days, devices, bool(request.form.get("may_record")),
                   bool(request.form.get("may_transcribe")), current_user) for name in names]
    db.session.commit()
    for _ in made:
        security_log.event("interviewer_access_created", account=current_user.id, target=survey.code)
    if batch:
        session[BATCH_KEY] = (session.get(BATCH_KEY, []) + [batch])[-5:]
    if skipped:
        flash(de_en("Schon vorhanden, nicht noch einmal angelegt: {names}", "Already there, not created again: {names}",
                    names=", ".join(skipped)), "error")
    if len(made) == 1:
        access, key = made[0]
        session["iv_new"] = {"a": access.id, "k": key}  # shown once on the next page (show_once)
        return _back(survey)
    from app.links import qr

    rows = [{"name": a.name, "url": link(survey, k)} for a, k in made]
    for row in rows:
        row["qr"] = qr(row["url"]).decode()
    response = current_app.make_response(render_template("interviewer/links.html", survey=survey, rows=rows, days=days))
    response.headers["Cache-Control"] = "no-store"  # the keys stand only in this answer
    return response


def show_once(survey):
    """The link of an access just made or renewed, once: {"access", "url", "qr" (a data: address of the SVG)} or None."""
    import base64

    from app.links import qr

    new = session.pop("iv_new", None)
    if not isinstance(new, dict):
        return None
    access = db.session.get(InterviewerAccess, new.get("a")) if isinstance(new.get("a"), int) else None
    if access is None or access.survey_id != survey.id or access.token_hash != _digest(str(new.get("k", ""))):
        return None
    url = link(survey, new["k"])
    return {"access": access, "url": url, "qr": "data:image/svg+xml;base64," + base64.b64encode(qr(url)).decode()}


ACTIONS = ("link", "block", "unblock", "signout", "extend", "delete")


@bp.route("/guide/<code>/interviewers/<int:aid>/<action>", methods=["POST"])
@login_required
def change(code, aid, action):
    from app import security_log

    survey = _manage(code)
    access = db.session.get(InterviewerAccess, aid)
    if access is None or access.survey_id != survey.id or action not in ACTIONS:
        abort(404)
    name = access.name
    if action == "link":
        session["iv_new"] = {"a": access.id, "k": new_key(access)}
        message = de_en("Neuer Link für {name}: Der alte öffnet nichts mehr.", "New link for {name}: the old one opens nothing any more.", name=name)
    elif action == "block":
        access.blocked = True
        sign_out(access)
        message = de_en("Zugang von {name} gesperrt.", "Access of {name} blocked.", name=name)
    elif action == "unblock":
        access.blocked = False
        message = de_en("Zugang von {name} entsperrt. Die Geräte öffnen den Link neu.", "Access of {name} unblocked. The devices open the link again.", name=name)
    elif action == "signout":
        sign_out(access)
        message = de_en("Alle Geräte von {name} abgemeldet.", "All devices of {name} signed out.", name=name)
    elif action == "extend":
        days = _number("days", DEFAULT_DAYS, 1, 365)
        access.expires_at = max(access.expires_at, utcnow()) + timedelta(days=days)
        message = de_en("Zugang von {name} verlängert.", "Access of {name} extended.", name=name)
    else:  # the interviews stay, with the name (decision C)
        db.session.delete(access)
        message = de_en("Zugang von {name} gelöscht. Die Interviews bleiben beim Leitfaden.", "Access of {name} deleted. The interviews stay with the guide.", name=name)
    db.session.commit()
    security_log.event(f"interviewer_access_{action}", account=current_user.id, target=survey.code)
    flash(message, "success")
    return _back(survey)


# --- the interviewers' part ----------------------------------------------------------------------------------------


def _guide_for_interviewer(code):
    return guide(code)


DEVICE_KINDS = (("iPad", "iPad"), ("iPhone", "iPhone"), ("Android", "Android"), ("Windows", "Windows"),
                ("Macintosh", "Mac"), ("CrOS", "Chromebook"), ("Linux", "Linux"))


def device_kind(agent):
    agent = agent or ""
    return next((name for needle, name in DEVICE_KINDS if needle in agent), de_en("Gerät", "Device"))


def _refused(survey, text, status=400):
    return render_template("interviewer/start.html", survey=survey, problem=text, hints=HINTS_VERSION), status


@bp.route("/i/<code>", methods=["GET", "POST"])
def start(code):
    """The link's page: the hints to confirm; the key comes from the address's "#" (interviewer.js)."""
    from app import limits, security_log

    survey = _guide_for_interviewer(code)
    if request.method == "GET":
        if current(survey) is not None:
            return redirect(url_for("interviewer.home", code=survey.code))
        return render_template("interviewer/start.html", survey=survey, problem=None, hints=HINTS_VERSION)
    if not limits.allow_interviewer_try(count=False):
        return _refused(survey, de_en("Zu viele Versuche. Bitte später erneut öffnen.", "Too many attempts. Please open it again later."), 429)
    key = request.form.get("key", "").strip()
    access = db.session.scalar(db.select(InterviewerAccess).filter_by(token_hash=_digest(key))) if key else None
    if access is None or access.survey_id != survey.id:
        limits.allow_interviewer_try()
        security_log.event("interviewer_link_refused", target=survey.code, reason="unknown")
        return _refused(survey, de_en("Dieser Link gilt nicht (mehr). Bitte bei den Forschenden einen neuen erbitten.",
                                      "This link is not (or no longer) valid. Please ask the researchers for a new one."), 404)
    if not usable(access, survey):
        security_log.event("interviewer_link_refused", target=survey.code, reason=state(access))
        return _refused(survey, de_en("Dieser Zugang ist gesperrt oder abgelaufen. Bitte an die Forschenden wenden.",
                                      "This access is blocked or has expired. Please contact the researchers."), 403)
    if access.hints_version != HINTS_VERSION and not request.form.get("hints"):
        return _refused(survey, de_en("Bitte die Hinweise lesen und bestätigen.", "Please read and confirm the notes."))
    # those idle no longer count, and this browser's own device is replaced, not added once more (K1)
    old = _entries().get(str(survey.id))
    mine = old.get("d") if isinstance(old, dict) and old.get("a") == access.id and old.get("r") == access.revision else None
    devices = [d for d in (access.devices or []) if not _stale(d, utcnow()) and (mine is None or d.get("id") != mine)]
    if len(devices) >= access.max_devices:
        security_log.event("interviewer_link_refused", target=survey.code, reason="devices")
        return _refused(survey, de_en("Dieser Link ist schon auf {n} Geräten geöffnet. Die Forschenden können die Geräte abmelden.",
                                      "This link is already open on {n} devices. The researchers can sign the devices out.",
                                      n=len(devices)), 403)
    now = utcnow().isoformat(timespec="seconds")
    device = {"id": secrets.token_urlsafe(12), "kind": device_kind(request.headers.get("User-Agent")), "first": now, "last": now}
    access.devices = devices + [device]
    access.hints_version, access.last_used_at = HINTS_VERSION, utcnow()
    db.session.commit()
    session.permanent = True
    session[SESSION_KEY] = {**_entries(), str(survey.id): {"a": access.id, "r": access.revision, "d": device["id"], "t": now}}
    security_log.event("interviewer_link_opened", target=survey.code, kind=device["kind"])
    return redirect(url_for("interviewer.home", code=survey.code))


def _mine(code):
    survey = _guide_for_interviewer(code)
    return survey, current(survey)


def _again(survey):
    flash(de_en("Bitte den Link erneut öffnen.", "Please open the link again."), "warning")
    return redirect(url_for("interviewer.start", code=survey.code))


def own_interviews(survey, access):
    """All interviews of the access, the ones handed over (transcript checked) included, which show without content."""
    from app import interview_data

    return [r for r in interview_data.interviews(survey) if (r.interview or {}).get("access") == access.id]


def next_label(survey, access):
    from app import interview_data

    prefix = short(access.name)
    from app import interview_offline

    taken = {(r.interview or {}).get("label") for r in interview_data.interviews(survey)} | interview_offline.reserved_labels(survey)
    n = 1
    while f"{prefix}-{n:02d}" in taken:
        n += 1
    return f"{prefix}-{n:02d}"


@bp.route("/i/<code>/interviews")
def home(code):
    from app import interview, interview_audio, interview_data, interview_offline, interview_transcribe

    survey, access = _mine(code)
    if access is None:
        return _again(survey)
    return render_template("interviewer/home.html", survey=survey, access=access, rows=own_interviews(survey, access),
                           visible=visible, label=next_label(survey, access), st=interview.state(survey),
                           modes=interview.modes(), forms=interview_data.consent_forms(), day=interview.day_text,
                           today=f"{utcnow():%Y-%m-%d}", parts=interview_audio.parts,
                           whisper=interview_transcribe.configured() and whisper_on(access),
                           transcribing=interview_transcribe.running, transcription_progress=interview_transcribe.progress_info,
                           transcription_error=interview_transcribe.error_info, offline=interview_offline.panel(survey, access))


def whisper_on(access):
    """The Whisper button follows the switch "Generative KI nutzen" of the researcher who made the access."""
    from app import ai_access
    from app.models import User

    by = db.session.get(User, access.created_by_id) if access.created_by_id else None
    return ai_access.ai_on(by) if by is not None else True


@bp.route("/i/<code>/interviews/new", methods=["POST"])
def new(code):
    """A new interview after the consent step, as the researchers start one (interview_data.new), held by this access."""
    from app import interview, interview_data

    survey, access = _mine(code)
    if access is None:
        return _again(survey)
    back = redirect(url_for("interviewer.home", code=survey.code))
    if not request.form.get("consent"):
        flash(de_en("Ohne Einwilligung beginnt kein Interview: bitte „Einwilligung aller Sprechenden liegt vor“ ankreuzen.",
                    "No interview begins without consent: please tick “Consent of everyone who speaks given”."), "error")
        return back
    label = " ".join(request.form.get("label", "").split())[:20] or next_label(survey, access)
    from app import interview_offline

    if label in {(r.interview or {}).get("label") for r in interview_data.interviews(survey)} | interview_offline.reserved_labels(survey):
        flash(de_en("Die Kennung „{label}“ ist schon vergeben.", "The identifier “{label}” is already taken.", label=label), "error")
        return back
    audio = bool(request.form.get("audio")) and access.may_record
    mode = request.form.get("mode") if request.form.get("mode") in interview.MODES else interview.state(survey)["mode"]
    row = Response(survey_id=survey.id, language=survey.default_language, source="test" if request.form.get("trial") else "real",
                   status=Response.ACTIVE, answers={}, pid=new_pid(survey.code), started_at=utcnow(), last_active_at=utcnow(),
                   interview={"label": label, "by": None, "by_name": access.name, "access": access.id,
                              "date": request.form.get("date", "")[:10],
                              "mode": mode, "consent": interview_data.consent_of(request.form, audio),
                              "marks": [], "elapsed": 0, "blocks": {}})
    if not survey.erase_code:  # interviews get a deletion code to hand over (app/erasure.py)
        survey.erase_code = True
    db.session.add(row)
    db.session.commit()
    return redirect(url_for("interview.conduct", code=survey.code, interview=row.id))


@bp.route("/i/<code>/interviews/<int:rid>/delete", methods=["POST"])
def delete(code, rid):
    """Only an own trial interview (decision D); real interviews are deleted by the researchers."""
    from app import erasure

    survey, access = _mine(code)
    if access is None:
        return _again(survey)
    row = db.session.get(Response, rid)
    if row is None or row.survey_id != survey.id or not mine(access, row) or row.source != "test":
        abort(404)
    label = (row.interview or {}).get("label", "")
    erasure.erase(survey, [row], "request")
    flash(de_en("Probeinterview {label} gelöscht.", "Trial interview {label} deleted.", label=label), "success")
    return redirect(url_for("interviewer.home", code=survey.code))


@bp.route("/i/<code>/logout", methods=["POST"])
def logout(code):
    survey = _guide_for_interviewer(code)
    entry = _entries().get(str(survey.id))
    if isinstance(entry, dict) and isinstance(entry.get("a"), int):  # the device signs off and frees its place (K1)
        access = db.session.get(InterviewerAccess, entry["a"])
        if access is not None and access.survey_id == survey.id and entry.get("r") == access.revision:
            _drop_device(access, entry.get("d"))
    _forget(survey)
    return render_template("interviewer/start.html", survey=survey, problem=None, hints=HINTS_VERSION, left=True)

