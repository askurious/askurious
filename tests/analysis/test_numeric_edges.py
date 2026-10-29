"""Results that must not change with the scale or a shift of the values (audit storage and calculations
2026-10-28, M4-R, M5)."""
import math

from app.group_tests import welch_anova, welch_t
from app.scales import pearson

R = math.sqrt(3 / 7)  # x = [1, 2, 3], y = [2, 1, 4]: sxy = 2, sxx = 2, syy = 14/3


def test_pearson_is_unchanged_by_a_large_shift():
    """M5: 9e14 + small differences (exact floats below MAX_NUMBER) give the r of the differences."""
    for shift in (0.0, 9e14, -9e14, 1e300):
        r, n, p = pearson([shift + 1, shift + 2, shift + 3], [shift + 2, shift + 1, shift + 4])
        if shift == 1e300:  # the differences are below the float spacing there: no variance left
            assert r is None
            continue
        assert n == 3 and abs(r - R) < 1e-12 and abs(p - 0.5456289483429904) < 1e-9


def test_pearson_still_works_at_the_ends_of_the_float_range():
    assert abs(pearson([1e-320, 2e-320, 3e-320], [2, 1, 4])[0] - R) < 1e-12
    r = pearson([1e308, -1e308, 5e307], [1, 2, 4])[0]  # the shift overflows: scaled only
    assert r is not None and -1 <= r <= 1


def test_welch_with_subnormal_values():
    """M4-R: values around 1e-310 (subnormal) give the t, df and p of the same values times 1e310."""
    base = welch_t([1, 2, 3], [2, 4, 6])
    for scale in (1e-310, 1e-320, 1e300):
        res = welch_t([v * scale for v in (1, 2, 3)], [v * scale for v in (2, 4, 6)])
        assert abs(res["t"] - base["t"]) < 1e-6 and abs(res["df"] - base["df"]) < 1e-6
    a = welch_anova([[1, 2, 3], [2, 4, 6], [3, 5, 9]])
    b = welch_anova([[v * 1e-310 for v in g] for g in ([1, 2, 3], [2, 4, 6], [3, 5, 9])])
    assert abs(a["f"] - b["f"]) < 1e-6 and abs(a["df2"] - b["df2"]) < 1e-6
