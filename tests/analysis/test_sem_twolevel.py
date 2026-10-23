"""Two-level structural equation models (app/sem_twolevel.py, longitudinal and multilevel concept 2026-10-17, E9)
against independently computed reference values (two-level ML, observed information): seeded entries of 90
persons (3 to 13 each), four statements of one factor at both levels with the loadings equal over the levels."""
import numpy as np
import pytest

from app import sem, sem_table, sem_twolevel

r = sem_table.row
COLS = ["y1", "y2", "y3", "y4"]
WITHIN = ([r("=~", "FW", c, 1 if k == 0 else None, None if k == 0 else f"l{k}") for k, c in enumerate(COLS)]
          + [r("~~", c, c) for c in COLS] + [r("~~", "FW", "FW")])
BETWEEN = ([r("=~", "FB", c, 1 if k == 0 else None, None if k == 0 else f"l{k}") for k, c in enumerate(COLS)]
           + [r("~~", c, c) for c in COLS] + [r("~1", c) for c in COLS] + [r("~~", "FB", "FB")])


def _data():
    rng = np.random.default_rng(78)
    rows = []
    for j in range(90):
        k = int(rng.integers(3, 14))
        fb = rng.normal(0, 0.6)
        ub = rng.normal(0, 0.3, 4)
        for _ in range(k):
            fw = rng.normal(0, 1)
            ys = [3 + 0.2 * i + lam * (fb + fw) + ub[i] + rng.normal(0, 0.6 if i < 2 else 0.7)
                  for i, lam in enumerate((1.0, 0.8, 0.7, 0.9))]
            rows.append([j] + ys)
    a = np.round(np.array(rows, dtype=float), 6)
    return a[:, 0].astype(int), a[:, 1:]


def _est(fit, level, op, lhs, rhs=None):
    found = sem_twolevel.find(fit, level, op, lhs, rhs)
    return found["est"], found["se"]


def test_two_level_cfa_reference_values():
    ids, y = _data()
    fit = sem_twolevel.fit(y, ids, COLS, WITHIN, BETWEEN)
    assert fit["converged"] and fit["admissible"] and fit["df"] == 7 and fit["persons"] == 90
    # the reference chi-square 9.354640 rests on a saturated model fitted by EM, whose log-likelihood is 1.1e-4 below
    # the maximum found here (-3669.169857 against -3669.169743)
    assert fit["chi2"] == pytest.approx(9.354640, abs=6e-4)
    assert (fit["cfi"], fit["rmsea"]) == pytest.approx((0.998385, 0.021937), abs=1e-5)
    assert fit["srmr_within"] == pytest.approx(0.007265, abs=1e-5)
    assert fit["srmr_between"] == pytest.approx(0.054837, abs=2e-4)
    assert _est(fit, "within", "=~", "FW", "y2") == pytest.approx((0.834162, 0.033300), abs=2e-6)
    assert _est(fit, "between", "=~", "FB", "y2") == pytest.approx((0.834162, 0.033300), abs=2e-6)
    assert _est(fit, "within", "~~", "FW", "FW") == pytest.approx((0.991902, 0.075292), abs=2e-6)
    assert _est(fit, "between", "~~", "FB", "FB") == pytest.approx((0.352352, 0.078729), abs=2e-6)
    assert _est(fit, "between", "~~", "y3", "y3") == pytest.approx((0.157551, 0.039633), abs=2e-6)
    assert _est(fit, "between", "~1", "y4") == pytest.approx((3.616942, 0.084110), abs=2e-6)
    # reliability within and between persons (Geldhof et al., 2014)
    w, b = sem_twolevel.omega(fit, "within", "FW", COLS), sem_twolevel.omega(fit, "between", "FB", COLS)
    assert 0.8 < w < 0.95 and 0.8 < b < 1.0


def test_too_few():
    with pytest.raises(sem.SemError):
        sem_twolevel.fit(np.ones((5, 4)), [1, 1, 2, 2, 3], COLS, WITHIN, BETWEEN)
