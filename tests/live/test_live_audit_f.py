"""Live votes after the audit of data and computations (2026-10-05, package F): answers on their way while the slide
changes (T-16), one participation per device (T-17), progress in blocks as answered questions (T-18), further words
in the export (T-19), a corrected tap (T-21), quiz countdowns in blocks (T-22), trial runs (T-23), runs filtered in
the database (T-40)."""
import csv
import io
import time

from app.extensions import db
from app.models import Block, Item, Response, Survey
from tests.live.test_live import add, key, make, send, steer



def issued(app):
    """A device id as the server issues it (signed, audit 2026-10-18, BIZ-02)."""
    from app.live import devices

    with app.test_request_context():
        return devices.issue()


def _rows(app, sid):
    with app.app_context():
        return [(r.answers, r.source, dict(r.params or {}), dict(r.layout or {}))
                for r in db.session.scalars(db.select(Response).filter_by(survey_id=sid).order_by(Response.id))]


def _patch_live(app, sid, **changes):
    with app.app_context():
        survey = db.session.get(Survey, sid)
        survey.live = {**survey.live, **changes}
        db.session.commit()


def _live(app, sid):
    with app.app_context():
        return dict(db.session.get(Survey, sid).live)


def _blocks(app, client, title="Blöcke"):
    code, sid, block = make(app, client, title)
    client.post(f"/presenter/{code}/settings", data={"part": "teilnahme", "flow": "blocks"})
    return code, sid, block


# --- T-16 ----------------------------------------------------------------------------------------------------------


def test_an_answer_on_its_way_while_the_slide_changes_is_taken(app, client):
    code, sid, block = make(app, client)
    q1 = add(app, client, sid, block, "single_choice", question="A?", options=["x", "y"])
    q2 = add(app, client, sid, block, "single_choice", question="B?", options=["x", "y"])
    steer(client, code, action="start")
    p = app.test_client()
    assert key(p, code) == f"q-{q1}"
    steer(client, code, action="next")                                       # the presenter moves on while the phone sends
    r = p.post(f"/live/{code}/answer", data={"ref": f"q-{q1}", f"i{q1}": "1"})
    assert r.status_code == 200 and r.json["ok"]
    assert [a for a, *_ in _rows(app, sid)] == [{str(q1): "1"}]
    assert key(p, code) == f"q-{q2}"                                          # and the phone follows the screen


def test_after_the_grace_or_when_closed_the_phone_is_told_it_was_over(app, client):
    code, sid, block = make(app, client)
    q1 = add(app, client, sid, block, "single_choice", question="A?", options=["x", "y"])
    add(app, client, sid, block, "single_choice", question="B?", options=["x", "y"])
    steer(client, code, action="start")
    steer(client, code, action="next")
    left = _live(app, sid)["left"]
    _patch_live(app, sid, left={**left, "at": time.time() - 10})              # more than LEAVE_GRACE seconds ago
    r = app.test_client().post(f"/live/{code}/answer", data={"ref": f"q-{q1}", f"i{q1}": "1"})
    assert r.status_code == 409 and r.json["error"] == "closed"
    steer(client, code, action="prev")
    steer(client, code, action="close")                                       # ended on purpose: no grace
    steer(client, code, action="next")
    r = app.test_client().post(f"/live/{code}/answer", data={"ref": f"q-{q1}", f"i{q1}": "1"})
    assert r.status_code == 409 and _rows(app, sid) == []
    page = app.test_client().get(f"/live/{code}").data.decode()
    assert "war schon vorbei" in page                                         # the phone's note (live.js shows it)


def test_a_page_of_the_block_just_left_is_still_taken(app, client):
    code, sid, first = _blocks(app, client)
    q1 = add(app, client, sid, first, "rating", question="A")
    steer(client, code, action="add_block")
    with app.app_context():
        second = max(b.id for b in db.session.get(Survey, sid).blocks)
    q2 = add(app, client, sid, second, "rating", question="B")
    steer(client, code, action="start")
    p = app.test_client()
    assert key(p, code) == f"p-{first}-0"
    steer(client, code, action="next_block")
    assert p.post(f"/live/{code}/answer", data={"ref": f"p-{first}-0", "page": str(q1), f"i{q1}": "4"}).json["ok"]
    assert key(p, code) == f"p-{second}-0"
    steer(client, code, action="unrelease")                                   # ended on purpose
    r = p.post(f"/live/{code}/answer", data={"ref": f"p-{second}-0", "page": str(q2), f"i{q2}": "4"})
    assert r.status_code == 409 and r.json["error"] == "closed"
    assert [a for a, *_ in _rows(app, sid)] == [{str(q1): 4}]


# --- T-17 ----------------------------------------------------------------------------------------------------------


def test_a_phone_without_its_cookie_stays_one_participation(app, client):
    code, sid, block = make(app, client)
    q1 = add(app, client, sid, block, "single_choice", question="A?", options=["x", "y"])
    q2 = add(app, client, sid, block, "single_choice", question="B?", options=["x", "y"])
    steer(client, code, action="start")
    p = app.test_client()
    page = p.get(f"/live/{code}").data.decode()
    assert f'data-device="' in page
    DEVICE = page.split('data-device="', 1)[1].split('"', 1)[0]               # the id the server issued
    r = send(p, code, {f"i{q1}": "1", "device": DEVICE})
    assert "Expires=" in r.headers.get("Set-Cookie", "")                      # a permanent session
    p.delete_cookie("session")                                                # cookie lost, or the answer never came back
    assert send(p, code, {f"i{q1}": "2", "device": DEVICE}).json["ok"]        # sent again: the same participation
    steer(client, code, action="next")
    assert send(p, code, {f"i{q2}": "2", "device": DEVICE}).json["ok"]
    rows = _rows(app, sid)
    assert len(rows) == 1 and rows[0][0] == {str(q1): "2", str(q2): "2"}
    assert DEVICE not in str(rows[0][2])                                      # kept hashed with the vote, not as sent
    other = app.test_client()                                                 # another phone is another participation
    assert send(other, code, {f"i{q2}": "1", "device": issued(app)}).json["ok"]
    assert len(_rows(app, sid)) == 2


def test_the_session_device_finds_the_participation_whose_answer_was_lost(app, client):
    code, sid, block = make(app, client)
    q1 = add(app, client, sid, block, "single_choice", question="A?", options=["x", "y"])
    steer(client, code, action="start")
    p = app.test_client()
    p.get(f"/live/{code}")                                                    # the session's device id
    assert send(p, code, {f"i{q1}": "1"}).json["ok"]
    with p.session_transaction() as s:                                        # the reply with the participation got lost
        for k in [k for k in s if k.startswith("live:")]:
            del s[k]
    assert send(p, code, {f"i{q1}": "2"}).json["ok"]
    assert [a for a, *_ in _rows(app, sid)] == [{str(q1): "2"}]


# --- T-18 ----------------------------------------------------------------------------------------------------------


def test_a_question_added_to_the_running_block_is_shown_and_nothing_overwritten(app, client):
    code, sid, block = _blocks(app, client)
    q1 = add(app, client, sid, block, "single_choice", question="A?", options=["x", "y"])
    add(app, client, sid, block, "page_break")
    q2 = add(app, client, sid, block, "single_choice", question="B?", options=["x", "y"])
    steer(client, code, action="start")
    p = app.test_client()
    assert p.post(f"/live/{code}/answer", data={"ref": key(p, code), f"i{q1}": "1"}).json["ok"]
    qn = add(app, client, sid, block, "single_choice", question="NEU?", options=["x", "y"])
    with app.app_context():                                                   # the new question on a page of its own, on top
        b = db.session.get(Block, block)
        items = sorted(b.items, key=lambda i: i.position)
        new = next(i for i in items if i.id == qn)
        b.items = [new, Item(type="page_break", code="pbx", config={}, texts={})] + [i for i in items if i.id != qn]
        for n, i in enumerate(b.items):
            i.position = n
        db.session.commit()
    html = p.get(f"/live/{code}?weiter=1").data.decode()
    assert f'name="i{qn}"' in html and f'name="i{q1}"' not in html            # the new one, not the answered one again
    assert p.post(f"/live/{code}/answer", data={"ref": key(p, code), "page": str(qn), f"i{qn}": "2"}).json["ok"]
    assert f'name="i{q2}"' in p.get(f"/live/{code}?weiter=1").data.decode()
    assert p.post(f"/live/{code}/answer", data={"ref": key(p, code), "page": str(q2), f"i{q2}": "1"}).json["ok"]
    assert key(p, code) == "end"
    (answers, _, _, layout), = _rows(app, sid)
    assert answers == {str(q1): "1", str(qn): "2", str(q2): "1"}
    assert sorted(layout["live_done"][str(block)]) == sorted([q1, qn, q2])


def test_a_page_that_changed_under_the_phone_is_not_stored(app, client):
    code, sid, block = _blocks(app, client)
    q1 = add(app, client, sid, block, "rating", question="A")
    add(app, client, sid, block, "page_break")
    q2 = add(app, client, sid, block, "rating", question="B")
    steer(client, code, action="start")
    p = app.test_client()
    r = p.post(f"/live/{code}/answer", data={"ref": f"p-{block}-0", "page": str(q2), f"i{q2}": "3"})
    assert r.status_code == 409 and r.json["error"] == "moved" and _rows(app, sid) == []
    assert q1


def test_older_progress_as_a_number_of_pages_still_counts(app, client):
    code, sid, block = _blocks(app, client)
    q1 = add(app, client, sid, block, "rating", question="A")
    add(app, client, sid, block, "page_break")
    q2 = add(app, client, sid, block, "rating", question="B")
    steer(client, code, action="start")
    p = app.test_client()
    assert p.post(f"/live/{code}/answer", data={"ref": f"p-{block}-0", f"i{q1}": "3"}).json["ok"]
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(survey_id=sid))
        r.layout = {"live_pages": {str(block): 1}}                            # as stored before the audit
        db.session.commit()
    assert key(p, code) == f"p-{block}-1" and f'name="i{q2}"' in p.get(f"/live/{code}?weiter=1").data.decode()


# --- T-19 ----------------------------------------------------------------------------------------------------------


def test_further_words_are_marked_in_export_codebook_and_readme(app, client):
    from app import export

    code, sid, block = make(app, client)
    q1 = add(app, client, sid, block, "single_choice", question="A?", options=["x", "y"])
    q2 = add(app, client, sid, block, "text_input", question="Wort")
    _patch_live(app, sid, multi=True)
    steer(client, code, action="start")
    p = app.test_client()
    send(p, code, {f"i{q1}": "1"})
    steer(client, code, action="next")
    for w in ("eins", "zwei", "drei"):
        assert send(p, code, {f"i{q2}": w}).json["ok"]
    rows = list(csv.DictReader(io.StringIO(client.get(f"/surveys/{sid}/export/data.csv").data.decode("utf-8-sig"))))
    assert [r["weiteres_wort"] for r in rows] == ["0", "1", "1"]
    assert rows[0]["hauptteilnahme"] == "" and rows[1]["hauptteilnahme"] == rows[2]["hauptteilnahme"] == rows[0]["pid"]
    book = client.get(f"/surveys/{sid}/export/codebook.csv").data.decode("utf-8-sig")
    assert "weiteres_wort" in book and "keine weitere Person" in book and "hauptteilnahme" in book
    assert export.is_public_column("weiteres_wort") and not export.is_public_column("hauptteilnahme")
    with app.app_context():
        readme = export.readme_md(db.session.get(Survey, sid))
    assert "Completed participations: 1" in readme and "2 rows of `data.csv` are further words" in readme
    page = client.get(f"/presenter/{code}/present").data.decode()
    assert "1 Teilnahme" in page                                              # the counts of the runs: persons
    from app import results

    with app.app_context():
        assert results.compute(db.session.get(Survey, sid))["n_rows"] == 1      # N of results and methods: persons


# --- T-21 ----------------------------------------------------------------------------------------------------------


def test_a_corrected_tap_on_an_answered_page_of_the_running_block_is_stored(app, client):
    code, sid, block = _blocks(app, client)
    q1 = add(app, client, sid, block, "rating", question="A")
    add(app, client, sid, block, "page_break")
    add(app, client, sid, block, "rating", question="B")
    steer(client, code, action="start")
    p = app.test_client()
    assert p.post(f"/live/{code}/answer", data={"ref": f"p-{block}-0", "page": str(q1), f"i{q1}": "2"}).json["ok"]
    # the second tap, sent by live.js once the first one has come back
    assert p.post(f"/live/{code}/answer", data={"ref": f"p-{block}-0", "page": str(q1), f"i{q1}": "5"}).json["ok"]
    assert [a for a, *_ in _rows(app, sid)] == [{str(q1): 5}] and key(p, code) == f"p-{block}-1"


def test_the_phone_sends_a_tap_made_while_the_first_was_on_its_way():
    import pathlib

    js = (pathlib.Path(__file__).parents[2] / "app" / "static" / "live.js").read_text(encoding="utf-8")
    assert "if (sending) { again = true; return; }" in js and "values() !== sent" in js
    assert 'res.d.error === "closed") { held(stage.dataset.late)' in js        # T-16: "closed" is no success


# --- T-22 ----------------------------------------------------------------------------------------------------------


def _quiz_block(app, client):
    code, sid, block = _blocks(app, client, "Quiz")
    q = add(app, client, sid, block, "single_choice", question="2+2?", options=["3", "4"])
    w = add(app, client, sid, block, "rating", question="Wie schwer?")
    client.post(f"/presenter/{code}/control", data={"action": "quiz_set", "item": q, "on": "1", "correct": "2", "seconds": "20"})
    steer(client, code, action="start")
    return code, sid, block, q, w


def test_a_quiz_question_in_blocks_keeps_its_countdown_and_answer_time(app, client):
    from app import live

    code, sid, block, q, w = _quiz_block(app, client)
    p = app.test_client()
    p.get(f"/live/{code}")                                                    # the page is shown: its clock starts
    left = p.get(f"/live/{code}/state").json["left"]
    assert 18 <= left <= 20
    assert p.post(f"/live/{code}/answer", data={"ref": f"p-{block}-0", f"i{q}": "2", f"i{w}": "3"}).json["ok"]
    (answers, _, _, layout), = _rows(app, sid)
    assert answers == {str(q): "2", str(w): 3} and 0 <= layout["live_times"][str(q)] < 5
    with app.app_context():
        survey = db.session.get(Survey, sid)
        board = live.leaderboard(survey, live.state(survey), fresh=True)
    assert 900 < board[0][2] <= 1000                                          # the faster the more, as question by question


def test_a_quiz_answer_after_the_countdown_does_not_count_in_blocks(app, client):
    code, sid, block, q, w = _quiz_block(app, client)
    _patch_live(app, sid, block_at=time.time() - 60)                          # shown long ago (no page view kept)
    r = app.test_client().post(f"/live/{code}/answer", data={"ref": f"p-{block}-0", f"i{q}": "2", f"i{w}": "3"})
    assert r.json["ok"] and r.json["late"] == [q]
    assert [a for a, *_ in _rows(app, sid)] == [{str(w): 3}]                 # the other answers are kept


# --- T-23 ----------------------------------------------------------------------------------------------------------


def test_a_trial_run_gives_test_participations(app, client):
    from app import export

    code, sid, block = make(app, client)
    q = add(app, client, sid, block, "single_choice", question="A?", options=["x", "y"])
    steer(client, code, action="start")
    send(app.test_client(), code, {f"i{q}": "1"})                            # the presenter tries it with the own phone
    run = _live(app, sid)["run"]["id"]
    st = steer(client, code, action="run_test", run=run, on="1")
    assert st["run"]["test"] is True
    send(app.test_client(), code, {f"i{q}": "2"})
    assert [s for _, s, *_ in _rows(app, sid)] == ["test", "test"]
    assert "Probe" in client.get(f"/presenter/{code}/present").data.decode()
    st = steer(client, code, action="new_run", name="Vorlesung")
    assert not st["run"].get("test") and st["runs"][0]["test"] is True
    send(app.test_client(), code, {f"i{q}": "2"})
    with app.app_context():
        table = export.rows(db.session.get(Survey, sid), False, ("real",))
    assert [r["durchgang"] for r in table] == ["Vorlesung"]                   # results and export "nur echte"
    steer(client, code, action="run_test", run=run)                           # taken back
    assert [s for _, s, *_ in _rows(app, sid)] == ["real", "real", "real"]
    st = steer(client, code, action="new_run", name="Probe 2", test="1")
    assert st["run"]["test"] is True


# --- T-40 ----------------------------------------------------------------------------------------------------------


def test_the_runs_are_read_from_the_database_one_by_one(app, client):
    from sqlalchemy import event

    from app import live

    code, sid, block = make(app, client)
    q = add(app, client, sid, block, "single_choice", question="A?", options=["x", "y"])
    steer(client, code, action="start")
    send(app.test_client(), code, {f"i{q}": "1"})
    first = _live(app, sid)["run"]["id"]
    steer(client, code, action="new_run")
    for _ in range(2):
        send(app.test_client(), code, {f"i{q}": "2"})
    with app.app_context():
        survey = db.session.get(Survey, sid)
        legacy = Response(survey_id=sid, language="de", status=Response.COMPLETED, answers={str(q): "1"}, pid="old",
                          params={})                                         # first run of an older live vote: no run
        db.session.add(legacy)
        db.session.commit()
        statements = []
        listener = lambda conn, cursor, sql, *a: statements.append(sql)
        event.listen(db.engine, "before_cursor_execute", listener)
        try:
            with app.test_request_context():
                now = live.state(survey)["run"]["id"]
                assert len(live._responses(survey, now)) == 2
                assert len(live._responses(survey, first)) == 1
                assert [r.pid for r in live._responses(survey, 0)] == ["old"]
        finally:
            event.remove(db.engine, "before_cursor_execute", listener)
        reads = [s for s in statements if "FROM response" in s]
        assert len(reads) == 3 and all("response.params" in s.split("WHERE")[1] for s in reads)  # the run in the WHERE
    app.config["LIVE_RUN_PARTICIPATIONS"] = 2                                # the cap counted in the database
    r = send(app.test_client(), code, {f"i{q}": "1"})
    assert r.status_code == 200                                              # it reports, refuses nobody (audit 2026-10-06, S5)
    assert client.get(f"/presenter/{code}/result.json").json["current"]["n"] == 3
    assert "Teilnahmen erreicht" in client.get(f"/presenter/{code}/present").data.decode()
