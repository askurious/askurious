"""Second code review 2026-10-03, package 3 "Teilnahme und Live": R-1 to R-4, R-8 to R-11, A-1, A-4, A-6, A-H1, A-H3,
A-H4 (code-review-2026-10-03.md)."""
import json
import random
import re
import secrets
from datetime import timedelta

import pytest
from sqlalchemy import event
from sqlalchemy.orm.exc import StaleDataError

from app import balance, duration, flow, loops, runtime
from app.extensions import db
from app.models import Block, Response, Survey, utcnow
from tests.live import test_live
from tests.participation import test_participate
from tests.quotas import test_quotas
from tests.conftest import CONSENT_OK, add, fill_consent


def _rows(app):
    with app.app_context():
        return db.session.scalar(db.select(db.func.count(Response.id)))


# --- R-1: the first page sent again after its answer was lost ------------------------------------------------------


def _started(app, client):
    test_participate.build(client)
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    return f"/s/{test_participate.uid(app)}"


def test_first_page_sent_again_with_the_old_cookie_goes_on(app, client, survey):
    url = _started(app, client)
    p = app.test_client()
    p.get(url)
    old = p.get_cookie("session").value
    assert p.post(url, data=CONSENT_OK).status_code in (302, 303)   # stored; the answer never reaches the phone
    p.set_cookie("session", old)                                     # the browser still has the cookie from before
    again = p.post(url, data=CONSENT_OK)
    assert again.status_code == 303 and again.headers["Location"].endswith(url)
    assert _rows(app) == 1
    assert "Alter" in p.get(url).data.decode()                       # on with the stored participation (page 2)
    assert p.post(url, data=CONSENT_OK).status_code < 500            # an old page once more: the current page
    assert _rows(app) == 1


def test_first_page_stored_by_a_request_at_the_same_moment(app, client, survey, monkeypatch):
    from app import participate

    url = _started(app, client)
    p = app.test_client()
    p.get(url)
    old = p.get_cookie("session").value
    p.post(url, data=CONSENT_OK)
    p.set_cookie("session", old)
    real, calls = participate._stored_first_page, []

    def not_yet(survey, pending):  # the other request had not stored it when this one looked
        calls.append(1)
        return None if len(calls) == 1 else real(survey, pending)

    monkeypatch.setattr(participate, "_stored_first_page", not_yet)
    again = p.post(url, data=CONSENT_OK)                             # the insert meets the unique pid
    assert again.status_code == 303 and len(calls) == 2 and _rows(app) == 1
    assert "Alter" in p.get(url).data.decode()


# --- R-2: the session cookie with large loop tables, and the keys of ended participations --------------------------


def _big_loop(app, client, n_rows, k):
    fill_consent(client)
    add(client, "text_input")
    client.post("/surveys/1/editor/items/2", data={"code": "scr", "question": "Screen {{x}}"})
    rows = [{"id": "r" + secrets.token_hex(3), "values": {"c1": {"de": f"v{n}"}}} for n in range(n_rows)]
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1},
                  {"t": "loop", "id": "l1", "name": "S", "code": "loop1", "columns": [{"id": "c1", "name": "x", "hidden": False}],
                   "rows": rows, "mode": "k", "k": k, "order": "random", "balanced": True,
                   "children": [{"t": "block", "block": 2}]}]}
        flow.normalize(s)
        s.status, s.opened_at = Survey.RUNNING, s.created_at
        db.session.commit()
        rng = random.Random(1)
        for n in range(40):
            pick = rng.sample([r["id"] for r in rows], len(rows) // 2)
            db.session.add(Response(survey_id=1, language="de", answers={}, source="real", is_preview=False, pid=f"x{n}",
                                    layout={"pages": [], "loops": {"l1": pick}, "conditions": {}, "cj": {}}))
        db.session.commit()
        return s.code


def test_a_large_loop_table_fits_the_session_cookie(app, client, survey):
    uid = _big_loop(app, client, 600, 1)
    p = app.test_client()
    p.get(f"/s/{uid}?lang=de")
    assert len(p.get_cookie("session").value) < 4000
    p.post(f"/s/{uid}", data=CONSENT_OK)
    assert _rows(app) == 41                                          # the first "Next" found its pending participation


def test_the_kept_counts_of_a_loop_give_the_same_draw(app):
    """keep() keeps only the counts of the picked rows and one count for the rest: the same rows in the same order."""
    ids = [f"r{n}" for n in range(60)]
    node = {"t": "loop", "id": "l1", "name": "S", "code": "loop1", "columns": [{"id": "c1", "name": "x", "hidden": False}],
            "rows": [{"id": i, "values": {}} for i in ids], "mode": "k", "k": 7, "order": "random", "balanced": True}
    s = Survey(id=5)
    gen = random.Random(3)
    with app.test_request_context():
        for seed in range(40):
            full = {"loop": {"l1": {i: gen.choice([0, 0, 1, 2, 3, 5]) for i in ids if gen.random() < 0.8}}}
            with balance.frozen(s, json.loads(json.dumps(full))):
                first = loops.choose_rows(s, node, random.Random(seed))
            table = {"rand": {}, "loop": full["loop"], "version": {}}
            kept = json.loads(json.dumps(balance.keep(table, {"loops": {"l1": first}})))
            assert len(kept["loop"]["l1"][0]) == 7
            with balance.frozen(s, kept):
                assert loops.choose_rows(s, node, random.Random(seed)) == first


def test_the_ended_participations_of_other_surveys_are_forgotten(app, client, survey):
    from app import participate

    url = _started(app, client)
    with app.app_context():
        for n in range(15):
            db.session.add(Response(survey_id=1, language="de", answers={}, source="real", is_preview=False, pid=f"e{n}",
                                    status=Response.COMPLETED if n else Response.ACTIVE))
        db.session.commit()
        ids = [r.id for r in db.session.scalars(db.select(Response).order_by(Response.id))]
    p = app.test_client()
    with p.session_transaction() as sess:  # what other surveys left in this browser (survey ids 100 and on)
        for n, rid in enumerate(ids):
            sess[f"r{100 + n}"], sess[f"rp{100 + n}"], sess[f"pp{100 + n}"] = rid, f"e{n}", {"src": "x"}
    p.get(url)
    p.post(url, data=CONSENT_OK)
    with p.session_transaction() as sess:
        left = sorted(int(k[1:]) for k in sess if re.fullmatch(r"r\d+", k))
    # the active one (100) and the newest KEEP_ENDED ended ones stay, and this survey's own
    assert left == [1, 100] + list(range(115 - participate.KEEP_ENDED, 115))
    with p.session_transaction() as sess:
        assert "rp101" not in sess and "pp101" not in sess and "rp114" in sess


def test_the_design_check_warns_of_a_large_balanced_draw(app, client, survey):
    _big_loop(app, client, 600, loops.BALANCED_PICK_MAX + 1)
    with app.app_context():
        found = [m for _, m, _, _ in loops.check(db.session.get(Survey, 1))]
    assert any("Sitzungscookie" in m for m in found), found


# --- R-3: an end "Quote voll" at the top level in the estimate of the duration -------------------------------------


def test_the_duration_goes_on_past_an_end_for_full_quotas(app, client, survey):
    fill_consent(client)
    for _ in range(5):
        add(client, "text_input")
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1}, {"t": "block", "block": 2}]}
        flow.normalize(s)
        db.session.commit()
        plain = duration.estimate(s)
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1},
                  {"t": "end", "id": "qe1", "kind": "quotafull", "quotas": [99], "rule": "any", "text": {}},
                  {"t": "block", "block": 2}]}
        quota_end = duration.estimate(s)
        assert quota_end["typical"] == pytest.approx(plain["typical"]) and quota_end["high"] == pytest.approx(plain["high"])
        assert quota_end["low"] < plain["low"]                       # who is turned away there
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1},
                  {"t": "end", "id": "so1", "kind": "screenout", "text": {}}, {"t": "block", "block": 2}]}
        assert duration.estimate(s)["typical"] < plain["typical"] / 2  # an end for all stops the estimate


# --- R-4: the times of the first page when it is not the first entry of the layout ---------------------------------


def _first_page(app, client, children, **settings):
    fill_consent(client)
    add(client, "text_input")
    client.post("/surveys/1/editor/items/2", data={"code": "scr", "question": "Screen", "answer_mode": "required"})
    client.post("/surveys/1/panel", data={"param_name": "src", "param_mode": "store", "param_required": "0"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": children}
        flow.normalize(s)
        for k, v in settings.items():
            setattr(s, k, v)
        s.status, s.opened_at = Survey.RUNNING, s.created_at
        db.session.commit()
        return s.code


def test_the_page_time_of_a_first_page_after_a_skipped_entry(app, client, survey):
    uid = _first_page(app, client, [
        {"t": "branch", "id": "b1", "cond": {"item": "p:src", "op": "eq", "value": "x"}, "children": [{"t": "block", "block": 2}]},
        {"t": "block", "block": 1}], page_timing=True)
    p = app.test_client()
    assert "Einwilligung" in p.get(f"/s/{uid}").data.decode()
    p.post(f"/s/{uid}", data={**CONSENT_OK, "pos": "r1"})
    with app.app_context():
        r = db.session.scalar(db.select(Response))
        assert "1" in ((r.paradata or {}).get("_t") or {}), r.paradata


def test_the_timer_of_a_first_page_runs_from_when_it_was_shown(app, client, survey):
    uid = _first_page(app, client, [{"t": "block", "block": 2}, {"t": "block", "block": 1}])
    with app.app_context():
        b = db.session.get(Block, 2)
        b.settings = {**(b.settings or {}), "max_seconds": 5}
        db.session.commit()
        sid = db.session.scalar(db.select(Survey.id))
    p = app.test_client()
    p.get(f"/s/{uid}")
    with p.session_transaction() as sess:  # non-essential limits leave at least 30 seconds to react
        sess[f"n{sid}"] = {**sess[f"n{sid}"], "shown": (utcnow() - timedelta(seconds=40)).isoformat()}
    html = p.post(f"/s/{uid}", data={"nav": "next", "time_up": "1", "pos": "r0"}, follow_redirects=True).data.decode()
    assert "has-error" not in html and "Einwilligung" in html        # on without an answer, as on a later page
    with app.app_context():
        r = db.session.scalar(db.select(Response))
        assert r.paradata.get("_timeup")


def test_a_held_first_page_holds_its_button(app, client, survey):
    uid = _first_page(app, client, [{"t": "block", "block": 2}, {"t": "block", "block": 1}])
    with app.app_context():
        b = db.session.get(Block, 2)
        b.settings = {**(b.settings or {}), "min_seconds": 60}
        db.session.commit()
    p = app.test_client()
    p.get(f"/s/{uid}")
    html = p.post(f"/s/{uid}", data={"nav": "next", "i2": "ja", "pos": "r0"}, follow_redirects=True).data.decode()
    assert "Einwilligung" not in html                                # held: the same page again
    with app.app_context():
        assert db.session.scalar(db.select(Response)).position == 0


# --- R-8: a reservation that ran out, taken back at a completion that comes with a GET ------------------------------


def test_a_reservation_is_taken_back_at_a_completion_by_get(app, client, survey):
    test_quotas.build(app, client, cells=[("Männer", "m", 2, [(2, "eq", "1")])])
    uid = test_quotas.start(app, client)
    test_quotas.screen(app, uid)
    from app import quotas

    with app.app_context():
        r = db.session.scalar(db.select(Response))
        r.last_active_at = utcnow() - timedelta(minutes=90)
        db.session.commit()
        assert quotas.release_expired() == 1
        db.session.commit()
    assert test_quotas.counters(app)["running"] == 0
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        r = db.session.scalar(db.select(Response))
        # back from an external section that was the last element: the page after it is the end, with a GET
        r.layout = {**r.layout, "pages": [*r.layout["pages"], {"ext": "gone"}]}
        r.position = len(r.layout["pages"]) - 1
        runtime.step(s, r, {}, "de", "GET")
        db.session.commit()
        assert r.status == Response.COMPLETED
    assert test_quotas.counters(app) == {"running": 0, "done": 1, "refused": 0, "over": 0}


# --- R-9, R-11: a request sent twice --------------------------------------------------------------------------------


def test_the_preview_sent_twice_shows_the_page_again(app, client, survey, monkeypatch):
    fill_consent(client)
    client.get("/surveys/1/editor/preview")

    def stale(*args, **kwargs):
        raise StaleDataError("sent twice")

    monkeypatch.setattr(runtime, "step", stale)
    app.config["PROPAGATE_EXCEPTIONS"] = False
    r = client.post("/surveys/1/editor/preview?lang=de", data=CONSENT_OK)
    assert r.status_code == 303 and r.headers["Location"].endswith("/surveys/1/editor/preview?lang=de")


def test_a_live_answer_tapped_twice_is_no_server_error(app, client, monkeypatch):
    from app import live

    code, sid, block = test_live.make(app, client)
    q = test_live.add(app, client, sid, block, "rating", question="Wie?")
    test_live.steer(client, code, action="start")

    def stale(*args, **kwargs):
        raise StaleDataError("tapped twice")

    monkeypatch.setattr(live.audience, "_response", stale)
    app.config["PROPAGATE_EXCEPTIONS"] = False
    p = app.test_client()
    r = p.post(f"/live/{code}/answer", data={"ref": f"q-{q}", f"i{q}": "4"})
    assert r.status_code == 409 and r.json["error"] == "stale"
    assert p.post(f"/live/{code}/name", data={"name": "Kim"}).status_code == 409


def test_a_return_link_sent_twice_keeps_its_token(app):
    from app import participate

    with app.test_request_context("/s/abc/back?t=token123"):
        answer = participate._sent_twice(StaleDataError())
    assert answer.status_code == 303 and answer.headers["Location"].endswith("/s/abc/back?t=token123")


# --- R-10: no new participation once the field time is over -------------------------------------------------------


def test_no_new_participation_after_the_field_end(app, client, survey, monkeypatch):
    from app import lifecycle

    url = _started(app, client)
    p = app.test_client()
    p.get(url)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.field_end = utcnow() - timedelta(seconds=5)                # over, not closed yet (closing runs once a minute)
        db.session.commit()
    monkeypatch.setattr(lifecycle, "maybe_close_due_surveys", lambda: None)
    p.post(url, data=CONSENT_OK)
    assert _rows(app) == 0
    html = app.test_client().get(url).data.decode()
    assert "beendet" in html and "Einwilligung" not in html
    with app.app_context():
        assert db.session.get(Survey, 1).status == Survey.RUNNING


# --- A-1, A-4, A-6: live votes in the results and the export --------------------------------------------------------


def test_live_votes_have_no_speeders(app, client):
    code, sid, block = test_live.make(app, client)
    q = test_live.add(app, client, sid, block, "rating", question="Wie?")
    test_live.steer(client, code, action="start")
    for _ in range(5):
        test_live.send(app.test_client(), code, {f"i{q}": "4"})
    with app.app_context():
        rows = db.session.scalars(db.select(Response).filter_by(survey_id=sid).order_by(Response.id)).all()
        for r, seconds in zip(rows, (600, 600, 600, 20, 10)):  # two joined late
            r.started_at = r.completed_at - timedelta(seconds=seconds)
        db.session.commit()
    page = client.get(f"/surveys/{sid}/results?go=1").data.decode()
    assert re.findall(r"Grundlage: <strong>(\d+)</strong>", page) == ["5"]
    with app.app_context():
        from app import export

        s = db.session.get(Survey, sid)
        assert [r["speeder"] for r in export.rows(s, with_quality=False)] == [0] * 5
        assert not export.marked_ids(s)


def test_live_runs_on_the_same_day_are_told_apart(app, client):
    code, sid, block = test_live.make(app, client)
    q = test_live.add(app, client, sid, block, "rating", question="Wie?")
    test_live.steer(client, code, action="new_run")
    test_live.send(app.test_client(), code, {f"i{q}": "4"})
    st = test_live.steer(client, code, action="new_run")
    for _ in range(2):
        test_live.send(app.test_client(), code, {f"i{q}": "2"})
    names = [r["name"] for r in st["runs"] + [st["run"]]]
    assert len(set(names)) == len(names) and names[-1].endswith(" (2)")
    with app.app_context():
        from app import export

        s = db.session.get(Survey, sid)
        cols = [c for c, _ in export.columns(s)]
        assert {"durchgang", "durchgang_id"} <= set(cols)
        assert {"durchgang", "durchgang_id"} <= {row[0] for row in export.codebook(s)}       # A-6
        assert sorted(r["durchgang_id"] for r in export.rows(s, with_quality=False)) == sorted([st["runs"][-1]["id"]] + [st["run"]["id"]] * 2)
    page = client.get(f"/surveys/{sid}/results?durchgang={st['run']['id']}").data.decode()
    assert re.findall(r"Grundlage: <strong>(\d+)</strong>", page) == ["2"]
    assert f'value="{st["run"]["id"]}" selected' in page


def test_the_codebook_lists_every_column_of_a_live_vote(app, client):
    code, sid, block = test_live.make(app, client)
    q = test_live.add(app, client, sid, block, "rating", question="Wie?")
    test_live.steer(client, code, action="start")
    test_live.send(app.test_client(), code, {f"i{q}": "4"})
    with app.app_context():
        from app import export

        s = db.session.get(Survey, sid)
        book = [r[0] for r in export.codebook(s)]
        assert [c for c, _ in export.columns(s) if c not in book] == []


# --- A-H1, A-H3, A-H4: live votes ------------------------------------------------------------------------------------


def test_a_quiz_question_opened_again_after_its_countdown(app, client):
    code, sid, block = test_live.make(app, client)
    q = test_live.add(app, client, sid, block, "single_choice", question="Wer?", options=["A", "B"])
    client.post(f"/presenter/{code}/control", data={"action": "quiz_set", "item": q, "on": "1", "seconds": "10", "correct": "1"})
    test_live.steer(client, code, action="start")
    with app.app_context():
        s = db.session.get(Survey, sid)
        s.live = {**s.live, "opened": {str(q): s.live["opened"][str(q)] - 60}}   # its countdown ran out a while ago
        db.session.commit()
    p = app.test_client()
    assert test_live.key(p, code).startswith("closed-")
    test_live.steer(client, code, action="reopen")
    assert test_live.key(p, code) == f"q-{q}"
    assert test_live.send(p, code, {f"i{q}": "1"}).json["ok"]


def test_a_nickname_alone_is_no_participation(app, client):
    code, sid, block = test_live.make(app, client)
    q = test_live.add(app, client, sid, block, "single_choice", question="Wer?", options=["A", "B"])
    client.post(f"/presenter/{code}/control", data={"action": "quiz_set", "item": q, "on": "1", "correct": "1"})
    test_live.steer(client, code, action="start")
    p = app.test_client()
    assert p.post(f"/live/{code}/name", data={"name": "Kim"}).json["ok"]
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(survey_id=sid)) == 0
    assert 'value="Kim"' in p.get(f"/live/{code}").data.decode()
    assert test_live.send(p, code, {f"i{q}": "1"}).json["ok"]
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(survey_id=sid))
        assert r.layout["live_nick"] == "Kim" and str(q) in r.answers
    assert p.post(f"/live/{code}/name", data={"name": "Kim B."}).json["ok"]   # with a participation: kept with it
    with app.app_context():
        assert db.session.scalar(db.select(Response).filter_by(survey_id=sid)).layout["live_nick"] == "Kim B."


def test_the_screen_reads_the_answers_once_per_request(app, client):
    code, sid, block = test_live.make(app, client)
    q = test_live.add(app, client, sid, block, "text_input", question="Begriffe?")
    test_live.steer(client, code, action="start")
    for word in ("Haus", "Baum", "Haus"):
        test_live.send(app.test_client(), code, {f"i{q}": word})
    test_live.steer(client, code, action="results_on")
    reads = []

    def count(conn, cursor, statement, *args):
        if re.search(r"FROM responses?\b", statement) and "count(" not in statement.lower():
            reads.append(statement)

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", count)
    try:
        current = client.get(f"/presenter/{code}/result.json").json["current"]
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert current["n"] == 3 and {row[0]: row[1] for row in current["rows"]} == {"Haus": 2, "Baum": 1}
    assert len(reads) == 1, reads
