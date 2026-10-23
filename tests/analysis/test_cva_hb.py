"""Ratings-based conjoint, stage 2: part-worths per person by hierarchical Bayes regression (app/cva_hb.py)."""
import csv
import io
import random

import numpy as np
import pytest

from app.extensions import db
from app.models import Item, Survey
from tests.analysis.test_cva import TRUE, setup

HB_FAST = {"HB_SYNC": True, "HB_BURN": 300, "HB_DRAWS": 600, "HB_THIN": 2}


def test_gibbs_recovers_part_worths_and_borrows_for_short_answers():
    """Simulated persons around TRUE; a third answered only half of the tasks (fewer than the parameters + 1, no
    regression of their own) and still get values."""
    from app import cva_analysis, cva_design, cva_hb

    levels = [3, 3, 2]
    plan = cva_design.generate(levels, 12, "single", 1, seed=1)[0]
    rng = random.Random(3)
    persons = []
    for p in range(60):
        own = [[x + rng.gauss(0, 0.4) for x in a] for a in TRUE]
        tasks = plan[:6] if p % 3 == 0 else plan
        persons.append({"pid": f"p{p}", "x": [[1.0] + cva_analysis.effects_row(t, levels) for t in tasks],
                        "y": [5 + sum(own[a][lv] for a, lv in enumerate(t)) + rng.gauss(0, 0.6) for t in tasks]})
    inp = {"format": cva_hb.FORMAT, "type": "cva", "form": "single", "scale": [0, 10], "levels": levels,
           "persons": persons, "key": "k", "settings": {"burn": 1000, "draws": 2000, "thin": 5, "seed": 1}}
    res = cva_hb.run(inp)
    util = np.array(res["util"])
    assert util.shape == (60, 8) and np.allclose(util[:, :3].sum(axis=1), 0, atol=1e-3)
    assert util.mean(axis=0)[[0, 2, 3, 5, 6]] == pytest.approx([1.5, -1.5, 1.0, -1.2, 0.5], abs=0.3)
    assert np.mean(res["constant"]) == pytest.approx(5.0, abs=0.3)
    assert np.allclose(np.array(res["importance"]).sum(axis=1), 1, atol=1e-3)
    assert cva_hb.draw_parameters(res).shape[1:] == (60, 8)
    assert res["util"][0][0] > 0.5                                   # a person with six answers, from the prior and the others


def test_button_estimate_results_simulator_export_and_methods(app, client, survey):
    setup(client)
    app.config.update(HB_FAST)
    with app.app_context():
        from app import dummy

        assert dummy.generate(db.session.get(Survey, 1), 25, seed=5) == 25
        db.session.commit()
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert "Teilnutzen je Person schätzen (Hierarchical Bayes)" in html
    r = client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})
    assert r.status_code == 302 and r.location.endswith("#hb-2")
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert "<strong>fertig</strong>" in html and "Teilnutzen je Person (Hierarchical Bayes)" in html
    assert "Lenk, P. J." in html and "Grundlage: die HB-Teilnutzen" in html
    with app.app_context():
        from app import cva_hb

        item = db.session.get(Item, 2)
        res = cva_hb.load(item)
        assert res["type"] == "cva" and 15 <= len(res["pids"]) <= 25 and cva_hb.current(item) is not None
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv?source=dummy").data.decode("utf-8-sig"))))
    assert rows[0]["wo_hb_miete_1"] != "" and rows[0]["wo_hb_r2"] != "" and rows[0]["wo_hb_constant"] != ""
    book = client.get("/surveys/1/export/codebook.csv?source=dummy").data.decode("utf-8-sig")
    assert "Hierarchical-Bayes-Regression" in book
    part = client.get("/surveys/1/results?source=dummy&hb_part=2", headers={"X-Requested-With": "fetch"})
    assert part.status_code == 200 and "hb-quality-2" in part.data.decode()
    methods = client.get("/surveys/1/methods?source=dummy").data.decode()
    assert "Hierarchical-Bayes-Regression" in methods
