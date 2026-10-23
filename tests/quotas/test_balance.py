"""Balanced assignment and the live comparison without reading every participation on every request
(audit of 2026-10-01, package 3; app/balance.py)."""
import random
import time
from collections import Counter
from itertools import combinations

from sqlalchemy import event

from app import balance, flow
from app.extensions import db
from app.models import Response, Survey, utcnow
from tests.conftest import CONSENT_OK
from tests.editor.test_flow import setup_design


def test_least_used_is_the_combination_with_the_smallest_sum():
    used = {"a": 5, "b": 1, "c": 3, "d": 1, "e": 0, "f": 4}
    for k in range(1, 6):
        best = min(sum(used[x] for x in c) for c in combinations(used, k))
        for seed in range(20):
            picked = balance.least_used(list(used), used, k, random.Random(seed))
            assert len(set(picked)) == k and sum(used[x] for x in picked) == best
    # ties are broken at random
    assert {tuple(balance.least_used("abcd", {}, 1, random.Random(s))) for s in range(40)} == {("a",), ("b",), ("c",), ("d",)}


def test_large_randomizer_is_chosen_at_once():
    """12 of 24 used to list 2.7 million combinations for every new participant (seconds, hundreds of MB)."""
    node = {"t": "rand", "id": "r1", "k": 12, "balanced": True,
            "children": [{"t": "block", "block": i} for i in range(24)]}
    seen = Counter()
    used = {}
    original = flow._assignment_counts
    flow._assignment_counts = lambda survey, rid: used
    try:
        started = time.process_time()
        for n in range(200):
            picked = flow.choose(None, node, random.Random(n))
            assert len(picked) == 12 and picked == sorted(picked, key=lambda c: int(c[1:]))  # in the order of the flow
            for c in picked:
                used[c] = used.get(c, 0) + 1
            seen.update(picked)
        assert time.process_time() - started < 2
    finally:
        flow._assignment_counts = original
    assert set(seen.values()) == {100}  # every block shown equally often


def _layout_reads(app):
    """Counts the statements that read the layouts of all participations."""
    reads = []

    def note(conn, cursor, statement, parameters, context, executemany):
        # the layouts whole, or the parts the counts need (SQLite: JSON_EXTRACT; code review 2026-10-07, T4)
        head = statement.lstrip().upper()
        if head.startswith(("SELECT RESPONSE.LAYOUT", "SELECT JSON_QUOTE(JSON_EXTRACT(RESPONSE.LAYOUT")):
            reads.append(statement)

    with app.app_context():
        event.listen(db.engine, "before_cursor_execute", note)
    return reads


def _take_part(app, uid):
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    p.post(f"/s/{uid}", data={"nav": "next", "i2": "30"})
    return "A" if "Framing A" in p.get(f"/s/{uid}").data.decode() else "B"


def test_starts_stay_balanced_without_reading_all_layouts_each_time(app, client, survey):
    app.config["BALANCE_SECONDS"] = 60
    setup_design(app, client)
    client.post("/surveys/1/start", data={})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    reads = _layout_reads(app)
    seen = Counter(_take_part(app, uid) for _ in range(8))
    assert seen == {"A": 4, "B": 4}  # what this process stores is counted at once
    assert len(reads) == 1           # one pass over the participations, not two per start
    # a crawler that opens the link over and over stores nothing and reads nothing
    for _ in range(5):
        app.test_client().get(f"/s/{uid}")
    assert len(reads) == 1


def test_fresh_counts_without_keeping(app, client, survey):
    assert app.config["BALANCE_SECONDS"] == 0  # the setting of the tests
    setup_design(app, client)
    client.post("/surveys/1/start", data={})
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert balance.counts(s)["rand"] == {}
        db.session.add(Response(survey_id=1, source="real", layout={"conditions": {"r1": ["k3"]}, "loops": {"l1": ["ra", "rb"]},
                                                                    "cj": {"7": {"version": 2}}}))
        db.session.add(Response(survey_id=1, source="test", is_preview=True, layout={"conditions": {"r1": ["k4"]}}))
        db.session.commit()
        assert balance.counts(s) == {"rand": {"r1": {"k3": 1}}, "loop": {"l1": {"ra": 1, "rb": 1}}, "version": {"7": {2: 1}}}
        assert flow._assignment_counts(s, "r1") == {"k3": 1}


def test_kept_counts_are_dropped_when_a_participation_goes(app, client, survey):
    app.config["BALANCE_SECONDS"] = 60
    setup_design(app, client)
    client.post("/surveys/1/start", data={})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    _take_part(app, uid)
    with app.app_context():
        s = db.session.get(Survey, 1)
        assert sum(balance.counts(s)["rand"]["r1"].values()) == 1
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data={"i1": "0"})  # declines: the participation is deleted
    with app.app_context():
        assert db.session.scalar(db.select(db.func.count(Response.id))) == 1
        assert sum(balance.counts(db.session.get(Survey, 1))["rand"]["r1"].values()) == 1


def test_answers_for_the_live_comparison_are_kept_for_a_while(app, client, survey):
    app.config["BALANCE_SECONDS"] = 60
    with app.app_context():
        s = db.session.get(Survey, 1)
        for n in range(3):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, completed_at=utcnow(),
                                    answers={"2": n, "3": "x"}))
        db.session.add(Response(survey_id=1, source="real", status=Response.ACTIVE, answers={"2": 9}))
        db.session.add(Response(survey_id=1, source="dummy", is_preview=True, status=Response.COMPLETED, answers={"2": 7}))
        db.session.commit()
        assert [a for _, a in balance.answers(s, "2")] == [0, 1, 2]            # completed real ones, only this answer
        assert [a for _, a in balance.answers(s, "2", preview=True)] == [0, 1, 2, 7]
        db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, completed_at=utcnow(), answers={"2": 5}))
        db.session.commit()
        assert [a for _, a in balance.answers(s, "2")] == [0, 1, 2]            # kept
        assert [a for _, a in balance.answers(s, "2", preview=True)] == [0, 1, 2, 7, 5]  # the preview is always fresh
        balance.forget(1)
        assert [a for _, a in balance.answers(s, "2")] == [0, 1, 2, 5]
        app.config["BALANCE_SECONDS"] = 0
        db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, completed_at=utcnow(), answers={"2": 6}))
        db.session.commit()
        assert [a for _, a in balance.answers(s, "2")] == [0, 1, 2, 5, 6]


def test_first_page_stores_the_condition_it_showed(app, client, survey):
    """Z-1 (audit 2026-10-03): the first page is drawn when the link is opened, stored on the first "Next". A
    start in between must not change the condition: same seed with other counts drew the other one."""
    setup_design(app, client)
    with app.app_context():
        s = db.session.get(Survey, 1)
        s.flow = {"t": "root", "children": [  # the randomized framing on the very first page
            {"t": "rand", "id": "r1", "code": "framing", "name": "Bild", "k": 1, "balanced": True, "shuffle": False,
             "children": [{"t": "block", "block": 3}, {"t": "block", "block": 4}]},
            {"t": "block", "block": 1}, {"t": "block", "block": 2}, {"t": "block", "block": 5}]}
        flow.normalize(s)
        db.session.commit()
    client.post("/surveys/1/start", data={})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    for _ in range(6):
        a = app.test_client()
        page = a.get(f"/s/{uid}").data.decode()
        shown = "k3" if "Framing A" in page else "k4"
        assert ("Framing B" in page) == (shown == "k4")
        # someone else starts and is stored with the same condition: the tie A was drawn from tips over
        with app.app_context():
            db.session.add(Response(survey_id=1, source="real", layout={"conditions": {"r1": [shown]}}))
            db.session.commit()
        assert ("Framing A" in a.get(f"/s/{uid}").data.decode()) == (shown == "k3")  # a reload shows the same
        a.post(f"/s/{uid}", data={"nav": "next"})
        with app.app_context():
            stored = db.session.scalars(db.select(Response).filter(Response.layout.isnot(None))
                                        .order_by(Response.id.desc())).first()
            assert stored.pid and stored.layout["conditions"]["r1"] == [shown]
            db.session.execute(db.delete(Response))
            db.session.commit()


def test_kept_counts_survive_the_session_cookie(app):
    """Z-1: what travels in the session is JSON; design versions come back as numbers, other draws are not kept. A
    loop keeps the counts of its picked rows and one count for all others (second review R-2)."""
    import json

    table = {"rand": {"r1": {"k3": 2, "k4": 1}, "r9": {"k7": 5}}, "loop": {"l1": {"ra": 1}},
             "version": {"7": {0: 3, 2: 1}, "8": {1: 1}}}
    layout = {"conditions": {"r1": ["k4"]}, "loops": {"l1": ["ra"]}, "cj": {"7": {"version": 2}}}
    kept = json.loads(json.dumps(balance.keep(table, layout)))
    assert kept == {"rand": {"r1": {"k3": 2, "k4": 1}}, "loop": {"l1": [{"ra": 1}, 2]}, "version": {"7": {"0": 3, "2": 1}}}
    with app.test_request_context():
        s = Survey(id=5)
        with balance.frozen(s, kept):
            got = balance.counts(s)
        assert got["version"] == {"7": {0: 3, 2: 1}} and got["rand"] == {"r1": {"k3": 2, "k4": 1}}
