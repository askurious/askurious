"""Convergence of the HB chain (app/hb_convergence.py): ESS, split-R-hat, stored trace, page, longer run."""
import math
from statistics import NormalDist

import numpy as np
import pytest

from app import hb_convergence as hc
from app.extensions import db
from app.models import Item


def ar1(n, phi, seed):
    rng = np.random.default_rng(seed)
    e = rng.standard_normal(n)
    x = np.empty(n)
    x[0] = e[0] / math.sqrt(1 - phi ** 2)
    for t in range(1, n):
        x[t] = phi * x[t - 1] + e[t]
    return x


# --- own numpy recomputation, without FFT and without the module's helpers -------------------------------

def _znorm(chains):
    flat = chains.ravel()
    ranks = np.array([(flat < v).sum() + ((flat == v).sum() + 1) / 2 for v in flat])
    inv = NormalDist().inv_cdf
    return np.array([inv((r - 0.375) / (flat.size + 0.25)) for r in ranks]).reshape(chains.shape)


def _acf(x):
    """Rank-normalised halves of x and their combined autocorrelations (direct sums, no FFT)."""
    h = len(x) // 2
    c = _znorm(np.stack([x[:h], x[-h:]]))
    m, n = c.shape
    acov = np.array([[np.dot(ch[:n - t] - ch.mean(), ch[t:] - ch.mean()) / n for t in range(n)] for ch in c])
    W = (acov[:, 0] * n / (n - 1)).mean()
    var_plus = W * (n - 1) / n + c.mean(axis=1).var(ddof=1)
    return 1 - (W - acov.mean(axis=0)) / var_plus, m, n


def _ref_ess_positive(x):
    """The ESS before the audit of 2026-10-05: Geyer's initial positive sequence only."""
    rho, m, n = _acf(x)
    rho[0] = 1
    s, t = 0.0, 0
    while t + 1 < n and rho[t] + rho[t + 1] > 0:
        s += rho[t] + rho[t + 1]
        t += 2
    return m * n / (2 * s - 1)


def _ref_ess(x):
    """Geyer's (1992) initial monotone sequence written out as in the paper, with direct autocovariances instead of
    the FFT: pair sums P_k while positive, each P_k replaced by min(P_k, P_k-1), tau = 2 sum P_k - 1 (audit
    2026-10-05, H-4), ESS = M n / tau, at most M n draws."""
    rho, m, n = _acf(x)
    rho[0] = 1
    total, previous, k = 0.0, math.inf, 0
    while 2 * k + 1 < n:
        p = rho[2 * k] + rho[2 * k + 1]
        if p <= 0:
            break
        previous = min(previous, p)
        total += previous
        k += 1
    tau = 2 * total - 1
    return m * n if tau <= 0 else min(m * n, m * n / tau)


def _ref_split_rhat(x):
    def classic(c):
        n = c.shape[1]
        W = c.var(axis=1, ddof=1).mean()
        B = n * c.mean(axis=1).var(ddof=1)
        return math.sqrt(((n - 1) / n * W + B / n) / W)

    h = len(x) // 2
    bulk = classic(_znorm(np.stack([x[:h], x[-h:]])))
    f = np.abs(x - np.median(x))
    return max(bulk, classic(_znorm(np.stack([f[:h], f[-h:]]))))


def test_ess_and_rhat_match_own_recomputation():
    for phi, seed in ((0.0, 1), (0.6, 2), (0.9, 3)):
        x = ar1(801, phi, seed)                                  # odd length: the middle draw is dropped
        assert hc.ess(x) == pytest.approx(_ref_ess(x), rel=1e-9)
        assert hc.rhat(x) == pytest.approx(_ref_split_rhat(x), rel=1e-9)


def test_ess_monotone_sequence_lifts_single_low_chains():
    """H-4 (audit 2026-10-05): with the initial positive sequence alone single AR(1) chains came out far too low (5 %
    quantile of the old against the monotone value 0.59 at rho 0.9). The monotone sequence gives the reference value,
    and the error against the true n (1 - rho) / (1 + rho) gets smaller."""
    n, phi = 1000, 0.9
    true = n * (1 - phi) / (1 + phi)
    now, before, differs = [], [], 0
    for seed in range(40):
        x = ar1(n, phi, 100 + seed)
        now.append(hc.ess(x))
        before.append(_ref_ess_positive(x))
        assert now[-1] == pytest.approx(_ref_ess(x), rel=1e-9)
        differs += abs(now[-1] - before[-1]) > 1e-6
    assert differs >= 10                                         # the monotone step mattered for many chains
    rmse = lambda v: math.sqrt(np.mean((np.array(v) - true) ** 2))  # noqa: E731
    assert rmse(now) < rmse(before)
    assert np.percentile(np.array(before) / np.array(now), 5) < 0.85   # the old low outliers


def test_ess_of_ar1_chain_matches_theory():
    n = 20000
    for phi in (0.0, 0.5, 0.9):
        expected = n * (1 - phi) / (1 + phi)
        mean = np.mean([hc.ess(ar1(n, phi, seed)) for seed in range(6)])
        assert mean == pytest.approx(expected, rel=0.06), phi
    assert hc.ess(ar1(n, 0.9, 1)) < 0.08 * n                    # strongly autocorrelated: far below n


def test_ess_of_antithetic_chain_stays_at_the_number_of_draws():
    """Festlegung von Askurious: the ESS is at most the number of draws in both halves; an antithetic chain (true ESS
    19 n at rho -0.9) is not counted as better than independent draws, an odd length drops the middle draw."""
    assert hc.ess(ar1(800, -0.9, 4)) == 800
    assert hc.ess(ar1(801, -0.9, 4)) == 800
    assert hc.ess_chains(np.array([[1.0, -1.0] * 10, [-1.0, 1.0] * 10])) == 40   # tau <= 0: the draws, not None


def test_split_rhat_flags_shifted_halves_and_spread():
    rng = np.random.default_rng(4)
    stationary = rng.standard_normal(1000)
    assert hc.rhat(stationary) < hc.RHAT_LIMIT
    shifted = np.concatenate([rng.standard_normal(500), rng.standard_normal(500) + 0.5])
    assert hc.rhat(shifted) > 1.03
    wider = np.concatenate([rng.standard_normal(500), 3 * rng.standard_normal(500)])  # same mean, other spread
    assert hc.rhat(wider) > 1.03                                 # found by the folded draws
    assert hc.rhat(np.ones(100)) is None and hc.ess(np.ones(100)) is None


def test_thin_trace_and_rule():
    t = np.arange(1200.0)
    thin = hc.thin_trace(t)
    assert len(thin) == hc.TRACE_POINTS == 500 and thin[0] == 0 and thin[-1] == 1199
    assert np.all(np.diff(thin) > 0)
    assert list(hc.thin_trace(np.arange(300.0))) == list(range(300))
    rng = np.random.default_rng(5)
    good = hc.diagnose(rng.standard_normal((2000, 3)), rng.standard_normal(2000))
    assert good["diag_ok"] and good["rhat_max"] < 1.01 and good["ess_min"] >= 400 and len(good["rhat"]) == 3
    assert len(good["ll_trace"]) == 500
    assert not hc.diagnose(rng.standard_normal((2000, 3)), rng.standard_normal(2000), geweke_ok=False)["diag_ok"]
    sticky = np.column_stack([ar1(2000, 0.95, 6), rng.standard_normal(2000)])
    short = hc.diagnose(sticky, rng.standard_normal(2000))
    assert short["ess_min"] < 400 and not short["diag_ok"]
    assert "ESS min." in hc.warning({**short, "kept": 2000}) and hc.warning(good) is None


def test_longer_settings_double_up_to_a_limit():
    base = {"burn": 400, "draws": 400, "thin": 2, "seed": 1}
    once = hc.longer_settings(base, base)
    assert once == {"burn": 800, "draws": 800, "thin": 2, "seed": 1}
    assert hc.longer_settings({**base, "burn": 800, "draws": 800}, base)["burn"] == 1600   # 4 x the length
    assert hc.longer_settings({**base, "burn": 1600, "draws": 1600}, base) is None


@pytest.mark.parametrize("bad_part", ["mean", "shown", "likelihood", "nonfinite"])
def test_missing_diagnostic_cannot_pass_convergence(bad_part):
    """Review 2026-10-13: one healthy parameter must not hide another that has stopped moving."""
    rng = np.random.default_rng(131026)
    means, shown, ll = rng.normal(size=(4000, 2)), rng.normal(size=(4000, 2)), rng.normal(size=4000)
    if bad_part == "mean":
        means[:, 1] = 1
    elif bad_part == "shown":
        shown[:, 1] = 1
    elif bad_part == "likelihood":
        ll[:] = 1
    else:
        means[10, 1] = np.nan
    result = hc.diagnose(means, ll, shown=shown)
    assert not result["diag_ok"] and not result["diag_complete"]
    assert "mindestens eine" in hc.warning(result)
    assert "not assessable" in hc.describe(result, "en")
    import json
    json.dumps(result, allow_nan=False)


def test_all_free_parameters_need_diagnostics_and_matching_draws():
    rng = np.random.default_rng(131026)
    means, ll = rng.normal(size=(4000, 2)), rng.normal(size=4000)
    assert hc.diagnose(means, ll)["diag_complete"]
    assert not hc.diagnose(np.empty((4000, 0)), ll)["diag_ok"]
    assert not hc.diagnose(means, ll[:-1])["diag_ok"]
    assert not hc.diagnose(means, ll, shown=np.empty((4000, 0)))["diag_ok"]
    assert hc.trace_chart([1, None, 2]) is None


def test_default_length_and_longest_run_fit_together(app):
    """H-2 (audit 2026-10-05): the default length passes the rule on healthy data (5000 + 20 000); the button
    "doppelte Länge" doubles it twice at most, so the longest run stays near the old longest (120 000 iterations)."""
    from app import maxdiff_hb

    with app.app_context():
        for key in ("HB_BURN", "HB_DRAWS"):
            app.config.pop(key, None)
        base = maxdiff_hb.settings()
    assert (base["burn"], base["draws"], base["thin"]) == (5000, 20000, 10)
    ladder = [base]
    while (nxt := hc.longer_settings(ladder[-1], base)) is not None:
        ladder.append(nxt)
    assert [s["draws"] for s in ladder] == [20000, 40000, 80000]
    assert ladder[-1]["burn"] + ladder[-1]["draws"] == 100000


# --- CBC: stored trace, page, button, codebook, method text -------------------------------------------------

def test_cbc_stores_trace_shows_line_and_runs_longer(app, client, survey, monkeypatch):
    from tests.analysis.test_conjoint_hb import HB_FAST, simulate_segments
    from tests.analysis.test_cbc_results import setup

    setup(client, price_attr=True, none=True, tasks=12, versions=6)
    app.config.update(HB_FAST)                                   # 400 draws, every 2nd kept: 200 draws
    simulate_segments(app, 40, none_asc=-0.5)
    client.post("/surveys/1/results/hb/2", data={"source": "real"})
    with app.app_context():
        from app import conjoint_hb

        item = db.session.get(Item, 2)
        res = conjoint_hb.load(item)
        assert res["format"] == conjoint_hb.FORMAT == 7
        assert len(res["ll_trace"]) == res["kept"] == 200
        k = len(res["mu"])                                       # every parameter of mu: levels, price, none
        assert len(res["rhat"]) == len(res["ess"]) == k
        # W-2: the means the page shows (part-worths, constant, price per draw) count as well
        assert res["ess_shown"] is not None
        assert res["ess_min"] == pytest.approx(min(res["ess"] + [res["ess_ll"], res["ess_shown"]]), abs=0.11)
        assert res["ess_min"] < 400 and not res["diag_ok"] and res["converged"] is False   # 200 draws never do
    html = client.get("/surveys/1/results").data.decode()
    assert "Konvergenz der Kette" in html and 'class="hb-trace-line"' in html and "<polyline" in html
    # the status line names R̂ max. and ESS min. ("R̂ max." as plain text is only in the note when R̂ itself fails,
    # which depends on the design drawn at random: about 1 in 20 runs it does not)
    status = html.split('class="muted small hb-convergence"')[1].split("</p>")[0]
    assert "prüfen</strong>" in status and "glossary#rhat" in status and "glossary#ess" in status
    assert status.count(" max. ") >= 1 and status.count(" min. ") >= 1 and "ESS min." in html
    assert "Neu schätzen mit doppelter Länge" in html and 'name="longer" value="1"' in html
    assert "Die Schätzung hat sich vielleicht noch nicht eingependelt" in html
    # the button for a longer run sits right in the note at the top (and again in "Konvergenz der Kette")
    note = html.split('class="flash flash-warning hb-conv-note"')[1].split("</div></div>")[0]
    assert "Die Schätzung hat sich vielleicht noch nicht eingependelt" in note and 'name="longer" value="1"' in note
    # Ralf, 2026-10-05: only there, not a second time in "Konvergenz der Kette"
    assert "Neu schätzen mit doppelter Länge" in note and html.count('name="longer" value="1"') == 1
    assert "Vehtari, A., Gelman, A., Simpson, D., Carpenter, B., &amp; Bürkner, P.-C. (2021)" in html
    assert "https://doi.org/10.1214/ss/1177011137" in html
    assert " onclick=" not in html.split("Konvergenz der Kette")[1].split("Hierarchical Bayes (HB-MNL)")[0]
    book = client.get("/surveys/1/export/codebook.csv").data.decode("utf-8-sig")
    line = next(l for l in book.splitlines() if l.startswith("cbc_hb_rlh"))
    assert "split-R̂ max." in line and "(ESS) min." in line and "von 200 Ziehungen" in line
    page = client.get("/surveys/1/methods").data.decode()
    assert "rang-normalisierten split-R̂" in page and "ESS min." in page and "nicht erfüllt" in page
    assert "Vehtari et al., 2021" in page and "https://doi.org/10.1214/20-BA1221" in page
    # the button: burn-in and iterations doubled, the result shown again as current
    r = client.post("/surveys/1/results/hb/2", data={"source": "real", "longer": "1"})
    assert r.status_code == 302
    with app.app_context():
        from app import conjoint_hb

        res = conjoint_hb.load(db.session.get(Item, 2))
        assert res["settings"]["burn"] == 800 and res["settings"]["draws"] == 800 and res["kept"] == 400
        assert len(res["ll_trace"]) == 400
    # the same answers: the kept results stay, only the view of the estimate is read anew (Ralf, 2026-10-04)
    from app import results
    computed = []
    real = results.compute
    monkeypatch.setattr(results, "compute", lambda *a, **k: computed.append(1) or real(*a, **k))
    html = client.get("/surveys/1/results").data.decode()
    assert not computed
    assert "<strong>fertig</strong>" in html and "800 Burn-in- und 800 weitere Iterationen" in html
    assert "1.600 Burn-in- und 1.600 weitere Iterationen statt 800 und 800" in html


def test_old_format_counts_as_stale_once(app, client, survey):
    from tests.analysis.test_conjoint_hb import HB_FAST, simulate_segments
    from tests.analysis.test_cbc_results import setup

    setup(client, price_attr=True, none=True, tasks=12, versions=6)
    app.config.update(HB_FAST)
    simulate_segments(app, 20, none_asc=-0.5)
    client.post("/surveys/1/results/hb/2", data={"source": "real"})
    with app.app_context():
        from app import conjoint_hb, maxdiff_hb

        item = db.session.get(Item, 2)
        old = {k: v for k, v in conjoint_hb.load(item).items()
               if k not in ("ll_trace", "rhat", "ess", "ess_ll", "rhat_max", "ess_min", "diag_ok", "geweke_ok")}
        maxdiff_hb._write(maxdiff_hb._paths(item)[0], {**old, "format": 6})
        assert conjoint_hb.current(item) is None
    html = client.get("/surveys/1/results").data.decode()
    assert "Individuelle Nutzen schätzen (Hierarchical Bayes)" in html and "Konvergenz der Kette" not in html


# --- MaxDiff: statement means, trace thinned to 500 points ------------------------------------------------

def test_maxdiff_judges_statement_means_and_thins_trace(app, client, survey):
    from tests.analysis.test_maxdiff_hb import with_dummies

    with_dummies(app, client)
    app.config.update({"HB_BURN": 100, "HB_DRAWS": 1200, "HB_THIN": 2})   # 600 kept draws
    client.post("/surveys/1/results/hb/2", data={"source": "dummy"})
    with app.app_context():
        from app import maxdiff_hb

        res = maxdiff_hb.load(db.session.get(Item, 2))
        assert res["format"] == maxdiff_hb.FORMAT == 5 and res["kept"] == 600
        assert len(res["ll_trace"]) == 500                       # thinned for storage
        assert len(res["rhat"]) == len(res["est"]) == 8          # one per statement, not the threshold
        assert all(v is None or v > 0.99 for v in res["rhat"])
    html = client.get("/surveys/1/results?source=dummy").data.decode()
    assert 'class="hb-trace-line"' in html and "je Populationsmittel (Aussagen)" in html
    assert "für die Grafik auf 500 Punkte ausgedünnt" in html


def test_acbc_gets_the_same_check(app, client, survey):
    from app.models import Survey
    from tests.analysis import test_acbc_results as acbc
    from tests.analysis.test_conjoint_hb import HB_FAST

    acbc.setup(client, screens=5, per_screen=4, price=True)
    app.config.update(HB_FAST)
    with app.app_context():
        acbc.add_responses(db.session.get(Survey, 1), acbc.simulate(db.session.get(Item, 2), 30, price_beta=-0.01))
    client.post("/surveys/1/results/hb/2", data={"source": "real"})
    with app.app_context():
        from app import conjoint_hb

        res = conjoint_hb.load(db.session.get(Item, 2))
        assert res["type"] == "acbc" and len(res["rhat"]) == len(res["mu"]) and len(res["ll_trace"]) == res["kept"]
    html = client.get("/surveys/1/results").data.decode()
    assert "Konvergenz der Kette" in html and "Neu schätzen mit doppelter Länge" in html
