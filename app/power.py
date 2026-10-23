"""Power of the tests the study plan names (app/study_plan.py, concept poweranalyse-konzept.md): the same mathematics
as the sample size calculator in the browser (app/static/samplesize.js), here in Python for what the server shows
and writes: the number a hypothesis needs (a priori), the smallest effect the data of the results could detect
(sensitivity), the line in the data collection, the pre-registration and the methods draft.

  t-test            exact power over the noncentral t distribution (after Lenth, 1989)
  ANOVA             noncentral F, Cohen's f (lambda = f² N)
  chi-square        noncentral chi-square, Cohen's w (lambda = w² N)
  correlation       exact distribution of r under a bivariate normal population (Fisher, 1915; Faul et al., 2009)
  Mann-Whitney U,   each person counts as 3/π ≈ 0.955 persons of the t-test or the ANOVA: the asymptotic relative
  Kruskal-Wallis    efficiency (A.R.E.) under normal data (Lehmann, 2006)
  Spearman          n of Pearson's r with the factor 1 + r²/2 on n - 3 (Bonett & Wright, 2000)

Welch's t-test and ANOVA are planned as the Student and the Fisher test with equal variances: there they have the
same power. All tests two-sided, as the results per hypothesis (app/hypotheses.py). Plain Python and numpy, scipy is
not there; the quantiles come from app/intervals.py, the incomplete beta and gamma functions from app/scales.py and
app/group_tests.py. tests/analysis/test_power.py checks the numbers against samplesize.js."""
import math
from functools import lru_cache

import numpy as np

from app.group_tests import chi2_sf, ncx2_sf, poisson_weights
from app.intervals import f_quantile, t_quantile
from app.scales import _betainc

ARE = 3 / math.pi  # rank tests against t-test and ANOVA under normal data (Lehmann, 2006)
MAX_N = 10_000_000

LENTH = ("Lenth, R. V. (1989). Algorithm AS 243: Cumulative distribution function of the non-central t distribution. "
         "Journal of the Royal Statistical Society: Series C (Applied Statistics), 38(1), 185–189. "
         "https://doi.org/10.2307/2347693")
LEHMANN = "Lehmann, E. L. (2006). Nonparametrics: Statistical methods based on ranks (Rev. ed.). Springer."
BONETT_WRIGHT = ("Bonett, D. G., & Wright, T. A. (2000). Sample size requirements for estimating Pearson, Kendall and "
                 "Spearman correlations. Psychometrika, 65(1), 23–28. https://doi.org/10.1007/BF02294183")


# --- distributions ------------------------------------------------------------------------------------------------

def _nct_cdf(t, df, delta):
    """P(T' <= t) of the noncentral t distribution for t >= 0 (Lenth, 1989; summed in full as samplesize.js)."""
    x = t * t / (t * t + df)
    if not delta:  # central t: the Poisson weights below are 1, 0, 0, … only in the limit (PA-1)
        return 1 - 0.5 * _betainc(df / 2, 0.5, df / (df + t * t))
    lam = delta * delta / 2
    total = 0.0
    last = math.ceil(lam + 12 * math.sqrt(lam) + 60)
    log_lam = math.log(lam)
    log_delta = math.log(abs(delta))
    for j in range(last + 1):
        lp = -lam + (j * log_lam if j else 0.0) - math.lgamma(j + 1)
        total += math.exp(lp) * _betainc(j + 0.5, df / 2, x)
        if delta:
            lq = -lam + (j * log_lam if j else 0.0) - math.lgamma(j + 1.5) + log_delta - 0.5 * math.log(2)
            total += (1 if delta > 0 else -1) * math.exp(lq) * _betainc(j + 1, df / 2, x)
    norm = 0.5 * math.erfc(delta / math.sqrt(2))  # P(Z <= -delta)
    return min(1.0, max(0.0, norm + total / 2))


def t_power(df, delta, alpha=0.05, onesided=False):
    """Power of a t-test with df degrees of freedom and noncentrality delta."""
    if df <= 0:
        return 0.0
    c = t_quantile(1 - (alpha if onesided else alpha / 2), df)
    up = 1 - _nct_cdf(c, df, delta)
    return up if onesided else up + (1 - _nct_cdf(c, df, -delta))


def _ncf_sf(x, d1, d2, lam):
    """P(F' > x) of the noncentral F distribution (Poisson mixture of central F)."""
    y = d2 / (d2 + d1 * x)
    half = lam / 2
    if half <= 0:  # central F (PA-1)
        return min(1.0, _betainc(d2 / 2, d1 / 2, y))
    return min(1.0, sum(w * _betainc(d2 / 2, d1 / 2 + j, y) for j, w in poisson_weights(half)))


def f_power(d1, d2, lam, alpha=0.05):
    """Power of an F test with d1, d2 degrees of freedom and noncentrality lam."""
    if d1 <= 0 or d2 <= 0:
        return 0.0
    return _ncf_sf(f_quantile(1 - alpha, d1, d2), d1, d2, lam)


@lru_cache(maxsize=256)
def _chi2_quantile(alpha, df):
    lo, hi = 0.0, df + 40 * math.sqrt(2 * df) + 100
    for _ in range(200):
        mid = (lo + hi) / 2
        if chi2_sf(mid, df) > alpha:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def chi2_power(N, df, w, alpha=0.05):
    """Power of the chi-square test with df degrees of freedom, N persons, effect size w (Cohen, 1988)."""
    if df <= 0 or N <= df:
        return 0.0
    return ncx2_sf(_chi2_quantile(alpha, df), df, w * w * N)  # central (w = 0): the power is alpha (PA-1)


def _r_probability(a, b, n, rho, steps=4000):
    """P(a < r < b) of the sample correlation of n pairs; with r = sin θ the integrand is smooth (as samplesize.js)."""
    t0, t1 = math.asin(a), math.asin(b)
    th = np.linspace(t0, t1, steps + 1)
    r, cs = np.sin(th), np.cos(th)
    z = (1 + rho * r) / 2
    c = n - 0.5
    term, total = np.ones_like(z), np.ones_like(z)
    for k in range(200000):
        term = term * ((k + 0.5) * (k + 0.5) / ((c + k) * (k + 1))) * z
        total += term
        if np.all(term < total * 1e-16):
            break
    with np.errstate(divide="ignore"):
        log_density = (math.log(n - 2) + math.lgamma(n - 1) - math.lgamma(n - 0.5) - 0.5 * math.log(2 * math.pi)
                       + (n - 1) / 2 * math.log(1 - rho * rho) - (n - 1.5) * np.log(1 - rho * r) + np.log(total))
        val = np.where(cs > 0, np.exp(log_density + (n - 3) * np.log(np.where(cs > 0, cs, 1.0))), 0.0)
    weights = np.ones(steps + 1)
    weights[1:-1:2], weights[2:-1:2] = 4, 2
    return float(np.sum(val * weights) * (t1 - t0) / steps / 3)


def corr_power(n, rho, alpha=0.05, onesided=False):
    """Exact power of the test of H0: rho = 0 (t-test of r with n - 2 degrees of freedom)."""
    if n < 4:
        return 0.0
    c = t_quantile(1 - (alpha if onesided else alpha / 2), n - 2)
    rc = c / math.sqrt(c * c + n - 2)
    rho = min(abs(rho), 0.999)
    up = _r_probability(rc, 1, n, rho)
    return up if onesided else up + _r_probability(-1, -rc, n, rho)


def smallest(power, target, lo):
    """The smallest whole n >= lo with power(n) >= target (power grows with n); None when beyond MAX_N."""
    if power(lo) >= target:
        return lo
    hi = lo
    while power(hi) < target:
        lo, hi = hi, hi * 2
        if hi > MAX_N:
            return None
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if power(mid) >= target:
            hi = mid
        else:
            lo = mid
    return hi


def _invert(f, target, lo, hi):
    """x with f(x) = target for an increasing f on [lo, hi] (bisection)."""
    for _ in range(80):
        mid = (lo + hi) / 2
        if f(mid) < target:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-6:
            break
    return (lo + hi) / 2


# --- what the study plan asks -------------------------------------------------------------------------------------

# the effect size each analysis of the plan is planned with, with Cohen's (1988) small, medium, large
MEASURES = {"r": "r", "rho": "r", "t": "d", "u": "d", "anova": "f", "kw": "f", "chi2": "w",
            "sem": "β", "panel": "β"}  # a path of the structural equation model: its standardised beta (app/sem_model.planned_n)
# β with the steps of r, as a standardised path is read like a correlation (Festlegung von Askurious)
COHEN = {"r": (0.1, 0.3, 0.5), "d": (0.2, 0.5, 0.8), "f": (0.1, 0.25, 0.4), "w": (0.1, 0.3, 0.5), "β": (0.1, 0.3, 0.5)}
LIMITS = {"r": (0.01, 0.95), "d": (0.01, 5.0), "f": (0.01, 3.0), "w": (0.01, 3.0), "β": (0.01, 0.95)}


def measure(analysis):
    """"r", "d", "f" or "w" for an analysis of the plan, None where no test is planned (describe, open)."""
    return MEASURES.get(analysis)


def valid_effect(analysis, effect):
    m = measure(analysis)
    if m is None or effect is None:
        return False
    lo, hi = LIMITS[m]
    return lo <= effect <= hi


@lru_cache(maxsize=1024)
def needed(analysis, effect, power=0.8, alpha=0.05, groups=2, df=1):
    """Completed participations (all groups together) for a hypothesis tested with `analysis` (a key of
    study_plan.ANALYSES) to reach `power` at a true effect of `effect` (r, d, f or w; see MEASURES); groups: groups
    of an ANOVA, Kruskal-Wallis (equal sizes); df: of a chi-square test. None when no number comes about."""
    if not valid_effect(analysis, effect) or not 0 < power < 1:
        return None
    if analysis in ("r", "rho"):
        n = smallest(lambda n: corr_power(n, effect, alpha), power, 4)
        if n is not None and analysis == "rho":
            n = math.ceil((n - 3) * (1 + effect * effect / 2) - 1e-9) + 3
        return n
    # a rank test counts each person as ARE persons of the t-test or the ANOVA (asymptotic relative efficiency,
    # Lehmann, 2006)
    e = ARE if analysis in ("u", "kw") else 1.0
    if analysis in ("t", "u"):
        per = smallest(lambda n: t_power(2 * n * e - 2, effect * math.sqrt(n * e / 2), alpha), power, 2)
        return None if per is None else 2 * per
    if analysis in ("anova", "kw"):
        g = max(2, int(groups or 2))
        per = smallest(lambda n: f_power(g - 1, n * g * e - g, effect * effect * n * g * e, alpha), power, 2)
        return None if per is None else g * per
    if analysis == "chi2":
        k = max(1, int(df or 1))
        return smallest(lambda n: chi2_power(n, k, effect, alpha), power, k + 2)
    return None


def detectable(analysis, sizes, power=0.8, alpha=0.05, df=1):
    """The smallest effect (r, d, f or w) the test of `analysis` finds with `power` in data of the given group sizes
    (one number for a correlation or a chi-square test: the cases; a list of group sizes for t, U, ANOVA and
    Kruskal-Wallis). None when the data are too few for the power. Sensitivity analysis (Lakens, 2022), not the
    "observed power" of the found effect (Hoenig & Heisey, 2001)."""
    m = measure(analysis)
    if m is None or analysis == "sem" or not 0 < power < 1:  # the model's paths: app/sem_model.planned_n
        return None
    lo, hi = LIMITS[m]
    if analysis in ("r", "rho"):
        n = int(sizes if not isinstance(sizes, (list, tuple)) else sum(sizes))
        if n < 4:
            return None
        f = (lambda r: corr_power(n, r, alpha))
    elif analysis in ("t", "u"):
        a, b = (list(sizes) + [0, 0])[:2]
        if analysis == "u":
            a, b = a * ARE, b * ARE
        if a < 2 or b < 2:
            return None
        f = (lambda d: t_power(a + b - 2, d * math.sqrt(a * b / (a + b)), alpha))
    elif analysis in ("anova", "kw"):
        ns = [s * (ARE if analysis == "kw" else 1) for s in sizes]
        g, N = len(ns), sum(ns)
        if g < 2 or N <= g:
            return None
        f = (lambda e: f_power(g - 1, N - g, e * e * N, alpha))
    else:  # chi2
        n = int(sizes if not isinstance(sizes, (list, tuple)) else sum(sizes))
        k = max(1, int(df or 1))
        if n <= k + 1:
            return None
        f = (lambda w: chi2_power(n, k, w, alpha))
    if f(hi) < power:
        return None
    effect = _invert(f, power, lo / 10, hi)
    if analysis == "rho":
        effect = _rho_detectable(effect, int(sizes if not isinstance(sizes, (list, tuple)) else sum(sizes)), power, alpha)
    return effect


def _rho_detectable(r_pearson, n, power, alpha):
    """For Spearman: the r whose Pearson n, inflated by 1 + r²/2 (Bonett & Wright, 2000), is the n at hand."""
    def cases(r):
        base = smallest(lambda m: corr_power(m, r, alpha), power, 4)
        return MAX_N if base is None else (base - 3) * (1 + r * r / 2) + 3
    lo, hi = r_pearson, 0.95
    if cases(hi) > n:
        return None
    for _ in range(40):
        mid = (lo + hi) / 2
        if cases(mid) > n:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-4:
            break
    return hi


def power_at(analysis, effect, sizes, alpha=0.05, df=1):
    """The power the test had for a given effect in data of the given group sizes (for "geplant war …")."""
    if not valid_effect(analysis, effect) or analysis == "sem":
        return None
    if analysis in ("r", "rho"):
        n = int(sizes if not isinstance(sizes, (list, tuple)) else sum(sizes))
        if analysis == "rho":
            n = int(3 + (n - 3) / (1 + effect * effect / 2))
        return corr_power(n, effect, alpha) if n >= 4 else None
    if analysis in ("t", "u"):
        a, b = (list(sizes) + [0, 0])[:2]
        if analysis == "u":
            a, b = a * ARE, b * ARE
        return t_power(a + b - 2, effect * math.sqrt(a * b / (a + b)), alpha) if a >= 2 and b >= 2 else None
    if analysis in ("anova", "kw"):
        ns = [s * (ARE if analysis == "kw" else 1) for s in sizes]
        g, N = len(ns), sum(ns)
        return f_power(g - 1, N - g, effect * effect * N, alpha) if g >= 2 and N > g else None
    n = int(sizes if not isinstance(sizes, (list, tuple)) else sum(sizes))
    return chi2_power(n, max(1, int(df or 1)), effect, alpha)
