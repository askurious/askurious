"""Interviews kodieren: a page of the coding loads the interviews of the guide once per GET request
(transcript_coding._real_interviews), and coding a passage checks its one interview (transcript_coding.sees) instead
of loading all of them with their transcripts (performance audit 2026-11-11: the overview asked nine times, each click
on "Stelle kodieren" carried the whole project out of the database)."""
from app import interview_data
from tests.interview.test_transcript_coding_review import _code, _setup, _sub


def _counted(monkeypatch):
    calls = []
    real = interview_data.interviews

    def counted(survey, source=None):
        calls.append(source)
        return real(survey, source)

    monkeypatch.setattr(interview_data, "interviews", counted)
    return calls


def test_the_overview_loads_the_interviews_once(app, client, monkeypatch):
    code, pid, rid, base, main, bea = _setup(app, client)
    tired = _sub(client, base, main, "Erschöpfung")
    _code(client, base, rid, "Abends", tired, 29)
    calls = _counted(monkeypatch)
    page = client.get(f"{base}/overview").data.decode()
    assert "Erschöpfung" in page and calls.count("real") == 1, calls


def test_coding_a_passage_does_not_load_every_interview(app, client, monkeypatch):
    code, pid, rid, base, main, bea = _setup(app, client)
    tired = _sub(client, base, main, "Erschöpfung")
    calls = _counted(monkeypatch)
    _code(client, base, rid, "Abends", tired, 29)
    assert "real" not in calls, calls


def test_someone_who_does_not_see_the_interview_cannot_code_it(app, client):
    from app.extensions import db
    from app.models import CodingProject

    code, pid, rid, base, main, bea = _setup(app, client)
    tired = _sub(client, base, main, "Erschöpfung")
    with app.app_context():
        db.session.get(CodingProject, pid).visibility = CodingProject.OWN
        db.session.commit()
    r = bea.post(f"{base}/i/{rid}/codings", json={"start": 0, "end": 6, "cat": tired})
    assert r.status_code == 404
