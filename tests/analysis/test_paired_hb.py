"""Paired comparison, stage 2: values per person by hierarchical Bayes (app/paired_hb.py), the quality criterion
"wie geraten" (app/quality.py, qc_paired) and the power from the design (app/power_choice.paired, planner)."""
import csv
import io

import numpy as np
import pytest

from app.extensions import db
from app.models import Item, Response, Survey
from tests.analysis.test_paired import setup

HB_FAST = {"HB_SYNC": True, "HB_BURN": 200, "HB_DRAWS": 200, "HB_THIN": 2}


def with_dummies(app, client, n=30, **kw):
    setup(client, **kw)
    app.config.update(HB_FAST)
    with app.app_context():
        from app import dummy

        assert dummy.generate(db.session.get(Survey, 1), n, seed=5) == n
        db.session.commit()


def test_hb_button_estimate_results_and_export(app, client, survey):
    with_dummies(app, client, answer="tie")
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert "Werte je Person schätzen (Hierarchical Bayes)" in html and "pc_hb_value1" not in client.get(
        "/surveys/1/export/data.csv?source=dummy").data.decode()
    r = client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})
    assert r.status_code == 302 and r.location.endswith("#hb-2")
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert "<strong>fertig</strong>" in html and "Mittlere Werte" in html and "Werte je Person schätzen" not in html
    with app.app_context():
        from app import paired_hb

        item = db.session.get(Item, 2)
        res = paired_hb.load(item)
        assert res["type"] == "paired" and 20 <= len(res["pids"]) <= 30 and res["pool"]["sources"] == ["dummy"]
        assert np.allclose(np.array(res["prob"]).sum(axis=1), 100, atol=0.05)
        assert np.allclose(np.array(res["util"]).mean(axis=1), 0, atol=1e-3)
        view = paired_hb.summary(res, item.kind.labels(item))
        assert [r["score"] for r in view["rows"]] == sorted((r["score"] for r in view["rows"]), reverse=True)
        true = __import__("random").Random("paired:2")
        base = [true.gauss(0, 1) for _ in range(6)]
        assert view["rows"][0]["n"] - 1 in sorted(range(6), key=lambda i: -base[i])[:2]
        assert paired_hb.current(item) is not None
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv?source=dummy").data.decode("utf-8-sig"))))
    assert rows[0]["pc_hb_value1"] != "" and rows[0]["pc_hb_rlh"] != ""
    assert sum(float(rows[0][f"pc_hb_value{i}"]) for i in range(1, 7)) == pytest.approx(100, abs=0.1)
    book = client.get("/surveys/1/export/codebook.csv?source=dummy").data.decode("utf-8-sig")
    assert "Bradley-Terry, benachbarte Kategorien" in book
    # the HB block alone, as static/hb.js fetches it
    part = client.get("/surveys/1/results?source=dummy&hb_part=2", headers={"X-Requested-With": "fetch"})
    assert part.status_code == 200 and "Mittlere Werte" in part.data.decode() and "hb-quality-2" in part.data.decode()


def test_qc_paired_marks_random_choices(app, client, survey):
    setup(client)
    with app.app_context():
        from app import quality

        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        pairs = item.config["design"]["versions"][0]
        order = {a: a for a in range(6)}
        consistent = {"v": 0, **{f"t{t}": (1 if order[a] < order[b] else -1) for t, (a, b) in enumerate(pairs)}}
        # a cycle 0 > 1 > 2 > 0 ... on every triple: alternate by the parity of the distance
        cyclic = {"v": 0, **{f"t{t}": (1 if ((b - a) % 6) in (1, 2) else -1) for t, (a, b) in enumerate(pairs)}}
        for answer in (consistent, cyclic):
            s.responses.append(Response(answers={"2": answer}, layout={"cj": {"2": {"version": 0}}}, status=Response.COMPLETED))
        db.session.commit()
        from app import export

        ctx = quality.context(s, export._items(s), s.responses)
        values = [quality.values(s, r, ctx) for r in s.responses]
        assert values[0]["qc_paired"] == 0 and values[1]["qc_paired"] == 1
        assert ("qc_paired" in [c for c, _ in quality.columns(export._items(s))])
        assert "paired" in [k for k, _, _ in quality.available(ctx)]
        assert "paired" in quality.OPT_IN                     # counted only where it was ticked


def test_power_from_the_design_and_planner(app, client, survey, monkeypatch):
    # one fixed design: the formula below holds for most random ones, but about 3 in 100 designs of 6 options and 15
    # pairs have a sum variance 14 % above it, and the test turned red now and then (test suite concept 2026-10-18, C)
    import types

    from app.items import paired as paired_item

    monkeypatch.setattr(paired_item, "secrets", types.SimpleNamespace(randbits=lambda bits: 1))
    setup(client)
    with app.app_context():
        from app import power_choice

        item = db.session.get(Item, 2)
        p = power_choice.paired(item)
        K, P = 6, 15
        assert len(p["var"]) == 6 and max(p["var"]) == pytest.approx(2 * (K - 1) ** 2 / (P * K), rel=0.05)
    page = client.get("/sample-size/?survey=1").data.decode()
    assert "Paarvergleich (Bradley-Terry)" in page and 'data-ss="pck"' in page and "Übernommen aus dem Paarvergleich" in page
    assert 'data-ss-choice-power="pairedcmp"' in page and "Bradley, R. A., &amp; Terry" in page


def test_hb_pids_follow_the_persons_with_valid_answers(app):
    """Code review 2026-10-15, C7: a person without one valid category had no sets but kept a pid, so every person after
    them got the values of the neighbour exported (and the last one an IndexError)."""
    from app import paired_hb
    from tests.analysis.test_paired import simulate

    true = [0.8, 0.0, -0.8]
    pairs = [[(0, 1), (1, 2), (2, 0)]]
    persons = simulate(true, pairs, 14, [-1, 1], seed=1)
    rows = [{"pid": f"p{p}", "a": [list(x) for x in answered]} for p, _, answered in persons]
    rows.insert(3, {"pid": "nothing", "a": [[0, 1, 0], [1, 2, 0]]})  # only "gleich", not in the form "choice"
    inp = {"key": "k", "n_items": 3, "cats": [-1, 1], "persons": rows,
           "settings": {"burn": 100, "draws": 100, "thin": 2, "seed": 1}}
    with app.app_context():
        r = paired_hb.run(inp)
    assert "nothing" not in r["pids"] and r["pids"] == [f"p{p}" for p in range(14)]
    assert len(r["pids"]) == len(r["prob"]) == len(r["rlh"])
