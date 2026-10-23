"""95 % confidence intervals and the item analysis of the results page (app/intervals.py, scales.item_analysis,
psm_analysis.point_intervals; concept statistik-erweiterungen-konzept.md, stage 1)."""
import math

import numpy as np
import pytest

from app import intervals, psm_analysis, results, scales


def test_wilson_interval_of_a_share():
    lo, hi = intervals.wilson(7, 20)
    assert (round(lo, 3), round(hi, 3)) == (0.181, 0.567)
    assert intervals.wilson(0, 10)[0] == 0 and intervals.wilson(10, 10)[1] == 1     # stays within 0 … 1
    assert intervals.wilson(3, 9) is None                                            # below n = 10: none


def test_t_quantiles_and_the_interval_of_a_mean():
    # Student's t, 97.5 % quantile (tables): df 10 -> 2.2281, df 29 -> 2.0452, df 1000 -> 1.9623
    for df, q in ((10, 2.228139), (29, 2.045230), (1000, 1.962339)):
        assert abs(intervals.t_quantile(0.975, df) - q) < 1e-5
    lo, hi = intervals.mean_ci(3.0, 1.0, 30)
    assert abs((hi - lo) / 2 - 2.045230 / math.sqrt(30)) < 1e-5 and abs((lo + hi) / 2 - 3) < 1e-12
    assert intervals.mean_ci(3.0, 1.0, 9) is None and intervals.mean_ci(3.0, None, 30) is None


def _f_quantile(p, d1, d2):
    """Own computation: the F distribution function is I_{d1 x / (d1 x + d2)}(d1/2, d2/2); bisection on it."""
    lo, hi = 0.0, 100.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if scales._betainc(d1 / 2, d2 / 2, d1 * mid / (d1 * mid + d2)) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def test_feldt_interval_of_alpha():
    # tables of the F distribution: F(0.975; 10, 20) = 2.7737, F(0.975; 20, 10) = 3.4185, F(0.025; 10, 20) = 1 / 3.4185
    assert abs(_f_quantile(0.975, 10, 20) - 2.7737) < 1e-4 and abs(_f_quantile(0.975, 20, 10) - 3.4185) < 1e-4
    assert abs(intervals.f_quantile(0.025, 10, 20) - 1 / 3.4185) < 1e-4
    # alpha = .80 from n = 11 people and k = 3 items: (1 - rho) / (1 - .80) ~ F(10, 20) (Feldt et al., 1987)
    lo, hi = intervals.feldt(0.80, 11, 3)
    assert abs(lo - (1 - 0.2 * _f_quantile(0.975, 10, 20))) < 1e-8 and abs(hi - (1 - 0.2 * _f_quantile(0.025, 10, 20))) < 1e-8
    assert (round(lo, 3), round(hi, 3)) == (0.445, 0.941)
    # large samples: still converges and narrows around alpha
    lo, hi = intervals.feldt(0.85, 2000, 10)
    assert lo < 0.85 < hi and hi - lo < 0.02
    assert intervals.feldt(0.8, 9, 3) is None and intervals.feldt(None, 50, 3) is None


def test_fisher_z_interval_and_spearman_brown():
    lo, hi = intervals.fisher_r(0.5, 50)
    assert (round(lo, 3), round(hi, 3)) == (0.257, 0.683)
    assert intervals.fisher_r(0.5, 9) is None and intervals.fisher_r(1.0, 50) is None
    rng = np.random.default_rng(3)
    z = rng.standard_normal(40)
    x = np.column_stack([z + rng.standard_normal(40), z + rng.standard_normal(40)]).tolist()
    r, n, _ = scales.pearson([a for a, _ in x], [b for _, b in x])
    rlo, rhi = intervals.fisher_r(r, n)
    lo, hi = intervals.spearman_brown(x)
    assert abs(lo - 2 * rlo / (1 + rlo)) < 1e-12 and abs(hi - 2 * rhi / (1 + rhi)) < 1e-12
    assert lo < scales.spearman_brown(x) < hi


def _scale(n, k, seed):
    rng = np.random.default_rng(seed)
    z = rng.standard_normal(n)
    return np.clip(np.round(3 + z[:, None] * rng.uniform(0.4, 1.0, k) + rng.standard_normal((n, k))), 1, 5).tolist()


def test_omega_bootstrap_is_fixed_and_holds_the_estimate():
    x = _scale(120, 5, 7)
    ci = intervals.omega(x)
    assert ci == intervals.omega(x)                                                  # fixed seed: same interval
    assert ci[0] < scales.mcdonald_omega(x) < ci[1]
    assert intervals.omega(x[:29]) is None                                           # bootstrap from n = 30 on
    # the draws start from the fit of the whole sample: the same omega as the full search
    a = np.asarray(x)
    start = scales._one_factor(np.cov(a, rowvar=False))[1]
    rng = np.random.default_rng(1)
    for _ in range(20):
        draw = a[rng.integers(0, len(a), len(a))]
        assert abs(scales.mcdonald_omega(draw, start) - scales.mcdonald_omega(draw.tolist())) < 1e-6


def test_item_analysis_against_numpy():
    x = _scale(200, 4, 11)
    x = [row[:3] + [6 - row[3]] for row in x]                                        # one item runs against the others
    rows = scales.item_analysis(x, [(1, 5)] * 4)
    a = np.asarray(x, dtype=float)
    alpha = results.cronbach_alpha(x)
    for j, row in enumerate(rows):
        rest = np.delete(a, j, axis=1).sum(axis=1)
        assert abs(row["rit"] - np.corrcoef(a[:, j], rest)[0, 1]) < 1e-12
        assert abs(row["mean"] - a[:, j].mean()) < 1e-12 and abs(row["sd"] - a[:, j].std(ddof=1)) < 1e-12
        assert abs(row["p"] - (a[:, j].mean() - 1) / 4 * 100) < 1e-9
        assert abs(row["alpha_without"] - results.cronbach_alpha(np.delete(a, j, axis=1).tolist())) < 1e-9
        assert row["n"] == 200
    assert rows[3]["rit"] < 0 and rows[3]["alpha_without"] > alpha                   # the one to look at again
    assert rows[3]["loading"] < 0 < rows[0]["loading"]
    # three items: the loadings are those of the closed one-factor solution, standardized
    three = [row[:3] for row in x]
    s = np.cov(np.asarray(three), rowvar=False)
    first = math.sqrt(s[0, 1] * s[0, 2] / s[1, 2])
    expected = np.array([first, s[0, 1] / first, s[0, 2] / first]) / np.sqrt(np.diag(s))
    got = [row["loading"] for row in scales.item_analysis(three)]
    assert np.allclose(got, expected, atol=1e-9)
    two = scales.item_analysis([row[:2] for row in x])
    assert two[0]["alpha_without"] is None and two[0]["loading"] is None and two[0]["p"] is None
    assert abs(two[0]["rit"] - np.corrcoef(a[:, 0], a[:, 1])[0, 1]) < 1e-12


def _psm_cases(n, seed, persons=None):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        tc = float(rng.integers(5, 15))
        c = tc + float(rng.integers(1, 10))
        e = c + float(rng.integers(1, 10))
        te = e + float(rng.integers(1, 15))
        out.append({"tc": tc, "c": c, "e": e, "te": te, "person": i if persons is None else i // persons})
    return out


def test_psm_points_in_numpy_equal_the_curves_and_intersections():
    for seed in range(5):
        good = _psm_cases(60, seed)
        cv = psm_analysis.curves(good)
        p = cv["prices"]
        expected = {"PMC": psm_analysis.intersection(p, cv["too_cheap"], cv["not_cheap"]),
                    "OPP": psm_analysis.intersection(p, cv["too_cheap"], cv["too_expensive"]),
                    "IPP": psm_analysis.intersection(p, cv["not_expensive"], cv["not_cheap"]),
                    "PME": psm_analysis.intersection(p, cv["not_expensive"], cv["too_expensive"])}
        got = psm_analysis._points_of({k: np.array([c[k] for c in good]) for k in psm_analysis.KEYS})
        assert got == pytest.approx(expected)


def test_psm_bootstrap_over_persons():
    good = _psm_cases(80, 1)
    ci = psm_analysis.point_intervals(good)
    assert ci == psm_analysis.point_intervals(good)                                  # fixed seed
    cv = psm_analysis.curves(good)
    opp = psm_analysis.intersection(cv["prices"], cv["too_cheap"], cv["too_expensive"])
    assert ci["OPP"][0] <= opp <= ci["OPP"][1]
    assert psm_analysis.point_intervals(good[:29]) is None                           # from 30 persons on
    # loop passes: two cases per person; 40 persons are drawn, not 80 cases
    looped = _psm_cases(80, 1, persons=2)
    assert psm_analysis.point_intervals(looped) is not None
    assert psm_analysis.point_intervals(_psm_cases(58, 1, persons=2)) is None        # 29 persons
