import csv
import io
import math
import os
import random

import numpy as np

from app.extensions import db
from app.models import Item, Response, Survey, new_pid
from tests.analysis import test_acbc_results as acbc
from tests.analysis.test_cbc_results import PRICES, setup

HB_FAST = {"HB_SYNC": True, "HB_BURN": 400, "HB_DRAWS": 400, "HB_THIN": 2}
LOOK = ([1.5, 0.0, -1.5], [-1.5, 0.0, 1.5])   # two segments with opposite taste for "look"
PRIVACY = [0.6, -0.6]                         # the same for everyone


def simulate_segments(app, n, price_beta=-0.012, none_asc=None, seed=7):
    """n persons, alternately of two segments; returns the segment per pid."""
    rng = random.Random(seed)
    segments = {}
    with app.app_context():
        s = db.session.get(Survey, 1)
        item = db.session.get(Item, 2)
        kind, c = item.kind, item.config
        codes = [a["code"] for a in c["attributes"]]

        def gumbel():
            return -math.log(-math.log(rng.random()))

        for p in range(n):
            seg = p % 2

            def utility(profile):
                u = 0.0
                for code, lv in zip(codes, profile):
                    u += {"look": LOOK[seg][lv], "privacy": PRIVACY[lv] if lv < 2 else 0,
                          "price": (PRICES[lv] - PRICES[1]) * price_beta}[code]
                return u

            v = rng.randrange(len(c["design"]["versions"]))
            answer = {}
            for t in range(c["tasks"]):
                u = [utility(kind.profile(item, v, t, a)) + gumbel() for a in range(c["alternatives"])]
                if none_asc is not None:
                    u.append(none_asc + gumbel())
                best = max(range(len(u)), key=u.__getitem__)
                answer[f"t{t}"] = 0 if best == c["alternatives"] else best + 1
            pid = new_pid(s.code)
            segments[pid] = seg
            s.responses.append(Response(answers={"2": answer}, layout={"cj": {"2": {"version": v, "order": []}}},
                                        status=Response.COMPLETED, pid=pid))
        db.session.commit()
    return segments


def wide(client, query=""):
    return list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv" + query).data.decode("utf-8-sig"))))


def test_cbc_button_estimate_recovers_segments_and_exports(app, client, survey):
    setup(client, price_attr=True, none=True, tasks=12, versions=6)
    app.config.update(HB_FAST)
    segments = simulate_segments(app, 80, none_asc=-0.5)
    html = client.get("/surveys/1/results").data.decode()
    assert "Individuelle Nutzen (Hierarchical Bayes)" in html and "Individuelle Nutzen schätzen (Hierarchical Bayes)" in html
    assert "cbc_hb_look_1" not in client.get("/surveys/1/export/data.csv").data.decode()      # nothing yet
    r = client.post("/surveys/1/results/hb/2", data={"source": "real", "speeder": "50"})
    assert r.status_code == 302 and r.location.endswith("#hb-2")
    with app.app_context():
        from app import conjoint_hb

        item = db.session.get(Item, 2)
        res = conjoint_hb.load(item)
        assert res["type"] == "conjoint" and len(res["pids"]) == 80 and res["settings"]["burn"] == 400
        assert os.path.exists(os.path.join(app.config["UPLOAD_FOLDER"], "hb", item.block.survey.uid, "item_2.json"))
        util = np.array(res["util"])
        spans = {a: (start, n) for a, start, n in res["spans"]}
        assert sorted(spans) == [0, 2]                                         # price (attribute 1) is linear
        for start, n in spans.values():                                        # zero-centred per attribute
            assert np.allclose(util[:, start:start + n].sum(axis=1), 0, atol=1e-3)
        # heterogeneity is found: "humanoid" (look level 1) high in segment 0, low in segment 1
        seg = np.array([segments[pid] for pid in res["pids"]])
        look0 = util[:, spans[0][0]]
        assert look0[seg == 0].mean() > 0.6 and look0[seg == 1].mean() < -0.6
        assert ((look0 > 0) == (seg == 0)).mean() > 0.85
        assert np.corrcoef(look0, np.where(seg == 0, 1.5, -1.5))[0, 1] > 0.8
        # everyone agrees on privacy: positive for (almost) all, and less spread than the look
        privacy0 = util[:, spans[2][0]]
        assert (privacy0 > 0).mean() > 0.9 and privacy0.std() < look0.std()
        imp = np.array(res["importance"])
        assert imp.shape == (80, 3) and np.allclose(imp.sum(axis=1), 1, atol=1e-3)
        assert np.mean(res["price"]) < 0 and res["asc"] is not None
        view = conjoint_hb.summary(item, {**res}, "de")
        look = view["attributes"][0]
        # the mean over both segments is near 0, the spread is what differs from the aggregate model
        assert abs(look["levels"][0]["mean"]) < 0.5 and look["levels"][0]["sd"] > 0.8
        assert abs(sum(lv["best"] for lv in look["levels"]) - 1) < 1e-9
        assert look["importance"] > view["attributes"][1]["importance"]         # look matters most per person
    html = client.get("/surveys/1/results").data.decode()
    assert "<strong>fertig</strong>" in html and "Individuelle Nutzen schätzen (Hierarchical Bayes)" not in html
    assert "hb-utilities" in html and "md-rlh-chart" in html and "bevorzugt" in html and "Akzeptanzrate" in html
    import re

    bars = re.search(r'<ul class="plain res-bars cm-importance-bars hb-importance">(.*?)</ul>', html, re.S).group(1)
    shares = [float(x.replace(",", ".")) for x in re.findall(r'<span class="res-bar-n"[^>]*>(\d+,\d)\s*%', bars)]
    assert len(shares) == 3 and shares == sorted(shares, reverse=True)          # most important first, one decimal
    # the spread of the people: SD beside the share, quartiles only in the table, no line on the bar (Ralf, 2026-10-26)
    assert "res-ci-bar" not in bars and bars.count(">SD ") == 3
    table = html.split('class="res-table hb-importance-table"', 1)[1].split("</table>", 1)[0]
    assert "<th class=\"num\">Q1</th>" in table and table.count("<tr>") == 4
    assert all(q[0] <= q[1] <= q[2] for q in [a["importance_q"] for a in view["attributes"]] if q)
    assert "(oft der Preis), etwas wichtiger" in html                          # H-12: the convention's bias
    # C-2: prices 50 to 150, coefficient per 10 currency units in both blocks (was per unit, 4 decimals)
    assert html.count("Preiskoeffizient je 10 Währungseinheiten") == 2
    # the linear price also as bars in the HB block, as in the aggregate model (Ralf, 2026-10-22)
    hb_block = html.split("hb-importance", 1)[1]
    assert "die Balken zeigen den mittleren Koeffizienten" in hb_block
    # H-10: the method page describes the experiment, its estimation and the HB run, with full sources
    page = client.get("/surveys/1/methods").data.decode()
    assert "Wahlexperimente" in page and "Choice-Based Conjoint (CBC) mit 3 Merkmalen (3/3/2 Stufen), 12 Auswahlaufgaben" in page
    assert "n = 80 Personen" in page and "per Hierarchical Bayes geschätzt" in page
    assert "McFadden, D. (1974). Conditional logit analysis" in page and "Allenby, G. M., &amp; Rossi" in page
    assert page.count('<details class="sources">') == 1                       # one fold of sources per page
    rows = wide(client)
    assert len(rows) == 80
    for col in ("cbc_hb_look_1", "cbc_hb_look_3", "cbc_hb_privacy_2", "cbc_hb_none", "cbc_hb_price",
                "cbc_hb_imp_look", "cbc_hb_imp_privacy", "cbc_hb_imp_price", "cbc_hb_rlh"):
        assert rows[0][col] not in ("", None), col
    assert "cbc_hb_price_1" not in rows[0]                                      # the linear price has no levels
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "cbc_hb_look_1" in book and "Hierarchical Bayes (HB-MNL, Auswahlaufgaben" in book
    # a new participation makes the result stale: no longer shown and no longer exported
    simulate_segments(app, 1, seed=99)
    html = client.get("/surveys/1/results").data.decode()
    assert "keine aktuelle HB-Schätzung" in html and "Individuelle Nutzen schätzen (Hierarchical Bayes)" in html
    assert "hb-utilities" not in html and "md-rlh-chart" not in html             # the old values are gone
    assert "cbc_hb_look_1" not in client.get("/surveys/1/export/data.csv").data.decode()


def test_acbc_hb_with_price_and_threshold(app, client, survey):
    acbc.setup(client, screens=5, per_screen=4, price=True)
    app.config.update(HB_FAST)
    with app.app_context():
        acbc.add_responses(db.session.get(Survey, 1), acbc.simulate(db.session.get(Item, 2), 60, price_beta=-0.01))
    assert "Individuelle Nutzen schätzen (Hierarchical Bayes)" in client.get("/surveys/1/results").data.decode()
    assert client.post("/surveys/1/results/hb/2", data={"source": "real", "speeder": "50"}).status_code == 302
    with app.app_context():
        from app import conjoint_hb

        item = db.session.get(Item, 2)
        res = conjoint_hb.load(item)
        assert res["type"] == "acbc" and len(res["pids"]) == 60 and len(res["spans"]) == 4
        util = np.array(res["util"])
        for _, start, n in res["spans"]:
            assert np.allclose(util[:, start:start + n].sum(axis=1), 0, atol=1e-3)
        look = util[:, res["spans"][0][1]:res["spans"][0][1] + 3].mean(axis=0)
        assert look[0] > 0.3 and look[2] < -0.3                                 # TRUE look: 0.8, 0, -0.8
        assert np.mean(res["price"]) < 0 and res["asc"] is not None
        assert np.array(res["importance"]).shape == (60, 5)                     # four attributes and the price
        assert conjoint_hb.current(item) is not None
    html = client.get("/surveys/1/results").data.decode()
    assert "<strong>fertig</strong>" in html and "Schwelle „kommt nicht infrage“: Mittel" in html
    row = wide(client)[0]
    for col in ("robo_hb_look_1", "robo_hb_voice_3", "robo_hb_threshold", "robo_hb_price", "robo_hb_imp_look",
                "robo_hb_imp_price", "robo_hb_rlh"):
        assert row[col] not in ("", None), col


def test_hb_route_only_for_choice_models(app, client, survey):
    from tests.conftest import add, fill_consent

    fill_consent(client)
    add(client, "single_choice")                                                # item 2: no HB model
    assert client.post("/surveys/1/results/hb/2", data={"source": "real"}).status_code == 404
    assert client.post("/surveys/1/results/hb/999", data={"source": "real"}).status_code == 404


def test_no_data_no_button(app, client, survey):
    setup(client)
    html = client.get("/surveys/1/results").data.decode()
    assert "Individuelle Nutzen schätzen (Hierarchical Bayes)" not in html
    r = client.post("/surveys/1/results/hb/2", data={"source": "real"}, follow_redirects=True)
    assert "fehlen Daten (mindestens 10 Personen" in r.data.decode()        # H-13: was "zwei", hb.MIN_PERSONS is 10


def test_display_no_minus_zero_and_rlh_legend(app, client, survey):
    from flask import render_template_string

    setup(client, tasks=6, versions=2)
    app.config.update({**HB_FAST, "HB_BURN": 60, "HB_DRAWS": 60})
    simulate_segments(app, 12)
    client.post("/surveys/1/results/hb/2", data={"source": "real", "speeder": "50"})
    html = client.get("/surveys/1/results").data.decode()
    assert "−0,00" not in html and "-0,00" not in html                 # a value that rounds to zero has no sign
    assert 'class="md-rlh-legend' in html and ">Personen</text>" in html
    assert "hb-c-range" in html and "hb-importance" in html
    assert "steigt die individuelle Wichtigkeit mit der Zahl der Stufen" in html
    with app.test_request_context():
        # the shared formatter of the results partials (_macros.html): what shows as zero has no sign
        out = render_template_string(
            '{% from "_macros.html" import signed %}'
            "{{ signed(-0.001) }} {{ signed(0.004) }} {{ signed(-0.02) }} {{ signed(0.4, 0) }} {{ signed(-0.4, 0) }} {{ signed(1.5) }}")
        assert out == "0,00 0,00 −0,02 0 0 +1,50", out


def test_hb_buttons_work_in_place(app, client, survey):
    """Ralf, 2026-10-05: the HB buttons do not load the page again; hb.js sends them in the background and fetches the
    HB block of the question alone (hb_part)."""
    setup(client, tasks=6, versions=2)
    app.config.update({**HB_FAST, "HB_BURN": 60, "HB_DRAWS": 60})
    simulate_segments(app, 12)
    html = client.get("/surveys/1/results").data.decode()
    assert 'class="hb-part"' in html and "data-hb-form" in html and "hb.js" in html
    fetch = {"X-Requested-With": "fetch"}
    r = client.post("/surveys/1/results/hb/2", data={"source": "real"}, headers=fetch)
    assert r.status_code == 200 and r.is_json and r.json["state"] == "started" and r.json["ok"]
    part = client.get("/surveys/1/results?hb_part=2", headers=fetch)
    body = part.data.decode()
    assert part.status_code == 200 and body.strip().startswith('<section class="hb-part"') and "<strong>fertig</strong>" in body
    assert "<html" not in body and "tabpage" not in body                   # the block alone, not the page
    again = client.post("/surveys/1/results/hb/2", data={"source": "real"}, headers=fetch)
    assert again.json["state"] == "current" and "passt schon" in again.json["message"]
    # without the script (no header): as before, back to the page
    assert client.post("/surveys/1/results/hb/2", data={"source": "real"}).status_code == 302
    assert client.get("/surveys/1/results?hb_part=999", headers=fetch).status_code == 404


def test_running_estimation_shows_only_its_bar(app, client, survey):
    """Ralf, 2026-10-05: while an HB estimation runs, the old results, the convergence note and its button are hidden."""
    from app import conjoint_hb

    setup(client, tasks=6, versions=2)
    app.config.update({**HB_FAST, "HB_BURN": 60, "HB_DRAWS": 60})
    simulate_segments(app, 12)
    client.post("/surveys/1/results/hb/2", data={"source": "real"})
    with app.app_context():
        item = db.session.get(Item, 2)
        conjoint_hb.maxdiff_hb._write(conjoint_hb.maxdiff_hb._paths(item)[1], {   # a run in progress (its marker)
            "state": "running", "started": conjoint_hb.maxdiff_hb._now().isoformat(),
            "beat": conjoint_hb.maxdiff_hb._now().isoformat(), "progress": 0.3, "key": "x", "pool": {}})
    part = client.get("/surveys/1/results?hb_part=2", headers={"X-Requested-With": "fetch"}).data.decode()
    assert "data-hb-running" in part and "Schätzung der individuellen Nutzen läuft" in part
    assert "hb-utilities" not in part and "hb-conv-note" not in part and "Neu schätzen mit doppelter Länge" not in part
    assert "<strong>fertig</strong>" not in part and "Wichtigkeit der Merkmale" not in part


def test_hb_importances_leave_out_loose_attributes():
    """As in the aggregate model: an attribute whose values only the prior sets gets no importance, the others are
    shares among themselves, on the page and in the export (audit data storage and calculations 2026-10-21, D4)."""
    from app import conjoint_hb

    res = {"importance": [[0.5, 0.3, 0.2], [0.2, 0.2, 0.6]], "loose": [1], "spans": [[0, 0, 2], [1, 2, 3], [2, 5, 2]],
           "has_price_importance": False}
    imp = conjoint_hb.importances(res)
    assert np.isnan(imp[:, 1]).all()
    assert np.allclose(imp[:, [0, 2]], [[0.5 / 0.7, 0.2 / 0.7], [0.2 / 0.8, 0.6 / 0.8]])
    assert np.allclose(conjoint_hb.importances({**res, "loose": []}), res["importance"])  # nothing loose: as stored
    # with the price as a column of its own: it stays among the shares
    priced = {**res, "importance": [[0.4, 0.2, 0.2, 0.2]], "has_price_importance": True}
    assert np.allclose(conjoint_hb.importances(priced)[0, [0, 2, 3]], [0.5, 0.25, 0.25])
