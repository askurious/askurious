"""Convergence of the diagonal of Sigma (part of the rule) and the hint on the individual utilities (split-R-hat per
person, app/hb.person_rhat, app/hb_convergence.person_check), 2026-11-23."""
import numpy as np
import pytest

from app import hb
from app import hb_convergence as hc
from app.choice_model import ChoiceSet
from app.extensions import db
from app.models import Item


def simulated(n=60, k=4, tasks=12, alts=3, seed=7):
    rng = np.random.default_rng(seed)
    mu = rng.normal(0, 1, k)
    persons = []
    for p in range(n):
        b = mu + rng.normal(0, 0.8, k)
        sets = []
        for _ in range(tasks):
            X = rng.choice([-1.0, 0.0, 1.0], size=(alts, k))
            sets.append(ChoiceSet(X, int((X @ b + rng.gumbel(size=alts)).argmax()), p))
        persons.append(sets)
    return persons, k


def split_rhat_direct(x):
    """Classic split-R-hat of one chain, written out (Vehtari et al., 2021, eq. 1 to 4)."""
    h = len(x) // 2
    a, b = x[:h], x[-h:]
    W = (a.var(ddof=1) + b.var(ddof=1)) / 2
    B = h * np.var([a.mean(), b.mean()], ddof=1)
    return np.sqrt(((h - 1) / h * W + B / h) / W)


def test_person_rhat_from_sums_equals_the_direct_formula():
    rng = np.random.default_rng(1)
    draws = rng.normal(size=(301, 5, 3)) + np.linspace(0, 1, 301)[:, None, None] * np.array([0, 0.5, 2])
    h = 150
    sums = np.stack([draws[:h].sum(axis=0), draws[-h:].sum(axis=0)])
    squares = np.stack([(draws[:h] ** 2).sum(axis=0), (draws[-h:] ** 2).sum(axis=0)])
    got = hb.person_rhat(sums, squares, h)
    want = np.array([[split_rhat_direct(draws[:, i, j]) for j in range(3)] for i in range(5)])
    assert got == pytest.approx(want, rel=1e-9)
    assert np.all(got[:, 2] > 1.05) and np.all(got[:, 0] < 1.05)       # the drifting parameter is flagged
    assert np.isnan(hb.person_rhat(np.zeros((2, 2, 2)), np.zeros((2, 2, 2)), 10)).all()   # no spread: no value
    assert np.isnan(hb.person_rhat(sums, squares, 3)).all()


def test_estimate_returns_sigma_trace_and_person_rhat_matching_its_draws():
    persons, k = simulated(n=12)
    r = hb.estimate(persons, k, burn=40, draws=80, thin=2, seed=3, keep=40)
    assert r["sigma_trace"].shape == (40, k) and r["person_rhat"].shape == (12, k)
    assert r["sigma_trace"].mean(axis=0) == pytest.approx(r["sigma_diag"])
    # keep=40 of 40 kept draws: the kept betas are every draw, so the split-R-hat can be recomputed from them
    B = r["draws"]
    want = np.array([[split_rhat_direct(B[:, i, j]) for j in range(k)] for i in range(12)])
    assert r["person_rhat"] == pytest.approx(want, rel=1e-6)


def test_sigma_joins_the_rule():
    rng = np.random.default_rng(5)
    means, ll = rng.normal(size=(2000, 2)), rng.normal(size=2000)
    good = hc.diagnose(means, ll, sigma=np.exp(rng.normal(size=(2000, 2))))
    assert good["diag_ok"] and good["rhat_sigma_max"] < 1.01 and good["ess_sigma_min"] > 400
    drift = np.exp(rng.normal(size=(2000, 2)))
    drift[:, 1] += np.linspace(0, 3, 2000)                                 # a variance still growing
    bad = hc.diagnose(means, ll, sigma=drift)
    assert not bad["diag_ok"] and bad["rhat_sigma_max"] > 1.01 and bad["rhat_max"] == bad["rhat_sigma_max"]
    stuck = np.ones((2000, 2))
    assert not hc.diagnose(means, ll, sigma=stuck)["diag_complete"]
    assert "rhat_sigma_max" not in hc.diagnose(means, ll)                   # without sigma as before


def test_person_check_share_and_threshold():
    values = np.full((40, 3), 1.001)
    values[:2, 1] = 1.2                                                    # 2 of 40 persons: 5 %, no hint yet
    values[39] = np.nan                                                    # not assessable: left out
    check = hc.person_check(values)
    assert check == {"person_rhat_flagged": 2, "person_rhat_checked": 39, "person_rhat_share": round(2 / 39, 4)}
    assert hc.person_hint(check)                                           # 5.1 % > 5 %
    values[1, 1] = 1.0
    assert not hc.person_hint(hc.person_check(values))                      # 2.6 %
    assert hc.person_check(np.empty((0, 3)))["person_rhat_share"] is None
    assert not hc.person_hint({})


def test_healthy_chain_flags_few_persons_an_unsettled_one_many():
    """Simulated healthy data, 60 persons, 1000 kept draws: no person or hardly any above 1.05; a chain cut off
    right after the start: far more than 5 %. The basis of PERSON_SHARE_HINT."""
    persons, k = simulated()
    settled = hb.estimate(persons, k, burn=1500, draws=4000, thin=4, seed=2, keep=0)
    share = hc.person_check(settled["person_rhat"])["person_rhat_share"]
    assert share <= hc.PERSON_SHARE_HINT
    unsettled = hb.estimate(persons, k, burn=0, draws=60, thin=1, seed=2, keep=0)
    assert hc.person_check(unsettled["person_rhat"])["person_rhat_share"] > 0.2


def _run_cbc(app, client):
    from tests.analysis.test_cbc_results import setup
    from tests.analysis.test_conjoint_hb import HB_FAST, simulate_segments

    setup(client, price_attr=True, none=True, tasks=12, versions=6)
    app.config.update(HB_FAST)
    simulate_segments(app, 20, none_asc=-0.5)
    client.post("/surveys/1/results/hb/2", data={"source": "real"})


def _rewrite(app, change):
    with app.app_context():
        from app import conjoint_hb, maxdiff_hb

        item = db.session.get(Item, 2)
        res = change(conjoint_hb.load(item))
        maxdiff_hb._write(maxdiff_hb._paths(item)[0], res)
        return res


def test_cbc_stores_and_shows_sigma_and_the_person_hint(app, client, survey):
    _run_cbc(app, client)
    with app.app_context():
        from app import conjoint_hb

        res = conjoint_hb.load(db.session.get(Item, 2))
        assert res["rhat_sigma_max"] is not None and res["ess_sigma_min"] is not None
        assert res["person_rhat_checked"] == len(res["pids"]) and res["person_rhat_share"] is not None
        assert res["rhat_max"] >= res["rhat_sigma_max"] and res["ess_min"] <= res["ess_sigma_min"]
    html = client.get("/surveys/1/results").data.decode()
    assert "Varianzen zwischen den Personen (Diagonale von Σ)" in html
    assert 'class="muted small hb-person-rhat"' in html and "Individuelle Nutzen (nur Hinweis)" in html
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    line = next(l for l in book.splitlines() if l.startswith("cbc_hb_rlh"))
    assert "Konvergenz der Populationsmittel und -varianzen" in line and "individuelle Nutzen:" in line
    page = client.get("/surveys/1/methods").data.decode()
    assert "je Varianz zwischen den Personen" in page and "über 1,05" in page


def test_person_hint_offers_the_longer_run_once_and_reaches_the_simulator(app, client, survey):
    """The rule passes, but 30 % of persons have a utility above 1.05: the hint, the button once in "Konvergenz der
    Kette", the note in the codebook and in the simulator's notes; without the hint none of it."""
    _run_cbc(app, client)
    _rewrite(app, lambda r: {**r, "diag_ok": True, "diag_complete": True, "converged": True, "geweke_ok": True,
                             "geweke_ll_hint": False, "rhat_max": 1.0, "ess_min": 900,
                             "person_rhat_flagged": 6, "person_rhat_checked": 20, "person_rhat_share": 0.3})
    html = client.get("/surveys/1/results").data.decode()
    section = html.split("Konvergenz der Kette")[1].split("Hierarchical Bayes (HB-MNL)")[0]
    assert "hb-conv-note" not in html and "30,0 %" in section and "Mehr als 5 %" in section
    assert html.count('name="longer" value="1"') == 1 and "Neu schätzen mit doppelter Länge" in section
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    assert "über 5 %: mit doppelter Länge neu schätzen" in book
    with app.test_request_context():
        from app import conjoint_hb, simulator

        conv = hc.view(conjoint_hb.load(db.session.get(Item, 2)))
        assert conv["person_note"] and "30,0 %" in conv["person_note"]
        notes = simulator._notes({"hb": {"state": "current", "result": {"convergence": conv, "warnings": []}}}, "hb")
        assert conv["person_note"] in notes
    _rewrite(app, lambda r: {**r, "person_rhat_flagged": 1, "person_rhat_share": 0.05})
    html = client.get("/surveys/1/results").data.decode()
    assert "5,0 %" in html and "Mehr als 5 %" not in html and 'name="longer" value="1"' not in html


def test_stored_result_without_the_new_values_shows_as_before(app, client, survey):
    """An estimate stored before 2026-11-23: no Sigma line, no person line, the same judgement, no crash."""
    _run_cbc(app, client)
    new = ("rhat_sigma_max", "ess_sigma_min", "person_rhat_flagged", "person_rhat_checked", "person_rhat_share")
    for ok in (True, False):
        old = _rewrite(app, lambda r: {**{key: v for key, v in r.items() if key not in new}, "diag_ok": ok,
                                       "converged": ok, "geweke_ll_hint": False,
                                       **({"rhat_max": 1.0, "ess_min": 900} if ok else {})})
        conv = hc.view(old)
        assert conv["ok"] is ok and conv["rhat_sigma_max"] is None and conv["person_rhat_share"] is None
        assert not conv["person_hint"] and conv["person_note"] is None and (conv["longer"] is None) is ok
        assert "individuelle Nutzen" not in hc.describe(old, "de") and "Populationsmittel und" not in hc.describe(old, "de")
        html = client.get("/surveys/1/results").data.decode()
        assert "Konvergenz der Kette" in html and "Diagonale von Σ):" not in html and "hb-person-rhat" not in html
        assert ("hb-conv-ok" in html) is ok and ('name="longer" value="1"' in html) is (not ok)
        assert client.get("/surveys/1/export/codebook.csv").status_code == 200
        assert client.get("/surveys/1/methods").status_code == 200
