"""A-04 and K-12 (tester 2026-11-09): a restart ended the process while a transcription ran. The run went on, every
further part failed at once with "cannot schedule new futures after interpreter shutdown", and the interview ended
as an error with a long list of parts, which housekeeping never took up again. Now the run stops without an error
and is resumed after the restart; such old errors are taken up once; parts with the same reason are named together."""
from datetime import timedelta

from app import interview_transcribe
from app.extensions import db
from app.models import Response, utcnow
from tests.interview.test_interview_robust import _R, _tr, _whisper


def test_a_shutdown_leaves_the_run_running_for_resume(app, client, monkeypatch):
    """The first part is done, then the process ends: no error, the part stays, the run is still "running"."""
    def answer(name, data):
        if len(calls) == 2:
            monkeypatch.setattr(interview_transcribe, "_shutting_down", lambda: True)
        return _R({"segments": [{"start": 0.0, "end": 4.0, "text": " Teil fertig."}]})

    code, sid, rid, calls = _whisper(app, client, monkeypatch, answer, parts=3)
    client.post(f"/guide/{code}/interviews/{rid}/transcribe")
    tr = _tr(app, rid)
    assert tr["state"] == "running" and not tr.get("error")
    assert set(tr["parts"]) == {"1", "2"}     # a result that arrived is kept
    assert len(calls) == 2                    # part 3 is not sent by the ending process; the resume sends it


def test_a_shutdown_error_of_a_part_is_no_failure(app, client, monkeypatch):
    """The request itself fails as the process ends (the message the testers saw): not stored as the part's error."""
    def answer(name, data):
        monkeypatch.setattr(interview_transcribe, "_shutting_down", lambda: True)
        raise RuntimeError("cannot schedule new futures after interpreter shutdown")

    code, sid, rid, calls = _whisper(app, client, monkeypatch, answer, parts=2)
    client.post(f"/guide/{code}/interviews/{rid}/transcribe")
    tr = _tr(app, rid)
    assert tr["state"] == "running" and not tr.get("error") and len(calls) == 1


def test_an_old_shutdown_error_is_taken_up_once(app, client, monkeypatch):
    code, sid, rid, calls = _whisper(app, client, monkeypatch, parts=2)
    old = (utcnow() - timedelta(hours=12)).isoformat(timespec="seconds")
    with app.app_context():
        row = db.session.get(Response, rid)
        interview_transcribe._set(row, state="error", started=old, beat=old, run="", resumed=interview_transcribe.RESUME_TRIES,
                                  error="Nicht transkribiert: Teil 2: cannot schedule new futures after interpreter shutdown. "
                                        "Die übrigen Teile sind fertig.", parts={"1": {"bytes": 9, "text": "Eins. #00:00:04-0#"}})
        db.session.commit()
        assert interview_transcribe.resume_stale() == 1
    tr = _tr(app, rid)
    assert tr["state"] == "done" and not tr.get("error")
    assert [c[0] for c in calls] == [calls[0][0]] and len(calls) == 1   # only the missing part was sent
    with app.app_context():
        assert interview_transcribe.resume_stale() == 0


def test_other_errors_are_not_taken_up(app, client, monkeypatch):
    code, sid, rid, calls = _whisper(app, client, monkeypatch, parts=1)
    with app.app_context():
        row = db.session.get(Response, rid)
        interview_transcribe._set(row, state="error", run="", error="Der Whisper-Server antwortet mit 400.")
        db.session.commit()
        assert interview_transcribe.resume_stale() == 0


def test_parts_with_the_same_reason_are_named_together():
    failed = [(str(n), "Der Dienst antwortet nicht.") for n in (3, 4, 5, 6, 9)] + [("7", "")]
    text = str(interview_transcribe._PartsFailed(failed))
    assert text == ("Nicht transkribiert: Teile 3–6, 9: Der Dienst antwortet nicht; Teil 7. "
                    "Die übrigen Teile sind fertig.")
    assert interview_transcribe._ranges(["1", "2"]) == "1, 2"
    assert interview_transcribe._ranges(["a", "b"]) == "a, b"
