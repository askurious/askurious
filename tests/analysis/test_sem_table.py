"""Models written as a parameter table (app/sem_table.py, longitudinal and multilevel concept 2026-10-17, package 1)
against independently computed reference values (FIML with robust errors, MLR): seeded panel data of three waves
with gaps (6 % at random, 15 % gone after wave 2), values rounded to six places. Checked: scalar invariance over time
with residual covariances of the same statement, a linear growth curve and the random-intercept cross-lagged panel
model, with and without equal paths, and the Wald test of equal paths."""
import math
import warnings

import numpy as np
import pytest

from app import sem, sem_table as T

r = T.row


def _items():
    rng = np.random.default_rng(1717)
    n = 500
    rix = rng.normal(size=n)
    riy = 0.4 * rix + math.sqrt(0.84) * rng.normal(size=n)
    wx = [rng.normal(size=n)]
    wy = [0.3 * wx[0] + math.sqrt(0.91) * rng.normal(size=n)]
    for _ in (1, 2):
        wx.append(0.5 * wx[-1] + 0.2 * wy[-1] + 0.8 * rng.normal(size=n))
        wy.append(0.4 * wy[-1] + 0.25 * wx[-2] + 0.8 * rng.normal(size=n))
    cols, out = [], []
    for t in range(3):
        fx = 3.0 + 0.2 * t + 0.7 * rix + 0.6 * wx[t]
        fy = 2.5 + 0.7 * riy + 0.6 * wy[t]
        for name, f in (("x", fx), ("y", fy)):
            for k, lam in enumerate((1.0, 0.9, 0.8)):
                cols.append(f"{name}{k + 1}_t{t + 1}")
                out.append(0.3 * k + lam * f + 0.6 * rng.normal(size=n))
    x = np.column_stack(out)
    x *= np.sqrt(8 / rng.chisquare(10, size=n))[:, None] ** 0.5
    x[rng.random(x.shape) < 0.06] = np.nan
    x[rng.random(n) < 0.15, 12:] = np.nan
    return cols, np.round(x, 6)


def _scores():
    cols, x = _items()
    out, names = [], []
    for t in (1, 2, 3):
        for v in ("x", "y"):
            idx = [cols.index(f"{v}{k}_t{t}") for k in (1, 2, 3)]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                out.append(np.round(np.nanmean(x[:, idx], axis=1), 6))
            names.append(f"{v}_t{t}")
    return names, np.column_stack(out)


def _cfa():
    items = ["x1", "x2", "x3"]
    tab = []
    for t in (1, 2, 3):
        for k, it in enumerate(items):
            tab.append(r("=~", f"X{t}", f"{it}_t{t}", 1 if k == 0 else None, None if k == 0 else f"l{k}"))
    for t in (1, 2, 3):
        for it in items:
            tab += [r("~~", f"{it}_t{t}", f"{it}_t{t}"), r("~1", f"{it}_t{t}", None, None, f"i_{it}")]
    for it in items:
        tab += [r("~~", f"{it}_t{a}", f"{it}_t{b}") for a, b in ((1, 2), (1, 3), (2, 3))]
    for t in (1, 2, 3):
        tab += [r("~~", f"X{t}", f"X{t}"), r("~1", f"X{t}", None, 0 if t == 1 else None)]
    tab += [r("~~", f"X{a}", f"X{b}") for a, b in ((1, 2), (1, 3), (2, 3))]
    return [f"{it}_t{t}" for t in (1, 2, 3) for it in items], tab


def _growth():
    cols = ["x_t1", "x_t2", "x_t3"]
    tab = [r("=~", "I", c, 1) for c in cols] + [r("=~", "S", c, t) for t, c in enumerate(cols)]
    for c in cols:
        tab += [r("~1", c, None, 0), r("~~", c, c)]
    return cols, tab + [r("~1", "I"), r("~1", "S"), r("~~", "I", "I"), r("~~", "S", "S"), r("~~", "I", "S")]


def _riclpm(equal=False):
    cols = [f"{v}_t{t}" for t in (1, 2, 3) for v in ("x", "y")]
    tab = [r("=~", f"RI{v}", f"{v}_t{t}", 1) for v in "xy" for t in (1, 2, 3)]
    tab += [r("=~", f"w{v}{t}", f"{v}_t{t}", 1) for v in "xy" for t in (1, 2, 3)]
    for c in cols:
        tab += [r("~1", c), r("~~", c, c, 0)]
    for t in (2, 3):
        tab += [r("~", f"wx{t}", f"wx{t - 1}", None, "ax" if equal else None),
                r("~", f"wx{t}", f"wy{t - 1}", None, "byx" if equal else None),
                r("~", f"wy{t}", f"wy{t - 1}", None, "ay" if equal else None),
                r("~", f"wy{t}", f"wx{t - 1}", None, "bxy" if equal else None)]
    tab.append(r("~~", "wx1", "wy1"))
    for t in (1, 2, 3):
        tab += [r("~~", f"wx{t}", f"wx{t}"), r("~~", f"wy{t}", f"wy{t}")]
    tab += [r("~~", f"wx{t}", f"wy{t}") for t in (2, 3)]
    return cols, tab + [r("~~", "RIx", "RIx"), r("~~", "RIy", "RIy"), r("~~", "RIx", "RIy")]


SAME = [["x_t1", "x_t2", "x_t3"], ["y_t1", "y_t2", "y_t3"]]


def _fit(make, data, same=None, **kw):
    cols, tab = make(**kw)
    names, x = data()
    return T.fit(x[:, [names.index(c) for c in cols]], cols, tab, same=same)


def _check(fit, chi2, scaled, cfi, tli, rmsea, srmr):
    assert fit["converged"] and fit["admissible"]
    assert fit["chi2"] == pytest.approx(chi2, abs=1e-5)
    assert fit["scaled"]["chi2"] == pytest.approx(scaled, abs=1e-4)
    assert (fit["cfi"], fit["tli"], fit["rmsea"], fit["srmr"]) == pytest.approx((cfi, tli, rmsea, srmr), abs=1e-6)


def _row(fit, *key):
    found = T.find(fit, *key)
    return found["est"], found["se"], found["std"]


def test_scalar_invariance_over_time_reference_values():
    same = [[f"x{k}_t{t}" for t in (1, 2, 3)] for k in (1, 2, 3)]
    fit = _fit(_cfa, _items, same)
    assert fit["df"] == 23
    _check(fit, 13.829071, 12.520538, 1.0, 1.005710, 0.0, 0.018463)
    assert _row(fit, "=~", "X1", "x2_t1") == pytest.approx((0.928930, 0.028154, 0.836702), abs=1e-5)
    assert _row(fit, "=~", "X3", "x2_t3")[0] == pytest.approx(0.928930, abs=1e-5)  # the same parameter
    assert _row(fit, "~~", "x2_t1", "x2_t3") == pytest.approx((-0.031772, 0.025160, -0.097533), abs=1e-5)
    assert _row(fit, "~1", "X2") == pytest.approx((0.177886, 0.034932, 0.182971), abs=1e-5)


def test_growth_curve_reference_values():
    fit = _fit(_growth, _scores, SAME[:1])
    _check(fit, 2.121910, 2.146555, 0.998382, 0.995147, 0.047369, 0.011819)
    assert _row(fit, "~1", "I") == pytest.approx((2.904589, 0.041636, 3.448097), abs=1e-5)
    assert _row(fit, "~1", "S") == pytest.approx((0.202678, 0.018734, 0.808960), abs=1e-5)
    assert _row(fit, "~~", "I", "S") == pytest.approx((-0.063360, 0.037227, -0.300214), abs=1e-5)


def test_riclpm_and_equal_paths_reference_values():
    fit = _fit(_riclpm, _scores, SAME)
    _check(fit, 0.227852, 0.228129, 1.0, 1.008204, 0.0, 0.003897)
    wald = T.wald_equal(fit, [[("~", "wx2", "wx1"), ("~", "wx3", "wx2")], [("~", "wy2", "wx1"), ("~", "wy3", "wx2")]])
    assert (wald["chi2"], wald["df"], wald["p"]) == pytest.approx((7.546498, 2, 0.022977), abs=1e-5)
    eq = _fit(_riclpm, _scores, SAME, equal=True)
    assert eq["df"] == 5
    _check(eq, 11.782403, 11.620892, 0.995196, 0.985587, 0.052086, 0.023551)
    assert _row(eq, "~", "wy2", "wx1") == pytest.approx((0.114506, 0.089751, 0.098702), abs=1e-5)
    assert _row(eq, "~", "wx2", "wy1") == pytest.approx((0.072321, 0.066529, 0.068286), abs=1e-5)
    assert _row(eq, "~~", "RIx", "RIy") == pytest.approx((0.309199, 0.048904, 0.619844), abs=1e-5)


def test_lavaan_syntax_and_errors():
    cols, tab = _growth()
    text = T.lavaan_syntax(tab)
    assert "I =~ 1*x_t1" in text and "S =~ 2*x_t3" in text and "x_t1 ~ 0*1" in text and "I ~~ S" in text
    with pytest.raises(sem.SemError):
        T.Model(tab + [r("~~", "I", "S")], cols)  # the same cell twice
    with pytest.raises(sem.SemError):
        T.fit(np.ones((10, 3)), cols, tab)  # constant columns
