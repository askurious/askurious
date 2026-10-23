"""Welch's t and Welch's ANOVA do not depend on the scale of the values (audit 2026-10-23, M4): around 1e-100 the
squared variances underflowed and the degrees of freedom became 0/0."""
import pytest

from app import group_tests as gt

X, Y = [0.0, 1.0, 2.5], [2.0, 3.0, 4.0]
GROUPS = [[0, 1, 2], [2, 3, 4], [1, 5, 6]]


@pytest.mark.parametrize("scale", [1e-300, 1e-100, 1e-60, 1e60, 1e100, 1e300])
def test_welch_t_is_scale_invariant(scale):
    ref = gt.welch_t(X, Y)
    r = gt.welch_t([v * scale for v in X], [v * scale for v in Y])
    for key in ("t", "df", "p", "d", "d_raw"):
        assert r[key] == pytest.approx(ref[key], rel=1e-9)
    assert r["d_ci"] == pytest.approx(ref["d_ci"], rel=1e-9)


@pytest.mark.parametrize("scale", [1e-300, 1e-100, 1e100])
def test_welch_anova_is_scale_invariant(scale):
    ref = gt.welch_anova(GROUPS)
    r = gt.welch_anova([[v * scale for v in g] for g in GROUPS])
    for key in ("f", "df2", "p", "omega2"):
        assert r[key] == pytest.approx(ref[key], rel=1e-9)
