"""The estimators of app/sem.py against closed forms and simulations (concept strukturgleichungsmodelle-konzept.md,
E13): ML equals OLS for a saturated path model of observed variables and equals the one-factor fit of app/scales.py
for one construct; chi-square is calibrated and the robust errors cover; PLS equals OLS with single indicators."""
import math

import numpy as np
import pytest

from app import scales, sem


def _items(rng, f, k, loading=0.7):
    return np.column_stack([loading * f + math.sqrt(1 - loading ** 2) * rng.normal(size=len(f)) for _ in range(k)])


def _three(rng, n, elliptical=False):
    """Three constructs A -> B -> C plus A -> C, 4, 3 and 4 indicators (loadings .7); standardised paths .5, .4, .3.
    elliptical: multivariate t with 8 degrees of freedom (one random scale per person: excess kurtosis 1.5)."""
    noise = (lambda size: rng.normal(size=size))
    a = noise(n)
    b = 0.5 * a + math.sqrt(0.75) * noise(n)
    rest = 1 - 0.4 ** 2 - 0.3 ** 2 - 2 * 0.4 * 0.3 * 0.5
    c = 0.4 * b + 0.3 * a + math.sqrt(rest) * noise(n)

    def items(f, k):
        return np.column_stack([0.7 * f + math.sqrt(0.51) * noise(n) for _ in range(k)])

    x = np.hstack([items(a, 4), items(b, 3), items(c, 4)])
    if elliptical:
        x *= np.sqrt(6 / rng.chisquare(8, size=n))[:, None]
    cols = [f"a{i}" for i in range(4)] + [f"b{i}" for i in range(3)] + [f"c{i}" for i in range(4)]
    spec = {"latent": {"A": cols[:4], "B": cols[4:7], "C": cols[7:]},
            "paths": [("A", "B"), ("B", "C"), ("A", "C")], "indirect": [("A", "B", "C")]}
    return x, cols, spec


def test_saturated_path_model_equals_ols():
    rng = np.random.default_rng(1)
    n = 300
    x = rng.normal(size=n)
    m = 0.4 * x + rng.normal(size=n)
    y = 0.3 * m + 0.2 * x + rng.normal(size=n)
    data = np.column_stack([x, m, y])
    spec = {"observed": ["x", "m", "y"], "paths": [("x", "m"), ("m", "y"), ("x", "y")], "indirect": [("x", "m", "y")]}
    r = sem.fit_ml(data, ["x", "m", "y"], spec)
    assert r["df"] == 0 and r["chi2"] == pytest.approx(0, abs=1e-8)
    z = (data - data.mean(0)) / data.std(0)
    a = np.linalg.lstsq(z[:, [0]], z[:, 1], rcond=None)[0]
    b = np.linalg.lstsq(z[:, [0, 1]], z[:, 2], rcond=None)[0]
    paths = {(p["from"], p["to"]): p["std"] for p in r["paths"]}
    assert paths[("x", "m")] == pytest.approx(a[0], abs=1e-6)
    assert paths[("x", "y")] == pytest.approx(b[0], abs=1e-6)
    assert paths[("m", "y")] == pytest.approx(b[1], abs=1e-6)
    assert r["indirect"][0]["std"] == pytest.approx(a[0] * b[1], abs=1e-6)
    # PLS with one indicator per construct is the same regression
    q = sem.fit_pls(data, ["x", "m", "y"], spec)
    assert {(p["from"], p["to"]): p["std"] for p in q["paths"]} == pytest.approx(paths, abs=1e-6)


def test_one_construct_equals_the_one_factor_fit_of_the_scales():
    rng = np.random.default_rng(2)
    f = rng.normal(size=400)
    x = np.column_stack([lam * f + rng.normal(size=400) * math.sqrt(1 - lam ** 2) for lam in (0.8, 0.7, 0.6, 0.5, 0.75)])
    cols = [f"i{k}" for k in range(5)]
    r = sem.fit_ml(x, cols, {"latent": {"F": cols}})
    lam, psi = scales._one_factor(np.cov(x, rowvar=False, bias=True))
    std = np.abs(lam) / np.sqrt(lam ** 2 + psi)
    assert [row["std"] for row in r["loadings"]["F"]] == pytest.approx(std.tolist(), abs=1e-5)
    assert r["df"] == 5 and r["admissible"]


def test_estimates_recover_the_paths_and_chi_square_is_calibrated():
    rng = np.random.default_rng(3)
    stats, paths = [], []
    for _ in range(30):
        x, cols, spec = _three(rng, 600)
        r = sem.fit_ml(x, cols, spec)
        assert r["converged"] and r["df"] == 41
        stats.append(r["chi2"])
        paths.append([p["std"] for p in r["paths"]])
    assert np.mean(stats) == pytest.approx(41, abs=4)  # sd of the mean about 9 / sqrt(30)
    assert np.mean(paths, axis=0) == pytest.approx([0.5, 0.4, 0.3], abs=0.03)


def test_robust_errors_cover_and_scaling_reacts_to_heavy_tails():
    rng = np.random.default_rng(4)
    hits, scale_normal, scale_heavy = 0, [], []
    for _ in range(40):
        x, cols, spec = _three(rng, 400)
        r = sem.fit_ml(x, cols, spec)
        lo, hi = r["paths"][0]["ci"]
        hits += lo <= 0.5 <= hi
        scale_normal.append(r["scaled"]["c"])
        # independent heavy-tailed factors and errors would not need it (asymptotic robustness); a common scale does
        xh, cols, spec = _three(rng, 400, elliptical=True)
        scale_heavy.append(sem.fit_ml(xh, cols, spec)["scaled"]["c"])
    assert hits >= 34  # about 95 % of 40
    assert np.mean(scale_normal) == pytest.approx(1, abs=0.06)
    assert np.mean(scale_heavy) > 1.3  # kurtosis inflates T, the scaling corrects it


def test_fit_indices_and_rmsea_interval():
    rng = np.random.default_rng(5)
    x, cols, spec = _three(rng, 500)
    r = sem.fit_ml(x, cols, spec)
    chi2, df, n = r["chi2"], r["df"], r["n"]
    assert r["rmsea"] == pytest.approx(math.sqrt(max(chi2 - df, 0) / (df * n)))
    lo, hi = r["rmsea_ci"]
    assert lo <= r["rmsea"] <= hi
    # at the bounds T is the 95 % and the 5 % quantile of the noncentral chi-square
    if lo > 0:
        assert sem.ncx2_sf(chi2, df, lo ** 2 * df * n) == pytest.approx(0.05, abs=1e-4)
    assert sem.ncx2_sf(chi2, df, hi ** 2 * df * n) == pytest.approx(0.95, abs=1e-4)
    assert 0.97 < r["cfi"] <= 1 and r["srmr"] < 0.05


def test_rmsea_interval_stops_early_with_the_same_result(monkeypatch):
    """Audit 2026-10-11 (N16): the bisection stops at a relative width of 1e-7 instead of after 80 steps; the
    interval stays the same to well beyond 4 decimals, with far fewer Poisson sums."""
    def fixed(t, df, n, level=0.90):  # the former rule: always 80 halvings
        tail = (1 - level) / 2

        def solve(target):
            if sem.ncx2_sf(t, df, 0.0) >= target:
                return 0.0
            lo, hi = 0.0, max(10.0, 2 * t)
            while sem.ncx2_sf(t, df, hi) < target:
                hi *= 2
            for _ in range(80):
                mid = (lo + hi) / 2
                lo, hi = (mid, hi) if sem.ncx2_sf(t, df, mid) < target else (lo, mid)
            return (lo + hi) / 2

        return math.sqrt(solve(tail) / (df * n)), math.sqrt(solve(1 - tail) / (df * n))

    calls = []
    sf = sem.ncx2_sf
    monkeypatch.setattr(sem, "ncx2_sf", lambda *a: calls.append(1) or sf(*a))
    for t, df, n in ((12.0, 8, 200), (87.3, 24, 500), (402.0, 160, 2000), (1500.0, 390, 5000), (30.0, 41, 300)):
        calls.clear()
        new = sem.rmsea_ci(t, df, n)
        quick = len(calls)
        calls.clear()
        old = fixed(t, df, n)
        assert len(calls) > quick
        assert [round(v, 4) for v in new] == [round(v, 4) for v in old]
        assert new == pytest.approx(old, abs=1e-6)


def test_noncentral_chi_square_against_simulation():
    rng = np.random.default_rng(6)
    draws = rng.noncentral_chisquare(10, 7.5, size=200_000)
    for x in (8.0, 15.0, 25.0):
        assert sem.ncx2_sf(x, 10, 7.5) == pytest.approx(np.mean(draws > x), abs=0.004)


def test_misspecified_model_fits_worse():
    rng = np.random.default_rng(7)
    x, cols, spec = _three(rng, 800)
    wrong = {"latent": {"AB": cols[:7], "C": cols[7:]}, "paths": [("AB", "C")]}
    good, bad = sem.fit_ml(x, cols, spec), sem.fit_ml(x, cols, wrong)
    assert bad["rmsea"] > 0.08 > good["rmsea"] and bad["cfi"] < 0.9


def test_quality_by_hand():
    corr = np.array([[1, .6, .3, .2], [.6, 1, .2, .3], [.3, .2, 1, .5], [.2, .3, .5, 1]])
    spec = {"latent": {"A": ["a1", "a2"], "B": ["b1", "b2"]}}
    loads = {"A": [{"std": 0.8}, {"std": 0.6}], "B": [{"std": 0.7}, {"std": 0.7}]}
    q = sem.quality(sem.check(spec), loads, corr, ["a1", "a2", "b1", "b2"])
    assert q["ave"]["A"] == pytest.approx((0.64 + 0.36) / 2)
    assert q["cr"]["A"] == pytest.approx(1.96 / (1.96 + 0.36 + 0.64))
    assert q["htmt"][("A", "B")] == pytest.approx(0.25 / math.sqrt(0.6 * 0.5))


def test_heywood_case_is_not_admissible():
    rng = np.random.default_rng(8)
    n = 2000
    f, shared = rng.normal(size=n), math.sqrt(0.1) * rng.normal(size=n)
    # the residuals of i2 and i3 correlate negatively: the one-factor model needs a loading of i1 above its
    # standard deviation (lambda² = s12 s13 / s23 = .36 / .26), a negative residual variance
    x = np.column_stack([f + 0.05 * rng.normal(size=n), 0.6 * f + shared + 0.5 * rng.normal(size=n),
                         0.6 * f - shared + 0.5 * rng.normal(size=n)])
    r = sem.fit_ml(x, ["i1", "i2", "i3"], {"latent": {"F": ["i1", "i2", "i3"]}})
    assert "heywood" in r["problems"] and not r["admissible"]
    with pytest.raises(sem.SemError):
        sem.check({"latent": {"F": ["i1"]}})
    with pytest.raises(sem.SemError):
        sem.check({"observed": ["a", "b"], "paths": [("a", "b"), ("b", "a")]})


def test_pls_attenuates_and_bootstrap_covers():
    rng = np.random.default_rng(9)
    x, cols, spec = _three(rng, 500)
    q = sem.fit_pls(x, cols, spec)
    assert q["converged"]
    # composites carry measurement error: paths between ML's and the attenuated ones
    assert 0.3 < q["paths"][0]["std"] < 0.5
    assert all(0.6 < row["std"] < 0.9 for rows in q["loadings"].values() for row in rows)
    boot = sem.bootstrap(x, cols, spec, "pls", draws=200, seed=1)
    lo, hi = boot["paths"][("A", "B")]["ci"]
    assert lo < q["paths"][0]["std"] < hi and boot["failed"] == 0
    ml = sem.bootstrap(x, cols, spec, "ml", draws=100, seed=1)
    lo, hi = ml["indirect"][("A", "B", "C")]["ci"]
    assert lo < 0.2 < hi


def test_rmsea_power_against_simulation():
    # MacCallum, Browne & Sugawara (1996): reject close fit (RMSEA .05) when the model fits badly (.08)
    rng = np.random.default_rng(10)
    n, df = 300, 30
    lam0, lam1 = (n - 1) * df * 0.05 ** 2, (n - 1) * df * 0.08 ** 2
    crit = np.quantile(rng.noncentral_chisquare(df, lam0, 400_000), 0.95)
    simulated = np.mean(rng.noncentral_chisquare(df, lam1, 400_000) > crit)
    assert sem.rmsea_power(n, df) == pytest.approx(simulated, abs=0.005)
    need = sem.rmsea_n(df)
    assert sem.rmsea_power(need, df) >= 0.8 > sem.rmsea_power(need - 1, df)


def test_the_bootstrap_job_runs_on_plain_json():
    """The input travels as JSON (estimation process, Rechenknecht): lists instead of tuples."""
    import json

    from app import compute

    rng = np.random.default_rng(11)
    x, cols, spec = _three(rng, 300)
    inp = json.loads(json.dumps({"key": "k", "rows": x.tolist(), "cols": cols, "method": "cb", "draws": 60, "seed": 1,
                                 "spec": {"latent": spec["latent"], "observed": [], "paths": spec["paths"],
                                          "indirect": spec["indirect"]}}))
    seen = []
    out = compute.runner("sem")(inp, seen.append)
    assert out["key"] == "k" and out["draws"] == 60 and out["failed"] == 0 and seen
    [(x_, m, y, lo, hi, se)] = out["indirect"]
    assert (x_, m, y) == ("A", "B", "C") and lo < hi and se > 0
    json.dumps(out)  # stored as JSON


def test_path_power_in_the_planned_model():
    """Satorra & Saris (1985): with single observed variables the path test needs the N of the correlation test; with
    constructs (loadings .7) more, and a simulation reaches the planned power."""
    from app import power

    simple = {"observed": ["x", "y"], "paths": [("x", "y")]}
    assert sem.path_n(simple, {("x", "y"): 0.3}, [("x", "y")]) == pytest.approx(power.needed("r", 0.3), abs=1)
    spec = {"latent": {"A": ["a1", "a2", "a3", "a4"], "B": ["b1", "b2", "b3"]}, "paths": [("A", "B")]}
    n = sem.path_n(spec, {("A", "B"): 0.3}, [("A", "B")])
    assert power.needed("r", 0.3) < n < 200
    sigma = sem.population(spec, {("A", "B"): 0.3})
    cols = sem.columns(spec)
    rng = np.random.default_rng(12)
    hits = 0
    for _ in range(150):
        r = sem.fit_ml(rng.multivariate_normal(np.zeros(len(cols)), sigma, size=n), cols, spec, robust=False)
        hits += r["converged"] and r["paths"][0]["p"] is not None and r["paths"][0]["p"] < 0.05
    assert hits / 150 == pytest.approx(0.8, abs=0.08)
    # mediation: both paths (joint significance) need more than either alone
    med = {"observed": ["x", "m", "y"], "paths": [("x", "m"), ("m", "y"), ("x", "y")]}
    betas = {("x", "m"): 0.3, ("m", "y"): 0.3}
    assert sem.path_n(med, betas, [("x", "m"), ("m", "y")]) > sem.path_n(med, betas, [("x", "m")])
    assert sem.population(med, {("x", "m"): 0.9, ("m", "y"): 0.9, ("x", "y"): 0.9}) is None  # R² above 1


def test_rmsea_planner_in_the_browser_matches_python():
    from tests.analysis.test_samplesize import NODE, js

    if NODE is None:
        pytest.skip("needs Node")
    got = js(["rmseaPower", 300, 30, 0.05, 0.08, 0.05], ["rmseaPower", 150, 80, 0.05, 0.08, 0.05])
    assert got == pytest.approx([sem.rmsea_power(300, 30), sem.rmsea_power(150, 80)], abs=1e-6)


# --- audit 2026-10-06 (SEM-2, SEM-3, SEM-7) ---------------------------------------------------------------------------

def _units_data():
    """The case of the audit's t3b_skala.py: x -> A -> y, x in an ever larger unit."""
    rng = np.random.default_rng(5)
    n = 400
    f = rng.normal(0, 1, n)
    x0 = 0.5 * f + rng.normal(0, 1, n)
    items = np.column_stack([.7 * f + rng.normal(0, .7, n) for _ in range(3)])
    y = 0.4 * f + rng.normal(0, 1, n)
    cols = ["a1", "a2", "a3", "x", "y"]
    spec = {"latent": {"A": cols[:3]}, "observed": ["x", "y"], "paths": [("x", "A"), ("A", "y")],
            "indirect": [("x", "A", "y")]}
    return items, x0, y, cols, spec


def test_the_unit_of_an_observed_variable_changes_nothing():
    """SEM-2: ML does not depend on the units. Before, from an SD of about 100 the model counted as not identified,
    from 10^4 the path stayed at its start value 0 (chi² 115.96 instead of 2.89)."""
    items, x0, y, cols, spec = _units_data()
    rows = []
    for k in (1, 10, 100, 1000, 10_000, 100_000):
        r = sem.fit_ml(np.column_stack([items, x0 * k, y]), cols, spec)
        p0 = r["paths"][0]
        rows.append((k, r["chi2"], r["cfi"], p0["std"], p0["z"], p0["est"] * k, p0["se"] * k, r["problems"]))
    for k, chi2, cfi, std, z, est, se, problems in rows:
        assert chi2 == pytest.approx(2.8917, abs=1e-3) and cfi == pytest.approx(1.0)
        assert std == pytest.approx(0.4122, abs=1e-3) and z == pytest.approx(7.219, abs=1e-2)
        assert est == pytest.approx(rows[0][5], rel=1e-5) and se == pytest.approx(rows[0][6], rel=1e-5)
        assert problems == []
    # the bootstrap's start (in the data's units) is taken over too
    big = np.column_stack([items, x0 * 10_000, y])
    full = sem.fit_ml(big, cols, spec, robust=False, light=True)
    again = sem.fit_ml(big, cols, spec, robust=False, light=True, start=full["theta"])
    assert again["converged"] and again["theta"] == pytest.approx(full["theta"], rel=1e-6)


def test_constructs_correlated_beyond_one_are_not_admissible():
    """SEM-3 (t3_grenzfaelle.py): one true factor, two constructs of two statements each, n = 120: a correlation of
    the constructs above 1 is an inadmissible solution."""
    rng = np.random.default_rng(11)
    cols = ["a1", "a2", "b1", "b2"]
    spec = {"latent": {"A": cols[:2], "B": cols[2:]}, "observed": [], "paths": []}
    seen = admissible_seen = 0
    for _ in range(400):
        f = rng.normal(0, 1, 120)
        x = np.column_stack([.5 * f + rng.normal(0, .9, 120) for _ in range(4)])
        r = sem.fit_ml(x, cols, spec)
        if not r.get("converged"):
            continue
        c = r["latent_corr"]["matrix"][0][1]
        if abs(c) > 1:
            assert not r["admissible"]
            if "heywood" not in r["problems"]:  # with a negative variance the correlation means nothing
                assert "latent_pd" in r["problems"]
                seen += 1
        elif "heywood" not in r["problems"]:
            assert "latent_pd" not in r["problems"]
            admissible_seen += 1
        if seen >= 3 and admissible_seen >= 3:
            break
    assert seen >= 3 and admissible_seen >= 3


def test_the_bootstrap_counts_inadmissible_draws():
    """SEM-7 (t3_grenzfaelle.py): a weak construct of two statements, n = 100: draws without a solution are left
    out, draws with an inadmissible solution stay in and are counted; the page calls the intervals unsure."""
    from app import sem_model

    rng = np.random.default_rng(11)
    n = 100
    x = rng.normal(0, 1, n)
    m = .3 * x + rng.normal(0, 1, n)
    y = .3 * m + rng.normal(0, 1, n)
    data = np.column_stack([.45 * m + rng.normal(0, 1, n), .45 * m + rng.normal(0, 1, n), x, y])
    cols = ["m1", "m2", "x", "y"]
    spec = {"latent": {"M": cols[:2]}, "observed": ["x", "y"], "paths": [("x", "M"), ("M", "y"), ("x", "y")],
            "indirect": [("x", "M", "y")]}
    full = sem.fit_ml(data, cols, spec, robust=False, light=True)
    res = sem.bootstrap(data, cols, spec, "cb", draws=200, seed=1, start=full["theta"])
    assert res["inadmissible"] > 0
    assert sem_model.unsure(res)
    assert not sem_model.unsure({"draws": 2000, "failed": 50, "inadmissible": 49})
    assert sem_model.unsure({"draws": 2000, "failed": 50, "inadmissible": 50})
    rng = np.random.default_rng(9)
    x, cols, spec = _three(rng, 500)
    clean = sem.bootstrap(x, cols, spec, "pls", draws=20, seed=1)
    assert clean["inadmissible"] == 0 and clean["failed"] == 0


def test_a_collapsed_estimate_is_not_admissible():
    """Two almost identical predictors give standardised paths of hundreds (in a survey 10^40 and R² below 0): no
    values but a collapse, flagged "extreme" (Ralf, 2026-10-07). Since the information matrix is built as one matrix
    product (code review 2026-10-15, A3) this example ends in the split solution instead and is flagged "not
    identified"; either way it is inadmissible, so the diagram shows no values."""
    rng = np.random.default_rng(3)
    n = 300
    x1 = rng.normal(size=n)
    x2 = x1 + rng.normal(size=n) * 1e-4
    y = 0.5 * x1 + rng.normal(size=n)
    spec = {"observed": ["x1", "x2", "y"], "paths": [("x1", "y"), ("x2", "y")], "indirect": []}
    r = sem.fit_ml(np.column_stack([x1, x2, y]), ["x1", "x2", "y"], spec)
    assert {"extreme", "identified"} & set(r["problems"]) and not r["admissible"]


def _dense_scaling(x, sigma, delta):
    """tr(U Gamma) / df with dense matrices (Satorra & Bentler, 1994), for checking the blockwise computation."""
    p = x.shape[1]
    rows, cols = np.tril_indices(p)
    c = x - x.mean(0)
    gamma = np.cov(np.stack([c[:, i] * c[:, j] for i, j in zip(rows, cols)], 1), rowvar=False, bias=True)
    dup = np.zeros((p * p, len(rows)))
    for k, (i, j) in enumerate(zip(rows, cols)):
        dup[i * p + j, k] = dup[j * p + i, k] = 1
    inv = np.linalg.inv(sigma)
    w = 0.5 * dup.T @ np.kron(inv, inv) @ dup
    u = w - w @ delta @ np.linalg.inv(delta.T @ w @ delta) @ delta.T @ w
    return float(np.trace(u @ gamma)) / (len(rows) - delta.shape[1])


def test_robust_fit_indices():
    """SEM extensions 2026-10-17, point 1: robust CFI, TLI and RMSEA (Brosseau-Liard et al., 2012; Brosseau-Liard &
    Savalei, 2014). With normal answers they are close to the normal-theory ones; the scaling factor of the null model
    equals the dense formula; with heavy tails the robust RMSEA uses T - c df."""
    rng = np.random.default_rng(11)
    x, cols, spec = _three(rng, 900)
    r = sem.fit_ml(x, cols, spec)
    rob = r["robust_fit"]
    assert rob["cfi"] == pytest.approx(r["cfi"], abs=0.01) and rob["rmsea"] == pytest.approx(r["rmsea"], abs=0.01)
    x, cols, spec = _three(rng, 400, elliptical=True)
    r = sem.fit_ml(x, cols, spec)
    rob, c = r["robust_fit"], r["scaled"]["c"]
    assert c > 1.2
    assert rob["rmsea"] == pytest.approx(math.sqrt(max(r["chi2"] - c * r["df"], 0) / (r["n"] * r["df"])))
    xs = x / x.std(0)
    s = np.cov(xs, rowvar=False, bias=True)
    p = s.shape[0]
    rows, cols_ = np.tril_indices(p)
    delta = np.zeros((len(rows), p))
    for k, (i, j) in enumerate(zip(rows, cols_)):
        if i == j:
            delta[k, i] = 1
    assert rob["c_base"] == pytest.approx(_dense_scaling(xs, np.diag(np.diag(s)), delta), rel=1e-8)
    t_b, df_b = r["baseline"]
    d_m, d_b = r["chi2"] - c * r["df"], t_b - rob["c_base"] * df_b
    assert rob["cfi"] == pytest.approx(1 - max(d_m, 0) / max(d_m, d_b, 0))
    assert rob["tli"] == pytest.approx(1 - (d_m / r["df"]) / (d_b / df_b))
    lo, hi = rob["rmsea_ci"]
    assert lo <= rob["rmsea"] <= hi


def test_largest_residuals_point_at_the_missing_relation():
    """Point 12: an error correlation the model leaves out shows up as the largest residual correlation."""
    rng = np.random.default_rng(12)
    n = 2000
    f = rng.normal(size=n)
    shared = rng.normal(size=n)
    x = np.column_stack([0.7 * f + 0.5 * rng.normal(size=n) + (0.6 * shared if k in (1, 3) else 0) for k in range(5)])
    cols = [f"i{k}" for k in range(5)]
    r = sem.fit_ml(x, cols, {"latent": {"F": cols}})
    a, b, v = r["residuals"][0]
    assert {a, b} == {"i1", "i3"} and v > sem.RESIDUAL_MARK
    s = np.corrcoef(x, rowvar=False)
    assert len(r["residuals"]) == sem.RESIDUAL_TOP and abs(v) <= 1 and abs(s[1, 3]) > abs(v)


def test_johnson_neyman_region_by_hand():
    """Point 14: the bounds are where the slope's z is exactly 1.96."""
    b1, b3, v11, v13, v33 = 0.20, 0.15, 0.004, 0.0005, 0.002
    found = sem.slope_region(b1, b3, v11, v13, v33)
    assert found["kind"] == "outside"
    for w in found["bounds"]:
        assert abs(b1 + b3 * w) / math.sqrt(v11 + 2 * w * v13 + w * w * v33) == pytest.approx(sem.Z975)
    lo, hi = found["bounds"]
    inside = (lo + hi) / 2
    assert abs(b1 + b3 * inside) / math.sqrt(v11 + 2 * inside * v13 + inside ** 2 * v33) < sem.Z975
    assert sem.slope_region(0.5, 0.0, 0.001, 0, 0.0001)["kind"] == "inside"  # far from 0 the slope is unsure again
    assert sem.slope_region(0.0, 0.01, 0.01, 0, 0.01)["kind"] == "none"
    assert sem.slope_region(0.0, 0.3, 0.01, 0, 0.001)["kind"] == "outside"


def test_the_bootstrap_job_keeps_single_questions():
    """SEM extensions 2026-10-17, point 7: the job gets the reliability of a single question along (without it every
    draw of a construct with one indicator failed)."""
    import json

    from app import compute

    rng = np.random.default_rng(13)
    n = 300
    t = rng.normal(size=n)
    x = np.column_stack([math.sqrt(0.7) * t + math.sqrt(0.3) * rng.normal(size=n)])
    f = 0.5 * t + math.sqrt(0.75) * rng.normal(size=n)
    data = np.hstack([_items(rng, f, 3), x])
    inp = json.loads(json.dumps({"key": "k", "rows": data.tolist(), "cols": ["y0", "y1", "y2", "x°"], "method": "cb",
                                 "draws": 40, "seed": 1,
                                 "spec": {"latent": {"Y": ["y0", "y1", "y2"], "x": ["x°"]}, "observed": [],
                                          "paths": [["x", "Y"]], "indirect": [], "single": {"x": 0.7}}}))
    out = compute.runner("sem")(inp, lambda *_: None)
    assert out["failed"] == 0 and out["paths"][0][2] < out["paths"][0][3]


def test_pls_figures_by_hand():
    """Point 13: R² adjusted, f² and VIF of the structural model from the construct scores."""
    rng = np.random.default_rng(21)
    x, cols, spec = _three(rng, 500)
    r = sem.fit_pls(x, cols, spec)
    n = 500
    r2 = r["r2"]["C"]
    assert r["r2_adj"]["C"] == pytest.approx(1 - (1 - r2) * (n - 1) / (n - 3))
    s = sem.fit_pls(x, cols, spec, light=True, scores=True)["scores"]
    without = sem._r2_of(s["C"], np.column_stack([s["A"]]))
    assert r["f2"][("B", "C")] == pytest.approx((r2 - without) / (1 - r2))
    rab = np.corrcoef(s["A"], s["B"])[0, 1]
    assert r["vif"][("A", "C")] == pytest.approx(1 / (1 - rab ** 2)) and r["vif"][("A", "B")] == 1.0


def test_pls_predict_beats_the_mean_and_says_its_power():
    rng = np.random.default_rng(22)
    x, cols, spec = _three(rng, 600)
    found = sem.pls_predict(x, cols, spec)
    assert found["folds"] == 10 and {i["construct"] for i in found["items"]} == {"B", "C"}
    assert all(i["q2"] > 0 for i in found["items"])
    assert set(found["constructs"].values()) <= {"high", "medium", "low", "none"}
    assert sem.pls_predict(x[:15], cols, spec) is None  # too few cases for ten folds


def test_formative_weights_recover_an_index():
    """Point 9: mode B weights are the regression of the inner proxy on the indicators; an index built from three
    uncorrelated parts with weights .6, .3 and .1 is found in that order, with VIF near 1."""
    rng = np.random.default_rng(23)
    n = 2000
    parts = rng.normal(size=(n, 3))
    index = parts @ np.array([0.6, 0.3, 0.1])
    y = 0.6 * index / index.std() + math.sqrt(1 - 0.36) * rng.normal(size=n)
    ys = _items(rng, y / y.std(), 3)
    x = np.hstack([parts, ys])
    cols = ["f0", "f1", "f2", "y0", "y1", "y2"]
    spec = {"latent": {"F": cols[:3], "Y": cols[3:]}, "paths": [("F", "Y")], "formative": ["F"]}
    r = sem.fit_pls(x, cols, spec)
    w = [row["weight"] for row in r["loadings"]["F"]]
    assert w[0] > w[1] > w[2] > 0 and all(abs(v - 1) < 0.1 for v in r["formative"]["F"])
    assert "F" not in r["quality"]["ave"]
    b = sem.bootstrap(x, cols, spec, "pls", draws=60)
    assert b["weights"][("F", "f0")]["ci"][0] > 0 and b["htmt"] == {}


def test_fiml_equals_ml_without_gaps_and_uses_every_case():
    """SEM extensions 2026-10-17, point 2: on complete data FIML is ML (same χ², paths and robust errors); with values
    missing completely at random it keeps every case and still finds the paths, its test scaled (Yuan & Bentler)."""
    from app import sem_means

    rng = np.random.default_rng(31)
    x, cols, spec = _three(rng, 500, elliptical=True)
    ml, fiml = sem.fit_ml(x, cols, spec), sem_means.fit_fiml(x, cols, spec)
    assert fiml["chi2"] == pytest.approx(ml["chi2"], rel=1e-8) and fiml["df"] == ml["df"]
    assert [p["std"] for p in fiml["paths"]] == pytest.approx([p["std"] for p in ml["paths"]], abs=1e-7)
    # the errors from the observed information (MLR with missing values; Yuan & Bentler, 2000): close to MLM, not equal
    assert [p["se"] for p in fiml["paths"]] == pytest.approx([p["se"] for p in ml["paths"]], rel=0.03)
    # the SRMR of FIML counts the (here zero) residual means too: p* + p terms instead of p*
    p = len(cols)
    assert fiml["cfi"] == pytest.approx(ml["cfi"], abs=1e-9)
    assert fiml["srmr"] == pytest.approx(ml["srmr"] * math.sqrt((p * (p + 1) / 2) / (p * (p + 1) / 2 + p)), abs=1e-6)
    gaps = x.copy()
    gaps[rng.random(gaps.shape) < 0.1] = np.nan
    found = sem_means.fit_fiml(gaps, cols, spec)
    assert found["n"] == 500 and found["n_complete"] < 200 and found["scaled"]["c"] > 1
    assert [p["std"] for p in found["paths"]] == pytest.approx([p["std"] for p in ml["paths"]], abs=0.06)
    mu, sigma, ok = sem_means.saturated(gaps)
    assert ok and np.allclose(mu, np.nanmean(gaps, axis=0), atol=0.05)


def test_bivariate_normal_and_wlsmv_on_ordinal_data():
    """Point 3: the bivariate normal against numerical integration; on statements cut into four categories WLSMV
    finds the loadings of .70 (ML on the codes underestimates them) and its scaled-shifted χ² has the mean df."""
    from app import sem_ordinal as so

    def ref(h, k, r):
        xs = np.linspace(-9, h, 20001)
        return np.trapezoid(so.phi(xs) * so.Phi((k - r * xs) / math.sqrt(1 - r * r)), xs)

    for r in (-0.97, -0.6, 0.0, 0.4, 0.97):
        for h, k in ((-1.2, 0.3), (0.5, 2.0), (1.0, -1.0)):
            assert float(so.bvn(h, k, r)) == pytest.approx(ref(h, k, r), abs=1e-7)
    rng = np.random.default_rng(32)
    cuts = np.array([-1.0, 0.0, 0.8])
    stats, loads, ml_loads, paths = [], [], [], []
    for _ in range(12):
        x, cols, spec = _three(rng, 500)
        codes = np.digitize(x, cuts).astype(float) + 1
        r = so.fit_wlsmv(codes, cols, spec)
        assert r["converged"] and r["admissible"]
        stats.append(r["scaled"]["chi2"])
        loads.append(np.mean([row["std"] for rows in r["loadings"].values() for row in rows]))
        ml_loads.append(np.mean([row["std"] for rows in sem.fit_ml(codes, cols, spec)["loadings"].values() for row in rows]))
        paths.append(r["paths"][0]["std"])
    assert abs(np.mean(stats) - 41) < 6 and abs(np.mean(loads) - 0.7) < 0.015 and np.mean(ml_loads) < 0.68
    assert abs(np.mean(paths) - 0.5) < 0.03 and so.ordinal_columns(codes) and not so.ordinal_columns(x)


def test_power_by_simulation_meets_the_planned_n():
    """SEM extensions 2026-10-17, point 16: at the N that the Satorra-Saris approximation plans for 80 % power, the
    simulation from the same population finds the path in about 80 % of the data sets."""
    from app import sem_power_job

    spec = {"latent": {"A": ["a0", "a1", "a2"], "B": ["b0", "b1", "b2"]}, "paths": [("A", "B")]}
    n = sem.path_n(spec, {("A", "B"): 0.25}, [("A", "B")])
    out = sem_power_job.run({"key": "k", "spec": {**spec, "paths": [["A", "B"]], "indirect": []},
                             "betas": [["A", "B", 0.25]], "n": n, "reps": 300, "seed": 2,
                             "hyps": [{"number": 1, "kind": "effect", "paths": [["A", "B"]], "cov": [], "direction": "pos"},
                                      {"number": 2, "kind": "effect", "paths": [["A", "B"]], "cov": [], "direction": "neg"}]})
    power = dict(out["power"])
    assert abs(power[1] - 0.8) < 0.07 and power[2] < 0.02 and out["failed"] < 15
