"""The word counts of the interview search kept in memory go with a deletion (code review 2026-10-08, R5): an
interview deleted on request, the retention period, the survey deleted."""
from app import interview_search, retention
from app.extensions import db
from app.models import Response, Survey

WORDS = ("Geheimalpha", "Geheimbeta")


def _searched(app, client):
    from tests.interview.test_interview import make
    from tests.interview.test_interview_data import start

    code, sid = make(app, client)
    ids = [start(app, client, code, label=f"I{n}") for n in range(2)]
    with app.app_context():
        for n, rid in enumerate(ids):
            row = db.session.get(Response, rid)
            text = f"B: Pflege und {WORDS[n]} #00:00:0{n}-0#"
            row.interview = {**row.interview, "transcript": {"state": "done", "text": text, "draft": text}}
        db.session.commit()
        uid = db.session.get(Survey, sid).uid
    interview_search._counted.clear()
    assert "geheimalpha" in client.get(f"/guide/{code}/interviews/search").get_data(as_text=True)
    assert {k[1] for k in interview_search._counted if k[0] == uid} == set(ids)
    return code, uid, ids


def test_a_deleted_interview_leaves_no_word_counts(app, client):
    code, uid, ids = _searched(app, client)
    client.post(f"/guide/{code}/interviews/{ids[0]}/delete")
    with app.app_context():
        assert db.session.get(Response, ids[0]) is None
    assert not [k for k in interview_search._counted if k[0] == uid]
    page = client.get(f"/guide/{code}/interviews/search").get_data(as_text=True)
    assert "geheimalpha" not in page and "geheimbeta" in page


def test_the_period_answers_drops_the_word_counts(app, client):
    _, uid, _ = _searched(app, client)
    interview_search._counted[("other", 1, "", "x")] = {}
    with app.app_context():
        retention._clean_files(uid, blank_ids=False)
    assert [k[0] for k in interview_search._counted] == ["other"]
