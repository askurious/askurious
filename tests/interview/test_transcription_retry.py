"""A transcription that breaks off with a failure of the moment (the service unreachable, a 5xx, a job lost with a
restart) tries the part again by itself, without an error in the list and without text twice (Ralf 2026-10-30)."""
import requests

from app import interview_transcribe, whisper_jobs
from tests.interview.test_interview_robust import _R, _tr, _whisper


def _quick(app, monkeypatch, waits=(0, 0)):
    app.config.update(WHISPER_RETRY_WAITS=waits)
    monkeypatch.setattr(interview_transcribe, "BEAT", 0.01)


def test_server_error_and_lost_connection_are_tried_again(app, client, monkeypatch):
    replies = [_R({}, status=503), requests.ConnectionError("weg")]

    def answer(name, data):
        if replies:
            reply = replies.pop(0)
            if isinstance(reply, Exception):
                raise reply
            return reply
        return _R({"segments": [{"start": 0.0, "end": 4.0, "text": " Einmal gesagt."}]})

    code, sid, rid, calls = _whisper(app, client, monkeypatch, answer)
    _quick(app, monkeypatch)
    client.post(f"/guide/{code}/interviews/{rid}/transcribe")
    tr = _tr(app, rid)
    assert tr["state"] == "done" and not tr.get("error") and len(calls) == 3
    assert tr["text"].count("Einmal gesagt.") == 1


def test_only_the_failed_part_is_sent_again(app, client, monkeypatch):
    failed = {"n": 0}

    def answer(name, data):
        if name.startswith("teil-2") and not failed["n"]:
            failed["n"] += 1
            return _R({}, status=502)
        return _R({"segments": [{"start": 0.0, "end": 4.0, "text": f" Text {name[:6]}."}]})

    code, sid, rid, calls = _whisper(app, client, monkeypatch, answer, parts=2)
    _quick(app, monkeypatch)
    client.post(f"/guide/{code}/interviews/{rid}/transcribe")
    tr = _tr(app, rid)
    assert tr["state"] == "done"
    assert [c[0][:6] for c in calls] == ["teil-1", "teil-2", "teil-2"]
    assert tr["text"].count("Text teil-1.") == 1 and tr["text"].count("Text teil-2.") == 1


def test_gives_up_after_the_waits_and_lasting_errors_are_not_repeated(app, client, monkeypatch):
    code, sid, rid, calls = _whisper(app, client, monkeypatch, lambda name, data: _R({}, status=500))
    _quick(app, monkeypatch)
    client.post(f"/guide/{code}/interviews/{rid}/transcribe")
    tr = _tr(app, rid)
    assert tr["state"] == "error" and "500" in tr["error"] and len(calls) == 3  # first try and two waits

    code, sid, rid, calls = _whisper(app, client, monkeypatch, lambda name, data: _R({}, status=413))
    _quick(app, monkeypatch)
    client.post(f"/guide/{code}/interviews/{rid}/transcribe")
    assert _tr(app, rid)["state"] == "error" and len(calls) == 1


def test_a_cancel_during_the_wait_ends_the_run(app, client, monkeypatch):
    code, sid, rid, calls = _whisper(app, client, monkeypatch, lambda name, data: _R({}, status=503))
    _quick(app, monkeypatch, waits=(0.2,))

    def pause(rid_, mine, seconds, key):
        client.post(f"/guide/{code}/interviews/{rid}/transcribe/cancel")
        return real(rid_, mine, seconds, key)

    real = interview_transcribe._pause
    monkeypatch.setattr(interview_transcribe, "_pause", pause)
    client.post(f"/guide/{code}/interviews/{rid}/transcribe")
    tr = _tr(app, rid)
    assert tr["state"] == "error" and tr["error"] == "Abgebrochen." and len(calls) == 1


def test_job_service_marks_failures_of_the_moment(app, tmp_path, monkeypatch):
    app.config.update(WHISPER_JOBS=True, WHISPER_URL="http://whisper:8091", WHISPER_MODEL="small", WHISPER_TIMEOUT=60)
    path = tmp_path / "audio"
    path.write_bytes(b"audio")
    monkeypatch.setattr(whisper_jobs, "_free", lambda: None)
    monkeypatch.setattr(whisper_jobs.time, "sleep", lambda s: None)
    monkeypatch.setattr(whisper_jobs.requests, "post", lambda *a, **k: _R({}, status=503))
    with app.app_context():
        try:
            whisper_jobs.transcribe(path, "de", "")
        except whisper_jobs.TransientError:
            pass
        else:
            raise AssertionError("503 must be a failure of the moment")
        monkeypatch.setattr(whisper_jobs.requests, "post", lambda *a, **k: _R({"id": "a" * 32}, status=202))
        monkeypatch.setattr(whisper_jobs.requests, "get", lambda *a, **k: _R({}, status=404))
        try:
            whisper_jobs.transcribe(path, "de", "")
        except whisper_jobs.TransientError:
            pass
        else:
            raise AssertionError("a job lost with a restart must be tried again")
        monkeypatch.setattr(whisper_jobs.requests, "get", lambda *a, **k: _R({"state": "failed", "error": "memory_limit"}))
        try:
            whisper_jobs.transcribe(path, "de", "")
        except whisper_jobs.TransientError:
            raise AssertionError("the memory limit is no failure of the moment")
        except whisper_jobs.MemoryLimitError:
            pass
