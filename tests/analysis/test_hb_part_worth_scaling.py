"""HB part-worths of CBC and ACBC rescaled per person (conjoint_hb.diff_factors, SCALE_KEY): only a display of the stored
result, chosen on the results page; the ranges of a person's attributes then sum to 100 x number of attributes."""
import csv
import io
from types import SimpleNamespace

import numpy as np
import pytest

from app.extensions import db
from app.models import Item
from tests.analysis.test_conjoint_hb import HB_FAST, simulate_segments
from tests.analysis.test_cbc_results import setup


def test_known_example():
    """A person with 4 attributes of 3 levels and the constant: factor 400 / 8.8593."""
    from app.conjoint_hb import diff_factors

    u = [-1.2365, -0.8631, 2.0996, -1.0410, 0.6706, 0.3704, -0.7590, 0.1056, 0.6533, 0.9883, -1.4110, 0.4227]
    res = {"util": [u, [0.0] * 12], "spans": [[0, 0, 3], [1, 3, 3], [2, 6, 3], [3, 9, 3]], "pids": ["a", "b"],
           "price": None, "asc": [0.2956, 0.0]}
    f = diff_factors(res)
    assert np.round(np.array(u) * f[0], 2).tolist() == [-55.83, -38.97, 94.80, -47.00, 30.28, 16.72, -34.27, 4.77,
                                                         29.50, 44.62, -63.71, 19.09]
    assert round(0.2956 * f[0], 2) == 13.35
    assert np.isnan(f[1])                                            # no range at all: left out


def test_ranges_sum_to_100_per_attribute_and_importances_stay():
    from app.conjoint_hb import diff_factors, importances

    rng = np.random.default_rng(3)
    n = 30
    util = np.column_stack([rng.normal(size=(n, 3)) - 0, rng.normal(size=(n, 2))])
    util[:, :3] -= util[:, :3].mean(axis=1, keepdims=True)
    util[:, 3:] -= util[:, 3:].mean(axis=1, keepdims=True)
    price = rng.normal(-0.02, 0.01, n)
    width = 150.0 - 50.0
    ranges = np.column_stack([np.ptp(util[:, :3], axis=1), np.ptp(util[:, 3:], axis=1), np.abs(price) * width])
    res = {"util": util.tolist(), "spans": [[0, 0, 3], [1, 3, 2]], "pids": list(range(n)), "price": price.tolist(),
           "price_range": [50.0, 150.0], "has_price_importance": True, "asc": None, "loose": [],
           "importance": (ranges / ranges.sum(axis=1, keepdims=True)).tolist()}
    f = diff_factors(res)
    scaled = ranges * f[:, None]
    assert np.allclose(scaled.sum(axis=1), 100 * 3)
    # the importances of the rescaled values are those of the raw ones
    assert np.allclose(scaled / scaled.sum(axis=1, keepdims=True), importances(res))


def test_switch_on_the_results_page_export_and_key(app, client, survey):
    setup(client, price_attr=True, none=True, tasks=12, versions=6)
    app.config.update(HB_FAST)
    simulate_segments(app, 40, none_asc=-0.5)
    client.post("/surveys/1/results/hb/2", data={"source": "real", "speeder": "50"})
    html = client.get("/surveys/1/results/analysis/2").data.decode()
    assert 'name="scale"' in html and "Rohwerte (Logit-Skala)" in html
    # rescaled is the default since 2026-11-24
    assert '<option value="diffs" selected>' in html and "normiert (Spannweiten je Person; Standard)" in html
    assert "Normiert: Alle Nutzen einer Person" in html
    fetch = {"X-Requested-With": "fetch"}
    r = client.post("/surveys/1/results/hb/2/scale", data={"source": "real", "scale": "raw"}, headers=fetch)
    assert r.status_code == 200 and r.get_json()["ok"]
    html = client.get("/surveys/1/results/analysis/2").data.decode()
    assert '<option value="raw" selected>' in html and "Normiert: Alle Nutzen einer Person" not in html
    assert "cbc_hb_norm_" not in client.get("/surveys/1/export/data.csv").data.decode()
    with app.app_context():
        from app import conjoint_hb

        item = db.session.get(Item, 2)
        assert item.config["hb_scale"] == "raw" and conjoint_hb.scale_of(item) == "raw"
        before = conjoint_hb.load(item)
        raw_view = conjoint_hb.summary(item, before, "de")
    r = client.post("/surveys/1/results/hb/2/scale", data={"source": "real", "scale": "diffs"}, headers=fetch)
    assert r.status_code == 200 and r.get_json()["ok"]
    with app.app_context():
        from app import conjoint_hb

        item = db.session.get(Item, 2)
        assert "hb_scale" not in item.config and conjoint_hb.scale_of(item) == "diffs"
        # only a display: the stored result, its key and its being current stay as they were
        after = conjoint_hb.load(item)
        assert after["key"] == before["key"] and after["created"] == before["created"]
        assert conjoint_hb.current(item) is not None
        view = conjoint_hb.summary(item, after, "de")
        d = view["diffs"]
        assert d["persons"] + d["left_out"] == len(after["pids"]) and d["intervals"]
        assert [a["importance"] for a in view["attributes"]] == [a["importance"] for a in raw_view["attributes"]]
        # the mean of the rescaled values is the mean over the persons of factor x raw value
        f = conjoint_hb.diff_factors(after)
        ok = np.isfinite(f)
        util = np.array(after["util"])[ok] * f[ok, None]
        assert d["attributes"][0]["levels"][0]["mean"] == pytest.approx(util[:, 0].mean())
        assert all(lv["lo"] <= lv["mean"] <= lv["hi"] for a in d["attributes"] for lv in a["levels"] if lv["lo"] is not None)
    html = client.get("/surveys/1/results/analysis/2").data.decode()
    assert '<option value="diffs" selected>' in html and "Normiert: Alle Nutzen einer Person" in html
    # export: the rescaled columns next to the raw ones; every person's ranges sum to 100 x attributes
    rows = list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv").data.decode("utf-8-sig"))))
    cols = list(rows[0])
    norm = [c for c in cols if c.startswith("cbc_hb_norm_")]
    assert "cbc_hb_look_1" in cols and "cbc_hb_norm_look_1" in norm and "cbc_hb_norm_none" in norm
    with app.app_context():
        from app import conjoint_hb

        item = db.session.get(Item, 2)
        res = conjoint_hb.load(item)
        attrs = item.kind.attributes(item)
        spans = [(attrs[a]["code"], n) for a, _, n in res["spans"]]
        width = res["price_range"][1] - res["price_range"][0] if res.get("has_price_importance") else None
    for row in (r for r in rows if r.get("cbc_hb_norm_look_1")):
        total = sum(np.ptp([float(row[f"cbc_hb_norm_{code}_{lv + 1}"]) for lv in range(n)]) for code, n in spans)
        if width is not None:
            total += abs(float(row["cbc_hb_norm_price"])) * width
        assert total == pytest.approx(100 * (len(spans) + (width is not None)), abs=0.1)
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "Spannweiten je Person summieren sich zu 100" in book
    # the value "diffs" stored before the new default is read as it
    with app.app_context():
        from app import conjoint_hb

        item = db.session.get(Item, 2)
        item.config = {**item.config, "hb_scale": "diffs"}
        db.session.commit()
        assert conjoint_hb.scale_of(db.session.get(Item, 2)) == "diffs"


def test_scale_route_checks(app, client, survey):
    setup(client)
    assert client.post("/surveys/1/results/hb/999/scale", data={"scale": "diffs"}).status_code == 404
    client.post("/surveys/1/results/hb/2/scale", data={"scale": "other"})
    with app.app_context():
        assert "hb_scale" not in db.session.get(Item, 2).config


def test_rescaled_table_in_the_report(app):
    from app import results_pdf
    from tests.exports.test_results_pdf import _flow_text

    level = {"mean": 0.6, "lo": 0.5, "hi": 0.7, "sd": 0.4, "q1": 0.3, "q3": 0.9, "best": 0.55}
    scaled = {**level, "mean": 61.5, "lo": 55.0, "hi": 68.0, "sd": 12.0, "q1": 50.0, "q3": 70.0}
    attr = {"label": "Kommunikator", "importance": 0.6, "importance_sd": 0.1, "importance_q": [0.5, 0.6, 0.7]}
    hb = {"persons": 40, "type": "conjoint", "converged": True,
          "attributes": [{**attr, "levels": [{**level, "label": "Wissenschaftler"}]}], "price": None, "asc": None,
          "diffs": {"attributes": [{**attr, "levels": [{**scaled, "label": "Wissenschaftler"}]}], "price": None,
                    "asc": None, "persons": 38, "left_out": 2, "intervals": True}}
    item = SimpleNamespace(config={"hb_scale": "diffs"})
    with app.app_context():
        rep = results_pdf.Report(None, "de")
        text = " ".join(_flow_text(results_pdf._cbc(rep, {"cbc": {"hb": {"result": hb}}, "item": item}, "cbc")).split())
        raw = " ".join(_flow_text(results_pdf._cbc(results_pdf.Report(None, "de"),
                                                   {"cbc": {"hb": {"result": hb}},
                                                    "item": SimpleNamespace(config={"hb_scale": "raw"})},
                                                   "cbc")).split())
    assert "cbc: Teilnutzen normiert (Hierarchical Bayes)" in text and "61,50" in text and "[55,00; 68,00]" in text
    assert "100 × Zahl der Merkmale" in text and "Ohne 2 Personen" in text
    assert "cbc: Teilnutzen (Hierarchical Bayes)" in raw and "0,60" in raw and "normiert" not in raw
