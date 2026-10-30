"""Transcription of an interview's recording by the institute's own Whisper server ("Interviews erheben",
part 4; Ralf 2026-10-04, decision B: a local Whisper server, so the voice never leaves the institute's servers and goes
to no third party).

The server is named in the .env: WHISPER_URL (base address), optionally WHISPER_TOKEN (sent as Bearer), WHISPER_MODEL
and WHISPER_PATH (default /v1/audio/transcriptions, the OpenAI-compatible interface of faster-whisper-server,
speaches or whisper.cpp with --convert). Without WHISPER_URL the button does not appear. The title of the guide and
the names of its Themenblöcke go along as "prompt" (names and terms spelled as in the guide); a server that refuses
the field gets the request again without it.
With WHISPER_JOBS=1 the local WhisperX service uses a durable queue instead:
job IDs are retained per recording part for resume and committed audio/interview erasure.

A transcription runs in a thread of its own (a recording of an hour takes minutes); the list of interviews shows its
state. The thread holds no database connection while Whisper works (data audit L-6, I-25): it stores what it has
after each part, each time on the interview loaded afresh and only under the key "transcript", so a case summary, a
note or a piece of the recording stored meanwhile is kept, and a conflict is tried again. While it waits it leaves a
sign of life ("beat") every BEAT seconds; a transcription without one for STALE_BEAT seconds counts as broken off (a
restart or deploy ends the thread, I-7) and may be started again, as may one cancelled with "Abbrechen". The parts
done so far stay ("parts"), and a new run sends only the parts still missing (I-21). A failure of the moment
(TransientError: the service unreachable, a 5xx, a job lost with a restart) is tried again by the run itself after the
pauses of RETRY_WAITS, while the list shows "Transkription läuft" (Ralf 2026-10-30).

The result is a draft after the rules of Dresing & Pehl (2018): paragraphs with a time mark #hh:mm:ss-0# at the end,
in the time of the interview clock (the part's start plus the time within it); a part whose file is missing is
noted as "[Teil n fehlt]". Optional WhisperX speaker labels are local to a recording part;
"I:" and "B:" are set by the researchers when they check the transcript, except for a part uploaded as one voice
("speaker" in app/interview_audio.py, Zoom's file per person): its paragraphs carry that label, and all such parts
are interleaved by time where the first of them stands (concept konzept-online-interviews-2026-10-15.md, 2a).
"Transkript geprüft" marks it as checked; with the period "bis die Transkription geprüft ist"
the parts of the recording it holds go a week later (app/interview_audio.py).

  Response.interview["transcript"] = {"state": "running" | "done" | "error", "started", "beat", "run", "finished",
                                       "error", "model", "seconds", "text", "covers": {part: bytes},
                                       "parts": {part: {"bytes", "text"}} while unfinished,
                                       "draft": Whisper's text only while it differs from "text" (a run after a
                                                correction; the transcript page offers to take it over or discard
                                                it, and it goes with the period of the recording),
                                       "draft_covers": the "covers" of the draft,
                                       "checked": ISO or None, "checked_parts": {part: bytes}, "edited": bool}"""
import os
import hmac
import re
import secrets
import threading
import time
from datetime import datetime, timedelta

import requests
from flask import Blueprint, abort, current_app, flash, jsonify, redirect, render_template, request, send_file, url_for
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm.exc import StaleDataError

from app import autosave, interviewer
from app.extensions import db, csrf, json_given
from app.interviewer import interviewer_or_signed_in
from app.lang import de_en
from app.models import Response, utcnow
from app.whisper_jobs import TransientError

bp = Blueprint("interview_transcribe", __name__, url_prefix="/guide")


@bp.route("/internal/whisper/cancel", methods=["POST"])
@csrf.exempt
def cancel_remote_jobs():
    """Erasure relay for participants and housekeeping; never reads audio or transcripts. Only for the app's own
    containers: its key comes from SECRET_KEY (whisper_jobs.relay_key), and nginx answers 404 from outside."""
    from app.whisper_jobs import relay_key

    secret = relay_key()
    if not secret or not hmac.compare_digest(secret.encode(), request.headers.get("X-Whisper-Cancel", "").encode()):
        abort(403)
    if not current_app.config.get("WHISPER_JOBS") or not current_app.config.get("WHISPER_TOKEN"):
        abort(503)
    if request.content_length is None or request.content_length > 16384:
        abort(413)
    payload = request.get_json(silent=True)
    records = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(records, dict) or len(records) > 32:
        abort(400)
    from app.whisper_jobs import cancel_records
    if not cancel_records(records, allow_relay=False):
        abort(503)
    return {"ok": True}


@bp.route("/internal/whisper/resume", methods=["POST"])
@csrf.exempt
def resume_remote_jobs():
    """Resume relay for housekeeping (resume_stale): the transcriptions broken off by a restart are taken up here,
    in the researchers' container, which alone reaches the Whisper service. Same key as the cancel relay."""
    from app.whisper_jobs import here, relay_key

    secret = relay_key()
    if not secret or not hmac.compare_digest(secret.encode(), request.headers.get("X-Whisper-Cancel", "").encode()):
        abort(403)
    if not here():
        abort(503)
    return {"taken": resume_stale()}


PARAGRAPH_GAP = 2.0      # seconds of silence between two segments that start a new paragraph
PARAGRAPH_CHARS = 700    # a paragraph longer than this ends at the next segment
MAX_TEXT = 400_000
MAX_PROMPT = 600         # characters of the prompt (Whisper reads some 220 tokens of it)
BEAT = 30                # seconds between two signs of life of a running transcription
STALE_BEAT = 120         # a "running" without a sign of life for this long counts as broken off
RETRY_WAITS = (60, 180, 600, 1200)  # seconds before a part is tried again after a failure of the moment (some 34
                                    # minutes in all, enough for a restart of the service); then it fails as before
VOICES = ("I", "B")      # the speaker of a part that holds one voice only (app/interview_audio.SPEAKERS)
_IN_FLIGHT = {}          # interview id -> requests to Whisper under way in this process (see busy(), I11)
_FLIGHT_LOCK = threading.Lock()


def _default_model(config):
    """The model a transcription takes when nobody chose one: with the local WhisperX queue large-v3-turbo unless the
    .env names small (whisper_jobs.default_model; Ralf, 2026-10-18), else the .env's model for the outside server."""
    if config.get("WHISPER_JOBS"):
        from app import whisper_jobs
        return whisper_jobs.default_model(config)
    return config.get("WHISPER_MODEL") or "whisper-1"


def configured():
    return bool(current_app.config.get("WHISPER_URL"))


def state(row):
    return dict((row.interview or {}).get("transcript") or {})


def _set(row, **values):
    row.interview = {**(row.interview or {}), "transcript": {**state(row), **values}}


def _now():
    return utcnow().isoformat(timespec="seconds")


def running(row):
    """Whether a transcription runs: "running" with a fresh sign of life."""
    st = state(row)
    if st.get("state") != "running":
        return False
    try:
        return utcnow() - datetime.fromisoformat(st.get("beat") or st.get("started")) < timedelta(seconds=STALE_BEAT)
    except (TypeError, ValueError):
        return False


def paragraphs(segments, voices=False):
    """[(end in seconds, text)] from Whisper's segments [(start, end, text)] (already in the interview's time).
    voices: the segments come from parts of one voice each, labelled "I:" and "B:" (a change of label starts a
    paragraph, as one of WhisperX's labels does)."""
    out, text, last_end, speaker = [], "", None, None
    for start, end, piece in segments:
        piece = " ".join(str(piece or "").split())
        if not piece:
            continue
        found = re.match(r"^((?:P\d+-)?S\d+|I|B):\s*" if voices else r"^((?:P\d+-)?S\d+):\s*", piece)
        tag = found.group(1) if found else None
        if text and (tag != speaker or start - (last_end or start) > PARAGRAPH_GAP or len(text) > PARAGRAPH_CHARS):
            out.append((last_end, text))
            text = ""
        if text and found:
            piece = piece[found.end():]
        text = f"{text} {piece}".strip()
        last_end = end
        speaker = tag
    if text:
        out.append((last_end, text))
    return out


def draft(segments, voices=False):
    from app.interview_results import stamp

    return "\n\n".join(f"{text} {stamp(end)}" for end, text in paragraphs(segments, voices))


def _language(survey):
    return (survey.default_language or "de").split("-")[0]


def _prompt(survey):
    """The guide's title and the names of its Themenblöcke, for Whisper's prompt."""
    from app import interview

    words = [survey.title or ""] + [b.title() for b in interview.guide_blocks(survey) if not b.default_named()]
    return ". ".join(" ".join(w.split()) for w in words if w and w.strip())[:MAX_PROMPT]


def transcribe_file(path, mime, language, prompt="", previous=None, created=None, progress=None, model=None):
    """Whisper's segments [(start, end, text)] of one file (raises RuntimeError with a readable reason)."""
    config = current_app.config
    if config.get("WHISPER_JOBS"):
        from app import whisper_jobs

        try:
            data = whisper_jobs.transcribe(path, language, prompt, previous, created, progress, model)
        except requests.RequestException as error:
            raise TransientError(de_en("Der Transkriptionsdienst ist nicht erreichbar ({e}).",
                                     "The transcription service cannot be reached ({e}).", e=error.__class__.__name__)) from error
        return _segments(data)
    url = config["WHISPER_URL"].rstrip("/") + (config.get("WHISPER_PATH") or "/v1/audio/transcriptions")
    headers = {"Authorization": f"Bearer {config['WHISPER_TOKEN']}"} if config.get("WHISPER_TOKEN") else {}
    fields = {"model": config.get("WHISPER_MODEL") or "whisper-1", "language": language, "response_format": "verbose_json"}

    def ask(data):
        with open(path, "rb") as f:
            return requests.post(url, headers=headers, timeout=int(config.get("WHISPER_TIMEOUT") or 3600),
                                 files={"file": (os.path.basename(path), f, mime)}, data=data)

    try:
        r = ask({**fields, "prompt": prompt} if prompt else fields)
        if prompt and r.status_code in (400, 422):  # a server that does not take the prompt
            r = ask(fields)
    except requests.RequestException as e:
        raise TransientError(de_en("Der Whisper-Server ist nicht erreichbar ({e}).", "The Whisper server cannot be reached ({e}).",
                                 e=e.__class__.__name__)) from e
    if r.status_code >= 400:
        raise (TransientError if r.status_code >= 500 or r.status_code == 429 else RuntimeError)(de_en("Der Whisper-Server antwortet mit {s}.", "The Whisper server answers with {s}.",
                                 s=r.status_code))
    try:
        data = r.json()
    except ValueError:
        return [(0.0, 0.0, r.text)]  # a server that answers with plain text
    return _segments(data)


def _segments(data):
    segments = []
    for segment in data.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        text = segment.get("text") or ""
        speaker = re.fullmatch(r"SPEAKER_(\d{1,4})", str(segment.get("speaker", "")))
        if speaker:
            text = f"S{int(speaker.group(1)) + 1}: {text.strip()}"
        segments.append((float(segment.get("start") or 0), float(segment.get("end") or 0), text))
    return segments or [(0.0, float(data.get("duration") or 0), data.get("text") or "")]


class _Cancelled(Exception):
    """The transcription was cancelled or started anew meanwhile: its result is dropped."""


def _store(rid, change, tries=5):
    """Merge a change into the stored transcript: the interview loaded afresh, change(transcript) gives the new one
    (None: nothing to store), only the key "transcript" is written; another request that stored the interview in
    between makes it try again. Returns the stored transcript or None. The session is closed afterwards, so no
    connection is held while Whisper works."""
    try:
        for attempt in range(tries):
            row = db.session.get(Response, rid, populate_existing=True)
            new = change(state(row)) if row is not None else None
            if new is None:
                db.session.rollback()
                return None
            row.interview = {**(row.interview or {}), "transcript": new}
            try:
                db.session.commit()
                return new
            except (StaleDataError, OperationalError):
                db.session.rollback()
                if attempt == tries - 1:
                    raise
                time.sleep(0.2 * (attempt + 1))
    finally:
        db.session.close()


def _still_mine(rid, mine):
    """Whether the run that asks is still the interview's current one (mine); True when that cannot be read, so a
    failure is never hidden."""
    try:
        row = db.session.get(Response, rid, populate_existing=True)
        return row is not None and mine(state(row))
    except Exception:  # noqa: BLE001 - the database itself is gone: report the failure as before
        db.session.rollback()
        return True
    finally:
        db.session.close()


def _jobs(rid):
    """The service's job records as stored now (a try that failed may have stored a new one meanwhile)."""
    try:
        row = db.session.get(Response, rid, populate_existing=True)
        return dict(state(row).get("jobs") or {}) if row is not None else {}
    finally:
        db.session.close()


def _pause(rid, mine, seconds, key):
    """Wait before a part is tried again, with a sign of life every BEAT seconds; the list shows "Transkription läuft"
    meanwhile, no error (Ralf 2026-10-30). A cancel or a new start ends the wait."""
    end = time.monotonic() + seconds
    while True:
        if _store(rid, lambda tr: {**tr, "beat": _now(), "progress": {"stage": "retrying", "part": key}}
                  if mine(tr) else None) is None:
            raise _Cancelled()
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(BEAT, left))


def _waiting(app, rid, mine, job):
    """job() (the request to Whisper) in a thread of its own, meanwhile a sign of life every BEAT seconds."""
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as StillWaiting

    def inner():
        with _FLIGHT_LOCK:
            _IN_FLIGHT[rid] = _IN_FLIGHT.get(rid, 0) + 1
        try:
            with app.app_context():
                return job()
        finally:
            with _FLIGHT_LOCK:
                _IN_FLIGHT[rid] -= 1
                if not _IN_FLIGHT[rid]:
                    del _IN_FLIGHT[rid]

    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"whisper-{rid}-request")
    try:
        future = pool.submit(inner)
        while True:
            try:
                return future.result(timeout=BEAT)
            except StillWaiting:
                if _store(rid, lambda tr: {**tr, "beat": _now()} if mine(tr) else None) is None:
                    raise _Cancelled() from None
    finally:
        pool.shutdown(wait=False)  # a cancelled request runs out by itself; its result is not used


def _assemble(tr, plan):
    """The draft of the parts done so far, in the order of the recording, and the bytes of each part it covers:
    (text, covers). A part not done (yet) is noted as "[Teil n fehlt]"."""
    got, pieces, covers = tr.get("parts") or {}, [], {}
    voices, at = [], None  # the parts of one voice each, interleaved by time where the first of them stood
    for key, full, found in plan:
        entry = got.get(key) or {}
        if "text" not in entry or (full is not None and entry.get("bytes") != found.get("bytes")):
            pieces.append(f"[Teil {key} fehlt]")
            continue
        if full is not None:
            covers[key] = entry.get("bytes")
        if isinstance(entry.get("segments"), list):
            voices += [tuple(s) for s in entry["segments"]]
            if at is None:
                at = len(pieces)
                pieces.append("")
            continue
        if entry["text"]:
            pieces.append(entry["text"])
    if at is not None:
        pieces[at] = draft(sorted(voices, key=lambda s: (s[0], s[1])), voices=True)
    pieces = [p for p in pieces if p]
    return "\n\n".join(pieces)[:MAX_TEXT], covers


def _plan(survey, row):
    """The parts of the recording in order: [(part as a key, file or None, its entry)]."""
    from app import interview_audio

    plan = []
    for p in interview_audio.parts(row):
        full, found = interview_audio.path(survey, row, p.get("part"))
        plan.append((str(p.get("part")), full, dict(found or p)))
    return plan


def _partial(tr, plan):
    """On an error: the parts done so far as the transcript ("partial"), so that they can be read and checked
    (pre-test 2026-10-16: finished parts stayed hidden behind the error). A checked or corrected text stays; the
    parts stay too, so a new run sends only the missing ones."""
    if not (tr.get("parts") or {}) or (tr.get("edited") and tr.get("text")):
        return {}
    text, covers = _assemble(tr, plan)
    return {"text": text, "covers": covers, "partial": True}


def work(app, survey_id, rid, run=None):
    """The transcription itself (in a thread): the parts of the recording not done yet in order, each stored when it
    is done, then the draft."""
    from app.models import Survey

    def mine(tr):
        return tr.get("state") == "running" and (run is None or tr.get("run") == run)

    plan = []  # (part as a key, file or None, its entry), read below: nothing of the row is used later
    with app.app_context():
        try:
            survey, row = db.session.get(Survey, survey_id), db.session.get(Response, rid)
            if survey is None or row is None:
                return
            language, prompt, began = _language(survey), _prompt(survey), time.monotonic()
            requested_model = state(row).get("requested_model") or _default_model(app.config)
            plan[:] = _plan(survey, row)
            done = dict(state(row).get("parts") or {})
            remote = dict(state(row).get("jobs") or {})
            db.session.close()
            for key, full, found in plan:
                if full is None or (done.get(key) or {}).get("bytes") == found.get("bytes"):
                    continue  # missing (noted in the draft) or done in an earlier run
                offset = float(found.get("start") or 0)
                def request_part(full=full, found=found, key=key):
                    if not app.config.get("WHISPER_JOBS"):
                        return transcribe_file(full, found["mime"], language, prompt)

                    def created(record):
                        entry = {**record, "bytes": found.get("bytes")}
                        if _store(rid, lambda tr: {**tr, "jobs": {**(tr.get("jobs") or {}), key: entry}} if mine(tr) else None) is None:
                            raise _Cancelled()

                    previous = remote.get(key)
                    if not isinstance(previous, dict) or previous.get("bytes") != found.get("bytes"):
                        previous = None
                    last_progress = None
                    def progressed(value):
                        nonlocal last_progress
                        value = {**value, "part": key}
                        if value != last_progress:
                            if _store(rid, lambda tr: {**tr, "progress": value} if mine(tr) else None) is None:
                                raise _Cancelled()
                            last_progress = value
                    return transcribe_file(full, found["mime"], language, prompt, previous=previous, created=created,
                                           progress=progressed, model=requested_model)

                waits = list(app.config.get("WHISPER_RETRY_WAITS", RETRY_WAITS) or ())
                while True:  # a failure of the moment is tried again by itself (TransientError, Ralf 2026-10-30)
                    try:
                        segments = _waiting(app, rid, mine, request_part)
                        break
                    except TransientError as error:
                        if not waits or not _still_mine(rid, mine):
                            raise
                        app.logger.warning("transcription of interview %s, part %s: %s; tried again in %s s",
                                           rid, key, error, waits[0])
                        _pause(rid, mine, waits.pop(0), key)
                        remote = _jobs(rid)  # a job the service still holds is asked again, not sent anew
                if found.get("speaker") in VOICES:  # one voice per file (Zoom's file per person): its label, kept by time
                    timed = [(offset + s, offset + e, f"{found['speaker']}: " + re.sub(r"^S\d+:\s*", "", t.strip()))
                             for s, e, t in segments if t and t.strip()]
                    entry = {"bytes": found.get("bytes"), "text": draft(timed, voices=True), "segments": timed}
                else:
                    # Speaker labels are local to one recording part; never imply an identity across separate files.
                    timed = [(offset + s, offset + e, re.sub(r"^S(\d+):", rf"P{key}-S\1:", t)) for s, e, t in segments]
                    entry = {"bytes": found.get("bytes"), "text": draft(timed)}
                stored = _store(rid, lambda tr, key=key, entry=entry: {**tr, "parts": {**(tr.get("parts") or {}), key: entry},
                                                                       "beat": _now()} if mine(tr) else None)
                if stored is None:
                    return  # cancelled or started anew

            def finish(tr):
                if not mine(tr):
                    return None
                text, covers = _assemble(tr, plan)
                out = {k: v for k, v in tr.items() if k not in ("parts", "draft", "draft_covers", "run", "progress",
                                                                 "partial", "resumed")}
                out.update(state="done", finished=_now(), error="", error_code="", model=requested_model if app.config.get("WHISPER_JOBS") else app.config.get("WHISPER_MODEL") or "whisper-1",
                           seconds=int(time.monotonic() - began))
                if tr.get("edited") and tr.get("text"):  # the checked or corrected text stays; the draft beside it
                    if text != tr["text"]:  # shown on the transcript page to take over or discard (audit 2026-10-10, E-1)
                        out["draft"], out["draft_covers"] = text, covers
                else:
                    out.update(text=text, covers=covers)
                return out

            _store(rid, finish)
        except _Cancelled:
            return
        except Exception as e:  # noqa: BLE001 - the list shows the reason, the log the details
            db.session.rollback()
            if not _still_mine(rid, mine):
                # cancelled ("Abbrechen") or started anew meanwhile: Whisper then reports the job as cancelled, which is
                # no failure; the interview already shows its state (log check after the deploy 2026-10-29)
                app.logger.info("transcription of interview %s ended: cancelled or started anew", rid)
                return
            app.logger.exception("transcription of interview %s failed", rid)
            reason = str(e) if isinstance(e, RuntimeError) else "Die Transkription ist fehlgeschlagen."
            from app.whisper_jobs import MemoryLimitError
            error_code = 'memory_limit' if isinstance(e, MemoryLimitError) else ''
            try:
                _store(rid, lambda tr: {**tr, **_partial(tr, plan), "state": "error", "error": reason, "error_code": error_code,
                                        "run": ""} if mine(tr) else None)
            except Exception:  # noqa: BLE001 - the database itself is gone; after STALE_BEAT it may start again
                app.logger.exception("state of the transcription of interview %s not stored", rid)
        finally:
            db.session.remove()


def busy(rid):
    """Whether a request to Whisper of an earlier run still works for this interview: a cancel does not stop it (it
    runs out by itself, up to WHISPER_TIMEOUT). A new start waits for it, so the Whisper server does not get the
    same part twice at once (I11). Only this process's requests: with several workers the next start may land in
    another one, as before."""
    with _FLIGHT_LOCK:
        return _IN_FLIGHT.get(rid, 0) > 0


def start(survey, row, background=True, model=None, resumed=0):
    from app import interview_audio

    run = secrets.token_hex(8)
    previous = state(row)
    requested = model or _default_model(current_app.config)
    changed = current_app.config.get("WHISPER_JOBS") and requested != (previous.get("requested_model") or previous.get("model") or _default_model(current_app.config))
    def reset():
        values = {"state": "running", "started": _now(), "beat": _now(), "error": "", "error_code": "", "run": run,
                  "progress": {}, "requested_model": requested, "resumed": resumed}
        if changed:
            values.update(parts={}, jobs={})
        _set(row, **values)
    interview_audio.retried(reset)
    if changed:
        from app.whisper_jobs import cancel_records
        cancel_records(previous.get("jobs"))
    app = current_app._get_current_object()
    if background:
        threading.Thread(target=work, args=(app, survey.id, row.id, run), daemon=True, name=f"whisper-{row.id}").start()
    else:
        work(app, survey.id, row.id, run)


RESUME_TRIES = 3  # a transcription broken off this often is not taken up again by itself


def resume_stale():
    """Housekeeping (app/housekeeping.py): a transcription marked "running" without a sign of life for STALE_BEAT
    seconds (the web process that held it ended, e.g. a restart) is taken up again here, in the housekeeping
    process, from the parts already done; with WHISPER_JOBS in the researchers' container instead (resume relay; only
    it reaches the service), and the queued job of the Whisper service is picked up by its ID instead of sent again. After RESUME_TRIES it is marked as failed, with the parts done so far readable
    (pre-test 2026-10-16: an interview stood on "läuft" for hours, its link hidden). Returns how many it took up."""
    from app.models import Survey

    if not current_app.config.get("WHISPER_URL"):
        return 0
    if current_app.config.get("WHISPER_JOBS"):
        from app import whisper_jobs

        if not whisper_jobs.here():  # housekeeping: the researchers' container takes them up (deploy 2026-10-18)
            return whisper_jobs.resume_relay()
    rows = db.session.scalars(db.select(Response).join(Survey, Survey.id == Response.survey_id)
                              .where(json_given(Survey.interview), Response.interview.isnot(None))).all()
    taken = 0
    for row in rows:
        st = state(row)
        if st.get("state") != "running" or running(row) or busy(row.id):
            continue
        survey = db.session.get(Survey, row.survey_id)
        tries = int(st.get("resumed") or 0)
        if tries >= RESUME_TRIES:
            plan = _plan(survey, row)
            _set(row, **_partial(st, plan), state="error", run="", progress={}, error_code="interrupted",
                 error="Die Transkription wurde mehrmals unterbrochen.")
            db.session.commit()
            continue
        current_app.logger.info("transcription of interview %s resumed (%s)", row.id, tries + 1)
        start(survey, row, background=not current_app.config.get("TESTING"),
              model=st.get("requested_model") if current_app.config.get("WHISPER_JOBS") else None, resumed=tries + 1)
        taken += 1
    return taken


# --- a transcript made elsewhere (f4, MAXQDA, a transcription service) ----------------------------------------------

UPLOAD_MAX = 5 * 1024 * 1024
# a .docx is a ZIP: a few megabytes of it can unpack to gigabytes. Only word/document.xml is read, as a stream
# (_docx_text); the sizes of the other parts are checked in the ZIP's headers only.
DOCX_MAX_UNPACKED = 50 * 1024 * 1024  # all parts together, unpacked (images in the file count)
DOCX_MAX_DOCUMENT = 10 * 1024 * 1024  # word/document.xml unpacked; a transcript of two hours has 1 to 3 MB
DOCX_MAX_ELEMENTS = 500_000           # XML elements read; a transcript of two hours with heavy formatting has ~100 000
DOCX_MAX_RATIO = 100                  # unpacked to packed size of a larger part; text and XML pack some 5 to 20 times
DOCX_MAX_PARTS = 1000
UPLOAD_TYPES = {"txt": "Text", "docx": "Word", "srt": "Untertitel (SRT)", "vtt": "Untertitel (WebVTT)"}
CUE_TIME = r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
SPEAKER = r"[^\W\d_][\w.]{0,11}:(?:\s|$)"           # "I:", "B1:", "Interviewer:" at the start of a turn
TABLE_TIME = r"#?(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:[.,-](\d{1,3}))?#?"
TABLE_HEADS = {"sprecher", "sprecherin", "person", "zeit", "zeitmarke", "zeitstempel", "text", "transkript", "aussage",
               "speaker", "time", "timestamp", "#"}


def _seconds(h, m, s, ms):
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int((ms or "0").ljust(3, "0")) / 1000


def _cues(text):
    """Subtitles (SRT or WebVTT) as segments [(start, end, text)]; a WebVTT voice <v I> becomes the speaker mark "I:"."""
    import re

    out = []
    text = text.replace(chr(13) + chr(10), chr(10)).replace(chr(13), chr(10))
    for block in re.split(r'\n\s*\n', text):
        lines = [l.strip() for l in block.split(chr(10)) if l.strip()]
        for n, line in enumerate(lines):
            m = re.match(CUE_TIME + r"\s*-->\s*" + CUE_TIME, line)
            if m:
                g = m.groups()
                said = " ".join(lines[n + 1:])
                voice = re.match(r"<v(?:\.[\w.-]+)?\s+([^>]{1,40})>", said)
                words = re.sub(r"<[^>]+>", "", said).strip()  # WebVTT voice and style tags
                if voice and not re.match(SPEAKER, words):
                    words = f"{' '.join(voice.group(1).split())}: {words}"
                out.append((_seconds(*g[:4]), _seconds(*g[4:]), words))
                break
    return out


def _turns(segments):
    """Paragraphs of subtitles: like Whisper's segments, and a new one at every change of speaker (a speaker mark at
    the start of a cue), so each turn has its own paragraph and time mark (Dresing & Pehl; data audit I-8)."""
    import re

    from app.interview_results import stamp

    out, group = [], []
    for seg in segments:
        if group and re.match(SPEAKER, " ".join(str(seg[2] or "").split())):
            out.append(group)
            group = []
        group.append(seg)
    if group:
        out.append(group)
    return "\n\n".join(f"{text} {stamp(end)}" for g in out for end, text in paragraphs(g))


def _decode(data):
    """The text of a plain file: a byte order mark (UTF-8, UTF-16, UTF-32) decides, else UTF-8, else UTF-16 without a
    mark (every second byte zero), else Windows-1252, but only without NUL bytes (data audit I-9)."""
    import codecs

    for mark, encoding in ((codecs.BOM_UTF32_LE, "utf-32"), (codecs.BOM_UTF32_BE, "utf-32"), (codecs.BOM_UTF8, "utf-8-sig"),
                           (codecs.BOM_UTF16_LE, "utf-16"), (codecs.BOM_UTF16_BE, "utf-16")):
        if data.startswith(mark):
            try:
                return data.decode(encoding)
            except UnicodeDecodeError:
                break
    else:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            pass
        if b"\x00" in data:
            even, odd = data[0::2], data[1::2]
            for zeros, encoding in ((odd, "utf-16-le"), (even, "utf-16-be")):
                if zeros.count(0) > len(zeros) * 0.3:
                    try:
                        return data.decode(encoding)
                    except UnicodeDecodeError:
                        break
        else:
            try:
                return data.decode("cp1252")
            except UnicodeDecodeError:
                pass
    raise ValueError(de_en("Die Textdatei ließ sich nicht lesen (Zeichensatz). Bitte als UTF-8 speichern.",
                           "The text file could not be read (character set). Please save it as UTF-8."))


def _table_line(cells):
    """A table row (speaker | time | text, in any order) as the paragraph "Sprecher: Text #hh:mm:ss-0#"; None for an
    empty row or the head of the table (data audit I-9)."""
    import re

    from app.interview_results import stamp

    if not cells or all(c.lower().rstrip(":") in TABLE_HEADS for c in cells):
        return None
    when = next((c for c in cells if re.fullmatch(TABLE_TIME, c)), None)
    rest = [c for c in cells if c is not when]
    who = rest[0].rstrip(":") if len(rest) > 1 and len(rest[0]) <= 20 and " " not in rest[0].strip(": ") else ""
    said = " ".join(rest[1:] if who else rest)
    line = f"{who}: {said}" if who else said
    if when:
        line += " " + stamp(_seconds(*re.fullmatch(TABLE_TIME, when).groups()))
    return line


class _Bounded:
    """A file that refuses to give more than `limit` bytes (word/document.xml as it is unpacked)."""

    def __init__(self, f, limit, error):
        self.f, self.left, self.error = f, limit, error

    def read(self, n=-1):
        chunk = self.f.read(64 * 1024 if n is None or n < 0 else min(n, 64 * 1024))
        self.left -= len(chunk)
        if self.left < 0:
            raise ValueError(self.error)
        return chunk


_RUN_TEXT = {"tab": "\t", "ptab": "\t", "cr": "\n", "noBreakHyphen": "-"}
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_text(f):
    """Paragraphs and tables of word/document.xml (an open file) in their order, read as a stream: what python-docx
    gave before, without building the tree (a file of 2 MB of empty paragraphs or tiny cells took 1 GB of memory and
    90 s; audit 2026-10-09, D2). Each element is let go once read. Stops at MAX_TEXT characters (more is not kept);
    ValueError past DOCX_MAX_ELEMENTS elements or for XML that is not well formed.

    As python-docx: the text of a paragraph is that of its runs and of the runs of its links (w:t; w:tab, w:ptab
    as a tab; w:br, w:cr as a line break; w:noBreakHyphen as "-"); a table cell is its paragraphs, one per line; a
    cell spanning columns comes once per column, a cell merged with the one above has that one's text."""
    from lxml import etree

    many = de_en("Die Word-Datei hat zu viele Elemente (Absätze, Zellen, Formatierungen).",
                 "The Word file has too many elements (paragraphs, cells, formatting).")
    out, size, count = [], 0, 0
    path = []        # local names from the root to the element read
    paragraphs = []  # the text pieces of each open paragraph (a text box holds paragraphs of its own)
    cell = None      # the open cell of a table at the top: {"text": [...], "span": 1, "merge": None}
    row, above, grid = [], {}, 0  # the cells of the open row; texts by grid column in the row above; column reached
    for event, el in etree.iterparse(f, events=("start", "end"), resolve_entities=False, huge_tree=False,
                                     no_network=True, load_dtd=False, remove_comments=True, remove_pis=True):
        tag = el.tag.rsplit("}", 1)[-1] if isinstance(el.tag, str) else ""
        if event == "start":
            path.append(tag)
            depth = len(path)
            if tag == "p":
                paragraphs.append([])
            elif tag == "tc" and depth == 5 and path[2:4] == ["tbl", "tr"]:
                cell = {"text": [], "span": 1, "merge": None}
            elif tag == "tr" and depth == 4 and path[2] == "tbl":
                row, grid = [], 0
            elif tag == "tbl" and depth == 3:
                above = {}
            continue
        count += 1
        if count > DOCX_MAX_ELEMENTS:
            raise ValueError(many)
        depth, parent = len(path), path[-2] if len(path) > 1 else ""
        in_run = parent == "r" and depth > 3 and (path[-3] == "p" or (path[-3] == "hyperlink" and path[-4] == "p"))
        if in_run and paragraphs:
            if tag == "t":
                paragraphs[-1].append(el.text or "")
            elif tag in _RUN_TEXT:
                paragraphs[-1].append(_RUN_TEXT[tag])
            elif tag == "br" and el.get(_W + "type", "textWrapping") == "textWrapping":
                paragraphs[-1].append("\n")
        elif tag == "p":
            text = "".join(paragraphs.pop())
            if depth == 3:
                text = text.strip()
                if text:
                    out.append(text)
                    size += len(text) + 2
            elif depth == 6 and cell is not None and path[2:5] == ["tbl", "tr", "tc"]:
                cell["text"].append(text)
        elif cell is not None and depth == 7 and parent == "tcPr" and tag in ("gridSpan", "vMerge"):
            if tag == "gridSpan":
                try:
                    cell["span"] = max(1, min(int(el.get(_W + "val", "1")), 64))
                except ValueError:
                    pass
            else:
                cell["merge"] = el.get(_W + "val", "continue")
        elif tag == "gridBefore" and depth == 6 and parent == "trPr" and path[2] == "tbl":
            try:
                grid += max(0, min(int(el.get(_W + "val", "0")), 64))
            except ValueError:
                pass
        elif tag == "tc" and depth == 5 and cell is not None:
            text = above.get(grid, "") if cell["merge"] == "continue" else "\n".join(cell["text"])
            for _ in range(cell["span"]):
                row.append((grid, text))
                grid += 1
            cell = None
        elif tag == "tr" and depth == 4 and path[2] == "tbl":
            above = dict(row)
            cells = []
            for _, text in row:  # a merged cell comes once per column it spans
                text = " ".join(text.split())
                if text and (not cells or cells[-1] != text):
                    cells.append(text)
            line = _table_line(cells)
            if line:
                out.append(line)
                size += len(line) + 2
        path.pop()
        el.clear(keep_tail=False)
        holder = el.getparent()
        if holder is not None:
            while el.getprevious() is not None:
                del holder[0]  # the elements read before: kept as empty shells otherwise
        if size >= MAX_TEXT:
            break  # the rest would not be kept
    return "\n\n".join(out)


def _docx_checked(data):
    """word/document.xml of a .docx (bytes) as a file to read, after the sizes in the ZIP's headers have been checked
    (ValueError with the reason). Nothing else of the file is unpacked, and the XML at most DOCX_MAX_DOCUMENT bytes."""
    import io
    import zipfile

    too_large = de_en("Die Word-Datei ist entpackt zu groß (Text höchstens {mb} MB).",
                      "The Word file is too large when unpacked (text at most {mb} MB).", mb=DOCX_MAX_DOCUMENT // (1024 * 1024))
    unreadable = de_en("Die Word-Datei ließ sich nicht lesen.", "The Word file could not be read.")
    from app import zip_limits

    if zip_limits.directory(data) is None:  # no end record that fits: not a ZIP zipfile could read (T03-N2)
        raise ValueError(unreadable)
    if zip_limits.too_many(data, DOCX_MAX_PARTS):  # counted before zipfile reads the directory (audit 2026-10-13, DA-4)
        raise ValueError(de_en("Die Word-Datei enthält zu viele Teile.", "The Word file contains too many parts."))
    z = zipfile.ZipFile(io.BytesIO(data))
    infos = z.infolist()
    if len(infos) > DOCX_MAX_PARTS:
        raise ValueError(de_en("Die Word-Datei enthält zu viele Teile.", "The Word file contains too many parts."))
    if sum(i.file_size for i in infos) > DOCX_MAX_UNPACKED:
        raise ValueError(too_large)
    if any(i.file_size > 1024 * 1024 and i.file_size > DOCX_MAX_RATIO * max(i.compress_size, 1) for i in infos):
        raise ValueError(too_large)
    main = next((i for i in infos if i.filename == "word/document.xml"), None)
    if main is None:
        raise ValueError(unreadable)
    if main.file_size > DOCX_MAX_DOCUMENT:
        raise ValueError(too_large)
    return _Bounded(z.open(main), DOCX_MAX_DOCUMENT, too_large)


def _docx_read(data):
    """The text of a .docx (bytes); ValueError with the reason."""
    import zipfile
    import zlib

    from lxml import etree

    try:
        return _docx_text(_docx_checked(data))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, NotImplementedError, OSError, EOFError, zlib.error,
            RuntimeError, etree.XMLSyntaxError) as e:
        raise ValueError(de_en("Die Word-Datei ließ sich nicht lesen.", "The Word file could not be read.")) from e


def read_upload(name, data):
    """The text of an uploaded transcript (raises ValueError with a readable reason), without control characters
    like the values of forms (app/request_values.py; audit 2026-10-13, DA-8)."""
    from app.request_values import clean_text

    ext = (name or "").rsplit(".", 1)[-1].lower()
    if ext not in UPLOAD_TYPES:
        raise ValueError(de_en("Bitte eine Datei vom Typ .txt, .docx, .srt oder .vtt hochladen.",
                               "Please upload a file of type .txt, .docx, .srt or .vtt."))
    if not data or len(data) > UPLOAD_MAX:
        raise ValueError(de_en("Die Datei ist leer oder größer als 5 MB.", "The file is empty or larger than 5 MB."))
    if ext == "docx":
        return clean_text(_docx_read(data))
    text = _decode(data)
    if ext in ("srt", "vtt"):
        cues = _cues(text)
        if not cues:
            raise ValueError(de_en("In der Untertiteldatei wurden keine Einträge mit Zeiten gefunden.",
                                   "No entries with times were found in the subtitle file."))
        return clean_text(_turns(cues))
    return clean_text(text.replace("\r\n", "\n").strip())


# --- the routes ----------------------------------------------------------------------------------------------------


def _row(code, rid):
    """(survey, interview) for a researcher with "Bearbeiten", or an interviewer's own interview whose access may
    transcribe (app/interviewer.py)."""
    from app import interviewer

    return interviewer.row_for(code, rid, right="transcribe")


def error_info(tr):
    if tr.get('error_code') == 'memory_limit':
        from app.whisper_jobs import memory_limit_message
        return memory_limit_message()
    if tr.get('error_code') == 'interrupted':
        return de_en("Die Transkription wurde mehrmals unterbrochen (z. B. durch Neustarts des Servers). Bitte erneut starten.",
                     "The transcription was interrupted several times (e.g. by restarts of the server). Please start it again.")
    return tr.get('error') or ''


def estimate_info(value, active):
    if not active:
        return ''
    if value.get('eta_overdue') is True:
        return de_en('Verarbeitung oder Wartezeit dauern länger als geschätzt. Der Auftrag bleibt aktiv.',
                     'Processing or waiting is taking longer than estimated. The job remains active.')
    until = value.get('eta_until')
    if type(until) not in (int, float) or not time.time() - 7 * 86400 <= until <= time.time() + 7 * 86400:
        return de_en('Restzeit noch nicht abschätzbar; Audiodauer oder Wartezeit unbekannt.',
                     'Remaining time cannot yet be estimated; audio duration or waiting time is unknown.')
    remaining = until - time.time()
    if remaining < 0:
        return de_en('Verarbeitung oder Wartezeit dauern länger als geschätzt. Der Auftrag bleibt aktiv.',
                     'Processing or waiting is taking longer than estimated. The job remains active.')
    import math
    low, high = max(1, math.ceil(remaining * .6 / 60)), max(1, math.ceil(remaining * 1.8 / 60))
    lower, upper = value.get('eta_low_until'), value.get('eta_high_until')
    if type(lower) in (int, float) and type(upper) in (int, float) and time.time() - 7 * 86400 <= lower <= upper <= time.time() + 7 * 86400:
        low, high = max(1, math.ceil((lower - time.time()) / 60)), max(1, math.ceil((upper - time.time()) / 60))
    return de_en('Geschätzte Restzeit für diesen Aufnahmeteil einschließlich Warteschlange: etwa {lo}–{hi} Minuten.',
                 'Estimated time remaining for this recording part including the queue: about {lo}–{hi} minutes.', lo=low, hi=high) if value.get('eta_sampled') is True else de_en(
                 'Grobe Restzeitschätzung für diesen Aufnahmeteil einschließlich Warteschlange: etwa {lo}–{hi} Minuten. Noch wenige Vergleichswerte.',
                 'Rough time estimate for this recording part including the queue: about {lo}–{hi} minutes. Few comparable runs yet.', lo=low, hi=high)


def progress_info(row):
    """Only editor-visible operational progress of this interview; no remote identifiers."""
    from app import interview_audio
    tr = state(row)
    parts = interview_audio.parts(row)
    covers = (tr.get("draft_covers") if tr.get("draft") else tr.get("covers")) if tr.get("state") == "done" else None
    done = sum(((tr.get("parts", {}).get(str(part["part"])) or {}).get("bytes") == part.get("bytes")) or
               ((covers or {}).get(str(part["part"])) == part.get("bytes")) for part in parts)
    active = running(row)
    value = tr.get("progress") or {}
    if not isinstance(value, dict):
        value = {}
    labels = {"waiting": ("Wartet auf einen freien Platz in der Warteschlange", "Waiting for a free place in the queue"),
              "loading": ("Modell wird geladen", "Loading model"), "decoding": ("Audio wird geprüft", "Checking audio"),
              "transcribing": ("Audio wird transkribiert", "Transcribing audio"),
              "aligning": ("Wortzeitmarken werden erstellt", "Aligning words"),
              "diarizing": ("Sprecher werden zugeordnet", "Assigning speakers")}
    stage = value.get("stage")
    if not isinstance(stage, str):
        stage = None
    position = value.get("position", 0)
    if active and stage == "queued" and type(position) is int and 1 <= position <= 8:
        text = de_en("Warteschlange: Platz {n}", "Queue: position {n}", n=position)
    elif active:
        text = de_en(*labels.get(stage, ("Transkription läuft", "Transcription running")))
    elif tr.get('state') == 'error' and tr.get('error_code') == 'memory_limit':
        text = de_en('Transkription fehlgeschlagen: {error}', 'Transcription failed: {error}', error=error_info(tr))
    else:
        text = de_en("Transkription abgeschlossen", "Transcription complete") if tr.get("state") == "done" else de_en("Transkription beendet oder unterbrochen", "Transcription ended or interrupted")
    return {"active": active, "text": text, "eta_text": estimate_info(value, active), "done": done, "total": len(parts),
            "parts_text": de_en("{d} von 1 Teil fertig", "{d} of 1 part done", d=done) if len(parts) == 1 else
                          de_en("{d} von {n} Teilen fertig", "{d} of {n} parts done", d=done, n=len(parts))}


@bp.get("/<code>/interviews/<int:rid>/transcribe/progress")
@interviewer_or_signed_in
def progress_status(code, rid):
    survey, row = _row(code, rid)
    return jsonify(progress_info(row)), 200, {"Cache-Control": "no-store"}


@bp.route("/<code>/interviews/<int:rid>/transcribe", methods=["POST"])
@interviewer_or_signed_in
def begin(code, rid):
    from app import ai_access, interview_audio

    survey, row = _row(code, rid)
    back = redirect(interviewer.list_url(survey))
    if not configured():
        abort(404)
    who = interviewer.actor(survey)  # an interviewer: the switch of the researcher who made the access
    if not (interviewer.whisper_on(who) if who else ai_access.ai_on()):  # "Generative KI nutzen" off: the recording
        flash(de_en(*ai_access.OFF), "error")                              # goes to no AI service (K7/R4, decision 3a)
        return back
    if who and any(running(r) for r in interviewer.own_interviews(survey, who) if r.id != row.id):
        flash(de_en("Es läuft schon eine Transkription. Bitte warten, bis sie fertig ist.",
                    "A transcription is already running. Please wait until it is done."), "warning")
        return back
    if not interview_audio.parts(row):
        flash(de_en("Für dieses Interview gibt es keine Aufnahme.", "There is no recording for this interview."), "error")
        return back
    if running(row):
        flash(de_en("Die Transkription läuft bereits.", "The transcription is already running."), "warning")
        return back
    if busy(row.id):
        flash(de_en("Die abgebrochene Anfrage an Whisper läuft noch. Bitte später erneut starten, sonst würde derselbe Teil doppelt transkribiert.",
                    "The cancelled request to Whisper is still running. Please start again later; otherwise the same part would be transcribed twice."), "warning")
        return back
    model = None
    if current_app.config.get("WHISPER_JOBS"):
        model = request.form.get("model") or state(row).get("requested_model") or _default_model(current_app.config)
        from app import whisper_jobs

        if model not in whisper_jobs.MODELS:
            flash(de_en("Bitte ein verfügbares Transkriptionsmodell auswählen.", "Please select an available transcription model."), "error")
            return back
    start(survey, row, background=not current_app.config.get("TESTING"), model=model)
    flash(de_en("Transkription von {label} gestartet; das dauert je nach Länge und Server einige Minuten bis mehrere Stunden.",
                "Transcription of {label} started; depending on the length and server this takes a few minutes to several hours.",
                label=(row.interview or {}).get("label", "")), "success")
    return back


@bp.route("/<code>/interviews/<int:rid>/transcribe/cancel", methods=["POST"])
@interviewer_or_signed_in
def cancel(code, rid):
    """Stop waiting for a transcription: the thread drops its result; parts already done stay for the next run."""
    from app import interview_audio

    survey, row = _row(code, rid)
    if state(row).get("state") == "running":
        plan = _plan(survey, row)
        interview_audio.retried(lambda: _set(row, **_partial(state(row), plan), state="error", error="Abgebrochen.", run="",
                                             progress={}))
        from app.whisper_jobs import cancel_records
        cancel_records(state(row).get("jobs"))
        flash(de_en("Transkription abgebrochen. Schon fertige Teile bleiben für den nächsten Versuch.",
                    "Transcription cancelled. Parts already done are kept for the next attempt."), "success")
    return redirect(interviewer.list_url(survey))


@bp.route("/<code>/interviews/<int:rid>/transcript", methods=["GET", "POST"])
@interviewer_or_signed_in
@autosave.aware
def edit(code, rid):
    """The transcript to check and correct (speakers I: and B:, wording); "Transkript geprüft" marks it checked and
    notes which parts of the recording it holds (data audit I-3: only those go by the period)."""
    from app import interview, interview_audio, transcript_export
    from app.interview_results import DRESING_PEHL

    survey, row = _row(code, rid)
    if request.method == "POST":
        text = request.form.get("text")
        text = None if text is None else text[:MAX_TEXT].replace("\r\n", "\n")  # None: "übergeben" keeps the text

        def work():
            tr = {**state(row), "edited": True, **({} if text is None else {"text": text})}
            if "checked_shown" in request.form:
                if not request.form.get("checked"):
                    tr["checked"] = None
                    tr.pop("checked_parts", None)
                elif not tr.get("checked"):
                    tr["checked"], tr["checked_parts"] = _now(), interview_audio.covered(row)
                    tr["checked_by"] = _checker(survey)  # "geprüft von" in the exported head (app/transcript_export.py)
            if text is not None and tr.get("draft") == text:
                tr.pop("draft")  # kept only while it differs from the text (data audit L-14)
                tr.pop("draft_covers", None)
            head = (row.interview or {}).get("transcript_head")
            if "head_shown" in request.form:  # the fields "Transkriptkopf": only the changed values are kept
                head = transcript_export.kept_head(request.form)
            iv = {k: v for k, v in (row.interview or {}).items() if k != "transcript_head"}
            row.interview = {**iv, "transcript": tr, **({"transcript_head": head} if head else {})}

        interview_audio.retried(work)
        if interviewer.actor(survey) is not None and not interviewer.visible(row):
            # an interviewer's "geprüft" hands the interview over (decision E): it no longer opens for them
            flash(de_en("Transkript von {label} an die Forschenden übergeben.", "Transcript of {label} handed over to the researchers.",
                        label=(row.interview or {}).get("label", "")), "success")
            return redirect(interviewer.list_url(survey))
        flash(de_en("Gespeichert.", "Saved."), "success")
        return redirect(url_for("interview_transcribe.edit", code=survey.code, rid=row.id))
    from app import interview_tasks

    return render_template("interview/transcript.html", survey=survey, row=row, st=state(row),
                           has_tasks=bool(interview_tasks.tasks(survey)),
                           head=transcript_export.head(survey, row, transcript_export.lang_of(survey)),
                           keep=interview.state(survey)["audio_keep"], grace=interview_audio.GRACE_DAYS, day=interview.day_text,
                           sources=[(de_en("Transkriptionsregeln", "Transcription rules"), [DRESING_PEHL])])


@bp.route("/<code>/interviews/<int:rid>/transcript/draft", methods=["POST"])
@interviewer_or_signed_in
def take_draft(code, rid):
    """Whisper's new draft beside a corrected transcript ("Neu transkribieren" after a correction): "übernehmen"
    makes it the transcript (the corrections and the tick go, it holds the recording as it is now), "verwerfen"
    deletes it (audit 2026-10-10, E-1)."""
    from app import interview_audio

    survey, row = _row(code, rid)
    take = request.form.get("action") == "take"

    def work():
        tr = dict(state(row))
        text, covers = tr.pop("draft", None), tr.pop("draft_covers", None)
        if take and text:
            tr.update(text=text, checked=None, edited=False)
            for key in ("checked_parts", "source", "filename"):
                tr.pop(key, None)
            if isinstance(covers, dict):
                tr["covers"] = covers
        row.interview = {**(row.interview or {}), "transcript": tr}

    if state(row).get("draft") is not None:
        interview_audio.retried(work)
        flash(de_en("Entwurf übernommen. Bitte das Transkript erneut prüfen.", "Draft accepted. Please check the "
                    "transcript again.") if take else de_en("Entwurf verworfen.", "Draft discarded."), "success")
    return redirect(url_for("interview_transcribe.edit", code=survey.code, rid=row.id))


@bp.route("/<code>/interviews/<int:rid>/transcript/upload", methods=["POST"])
@interviewer_or_signed_in
def upload(code, rid):
    """A transcript made elsewhere replaces the text; it is checked and exported like Whisper's draft. It counts as
    the transcript of the recording as it is now."""
    from app import interview_audio

    survey, row = _row(code, rid)
    back = redirect(url_for("interview_transcribe.edit", code=survey.code, rid=row.id))
    f = request.files.get("file")
    try:
        text = read_upload(f.filename if f else "", f.read(UPLOAD_MAX + 1) if f else b"")
    except ValueError as e:
        flash(str(e), "error")
        return back
    if not text.strip():
        flash(de_en("Die Datei enthält keinen Text.", "The file contains no text."), "error")
        return back

    def work():
        tr = {k: v for k, v in state(row).items() if k not in ("parts", "draft", "draft_covers", "checked_parts", "run")}
        tr.update(state="done", text=text[:MAX_TEXT], edited=True, checked=None, source="upload", error="",
                  filename=os.path.basename(f.filename)[:120], finished=_now(), model="",
                  covers={str(p.get("part")): p.get("bytes") for p in interview_audio.parts(row)})
        row.interview = {**(row.interview or {}), "transcript": tr}

    interview_audio.retried(work)
    flash(de_en("Transkript übernommen. Bitte prüfen und dann „Transkript geprüft“ ankreuzen.",
                "Transcript accepted. Please check it and then tick “Transcript checked”."), "success")
    return back


@bp.route("/<code>/interviews/<int:rid>/transcript.txt")
@interviewer_or_signed_in
def download(code, rid):
    import io

    survey, row = _row(code, rid)
    text = state(row).get("text")
    if not text:
        abort(404)
    label = (row.interview or {}).get("label", str(row.id))
    return send_file(io.BytesIO(text.replace("\n", "\r\n").encode("utf-8")), as_attachment=True, mimetype="text/plain",
                     download_name=f"{survey.code}-{label}-transkript.txt")


def _checker(survey):
    """The name of who ticks "Transkript geprüft": the interviewer of a personal link, else the signed-in person."""
    from flask_login import current_user

    actor = interviewer.actor(survey)
    if actor is not None:
        return actor.name or ""
    return (getattr(current_user, "name", "") or "") if current_user.is_authenticated else ""


@bp.route("/<code>/interviews/<int:rid>/transcript/export")
@interviewer_or_signed_in
def export(code, rid):
    """The transcript as Word or PDF with head and numbered lines (app/transcript_export.py); ?marks=1 keeps the
    time marks."""
    import io

    from app import transcript_export

    survey, row = _row(code, rid)
    if interviewer.actor(survey) is not None:  # exports are the researchers' (interviewers: no exports)
        abort(404)
    fmt = request.args.get("format")
    if fmt not in transcript_export.FORMATS or not transcript_export.has_text(row):
        abort(404)
    build, mimetype = transcript_export.FORMATS[fmt]
    # the results of the tasks: in unless switched off (the form sends tasks=0 and, ticked, tasks=1)
    tasks = "1" in request.args.getlist("tasks") or "tasks" not in request.args
    return send_file(io.BytesIO(build(survey, row, marks=request.args.get("marks") == "1", tasks=tasks)), as_attachment=True,
                     mimetype=mimetype, download_name=transcript_export.file_name(survey, row, fmt))
