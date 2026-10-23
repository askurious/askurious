"""Voice message: participants answer a question by speaking (experimental until 2026-10-16, now for everyone).

Tap to start, tap to stop (no hold-to-talk: a long press opens menus on phones and is hard with shaky hands); a
timer, a maximum length, playback, record again or delete; optionally a field to type instead. A browser without a
microphone, or a person who refuses access, sees only the text field.

The recording travels with the page itself: participant.js puts it into a file field of the form when the
recording stops, and the page goes as multipart. So nothing is uploaded for a page that is never sent, the preview
works like the real survey, and no route of its own needs to know the participation. It is written only when
the page passes its checks and the person goes on (commit/discard, runtime.step), within a bound per recording
(limit: the item's maximum length), per survey (VOICE_MAX_MB) and for the free disk (closed); while the survey cannot
take more, the page offers the text field instead of the recorder.

Files lie outside everything Askurious serves: instance/voice/<survey uid>/<pid>/<item id>-<random id>.<ext> (WebM/Opus,
Ogg/Opus or MP4/AAC, as the browser records). The pid is known before the participation is stored (the first
page), and it names the folder the export and the deletion find. They go with the participation: deleting a single
one (erasure, a declined consent, a test run) removes its folder once committed; the period "answers" removes the
survey's folder (app/retention.py); `run` sweeps folders whose participation is gone or never was stored.

  answer = {"audio": {"file": "12.webm", "mime": "audio/webm", "bytes": int, "seconds": int}, "text": "..."}
  (either part may be missing; None when nothing was given)"""
import os
import re
import secrets
import shutil
import time

from flask import current_app, g, has_request_context, request
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.items import register
from app.items.base import FactSheet  # what the type can do and is for (app/element_types.py)
from app.items.base import AnswerError, ItemType, in_lang, r_flag, r_int, whole
from app.lang import Both
from app.ui_i18n import pick, tr

MIMES = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/mp4": "m4a"}
MAX_BYTES = 8 * 1024 * 1024    # one recording; a page takes the bounds of all recordings of its survey (records)
# what one recording may take (limit): per second of the item's maximum length, where Opus at 48 kbit/s
# (participant.js) needs some 6 kB and AAC in Safari up to 16 kB, plus the container and the last second
BYTES_PER_SECOND, SLACK_BYTES = 16 * 1024, 256 * 1024
MIN_SECONDS, MAX_SECONDS, DEFAULT_SECONDS = 10, 300, 60
MAX_TEXT = 5000
FILE_RE = re.compile(r"\d{1,12}(?:-[a-f0-9]{32})?\.(webm|ogg|m4a)\Z")
PID_RE = re.compile(r"[A-Za-z0-9_-]{1,40}\Z")
STALE_HOURS = 48  # a folder without a stored participation (first page never sent) goes after this
USED_SECONDS = 60  # how long the bytes a survey's recordings take are counted once (closed(), per process)
_DOOMED = "voice_doomed"
_WRITE = "voice_write"  # durable recordings to finalize after their database reference is committed
_CLEAR = "voice_clear"
_PENDING = "voice_pending"
_used = {}  # survey uid -> (time counted, bytes)

MESSAGES = {
    "de": {"required": "Bitte sprechen Sie eine Antwort ein.",
           "required_or_type": "Bitte sprechen Sie eine Antwort ein oder schreiben Sie sie auf.",
           "refused": "Die Aufnahme konnte nicht angenommen werden. Bitte nehmen Sie sie noch einmal auf.",
           "full": "Aufnahmen können gerade nicht gespeichert werden. Bitte schreiben Sie Ihre Antwort auf."},
    "en": {"required": "Please record an answer.",
           "required_or_type": "Please record an answer or type it.",
           "refused": "The recording could not be accepted. Please record it again.",
           "full": "Recordings cannot be stored at the moment. Please type your answer."},
}


# --- the files ---------------------------------------------------------------------------------------------------


def root():
    return os.path.join(os.path.dirname(current_app.config["UPLOAD_FOLDER"]), "voice")


def folder(survey_uid, pid):
    if not PID_RE.match(str(pid or "")):
        return None
    return os.path.join(root(), survey_uid, pid)


def path(survey_uid, pid, answer):
    """The file of a stored answer, or None if there is none (also when the name is not one this module writes)."""
    audio = (answer or {}).get("audio") if isinstance(answer, dict) else None
    where = folder(survey_uid, pid)
    if not where or not isinstance(audio, dict) or not FILE_RE.match(str(audio.get("file") or "")):
        return None
    full = os.path.join(where, audio["file"])
    if os.path.isfile(full):
        return full
    pending = os.path.join(where, ".pending", audio["file"])
    return pending if os.path.isfile(pending) else None


def sniff(head):
    """The container of a recording from its first bytes, or None: what the browser declares is not trusted."""
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "audio/webm"
    if head[:4] == b"OggS":
        return "audio/ogg"
    if head[4:8] == b"ftyp":
        return "audio/mp4"
    return None


def limit(seconds):
    """Bytes one recording of at most `seconds` may have (never more than MAX_BYTES)."""
    return min(MAX_BYTES, (int(seconds) + 5) * BYTES_PER_SECOND + SLACK_BYTES)


def _read(upload, most):
    """The bytes and container of an uploaded recording, or None if it is refused (no audio, too long)."""
    data = upload.read(most + 1)
    mime = sniff(data[:12]) if data else None
    if mime is None or len(data) > most:
        return None
    return data, mime


def used_bytes(survey_uid):
    """Bytes the recordings of a survey take on the disk, counted at most every USED_SECONDS per process."""
    now = time.monotonic()
    seen = _used.get(survey_uid)
    if seen and now - seen[0] < USED_SECONDS:
        return seen[1]
    total = 0
    for top, _, files in os.walk(os.path.join(root(), survey_uid)):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(top, name))
            except OSError:
                pass
    _used[survey_uid] = (now, total)
    return total


def closed(survey_uid, size=0):
    """Why a recording of `size` bytes cannot be stored now, or None: the survey holds VOICE_MAX_MB already, or the
    disk would keep less than it must (as interview_audio._room). Then the page offers the text field only."""
    from app.interview_audio import MIN_FREE

    cap = int(current_app.config["VOICE_MAX_MB"]) * 1024 * 1024
    if used_bytes(survey_uid) + max(size, 1) > cap:  # at least one byte must still fit
        return "survey"
    try:
        free = shutil.disk_usage(os.path.dirname(current_app.config["UPLOAD_FOLDER"])).free
    except OSError:
        return None
    if free - size < int(current_app.config.get("INTERVIEW_AUDIO_MIN_FREE", MIN_FREE)):
        return "disk"
    return None


def _store(survey_uid, pid, item_id, data, mime):
    """Write a recording that was read and accepted; returns the file name."""
    where = folder(survey_uid, pid)
    os.makedirs(where, exist_ok=True)
    name = f"{item_id}.{MIMES[mime]}"
    for ext in MIMES.values():  # a new recording in another format replaces the old one
        if ext != MIMES[mime]:
            try:
                os.remove(os.path.join(where, f"{item_id}.{ext}"))
            except FileNotFoundError:
                pass
    tmp = os.path.join(where, f".{name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, os.path.join(where, name))
    seen = _used.get(survey_uid)
    if seen:
        _used[survey_uid] = (seen[0], seen[1] + len(data))
    return name


# --- written only when the page goes on ---------------------------------------------------------------------------
# parse_answer reads and checks a recording but keeps it in memory; runtime.step writes it when the page is
# accepted and the person goes forward (commit), and otherwise (an error on the page, a nudge, "Back", the language
# switch) drops it and puts the answer back as it was (discard). A page that never passes stores nothing on the disk.


def _stage(response, item, data, mime, previous):
    g.setdefault(_PENDING, []).append({"response": response, "item": item.id, "data": data, "mime": mime,
                                       "previous": previous})


def commit():
    """Durably spool a unique recording before committing its reference; keep earlier files until commit."""
    if not has_request_context():
        return
    from app.extensions import db

    for p in g.pop(_PENDING, []):
        response = p["response"]
        # A unique immutable file is durable BEFORE its database reference is acknowledged. Finalization is
        # optional: downloads can read the pending file, and the regular sweep retries its move after a restart.
        name = f"{p['item']}-{secrets.token_hex(16)}.{MIMES[p['mime']]}"
        where = os.path.join(folder(response.survey.uid, response.pid), ".pending")
        os.makedirs(where, exist_ok=True)
        with open(os.path.join(where, name), "xb") as output:
            output.write(p["data"])
            output.flush()
            os.fsync(output.fileno())
        _used.pop(response.survey.uid, None)
        if os.name != "nt":
            for base in (where, os.path.dirname(where), os.path.dirname(os.path.dirname(where)), root(),
                         os.path.dirname(root())):
                _sync_directory(base)
        key = str(p["item"])
        answer = (response.answers or {}).get(key)
        if isinstance(answer, dict) and answer.get("audio"):
            response.answers = {**response.answers, key: {**answer, "audio": {**answer["audio"], "file": name}}}
        db.session.info.setdefault(_WRITE, []).append(
            (response.survey.uid, response.pid, name))
        previous = p.get("previous") or {}
        if previous.get("file"):
            db.session.info.setdefault(_CLEAR, []).append((response.survey.uid, response.pid, previous["file"]))


def discard(response):
    """Drop the recordings of a page that does not go on: the answers keep their earlier recording (or none) and
    what was typed."""
    if not has_request_context():
        return
    pending = g.pop(_PENDING, [])
    if not pending:
        return
    stored = dict(response.answers or {})
    for p in pending:
        if p["response"] is not response:
            continue
        key = str(p["item"])
        answer = stored.get(key)
        if not isinstance(answer, dict) or not isinstance(answer.get("audio"), dict):
            continue  # not stored (an error on the question, or declined)
        previous = p["previous"]
        if previous and path(response.survey.uid, response.pid, {"audio": previous}):
            answer = {**answer, "audio": previous}
        else:
            answer = {k: v for k, v in answer.items() if k != "audio"}
        if answer:
            stored[key] = answer
        else:
            stored.pop(key, None)
        g.voice_dropped = {*g.get("voice_dropped", ()), p["item"]}  # the page shown again says so
    response.answers = stored


def _remove_file(survey_uid, pid, item_id):
    where = folder(survey_uid, pid)
    for base in (where, os.path.join(where, ".pending")) if where else ():
        if not os.path.isdir(base):
            continue
        for name in os.listdir(base):
            if FILE_RE.fullmatch(name) and (name.startswith(f"{item_id}.") or name.startswith(f"{item_id}-")):
                os.remove(os.path.join(base, name))


def remove_survey(survey_uid):
    """Every recording of a survey (the period "answers", deleting the survey)."""
    shutil.rmtree(os.path.join(root(), survey_uid), ignore_errors=True)


def used(survey):
    return any(i.type == "voice" for b in survey.blocks for i in b.items)


EXTRA_CAP = 22 * 1024 * 1024  # what the recordings of one page may add at most: with the form under the 25 MB of the proxy


def records(uid):
    """Bytes a page of the survey behind a participant link may carry in recordings beyond the form: the bounds of
    all its voice messages together (limit), at most EXTRA_CAP; 0 if it has none. Raising the bound once by
    MAX_BYTES refused a page with two long recordings from Safari, and both were lost (audit 2026-10-12, T-9)."""
    from app import links

    survey = links.resolve(uid) if uid else None
    if survey is None:
        return 0
    total = sum(limit(i.kind.max_seconds(i)) for b in survey.blocks for i in b.items if i.type == "voice")
    return min(total, EXTRA_CAP)


def allow_microphone():
    """This page may ask for the microphone (Permissions-Policy in create_app; every other page may not)."""
    if has_request_context():
        g.microphone = True


# --- deletion with the participation -----------------------------------------------------------------------------


@event.listens_for(Session, "before_flush")
def _note_doomed(session, flush_context, instances):
    """A participation that goes takes its recordings along, once the deletion is committed."""
    from app.models import Response

    for obj in session.deleted:
        if isinstance(obj, Response) and obj.pid and obj.survey is not None:
            session.info.setdefault(_DOOMED, []).append((obj.survey.uid, obj.pid))


@event.listens_for(Session, "after_commit")
def _remove_doomed(session):
    gone = set()
    for uid, pid in session.info.pop(_DOOMED, []):
        where = folder(uid, pid)
        if where:
            shutil.rmtree(where, ignore_errors=True)
            gone.add((uid, pid))
    _write_staged(session, gone)
    for uid, pid, name in session.info.pop(_CLEAR, []):
        if not FILE_RE.fullmatch(name):
            continue
        for base in (folder(uid, pid), os.path.join(folder(uid, pid), ".pending")):
            try:
                os.remove(os.path.join(base, name))
            except FileNotFoundError:
                pass
            except OSError as e:
                current_app.logger.warning("Old recording %s/%s/%s could not be removed: %s", uid, pid, name, e)
        _used.pop(uid, None)


def _write_staged(session, gone=()):
    """Finalize committed immutable recordings. A failed move leaves the durable file readable and retryable."""
    for uid, pid, name in session.info.pop(_WRITE, []):
        if (uid, pid) in gone:
            continue  # the participation went in the same commit
        try:
            _finalize(uid, pid, name)
        except OSError as e:
            current_app.logger.warning("Voice recording %s/%s/%s remains in durable staging: %s", uid, pid, name, e)


def _finalize(uid, pid, name):
    where = folder(uid, pid)
    pending = os.path.join(where, ".pending", name)
    if os.path.isfile(pending):
        os.replace(pending, os.path.join(where, name))
        if os.name != "nt":
            _sync_directory(where)
            _sync_directory(os.path.join(where, ".pending"))
    try:
        os.rmdir(os.path.join(where, ".pending"))
    except OSError:
        pass


def _sync_directory(where):
    directory = os.open(where, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


@event.listens_for(Session, "after_rollback")
def _keep_doomed(session):
    session.info.pop(_DOOMED, None)
    session.info.pop(_WRITE, None)  # the step was not stored: the earlier recording stays as it is
    session.info.pop(_CLEAR, None)


def run(now=None):
    """Sweep what no participation holds any more: folders of deleted surveys, of participations deleted in bulk
    (test data, dummy data), and of first pages that were never stored (after STALE_HOURS). Returns the number of
    folders removed. Never raises."""
    import time

    from app.extensions import db
    from app.models import Response, Survey

    base = root()
    if not os.path.isdir(base):
        return 0
    cutoff = (now.timestamp() if now else time.time()) - STALE_HOURS * 3600
    gone = 0
    try:
        for uid in os.listdir(base):
            survey = db.session.scalar(db.select(Survey).filter_by(uid=uid))
            if survey is None:
                remove_survey(uid)
                gone += 1
                continue
            # only the participations that have a folder here, not every answer of the survey (code review
            # 2026-10-15, E3)
            folders = os.listdir(os.path.join(base, uid))
            references = {}
            for start in range(0, len(folders), 500):
                rows = db.session.execute(db.select(Response.pid, Response.answers).filter(
                    Response.survey_id == survey.id, Response.pid.in_(folders[start:start + 500]))).all()
                references.update({pid: {v["audio"]["file"] for v in (answers or {}).values() if isinstance(v, dict)
                                         and isinstance(v.get("audio"), dict) and isinstance(v["audio"].get("file"), str)}
                                   for pid, answers in rows})
            pids = set(references)
            for pid in folders:
                where = os.path.join(base, uid, pid)
                if pid not in pids and os.path.getmtime(where) < cutoff:
                    shutil.rmtree(where, ignore_errors=True)
                    gone += 1
                elif pid in pids:
                    for name in os.listdir(where):
                        full = os.path.join(where, name)
                        if FILE_RE.fullmatch(name) and name not in references[pid] and os.path.getmtime(full) < cutoff:
                            os.remove(full)
                            _used.pop(uid, None)
                    pending = os.path.join(where, ".pending")
                    for name in os.listdir(pending) if os.path.isdir(pending) else ():
                        if name in references[pid]:
                            try:
                                _finalize(uid, pid, name)
                            except OSError:
                                pass  # the durable file remains downloadable; another sweep retries
                        elif os.path.getmtime(os.path.join(pending, name)) < cutoff:
                            os.remove(os.path.join(pending, name))
    except OSError as e:
        current_app.logger.error("Voice recordings not swept: %s", e)
    return gone


def zip_file(survey, responses):
    """The recordings of these participations as a ZIP: <pid>_<item code>.<ext>, the name the export column
    <code>_audio holds. Opus and AAC are compressed already: stored, not deflated again. In a temporary file (at the
    start, for send_file) that goes to the disk past 16 MB: in memory the ZIP took up to twice VOICE_MAX_MB per
    download (audit 2026-10-09, D3)."""
    import tempfile
    import zipfile

    items = [i for b in survey.blocks for i in b.items if i.type == "voice"]
    buf = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for r in responses:
            for item in items:
                answer = (r.answers or {}).get(str(item.id))
                full = path(survey.uid, r.pid, answer)
                if full:
                    z.write(full, export_name(r.pid, item, answer))
    buf.seek(0)
    return buf


def export_name(pid, item, answer):
    return f"{pid}_{item.code}.{answer['audio']['file'].rsplit('.', 1)[1]}"


# --- the item type -----------------------------------------------------------------------------------------------


@register
class Voice(ItemType):
    key = "voice"
    read_keys = ("text_fallback",)  # switched off the typed answers leave the export (audit 2026-10-12, M-3)
    label = Both("Sprachnachricht", "Voice message")
    short = Both("Antwort einsprechen", "Speak the answer")
    description = Both("Teilnehmende sprechen ihre Antwort ein (Antippen zum Starten und Beenden), auf Wunsch auch tippen",
                       "Participants speak their answer (tap to start and stop), or type it if they wish")
    order = 22
    fact_sheet = FactSheet(
        "structure",
        keywords=["Sprachnachricht", "Voice message", "Audio", "Aufnahme", "Recording", "Mikrofon", "Microphone"],
        can=[("Teilnehmende sprechen ihre Antwort ein, auf Wunsch tippen sie stattdessen.",
              "Participants speak their answer; if they wish, they type it instead.")],
        good_for=[("Lange, erzählende Antworten, die man ungern tippt",
                   "Long, narrative answers people do not like to type"),
                  ("Teilnehmende am Handy", "Participants on a phone")],
        not_for=[("Kurze Fakten (dann Freitext oder Auswahl)", "Short facts (then free text or a choice)")],
        designs=["cross", "interview"],
        examples=[("Erzählen Sie von Ihrer letzten Begegnung mit einem Roboter.",
                   "Tell us about your last encounter with a robot.")],
    )
    result_kind = None  # recordings are listened to, not counted
    allowed_in_loop = False  # one file per item and participation
    allowed_in_separated = False
    import_rules = {"max_seconds": r_int(MIN_SECONDS, MAX_SECONDS), "text_fallback": r_flag}

    def default_config(self):
        return {"max_seconds": DEFAULT_SECONDS, "text_fallback": True}

    def default_texts(self, lang):
        return {"question": tr(lang, "Was möchten Sie uns dazu sagen?", "What would you like to tell us about this?"),
                "notice": tr(lang, "Ihre Stimme wird aufgezeichnet und gespeichert. An der Stimme kann man Menschen "
                                   "erkennen; nennen Sie bitte keine Namen.",
                             "Your voice is recorded and stored. Voices can identify people; please do not mention names.")}

    def max_seconds(self, item):
        return int((item.config or {}).get("max_seconds") or DEFAULT_SECONDS)

    def text_fallback(self, item):
        return bool((item.config or {}).get("text_fallback", True))

    def update_from_form(self, item, form, lang, structural):
        super().update_from_form(item, form, lang, structural)
        texts = dict(item.texts or {})
        texts[lang] = {**texts.get(lang, {}), "notice": form.get("notice", "").strip()[:500]}
        item.texts = texts
        if structural:
            n = whole(form.get("max_seconds", "").strip())
            item.config = {**(item.config or {}),
                           "max_seconds": min(max(n, MIN_SECONDS), MAX_SECONDS) if n else DEFAULT_SECONDS,
                           "text_fallback": bool(form.get("text_fallback"))}

    # --- answers ---------------------------------------------------------------------------------------------

    def _participation(self):
        response = g.get("participation") if has_request_context() else None
        return response if response is not None and getattr(response, "pid", None) else None

    def _previous(self, item, response):
        old = (response.answers or {}).get(str(item.id)) if response is not None else None
        return old if isinstance(old, dict) and isinstance(old.get("audio"), dict) else None

    def closed(self, item):
        """True when recordings cannot be stored for this survey now (VOICE_MAX_MB, free disk): the page shows the
        text field instead of the recorder, also where the item has none otherwise."""
        survey = item.block.survey if item.block is not None else None
        return survey is not None and closed(survey.uid) is not None

    def limit_bytes(self, item):
        return limit(self.max_seconds(item))

    def _text(self, item, form, typing=False):
        if not (typing or self.text_fallback(item)):
            return ""
        return str(form.get(f"i{item.id}_text", "") or "").strip()[:MAX_TEXT]

    def parse_answer(self, item, form, lang="de", meta=None):
        m = pick(MESSAGES, lang)
        response = self._participation()
        audio = None
        upload = request.files.get(f"i{item.id}_audio") if has_request_context() and request.files else None
        full = self.closed(item)
        if upload is not None and upload.filename:
            if response is None or not folder(response.survey.uid, response.pid):
                raise AnswerError(m["refused"])
            read = _read(upload, self.limit_bytes(item))
            if read is None:
                raise AnswerError(m["refused"])
            why = closed(response.survey.uid, len(read[0]))
            if not why:
                from app import limits

                why = None if limits.allow_voice_bytes(response.survey, len(read[0])) else "sender"
            if why:
                current_app.logger.warning("Voice recording refused for survey %s: %s", response.survey.uid,
                                           {"survey": "VOICE_MAX_MB reached", "sender": "the sender's share is used up"}
                                           .get(why, "the disk is nearly full"))
                if not self._text(item, form, typing=True):
                    raise AnswerError(m["full"])
                full = True  # the typed answer stands in for it
            else:
                _stage(response, item, read[0], read[1], (self._previous(item, response) or {}).get("audio"))
                seconds = whole(str(form.get(f"i{item.id}_seconds", "")).strip()) or 0
                audio = {"file": f"{item.id}.{MIMES[read[1]]}", "mime": read[1], "bytes": len(read[0]),
                         "seconds": min(seconds, self.max_seconds(item) + 5)}
        elif form.get(f"i{item.id}_keep") == "1":
            old = self._previous(item, response)
            if old and path(response.survey.uid, response.pid, old):
                audio = old["audio"]
        elif response is not None and self._previous(item, response):
            from app.extensions import db

            previous = self._previous(item, response).get("audio") or {}
            if previous.get("file"):
                db.session.info.setdefault(_CLEAR, []).append((response.survey.uid, response.pid, previous["file"]))
        text = self._text(item, form, typing=full)
        if audio is None and not text:
            if item.required:
                raise AnswerError(m["full"] if full and not self.text_fallback(item)
                                  else m["required_or_type" if self.text_fallback(item) or full else "required"])
            return None
        return {**({"audio": audio} if audio else {}), **({"text": text} if text else {})}

    def display_value(self, item, form, meta=None):
        old = self._previous(item, self._participation()) if form.get(f"i{item.id}_keep") == "1" else None
        text = self._text(item, form, typing=True)
        return {**(old or {}), **({"text": text} if text else {})} or None

    def is_missing(self, item, answer):
        return not (isinstance(answer, dict) and (answer.get("audio") or answer.get("text")))

    def dummy_fields(self, item, rng, meta, answers=None):
        if self.text_fallback(item) and rng.random() < 0.7:
            return {f"i{item.id}_text": rng.choice(["Das finde ich gut.", "Bin mir nicht sicher.", "Zu teuer."])}
        return {}

    # --- export ------------------------------------------------------------------------------------------------

    def open_columns(self, item):
        # the file name holds the pid; typed text
        return [c for c, _ in self.export_columns(item) if c.endswith(("_audio", "_text"))]

    def export_columns(self, item):
        # the typed text always has its column: also without "Tippen möglich" the page offers only the text field
        # when no recording can be stored (closed()), and the setting may be switched off after answers came in
        # (audit 2026-10-12, M-1)
        return [(f"{item.code}_audio", f"{item.text('question')} (Datei im ZIP der Sprachnachrichten)"),
                (f"{item.code}_sec", "Länge der Sprachnachricht in Sekunden"),
                (f"{item.code}_text", f"{item.text('question')} (getippt)")]

    def export_values(self, item, answer):
        audio = (answer or {}).get("audio") if isinstance(answer, dict) else None
        return {f"{item.code}_audio": None, f"{item.code}_sec": audio.get("seconds") if audio else None,
                f"{item.code}_text": (answer or {}).get("text") if isinstance(answer, dict) else None}

    def export_response_values(self, item, answer, response):
        audio = (answer or {}).get("audio") if isinstance(answer, dict) else None
        return {f"{item.code}_audio": export_name(response.pid, item, answer)} if audio and response.pid else {}

    def codebook_info(self, item, column, lang, ui=None):
        if column == f"{item.code}_audio":
            return "Text", in_lang(ui, "Dateiname", "file name"), [
                in_lang(ui, "die Aufnahme liegt im ZIP „Sprachnachrichten“ (Reiter „Export“)",
                        "the recording is in the ZIP “Sprachnachrichten” (voice messages, tab “Export”)"),
                in_lang(ui, "leer, wenn nichts aufgenommen wurde", "empty if nothing was recorded")]
        if column == f"{item.code}_sec":
            return "Zahl", f"0–{self.max_seconds(item)}", [in_lang(ui, "vom Browser gemessen", "measured by the browser")]
        notes = [in_lang(ui, "statt einer Aufnahme getippt", "typed instead of a recording")]
        if not self.text_fallback(item):
            notes.append(in_lang(ui, "„Tippen möglich“ ist aus: getippt nur, weil keine Aufnahme gespeichert werden konnte "
                                     "(Speicher der Befragung voll) oder bevor die Einstellung abgeschaltet wurde",
                                 "“typing possible” is off: typed only because no recording could be stored (the "
                                 "survey's storage was full) or before the setting was switched off"))
        return "Text", "", notes

    def questionnaire_lines(self, item, lang):
        return [tr(lang, f"[Sprachnachricht, höchstens {self.max_seconds(item)} Sekunden"
                         f"{'; Tippen möglich' if self.text_fallback(item) else ''}]",
                   f"[Voice message, at most {self.max_seconds(item)} seconds"
                   f"{'; typing possible' if self.text_fallback(item) else ''}]")]
