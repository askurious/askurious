"""Storage and mathematical integrity regressions from the fresh audit of 14 October 2026."""
from app.extensions import db
from app.models import Response


def test_failed_final_move_retains_audio_and_sweep_recovers_it(app, client, survey, monkeypatch):
    from app.items import voice
    from tests.interview.test_voice import _voice, _start, _participant, _send, _row
    _voice(app, client)
    url, survey_uid = _start(app, client)
    phone = _participant(app, url)
    def unavailable(*args, **kwargs):
        raise OSError("simulated disk write failure")
    finalize = voice._finalize
    monkeypatch.setattr(voice, "_finalize", unavailable)
    sent = _send(phone, url)
    assert sent.status_code == 302
    rid, pid, answers = _row(app)
    with app.app_context():
        assert db.session.get(Response, rid).status == Response.COMPLETED
        assert answers["2"]["audio"]["file"].startswith("2-")
        assert voice.path(survey_uid, pid, answers["2"]) is not None
        assert not db.session.info.get(voice._WRITE)
        staged = voice.path(survey_uid, pid, answers["2"])
        assert ".pending" in staged
        from pathlib import Path
        original = Path(staged).read_bytes()
        monkeypatch.setattr(voice, "_finalize", finalize)
        voice.run()
        full = voice.path(survey_uid, pid, answers["2"])
        assert ".pending" not in full and Path(full).read_bytes() == original


def test_failed_durable_write_does_not_acknowledge_completed_answer(app, client, survey, monkeypatch):
    import pytest
    from app.items import voice
    from tests.interview.test_voice import _voice, _start, _participant, _send, _row
    _voice(app, client)
    url, _ = _start(app, client)
    phone = _participant(app, url)
    def failed_fsync(*args):
        raise OSError("simulated durable write failure")
    monkeypatch.setattr(voice.os, "fsync", failed_fsync)
    with pytest.raises(OSError, match="durable write failure"):
        _send(phone, url)
    with app.app_context():
        rows = list(db.session.scalars(db.select(Response).filter_by(is_preview=False)))
        assert all(row.status != Response.COMPLETED for row in rows)
        assert all(not row.answers.get("2", {}).get("audio") for row in rows)
