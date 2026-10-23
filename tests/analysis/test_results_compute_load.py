"""The heavy computations of the results page stay fast with large samples and give the same numbers as before
(code review 2026-10-15, package A): Fisher's exact test in logs (A1), the Monte Carlo chi-square test with tables
drawn at fixed margins (A2), the robust ML parts without the p* x p* matrices (A3), one noncentral chi-square for
sem, power and the pilot (A6), the cached RMSEA sample size (A7), the bootstrap of preference shares in blocks (A8)
and the omega locks of a process (A12)."""
import math
import random
import time

import numpy as np
import pytest

from app import group_tests as gt
from app import power, power_pilot, sem


# --- A1: Fisher's exact test ------------------------------------------------------------------------------------

def _fisher_exact_integers(table):
    """The former implementation: whole-number binomial coefficients, every table of the margins."""
    (a, b), (c, d) = table
    r1, r2, c1 = a + b, c + d, a + c
    whole = math.comb(r1 + r2, c1)
    probs = [math.comb(r1, x) * math.comb(r2, c1 - x) / whole for x in range(max(0, c1 - r2), min(r1, c1) + 1)]
    seen = math.comb(r1, a) * math.comb(r2, c) / whole
    return min(1.0, sum(q for q in probs if q <= seen * (1 + 1e-7)))


def test_fisher_in_logs_equals_the_exact_integers():
    rng = random.Random(7)
    tables = [[[a, b], [c, d]] for a in (0, 1, 3, 7) for b in (0, 2, 9) for c in (1, 4, 12) for d in (0, 5, 30)]
    tables += [[[rng.randint(0, 400), rng.randint(0, 400)], [rng.randint(0, 400), rng.randint(0, 400)]]
               for _ in range(40)]
    tables += [[[500, 520], [480, 530]], [[1, 999], [10, 990]], [[50, 50], [50, 50]]]  # the middle table: p = 1
    for t in tables:
        if sum(map(sum, t)):
            old = _fisher_exact_integers(t)
            assert gt.fisher_2x2(t) == pytest.approx(old, rel=1e-9, abs=1e-15), t
    # hypergeometric, two-sided: 3 1 / 1 3 gives p = 0.4857143; 1 9 / 6 4 gives 0.05728
    assert gt.fisher_2x2([[3, 1], [1, 3]]) == pytest.approx(0.4857142857, abs=1e-9)
    assert gt.fisher_2x2([[1, 9], [6, 4]]) == pytest.approx(0.0572755, abs=1e-6)


def test_fisher_with_forty_thousand_cases_is_fast():
    start = time.process_time()
    p = gt.fisher_2x2([[10_000, 10_000], [9_800, 10_200]])
    assert time.process_time() - start < 1.0  # was about 2 minutes
    assert 0.03 < p < 0.06  # chi-square p of the same table: 0.046
    assert gt.fisher_2x2([[20_000, 0], [0, 20_000]]) == 0.0  # beyond the smallest float


# --- A2: Monte Carlo chi-square ---------------------------------------------------------------------------------

def test_monte_carlo_with_ten_thousand_cases_is_fast():
    table = [[1200, 900, 4], [1300, 1000, 3], [2700, 2890, 3]]  # 10 000 cases, expected counts below 5
    start = time.process_time()
    t = gt.chi2_test(table)
    assert time.process_time() - start < 1.0  # was about 11 s
    assert t["cochran"] and t["exact"] == "monte_carlo" and t["draws"] == gt.DRAWS
    assert abs(t["exact_p"] - t["p"]) < 0.05  # large n: close to the asymptotic p


# --- A3: robust ML without the dense p* x p* matrices -------------------------------------------------------------

def _dense_gamma(x):
    rows, cols = np.tril_indices(x.shape[1])
    c = x - x.mean(axis=0)
    d = c[:, rows] * c[:, cols]
    d -= d.mean(axis=0)
    return d.T @ d / len(x)


def _dense_meat(x, wd, w=None):  # unweighted only (SEM extensions 2026-10-17: the weights are an argument)
    return wd.T @ _dense_gamma(x) @ wd


def _dense_trace(x, inv, w=None):
    rows, cols = np.tril_indices(x.shape[1])
    t = inv[np.ix_(rows, rows)] * inv[np.ix_(cols, cols)] + inv[np.ix_(rows, cols)] * inv[np.ix_(cols, rows)]
    off = (rows != cols).astype(float)
    w = 0.5 * t * np.outer(1 + off, 1 + off) / 2
    return float(np.sum(w * _dense_gamma(x).T))


def _three(rng, n):
    a = rng.normal(size=n)
    b = 0.5 * a + math.sqrt(0.75) * rng.normal(size=n)
    c = 0.4 * b + 0.3 * a + 0.8 * rng.normal(size=n)
    x = np.hstack([np.column_stack([0.7 * f + 0.7 * rng.normal(size=n) for _ in range(k)])
                   for f, k in ((a, 4), (b, 3), (c, 4))])
    x *= np.sqrt(6 / rng.chisquare(8, size=n))[:, None]  # heavy tails: the scaling differs from 1
    cols = [f"a{i}" for i in range(4)] + [f"b{i}" for i in range(3)] + [f"c{i}" for i in range(4)]
    spec = {"latent": {"A": cols[:4], "B": cols[4:7], "C": cols[7:]},
            "paths": [("A", "B"), ("B", "C"), ("A", "C")], "indirect": [("A", "B", "C")]}
    return x, cols, spec


def test_robust_parts_equal_the_dense_matrices(monkeypatch):
    rng = np.random.default_rng(11)
    x, cols, spec = _three(rng, 700)  # more than one block of sem.CHUNK cases
    new = sem.fit_ml(x, cols, spec)
    groups = [x[:350], x[350:]]
    new_groups = sem.fit_ml_groups(groups, cols, spec)
    monkeypatch.setattr(sem, "_meat", _dense_meat)
    monkeypatch.setattr(sem, "_trace_w_gamma", _dense_trace)
    old = sem.fit_ml(x, cols, spec)
    old_groups = sem.fit_ml_groups(groups, cols, spec)
    assert new["scaled"]["c"] == pytest.approx(old["scaled"]["c"], rel=1e-10)
    assert new["scaled"]["chi2"] == pytest.approx(old["scaled"]["chi2"], rel=1e-10)
    assert abs(new["scaled"]["c"] - 1) > 0.1
    for a, b in zip(new["paths"], old["paths"]):
        assert (a["se"], a["std_se"]) == pytest.approx((b["se"], b["std_se"]), rel=1e-10)
    for lv in spec["latent"]:
        assert [r["se"] for r in new["loadings"][lv]] == pytest.approx([r["se"] for r in old["loadings"][lv]],
                                                                     rel=1e-10)
    for key, d in new_groups["diff"].items():
        assert d["se"] == pytest.approx(old_groups["diff"][key]["se"], rel=1e-10)


def test_robust_measurement_model_of_many_items_is_fast():
    rng = np.random.default_rng(12)
    n, k, per = 2000, 10, 10  # 100 statements: Gamma would be 5050 x 5050
    f = rng.normal(size=(n, k))
    x = np.hstack([0.7 * f[:, [j]] + 0.7 * rng.normal(size=(n, per)) for j in range(k)])
    cols = [f"i{j}_{m}" for j in range(k) for m in range(per)]
    spec = {"latent": {f"F{j}": cols[j * per:(j + 1) * per] for j in range(k)}}
    start = time.process_time()
    r = sem.fit_ml(x, cols, spec)
    assert r["converged"] and r["scaled"]["c"] == pytest.approx(1, abs=0.1)
    assert time.process_time() - start < 10  # was 27 s for the robust part alone and 1 GB, about 1 s now


# --- A6, A7: noncentral chi-square --------------------------------------------------------------------------------

def _ncx2_from_zero(x, df, lam):
    """The former sum over j = 0, 1, ... of the Poisson weights times Q(df/2 + j, x/2), each Q on its own."""
    half = lam / 2
    if half <= 0:
        return min(1.0, gt.gammaincc(df / 2, x / 2))
    total = 0.0
    for j in range(math.ceil(half + 12 * math.sqrt(half) + 60) + 1):
        total += math.exp(-half + (j * math.log(half) if j else 0.0) - math.lgamma(j + 1)) * gt.gammaincc(df / 2 + j,
                                                                                                         x / 2)
    return min(1.0, total)


def test_one_noncentral_chi_square_equals_the_former_sum():
    assert sem.ncx2_sf is gt.ncx2_sf and power_pilot._ncx2_sf is gt.ncx2_sf
    for x in (0.5, 3.0, 20.0, 150.0, 2500.0):
        for df in (1, 5, 40, 300):
            for lam in (0.0, 1e-9, 0.7, 12.0, 200.0, 3000.0):
                assert gt.ncx2_sf(x, df, lam) == pytest.approx(_ncx2_from_zero(x, df, lam), abs=1e-10)
    assert power.chi2_power(200, 4, 0.3) == pytest.approx(_ncx2_from_zero(power._chi2_quantile(0.05, 4), 4, 18.0),
                                                          abs=1e-12)


def test_rmsea_interval_is_fast_and_the_plan_cached():
    start = time.process_time()
    lo, hi = sem.rmsea_ci(5000.0, 1200, 20_000)
    assert time.process_time() - start < 1.0
    assert 0 < lo < math.sqrt((5000 - 1200) / (1200 * 20_000)) < hi
    sem.rmsea_n.cache_clear()
    assert sem.rmsea_n(10) == 782
    assert sem.rmsea_n(10) == 782 and sem.rmsea_n.cache_info().hits == 1


# --- A8: bootstrap of the preference shares in blocks --------------------------------------------------------------

def test_bootstrap_of_shares_in_blocks_equals_one_piece(monkeypatch):
    from app import choice_predict

    rng = np.random.default_rng(3)
    P, w = rng.random((300, 4)), rng.random(300)
    whole = choice_predict.bootstrap_means(P, w, draws=500)
    monkeypatch.setattr(choice_predict, "BLOCK", 7 * 300)  # seven samples at once
    assert np.allclose(choice_predict.bootstrap_means(P, w, draws=500), whole, rtol=0, atol=1e-14)
    assert choice_predict.bootstrap_means(P, w, draws=500).shape == (500, 4)


# --- A12: the omega locks of a process -----------------------------------------------------------------------------

def test_trimming_the_omega_locks_keeps_the_held_ones(monkeypatch):
    import threading

    from app import results

    locks = {(1, str(k)): threading.Lock() for k in range(70)}
    monkeypatch.setattr(results, "_omega_locks", locks)
    held = locks[(1, "5")]
    with held:
        results._trim_omega_locks()
        assert results._omega_locks == {(1, "5"): held}
    results._trim_omega_locks()
    assert results._omega_locks == {(1, "5"): held}  # below the limit: kept
