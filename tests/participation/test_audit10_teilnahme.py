"""Audit of data and computations 2026-10-10, package F (participation): a reload keeps the page's clock (T-1), a
participation keeps its condition and loop rows after "Zur Bearbeitung öffnen" (T-2), "Doch teilnehmen" takes the
decline back (T-3), the timer drops an answer changed into something invalid (T-4), an answer emptied in a live vote
in blocks is gone (T-5)."""
import csv
import io
import random
from datetime import timedelta

from app import flow, runtime
from tests.participation.test_participate import _pos
from app.extensions import db
from app.models import Response, Survey, utcnow
from tests.conftest import CONSENT_OK, add, fill_consent
from tests.analysis.test_pagetime import response as stored_response
from tests.analysis.test_pagetime import setup as timed_setup
from tests.analysis.test_pagetime import start as timed_start
from tests.analysis.test_pagetime import wait

E = "/surveys/1/editor"


# --- T-1 -----------------------------------------------------------------------------------------------------------


def _timed_survey(app, client, tempo=False, **first):
    fill_consent(client)
    add(client, "text_input")
    client.post(E + "/items/2", data={"code": "q1", "question": "Bitte beschreiben Sie ausführlich, was Sie gestern "
                                                               "den ganzen Tag lang gemacht haben und warum.", **first})
    add(client, "page_break")
    add(client, "text_input")
    client.post(E + "/items/4", data={"code": "q2", "question": "Frage 2"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.page_timing = True
        s.paradata = True
        if tempo:
            s.tempo_hint = "all"
        db.session.commit()
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        return db.session.get(Survey, 1).code


def _age_shown(app, seconds):
    with app.app_context():
        r = db.session.scalar(db.select(Response))
        pd = dict(r.paradata or {})
        pd["_shown"] = {**pd["_shown"], "at": (utcnow() - timedelta(seconds=seconds)).isoformat()}
        r.paradata = pd
        db.session.commit()


def _paradata(app):
    with app.app_context():
        return dict(db.session.scalar(db.select(Response)).paradata or {})


def test_a_reload_keeps_the_time_before_it(app, client, survey):
    uid = _timed_survey(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.get(f"/s/{uid}")              # the page with q1
    _age_shown(app, 90)             # read for 90 s ...
    p.get(f"/s/{uid}")              # ... then reloaded (or back from the privacy page)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "Text", "pos": "r1"})
    assert _paradata(app)["_t"]["2"] >= 90


def test_a_reload_gives_no_false_tempo_hint(app, client, survey):
    uid = _timed_survey(app, client, tempo=True)
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.get(f"/s/{uid}")
    _age_shown(app, 90)
    p.get(f"/s/{uid}")
    html = p.post(f"/s/{uid}", data={"nav": "next", "i2": "Text", "pos": "r1"}, follow_redirects=True).data.decode()
    assert not (_paradata(app).get("_tempo") or {}).get("fast")
    assert "sehr schnell" not in html


def test_the_first_page_opened_again_keeps_its_clock(app, client, survey):
    uid = _timed_survey(app, client)
    p = app.test_client()
    p.get(f"/s/{uid}")
    with p.session_transaction() as sess:
        key = next(k for k, v in sess.items() if isinstance(v, dict) and "shown" in v)
        sess[key] = {**sess[key], "shown": (utcnow() - timedelta(seconds=60)).isoformat()}
    p.get(f"/s/{uid}")              # the link opened again
    p.post(f"/s/{uid}", data=CONSENT_OK)
    assert _paradata(app)["_t"]["1"] >= 60


def test_after_an_error_the_page_starts_its_clock_anew_without_losing_time(app, client, survey):
    uid = _timed_survey(app, client, validation="integer")
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.get(f"/s/{uid}")
    _age_shown(app, 30)
    failed = p.post(f"/s/{uid}", data={"nav": "next", "i2": "abc", "pos": "r1"})
    assert failed.status_code == 200  # an error; the correction uses its new revision
    _age_shown(app, 20)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "5", "pos": _pos(failed.get_data(as_text=True))})
    assert 50 <= _paradata(app)["_t"]["2"] < 60   # both visits, nothing counted twice


# --- T-2 -----------------------------------------------------------------------------------------------------------


def _randomized(app, client):
    fill_consent(client)
    add(client, "text_input", block=2)                     # 2 in block 2 (condition A)
    client.post(E + "/items/2", data={"code": "qa", "question": "Frage A"})
    client.post(E + "/blocks")                             # block 3
    add(client, "text_input", block=3)                     # 3 (condition B)
    client.post(E + "/items/3", data={"code": "qb", "question": "Frage B"})
    client.post(E + "/blocks")                             # block 4
    add(client, "text_input", block=4)                     # 4 (after)
    client.post(E + "/items/4", data={"code": "qz", "question": "Frage Z"})
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [{"t": "block", "block": 1},
                  {"t": "rand", "id": "r1", "code": "bed", "name": "Bedingung", "k": 1, "balanced": False,
                   "shuffle": False, "children": [{"t": "block", "block": 2}, {"t": "block", "block": 3}]},
                  {"t": "block", "block": 4}]}
        flow.normalize(s)
        db.session.commit()
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        return db.session.get(Survey, 1).code


def _reopen_and_change(client):
    client.post("/surveys/1/close", data={})
    client.post("/surveys/1/reopen", data={"confirm": "1"})
    add(client, "info", block=4)                           # a small change: an info text
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})


def test_a_restart_keeps_the_condition_and_answers_stay_in_one_condition(app, client, survey):
    uid = _randomized(app, client)
    clients = []
    for n in range(10):
        p = app.test_client()
        p.get(f"/s/{uid}")
        p.post(f"/s/{uid}", data=CONSENT_OK)
        html = p.get(f"/s/{uid}").data.decode()
        item = 2 if 'name="i2"' in html else 3
        p.post(f"/s/{uid}", data={"nav": "next", f"i{item}": f"antwort{n}"})
        clients.append(p)
    with app.app_context():
        before = {r.id: r.layout["conditions"] for r in db.session.scalars(db.select(Response))}
    _reopen_and_change(client)
    with app.test_request_context():
        s = db.session.get(Survey, 1)
        for r in db.session.scalars(db.select(Response)):
            runtime.ensure_layout(s, r)
            assert r.layout["conditions"] == before[r.id]
        db.session.rollback()
    for n, p in enumerate(clients):
        for _ in range(6):
            html = p.get(f"/s/{uid}").data.decode()
            if "Vielen Dank" in html:
                break
            data = {"nav": "next", **CONSENT_OK}
            for i in (2, 3, 4):
                if f'name="i{i}"' in html and f"antwort{n}" not in html:
                    data[f"i{i}"] = f"neu{n}"
            p.post(f"/s/{uid}", data=data)
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv?all=1").data.decode("utf-8-sig"))))
    assert len(rows) == 10 and not [r for r in rows if r.get("qa") and r.get("qb")]


def test_kept_choice_and_rows_only_where_their_nodes_still_exist():
    node = {"t": "rand", "id": "r1", "k": 1, "children": [{"t": "block", "block": 2}, {"t": "block", "block": 3}]}
    assert flow._kept_choice(node, ["k3"]) == ["k3"]
    assert flow._kept_choice(node, ["k9"]) is None              # that child is gone: drawn anew
    assert flow._kept_choice({**node, "k": 2}, ["k3"]) is None  # draws two now
    assert flow._kept_choice(node, None) is None
    loop = {"rows": [{"id": "a"}, {"id": "b"}, {"id": "c"}], "mode": "all", "k": 1, "order": "fixed"}
    rng = random.Random(1)
    assert flow._kept_rows({}, loop, ["b", "a"], rng) == ["b", "a", "c"]     # every row: the new one added
    assert flow._kept_rows({}, {**loop, "mode": "k", "k": 2}, ["c", "a"], rng) == ["c", "a"]
    assert flow._kept_rows({}, {**loop, "mode": "k", "k": 2}, ["c"], rng) is None
    assert flow._kept_rows({}, loop, ["x"], rng) is None


def test_kept_orders_where_the_elements_are_the_same():
    old = {"stmt": {"5": ["s2", "s1"], "6": ["o1", "o2"]},
           "pages": [{"block": 2, "items": [8, 7]}]}
    new = {"stmt": {"5": ["s1", "s2"], "6": ["o2", "o1", "o3"]}, "pages": [{"block": 2, "items": [7, 8]}]}
    runtime._keep_orders(new, old)
    assert new["stmt"] == {"5": ["s2", "s1"], "6": ["o2", "o1", "o3"]}       # an element with a new option: anew
    assert new["pages"][0]["items"] == [8, 7]


# --- T-3 -----------------------------------------------------------------------------------------------------------


def test_take_part_after_all_takes_the_decline_back(app, client, survey):
    fill_consent(client)
    add(client, "text_input")
    client.post(E + "/items/2", data={"code": "q1", "question": "Frage"})
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    other = app.test_client()                               # somebody else declines and stays with it
    other.get(f"/s/{uid}")
    other.post(f"/s/{uid}", data={"i1": "0"})
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data={"i1": "0"})                  # a slip of the finger: "Nein"
    with app.app_context():
        assert db.session.get(Survey, 1).declined_count == 2
    p.post(f"/s/{uid}/after-all")                          # "Doch teilnehmen"
    p.post(f"/s/{uid}/after-all")                          # sent twice: counted back once
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "x"})
    with app.app_context():
        assert db.session.get(Survey, 1).declined_count == 1
        assert db.session.scalar(db.select(db.func.count(Response.id)).filter_by(status="completed")) == 1


# --- T-4 -----------------------------------------------------------------------------------------------------------


def test_the_timer_drops_an_answer_changed_into_something_invalid(app, client, survey):
    timed_setup(client, max_seconds=30)
    client.post(E + "/items/2", data={"code": "q1", "question": "Zahl", "validation": "integer"})
    p, url = timed_start(app, client)
    p.get(url)
    p.post(url, data={"pos": "r1", "nav": "next", "i2": "5"})
    assert stored_response(app)[2].get("2") == 5
    p.get(url)
    p.post(url, data={"pos": "r2", "nav": "back"})          # back to the number ...
    returned = p.get(url)
    wait(app, 30)
    assert p.post(url, data={"pos": _pos(returned.get_data(as_text=True)), "nav": "next", "time_up": "1", "i2": "fünf"}).status_code == 302
    assert "2" not in stored_response(app)[2]               # ... changed into words, then the time ran out


# --- T-5 -----------------------------------------------------------------------------------------------------------


def test_an_answer_emptied_on_a_page_sent_again_in_blocks_is_gone(app, client):
    from tests.live.test_live import add as live_add
    from tests.live.test_live import steer
    from tests.live.test_live_audit_f import _blocks, _rows

    code, sid, block = _blocks(app, client)
    q1 = live_add(app, client, sid, block, "rating", question="A")
    q2 = live_add(app, client, sid, block, "rating", question="B")
    steer(client, code, action="start")
    p = app.test_client()
    ref = {"ref": f"p-{block}-0", "page": f"{q1},{q2}"}
    assert p.post(f"/live/{code}/answer", data={**ref, f"i{q1}": "2", f"i{q2}": "3"}).json["ok"]
    assert p.post(f"/live/{code}/answer", data={**ref, f"i{q1}": "4"}).json["ok"]   # B taken back
    assert [a for a, *_ in _rows(app, sid)] == [{str(q1): 4}]
