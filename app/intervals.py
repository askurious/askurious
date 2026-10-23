"""95 % confidence intervals of the results page (concept statistik-erweiterungen-konzept.md, section 2; K2: fixed 95 %).

  share of an answer      Wilson score interval (Wilson, 1927); multiple choice per option on its own base
  mean                    t interval M +- t(0.975; n - 1) SD / sqrt(n) (Student, 1908)
  Cronbach's alpha        Feldt interval over the F distribution (Feldt, Woodruff & Salih, 1987)
  Pearson r               Fisher z; the Spearman-Brown coefficient of two statements through the interval of their r
  McDonald's omega        percentile bootstrap, OMEGA_DRAWS draws with a fixed seed (Kelley & Pornprasertmanit, 2016);
                          a draw without an omega counts as below every value (open_percentile); in a loop the
                          draws take persons with all their passes (cluster bootstrap)
  pooled reliability      Hartung-Knapp-Sidik-Jonkman with t(k - 1) (app/construct_pool.py)
  PSM price points        percentile bootstrap over persons (app/psm_analysis.py, Efron & Tibshirani, 1993)

No interval below MIN_N cases (bootstrap: MIN_BOOT); the page shows "–" with a note then. None for the median of
ordinal answers (K3). The quantiles of t and F come from the regularized incomplete beta function (scales._betainc):
scipy is not there."""
import math

MIN_N = 10  # an interval from this many cases on
MIN_BOOT = 30  # a bootstrap interval from this many cases on
OMEGA_DRAWS = 200  # K4: about 0.01 s per draw, kept in the results store
PSM_DRAWS = 1000  # the curves on the price grid are cheap
SEED = 20261003  # fixed: the same data give the same interval
Z = 1.959963984540054  # standard normal quantile 0.975

WILSON = ("Wilson, E. B. (1927). Probable inference, the law of succession, and statistical inference. Journal of the "
          "American Statistical Association, 22(158), 209–212. https://doi.org/10.1080/01621459.1927.10502953")
STUDENT = "Student. (1908). The probable error of a mean. Biometrika, 6(1), 1–25. https://doi.org/10.1093/biomet/6.1.1"
FELDT = ("Feldt, L. S., Woodruff, D. J., & Salih, F. A. (1987). Statistical inference for coefficient alpha. Applied "
         "Psychological Measurement, 11(1), 93–103. https://doi.org/10.1177/014662168701100107")
KELLEY = ("Kelley, K., & Pornprasertmanit, S. (2016). Confidence intervals for population reliability coefficients: "
          "Evaluation of methods, recommendations, and software for composite measures. Psychological Methods, 21(1), "
          "69–92. https://doi.org/10.1037/a0040086")
EFRON = ("Efron, B., & Tibshirani, R. J. (1993). An introduction to the bootstrap. Chapman & Hall. "
         "https://doi.org/10.1201/9780429246593")


def wilson(count, n):
    """(low, high) of a share count / n, None below MIN_N."""
    if not n or n < MIN_N:
        return None
    p = count / n
    z2 = Z * Z
    centre = (p + z2 / (2 * n)) / (1 + z2 / n)
    half = Z / (1 + z2 / n) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    # the ends are exactly 0 and 1 for no and for all answers (not 0.9999999999999999)
    return (0.0 if count == 0 else max(0.0, centre - half)), (1.0 if count == n else min(1.0, centre + half))


def _bisect(cdf, p, lo, hi):
    """x with cdf(x) = p for an increasing cdf, between lo and hi (hi is widened until it is above)."""
    while cdf(hi) < p:
        lo, hi = hi, hi * 2
    for _ in range(200):
        mid = (lo + hi) / 2
        if cdf(mid) < p:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-12 * max(1.0, hi):
            break
    return (lo + hi) / 2


def t_cdf(t, df):
    from app.scales import _betainc

    tail = 0.5 * _betainc(df / 2, 0.5, df / (df + t * t))
    return 1 - tail if t >= 0 else tail


def t_quantile(p, df):
    """Quantile of Student's t for p > 0.5."""
    return _bisect(lambda x: t_cdf(x, df), p, 0.0, 4.0)


def f_cdf(x, d1, d2):
    from app.scales import _betainc

    return 0.0 if x <= 0 else _betainc(d1 / 2, d2 / 2, d1 * x / (d1 * x + d2))


def f_quantile(p, d1, d2):
    return _bisect(lambda x: f_cdf(x, d1, d2), p, 0.0, 2.0)


def mean_ci(mean, sd, n, se=None):
    """t interval of a mean, None below MIN_N or without SD. se: a standard error taken in place of SD / sqrt(n)
    (weighted means: the linearization SE, weights.mean_se); n still gives the degrees of freedom n - 1."""
    if mean is None or sd is None or not n or n < MIN_N:
        return None
    half = t_quantile(0.975, n - 1) * (sd / math.sqrt(n) if se is None else se)
    return mean - half, mean + half


def cluster_mean_ci(groups):
    """t interval of the mean M over all values of `groups` (per person the values of their passes) with the
    cluster-robust standard error, SE^2 = G / (G - 1) * sum_g (sum_{i in g} (x_i - M))^2 / n^2, and t(G - 1);
    G persons, n values. Centred on M itself (with equal passes the t interval over the person means); None below
    MIN_N persons (audit 2026-10-05, K-2/I-2)."""
    groups = [g for g in groups if g]
    n, size = sum(len(g) for g in groups), len(groups)
    if size < MIN_N:
        return None
    mean = sum(sum(g) for g in groups) / n
    se = math.sqrt(size / (size - 1) * sum((sum(g) - len(g) * mean) ** 2 for g in groups)) / n
    half = t_quantile(0.975, size - 1) * se
    return mean - half, mean + half


def feldt(alpha, n, k):
    """Feldt interval of Cronbach's alpha: (1 - alpha) / (1 - alpha_hat) follows F(n - 1, (n - 1)(k - 1))."""
    if alpha is None or k < 2 or not n or n < MIN_N:
        return None
    d1, d2 = n - 1, (n - 1) * (k - 1)
    return 1 - (1 - alpha) * f_quantile(0.975, d1, d2), 1 - (1 - alpha) * f_quantile(0.025, d1, d2)


def fisher_r(r, n):
    """Fisher z interval of a Pearson correlation."""
    if r is None or not n or n < MIN_N or abs(r) >= 1:
        return None
    z, se = math.atanh(r), 1 / math.sqrt(n - 3)
    return math.tanh(z - Z * se), math.tanh(z + Z * se)


def spearman_brown(matrix):
    """Interval of the Spearman-Brown coefficient of two statements: 2r / (1 + r) at the ends of the interval of r.
    None for r <= 0: the coefficient is no reliability then (it falls below -1 from r < -1/3 on and runs to minus
    infinity), the page shows "–" next to "nicht interpretierbar"."""
    from app.scales import pearson

    if not matrix or len(matrix[0]) != 2:
        return None
    r, n, _ = pearson([row[0] for row in matrix], [row[1] for row in matrix])
    if r is None or r <= 0:
        return None
    ci = fisher_r(r, n)
    return None if ci is None else tuple(2 * x / (1 + x) for x in ci)


def percentile(values, draws):
    """(2.5 %, 97.5 %) percentiles of bootstrap estimates; None when fewer than half of the draws gave one."""
    import numpy as np

    values = [v for v in values if v is not None]
    if len(values) < draws / 2:
        return None
    lo, hi = np.percentile(np.asarray(values, dtype=float), [2.5, 97.5])
    return float(lo), float(hi)


def open_percentile(values):
    """(2.5 %, 97.5 %) percentiles of bootstrap estimates where a draw without an estimate (None) counts as below
    every value: such a draw of omega mostly has a statement that covaries negatively, a very low reliability.
    Leaving these draws out gave an interval on the condition "estimable", shifted upwards (audit 2026-10-05, K-1:
    coverage 78 % instead of 95 % with three statements). The lower end is None ("nach unten offen") when the
    2.5 % point falls among them; None as a whole when the 97.5 % point does."""
    import numpy as np

    if not values:
        return None
    x = np.asarray([-np.inf if v is None else v for v in values], dtype=float)
    with np.errstate(invalid="ignore"):
        lo, hi = np.percentile(x, [2.5, 97.5])
    if not math.isfinite(hi):
        return None
    return (float(lo) if math.isfinite(lo) else None), float(hi)


def omega_boot_runs(matrix, persons=None):
    """Whether omega_boot draws at all (else it gives (None, None) at once): MIN_BOOT complete cases (persons), three
    statements, two cases more than statements. The results page names an interval still being computed only then."""
    if not matrix or len(matrix) < MIN_BOOT or len(matrix[0]) < 3 or len(matrix) < len(matrix[0]) + 2:
        return False
    return persons is None or len(set(persons)) >= MIN_BOOT


def omega_boot(matrix, draws=OMEGA_DRAWS, persons=None):
    """Percentile bootstrap of McDonald's omega over the rows (cases) of the complete answers: (interval, share of
    the draws without an omega), (None, None) without one. persons: per row the person it belongs to (a scale in a
    loop, one row per pass): each draw takes persons with all their rows, as the passes of one person are not
    independent (audit 2026-10-05, I-2); from MIN_BOOT persons on."""
    import numpy as np

    from app import scales

    if not omega_boot_runs(matrix, persons):
        return None, None
    x = np.asarray(matrix, dtype=float)
    n = len(x)
    if persons is not None:
        _, rows_of = np.unique(np.asarray(persons), return_inverse=True)
        groups = int(rows_of.max()) + 1
    start = None
    if x.shape[1] > 3:  # three statements have a closed solution; from four on each draw starts from the whole sample
        fit = scales._one_factor(np.cov(x, rowvar=False))
        start = fit[1] if fit is not None else None
    rng = np.random.default_rng(SEED)

    def counts():
        # a draw of n rows with replacement is the covariance with each row weighted by how often it was drawn: the
        # same matrix as from the copied rows, without copying them (audit 2026-10-05); with persons, a row counts
        # as often as its person was drawn
        if persons is None:
            return np.bincount(rng.integers(0, n, n), minlength=n)
        return np.bincount(rng.integers(0, groups, groups), minlength=groups)[rows_of]

    found = []
    for _ in range(draws):
        weights = counts()
        found.append(scales.omega_of_cov(np.cov(x, rowvar=False, fweights=weights), start)
                     if np.count_nonzero(weights) > x.shape[1] + 1 else None)
    return open_percentile(found), sum(1 for v in found if v is None) / draws


def omega(matrix, draws=OMEGA_DRAWS, persons=None):
    """The bootstrap interval of omega_boot alone: (low or None, high), or None."""
    return omega_boot(matrix, draws, persons)[0]
