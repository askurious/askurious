"""Live votes after the audit of 2026-10-23: the same quiz answer sent again keeps its answer time (DS-05), and what a
phone sends from a page of an earlier run is refused instead of landing in the new run (DS-06)."""
import pathlib
import re
from types import SimpleNamespace

from app.extensions import db
from app.models import LiveFeedback, LiveQuestion, Response, Survey
from tests.live.test_live import add, make, steer


def _clock(monkeypatch, start=500.0):
    """The live modules' time, set by the test."""
    import time

    from app import live

    now = [start]
    clock = SimpleNamespace(time=lambda: now[0], monotonic=time.monotonic)
    for module in (live.base, live.control, live.audience, live.screen, live.scores):
        monkeypatch.setattr(module, "time", clock)
    return now


def _layouts(app, sid):
    with app.app_context():
        return [(dict(r.answers or {}), dict(r.layout or {}), dict(r.params or {}))
                for r in db.session.scalars(db.select(Response).filter_by(survey_id=sid).order_by(Response.id))]


def _run_of(p, code):
    """The run the phone's page was drawn for (data-live-run)."""
    return re.search(r'data-live-run="([^"]*)"', p.get(f"/live/{code}").data.decode()).group(1)


# --- DS-05 ---------------------------------------------------------------------------------------------------------


def test_a_quiz_answer_sent_again_keeps_its_time_a_changed_one_takes_the_new(app, client, monkeypatch):
    """audit 2026-10-23, DS-05: a retry of the same answer overwrote the answer time and lowered the points."""
    now = _clock(monkeypatch)
    code, sid, block = make(app, client, "Quiz Zeit")
    q = add(app, client, sid, block, "single_choice", question="Hauptstadt?", options=["Berlin", "Bonn"])
    client.post(f"/presenter/{code}/control", data={"action": "quiz_set", "item": q, "on": "1", "correct": "1", "seconds": "30"})
    steer(client, code, action="start")
    p = app.test_client()
    now[0] += 5
    assert p.post(f"/live/{code}/answer", data={"ref": f"q-{q}", f"i{q}": "1"}).json["ok"]
    now[0] += 5
    assert p.post(f"/live/{code}/answer", data={"ref": f"q-{q}", f"i{q}": "1"}).json["ok"]   # the same answer again
    (answers, layout, _), = _layouts(app, sid)
    assert answers == {str(q): "1"} and layout["live_times"][str(q)] == 5
    now[0] += 2
    assert p.post(f"/live/{code}/answer", data={"ref": f"q-{q}", f"i{q}": "2"}).json["ok"]   # changed on purpose
    (answers, layout, _), = _layouts(app, sid)
    assert answers == {str(q): "2"} and layout["live_times"][str(q)] == 12


def test_a_quiz_page_in_blocks_sent_again_keeps_its_time(app, client, monkeypatch):
    """audit 2026-10-23, DS-05: the same in the flow "In Blöcken" (a page answered already, sent once more)."""
    now = _clock(monkeypatch)
    code, sid, block = make(app, client, "Quiz Blöcke Zeit")
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "flow": "blocks"})
    q = add(app, client, sid, block, "single_choice", question="2+2?", options=["3", "4"])
    w = add(app, client, sid, block, "rating", question="Wie schwer?")
    client.post(f"/presenter/{code}/control", data={"action": "quiz_set", "item": q, "on": "1", "correct": "2", "seconds": "20"})
    steer(client, code, action="start")
    p = app.test_client()
    p.get(f"/live/{code}")
    page = f"{q},{w}"
    now[0] += 3
    assert p.post(f"/live/{code}/answer", data={"ref": f"p-{block}-0", "page": page, f"i{q}": "2", f"i{w}": "3"}).json["ok"]
    now[0] += 4
    assert p.post(f"/live/{code}/answer", data={"ref": f"p-{block}-0", "page": page, f"i{q}": "2", f"i{w}": "4"}).json["ok"]
    (answers, layout, _), = _layouts(app, sid)
    assert answers == {str(q): "2", str(w): 4} and layout["live_times"][str(q)] == 3
    now[0] += 1
    assert p.post(f"/live/{code}/answer", data={"ref": f"p-{block}-0", "page": page, f"i{q}": "1", f"i{w}": "4"}).json["ok"]
    (answers, layout, _), = _layouts(app, sid)
    assert answers[str(q)] == "1" and layout["live_times"][str(q)] == 8


# --- DS-06 ---------------------------------------------------------------------------------------------------------


def test_an_answer_from_the_page_of_an_earlier_run_is_refused(app, client):
    """audit 2026-10-23, DS-06: after "Neuer Durchgang" a POST from the old page landed in the new run."""
    code, sid, block = make(app, client, "Durchgang alt")
    q = add(app, client, sid, block, "single_choice", question="A?", options=["x", "y"])
    steer(client, code, action="start")
    p = app.test_client()
    old = _run_of(p, code)
    assert p.post(f"/live/{code}/answer", data={"ref": f"q-{q}", "run": old, f"i{q}": "1"}).json["ok"]
    steer(client, code, action="new_run", name="Seminar 2")
    steer(client, code, action="screen", item=q)
    r = p.post(f"/live/{code}/answer", data={"ref": f"q-{q}", "run": old, f"i{q}": "2"})
    assert r.status_code == 409 and r.json == {"ok": False, "error": "stale", "run": True}
    rows = _layouts(app, sid)
    assert [a for a, _, _ in rows] == [{str(q): "1"}]                        # the old run's answer is as it was
    new = _run_of(p, code)
    assert new != old
    assert p.post(f"/live/{code}/answer", data={"ref": f"q-{q}", "run": new, f"i{q}": "2"}).json["ok"]
    assert [a for a, _, _ in _layouts(app, sid)] == [{str(q): "1"}, {str(q): "2"}]


def test_after_alles_zurueck_the_old_page_is_refused_too(app, client):
    """audit 2026-10-23, DS-06: "Alles zurück" gives the run a new id; an answer from before it is not taken."""
    code, sid, block = make(app, client, "Zurück alt")
    q = add(app, client, sid, block, "single_choice", question="A?", options=["x", "y"])
    steer(client, code, action="start")
    p = app.test_client()
    old = _run_of(p, code)
    steer(client, code, action="restart")
    r = p.post(f"/live/{code}/answer", data={"ref": f"q-{q}", "run": old, f"i{q}": "1"})
    assert r.status_code == 409 and r.json["error"] == "stale"
    assert _layouts(app, sid) == []


def test_a_page_in_blocks_from_an_earlier_run_is_refused(app, client):
    """audit 2026-10-23, DS-06: the flow "In Blöcken" goes through the same check."""
    code, sid, block = make(app, client, "Blöcke alt")
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "flow": "blocks"})
    q = add(app, client, sid, block, "single_choice", question="A?", options=["x", "y"])
    steer(client, code, action="start")
    p = app.test_client()
    old = _run_of(p, code)
    steer(client, code, action="new_run", name="Seminar 2")
    steer(client, code, action="start")
    r = p.post(f"/live/{code}/answer", data={"ref": f"p-{block}-0", "run": old, f"i{q}": "1"})
    assert r.status_code == 409 and r.json["error"] == "stale"
    assert _layouts(app, sid) == []


def test_questions_votes_reactions_and_names_from_an_old_page_are_refused(app, client):
    """audit 2026-10-23, DS-06: Q&A, votes, reactions and the nickname are kept per run as well."""
    code, sid, block = make(app, client, "Fragen alt")
    add(app, client, sid, block, "rating", question="Wie war es?")
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "qa": "1"})
    steer(client, code, action="start")
    p = app.test_client()
    old = _run_of(p, code)
    assert p.post(f"/live/{code}/qa", data={"text": "Erste Frage?", "run": old}).json["ok"]
    steer(client, code, action="new_run", name="Seminar 2")
    with app.app_context():
        before = db.session.scalar(db.select(db.func.count(LiveQuestion.id)))
        qid = db.session.scalar(db.select(LiveQuestion.id))
        feedback = db.session.scalar(db.select(db.func.count(LiveFeedback.id)))
    for url, data in ((f"/live/{code}/qa", {"text": "Noch eine?"}), (f"/live/{code}/qa/{qid}/vote", {}),
                      (f"/live/{code}/reaction", {"e": "👍"}), (f"/live/{code}/name", {"name": "Anna"}),
                      (f"/live/{code}/withdraw", {"ref": "q-0", "word": "1"})):
        r = p.post(url, data={**data, "run": old})
        assert r.status_code == 409 and r.json["error"] == "stale" and r.json["run"], url
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(LiveQuestion.id))) == before
        assert db.session.scalar(db.select(db.func.count(LiveFeedback.id))) == feedback
    new = _run_of(p, code)
    assert p.post(f"/live/{code}/qa", data={"text": "Noch eine?", "run": new}).json["ok"]


def test_the_phone_sends_its_run_and_loads_anew_when_refused():
    """audit 2026-10-23, DS-06: live.js sends the run of its page with every POST and reloads on 409 "stale" + run."""
    js = (pathlib.Path(__file__).parents[2] / "app" / "static" / "live.js").read_text(encoding="utf-8")
    assert 'dataset.liveRun' in js and 'body.append("run", RUN)' in js
    assert 'd.error !== "stale" || !d.run' in js and "location.replace(location.pathname)" in js
    assert js.count("withRun(") >= 6                       # answer, word withdrawn, nickname, Q&A (ask, vote), reactions
    assert "if (newRun(res.d)) return;" in js
    join = (pathlib.Path(__file__).parents[2] / "app" / "templates" / "live" / "join.html").read_text(encoding="utf-8")
    assert 'data-live-run="{{ run }}"' in join
