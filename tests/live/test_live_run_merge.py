"""Merging runs of a live vote (Ralf, 2026-10-16): an earlier run goes into another one, its participations, questions
from the audience and "Unklar" taps with it; the exports keep the run each one was given in."""
from app import export
from app.extensions import db
from app.models import BrowserReceipt, LiveFeedback, LiveQuestion, Response, Survey
from tests.live.test_live import add, make, send, steer


def _setup(app, client, title="Zusammen"):
    code, sid, block = make(app, client, title)
    q = add(app, client, sid, block, "rating", question="Wie war es?")
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "qa": "1"})
    steer(client, code, action="start", item=q)
    return code, sid, q


def test_merge_an_earlier_run_into_the_current_one(app, client):
    code, sid, q = _setup(app, client)
    both = app.test_client()                                                    # this phone votes in both runs
    send(both, code, {f"i{q}": "4"})
    send(app.test_client(), code, {f"i{q}": "5"})
    both.post(f"/live/{code}/qa", data={"text": "Frage aus Runde 1"})
    st = steer(client, code, action="new_run", name="Runde 2")
    old, first_name, current = st["runs"][0]["id"], st["runs"][0]["name"], st["run"]["id"]
    with app.app_context():
        db.session.add(LiveFeedback(survey_id=sid, run_id=old, item_id=q, kind="unclear"))
        db.session.add(BrowserReceipt(survey_id=sid, scope=f"live:{old}", key="k1", expires_at=db.func.now()))
        db.session.commit()
    send(both, code, {f"i{q}": "2"})
    page = client.get(f"/presenter/{code}/present").data.decode()
    assert "Endgültig zusammenführen" in page and 'value="run_merge"' in page and f'<option value="{current}">Runde 2' in page
    r = client.post(f"/presenter/{code}/control", data={"action": "run_merge", "run": old, "into": current})
    assert r.headers["Location"].endswith(f"/presenter/{code}/present")
    with app.app_context():
        survey = db.session.get(Survey, sid)
        assert survey.live["runs"] == [] and survey.live["run"]["id"] == current
        rows = db.session.scalars(db.select(Response).filter_by(survey_id=sid).order_by(Response.id)).all()
        assert [r.params["live_run"] for r in rows] == [current] * 3                    # both participations of the phone stay
        table = export.rows(survey, False, ("real",))
        assert [(t["durchgang"], t["durchgang_ursprung"], t["durchgang_ursprung_id"]) for t in table] == [
            ("Runde 2", first_name, old), ("Runde 2", first_name, old), ("Runde 2", "Runde 2", current)]
        question = db.session.scalar(db.select(LiveQuestion).filter_by(survey_id=sid))
        assert (question.run_id, question.origin_run_id) == (current, old)
        assert db.session.scalar(db.select(LiveFeedback.run_id).filter_by(survey_id=sid)) == current
        assert db.session.scalar(db.select(BrowserReceipt.scope).filter_by(survey_id=sid, key="k1")) == f"live:{current}"
    assert client.get(f"/presenter/{code}/result.json").json["current"] is not None
    csv = client.get(f"/presenter/{code}/audience-questions.csv").data.decode("utf-8-sig")
    assert f"Runde 2;{first_name};Frage aus Runde 1;" in csv


def test_merging_twice_keeps_the_first_run(app, client):
    code, sid, q = _setup(app, client, "Dreimal")
    send(app.test_client(), code, {f"i{q}": "4"})
    a = steer(client, code, action="new_run", name="B")["runs"][0]
    send(app.test_client(), code, {f"i{q}": "3"})
    b = steer(client, code, action="new_run", name="C")["runs"][1]
    client.post(f"/presenter/{code}/control", data={"action": "run_merge", "run": a["id"], "into": b["id"]})
    client.post(f"/presenter/{code}/control", data={"action": "run_merge", "run": b["id"], "into": steer(client, code, action="screen", item=q)["run"]["id"]})
    with app.app_context():
        table = export.rows(db.session.get(Survey, sid), False, ("real",))
    assert [(t["durchgang"], t["durchgang_ursprung"]) for t in table] == [("C", a["name"]), ("C", "B")]


def test_merge_needs_admin_and_the_same_kind_of_run(app, client):
    code, sid, q = _setup(app, client, "Gleich")
    send(app.test_client(), code, {f"i{q}": "4"})
    trial = steer(client, code, action="new_run", name="Echt")
    old, current = trial["runs"][0]["id"], trial["run"]["id"]
    steer(client, code, action="run_test", run=old, on="1")                     # a trial and a real run: no merge
    assert "run_merge" not in client.get(f"/presenter/{code}/present").data.decode()
    client.post(f"/presenter/{code}/control", data={"action": "run_merge", "run": old, "into": current})
    client.post(f"/presenter/{code}/control", data={"action": "run_merge", "run": current, "into": old})  # the current one stays
    with app.app_context():
        live = db.session.get(Survey, sid).live
        assert [r["id"] for r in live["runs"]] == [old] and live["run"]["id"] == current


def test_editors_do_not_merge(app, client):
    from tests.live.test_live_role_limits import _member

    code, sid, q = _setup(app, client, "Rechte")
    st = steer(client, code, action="new_run", name="Zwei")
    editor = _member(app, sid, "editor")
    assert "run_merge" not in editor.get(f"/presenter/{code}/present").data.decode()
    editor.post(f"/presenter/{code}/control", data={"action": "run_merge", "run": st["runs"][0]["id"], "into": st["run"]["id"]})
    with app.app_context():
        assert len(db.session.get(Survey, sid).live["runs"]) == 1
