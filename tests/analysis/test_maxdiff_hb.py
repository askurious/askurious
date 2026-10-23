"""Hierarchical Bayes for MaxDiff: engine (app/hb.py), storage and page (app/maxdiff_hb.py), export columns."""
import csv
import io
import math
import os
import random
import threading
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.extensions import db
from app.models import Item, Survey
from tests.analysis.test_maxdiff import setup

TRUE = [1.2, 0.8, 0.4, 0.1, -0.1, -0.3, -0.7, -1.4]


def simulate(persons, seed, person_sd=0.8, random_share=0.0, threshold=None):
    """Heterogeneous best-worst answers: individual utilities TRUE + N(0, person_sd), Gumbel noise per choice.
    A share of the persons clicks at random. Returns versions, answers, anchors, individual utilities."""
    from app.maxdiff_design import generate

    rng = random.Random(seed)
    versions = generate(8, 4, 6, 3, seed=3)

    def gumbel():
        return -math.log(-math.log(max(rng.random(), 1e-12)))

    answers, anchors, utils = [], [], []
    for p in range(persons):
        v = p % len(versions)
        clicker = p < random_share * persons
        u = [x + rng.gauss(0, person_sd) for x in TRUE]
        tasks = {}
        for t, s in enumerate(versions[v]):
            if clicker:
                b, w = rng.sample(list(s), 2)
            else:
                b = max(s, key=lambda i: u[i] + gumbel())
                w = max((i for i in s if i != b), key=lambda i: -u[i] + gumbel())
            tasks[t] = (b, w)
        answers.append((v, tasks))
        if threshold is not None:
            anchors.append([int(u[i] + gumbel() > threshold + gumbel()) for i in range(8)])
        utils.append(u)
    return versions, answers, anchors, np.array(utils)


def person_sets(versions, answers, anchors=None):
    from app.maxdiff_analysis import anchor_sets, parameters, task_sets

    est, col, k, thr = parameters(8, versions, answers, bool(anchors))
    sets = [task_sets(versions, v, t, col, k) + (anchor_sets(anchors[n], est, col, k, thr) if anchors else [])
            for n, (v, t) in enumerate(answers)]
    M = np.zeros((8, k))
    M[np.arange(7), np.arange(7)] = 1.0
    M[:, :7] -= 1 / 8
    return sets, k, M


def test_hb_recovers_individual_and_mean_utilities():
    from app import hb

    versions, answers, _, U = simulate(150, seed=4)
    sets, k, M = person_sets(versions, answers)
    r = hb.estimate(sets, k, burn=1500, draws=1500, thin=5, seed=4, prior_cov=np.eye(k) + 1)
    assert r["persons"] == 150 and r["kept"] == 300 and r["sets"] == 150 * 12
    assert hb.ACCEPT_OK[0] < r["acceptance"] < hb.ACCEPT_OK[1]           # step sizes adapted towards hb.TARGET
    u = r["beta"] @ M.T
    true = U - U.mean(axis=1, keepdims=True)
    assert np.abs(u.mean(axis=0) - true.mean(axis=0)).max() < 0.3         # mean utilities
    corr = [np.corrcoef(u[i], true[i])[0, 1] for i in range(150)]
    assert np.mean(corr) > 0.75                                           # individual utilities
    # individual estimates beat the pooled one for most persons
    pooled = np.tile(u.mean(axis=0), (150, 1))
    assert np.mean(((u - true) ** 2).sum(axis=1) < ((pooled - true) ** 2).sum(axis=1)) > 0.6
    assert np.all(r["sigma_diag"] > 0) and r["checked"] and r["converged"]
    assert r["ll_trace"].shape == (300,) and r["loglik"] < 0


def test_hb_reproducible_with_seed():
    from app import hb

    versions, answers, _, _ = simulate(30, seed=8)
    sets, k, _ = person_sets(versions, answers)
    a = hb.estimate(sets, k, burn=150, draws=150, thin=3, seed=7)
    b = hb.estimate(sets, k, burn=150, draws=150, thin=3, seed=7)
    c = hb.estimate(sets, k, burn=150, draws=150, thin=3, seed=8)
    assert np.array_equal(a["beta"], b["beta"]) and a["acceptance"] == b["acceptance"]
    assert not np.array_equal(a["beta"], c["beta"])


def test_rlh_flags_random_clickers():
    from app import hb

    versions, answers, _, _ = simulate(80, seed=9, person_sd=0.5, random_share=0.25)
    sets, k, _ = person_sets(versions, answers)
    r = hb.estimate(sets, k, burn=600, draws=600, thin=3, seed=1)
    rlh, chance = r["rlh"], r["chance_rlh"]
    assert np.allclose(chance, math.sqrt(1 / 4 * 1 / 3))                  # best among 4, worst among 3
    assert np.all((rlh > 0) & (rlh < 1))
    clickers, others = rlh[:20], rlh[20:]
    assert clickers.mean() < others.mean()
    assert np.mean(clickers < 1.2 * chance[:20]) > np.mean(others < 1.2 * chance[20:])
    # RLH at utilities of zero is exactly the chance level
    assert np.allclose(hb.rlh(hb.Data(sets, k), np.zeros((80, k))), chance)


def test_geweke_check():
    from app import hb

    rng = np.random.default_rng(0)
    assert abs(hb.geweke(rng.standard_normal(400))) < 3
    assert abs(hb.geweke(np.linspace(0, 5, 400) + rng.standard_normal(400))) > 5   # still drifting
    assert hb.geweke(np.ones(10)) is None


def test_inverse_wishart_mean():
    from app import hb

    rng = np.random.default_rng(1)
    scale = np.array([[2.0, 0.5], [0.5, 1.0]])
    draws = np.array([hb._inv_wishart(rng, 10, scale) for _ in range(4000)])
    assert np.allclose(draws.mean(axis=0), scale / (10 - 2 - 1), atol=0.03)


# --- app: button, storage, stale results, export --------------------------------------------------------

HB_FAST = {"HB_SYNC": True, "HB_BURN": 200, "HB_DRAWS": 200, "HB_THIN": 2}


def with_dummies(app, client, n=40, **kw):
    setup(client, anchor=True, versions=2, **kw)
    app.config.update(HB_FAST)
    with app.app_context():
        from app import dummy

        assert dummy.generate(db.session.get(Survey, 1), n, seed=5) == n
        db.session.commit()


def wide(client, query="?source=dummy"):
    return list(csv.DictReader(io.StringIO(client.get("/surveys/1/export/data.csv" + query).data.decode("utf-8-sig"))))


def test_results_button_estimate_and_export(app, client, survey):
    with_dummies(app, client)
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert "Individuelle Nutzen (Hierarchical Bayes)" in html and "Individuelle Nutzen schätzen (Hierarchical Bayes)" in html
    assert "md_hb_util1" not in client.get("/surveys/1/export/data.csv?source=dummy").data.decode()   # nothing yet
    r = client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})
    assert r.status_code == 302 and "source=dummy" in r.location and r.location.endswith("#hb-2")
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert "<strong>fertig</strong>" in html and "Individuelle Nutzen schätzen (Hierarchical Bayes)" not in html
    assert "md-boxes" in html and "md-rlh-chart" in html and "Top 3" in html and "mögliche Zufallsklicker" in html
    assert "Posterior-Intervall" in html and "Akzeptanzrate" in html and "Konvergenz" in html
    assert html.count("md-threshold") >= 3                                   # MNL, HB bars, HB boxes (+ RLH)
    # the threshold assumes one scale of anchor and best-worst choices (audit 2026-10-06, L-F1): MNL and HB part
    assert html.count("auf derselben Skala liegen") == 2
    with app.app_context():
        from app import maxdiff_hb

        item = db.session.get(Item, 2)
        res = maxdiff_hb.load(item)
        # as the results page: without what quality control marks (Q-9), whatever the form sends
        assert res["settings"] == {"burn": 200, "draws": 200, "thin": 2, "seed": 1} and res["pool"]["drop"]
        assert res["pool"]["sources"] == ["dummy"] and res["anchored"]
        assert os.path.exists(os.path.join(app.config["UPLOAD_FOLDER"], "hb", item.block.survey.uid, "item_2.json"))
        assert np.allclose(np.array(res["prob"]).sum(axis=1), 100, atol=0.05)
        assert np.allclose(np.array(res["util"]).mean(axis=1), 0, atol=1e-3)
        view = maxdiff_hb.summary(res, item.kind.labels(item))
        assert [r["score"] for r in view["rows"]] == sorted((r["score"] for r in view["rows"]), reverse=True)
        assert sum(r["top"] for r in view["rows"]) == pytest.approx(3)       # three statements per person
        # Top 3 and "über Schwelle" over the kept draws per person (audit 2026-10-06, L-3)
        top, above = maxdiff_hb._draw_shares(res, len(res["est"]))
        assert view["shares_from_draws"] and above is not None
        by_n = {i + 1: j for j, i in enumerate(res["est"])}
        assert all(r["top"] == pytest.approx(top[by_n[r["n"]]]) and r["above"] == pytest.approx(above[by_n[r["n"]]])
                   for r in view["rows"])
        assert all(r["lo"] <= r["score"] <= r["hi"] for r in view["rows"])
    rows = wide(client)
    assert len(rows) == 40 and len(res["pids"]) == sum(r["qc_marked"] != "1" for r in rows) < 40
    for row in (r for r in rows if r["qc_marked"] != "1"):
        probs = [float(row[f"md_hb_prob{i}"]) for i in range(1, 9)]
        assert sum(probs) == pytest.approx(100, abs=0.05)
        assert 0 < float(row["md_hb_rlh"]) < 1 and row["md_hb_util_anchor"] and row["md_hb_prob_anchor"]
    cols = list(rows[0])
    assert cols.index("md_hb_util1") > cols.index("md_anchor8") and cols[cols.index("md_hb_rlh") - 1] == "md_hb_prob_anchor"
    # other data pool without any of the estimated persons: no HB columns (audit 2026-10-05, S23; they were there, empty)
    assert wide(client, "?source=real")[:1] == [] and "md_hb_rlh" not in client.get("/surveys/1/export/data.csv?source=real").data.decode()
    book = client.get("/surveys/1/export/codebook.csv?source=dummy").data.decode("utf-8-sig")
    line = next(l for l in book.splitlines() if l.startswith("md_hb_prob1"))
    assert "Hierarchical Bayes" in line and "200 Burn-in" in line and "Seed 1" in line and "Dummy-Daten" in line
    assert "Messniveau metrisch" in line
    import zipfile

    z = zipfile.ZipFile(io.BytesIO(client.get("/surveys/1/export/spss.zip?source=dummy").data))
    sps = z.read(next(n for n in z.namelist() if n.endswith(".sps"))).decode("utf-8-sig")
    import re

    assert re.search(r"md_hb_prob1 F\d+\.[1-4]\b", sps) and "md_hb_rlh" in sps.split("(SCALE)")[0].split("VARIABLE LEVEL")[-1]
    sav = client.get("/surveys/1/export/data.sav?source=dummy")
    assert sav.status_code == 200 and b"md_hb_rlh" in sav.data


def test_stale_result_after_new_data(app, client, survey):
    with_dummies(app, client, n=20)
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})
    r = client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"}, follow_redirects=True)
    assert "passt schon zu diesen Daten" in r.data.decode()                  # nothing to do
    with app.app_context():
        from app import dummy, maxdiff_hb

        assert maxdiff_hb.current(db.session.get(Item, 2)) is not None
        dummy.generate(db.session.get(Survey, 1), 3, seed=6)
        db.session.commit()
        assert maxdiff_hb.current(db.session.get(Item, 2)) is None
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    # the estimate for the old data is no longer shown: a note and the button to estimate again
    assert "keine aktuelle HB-Schätzung" in html and "Individuelle Nutzen schätzen (Hierarchical Bayes)" in html
    assert "md-boxes" not in html and "md-rlh-chart" not in html and "<strong>fertig</strong>" not in html
    assert "md_hb_util1" not in client.get("/surveys/1/export/data.csv?source=dummy").data.decode()
    assert "md_hb" not in client.get("/surveys/1/export/codebook.csv?source=dummy").data.decode()
    # estimated again: current; without the speeders (fast half at 95 % of the median) it is other data
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})
    assert "<strong>fertig</strong>" in client.get("/surveys/1/results?source=dummy").data.decode()
    client.post("/surveys/1/quality", data={"speeder": "95"})
    html = client.get("/surveys/1/results?source=dummy&drop=1").data.decode()
    assert "ausgeschlossen" in html and "keine aktuelle HB-Schätzung" in html and "md-boxes" not in html


def test_background_thread_and_markers(app, client, survey):
    with_dummies(app, client, n=12)
    app.config["HB_SYNC"] = False
    client.post("/surveys/1/results/hb/2", data={"source": "dummy", "speeder": "50"})
    for t in threading.enumerate():
        if t.name == "hb-item-2":
            t.join(60)
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert "<strong>fertig</strong>" in html
    with app.app_context():
        from app import maxdiff_hb

        item = db.session.get(Item, 2)
        _, marker_path = maxdiff_hb._paths(item)
        assert not os.path.exists(marker_path)
        old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        maxdiff_hb._write(marker_path, {"state": "running", "started": old, "beat": old, "progress": 0.4})
        assert maxdiff_hb.running(item)["state"] == "aborted"
        now = datetime.now(timezone.utc).isoformat()
        maxdiff_hb._write(marker_path, {"state": "running", "started": now, "beat": now, "progress": 0.4})
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert "data-hb-running" in html and "40 %" in html
    # the bar on the page follows the estimation without reloading the results
    assert 'data-url="/surveys/1/results/hb/2/status"' in html and 'aria-valuenow="40"' in html and 'style="width:40%"' in html
    r = client.get("/surveys/1/results/hb/2/status")
    assert r.get_json() == {"state": "running", "progress": 0.4, "queued": False} and r.headers["Cache-Control"] == "no-store"
    assert client.get("/surveys/1/results/hb/1/status").status_code == 404           # the consent, not MaxDiff
    with app.app_context():
        maxdiff_hb._write(marker_path, {"state": "error", "started": now, "message": "ValueError: kaputt"})
    assert client.get("/surveys/1/results/hb/2/status").get_json()["state"] == "error"
    assert "ValueError: kaputt" in client.get("/surveys/1/results?source=dummy").data.decode()
    with app.app_context():
        os.remove(marker_path)
    assert client.get("/surveys/1/results/hb/2/status").get_json() == {"state": "done"}   # the page reloads


def test_hb_route_checks(app, client, survey):
    setup(client)
    app.config.update(HB_FAST)
    r = client.post("/surveys/1/results/hb/2", data={"source": "real"}, follow_redirects=True)
    assert "fehlen Daten" in r.data.decode()
    assert client.post("/surveys/1/results/hb/1", data={}).status_code == 404       # the consent, not MaxDiff
    # no participation at all: the page offers its way in instead of empty panels (UI audit 2026-10-12, U26)
    assert "Noch keine Teilnahmen in der Grundlage" in client.get("/surveys/1/results").data.decode()   # no HB section


def test_top_and_above_threshold_over_the_draws():
    """L-3 (maxdiff/m01b): "Top 3" and "über Schwelle" were counted on the posterior means per person, which are shrunk
    toward the common mean. Now per person the share of the kept draws, then the mean over persons; by hand with
    loops, and a person whose mean lies above the threshold although most draws lie below it."""
    from app import hb, maxdiff_hb

    rng = np.random.default_rng(3)
    m, D, N = 5, 40, 6
    B = rng.normal(0, 1, (D, N, m)).astype("<f2").astype(float)       # 4 statement parameters + threshold
    res = {"draws": hb.pack_draws(B), "prob": [[0.0] * m] * N, "anchored": True}
    top, above = maxdiff_hb._draw_shares(res, m)
    want_top, want_above = np.zeros(m), np.zeros(m)
    for d in range(D):
        for n in range(N):
            b = B[d, n]
            u = np.append(b[:m - 1], 0.0)
            u = u - u.mean()
            t = b[m - 1] - np.append(b[:m - 1], 0.0).mean()
            order = sorted(range(m), key=lambda i: -u[i])
            for i in range(m):
                want_top[i] += (order.index(i) < maxdiff_hb.TOP) / (D * N)
                want_above[i] += (u[i] > t) / (D * N)
    assert np.allclose(top, want_top) and np.allclose(above, want_above)
    assert maxdiff_hb._draw_shares({**res, "anchored": False}, m + 1)[1] is None
    # one person, statement 1 against the threshold: 2 of 5 draws far above, 3 a little below; mean above
    B = np.zeros((5, 1, 3))
    B[:, 0, 0] = [3.0, 3.0, -1.0, -1.0, -1.0]
    top, above = maxdiff_hb._draw_shares({"draws": hb.pack_draws(B), "prob": [[0.0] * 3], "anchored": True}, 3)
    assert B[:, 0, 0].mean() > 0 and above[0] == pytest.approx(0.4)
    assert maxdiff_hb._draw_shares({"draws": None, "prob": [[0.0] * 3]}, 3) == (None, None)


def test_separated_statement_out_of_the_sum_100():
    """As in the pooled model, a statement everyone always chose as most important leaves the scale: the scores sum to
    100 over the other statements, its own follows with the same factor outside the sum (audit data storage and
    calculations 2026-10-21, D5)."""
    from app import maxdiff_hb
    from app.maxdiff_analysis import parameters

    versions, answers, _, _ = simulate(24, seed=5)
    fixed = []
    for v, tasks in answers:
        new = {}
        for t, (b, w) in tasks.items():
            s = versions[v][t]
            if 0 in s:
                b = 0
                w = w if w != 0 else next(i for i in s if i != 0)
            new[t] = (b, w)
        fixed.append((v, new))
    est, col, k, thr = parameters(8, versions, fixed, False)
    inp = {"key": "x", "versions": versions, "n_items": 8, "est": est, "col": col, "k": k, "thr": thr, "set_size": 4,
           "persons": [{"pid": f"p{n}", "v": v, "tasks": {str(t): list(bw) for t, bw in tasks.items()}, "anchor": None}
                       for n, (v, tasks) in enumerate(fixed)],
           "settings": {"burn": 100, "draws": 100, "thin": 1, "seed": 1}}
    res = maxdiff_hb.run(inp)
    assert res["separated"] == [0] and res["scores_without_separated"]
    prob = np.array(res["prob"])
    j = est.index(0)
    others = [n for n in range(len(est)) if n != j]
    assert np.allclose(prob[:, others].sum(axis=1), 100, atol=0.05)
    assert (prob[:, j] > prob[:, others].max(axis=1)).all()  # still on top, as a direction
