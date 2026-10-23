"""Transcripts that can be found (pre-test 2026-10-16): parts done before an error can be read, the state and the
link stand in the row instead of the folded recording box, and housekeeping takes up a transcription whose web
process ended."""
from datetime import timedelta

from app.extensions import db
from app.models import Response, utcnow
from tests.interview.test_interview_robust import _R, _tr, _whisper


def test_parts_done_before_an_error_can_be_read(app, client, monkeypatch):
    def answer(name, data):
        if name.startswith("teil-2"):
            return _R({}, status=500)
        return _R({"segments": [{"start": 0.0, "end": 3.0, "text": " Aus eins."}]})

    code, sid, rid, calls = _whisper(app, client, monkeypatch, answer, parts=2)
    client.post(f"/guide/{code}/interviews/{rid}/transcribe")
    tr = _tr(app, rid)
    assert tr["state"] == "error" and tr["partial"] is True
    assert tr["text"] == "Aus eins. #00:00:03-0#\n\n[Teil 2 fehlt]" and sorted(tr["parts"]) == ["1"]
    page = client.get(f"/guide/{code}/interviews").data.decode()
    row = page.split("</details>", 1)[1]                       # after the recording box, not folded inside it
    assert "Transkript prüfen" in row and "unvollständig" in row and "Weiter transkribieren" in row
    calls.clear()
    _whisper_ok = {"segments": [{"start": 0.0, "end": 3.0, "text": " Aus zwei."}]}
    from app import interview_transcribe

    monkeypatch.setattr(interview_transcribe.requests, "post",
                        lambda url, **kw: calls.append(kw["files"]["file"][0]) or _R(_whisper_ok))
    client.post(f"/guide/{code}/interviews/{rid}/transcribe")
    tr = _tr(app, rid)
    assert calls == ["teil-2-r2.webm"] and tr["state"] == "done" and "partial" not in tr
    assert tr["text"] == "Aus eins. #00:00:03-0#\n\nAus zwei. #00:10:03-0#"


def test_a_corrected_text_stays_when_a_later_part_fails(app, client, monkeypatch):
    from app import interview_transcribe

    code, sid, rid, calls = _whisper(app, client, monkeypatch, lambda name, data: _R({}, status=500))
    with app.app_context():
        row = db.session.get(Response, rid)
        interview_transcribe._set(row, text="I: Von Hand.", edited=True, parts={"1": {"bytes": 9, "text": "x"}})
        db.session.commit()
    client.post(f"/guide/{code}/interviews/{rid}/transcribe")
    tr = _tr(app, rid)
    assert tr["text"] == "I: Von Hand." and not tr.get("partial")


def test_housekeeping_takes_up_a_broken_off_transcription(app, client, monkeypatch):
    from app import interview_transcribe

    code, sid, rid, calls = _whisper(app, client, monkeypatch)
    old = (utcnow() - timedelta(minutes=10)).isoformat(timespec="seconds")
    with app.app_context():
        row = db.session.get(Response, rid)
        interview_transcribe._set(row, state="running", started=old, beat=old, run="weg")
        db.session.commit()
        assert interview_transcribe.resume_stale() == 1
    tr = _tr(app, rid)
    assert len(calls) == 1 and tr["state"] == "done" and tr["text"] == "Whisper-Entwurf. #00:00:04-0#"
    assert "resumed" not in tr
    with app.app_context():                                     # a fresh sign of life: left alone
        row = db.session.get(Response, rid)
        now = utcnow().isoformat(timespec="seconds")
        interview_transcribe._set(row, state="running", started=now, beat=now, run="läuft")
        db.session.commit()
        assert interview_transcribe.resume_stale() == 0


def test_housekeeping_gives_up_after_three_tries(app, client, monkeypatch):
    from app import interview_transcribe

    code, sid, rid, calls = _whisper(app, client, monkeypatch)
    old = (utcnow() - timedelta(minutes=10)).isoformat(timespec="seconds")
    with app.app_context():
        row = db.session.get(Response, rid)
        interview_transcribe._set(row, state="running", started=old, beat=old, run="weg",
                                  resumed=interview_transcribe.RESUME_TRIES)
        db.session.commit()
        assert interview_transcribe.resume_stale() == 0
    tr = _tr(app, rid)
    assert calls == [] and tr["state"] == "error" and tr["error_code"] == "interrupted"
    assert "mehrmals unterbrochen" in client.get(f"/guide/{code}/interviews").data.decode()


def test_housekeeping_runs_the_step():
    import inspect

    from app import housekeeping

    assert "interview_transcribe.resume_stale" in inspect.getsource(housekeeping.main)
