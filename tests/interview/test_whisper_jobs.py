"""Optional local job integration: streaming, resume and committed erasure."""
import io
import json
import threading

import requests
import pytest
from flask import Flask, request
from werkzeug.serving import make_server

from werkzeug.formparser import parse_form_data

from app import interview_audio, interview_transcribe, whisper_jobs
from app.extensions import db
from app.models import Response
from tests.interview.test_interview import make
from tests.interview.test_interview_data import start


def test_upload_stream_has_exact_length_and_roundtrips(tmp_path):
    path = tmp_path / "private.webm"
    path.write_bytes(b"audio" * 50000)
    body, headers = whisper_jobs._body(path, {"prompt": "Ärzte\r\nund Pflege", "model": "small"})
    chunks = list(body)
    assert max(map(len, chunks[1:-1])) <= 64 * 1024
    payload = b"".join(chunks)
    assert len(payload) == int(headers["Content-Length"])
    _, fields, files = parse_form_data({"wsgi.input": io.BytesIO(payload), "CONTENT_TYPE": headers["Content-Type"],
                                       "CONTENT_LENGTH": headers["Content-Length"], "REQUEST_METHOD": "POST"})
    assert fields["prompt"] == "Ärzte\r\nund Pflege"
    assert files["file"].read() == path.read_bytes()


def test_real_http_upload_has_consistent_framing_and_streamed_audio(tmp_path):
    path = tmp_path / "audio.wav"
    audio = b"audio" * 180000
    path.write_bytes(audio)
    received = []
    service = Flask("upload-check")
    @service.post("/upload")
    def upload():
        received.append(request.files["file"].read())
        return {"ok": True}
    server = make_server("127.0.0.1", 0, service)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        body, headers = whisper_jobs._body(path, {"model": "small"})
        prepared = requests.Request("POST", "http://127.0.0.1/upload", headers=headers, data=body).prepare()
        assert "Transfer-Encoding" not in prepared.headers
        with path.open("ab") as target:
            target.write(b"new recording bytes after the declared upload length")
        response = requests.post(f"http://127.0.0.1:{server.server_port}/upload", headers=headers, data=body, timeout=5)
        assert response.status_code == 200 and received == [audio]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_speaker_changes_become_separate_paragraphs():
    segments = interview_transcribe._segments({"segments": [
        {"start": 0, "end": 1, "text": "Hallo", "speaker": "SPEAKER_00"},
        {"start": 1, "end": 2, "text": "Wie geht es?", "speaker": "SPEAKER_00"},
        {"start": 2, "end": 3, "text": "Gut", "speaker": "SPEAKER_01"}]})
    assert interview_transcribe.paragraphs(segments) == [(2, "S1: Hallo Wie geht es?"), (3, "S2: Gut")]


def test_resume_polls_existing_job_without_upload(app, tmp_path, monkeypatch):
    app.config.update(WHISPER_JOBS=True, WHISPER_URL="http://whisper:8091", WHISPER_MODEL="small")
    path = tmp_path / "audio"
    path.write_bytes(b"audio")
    calls = []
    class Reply:
        status_code = 200
        def json(self):
            return {"state": "done", "result": {"text": "fertig"}}
    monkeypatch.setattr(whisper_jobs.requests, "get", lambda *a, **kw: Reply())
    monkeypatch.setattr(whisper_jobs.requests, "post", lambda *a, **kw: calls.append("upload"))
    with app.app_context():
        fields = {"model": "small", "language": "de", "prompt": "", "align": "true", "diarize": "true"}
        import hashlib
        previous = {"id": "a" * 32, "service": whisper_jobs.namespace(),
                    "options": hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()}
        assert whisper_jobs.transcribe(path, "de", "", previous)["text"] == "fertig"
    assert calls == []


def test_remote_erasure_follows_commit_and_not_rollback(app, client, monkeypatch):
    code, _ = make(app, client)
    rid = start(app, client, code)
    erased = []
    monkeypatch.setattr(whisper_jobs, "cancel_records", lambda records, **kwargs: erased.append(records))
    with app.app_context():
        row = db.session.get(Response, rid)
        row.interview = {**row.interview, "transcript": {"jobs": {"1": {"id": "a" * 32}}}}
        db.session.commit()
        interview_audio._cancel_jobs(row)
        db.session.rollback()
        assert erased == []
        row = db.session.get(Response, rid)
        db.session.delete(row)
        db.session.flush()
        assert erased == []
        db.session.commit()
    assert erased == [{"0": {"id": "a" * 32}}]


def test_cleanup_without_whisper_key_uses_bounded_relay(app, monkeypatch):
    app.config.update(WHISPER_JOBS=True, WHISPER_URL="http://whisper:8091", WHISPER_TOKEN="",
                      WHISPER_CANCEL_URL="http://researcher:8000/guide/internal/whisper/cancel", SECRET_KEY="k" * 48)
    calls = []
    class Reply:
        status_code = 200
    monkeypatch.setattr(whisper_jobs.requests, "post", lambda url, **kwargs: calls.append((url, kwargs)) or Reply())
    with app.app_context():
        record = {"id": "a" * 32, "service": whisper_jobs.namespace(), "bytes": 100, "options": "private"}
        assert whisper_jobs.cancel_records({"1": record, "2": {**record, "service": "old-service"}})
    assert len(calls) == 1
    assert calls[0][1]["json"] == {"jobs": {"1": {"id": record["id"], "service": record["service"]}}}
    with app.app_context():
        assert calls[0][1]["headers"] == {"X-Whisper-Cancel": whisper_jobs.relay_key()}


def test_cleanup_relay_requires_shared_secret_and_deletes_only(app, client, monkeypatch):
    """Abgleich 2026-10-15, NB-9: the key comes from SECRET_KEY, not from COMPUTE_SECRET, which the Rechenknecht has."""
    app.config.update(WHISPER_JOBS=True, WHISPER_URL="http://whisper:8091", WHISPER_TOKEN="t" * 48, COMPUTE_SECRET="s" * 48,
                      SECRET_KEY="k" * 48)
    route = "/guide/internal/whisper/cancel"
    assert client.post(route, json={"jobs": {}}).status_code == 403
    assert client.post(route, json={"jobs": {}}, headers={"X-Whisper-Cancel": "s" * 48}).status_code == 403
    with app.app_context():
        key = whisper_jobs.relay_key()
    assert len(key) == 64
    calls = []
    class Reply:
        status_code = 200
    monkeypatch.setattr(whisper_jobs.requests, "delete", lambda url, **kwargs: calls.append(url) or Reply())
    with app.app_context():
        record = {"id": "a" * 32, "service": whisper_jobs.namespace()}
    response = client.post(route, json={"jobs": {"1": record}}, headers={"X-Whisper-Cancel": key})
    assert response.status_code == 200
    assert calls == ["http://whisper:8091/v1/jobs/" + "a" * 32]


def test_memory_failure_recommends_smaller_model_or_shorter_audio(app, tmp_path, monkeypatch):
    app.config.update(WHISPER_JOBS=True, WHISPER_URL='http://whisper:8091')
    path = tmp_path / 'audio'
    path.write_bytes(b'audio')
    class Reply:
        status_code = 200
        def json(self):
            return {'state': 'failed', 'error': 'memory_limit'}
    class Submitted(Reply):
        status_code = 202
        def json(self):
            return {'id': 'a' * 32}
    monkeypatch.setattr(whisper_jobs.requests, 'post', lambda *a, **kw: Submitted())
    monkeypatch.setattr(whisper_jobs.requests, 'get', lambda *a, **kw: Reply())
    with app.app_context(), pytest.raises(whisper_jobs.MemoryLimitError) as error:
        whisper_jobs.transcribe(path, 'de', '')
    assert 'small' in str(error.value) and 'Speicherlimit' in str(error.value) and 'fertige Teile' in str(error.value)
    assert 'demselben Modell' in str(error.value)


def test_private_progress_validates_and_retains_estimate_for_the_display(app, tmp_path, monkeypatch):
    app.config.update(WHISPER_JOBS=True, WHISPER_URL='http://whisper:8091')
    path = tmp_path / 'audio'
    path.write_bytes(b'audio')
    replies = iter([{'state': 'queued', 'stage': 'queued', 'position': 1, 'estimate': {'seconds': True}},
                    {'state': 'running', 'stage': 'aligning', 'estimate': {'seconds': 600, 'sampled': True}},
                    {'state': 'done', 'result': {'text': 'done'}}])
    class Reply:
        status_code = 200
        def json(self):
            return next(replies)
    class Submitted(Reply):
        status_code = 202
        def json(self):
            return {'id': 'a' * 32}
    monkeypatch.setattr(whisper_jobs.requests, 'post', lambda *a, **kw: Submitted())
    monkeypatch.setattr(whisper_jobs.requests, 'get', lambda url, *a, **kw: Reply() if '/v1/jobs/' in url else Submitted())
    monkeypatch.setattr(whisper_jobs.time, 'sleep', lambda _: None)
    monkeypatch.setattr(whisper_jobs.time, 'time', lambda: 1000020)
    values = []
    with app.app_context():
        assert whisper_jobs.transcribe(path, 'de', '', progress=values.append) == {'text': 'done'}
    assert values[0] == {'stage': 'queued', 'position': 1}
    assert values[1] == {'stage': 'aligning', 'position': 0, 'eta_until': 1000620, 'eta_sampled': True, 'eta_overdue': False}


def test_a_full_service_makes_the_request_wait_in_the_app(app, tmp_path, monkeypatch):
    """Ralf, 2026-10-19: 50 and more overlapping requests; while the service has no free place the app waits (without
    sending the audio again and again) instead of failing, and a queued job does not use up WHISPER_TIMEOUT."""
    app.config.update(WHISPER_JOBS=True, WHISPER_URL='http://whisper:8091', WHISPER_TIMEOUT=10)
    path = tmp_path / 'audio'
    path.write_bytes(b'audio')
    free = iter([0, 0, 1, 1])
    jobs = iter([{'state': 'queued', 'stage': 'queued', 'position': 8}] * 3 + [{'state': 'done', 'result': {'text': 'ok'}}])
    clock = iter(range(0, 1000, 6))
    sent = []
    class Reply:
        def __init__(self, code, data):
            self.status_code, self.data = code, data
        def json(self):
            return self.data
    def get(url, *a, **kw):
        return Reply(200, {'state': 'busy', 'queued': 7, 'running': 1, 'free': next(free)}) if url.endswith('/v1/status') else Reply(200, next(jobs))
    def post(url, *a, data=None, **kw):
        b''.join(data)
        sent.append(url)
        return Reply(429, {'error': 'queue_full'}) if len(sent) == 1 else Reply(202, {'id': 'b' * 32})
    monkeypatch.setattr(whisper_jobs.requests, 'get', get)
    monkeypatch.setattr(whisper_jobs.requests, 'post', post)
    monkeypatch.setattr(whisper_jobs.time, 'sleep', lambda _: None)
    monkeypatch.setattr(whisper_jobs.time, 'monotonic', lambda: next(clock))
    values = []
    with app.app_context():
        assert whisper_jobs.transcribe(path, 'de', '', progress=values.append) == {'text': 'ok'}
    assert len(sent) == 2  # no upload while the status said full, one refused (429) and then taken
    assert [v['stage'] for v in values[:3]] == ['waiting'] * 3 and values[3]['stage'] == 'queued'


def test_default_model_is_turbo_unless_the_env_names_small(app):
    """Ralf, 2026-10-18: large-v3-turbo is the default; WHISPER_MODEL=small keeps small, anything else (also the
    unset "whisper-1") gives turbo."""
    assert whisper_jobs.default_model({"WHISPER_MODEL": "small"}) == "small"
    assert whisper_jobs.default_model({"WHISPER_MODEL": "large-v3-turbo"}) == "large-v3-turbo"
    assert whisper_jobs.default_model({"WHISPER_MODEL": "whisper-1"}) == "large-v3-turbo"
    assert whisper_jobs.default_model({}) == "large-v3-turbo"
    app.config.update(WHISPER_JOBS=False, WHISPER_MODEL="whisper-1")
    assert interview_transcribe._default_model(app.config) == "whisper-1"  # the outside server keeps its name
    app.config.update(WHISPER_JOBS=True)
    assert interview_transcribe._default_model(app.config) == "large-v3-turbo"


@pytest.mark.parametrize("state, running, queued, shown", [("busy", 1, 2, "1 läuft, 2 warten"),
                                                           ("ready", 0, 0, "bereit, nichts zu tun")])
def test_admin_server_page_shows_whisper_load(app, client, monkeypatch, state, running, queued, shown):
    """Ralf, 2026-10-18: admins see whether WhisperX computes; the public status page still does not."""
    app.config.update(WHISPER_JOBS=True, WHISPER_URL="http://whisper:8091", WHISPER_MODEL="whisper-1")
    monkeypatch.setattr(whisper_jobs, "status", lambda: ({"state": state, "queued": queued, "running": running}, 200))
    page = client.get("/admin/database").get_data(as_text=True)
    assert "Transkription (WhisperX)" in page and shown in page and "<code>large-v3-turbo</code>" in page


def test_admin_server_page_without_whisper_has_no_section(app, client):
    app.config.update(WHISPER_JOBS=False, WHISPER_URL="")
    assert "Transkription (WhisperX)" not in client.get("/admin/database").get_data(as_text=True)


def _done_after(failures):
    """A fake GET of the job service: `failures` connection errors (a container restarting), then the finished job."""
    calls = []

    class Reply:
        status_code = 200

        def json(self):
            return {"state": "done", "result": {"text": "fertig"}}

    def get(*args, **kwargs):
        calls.append(1)
        if len(calls) <= failures:
            raise requests.ConnectionError("restarting")
        return Reply()
    return get, calls


def test_a_short_gap_while_polling_does_not_fail_the_job(app, tmp_path, monkeypatch):
    """Deploy 2026-10-18: a connection error while WhisperX still computed marked the transcription as failed."""
    app.config.update(WHISPER_JOBS=True, WHISPER_URL="http://whisper:8091", WHISPER_TOKEN="t" * 48)
    path = tmp_path / "audio"
    path.write_bytes(b"audio")
    get, calls = _done_after(3)
    monkeypatch.setattr(whisper_jobs.requests, "get", get)
    monkeypatch.setattr(whisper_jobs.time, "sleep", lambda seconds: None)

    class Accepted:
        status_code = 202

        def json(self):
            return {"id": "b" * 32}
    monkeypatch.setattr(whisper_jobs.requests, "post", lambda *a, **kw: Accepted())
    with app.app_context():
        assert whisper_jobs.transcribe(path, "de", "")["text"] == "fertig"
    assert len(calls) == 4
    monkeypatch.setattr(whisper_jobs, "POLL_GRACE", 0)        # unreachable for longer than the grace: an error
    get, calls = _done_after(99)
    monkeypatch.setattr(whisper_jobs.requests, "get", get)
    with app.app_context(), pytest.raises(requests.ConnectionError):
        whisper_jobs.transcribe(path, "de", "")


def test_housekeeping_hands_broken_off_jobs_to_the_researchers_container(app, client, monkeypatch):
    """Deploy 2026-10-18: housekeeping is neither in the Whisper network nor has WHISPER_TOKEN; it took up the
    transcription itself and failed it with "nicht erreichbar (ConnectionError)". Now it asks the resume relay."""
    from datetime import timedelta

    from app.models import utcnow

    code, _ = make(app, client)
    rid = start(app, client, code)
    app.config.update(WHISPER_JOBS=True, WHISPER_URL="http://whisper:8091", WHISPER_TOKEN="", SECRET_KEY="k" * 48,
                      WHISPER_CANCEL_URL="http://researcher:8000/guide/internal/whisper/cancel")
    old = (utcnow() - timedelta(minutes=10)).isoformat(timespec="seconds")
    with app.app_context():
        row = db.session.get(Response, rid)
        interview_transcribe._set(row, state="running", started=old, beat=old, run="weg",
                                  jobs={"1": {"id": "a" * 32}})
        db.session.commit()
    posted = []

    class Reply:
        status_code = 200

        def json(self):
            return {"taken": 1}
    monkeypatch.setattr(whisper_jobs.requests, "post", lambda url, **kw: posted.append((url, kw)) or Reply())
    monkeypatch.setattr(whisper_jobs.requests, "get", lambda *a, **kw: pytest.fail("housekeeping polled WhisperX"))
    with app.app_context():
        assert interview_transcribe.resume_stale() == 1
        assert posted[0][0] == "http://researcher:8000/guide/internal/whisper/resume"
        assert posted[0][1]["headers"] == {"X-Whisper-Cancel": whisper_jobs.relay_key()}
        tr = interview_transcribe.state(db.session.get(Response, rid))
    assert tr["state"] == "running" and tr["jobs"] == {"1": {"id": "a" * 32}}
    monkeypatch.setattr(whisper_jobs.requests, "post", lambda *a, **kw: (_ for _ in ()).throw(requests.ConnectionError()))
    with app.app_context():                                     # researchers' container restarting: next round
        assert interview_transcribe.resume_stale() == 0


def test_resume_relay_requires_the_key_and_the_whisper_key(app, client, monkeypatch):
    app.config.update(WHISPER_JOBS=True, WHISPER_URL="http://whisper:8091", WHISPER_TOKEN="", SECRET_KEY="k" * 48)
    route = "/guide/internal/whisper/resume"
    assert client.post(route).status_code == 403
    with app.app_context():
        key = whisper_jobs.relay_key()
    assert client.post(route, headers={"X-Whisper-Cancel": key}).status_code == 503
    app.config["WHISPER_TOKEN"] = "t" * 48
    monkeypatch.setattr(interview_transcribe, "resume_stale", lambda: 2)
    response = client.post(route, headers={"X-Whisper-Cancel": key})
    assert response.status_code == 200 and response.get_json() == {"taken": 2}


def test_the_participants_side_queues_cancels_and_the_housekeeping_hands_them_on(app, monkeypatch):
    """Audit 2026-10-22, E-01 stage 2b: the participants' containers no longer reach the researchers' container, so a
    deletion there queues the cancel in whisper_cancel; the housekeeping hands it on through the relay and deletes the
    rows that went through, keeps them while the relay is down."""
    from app.models import WhisperCancel

    app.config.update(WHISPER_JOBS=True, WHISPER_URL="http://whisper:8091", WHISPER_TOKEN="", SIDE="participant",
                      WHISPER_CANCEL_URL="http://researcher:8000/guide/internal/whisper/cancel", SECRET_KEY="k" * 48)
    calls = []

    class Reply:
        status_code = 200

    monkeypatch.setattr(whisper_jobs.requests, "post", lambda url, **kwargs: calls.append((url, kwargs)) or Reply())
    with app.app_context():
        record = {"id": "a" * 32, "service": whisper_jobs.namespace()}
        assert whisper_jobs.cancel_records({"1": record, "2": {**record, "service": "old-service"}})
        assert calls == []                                                   # no call from the participants' side
        rows = db.session.scalars(db.select(WhisperCancel)).all()
        assert [(r.job_id, r.service) for r in rows] == [("a" * 32, whisper_jobs.namespace())]
    app.config["SIDE"] = "researcher"                                        # the housekeeping
    monkeypatch.setattr(whisper_jobs.requests, "post", lambda url, **kwargs: (_ for _ in ()).throw(
        requests.ConnectionError()))
    with app.app_context():
        assert whisper_jobs.drain_cancels() == 0                             # relay down: the row stays
        assert db.session.scalar(db.select(db.func.count(WhisperCancel.id))) == 1
    monkeypatch.setattr(whisper_jobs.requests, "post", lambda url, **kwargs: calls.append((url, kwargs)) or Reply())
    with app.app_context():
        assert whisper_jobs.drain_cancels() == 1
        assert db.session.scalar(db.select(db.func.count(WhisperCancel.id))) == 0
    assert len(calls) == 1 and list(calls[0][1]["json"]["jobs"].values()) == [
        {"id": "a" * 32, "service": record["service"]}]
