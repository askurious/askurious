"""Linear mixed models for diary studies (app/mixed.py, longitudinal and multilevel concept 2026-10-17, E8) against
independently computed reference values: seeded entries of 60 persons (3 to 15 each), the predictor split into within
(CWC) and between (person mean), REML with a random intercept and with a correlated random slope (Bates et al., 2015),
and the Satterthwaite degrees of freedom (Satterthwaite, 1946; Kuznetsova et al., 2017)."""
import numpy as np
import pytest

from app import mixed


def _data():
    rng = np.random.default_rng(31)
    rows = []
    for i in range(60):
        k = int(rng.integers(3, 16))
        mx = rng.normal(0, 1)
        u0, u1 = rng.normal(0, 0.6), rng.normal(0, 0.25)
        for _ in range(k):
            rows.append((i, mx + rng.normal(0, 0.8), u0, u1))
    ids = np.array([r[0] for r in rows])
    x = np.round(np.array([r[1] for r in rows]), 6)
    xw, xb = mixed.decompose(x, ids)
    u0, u1 = np.array([r[2] for r in rows]), np.array([r[3] for r in rows])
    y = 1 + 0.4 * xw + 0.2 * xb + u0 + u1 * xw + rng.normal(0, 0.7, len(x))
    return ids, x, np.round(y, 6)


def _rows(res):
    return [(f["est"], f["se"], f["df"], f["p"]) for f in res["fixed"]]


def test_random_intercept_reference_values():
    ids, x, y = _data()
    xw, xb = mixed.decompose(x, ids)
    res = mixed.fit(y, np.column_stack([np.ones(len(x)), xw, xb]), ids, ["(Intercept)", "xw", "xb"])
    expected = [(0.9258114502, 0.09829751760, 56.92992411, 3.263123574e-13),
            (0.4310137361, 0.04011119874, 497.30674445, 2.320846712e-24),
            (0.2094702859, 0.08964757620, 58.99190226, 2.287622429e-02)]
    for got, want in zip(_rows(res), expected):
        assert got[:2] == pytest.approx(want[:2], abs=1e-6)
        assert got[2] == pytest.approx(want[2], rel=1e-3)
        assert got[3] == pytest.approx(want[3], rel=1e-3)
    assert res["G"][0][0] == pytest.approx(0.5032610815, abs=1e-6)
    assert res["sigma"] ** 2 == pytest.approx(0.5493447244, abs=1e-6)
    assert res["reml"] == pytest.approx(1389.505146, abs=1e-5)
    assert mixed.icc(y, ids) == pytest.approx(0.4404684725, abs=1e-6)


def test_random_slope_reference_values():
    ids, x, y = _data()
    xw, xb = mixed.decompose(x, ids)
    res = mixed.fit(y, np.column_stack([np.ones(len(x)), xw, xb]), ids, ["(Intercept)", "xw", "xb"],
                    Z=np.column_stack([np.ones(len(x)), xw]))
    expected = [(0.9272873916, 0.09844046069, 57.09150265, 3.159111680e-13),
            (0.4142354427, 0.05311500875, 45.48258344, 6.315606487e-10),
            (0.2136188569, 0.08766634739, 59.30659532, 1.784059086e-02)]
    for got, want in zip(_rows(res), expected):
        assert got[:2] == pytest.approx(want[:2], abs=2e-6)
        assert got[2] == pytest.approx(want[2], rel=1e-3)
        assert got[3] == pytest.approx(want[3], rel=1e-3)
    G = np.array(res["G"])
    assert (G[0, 0], G[1, 1], G[0, 1]) == pytest.approx((0.51057627825, 0.06496850019, -0.06894233804), abs=2e-6)
    assert res["sigma"] ** 2 == pytest.approx(0.50726786268, abs=2e-6)
    assert res["reml"] == pytest.approx(1377.188556, abs=1e-5)


def test_t_distribution_and_errors():
    assert mixed._t_p(2.0, 10) == pytest.approx(0.07338803, abs=1e-7)
    assert mixed._t_p(1.959964, 1e7) == pytest.approx(0.05, abs=1e-5)
    with pytest.raises(mixed.MixedError):
        mixed.fit([1.0, 2.0], np.ones((2, 1)), [1, 1], ["(Intercept)"])


@pytest.mark.parametrize("t,df,expected", [
    (2, 0.25, 0.60512358443677217),
    (10, 0.5, 0.20267735277133117),
    (2, 2.5, 0.15739149575796593),
    (3, 7.25, 0.019134132993169073),
    (7.798176, 45.48258344, 6.3298248038362156e-10),
    (15, 100.5, 2.1441016290426383e-27),
    (30, 1000.25, 1.5054028701474749e-141),
    (1.959964, 1e7, 0.050000025922397141),
])
def test_t_probabilities_reference_values(t, df, expected):
    """Independently computed two-sided t probabilities 2 P(T > |t|); fractional Satterthwaite df and small tails.

    Absolute tolerance is zero: a probability rounded to zero must not pass for a small positive tail.
    Large df involve cancellation in the log-gamma difference, hence the relative tolerance of 1e-8.
    """
    for signed_t in (t, -t):
        assert mixed._t_p(signed_t, df) == pytest.approx(expected, rel=1e-8, abs=0)


@pytest.mark.parametrize("df", [0.25, 1, 2.5, 50, 1e7])
def test_zero_t_probability_is_one(df):
    assert mixed._t_p(0, df) == 1.0
