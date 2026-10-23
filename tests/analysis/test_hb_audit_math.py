"""Hierarchical Bayes after the mathematics audit of 2026-10-05 (H-1, H-3, H-5; H-2 and H-4 in test_hb_convergence.py)."""
import math

import numpy as np
import pytest
from sqlalchemy.orm.attributes import flag_modified

from app.extensions import db
from app.models import Item, Survey
from tests.analysis.test_maxdiff import setup
from tests.analysis.test_maxdiff_hb import HB_FAST, person_sets, simulate


# --- H-1: no HB estimate for a MaxDiff design that falls apart into groups --------------------------------------

def disconnected_dummies(app, client, n=30):
    """8 statements in two groups {1-4} and {5-8} that never share a set (the case of the audit), dummy answers."""
    setup(client, versions=1)
    app.config.update(HB_FAST)
    with app.app_context():
        from app import dummy

        item = db.session.get(Item, 2)
        c = dict(item.config)
        sets = [[0, 1, 2, 3], [4, 5, 6, 7], [1, 3, 0, 2], [5, 7, 6, 4], [2, 0, 3, 1], [6, 4, 7, 5]]
        c["design"] = {**c["design"], "versions": [sets]}
        c["sets"] = len(sets)
        item.config = c
        flag_modified(item, "config")
        db.session.commit()
        assert dummy.generate(db.session.get(Survey, 1), n, seed=5) == n
        db.session.commit()


def test_h1_disconnected_maxdiff_gets_no_hb_estimate(app, client, survey):
    disconnected_dummies(app, client)
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert "Die Aussagen zerfallen in 2 Gruppen" in html and "1, 2, 3, 4 | 5, 6, 7, 8" in html
    assert "eine längere Kette ändert daran nichts" in html
    assert "Individuelle Nutzen schätzen (Hierarchical Bayes)" not in html and "Für eine HB-Schätzung fehlen Daten" not in html
    r = client.post("/surveys/1/results/hb/2", data={"source": "dummy"}, follow_redirects=True)
    assert "deshalb keine HB-Schätzung" in r.data.decode()
    with app.app_context():
        from app import maxdiff_hb

        item = db.session.get(Item, 2)
        assert maxdiff_hb.load(item) is None
        table = maxdiff_hb.pool_table(item.survey, {"sources": ["dummy"], "drop": True})
        assert maxdiff_hb.model_input(item, table) is None
        assert maxdiff_hb.start(item, table, {"sources": ["dummy"]}, background=False) == "disconnected"


def test_h1_existing_estimate_of_a_disconnected_design_is_not_shown(app, client, survey, monkeypatch):
    """An estimate made before the check (here: with the check switched off) is neither shown nor exported."""
    from app import maxdiff_hb

    disconnected_dummies(app, client)
    monkeypatch.setattr(maxdiff_hb, "components", lambda n, versions, answers: [list(range(n))])
    client.post("/surveys/1/results/hb/2", data={"source": "dummy"})
    assert "<strong>fertig</strong>" in client.get("/surveys/1/results?source=dummy").data.decode()
    assert "md_hb_util1" in client.get("/surveys/1/export/data.csv?source=dummy").data.decode()
    monkeypatch.undo()
    maxdiff_hb._FITS.clear()  # the verdicts are kept per process; a new program (here: the check back on) is a new process
    from app import results_store

    monkeypatch.setattr(results_store, "_program", "a new version")  # ... with another program (stored with the estimate, V1)
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert "Die Aussagen zerfallen in 2 Gruppen" in html and "eine längere Kette ändert daran nichts" in html
    assert "<strong>fertig</strong>" not in html and "md-boxes" not in html and "keine aktuelle HB-Schätzung" not in html
    assert "md_hb_util1" not in client.get("/surveys/1/export/data.csv?source=dummy").data.decode()
    with app.app_context():
        assert maxdiff_hb.load(db.session.get(Item, 2)) is not None       # the file stays, it is only not used
        assert maxdiff_hb.current(db.session.get(Item, 2)) is None


# --- H-3: spread between persons in the scale of the shown utilities ---------------------------------------------

def test_h3_maxdiff_spread_is_that_of_the_zero_centred_utilities():
    """MaxDiff: the stored diagonal of Sigma belongs to the differences to the reference statement, about twice the
    variance of the zero-centred utilities the page shows (audit: page 0.889, zero-centred 0.396). The new value is
    the posterior mean of mean(diag(M Sigma M'))."""
    from app import maxdiff_hb
    from app.maxdiff_analysis import parameters

    versions, answers, _, U = simulate(120, seed=11, person_sd=0.5)
    est, col, k, thr = parameters(8, versions, answers, False)
    persons = [{"pid": n, "v": v, "tasks": {str(t): list(bw) for t, bw in tasks.items()}, "anchor": None}
               for n, (v, tasks) in enumerate(answers)]
    inp = {"key": "x", "data_key": "x", "versions": versions, "n_items": 8, "est": est, "col": col, "k": k, "thr": thr,
           "set_size": 4, "persons": persons, "settings": {"burn": 600, "draws": 600, "thin": 3, "seed": 2}}
    res = maxdiff_hb.run(inp)
    old = float(np.mean(res["sigma_diag"]))                     # the value the page showed before
    new = res["sigma_util"]
    true = float(np.var(U - U.mean(axis=1, keepdims=True), axis=0).mean())   # 0.25 * 7 / 8 in expectation
    assert 1.6 < old / new < 3.0                                  # the differences: about twice as large (or more)
    assert abs(new - true) < abs(old - true)
    view = maxdiff_hb.summary({**res, "created": "2026-10-05T12:00:00+00:00"}, [f"s{i}" for i in range(8)])
    assert view["sigma_mean"] == new and view["sigma_prior"] == pytest.approx(7 / 8)
    # an estimate from before (without the value) shows none instead of the old diagonal
    legacy = {key: value for key, value in res.items() if key != "sigma_util"}
    assert maxdiff_hb.summary({**legacy, "created": "2026-10-05T12:00:00+00:00"}, [""] * 8)["sigma_mean"] is None


def test_h3_spread_is_the_posterior_mean_over_kept_draws():
    from app import hb

    versions, answers, _, _ = simulate(30, seed=8)
    sets, k, M = person_sets(versions, answers)
    seen = []

    def spread(S):
        seen.append(np.mean(np.diag(M @ S @ M.T)))
        return [seen[-1], S[0, 0]]

    r = hb.estimate(sets, k, burn=100, draws=100, thin=4, seed=3, prior_cov=np.eye(k) + 1, spread=spread)
    assert len(seen) == r["kept"] == 25
    assert r["spread"][0] == pytest.approx(np.mean(seen))
    assert hb.estimate(sets, k, burn=20, draws=20, thin=4, seed=3)["spread"] is None


def test_h3_conjoint_names_constant_and_price_separately(app, client, survey):
    """CBC: one number mixed effects-coded parameters (prior (L-1)/L), the constant and the standardised price. Now
    the zero-centred part-worths, the constant and the price coefficient per currency unit each get their own."""
    from tests.analysis.test_cbc_results import setup as cbc_setup
    from tests.analysis.test_conjoint_hb import HB_FAST as CBC_FAST, simulate_segments

    cbc_setup(client, price_attr=True, none=True, tasks=12, versions=6)
    app.config.update(CBC_FAST)
    simulate_segments(app, 40, none_asc=-0.5)
    client.post("/surveys/1/results/hb/2", data={"source": "real"})
    with app.app_context():
        from app import conjoint_hb

        item = db.session.get(Item, 2)
        res = conjoint_hb.load(item)
        assert res["sigma_util"] > 0 and res["sigma_asc"] > 0 and res["sigma_price"] > 0
        assert res["sigma_util"] != pytest.approx(float(np.mean(res["sigma_diag"])), rel=0.01)
        view = conjoint_hb.summary(item, res, "de")
        assert view["sigma_mean"] == res["sigma_util"] and view["sigma_asc"] == res["sigma_asc"]
        assert view["sigma_price_sd"] == pytest.approx(math.sqrt(res["sigma_price"]))
    html = client.get("/surveys/1/results").data.decode()
    assert "mittlere Varianz der nullzentrierten Teilnutzen zwischen den Personen" in html
    assert "Varianz der Konstante" in html and "SD des Preiskoeffizienten" in html
    assert "Erwartungswert 1 je Parameter" not in html and "(L − 1)/L bei L Stufen" in html


# --- H-5: step sizes per person settle with a decreasing gain and are averaged -----------------------------------

def test_h5_acceptance_per_person_stays_near_the_target():
    """Before, the last window of 50 tries fixed each person's step size: acceptance per person after burn-in from
    0.05 to 0.84 (target 0.57). Now the last quarter of the burn-in settles them (Robbins-Monro) and averages them."""
    import inspect

    from app import hb

    versions, answers, _, _ = simulate(150, seed=4)
    sets, k, _ = person_sets(versions, answers)
    src = inspect.getsource(hb.estimate)
    src = src.replace("accepted_kept += int(accept.sum())", "accepted_kept += int(accept.sum()); per_person += accept")
    src = src.replace("    kept, kept_draws = [], []", "    kept, kept_draws = [], []\n    per_person = np.zeros(n)")
    src = src.replace('        "seconds": time.monotonic() - started,',
                      '        "seconds": time.monotonic() - started,\n        "per_person": per_person / max(draws, 1),')
    ns = dict(vars(hb))
    exec(compile(src.replace("def estimate(", "def estimate_counted(", 1), "estimate_counted", "exec"), ns)
    r = ns["estimate_counted"](sets, k, burn=2000, draws=1500, thin=5, seed=4, prior_cov=np.eye(k) + 1)
    acc = r["per_person"]
    assert abs(acc.mean() - hb.TARGET) < 0.05
    assert np.percentile(acc, 1) > 0.4 and np.percentile(acc, 99) < 0.75
    assert r["shift_acceptance"] > 0.3


def test_h5_settling_keeps_the_chain_valid_and_short_burn_ins_working():
    from app import hb

    versions, answers, _, _ = simulate(20, seed=8)
    sets, k, _ = person_sets(versions, answers)
    for burn in (0, 3, 10, 60):
        r = hb.estimate(sets, k, burn=burn, draws=40, thin=2, seed=1)
        assert r["kept"] == 20 and np.isfinite(r["beta"]).all() and np.isfinite(r["rho"])
    assert hb._chance(np.array([0.5, -math.log(2), np.nan]))[1] == pytest.approx(0.5)
    assert list(hb._chance(np.array([0.5, np.nan]))) == [1.0, 0.0]
    assert hb.ANDRIEU_THOMS in hb.SAMPLER_SOURCES
