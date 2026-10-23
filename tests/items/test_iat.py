"""IAT (app/items/iat.py): open to every researcher, the seven-block plan, answers checked against
the design, D score, export and long format."""
import json

from app.extensions import db
from app.models import Item, Response, Survey
from tests.conftest import CONSENT_OK, add, fill_consent

E = "/surveys/1/editor"


def _iat(app, client):
    fill_consent(client)
    assert add(client, "iat").status_code == 302                                  # item 2


def _answer(app, order=1, effect=100, fast=False, error_every=0):
    """A complete answer: incompatible blocks `effect` ms slower."""
    from app.items.iat import BLOCKS, compatible

    with app.app_context():
        item = db.session.get(Item, 2)
        _o, blocks = item.kind.plan(item, "seed")
    trials, n = [], 0
    for b in blocks:
        for k, (sid, _side) in enumerate(b["trials"]):
            slow = b["n"] in (3, 4, 6, 7) and not compatible(order, b["n"])
            rt = 200 if fast else 600 + (effect if slow else 0) + (k % 5) * 30
            n += 1
            trials.append([b["n"], sid, rt, int(bool(error_every) and n % error_every == 0)])
    assert len(trials) == sum(BLOCKS)
    return {"o": order, "t": trials}


def _start(app, client):
    client.post("/surveys/1/start", data={})
    with app.app_context():
        return f"/s/{db.session.get(Survey, 1).code}"


def test_open_without_a_tick(app, client, survey):
    """No longer experimental (Ralf, 2026-10-10): in the palette for everyone, with no feature switched on."""
    assert "Impliziter Assoziationstest" in client.get(E + "/").data.decode()
    assert add(client, "iat").status_code == 302
    assert client.get(E + "/items/2").status_code == 200


def test_plan_follows_the_standard_design(app, client, survey):
    from app.items.iat import BLOCKS, sides

    _iat(app, client)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.validate(item) == []
        o1, blocks = item.kind.plan(item, "p-1")
        assert item.kind.plan(item, "p-1") == (o1, blocks)                       # stable for one participant
        assert [len(b["trials"]) for b in blocks] == list(BLOCKS)
        cat = {s["id"]: s["cat"] for s in item.config["stimuli"]}
        for b in blocks:
            left, right = sides(o1, b["n"])
            for sid, side in b["trials"]:
                assert cat[sid] in (left if side == 0 else right)
            if b["n"] in (3, 4, 6, 7):                                             # target and attribute alternate
                kinds = [cat[sid] in "AB" for sid, _ in b["trials"]]
                assert all(x != y for x, y in zip(kinds, kinds[1:]))
        orders = {item.kind.order_of(item, f"p-{n}") for n in range(40)}
        assert orders == {1, 2}                                                     # drawn per person


def test_d_score_and_export(app, client, survey):
    _iat(app, client)
    url = _start(app, client)
    p = app.test_client()
    p.get(url)
    p.post(url, data=CONSENT_OK)
    html = p.get(url).data.decode()
    assert "data-iat-plan" in html and "data-iat-words" in html
    with app.app_context():  # the order drawn for this participation: another one is refused (audit 2026-10-12, N-12)
        pid = db.session.scalar(db.select(Response).filter_by(is_preview=False)).pid
        drawn = db.session.get(Item, 2).kind.order_of(db.session.get(Item, 2), pid)
    assert p.post(url, data={"nav": "next", "i2": json.dumps(_answer(app, order=drawn, error_every=20))}).status_code == 302
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        item = db.session.get(Item, 2)
        s = item.kind.scores(r.answers["2"])
    assert s["valid"] == 1 and s["d"] > 0.5 and s["rt_incomp"] - s["rt_comp"] == 100 and s["err"] == 5.0
    with app.app_context():
        code = db.session.get(Item, 2).code
    header = client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig").splitlines()[0].split(",")
    assert all(f"{code}_{k}" in header for k in ("d", "d1", "d2", "valid", "order", "err", "fast", "rt_comp", "rt_incomp"))
    long = client.get("/surveys/1/export/conjoint/2.csv")
    assert long.status_code == 200 and long.data.decode("utf-8-sig").count("\n") >= 188


def test_d_sign_and_validity(app, client, survey):
    _iat(app, client)
    with app.app_context():
        kind = db.session.get(Item, 2).kind
        assert kind.scores(_answer(app, order=1, effect=-100))["d"] < 0          # faster with B + X
        assert kind.scores(_answer(app, order=1, effect=0))["d"] == 0
        too_fast = kind.scores(_answer(app, fast=True))
        assert too_fast["valid"] == 0 and too_fast["d"] is None


def test_answers_that_break_the_design_are_refused(app, client, survey):
    _iat(app, client)
    with app.app_context():
        item = db.session.get(Item, 2)
        good = _answer(app)
        assert item.kind._parse(item, json.dumps(good)) is not None
        assert item.kind._parse(item, json.dumps({**good, "t": good["t"][:-1]})) is None   # a trial missing
        moved = [list(t) for t in good["t"]]
        moved[0][0] = 2                                                                  # a target word in block 2
        assert item.kind._parse(item, json.dumps({**good, "t": moved})) is None
        assert item.kind._parse(item, json.dumps({**good, "o": 3})) is None
        assert item.kind._parse(item, "nonsense") is None


def test_a_long_pause_is_capped_not_refused(app, client, survey):
    """Math audit 2026-10-05, N-2: one trial over 60 s (tab switched) no longer throws the whole answer away; it is
    capped and the D score drops it like any trial over 10,000 ms."""
    from app.items.iat import MAX_RT

    _iat(app, client)
    with app.app_context():
        item = db.session.get(Item, 2)
        good = _answer(app)
        slow = [list(t) for t in good["t"]]
        k = next(n for n, t in enumerate(slow) if t[0] == 4)
        slow[k][2] = 61_000
        parsed = item.kind._parse(item, json.dumps({**good, "t": slow}))
        assert parsed is not None and parsed["t"][k][2] == MAX_RT
        dropped = [t for n, t in enumerate(good["t"]) if n != k]
        assert item.kind.scores(parsed)["d"] == item.kind.scores({**good, "t": dropped})["d"]
        negative = [list(t) for t in good["t"]]
        negative[0][2] = -5
        assert item.kind._parse(item, json.dumps({**good, "t": negative})) is None


def test_full_sources_and_method_sentence(app, client, survey):
    """N-8: Greenwald et al. (2003) and Nosek et al. (2005) in full in the codebook, the editor and the method page."""
    _iat(app, client)
    with app.app_context():
        item = db.session.get(Item, 2)
        notes = item.kind.codebook_info(item, f"{item.code}_d", "de")[2]
        assert "https://doi.org/10.1037/0022-3514.85.2.197" in notes[0] and "https://doi.org/10.1177/0146167204271418" in notes[2]
    assert "An improved scoring algorithm" in client.get(E + "/items/2").data.decode()
    client.post("/surveys/1/start", data={})
    from app.models import utcnow

    with app.app_context():
        for n, answer in enumerate((_answer(app), _answer(app, fast=True))):
            db.session.add(Response(survey_id=1, source="real", status=Response.COMPLETED, answers={"2": answer},
                                    pid=f"t-{n}", started_at=utcnow(), completed_at=utcnow()))
        db.session.commit()
    from app import methods

    with app.app_context():
        text = methods.draft(methods.facts(db.session.get(Survey, 1)))
    assert "Einen D-Wert hatten 1 von n = 2 Personen" in text and "(Greenwald et al., 2003)" in text
    page = client.get("/surveys/1/results").data.decode()
    assert "einen D-Wert haben 1 von 2 Personen" in page and "Method variables and construct validity" in page


def test_d_score_for_piping_and_conditions(app, client, survey):
    """{{<code>_d}} in later texts ("0,46", empty without a score) and the D score as a number in the conditions of
    the flow (Philipp, 2026-10-06)."""
    from app import conditions, flow, piping

    _iat(app, client)
    strong, fast = _answer(app, order=1, effect=400), _answer(app, fast=True)
    with app.app_context():
        s, item = db.session.get(Survey, 1), db.session.get(Item, 2)
        d = item.kind.scores(strong)["d"]
        name = f"{item.code}_d"
        assert piping.sources(s)[name].id == 2
        assert str(piping.values(s, {2: strong}, "de")[name]) == f"{d:.2f}".replace(".", ",")
        assert str(piping.values(s, {2: fast}, "de")[name]) == ""                   # no D score: the text for empty
        assert "D-Wert" in client.get(E + "/items/2").data.decode() and name in client.get(E + "/items/2").data.decode()
        described = conditions.describe(s, "de", everything=True)["2"]
        assert described["kind"] == "number" and "D-Wert" in described["label"] and conditions.is_number(item)
        assert flow.holds({"item": 2, "op": "gt", "value": "0,35"}, {2: strong}, s)
        assert not flow.holds({"item": 2, "op": "lt", "value": "0,35"}, {2: strong}, s)
        assert flow.holds({"item": 2, "op": "between", "value": "1;3"}, {2: strong}, s) == (1 <= d <= 3)
        assert flow.holds({"item": 2, "op": "empty"}, {2: fast}, s)                 # a run without a score
        assert not flow.holds({"item": 2, "op": "gt", "value": "-5"}, {2: fast}, s)
        assert conditions.text(s, {"item": 2, "op": "gt", "value": "0,35"}) == f"{item.code} (D-Wert): über 0,35"
        assert conditions.text(s, {"item": 2, "op": "empty"}) == f"{item.code} (D-Wert): nicht ermittelt"
