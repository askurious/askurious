"""Holdout tasks of CBC and MaxDiff (app/holdouts.py, app/choice_predict.py): editor, pages, answers kept apart from
the estimation, export, lock after the start, dummy data, survey file and copy, hit rate and share error."""
import csv
import io
import json
import math
import random
import re

import numpy as np
import pytest
from werkzeug.datastructures import MultiDict

from app import holdouts
from app.choice_predict import concept_utility, shares
from app.extensions import db
from app.models import Item, Response, Survey
from tests.conftest import CONSENT_OK
from tests.analysis.test_conjoint import E, conjoint_form
from tests.analysis.test_conjoint import setup as cbc_setup

HB_FAST = {"HB_SYNC": True, "HB_BURN": 400, "HB_DRAWS": 400, "HB_THIN": 2}


def cbc_holdout_form(alts_per_holdout, none=True, price_attr=True, **kw):
    """The conjoint form (tests/analysis/test_conjoint.py) with holdout tasks: one list of (look, price, privacy) level
    numbers per alternative and holdout."""
    form = conjoint_form(none=none, **kw)
    if price_attr:
        form.add("attr_price", "a2")
    form.add("ho_count", str(len(alts_per_holdout)))
    for h, alts in enumerate(alts_per_holdout):
        for j, (look, price, privacy) in enumerate(alts):
            form.add(f"ho{h}_{j}_a1", f"a1l{look}")
            form.add(f"ho{h}_{j}_a2", f"a2l{price}")
            form.add(f"ho{h}_{j}_a3", f"a3l{privacy}")
    return form


def wide(client):
    return list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))


# --- hit rate and share error against a calculation by hand ------------------------------------------------

def test_hit_rate_and_mae_by_hand():
    chosen = [0, 1, 0, 2]
    utils = [[1.0, 0.0, 0.0], [0.0, 2.0, 0.0], [0.0, 1.0, 0.0], [1.0, 1.0, 0.0]]
    r = holdouts.score(chosen, utils)
    assert r["n"] == 4 and r["hits"] == 2 and r["hit"] == 0.5
    assert r["lo"] is None                                              # no interval below 10 persons
    e = math.e
    pred = [(e / (e + 2) + 1 / (e * e + 2) + 1 / (e + 2) + e / (2 * e + 1)) / 4,
            (1 / (e + 2) + e * e / (e * e + 2) + e / (e + 2) + e / (2 * e + 1)) / 4,
            (1 / (e + 2) + 1 / (e * e + 2) + 1 / (e + 2) + 1 / (2 * e + 1)) / 4]
    obs = [0.5, 0.25, 0.25]
    assert r["pred"] == pytest.approx(pred) and r["obs"] == obs
    assert r["mae"] == pytest.approx(sum(abs(p - o) for p, o in zip(pred, obs)) / 3)
    # a tie with the chosen alternative counts half; with ten persons the Wilson interval is there
    r = holdouts.score([0] * 10, [[1.0, 1.0]] * 6 + [[2.0, 0.0]] * 4)
    assert r["hits"] == pytest.approx(7) and r["hit"] == pytest.approx(0.7)
    from app import intervals

    assert (r["lo"], r["hi"]) == pytest.approx(intervals.wilson(7, 10))


def test_concept_utility_adds_part_worths_price_and_constant():
    model = {"pw": {0: [0.5, -0.5], 2: [1.0, 0.0, -1.0]}, "price": -0.02, "center": 100.0, "asc": 0.3}
    assert concept_utility(model, [1, 2, 0], price=150) == pytest.approx(-0.5 + 1.0 - 0.02 * 50)
    assert concept_utility(model, [0, 0, 2]) == pytest.approx(0.5 - 1.0)        # no price given: none added
    assert shares([0.0, math.log(3)]) == pytest.approx([0.25, 0.75])


def test_page_order():
    assert holdouts.page_order(8, 0) == [("t", t) for t in range(8)]
    assert holdouts.page_order(6, 3) == [("t", 0), ("t", 1), ("t", 2), ("h", 0), ("t", 3), ("t", 4), ("t", 5),
                                         ("h", 1), ("h", 2)]
    assert holdouts.page_order(3, 2) == [("t", 0), ("t", 1), ("t", 2), ("h", 0), ("h", 1)]


# --- CBC: editor, checks, pages, answers, export ----------------------------------------------------------

def test_cbc_editor_checks_pages_answers_and_export(app, client, survey):
    cbc_setup(client, none=True)
    # holdout 1 sensible, holdout 2: option 1 is option 2 at a higher price; holdout 3: two equal options
    client.post(E + "/items/2", data=cbc_holdout_form([[(0, 0, 0), (1, 2, 1)], [(2, 2, 0), (2, 0, 0)],
                                                       [(1, 1, 1), (1, 1, 1)]]))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert len(item.config["holdouts"]) == 3 and item.kind.design_current(item)   # the design stays current
        assert holdouts.cbc_profiles(item, 0) == [[0, 0, 0], [1, 2, 1]]
        warnings = item.kind.check_warnings(item)
        assert any("Holdout-Aufgabe 2: Option 1 ist gleich wie Option 2, nur teurer" in w for w in warnings)
        assert any("Holdout-Aufgabe 3: Option 1 und 2 sind gleich" in w for w in warnings)
        # besides: 4 tasks per person and a design that cannot separate all part-worths (W-5, H-11)
        assert len([w for w in warnings if w.startswith("Holdout")]) == 2 and len(warnings) == 4
        assert item.kind.validate(item) == []
        from app import design

        found = [f.message for f in design.check(db.session.get(Survey, 1))]
        assert any("nur teurer" in m for m in found)
    page = client.get(E + "/items/2").data.decode()
    assert "Holdout-Aufgaben" in page and 'name="ho0_1_a2"' in page and 'data-ho-slot="3"' in page
    # two holdouts are enough: the third goes
    client.post(E + "/items/2", data=cbc_holdout_form([[(0, 0, 0), (1, 2, 1)], [(2, 1, 0), (0, 2, 1)]]))
    with app.app_context():
        item = db.session.get(Item, 2)
        assert len(item.config["holdouts"]) == 2 and not [w for w in item.kind.check_warnings(item) if w.startswith("Holdout")]
        assert [k for _, _, k in item.kind.timing_pages(item)] == [".t0", ".t1", ".t2", ".h0", ".t3", ".h1"]
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    seen = []
    for n, choice in enumerate(["1", "2", "0", "2", "1", "0"], start=1):
        html = p.get(f"/s/{uid}").data.decode()
        name = re.search(r'name="(i2_[th]\d)"', html).group(1)
        assert f"Aufgabe {n} von 6" in html and "Keine davon" in html
        seen.append(name)
        if name == "i2_h0":   # the holdout looks like a task: the fixed options with their levels
            assert "humanoid" in html and "technisch" in html and "Datenschutz" in html
        p.post(f"/s/{uid}", data={"nav": "next", name: choice})
    assert seen == ["i2_t0", "i2_t1", "i2_t2", "i2_h0", "i2_t3", "i2_h1"]
    assert "Vielen Dank" in p.get(f"/s/{uid}").data.decode()
    with app.app_context():
        r = db.session.scalar(db.select(Response).filter_by(is_preview=False))
        assert r.answers["2"] == {"t0": 1, "t1": 2, "t2": 0, "h0": 2, "t3": 1, "h1": 0}
        from app import paradata

        item = db.session.get(Item, 2)
        assert paradata.page_key([item], {"holdout": 1, "n": 6}) == "2.h1"
        # holdout answers never enter the counts or the logit model
        from app import cbc_analysis

        sets, _, persons, _ = cbc_analysis.choice_sets(item, [r])
        assert persons == 1 and len(sets) == 4
        assert cbc_analysis.counts(item, [r], "de")["tasks"] == 4
    row = wide(client)[0]
    assert [row[f"cbc_t{t}"] for t in range(1, 5)] == ["1", "2", "0", "1"] and (row["cbc_ho1"], row["cbc_ho2"]) == ("2", "0")
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "cbc_ho1" in book and "geht nicht in Zählwerte, Logit-Modell und HB ein" in book
    assert "Option 1: look = humanoid, price = 50 €, privacy = lokal" in book


def test_cbc_holdouts_locked_after_start(app, client, survey):
    cbc_setup(client, none=True)
    client.post(E + "/items/2", data=cbc_holdout_form([[(0, 0, 0), (1, 2, 1)]]))
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    for _ in range(5):
        name = re.search(r'name="(i2_[th]\d)"', p.get(f"/s/{uid}").data.decode()).group(1)
        p.post(f"/s/{uid}", data={"nav": "next", name: "1"})
    client.post("/surveys/1/close", data={})
    client.post("/surveys/1/reopen", data={"confirm": "1"})
    with app.app_context():
        before = db.session.get(Item, 2).config["holdouts"]
        assert db.session.get(Item, 2).kind.design_locked(db.session.get(Item, 2))
    page = client.get(E + "/items/2").data.decode()
    assert re.search(r'<select name="ho_count"[^>]*disabled', page)
    client.post(E + "/items/2", data=cbc_holdout_form([[(2, 2, 1), (0, 0, 0)], [(1, 1, 1), (0, 0, 0)]]))
    client.post(E + "/items/2", data=cbc_holdout_form([]))
    with app.app_context():
        assert db.session.get(Item, 2).config["holdouts"] == before


def test_cbc_dummy_survey_file_and_copy(app, client, survey):
    cbc_setup(client, none=True)
    client.post(E + "/items/2", data=cbc_holdout_form([[(0, 0, 0), (1, 2, 1)], [(2, 1, 0), (0, 2, 1)]]))
    with app.app_context():
        from app import dummy

        assert dummy.generate(db.session.get(Survey, 1), 12, seed=3) == 12
        db.session.commit()
        answers = [r.answers["2"] for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        assert all(a.get("h0") in (0, 1, 2) and a.get("h1") in (0, 1, 2) for a in answers)
        held = db.session.get(Item, 2).config["holdouts"]
    data = json.loads(client.get("/surveys/1/export/survey.json").data)
    from tests.exports.test_transfer import _upload

    assert "ist als Entwurf angelegt" in _upload(client, data, title="Aus Datei")
    from tests.editor.test_duplicate import _copy

    assert "ist als Entwurf angelegt" in _copy(client)
    with app.app_context():
        for title in ("Aus Datei", "Welle 2"):
            new = db.session.scalar(db.select(Survey).filter_by(title=title))
            twin = next(i for b in new.blocks for i in b.items if i.type == "conjoint")
            assert twin.config["holdouts"] == held and holdouts.cbc_profiles(twin, 1) == [[2, 1, 0], [0, 2, 1]]
    # a file whose holdouts name levels it does not have: they are left out
    cbc = next(i for b in data["blocks"] for i in b["items"] if i["type"] == "conjoint")
    cbc["config"]["holdouts"][0]["alts"][0][0] = "zzz999"
    _upload(client, data, title="Fremd")
    with app.app_context():
        new = db.session.scalar(db.select(Survey).filter_by(title="Fremd"))
        twin = next(i for b in new.blocks for i in b.items if i.type == "conjoint")
        assert twin.config["holdouts"] == []


def test_cbc_hit_rate_from_hb_and_logit(app, client, survey):
    from tests.analysis.test_cbc_results import setup
    from tests.analysis.test_conjoint_hb import LOOK, PRIVACY, simulate_segments

    setup(client, price_attr=True, none=True, tasks=12, versions=6)
    app.config.update(HB_FAST)
    segments = simulate_segments(app, 60, none_asc=-0.5)
    with app.app_context():
        item = db.session.get(Item, 2)
        # holdout: look 0 / look 2 / look 1 at the middle price; segment 0 likes look 0, segment 1 look 2
        alts = [["a1l0", "a2l1", "a3l0"], ["a1l2", "a2l1", "a3l0"], ["a1l1", "a2l1", "a3l1"]]
        item.config = {**item.config, "holdouts": [{"id": "hx1", "alts": alts}]}
        rng = random.Random(1)
        for r in db.session.scalars(db.select(Response).filter_by(survey_id=1)):
            seg = segments[r.pid]
            u = [LOOK[seg][0] + PRIVACY[0], LOOK[seg][2] + PRIVACY[0], LOOK[seg][1] + PRIVACY[1], -0.5]
            u = [x - math.log(-math.log(rng.random())) for x in u]
            best = max(range(4), key=u.__getitem__)
            r.answers = {"2": {**r.answers["2"], "h0": 0 if best == 3 else best + 1}}
        db.session.commit()
    client.post("/surveys/1/results/hb/2", data={"source": "real", "speeder": "50"})
    with app.app_context():
        from app import conjoint_hb, results

        item = db.session.get(Item, 2)
        res = conjoint_hb.load(item)
        assert res and conjoint_hb.current(item) is not None
        # holdout answers and holdout settings are not part of what HB was estimated from
        item.config = {**item.config, "holdouts": [{"id": "hx1", "alts": [alts[1], alts[0], alts[2]]}]}
        assert conjoint_hb.current(item) is not None
        item.config = {**item.config, "holdouts": [{"id": "hx1", "alts": alts}]}
        db.session.commit()
        entry = next(e for e in results.compute(db.session.get(Survey, 1))["items"] if e["item"].id == 2)
        ho = entry["cbc"]["holdout"]
        row = ho["rows"][0]
        assert row["chance"] == pytest.approx(1 / 4) and row["hb"]["n"] == 60
        # by hand from the stored posterior means
        price = [50.0, 100.0, 150.0]
        center = res["coding"]["price_center"]
        spans = {a: (start, n) for a, start, n in res["spans"]}
        chosen = {r.pid: r.answers["2"]["h0"] for r in db.session.scalars(db.select(Response).filter_by(survey_id=1))}
        hits, pred = 0, [0.0] * 4
        for n, pid in enumerate(res["pids"]):
            util = res["util"][n]
            u = []
            for look, pr, priv in ((0, 1, 0), (2, 1, 0), (1, 1, 1)):
                u.append(util[spans[0][0] + look] + util[spans[2][0] + priv] + res["price"][n] * (price[pr] - center))
            u.append(res["asc"][n])
            c = chosen[pid] - 1 if chosen[pid] else 3
            hits += max(range(4), key=u.__getitem__) == c
            ex = [math.exp(x) for x in u]
            pred = [p + x / sum(ex) / 60 for p, x in zip(pred, ex)]
        obs = [sum(1 for pid in res["pids"] if (chosen[pid] - 1 if chosen[pid] else 3) == j) / 60 for j in range(4)]
        assert row["hb"]["hit"] == pytest.approx(hits / 60)               # hits: from the posterior means
        # the predicted shares: per person over the kept draws (audit 2026-10-05, W-1), not from the posterior means
        par = conjoint_hb.draw_parameters(res)
        U = np.stack([par["util"][:, :, spans[0][0] + look] + par["util"][:, :, spans[2][0] + priv]
                      + par["price"] * (price[pr] - center) for look, pr, priv in ((0, 1, 0), (2, 1, 0), (1, 1, 1))]
                     + [par["asc"]], axis=2)
        P = (np.exp(U) / np.exp(U).sum(axis=2, keepdims=True)).mean(axis=0).mean(axis=0)
        assert row["hb"]["mae"] == pytest.approx(sum(abs(p - o) for p, o in zip(P, obs)) / 4)
        assert row["hb"]["mae"] != pytest.approx(sum(abs(p - o) for p, o in zip(pred, obs)) / 4)
        assert row["hb"]["hit"] > 0.5 and row["pooled"]["hit"] < row["hb"]["hit"]     # the segments need HB
        assert row["pooled"]["n"] == 60
        # the HB shares keep the HB coding's price centre also when the aggregate model has no price (audit data
        # storage and calculations 2026-10-21, D6: one variable served both models)
        from app import holdouts

        seen = []
        real_draw, real_view = holdouts.draw_utilities, holdouts.cbc_view

        def without_pooled_price(item, table, cbc):
            model = {**cbc["model"], "price": {**(cbc["model"].get("price") or {}), "beta": None}}
            return real_view(item, table, {**cbc, "model": model})

        monkey = pytest.MonkeyPatch()
        monkey.setattr(holdouts, "draw_utilities", lambda *a: seen.append(a[-1]) or real_draw(*a))
        monkey.setattr(holdouts, "cbc_view", without_pooled_price)
        try:
            results.compute(db.session.get(Survey, 1))
        finally:
            monkey.undo()
        assert seen and all(c == pytest.approx(center) for c in seen) and center != 0
    html = client.get("/surveys/1/results").data.decode()
    assert "Vorhersage der Holdout-Aufgaben" in html and ">Trefferquote" in html
    assert "Orme, B. K. (2009). <i>Fine-tuning CBC and adaptive CBC questionnaires</i>" in html


def test_no_holdout_figures_without_hb(app, client, survey):
    cbc_setup(client, none=True)
    client.post(E + "/items/2", data=cbc_holdout_form([[(0, 0, 0), (1, 2, 1)]]))
    html = client.get("/surveys/1/results").data.decode()
    assert "Vorhersage der Holdout-Aufgaben" not in html and "Orme, B. K. (2009)" not in html


# --- MaxDiff ------------------------------------------------------------------------------------------------

def test_maxdiff_holdout_pages_export_dummy_and_hit_rate(app, client, survey):
    from tests.analysis.test_maxdiff import maxdiff_form
    from tests.analysis.test_maxdiff import setup as md_setup

    md_setup(client, versions=2)
    true = random.Random("maxdiff:2")
    base = [true.gauss(0, 1) for _ in range(8)]
    order = sorted(range(8), key=lambda i: -base[i])
    chosen_set = [order[0], order[3], order[5], order[7]]          # the best and the worst statement among others
    form = maxdiff_form(versions=2)
    form.add("ho_count", "2")
    for p, i in enumerate(chosen_set):
        form.add(f"ho0_{p}", f"s{i}")
    for p, i in enumerate([0, 0, 1, 2]):                            # a statement twice: incomplete
        form.add(f"ho1_{p}", f"s{i}")
    client.post(E + "/items/2", data=form)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert holdouts.maxdiff_set(item, 0) == chosen_set and holdouts.maxdiff_set(item, 1) is None
        assert any("Holdout-Set 2" in p for p in item.kind.validate(item))
    form.setlist("ho1_1", ["s3"])
    client.post(E + "/items/2", data=form)
    with app.app_context():
        item = db.session.get(Item, 2)
        assert item.kind.validate(item) == [] and item.kind.shown_tasks(item) == 8
        from app import runtime

        layout = runtime.build_layout(db.session.get(Survey, 1), random.Random(2))
        md = [p for p in layout["pages"] if p.get("items") == [2]]
        assert [("h", p["holdout"]) if "holdout" in p else ("t", p["task"]) for p in md] == \
            [("t", 0), ("t", 1), ("t", 2), ("h", 0), ("t", 3), ("t", 4), ("t", 5), ("h", 1)]
    # the holdout page in the preview: the fixed statements, fields of its own
    with app.app_context():
        uid = db.session.get(Survey, 1).code
    client.post("/surveys/1/start", data={"use_end": "1", "field_end": "2031-01-01"})
    p = app.test_client()
    p.get(f"/s/{uid}")
    p.post(f"/s/{uid}", data=CONSENT_OK)
    for n in range(1, 9):
        html = p.get(f"/s/{uid}").data.decode()
        assert f"Set {n} von 8" in html
        key = re.search(r'name="i2_([th]\d)_b"', html).group(1)
        rows = [int(x) for x in re.findall(r'data-md-row="(\d+)"', html)]
        if key == "h0":
            assert rows == chosen_set
        p.post(f"/s/{uid}", data={"nav": "next", f"i2_{key}_b": str(rows[0]), f"i2_{key}_w": str(rows[1])})
    row = wide(client)[0]
    assert row["md_ho1_best"] == str(chosen_set[0] + 1) and row["md_ho1_worst"] == str(chosen_set[1] + 1)
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "md_ho2_worst" in book and "Holdout-Set: für alle gleich" in book
    with app.app_context():
        from app import maxdiff_analysis

        item = db.session.get(Item, 2)
        persons = maxdiff_analysis.persons_from_table(item, wide(client))
        assert len(persons[0][2]) == 6                                  # six sets, the holdouts are not among them
    # dummy data answer the holdout sets from the same utilities; HB predicts them better than chance
    client.post("/surveys/1/close", data={})
    app.config.update(HB_FAST)
    with app.app_context():
        from app import dummy, results

        s = db.session.get(Survey, 1)
        assert dummy.generate(s, 60, seed=4) == 60
        db.session.commit()
        answers = [r.answers["2"] for r in db.session.scalars(db.select(Response).filter_by(source="dummy"))]
        best = [a["h0"]["b"] for a in answers]
        assert best.count(order[0]) > 30                               # the best statement mostly wins
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})
    with app.app_context():
        entry = next(e for e in results.compute(db.session.get(Survey, 1), sources=("dummy",),
                                                     drop_speeders=True)["items"]
                     if e["item"].id == 2)
        ho = entry["maxdiff"]["holdout"]
        first = ho["rows"][0]
        n = first["hb"]["n"]                                            # without the marked dummies (as HB)
        assert n >= 40 and first["chance"] == 0.25 and first["hb"]["hit"] > 0.45
        # the logit predicts the best statement for everyone: its hit rate is the share choosing it
        assert first["pooled"]["n"] == n and first["pooled"]["hit"] == pytest.approx(first["hb"]["obs"][0])
    html = client.get("/surveys/1/results?source=dummy&drop=1").data.decode()
    assert "Vorhersage der Holdout-Sets" in html
