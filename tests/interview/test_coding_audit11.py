"""Audit 2026-10-10, package U4 "Kodieren": the AI help in one pass over the participations (P1, K4) with only the
columns the coding reads (P2), the interval of alpha kept with the second coding (P4), the blind second coder neither
reads the first coding through a data access token or a guest's link (R1) nor changes the scheme (R2), and the page
"Kategorisieren" renders with AInstein on (N1, C3)."""
import re

import pytest
from sqlalchemy import event

from app.extensions import db
from app.models import Response, Survey
from tests.conftest import add, experiments_on, own_ai
from tests.interview.test_coding import _add, _assign, _survey
from tests.interview.test_coding_stage2 import _second_person


def _many(app, client, n):
    """Survey 1 with a free text and n finished participations, each with its own text and an AI suggestion."""
    experiments_on(client, "coding")
    add(client, "text_input")
    client.post("/surveys/1/editor/items/2", data={"code": "why", "question": "Warum?"})
    client.post("/surveys/1/categorise/2/add", data={"name": "Preis"})
    with app.app_context():
        db.session.add_all([Response(survey_id=1, source="real", is_preview=False, status=Response.COMPLETED,
                                     pid=f"p{i}", qversion=1, answers={"2": f"Antwort Nummer {i}"}, layout={},
                                     coding={"2~ai": [1]}) for i in range(n)])
        db.session.commit()


def _count_selects(app):
    """A list that collects every SELECT on the participations while it is listened to."""
    seen = []

    def listen(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT") and " response" in statement.lower():
            seen.append(statement)

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", listen)
    return seen, lambda: event.remove(engine, "before_cursor_execute", listen)


def test_accept_reads_the_participations_once_however_many_texts(app, client, survey):
    _many(app, client, 300)
    seen, stop = _count_selects(app)
    try:
        r = client.post("/surveys/1/categorise/2/ai/accept", data={"reviewed": "1"})
    finally:
        stop()
    assert r.status_code == 302
    with app.app_context():
        coded = [x.coding for x in db.session.scalars(db.select(Response))]
    assert all(c["2"] == [1] and c["2~ki"] == 1 for c in coded)
    # one read for the list of answers, one for the assignment: not one per text (it was 300 + 1)
    whole = [s for s in seen if "FROM response" in s and "WHERE response.survey_id" in s]
    assert len(whole) <= 3, len(whole)
    # only the columns the coding reads, "external" among them (the seal listener would load it row by row)
    assert all("response.layout" not in s and "response.paradata" not in s for s in whole)
    assert any("response.external" in s for s in whole)


@pytest.mark.slow
def test_accept_with_2000_participations_is_fast(app, client, survey):
    import time

    _many(app, client, 2000)
    t = time.monotonic()
    assert client.post("/surveys/1/categorise/2/ai/accept", data={"reviewed": "1"}).status_code == 302
    assert time.monotonic() - t < 15  # 75 s before the fix; about 2 s after on the audit machine


def test_ai_assign_writes_all_suggestions_in_one_pass(app, client, survey, monkeypatch):
    import json

    from app import ai

    _many(app, client, 40)
    with app.app_context():
        for r in db.session.scalars(db.select(Response)):
            r.coding = None
        db.session.commit()
    app.config["OPENAI_API_KEY"] = "test"
    own_ai(app)  # the AI coding help runs only over an own access (2026-10-21)
    monkeypatch.setattr(ai, "generate_text", lambda i, p, **kw: json.dumps(
        {"answers": [{"n": k, "ids": [1]} for k in range(len(json.loads(p)["answers"]))]}))
    client.post("/surveys/1/categorise/2/ai/informed", data={"informed": "1"})
    seen, stop = _count_selects(app)
    try:
        client.post("/surveys/1/categorise/2/ai/assign")
    finally:
        stop()
    with app.app_context():
        rows = list(db.session.scalars(db.select(Response)))
        assert sum(r.coding == {"2~ai": [1]} for r in rows) == 30  # smaller untrusted-data batches (KR-3)
        assert sum(r.coding is None for r in rows) == 10
    assert len([s for s in seen if "WHERE response.survey_id" in s]) <= 3
    client.post("/surveys/1/categorise/2/ai/assign")
    with app.app_context():
        assert all(r.coding == {"2~ai": [1]} for r in db.session.scalars(db.select(Response)))


def _blind_setup(app, client):
    """Survey 1 with a coded free text and a second coding of another person under way; returns (key, their client)."""
    key = _survey(app, client)
    _add(client, "Preis")
    _add(client, "Lieferung")
    _assign(client, "zu teuer", 1)
    _assign(client, "lieferung dauert", 2)
    _assign(client, "toll", 99)
    other, uid = _second_person(app)
    client.post(f"/surveys/1/categorise/{key}/second/start", data={"coder": str(uid)})
    return key, other


def test_blind_second_coder_gets_no_first_coding_through_a_token(app, client, survey):
    key, other = _blind_setup(app, client)
    r = other.post("/surveys/1/data-access/new", data={"name": "Laptop"})
    token = re.search(r'value="(askdt_[\w-]+)"', r.get_data(as_text=True)).group(1)
    api = app.test_client()
    head = api.get("/api/v1/surveys/1/data.csv", headers={"Authorization": f"Bearer {token}"}).data.decode("utf-8-sig")
    assert "why" in head.splitlines()[0] and "why_k" not in head
    book = api.get("/api/v1/surveys/1/codebook.csv", headers={"Authorization": f"Bearer {token}"}).data.decode("utf-8-sig")
    assert "why_k1" not in book
    # the first coder's own token still has the categories
    r = client.post("/surveys/1/data-access/new", data={"name": "Mine"})
    mine = re.search(r'value="(askdt_[\w-]+)"', r.get_data(as_text=True)).group(1)
    assert "why_k1" in api.get("/api/v1/surveys/1/data.csv", headers={"Authorization": f"Bearer {mine}"}).data.decode("utf-8-sig")


def test_guests_and_dashboards_see_no_categories_while_a_blind_coding_runs(app, client, survey):
    from app import coding

    key, other = _blind_setup(app, client)
    with app.test_request_context("/"):
        s = db.session.get(Survey, 1)
        assert coding.hidden(s) == [key]  # a guest: a public dashboard link
        assert coding.running(s) == [key]
        assert coding.plan(s, hide=coding.running(s)) == {}
    for n, c in (("zu teuer", "1"), ("lieferung dauert", "2"), ("toll", "99")):
        other.post(f"/surveys/1/categorise/{key}/assign", data={"norm": n, "slot": "second", "cat": c})
    other.post(f"/surveys/1/categorise/{key}/second/finish")
    with app.test_request_context("/"):
        s = db.session.get(Survey, 1)
        assert coding.hidden(s) == [] and coding.running(s) == []


def test_blind_second_coder_may_not_change_the_scheme(app, client, survey):
    key, other = _blind_setup(app, client)
    with app.app_context():
        before = db.session.get(Survey, 1).codings[key]
    for path, data in (("remove/1", {}), ("merge", {"source": "1", "target": "2"}), ("split/1", {"name": "Neu"}),
                       ("scheme", {"name_1": "Anders"}), ("add", {"name": "Dritte"})):
        assert other.post(f"/surveys/1/categorise/{key}/{path}", data=data).status_code == 403, path
    with app.app_context():
        assert db.session.get(Survey, 1).codings[key] == before


def test_alpha_interval_is_kept_when_the_second_coding_ends(app, client, survey, monkeypatch):
    from app import coding, coding_check

    key, other = _blind_setup(app, client)
    for n, c in (("zu teuer", "1"), ("lieferung dauert", "1"), ("toll", "99")):
        other.post(f"/surveys/1/categorise/{key}/assign", data={"norm": n, "slot": "second", "cat": c})
    other.post(f"/surveys/1/categorise/{key}/second/finish")
    with app.app_context():
        s = db.session.get(Survey, 1)
        kept = s.codings[key]["second"]["interval"]
        assert set(kept) == {"sig", "ci"}
        item, col, _ = coding.find(s, key)
        fresh = coding_check.report(s, item, col, key)
        # what is kept is what a fresh computation gives; a view does not draw again
        monkeypatch.setattr(coding_check, "_interval", lambda *a, **k: pytest.fail("computed again"))
        assert coding_check.report(s, item, col, key)["ci"] == fresh["ci"]
    monkeypatch.undo()
    assert "Krippendorffs α" in client.get(f"/surveys/1/categorise/{key}").data.decode()


def test_interval_kept_matches_a_fresh_one_for_many_units(app):
    from app import coding_check

    pairs = [(frozenset({1}), frozenset({1 if i % 4 else 2})) for i in range(30)]
    entry = coding_check._interval_entry(pairs, [1, 2, 98, 99], True, "2")
    assert entry["ci"] == list(coding_check._interval(pairs, [1, 2, 98, 99], True, seed=__import__("zlib").crc32(b"2")))
    second = {"interval": entry}
    assert coding_check._kept_interval(second, "2", pairs, [1, 2, 98, 99], True) == tuple(entry["ci"])
    # other pairs: computed anew, not the kept one
    other = pairs[:-1] + [(frozenset({1}), frozenset({1}))]
    assert coding_check._kept_interval(second, "2", other, [1, 2, 98, 99], True) == \
        coding_check._interval(other, [1, 2, 98, 99], True, seed=__import__("zlib").crc32(b"2"))


def test_page_renders_with_ainstein_on(app, client, survey):
    key = _survey(app, client)
    experiments_on(client, "coding", "ainstein")
    r = client.get(f"/surveys/1/categorise/{key}")
    assert r.status_code == 200 and "Kategorisieren" in r.data.decode()
