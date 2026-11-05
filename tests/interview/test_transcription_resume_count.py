"""Tester 2026-11-05 (interview S-04): the researchers' container renews its workers (--max-requests) and the
transcription's thread ends with its worker while WhisperX computes on. Such a break is no longer counted against
RESUME_TRIES, and the list says "wird fortgesetzt" and keeps asking instead of "beendet oder unterbrochen"."""
from datetime import timedelta

from app import interview_transcribe, whisper_jobs
from app.extensions import db
from app.models import Response, utcnow
from tests.interview.test_interview import make
from tests.interview.test_interview_data import start


class _Reply:
    def __init__(self, state, status=200):
        self.status_code, self._state = status, state

    def json(self):
        return {"state": self._state}


def _stale(app, client, minutes=3, **values):
    code, _ = make(app, client)
    rid = start(app, client, code)
    old = (utcnow() - timedelta(minutes=minutes)).isoformat(timespec="seconds")
    with app.app_context():
        row = db.session.get(Response, rid)
        interview_transcribe._set(row, state="running", started=old, beat=old, run="weg", **values)
        db.session.commit()
    return code, rid


def test_a_break_while_the_service_computes_is_not_counted(app, client, monkeypatch):
    app.config.update(WHISPER_JOBS=True, WHISPER_URL="http://whisper:8091", WHISPER_TOKEN="t")
    with app.app_context():
        record = {"id": "a" * 32, "service": whisper_jobs.namespace()}
    code, rid = _stale(app, client, resumed=interview_transcribe.RESUME_TRIES, jobs={"1": record})
    started = []
    monkeypatch.setattr(interview_transcribe, "start", lambda survey, row, **kw: started.append(kw["resumed"]))
    monkeypatch.setattr(interview_transcribe.requests, "get", lambda *a, **kw: _Reply("running"))
    with app.app_context():
        assert interview_transcribe.resume_stale() == 1
    assert started == [interview_transcribe.RESUME_TRIES]
    monkeypatch.setattr(interview_transcribe.requests, "get", lambda *a, **kw: _Reply("", status=404))
    with app.app_context():                                     # the job is gone: counted, and given up as before
        assert interview_transcribe.resume_stale() == 0
        assert interview_transcribe.state(db.session.get(Response, rid))["error_code"] == "interrupted"


def test_the_list_shows_a_break_as_being_resumed(app, client):
    code, rid = _stale(app, client, minutes=3)
    with app.app_context():
        row = db.session.get(Response, rid)
        info = interview_transcribe.progress_info(row)
        assert not interview_transcribe.running(row) and interview_transcribe.shown_running(row)
    assert info["active"] and info["text"] == "Transkription wird fortgesetzt …"
    code, rid = _stale(app, client, minutes=30)                  # long gone: shown as ended, may be started again
    with app.app_context():
        info = interview_transcribe.progress_info(db.session.get(Response, rid))
    assert not info["active"]


def test_the_bar_moves_within_a_part(app, client, monkeypatch):
    """Ralf 2026-11-05: "0 von 2 Teilen fertig" stood still for an hour while the first part was being diarised."""
    from tests.interview.test_interview_robust import _whisper

    code, sid, rid, calls = _whisper(app, client, monkeypatch, parts=2)
    now = utcnow().isoformat(timespec="seconds")
    with app.app_context():
        row = db.session.get(Response, rid)
        interview_transcribe._set(row, state="running", started=now, beat=now, run="r", progress={"stage": "diarizing", "part": "1"})
        db.session.commit()
        info = interview_transcribe.progress_info(row)
    assert (info["done"], info["total"]) == (0, 2) and info["bar"] == interview_transcribe.STAGE_SHARE["diarizing"]


def test_the_bar_moves_evenly_by_the_estimate(app, client, monkeypatch):
    """Ralf 2026-11-05: not only in the steps; from the part's start to the estimated end, never full before done."""
    import time

    from tests.interview.test_interview_robust import _whisper

    code, sid, rid, calls = _whisper(app, client, monkeypatch, parts=2)
    now = int(time.time())

    def info(since, until, stage="transcribing"):
        beat = utcnow().isoformat(timespec="seconds")
        with app.app_context():
            row = db.session.get(Response, rid)
            interview_transcribe._set(row, state="running", started=beat, beat=beat, run="r",
                                      progress={"stage": stage, "part": "1", "since": since, "eta_until": until})
            db.session.commit()
            return interview_transcribe.progress_info(row)

    assert abs(info(now - 600, now + 600)["bar"] - 0.5) < 0.02          # half the estimated time
    assert info(now - 6000, now - 10)["bar"] == interview_transcribe.BAR_CAP  # overdue: nearly full, not full
    assert info(now - 10, now + 6000, "diarizing")["bar"] == interview_transcribe.STAGE_SHARE["diarizing"]
