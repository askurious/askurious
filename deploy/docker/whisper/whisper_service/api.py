import hmac
import json
import re
import time
import threading

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge

from .queue import QueueFull
from .settings import options

JOB_ID = re.compile(r"[0-9a-f]{32}\Z")


def create_app(queue, ready=lambda: True):
    app = Flask(__name__)
    uploads = threading.BoundedSemaphore(2)
    app.config.update(MAX_CONTENT_LENGTH=queue.settings.max_bytes + 1024 * 1024,
                      MAX_FORM_MEMORY_SIZE=256 * 1024, MAX_FORM_PARTS=12)

    @app.before_request
    def authorize():
        if request.path != "/healthz" and not hmac.compare_digest(
                request.headers.get("Authorization", "").encode(), f"Bearer {queue.settings.token}".encode()):
            return jsonify(error="unauthorized"), 401
        if request.method == "POST":
            if not uploads.acquire(blocking=False):
                return jsonify(error="busy"), 429
            g.upload_slot = True

    @app.teardown_request
    def release(_error):
        if g.pop("upload_slot", False):
            uploads.release()

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_error):
        return jsonify(error="upload_too_large"), 413

    @app.get("/healthz")
    def health():
        return jsonify(ok=bool(ready())), 200 if ready() else 503

    @app.get("/v1/status")
    def status():
        if not ready():
            return jsonify(state="unavailable", queued=0, running=0), 503
        counts = queue.counts()
        return jsonify(state="busy" if counts["queued"] or counts["running"] else "ready", **counts)

    def submit():
        if not ready():
            return None, (jsonify(error="models_not_ready"), 503)
        audio = request.files.get("file")
        if audio is None:
            return None, (jsonify(error="missing_audio"), 400)
        try:
            parameters = options(request.form)
            return queue.submit(audio.stream, parameters), None
        except ValueError as error:
            return None, (jsonify(error=str(error)), 400)
        except QueueFull:
            return None, (jsonify(error="queue_full"), 429)

    @app.post("/v1/jobs")
    def add():
        job_id, error = submit()
        return error if error is not None else (jsonify(id=job_id), 202)

    def visible(job_id):
        if not JOB_ID.fullmatch(job_id):
            return None
        row = queue.get(job_id)
        if not row:
            return None
        out = {"id": row["id"], "state": row["state"]}
        out.update(queue.progress(row))
        if row['state'] in {'queued', 'running'}:
            out['estimate'] = queue.estimate(job_id)
        if row["state"] == "done":
            out["result"] = json.loads(row["result"])
        elif row["error"]:
            out["error"] = row["error"]
        return out

    @app.get("/v1/jobs/<job_id>")
    def get(job_id):
        out = visible(job_id)
        return (jsonify(out), 200) if out else (jsonify(error="not_found"), 404)

    @app.delete("/v1/jobs/<job_id>")
    def cancel(job_id):
        if not JOB_ID.fullmatch(job_id):
            return jsonify(error="not_found"), 404
        return (jsonify(ok=True), 200) if queue.cancel(job_id) else (jsonify(error="not_found"), 404)

    @app.post("/v1/audio/transcriptions")
    def compatible():
        job_id, error = submit()
        if error is not None:
            return error
        deadline = time.monotonic() + queue.settings.job_seconds
        while time.monotonic() < deadline:
            out = visible(job_id)
            if not out or out["state"] in {"failed", "cancelled"}:
                return jsonify(error=out.get("error", "cancelled") if out else "expired"), 422
            if out["state"] == "done":
                return jsonify(out["result"])
            time.sleep(1)
        return jsonify(error="wait_timeout"), 504

    return app
