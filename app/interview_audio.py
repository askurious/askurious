"""Audio recording of an interview ("Interviews erheben", part 3; Ralf 2026-10-04, decision A: on the
Askurious server).

The interview view records in the browser (MediaRecorder) and sends the recording in pieces of a few seconds while
the interview runs, so that a lost connection or a closed tab costs at most the last piece. The pieces of one
recording are appended in order to one file (a WebM/Opus or MP4 stream stays playable when its pieces are put
together); a recording started anew (after a reload, after a pause) is a further part. Only with the consent to the
recording (Response.interview["consent"]["audio"]) and only for "Bearbeiten" and "Admin".

The files lie outside the uploads that Askurious serves (instance/interview-audio/<survey uid>/<interview pid>/), never
publicly reachable; they go with the interview (deletion on request, retention, deleting the guide) and by the
guide's period for recordings ("audio_keep": a week after the transcript of a part is checked, or months after the
interview). Until 2026-10-05 the folder was named after Response.id, which SQLite gives again after the newest row is
deleted (data audit L-4); such a folder moves to the pid at the first access, and run() sweeps folders no interview
holds.

A piece is written at the length the database confirms (data audit L-2): a piece whose commit failed and that the
browser sends again replaces its first copy instead of following it, and each recording ("rec", a random id of the
browser) has a file of its own, so two devices that start at the same moment never write into one file (I-2).

A recording made elsewhere (Zoom, Teams, Webex, BBB; concept konzept-online-interviews-2026-10-15.md, Ralf
2026-10-15: 1c, 2a, 3a, 4a) comes in through the same pieces: the upload page (upload_page) cuts the file in the
browser. Such a part notes "source": "upload", where it was made ("origin") and, if it holds one voice only (Zoom's
"eigene Audiodatei je Person"), whose ("speaker": "I" or "B"); the transcription then labels and interleaves those
parts by time (app/interview_transcribe.py). Only sound is taken: a file with a picture is refused (data
minimisation), and an upload needs the tick of its page that everybody who speaks consented, also to the transfer
(consent["upload"], besides consent["all"] from the start of the interview; § 201 StGB).

  Response.interview["audio"] = [{"part": 1, "file": "teil-1-<rec>.webm", "mime": "audio/webm", "bytes": int,
                                   "pieces": int, "start": seconds of the interview clock when it began, "rec": str,
                                   "at": ISO time the last piece arrived,
                                   uploaded only: "source": "upload", "origin": key of ORIGINS, "speaker": "" | "I" | "B"}]"""
import os
import re
import shutil
import time
from datetime import timedelta

from flask import Blueprint, abort, current_app, jsonify, make_response, request, send_file
from flask_login import login_required
from sqlalchemy import event
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.extensions import db, json_given
from app.interviewer import interviewer_or_signed_in
from app.lang import de_en
from app.models import Response, utcnow
from app import webm_duration
from app.storage_room import remove_file, remove_tree, writing

bp = Blueprint("interview_audio", __name__, url_prefix="/guide")

MIMES = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3", "audio/wav": "wav"}
MAX_PIECE = 4 * 1024 * 1024        # bytes of one piece (a few seconds of speech are some 10 to 100 KB)
MAX_TOTAL = 400 * 1024 * 1024      # bytes of all parts of one interview (several hours of speech)
MAX_PARTS = 50
MIN_FREE = 200 * 1024 * 1024       # free space the disk keeps; below it a piece is refused with 507 (the browser keeps it)
GRACE_DAYS = 7                     # "bis die Transkription geprüft ist": a checked part goes a week after the tick
SWEEP_AGE = 3600                   # seconds before a folder or file no interview holds is swept (a request may be at work)
FILE_RE = re.compile(r"teil-\d{1,3}(-[a-z0-9]{1,16})?\.(webm|ogg|m4a|mp3|wav)")
_DOOMED = "interview_audio_doomed"
_swept = 0.0
ORIGINS = {"zoom": ("Zoom, lokale Aufzeichnung", "Zoom, local recording"), "zoom_cloud": ("Zoom, Cloud-Aufzeichnung", "Zoom, cloud recording"),
           "teams": ("Microsoft Teams", "Microsoft Teams"), "webex": ("Webex", "Webex"), "bbb": ("BigBlueButton", "BigBlueButton"),
           "phone": ("Telefon oder Diktiergerät", "Telephone or voice recorder"), "other": ("andere Quelle", "other source")}
SPEAKERS = {"": ("mehrere Sprechende", "several speakers"), "I": ("nur Interviewer/in (I)", "interviewer only (I)"),
            "B": ("nur befragte Person (B)", "interviewee only (B)")}
# A picture in the first piece: an MP4 track handler "vide" or a Matroska/WebM video codec (data minimisation, 3a).
_PICTURE = re.compile(rb"hdlr.{8}vide|V_VP[89]|V_AV1|V_MPEG|V_MS/VFW", re.DOTALL)


def sniff(head):
    """The container of a recording from its first bytes, or None (what the browser names is not trusted): those of
    the voice element plus MP3 and WAV, which recordings made elsewhere often are."""
    from app.items.voice import sniff as voice_sniff

    found = voice_sniff(head)
    if found:
        return found
    if head[:3] == b"ID3" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio/wav"
    return None


def has_picture(data):
    """Whether the first piece of a file shows a video track (only sound is kept)."""
    return bool(_PICTURE.search(data[:MAX_PIECE]))


def label(key, table):
    """The shown name of a key of ORIGINS or SPEAKERS."""
    return de_en(*table[key]) if key in table else ""


@bp.app_template_global()
def part_label(p):
    """Where an uploaded part comes from and, if it holds one voice, whose: "Zoom, lokale Aufzeichnung · nur I"."""
    return " · ".join(t for t in (label(p.get("origin"), ORIGINS), label(p.get("speaker"), SPEAKERS) if p.get("speaker") else "") if t)


def root():
    return os.path.join(os.path.dirname(current_app.config["UPLOAD_FOLDER"]), "interview-audio")


def _legacy(survey, row):
    return os.path.join(root(), survey.uid, str(row.id))


def folder(survey, row):
    """The interview's folder, named after its pid. A recording from before 2026-10-05 lies under the row's id: it
    moves now (only for an interview that holds parts, so a new interview never takes over a folder left behind)."""
    if not row.pid:
        return _legacy(survey, row)
    where = os.path.join(root(), survey.uid, row.pid)
    if not os.path.isdir(where) and parts(row) and os.path.isdir(_legacy(survey, row)):
        try:
            with writing(survey, 0, check=False):
                os.rename(_legacy(survey, row), where)
        except OSError:
            return _legacy(survey, row)
    return where


def parts(row):
    return [p for p in (row.interview or {}).get("audio") or [] if isinstance(p, dict) and p.get("file")]


def next_part(row):
    """The part number a new recording asks for (the server moves it on if another recording took it)."""
    return max((p.get("part") or 0 for p in parts(row)), default=0) + 1


def path(survey, row, part):
    found = next((p for p in parts(row) if p.get("part") == part), None)
    if found is None or not FILE_RE.fullmatch(found["file"]):
        return None, None
    full = os.path.join(folder(survey, row), found["file"])
    return (full, found) if os.path.isfile(full) else (None, None)


def _taken(audio):
    """409 with the next free part: the part asked for belongs to another recording (a second device records too)."""
    abort(make_response(jsonify(ok=False, part=max((p.get("part") or 0 for p in audio), default=0) + 1), 409))


def _room(where, size):
    """507 when the disk is nearly full: the browser keeps the piece and sends it again later."""
    try:
        free = shutil.disk_usage(where).free
    except OSError:
        return
    if free - size < int(current_app.config.get("INTERVIEW_AUDIO_MIN_FREE", MIN_FREE)):
        current_app.logger.error("interview recording refused: only %s bytes free on the disk", free)
        abort(make_response(jsonify(ok=False, error="full"), 507))


def append(survey, row, part, seq, mime, data, start, rec="", meta=None):
    """Add one piece to a part (seq counts from 0; a piece sent twice is ignored, a gap refused). Returns the next seq.
    `rec` names the recording: a part another recording already holds is refused with the next free part, so two
    devices that both start at the same part number never write into one file. `meta` ({"source", "origin",
    "speaker"}) goes with a part a file was uploaded into; its first piece must hold no picture."""
    if mime not in MIMES:
        abort(415)
    if not data or len(data) > MAX_PIECE:
        abort(413)
    audio = [dict(p) for p in parts(row)]  # copies: changing the loaded JSON in place would not be stored
    if sum(p.get("bytes") or 0 for p in audio) + len(data) > MAX_TOTAL:
        abort(413)
    current = next((p for p in audio if p.get("part") == part), None)
    if current is None:
        if seq != 0 or len(audio) >= MAX_PARTS:  # the reason goes along: the page stops sending and names it (F15)
            abort(make_response(jsonify(ok=False, error="gap" if seq != 0 else "parts"), 409))
        if part != max((p.get("part") or 0 for p in audio), default=0) + 1:
            _taken(audio)
        if sniff(data[:12]) != mime:  # the first piece starts the container the browser named (audit 2026-10-13, DA-6)
            abort(415)
        if meta and has_picture(data):
            abort(make_response(jsonify(ok=False, error="video"), 415))
        current = {"part": part, "file": f"teil-{part}{'-' + rec if rec else ''}.{MIMES[mime]}", "mime": mime, "bytes": 0,
                   "pieces": 0, "start": max(0, min(int(start or 0), 86400)), "rec": rec, **(meta or {})}
        audio.append(current)
    elif rec and current.get("rec") and current["rec"] != rec:
        _taken(audio)
    if seq < current["pieces"]:
        return current["pieces"]  # sent twice (an answer that got lost): already there
    if seq > current["pieces"]:
        abort(make_response(jsonify(ok=False, error="gap"), 409))
    where = folder(survey, row)
    os.makedirs(where, exist_ok=True)
    from app import media

    full = os.path.join(where, current["file"])
    try:
        with media.writing(survey, 0, check=False):
            _room(where, len(data))
            existing = os.path.getsize(full) if os.path.exists(full) else 0
            media.check_room(survey, max(0, current["bytes"] + len(data) - existing))
            with open(full, "r+b" if os.path.exists(full) else "wb") as f:
                # Retry overwrites bytes whose database commit failed rather than counting them twice.
                f.seek(current["bytes"])
                f.write(data)
                f.truncate()
    except ValueError:
        abort(make_response(jsonify(ok=False, error="full"), 507))
    current["bytes"] += len(data)
    current["pieces"] += 1
    current["at"] = utcnow().isoformat(timespec="seconds")
    row.interview = {**(row.interview or {}), "audio": [dict(p) for p in audio]}
    row.last_active_at = utcnow()
    return current["pieces"]


def retried(work):
    """work() and the commit; when another request stored the same interview in the meantime (its version column:
    StaleDataError, e.g. the autosave of the interview view or the report of the recording device), the row is
    loaded afresh and work() runs once more. A second conflict goes to the app's handler (409 "stale")."""
    try:
        result = work()
        db.session.commit()
        return result
    except StaleDataError:
        db.session.rollback()  # expires the row: work() reads the stored state
    result = work()
    db.session.commit()
    return result


def clock(row, now=None):
    """Seconds of the interview clock now, as the interview view last sent it (app/interview_data.save)."""
    from datetime import datetime

    meta = row.interview or {}
    seconds = int(meta.get("elapsed") or 0)
    if meta.get("running") and meta.get("clock_at"):
        try:
            seconds += max(0, int(((now or utcnow()) - datetime.fromisoformat(meta["clock_at"])).total_seconds()))
        except ValueError:
            pass
    return min(seconds, 86400)


def remove(survey, row):
    """Delete the recording of an interview (the caller commits)."""
    _cancel_jobs(row)
    remove_tree(folder(survey, row), ignore_errors=True)
    if row.interview and row.interview.get("audio"):
        row.interview = {**row.interview, "audio": [], "audio_removed": utcnow().isoformat(timespec="seconds")}


def remove_parts(survey, row, numbers):
    """Delete some parts of the recording (the caller commits); without parts left, the whole folder."""
    left = [p for p in parts(row) if p.get("part") not in numbers]
    if not left:
        remove(survey, row)
        return
    where = folder(survey, row)
    _cancel_jobs(row, numbers)
    for p in parts(row):
        if p.get("part") in numbers and FILE_RE.fullmatch(p["file"]):
            try:
                remove_file(os.path.join(where, p["file"]))
            except FileNotFoundError:
                pass
    row.interview = {**row.interview, "audio": [dict(p) for p in left], "audio_removed": utcnow().isoformat(timespec="seconds")}


@event.listens_for(Response, "after_delete")
def _doomed(mapper, connection, target):
    """An interview that goes takes its recording along, once the deletion is committed."""
    _cancel_jobs(target)
    if not (target.interview and target.interview.get("audio")):
        return
    from app.models import Survey

    uid = connection.execute(db.select(Survey.uid).where(Survey.id == target.survey_id)).scalar()
    if uid:
        doomed = Session.object_session(target).info.setdefault(_DOOMED, [])
        doomed.append(os.path.join(root(), uid, str(target.id)))  # a folder from before 2026-10-05
        if target.pid:
            doomed.append(os.path.join(root(), uid, target.pid))


@event.listens_for(Session, "after_commit")
def _remove_doomed(session):
    from app.whisper_jobs import cancel_records

    pending = session.info.pop("whisper_cancel", [])
    if pending:
        # One unavailable service must not add a network timeout for every deleted interview.
        records = {str(index): record for index, record in enumerate(record for group in pending for record in group.values())}
        cancel_records(records)
    for f in session.info.pop(_DOOMED, []):
        remove_tree(f, ignore_errors=True)


@event.listens_for(Session, "after_rollback")
def _keep_doomed(session):
    session.info.pop(_DOOMED, None)
    session.info.pop("whisper_cancel", None)


def _cancel_jobs(row, numbers=None):
    """Remote erasure follows a successful database commit, also for person-code erasure."""
    records = ((row.interview or {}).get("transcript") or {}).get("jobs") or {}
    if not isinstance(records, dict):
        return
    selected = {str(key): dict(value) for key, value in records.items()
                if isinstance(value, dict) and (numbers is None or str(key) in {str(n) for n in numbers})}
    session = Session.object_session(row)
    if selected and session is not None:
        session.info.setdefault("whisper_cancel", []).append(selected)


# --- the periods ---------------------------------------------------------------------------------------------------


def covered(row):
    """{part (str): bytes} of the recording the transcript's text was made from: the parts Whisper transcribed or
    that were there when a transcript was uploaded (transcript["covers"]), as long as they are unchanged. A
    transcript typed by hand has no such note: then the recording as it is now."""
    tr = (row.interview or {}).get("transcript") or {}
    now = {str(p.get("part")): p.get("bytes") for p in parts(row)}
    covers = tr.get("covers")
    if not isinstance(covers, dict):
        return now
    return {k: b for k, b in covers.items() if now.get(k) == b}


def checked_parts(row):
    """The parts the checked transcript holds (noted when "Transkript geprüft" was ticked; from before 2026-10-05
    without the note: the parts the transcript covers)."""
    tr = (row.interview or {}).get("transcript") or {}
    if not tr.get("checked"):
        return {}
    noted = tr.get("checked_parts")
    return dict(noted) if isinstance(noted, dict) else covered(row)


def _end(row):
    """The end of the interview the periods count from; an interview never ended counts from its last activity
    (data audit L-3: else it would be kept for ever)."""
    return row.completed_at or row.last_active_at or row.started_at


def _doomed_parts(row):
    """The parts the checked transcript holds, unchanged since (a part recorded or grown later stays)."""
    held = checked_parts(row)
    return {p["part"] for p in parts(row) if held.get(str(p.get("part"))) == p.get("bytes")}


def due(survey, row):
    """When the recording (or, with "transcript", the parts the checked transcript holds) goes by the guide's period:
    months after the end of the interview; with "transcript" a week after the tick; None while neither applies."""
    from datetime import datetime

    from app import interview
    from app.retention import _add_months

    keep = interview.state(survey)["audio_keep"]
    if keep == "transcript":
        checked = ((row.interview or {}).get("transcript") or {}).get("checked")
        if not checked or not _doomed_parts(row):
            return None
        try:
            return datetime.fromisoformat(checked) + timedelta(days=GRACE_DAYS)
        except ValueError:
            return utcnow() + timedelta(days=GRACE_DAYS)
    end = _end(row)
    return _add_months(end, int(keep)) if end else None


def _drop_draft(row):
    """Whisper's draft beside a corrected transcript (app/interview_transcribe.py) holds the raw text of the
    recording, real names included: it goes with the recording's period (audit 2026-10-10, E-1). True if one went."""
    tr = (row.interview or {}).get("transcript") or {}
    if "draft" not in tr and "draft_covers" not in tr:
        return False
    row.interview = {**row.interview, "transcript": {k: v for k, v in tr.items() if k not in ("draft", "draft_covers")}}
    return True


def run(now=None):
    """Delete the recordings whose period is over (with interview_data.run), then the folders no interview holds."""
    from app import interview, interview_data
    from app.models import Survey

    now, gone = now or utcnow(), 0
    for survey in db.session.scalars(db.select(Survey).filter(json_given(Survey.interview))).all():
        transcript = interview.state(survey)["audio_keep"] == "transcript"
        for row in interview_data.interviews(survey):
            when = due(survey, row) if parts(row) else None
            if when is not None and when <= now:
                if transcript:
                    remove_parts(survey, row, _doomed_parts(row))
                else:
                    remove(survey, row)
                _drop_draft(row)
                gone += 1
            elif not parts(row) and _drop_draft(row):  # the recording is gone (removed by hand): its draft too
                gone += 1
    if gone:
        db.session.commit()
    global _swept
    if time.time() - _swept > SWEEP_AGE:  # the housekeeping runs every few minutes; the sweep once an hour is enough
        _swept = time.time()
        sweep(now)
    return gone


def sweep(now=None):
    """Remove what no interview holds (data audit L-4): folders of deleted guides and interviews whose removal failed,
    and files of a part that was never stored (a commit refused). Never raises; returns how many went."""
    from app.models import Survey

    base = root()
    if not os.path.isdir(base):
        return 0
    cutoff = (now.timestamp() if now else time.time()) - SWEEP_AGE
    gone = 0
    try:
        for uid in os.listdir(base):
            survey = db.session.scalar(db.select(Survey).filter_by(uid=uid))
            if survey is None:
                remove_tree(os.path.join(base, uid), ignore_errors=True)
                gone += 1
                continue
            rows = db.session.scalars(db.select(Response).filter(Response.survey_id == survey.id,
                                                                 Response.interview.is_not(None))).all()
            held = {}
            for row in rows:
                if parts(row):
                    held[os.path.basename(folder(survey, row))] = {p["file"] for p in parts(row)}
                elif row.pid:
                    held[row.pid] = set()
            for name in os.listdir(os.path.join(base, uid)):
                where = os.path.join(base, uid, name)
                if name not in held:
                    if os.path.getmtime(where) < cutoff:
                        remove_tree(where, ignore_errors=True)
                        gone += 1
                    continue
                for fname in os.listdir(where):
                    full = os.path.join(where, fname)
                    if fname not in held[name] and os.path.getmtime(full) < cutoff:
                        remove_file(full)
                        gone += 1
    except OSError as e:
        current_app.logger.error("Interview recordings not swept: %s", e)
    return gone


# --- the routes ----------------------------------------------------------------------------------------------------


def _row(code, rid, need="editor", right=None):
    """(survey, interview) for a researcher with `need`, or an interviewer's own interview (app/interviewer.py)."""
    from app import interviewer

    return interviewer.row_for(code, rid, need=need, right=right)


PRIVATE = ("interview_audio", "interview_transcribe", "interview_results", "interview_summaries", "interview_data",
           "interview_search", "interview_remote")


@bp.after_app_request
def _private(response):
    """Recordings, transcripts, notes and their exports never stay in the browser's cache (a shared tablet of the
    institute; data audit I-18)."""
    if request.blueprint in PRIVATE:
        response.headers["Cache-Control"] = "private, no-store"
    return response


@bp.route("/<code>/interviews/<int:rid>/audio", methods=["POST"])
@interviewer_or_signed_in
def upload(code, rid):
    survey, row = _row(code, rid, right="record")
    if not ((row.interview or {}).get("consent") or {}).get("audio"):
        abort(403)  # no consent to the recording: nothing is taken
    mime = (request.content_type or "").split(";")[0].strip().lower()
    data = request.get_data(cache=False)
    rec = re.sub(r"[^a-z0-9]", "", request.args.get("rec", "").lower())[:16]
    part, seq = request.args.get("part", type=int) or 0, request.args.get("seq", type=int) or 0
    meta = None
    if request.args.get("source") == "upload":  # a file recorded elsewhere (upload_page)
        meta = {"source": "upload", "origin": request.args.get("origin") if request.args.get("origin") in ORIGINS else "other",
                "speaker": request.args.get("speaker") if request.args.get("speaker") in SPEAKERS else ""}

    def work():
        # the tick of the upload page: everybody who speaks consented before the recording and to its transfer
        # (§ 201 StGB); asked once per interview, also when the tick at its start ("all") was given (Ralf 2026-10-15)
        if meta is not None and not ((row.interview or {}).get("consent") or {}).get("upload"):
            if request.args.get("all") != "1":
                abort(make_response(jsonify(ok=False, error="consent"), 403))  # everybody who speaks (§ 201 StGB)
            now = utcnow().isoformat(timespec="seconds")
            old = (row.interview or {}).get("consent") or {}
            consent = {**old, "all": old.get("all") or now, "upload": now}
            row.interview = {**(row.interview or {}), "consent": consent}
        if meta is not None:
            return append(survey, row, part, seq, mime, data, request.args.get("start", type=int) or 0, rec, meta)
        start = request.args.get("start", type=int)
        if start is None:  # from the recording device (app/interview_remote.py): it does not know the interview clock;
            # it sends how long ago (ms) its recording began, so the part begins there and not when its first piece
            # got through (data audit I-6)
            age = max(0, min(request.args.get("age", type=int) or 0, 86400 * 1000))
            start = clock(row) - age // 1000
        return append(survey, row, part, seq, mime, data, start, rec)

    return jsonify(ok=True, next=retried(work))


BATCH_PIECES = 12                 # pieces of one batch (a minute of recording)
BATCH_BYTES = 2 * 1024 * 1024     # bytes of one batch, well below MAX_CONTENT_LENGTH and nginx's 11m


@bp.route("/<code>/interviews/<int:rid>/audio/batch", methods=["POST"])
@interviewer_or_signed_in
def upload_batch(code, rid):
    """Several pieces in one request (Ralf 2026-10-22, offline J): after a long time without a connection the browser
    catches up with a minute of recording per request instead of one request every five seconds. Form fields: "meta"
    (JSON list of {part, seq, rec, start or age}) and the files "p0", "p1", … in that order, all with one type. Stored
    as one by one in upload() and committed together; any refusal refuses the whole batch with the answer the single
    piece would get, and the page then sends piece by piece (409 of another part, a gap)."""
    import json

    survey, row = _row(code, rid, right="record")
    if not ((row.interview or {}).get("consent") or {}).get("audio"):
        abort(403)
    try:
        metas = json.loads(request.form.get("meta") or "[]")
    except ValueError:
        abort(400)
    if not isinstance(metas, list) or not metas or len(metas) > BATCH_PIECES:
        abort(400)
    files = [request.files.get(f"p{n}") for n in range(len(metas))]
    if any(f is None for f in files):
        abort(400)
    blobs = [f.read(MAX_PIECE + 1) for f in files]
    if sum(len(b) for b in blobs) > BATCH_BYTES:
        abort(413)
    mime = (files[0].mimetype or "").split(";")[0].strip().lower()

    def work():
        last = 0
        for meta, data in zip(metas, blobs):
            if not isinstance(meta, dict):
                abort(400)
            rec = re.sub(r"[^a-z0-9]", "", str(meta.get("rec") or "").lower())[:16]
            part, seq = int(meta.get("part") or 0), int(meta.get("seq") or 0)
            start = meta.get("start")
            if start is None:  # the recording device: how long ago its recording began (upload())
                age = max(0, min(int(meta.get("age") or 0), 86400 * 1000))
                start = clock(row) - age // 1000
            last = append(survey, row, part, seq, mime, data, int(start), rec)
        return last

    return jsonify(ok=True, next=retried(work), pieces=len(metas))


@bp.route("/<code>/interviews/<int:rid>/netcheck", methods=["POST"])
@interviewer_or_signed_in
def netcheck(code, rid):
    """The connection check before an interview (offline K): the page sends some 100 KB and times the answer; nothing
    is kept."""
    _row(code, rid, right="record")
    request.get_data(cache=False)
    return jsonify(ok=True)


@bp.route("/<code>/interviews/<int:rid>/audio/upload")
@interviewer_or_signed_in
def upload_page(code, rid):
    """A recording made elsewhere (Zoom, Teams, Webex, BBB, a voice recorder) added to the interview: the page cuts
    each file into pieces and sends them like a recording of the interview view (upload())."""
    from flask import render_template

    from app import interviewer

    survey, row = _row(code, rid, right="record")
    if not ((row.interview or {}).get("consent") or {}).get("audio"):
        abort(403)
    used = sum(p.get("bytes") or 0 for p in parts(row))
    return render_template("interview/audio_upload.html", survey=survey, row=row, origins=ORIGINS, speakers=SPEAKERS,
                           label=label, piece=MAX_PIECE, left=max(0, MAX_TOTAL - used), parts_left=MAX_PARTS - len(parts(row)),
                           next_part=next_part(row), back=interviewer.list_url(survey),
                           whisper_jobs=bool(current_app.config.get("WHISPER_JOBS")))


@bp.route("/<code>/interviews/<int:rid>/audio/<int:part>")
@interviewer_or_signed_in
def listen(code, rid, part):
    survey, row = _row(code, rid)
    full, found = path(survey, row, part)
    if full is None:
        abort(404)
    label = (row.interview or {}).get("label", str(row.id))
    name = f"{survey.code}-{label}-teil-{part}.{MIMES.get(found['mime'], 'webm')}"
    download = request.args.get("download") == "1"
    plan = webm_duration.head_plan(full) if found["mime"] == "audio/webm" else None
    if plan is None:
        return send_file(full, mimetype=found["mime"], as_attachment=download, download_name=name, conditional=True)
    # A recorder's WebM names no length (the player showed 0:00): served with a head that names it (app/webm_duration.py)
    from werkzeug.wsgi import wrap_file

    joined = webm_duration.Joined(plan[0], full, plan[1])
    response = current_app.response_class(wrap_file(request.environ, joined), mimetype=found["mime"], direct_passthrough=True)
    response.headers.set("Content-Disposition", "attachment" if download else "inline", filename=name)
    stat = os.stat(full)
    response.last_modified = int(stat.st_mtime)
    response.set_etag(f"wd-{stat.st_size}-{stat.st_mtime_ns}")
    response.content_length = joined.length
    return response.make_conditional(request, accept_ranges=True, complete_length=joined.length)


@bp.route("/<code>/interviews/<int:rid>/audio/delete", methods=["POST"])
@login_required
def delete(code, rid):
    from flask import flash, redirect, url_for

    survey, row = _row(code, rid, need="admin")  # deleting data: admins (security audit 2026-10-06, S22)
    remove(survey, row)
    db.session.commit()
    flash(de_en("Aufnahme von {label} gelöscht.", "Recording of {label} deleted.", label=(row.interview or {}).get("label", "")), "success")
    return redirect(url_for("interview_data.index", code=survey.code))
