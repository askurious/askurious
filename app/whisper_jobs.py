"""Optional durable job protocol of the institution's local WhisperX service."""
import hashlib
import hmac
import json
import os
import random
import re
import secrets
import time

import requests
from flask import current_app

from app.lang import de_en

JOB_ID = re.compile(r"[0-9a-f]{32}\Z")
MODELS = ("small", "large-v3-turbo")
DEFAULT_MODEL = "large-v3-turbo"  # Ralf, 2026-10-18; small stays selectable per job


def default_model(config):
    """The model of a job nobody chose one for: WHISPER_MODEL when it names a model of the local service, else
    large-v3-turbo (an unset WHISPER_MODEL reads "whisper-1", the name of the OpenAI-compatible route)."""
    model = config.get("WHISPER_MODEL")
    return model if model in MODELS else DEFAULT_MODEL


class MemoryLimitError(RuntimeError):
    pass


def memory_limit_message():
    return de_en("Der Transkriptionsauftrag hat sein Speicherlimit erreicht. Bitte bei large-v3-turbo das kleinere Modell small wählen oder die Aufnahme in kürzere Teile aufteilen. Bei einem erneuten Versuch mit demselben Modell bleiben fertige Teile erhalten.",
                 "The transcription job reached its memory limit. If using large-v3-turbo, please choose the smaller small model or split the recording into shorter parts. When retrying with the same model, completed parts are retained.")


def namespace():
    return hashlib.sha256(current_app.config.get("WHISPER_URL", "").rstrip("/").encode()).hexdigest()


def _headers():
    token = current_app.config.get("WHISPER_TOKEN", "")
    return {"Authorization": f"Bearer {token}"} if token else {}


def _url(job_id=""):
    return current_app.config["WHISPER_URL"].rstrip("/") + "/v1/jobs" + (f"/{job_id}" if job_id else "")


def status():
    """Only aggregate operational counters, never job ids or transcription content."""
    config = current_app.config
    if not config.get("WHISPER_JOBS") or not config.get("WHISPER_URL"):
        return {"state": "disabled", "queued": 0, "running": 0}, 200
    if config.get("WHISPER_TOKEN"):
        try:
            response = requests.get(config["WHISPER_URL"].rstrip("/") + "/v1/status", headers=_headers(),
                                    timeout=(1, 2), allow_redirects=False)
            data = response.json() if response.status_code == 200 else None
            if isinstance(data, dict) and data.get("state") in ("ready", "busy"):
                counts = {key: data.get(key) for key in ("queued", "running")}  # "free" stays inside (_free)
                if all(type(value) is int and 0 <= value <= 8 for value in counts.values()) and counts["running"] <= 1 and sum(counts.values()) <= 8:
                    return {"state": "busy" if any(counts.values()) else "ready", **counts}, 200
        except (requests.RequestException, ValueError):
            pass
    return {"state": "unavailable", "queued": 0, "running": 0}, 503


def relay_key():
    """The key of the internal cancel relay (/guide/internal/whisper/cancel), "" when none can be had. Derived from
    SECRET_KEY, which all app containers share and the Rechenknecht does not have: before it was COMPUTE_SECRET, and
    with it the compute container could cancel transcriptions (Abgleich 2026-10-15, NB-9). The development key and
    keys shorter than 32 characters give none."""
    from config import DEV_SECRET_KEY

    key = current_app.config.get("SECRET_KEY") or ""
    if not isinstance(key, str) or len(key) < 32 or key == DEV_SECRET_KEY:
        return ""
    return hmac.new(key.encode(), b"askurious whisper cancel relay", hashlib.sha256).hexdigest()


def relay_url(action):
    """The internal relay of the researchers' container for action ("cancel" or "resume"), "" when none is set. Only
    that container is in the Whisper network and has WHISPER_TOKEN; housekeeping and participants reach it by
    WHISPER_CANCEL_URL, whose last path step names the action."""
    url = current_app.config.get("WHISPER_CANCEL_URL") or ""
    if not url.rstrip("/").endswith("/cancel"):
        return url if action == "cancel" else ""
    return url.rstrip("/")[:-len("cancel")] + action


def here():
    """Whether this process can talk to the job service itself (the researchers' container): with WHISPER_TOKEN.
    Housekeeping has neither the key nor the Whisper network; a job it polled failed as "nicht erreichbar
    (ConnectionError)" while WhisperX still computed it (deploy 2026-10-18)."""
    config = current_app.config
    return bool(config.get("WHISPER_JOBS") and config.get("WHISPER_URL") and config.get("WHISPER_TOKEN"))


def resume_relay():
    """Ask the researchers' container to take up broken-off transcriptions (interview_transcribe.resume_stale).
    Returns how many it took up; 0 when it cannot be reached (mid-deploy): housekeeping asks again next round."""
    url, secret = relay_url("resume"), relay_key()
    if not url or not secret:
        return 0
    try:
        response = requests.post(url, json={}, headers={"X-Whisper-Cancel": secret}, timeout=(2, 30))
        taken = response.json().get("taken") if response.status_code == 200 else None
    except (requests.RequestException, ValueError, AttributeError):
        taken = None
    if type(taken) is not int:
        current_app.logger.warning("Local transcription resume relay unavailable")
        return 0
    return taken


def cancel_records(records, parts=None, allow_relay=True):
    """Cancel retained jobs on the current service, without sending a credential to an old host."""
    if not current_app.config.get("WHISPER_JOBS") or not current_app.config.get("WHISPER_URL"):
        return False
    if records is not None and not isinstance(records, dict):
        return False
    selected = {}
    for key, record in (records or {}).items():
        if parts is not None and str(key) not in {str(part) for part in parts}:
            continue
        if not isinstance(record, dict) or record.get("service") != namespace() or not JOB_ID.fullmatch(str(record.get("id", ""))):
            continue
        selected[str(key)] = {"id": record["id"], "service": record["service"]}
    if not selected:
        return True
    if not current_app.config.get("WHISPER_TOKEN"):
        if current_app.config.get("SIDE") == "participant":
            return _queue(selected)
        relay = relay_url("cancel")
        secret = relay_key()
        if allow_relay and relay and secret:
            try:
                for offset in range(0, len(selected), 32):
                    batch = dict(list(selected.items())[offset:offset + 32])
                    response = requests.post(relay, json={"jobs": batch}, headers={"X-Whisper-Cancel": secret}, timeout=(2, 5))
                    if response.status_code != 200:
                        current_app.logger.warning("Local transcription cleanup relay unavailable")
                        return False
            except requests.RequestException:
                current_app.logger.warning("Local transcription cleanup relay unavailable")
                return False
            return True
        return False
    for record in selected.values():
        try:
            response = requests.delete(_url(record["id"]), headers=_headers(), timeout=(2, 3))
            if response.status_code not in (200, 404):
                current_app.logger.warning("Local transcription cleanup could not be confirmed")
                return False
        except requests.RequestException:
            current_app.logger.warning("Local transcription cleanup service unavailable")
            return False
    return True


def _queue(selected):
    """The participants' side: the jobs go to the table whisper_cancel, in a transaction of their own (this runs after
    the deletion's commit), and the housekeeping hands them on (drain_cancels). Their containers reach neither the
    Whisper service nor the researchers' container (audit 2026-10-22, E-01, stage 2b)."""
    from app.extensions import db
    from app.models import WhisperCancel, _random_id

    rows = [{"id": _random_id(), "job_id": record["id"], "service": record["service"], "created_at": _utcnow()}
            for record in selected.values()]
    try:
        with db.engine.begin() as con:
            con.execute(WhisperCancel.__table__.insert(), rows)
    except Exception:  # noqa: BLE001 - the deletion itself stands; the job runs out on the service
        current_app.logger.warning("Transcription cancel could not be queued")
        return False
    return True


def _utcnow():
    from app.models import utcnow

    return utcnow()


DRAIN_BATCH = 320


def drain_cancels():
    """The housekeeping: hand the queued cancels of the participants' side on (cancel_records, through the
    researchers' container) and delete the rows that went through. Returns how many. Rows of another service than the
    current one are dropped: cancel_records would not send them anyway."""
    from app.extensions import db
    from app.models import WhisperCancel

    rows = list(db.session.scalars(db.select(WhisperCancel).order_by(WhisperCancel.created_at).limit(DRAIN_BATCH)))
    if not rows:
        return 0
    records = {row.id: {"id": row.job_id, "service": row.service} for row in rows}
    if not cancel_records(records):
        db.session.rollback()
        return 0
    for row in rows:
        db.session.delete(row)
    db.session.commit()
    return len(rows)


def _body(path, fields):
    """Stream the recording; requests' files= would read the entire audio into a web worker."""
    boundary = secrets.token_hex(16)
    prefix = b"".join(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
                       for key, value in fields.items())
    prefix += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="recording"\r\n'
               'Content-Type: application/octet-stream\r\n\r\n').encode()
    suffix = f"\r\n--{boundary}--\r\n".encode()
    size = os.path.getsize(path)

    def chunks():
        yield prefix
        with open(path, "rb") as source:
            remaining = size
            while remaining:
                piece = source.read(min(64 * 1024, remaining))
                if not piece:
                    raise OSError("Recording changed during upload")
                yield piece
                remaining -= len(piece)
        yield suffix

    length = len(prefix) + size + len(suffix)

    class Body:
        def __iter__(self):
            return chunks()

        def __len__(self):
            # requests otherwise adds Transfer-Encoding: chunked despite the explicit Content-Length.
            return length

    return Body(), {"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(length)}


POLL_GRACE = 600  # seconds the service may stay unreachable before a job counts as lost (a restart of a container)


def _job(job_id):
    """The job's state as (status code, data). A connection error or a 5xx is tried again for up to POLL_GRACE seconds:
    the job runs on in WhisperX while the network or a container restarts, so a short gap never fails it."""
    until = time.monotonic() + POLL_GRACE
    while True:
        try:
            response = requests.get(_url(job_id), headers=_headers(), timeout=(5, 15))
            if response.status_code < 500:
                return response.status_code, response.json() if response.status_code == 200 else {}
            if time.monotonic() >= until:
                return response.status_code, {}
        except requests.RequestException:
            if time.monotonic() >= until:
                raise
        time.sleep(5)


WAIT_FOR_PLACE = (20, 40)  # seconds between two looks for a free place in the service, at random so waiters spread


def _free():
    """Free places in the service, None when it does not say (an older service, or not reachable): then just send."""
    try:
        response = requests.get(current_app.config["WHISPER_URL"].rstrip("/") + "/v1/status", headers=_headers(),
                                timeout=(2, 5), allow_redirects=False)
        free = response.json().get("free") if response.status_code == 200 else None
    except (requests.RequestException, ValueError, AttributeError):
        return None
    return free if type(free) is int and free >= 0 else None


def _submit(path, fields, progress):
    """Sends the recording once the service has a free place. While it is full (8 open jobs, or too little disk), the
    request waits here in the app instead of failing, however many come together (Ralf, 2026-10-19: 50 and more
    overlapping requests during an analysis). The service's own copy of the audio so still waits at most a few jobs,
    within the 24 hours of the privacy statement; the recording itself is kept by the app anyway. Cancelling works
    through progress(), as while the service computes."""
    while True:
        if _free() != 0:
            body, extra = _body(path, fields)
            response = requests.post(_url(), headers={**_headers(), **extra}, data=body, timeout=(5, 30))
            if response.status_code != 429:
                return response
        if progress is not None:
            progress({"stage": "waiting", "position": 0, "checked": int(time.time())})  # changes, so it is stored
        time.sleep(random.uniform(*WAIT_FOR_PLACE))


def transcribe(path, language, prompt, previous=None, created=None, progress=None, model=None):
    config = current_app.config
    fields = {"model": model or default_model(config), "language": language, "prompt": prompt,
              "align": str(bool(config.get("WHISPER_ALIGN", True))).lower(),
              "diarize": str(bool(config.get("WHISPER_DIARIZE", True))).lower()}
    signature = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
    job_id = None
    if isinstance(previous, dict) and previous.get("service") == namespace() and previous.get("options") == signature:
        candidate = str(previous.get("id", ""))
        if JOB_ID.fullmatch(candidate):
            code, data = _job(candidate)
            if code == 200 and data.get("state") not in ("failed", "cancelled"):
                job_id = candidate
    if job_id is None:
        response = _submit(path, fields, progress)
        if response.status_code != 202:
            raise RuntimeError(de_en("Der Transkriptionsdienst nimmt den Auftrag nicht an ({s}).",
                                     "The transcription service does not accept the job ({s}).", s=response.status_code))
        job_id = str(response.json().get("id", ""))
        if not JOB_ID.fullmatch(job_id):
            raise RuntimeError(de_en("Ungültige Antwort des Transkriptionsdienstes.", "Invalid response from the transcription service."))
    record = {"id": job_id, "service": namespace(), "options": signature}
    if created is not None:
        try:
            created(record)
        except BaseException:
            cancel_records({"job": record})
            raise
    limit = int(config.get("WHISPER_TIMEOUT") or 21600)
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        code, data = _job(job_id)
        if code == 200 and data.get("state") == "queued":  # the limit counts from the start of computing, not the wait
            deadline = time.monotonic() + limit
        if code != 200:
            raise RuntimeError(de_en("Der Transkriptionsauftrag ist nicht mehr erreichbar ({s}).",
                                     "The transcription job can no longer be reached ({s}).", s=code))
        if progress is not None and data.get("state") in ("queued", "running"):
            stage = data.get("stage")
            position = data.get("position", 0)
            stage = stage if isinstance(stage, str) and stage in {"queued", "loading", "decoding", "transcribing", "aligning", "diarizing"} else data["state"]
            position = position if type(position) is int and 0 <= position <= 8 else 0
            try:
                value = {"stage": stage, "position": position}
                estimate = data.get('estimate')
                if isinstance(estimate, dict) and type(estimate.get('seconds')) is int and 0 < estimate['seconds'] <= 7 * 86400:
                    value.update(eta_until=round((time.time() + estimate['seconds']) / 60) * 60,
                                 eta_sampled=estimate.get('sampled') is True, eta_overdue=estimate.get('overdue') is True)
                    low, high = estimate.get('low_seconds'), estimate.get('high_seconds')
                    if type(low) is int and type(high) is int and 0 < low <= high <= 7 * 86400:
                        value.update(eta_low_until=round((time.time() + low) / 60) * 60,
                                     eta_high_until=round((time.time() + high) / 60) * 60)
                progress(value)
            except BaseException:
                cancel_records({"job": record})
                raise
        if data.get("state") == "done":
            return data["result"]
        if data.get("state") in ("failed", "cancelled"):
            if data.get('error') == 'memory_limit':
                raise MemoryLimitError(memory_limit_message())
            if data.get("error") == "duration_limit":
                raise RuntimeError(de_en("Der Aufnahmeteil ist länger als 60 Minuten. Bitte vor der Transkription aufteilen.",
                                         "The recording part exceeds 60 minutes. Please split it before transcription."))
            raise RuntimeError(de_en("Der Transkriptionsauftrag wurde abgebrochen oder konnte nicht verarbeitet werden.",
                                     "The transcription job was cancelled or could not be processed."))
        time.sleep(5)
    # Timeout ends the app's wait, not a still-running durable job. Its retained id allows a later retry.
    raise RuntimeError(de_en("Der Auftrag ist noch nicht fertig. Bitte später erneut starten; fertige Teile bleiben erhalten.",
                             "The job is not finished yet. Please start again later; completed parts are retained."))
