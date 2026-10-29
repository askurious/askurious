"""Tests and effect sizes for comparing groups (results page, "Nach Gruppen aufteilen"; concept
statistik-erweiterungen-konzept.md, section 4). Plain Python, no scipy:
  chi2_sf      upper tail of the chi-square distribution: regularized incomplete gamma function Q(df/2, x/2)
  t_sf2        two-sided p of a t value: incomplete beta function (scales._betainc)
  f_sf         upper tail of the F distribution: incomplete beta function
  norm_sf2     two-sided p of a z value: math.erfc
The tests: chi-square test of independence with Cramér's V (Pearson, 1900; Cramér, 1946) and Cochran's rule; where
the rule warns, an exact p beside it: Fisher's exact test for 2 x 2, a Monte Carlo permutation test with a fixed seed
for larger tables (audit 2026-10-05, I-3; Agresti, 2013). Mann-Whitney U (Mann & Whitney, 1947; without ties exact
while both groups are under EXACT_GROUP; with ties exact below EXACT_N cases, the permutation distribution of the
mid-rank sum; else the normal approximation with tie and continuity correction) with the rank-biserial correlation,
Spearman's rank correlation without ties (spearman_p), Kruskal-Wallis H (tie correction) with epsilon squared,
Welch's t with the standardised
difference delta* in the average SD and its 95 % interval (Bonett, 2008), Welch's ANOVA with omega squared; where a group does not vary, Welch's weights are not defined and
the Brown-Forsythe F* takes its place (Brown & Forsythe, 1974). Descriptive figures take optional weights (stage 3
of the concept, weighting): weighted M and SD as on the results page (weights.mean, weights.sd) and the interval with
the linearization standard error (weights.mean_se, t with the df of Kish's effective n); the tests are only defined without weights and return None with them."""
import math
import random

from app import weights as weighting
from app.intervals import Z as Z975
from app.intervals import mean_ci, t_quantile  # noqa: F401 (t_quantile: one quantile function for the page)
from app.scales import _betainc, _fraction


# --- distributions ----------------------------------------------------------------------------------------------

def gammaincc(a, x):
    """Regularized upper incomplete gamma function Q(a, x) = 1 - P(a, x): below x = a + 1 the power series of P,
    above it Legendre's continued fraction of Q (Abramowitz & Stegun, 1964, 6.5.29 and 6.5.31); as upperGamma() in
    static/samplesize.js (rights review 2026-10-09)."""
    if x <= 0:
        return 1.0
    scale = math.exp(-x + a * math.log(x) - math.lgamma(a))
    if x < a + 1:
        add = total = 1.0 / a
        n = 1
        while n < 1000 and abs(add) >= abs(total) * 1e-16:
            add *= x / (a + n)
            total += add
            n += 1
        return max(0.0, 1.0 - scale * total)
    return scale * _fraction(lambda n: (-(n - 1) * (n - 1 - a), x + 2 * n - 1 - a), 1000, 1e-16)


def chi2_sf(x, df):
    """P(X >= x) for a chi-square distribution with df degrees of freedom."""
    return gammaincc(df / 2, x / 2) if df > 0 else None


def poisson_weights(half):
    """(j, exp(-half) half^j / j!) for the j that count in a Poisson mixture with mean half > 0: a window of 12 SD
    plus 60 on each side of the mean, as far as that reaches; the weights outside it add less than 1e-15. Summing
    from j = 0 on cost a term per unit of half for nothing (code review 2026-10-15, A6)."""
    spread = 12 * math.sqrt(half) + 60
    log_half = math.log(half)
    for j in range(max(0, math.floor(half - spread)), math.ceil(half + spread) + 1):
        yield j, math.exp(-half + (j * log_half if j else 0.0) - math.lgamma(j + 1))


def ncx2_sf(x, df, lam):
    """P(X > x) of the noncentral chi-square distribution with df degrees of freedom and noncentrality lam: the
    Poisson mixture of central ones (one function for sem, power and power_pilot, code review 2026-10-15, A6).
    Q(df/2 + j, x/2) only once, then up by Q(a + 1, y) = Q(a, y) + y^a e^-y / Gamma(a + 1) (Abramowitz & Stegun,
    1964, 6.5.21)."""
    half, y = lam / 2, x / 2
    if half <= 0:  # central: the Poisson weights are 1, 0, 0, ...
        return min(1.0, gammaincc(df / 2, y))
    total, q, a = 0.0, None, None
    for j, w in poisson_weights(half):
        if q is None:
            a = df / 2 + j
            q = gammaincc(a, y)
        else:
            if y > 0:
                q = min(1.0, q + math.exp(-y + (a * math.log(y)) - math.lgamma(a + 1)))
            a += 1
        total += w * q
    return min(1.0, total)


def t_sf2(t, df):
    """Two-sided p of a t value with df (also fractional) degrees of freedom."""
    if df <= 0 or not math.isfinite(t):
        return None
    return _betainc(df / 2, 0.5, df / (df + t * t))


def f_sf(f, df1, df2):
    """P(F >= f) for an F distribution with df1 and df2 (also fractional) degrees of freedom."""
    if df1 <= 0 or df2 <= 0:
        return None
    if f <= 0:
        return 1.0
    return _betainc(df2 / 2, df1 / 2, df2 / (df2 + df1 * f))


def norm_sf2(z):
    """Two-sided p of a z value."""
    return math.erfc(abs(z) / math.sqrt(2))


# --- descriptive figures, optionally weighted ---------------------------------------------------------------------

def _weights(values, weights):
    return [1.0] * len(values) if weights is None else [float(w) for w in weights]


def describe(values, weights=None):
    """n, M, SD and the 95 % t interval of M of numbers (None left out; the interval as on the page,
    intervals.mean_ci: from intervals.MIN_N values on). With weights the same figures as the results page
    (results.numeric_stats): weighted M, SD with the correction n / (n - 1) for sampling weights (weights.sd;
    Lumley, 2010) and the interval with the linearization standard error (weights.mean_se) and the degrees of
    freedom of Kish's effective n (math audit 2026-10-05, K-7 and I-1); n stays the number of answers."""
    pairs = [(v, w) for v, w in zip(values, _weights(values, weights)) if v is not None]
    n = len(pairs)
    if not n:
        return {"n": 0, "mean": None, "sd": None, "ci": None}
    if weights is not None:
        xs, ws = [v for v, _ in pairs], [w for _, w in pairs]
        mean, sd = weighting.mean(xs, ws), weighting.sd(xs, ws)
        n_eff = weighting.n_eff([w for w in ws if w])
        return {"n": n, "mean": mean, "sd": sd, "ci": mean_ci(mean, sd, n_eff, se=weighting.mean_se(xs, ws)),
                "n_eff": n_eff}
    mean = sum(v for v, _ in pairs) / n
    sd = ci = None
    if n > 1:
        sd = math.sqrt(sum((v - mean) ** 2 for v, _ in pairs) / (n - 1))
        ci = mean_ci(mean, sd, n)
    return {"n": n, "mean": mean, "sd": sd, "ci": ci}


def count(keys, weights=None):
    """{key: count} of the keys (None left out); with weights the sums of the weights."""
    out = {}
    for k, w in zip(keys, _weights(keys, weights)):
        if k is not None:
            out[k] = out.get(k, 0) + w
    return out


# --- tests --------------------------------------------------------------------------------------------------------

def chi2_test(table, weights=None):
    """Chi-square test of independence of a table [rows][columns] of counts. Rows and columns without any count
    are left out. Returns chi2, df, p, Cramér's V, n and Cochran's rule: "cochran" is True when more than 20 % of the
    expected counts are below 5 or one is below 1 (the p value is then unreliable). An exact p beside it
    (audit 2026-10-05, I-3): "exact_p" with "exact" = "fisher" for every 2 x 2 table (Fisher's exact test), and
    "monte_carlo" with "draws" for a larger table where Cochran's rule warns (permutation test, fixed seed). None
    below 2 x 2 or with weights."""
    if weights is not None:
        return None
    rows = [r for r in table if sum(r)]
    if not rows:
        return None
    cols = [j for j in range(len(rows[0])) if sum(r[j] for r in rows)]
    rows = [[r[j] for j in cols] for r in rows]
    if len(rows) < 2 or len(cols) < 2:
        return None
    rs = [sum(r) for r in rows]
    cs = [sum(r[j] for r in rows) for j in range(len(cols))]
    n = sum(rs)
    chi2, small, tiny = 0.0, 0, False
    for i, r in enumerate(rows):
        for j, o in enumerate(r):
            e = rs[i] * cs[j] / n
            chi2 += (o - e) ** 2 / e
            small += e < 5
            tiny = tiny or e < 1
    df = (len(rows) - 1) * (len(cols) - 1)
    v = math.sqrt(chi2 / (n * (min(len(rows), len(cols)) - 1)))
    # shape: rows and columns that count, for the bias-corrected V of the overview (compare.corrected_v, X53)
    out = {"chi2": chi2, "df": df, "p": chi2_sf(chi2, df), "v": min(v, 1.0), "n": n, "shape": [len(rows), len(cols)],
           "cochran": tiny or small > 0.2 * len(rows) * len(cols), "exact": None, "exact_p": None}
    whole = [[int(o) for o in r] for r in rows]  # counts (1.0 from a column of 0/1 answers is one person)
    if whole != rows:
        return out
    if len(rows) == 2 and len(cols) == 2:
        out.update(exact="fisher", exact_p=fisher_2x2(whole))
    elif out["cochran"]:
        p, draws = permutation_chi2(whole, chi2)
        out.update(exact="monte_carlo", exact_p=p, draws=draws)
    return out


def fisher_2x2(table):
    """Two-sided p of Fisher's exact test of a 2 x 2 table [[a, b], [c, d]]: the sum of the hypergeometric
    probabilities of all tables with the same margins that are not more probable than the one observed (Agresti,
    2013). A table counts as equally probable within a relative tolerance of 1e-7 (Askurious default): the sums of
    lgamma below carry rounding errors of about 1e-13, so without it a table exactly as probable as the observed one
    could drop out, and 1e-7 is far below any difference that matters for p. The log probabilities (lgamma)
    relative to the mode, summed from the mode outward until they no longer count: whole-number binomial
    coefficients took minutes from about n = 10 000 on (code review 2026-10-15, A1)."""
    (a, b), (c, d) = table
    r1, r2, c1 = a + b, c + d, a + c
    low, high = max(0, c1 - r2), min(r1, c1)

    def log_prob(x):  # log of C(r1, x) C(r2, c1 - x) without the common C(n, c1)
        return (math.lgamma(r1 + 1) - math.lgamma(x + 1) - math.lgamma(r1 - x + 1)
                + math.lgamma(r2 + 1) - math.lgamma(c1 - x + 1) - math.lgamma(r2 - c1 + x + 1))

    mode = min(high, max(low, (c1 + 1) * (r1 + 1) // (r1 + r2 + 2)))
    top = log_prob(mode)
    seen = log_prob(a) - top + math.log1p(1e-7)
    total = tail = 0.0
    for step in (1, -1):  # unimodal: each side falls from the mode on
        x = mode if step == 1 else mode - 1
        while low <= x <= high:
            rel = log_prob(x) - top
            if rel < -745:  # exp() is 0 from here on, for every further table
                break
            q = math.exp(rel)
            total += q
            if rel <= seen:
                tail += q
            x += step
    return min(1.0, tail / total)


SEED = 20261005  # the Monte Carlo permutation test draws the same tables every time: the same p on every view
DRAWS = 10_000   # tables drawn with the margins fixed (independent of n)


def permutation_chi2(table, observed):
    """Monte Carlo p of the chi-square statistic of a table with fixed margins (Hope, 1968): random tables with the
    observed margins (each one the table of a random permutation of the cases), (1 + tables with chi2 >= observed) /
    (1 + draws). The tables are drawn cell by cell as hypergeometric counts, all draws at once (numpy), so the time
    does not grow with n; before, every draw shuffled all n cases in Python (code review 2026-10-15, A2). Returns
    (p, draws)."""
    import numpy as np

    rs = [sum(r) for r in table]
    cs = [sum(r[j] for r in table) for j in range(len(table[0]))]
    n = sum(rs)
    rng = np.random.default_rng(SEED)
    left = np.tile(np.array(cs, dtype=np.int64), (DRAWS, 1))  # cases not yet given to a row, per column
    expected = np.outer(rs, cs) / n
    chi2 = np.zeros(DRAWS)
    for i, ri in enumerate(rs):
        if i == len(rs) - 1:  # the last row takes what is left
            counts = left
        else:
            counts = np.zeros_like(left)
            need = np.full(DRAWS, ri, dtype=np.int64)
            rest = left.sum(axis=1)
            for j in range(len(cs)):
                rest = rest - left[:, j]
                x = rng.hypergeometric(left[:, j], rest, need) if j < len(cs) - 1 else need
                counts[:, j] = x
                need = need - x
            left = left - counts
        chi2 += (((counts - expected[i]) ** 2) / expected[i]).sum(axis=1)
    limit = observed - 1e-9 * max(1.0, observed)
    hits = int(np.sum(chi2 >= limit))
    return (1 + hits) / (1 + DRAWS), DRAWS


def _ranks(values):
    """Mid-ranks (1-based) of the values and the tie term sum(t^3 - t)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    ties = 0
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        t = j - i + 1
        ties += t ** 3 - t
        i = j + 1
    return ranks, ties


EXACT_N = 20  # Mann-Whitney with ties: the exact permutation distribution below this many cases (audit I-5)
EXACT_GROUP = 50  # Mann-Whitney without ties: exact while both groups are smaller (Askurious default, audit 2026-10-12,
# S-9): _u_counts then takes at most about n1^2 n2 = 120 000 steps, a few milliseconds


def _u_counts(n1, n2):
    """The number of orderings of n1 + n2 distinct values for every U of the first group: [count for U = 0 ...
    n1 n2], the coefficients of the Gaussian binomial coefficient [n1 + n2 over n1] in q (Mann & Whitney, 1947),
    built as the product of (1 - q^(n2 + i)) / (1 - q^i) for i = 1 ... n1 in whole numbers."""
    size = n1 * n2 + 1
    poly = [1] + [0] * (size - 1)
    for i in range(1, n1 + 1):
        k = n2 + i
        for u in range(size - 1, k - 1, -1):  # times (1 - q^k)
            poly[u] -= poly[u - k]
        for u in range(i, size):  # divided by (1 - q^i): a running sum with step i
            poly[u] += poly[u - i]
    return poly


def mann_whitney(x, y, weights=None):
    """Mann-Whitney U of two groups (Mann & Whitney, 1947; Ralf, audit 2026-10-12, S-9): without ties the exact
    two-sided p while both groups are smaller than EXACT_GROUP (twice the smaller tail of the distribution of U, at
    most 1); with ties below EXACT_N cases the exact conditional permutation distribution (_tied_exact_p); else the
    normal approximation with tie and continuity correction (Hollander, Wolfe & Chicken, 2014; z is the corrected
    one). "exact" says which. U is that of the first group (pairs in which it is higher, ties half); r is the rank-biserial
    correlation 2U/(n1 n2) - 1: positive when the first group tends to higher values (Kerby, 2014)."""
    if weights is not None or len(x) < 1 or len(y) < 1:
        return None
    n1, n2 = len(x), len(y)
    ranks, ties = _ranks(list(x) + list(y))
    n = n1 + n2
    u = sum(ranks[:n1]) - n1 * (n1 + 1) / 2
    var = n1 * n2 / 12 * ((n + 1) - ties / (n * (n - 1)))
    if var <= 0:
        return None
    diff = u - n1 * n2 / 2
    z = (diff - math.copysign(0.5, diff) if diff else 0.0) / math.sqrt(var)
    out = {"u": u, "z": z, "p": norm_sf2(z), "r": 2 * u / (n1 * n2) - 1, "n": n, "exact": False}
    if not ties and n1 < EXACT_GROUP and n2 < EXACT_GROUP:
        counts = _u_counts(n1, n2)
        whole, k = sum(counts), int(round(u))
        low, high = sum(counts[:k + 1]) / whole, sum(counts[k:]) / whole
        out.update(p=min(1.0, 2 * min(low, high)), exact=True)
    elif ties and n < EXACT_N:
        out.update(p=_tied_exact_p(ranks, n1), exact=True)
    return out


def _tied_exact_p(ranks, n1):
    """Exact two-sided p of the Mann-Whitney test with ties (audit 2026-10-05, I-5): the permutation distribution of
    the sum of the first group's mid-ranks, conditional on the ties, counted over all ways to pick n1 of the n
    ranks (dynamic programming over the doubled, hence whole, mid-ranks); twice the smaller tail, at most 1
    (Hollander, Wolfe & Chicken, 2014)."""
    doubled = [int(round(2 * r)) for r in ranks]
    seen = sum(doubled[:n1])
    ways = [{0: 1}] + [{} for _ in range(n1)]  # ways[k]: {sum of k doubled ranks: number of picks}
    for r in doubled:
        for k in range(min(n1, len(doubled)), 0, -1):
            for total, c in ways[k - 1].items():
                ways[k][total + r] = ways[k].get(total + r, 0) + c
    dist = ways[n1]
    whole = sum(dist.values())
    low = sum(c for total, c in dist.items() if total <= seen) / whole
    high = sum(c for total, c in dist.items() if total >= seen) / whole
    return min(1.0, 2 * min(low, high))


SPEARMAN_EXACT = 12  # Spearman without ties: exact distribution of S up to this n (Askurious default, see _s_counts)
_S_COUNTS = {}


def _s_counts(n):
    """Exact null distribution of S = sum (i - p_i)^2 over all n! permutations p of 0 ... n - 1: a list whose
    entry S is the number of permutations with that S. Dynamic programming over the set of ranks already given to
    the first i positions: for every such set (bit mask) a row of counts per partial S, one numpy step per rank
    added. Askurious default SPEARMAN_EXACT = 12: there it costs about 0.1 s CPU and under 10 MB once per process;
    every further pair triples time and memory (13: 0.2 s, 14: 0.6 s, 15: 1.6 s), while the approximation above
    it (_s_tail) is already within 1.1 % of the exact p for p between .001 and .2 at n = 13 (checked against the
    exact distribution up to n = 17)."""
    if n not in _S_COUNTS:
        import numpy as np

        width = (n ** 3 - n) // 3 + 1  # S runs from 0 to (n^3 - n) / 3
        masks = np.zeros(1, dtype=np.int64)
        rows = np.zeros((1, width), dtype=np.int64)
        rows[0, 0] = 1
        for i in range(n):
            free = [(masks >> j) & 1 == 0 for j in range(n)]
            nxt = np.unique(np.concatenate([masks[f] | (1 << j) for j, f in enumerate(free) if f.any()]))
            out = np.zeros((len(nxt), width), dtype=np.int64)
            for j, f in enumerate(free):
                if f.any():
                    d = (i - j) ** 2
                    out[np.searchsorted(nxt, masks[f] | (1 << j)), d:] += rows[f, :width - d]
            masks, rows = nxt, out
        _S_COUNTS[n] = [int(c) for c in rows[0]]
    return _S_COUNTS[n]


def _rho_moments(n):
    """Exact even moments E(rho^2), E(rho^4), E(rho^6) of Spearman's rho under independence without ties, as
    fractions. E(rho^2) = 1/(n - 1) and E(rho^4) = 3(25n^3 - 38n^2 - 35n + 72) / (25n(n + 1)(n - 1)^3) (Kendall &
    Gibbons, 1990). The moments of S are polynomials in n (David, Kendall & Stuart, 1951); the sixth central moment
    of S, n^3 (n - 1)(n + 1)^3 P(n) / 3 810 240 with P below, Askurious fitted exactly (rational arithmetic) to the
    exact distributions of n = 1 ... 16 and checked against n = 17; all three agree with the exact distributions."""
    from fractions import Fraction

    p6 = (1225 * n ** 8 - 4361 * n ** 7 - 178 * n ** 6 + 23818 * n ** 5 - 22783 * n ** 4 - 50081 * n ** 3
          + 54280 * n ** 2 + 44160 * n - 28800)
    mu6_s = Fraction(n ** 3 * (n - 1) * (n + 1) ** 3 * p6, 3810240)
    return (Fraction(1, n - 1),
            Fraction(3 * (25 * n ** 3 - 38 * n ** 2 - 35 * n + 72), 25 * n * (n + 1) * (n - 1) ** 3),
            mu6_s * Fraction(6, n ** 3 - n) ** 6)


_S_SERIES = {}


def _s_series(n):
    """(alpha, [c_k m_k for k = 0 ... 3]) of the approximation in _s_tail: the density of rho is taken as the
    symmetric beta density on [-1, 1] with alpha = (n - 2)/2, w(x) ~ (1 - x^2)^(alpha - 1), the distribution of
    Pearson's r under normality, which already has the variance 1/(n - 1) of rho (Pitman, 1937), times an even
    polynomial c_0 + c_1 x^2 + c_2 x^4 + c_3 x^6 chosen so that the density keeps total mass 1 and the exact
    moments E(rho^2), E(rho^4) and E(rho^6) (_rho_moments): moment fitting in the manner of a Gram-Charlier
    series, with the beta density in place of the normal one (Kendall & Stuart, 1977), so that the support stays
    [-1, 1]. Solved exactly with fractions (m_k are the beta moments E(x^2k) = prod (2j - 1)/(n + 2j - 3))."""
    if n not in _S_SERIES:
        from fractions import Fraction

        m = [Fraction(1)]
        for k in range(1, 7):
            m.append(m[-1] * Fraction(2 * k - 1, n + 2 * k - 3))
        target = [Fraction(1), *_rho_moments(n)]
        a = [[m[j + k] for k in range(4)] + [target[j]] for j in range(4)]
        for col in range(4):  # Gauss-Jordan; the matrix of beta moments is positive definite
            a[col] = [v / a[col][col] for v in a[col]]
            for r in range(4):
                if r != col:
                    a[r] = [x - a[r][col] * y for x, y in zip(a[r], a[col])]
        _S_SERIES[n] = ((n - 2) / 2, [float(a[k][4] * m[k]) for k in range(4)])
    return _S_SERIES[n]


def _s_tail(n, d):
    """P(|S - E S| >= d) of Spearman's S for n > SPEARMAN_EXACT pairs without ties, from the density of _s_series:
    with x = 6(d - 1)/(n^3 - n) the integral of w(t) t^2k over |t| >= x is m_k I_(1 - x^2)(alpha, k + 1/2) (the
    regularized incomplete beta function, substituting u = t^2), so P = sum c_k m_k I_(1 - x^2)(alpha, k + 1/2).
    d - 1 instead of d: S only takes even values, the continuity correction goes half a step of 2 towards the
    centre. Checked against the exact distribution for n = 13 ... 17: at most 1.1 % (n = 13) to 0.7 % (n = 17)
    relative error for exact p between .001 and .2, at most 1.6e-4 absolute there and 3.2e-4 anywhere; the t
    distribution with n - 2 df alone was off by up to 41 % there, an Edgeworth series with the same three moments
    by up to 13 %."""
    x = 6 * max(d - 1, 0) / (n ** 3 - n)
    if x >= 1:
        return 0.0
    alpha, cm = _s_series(n)
    p = sum(c * _betainc(alpha, k + 0.5, 1 - x * x) for k, c in enumerate(cm))
    return min(1.0, max(0.0, p))


def spearman_p(rho, n):
    """Two-sided p of Spearman's rank correlation rho of n pairs without ties (audit 2026-10-12, S-8, rewritten
    2026-10-22) from S = (n^3 - n)(1 - rho)/6, rounded to the nearest even number (S is always even; this takes
    out floating point noise of rho). Two-sided rule: the probability under independence of an S at least as far
    from its mean (n^3 - n)/6 as the observed one, in either direction. Reversing one of the rankings maps S to
    (n^3 - n)/3 - S, so the null distribution is symmetric and this equals twice the tail on the observed side,
    with p = 1 at the mean; there is no choice between doubling and summing smaller probabilities as with Fisher's
    test. Up to SPEARMAN_EXACT pairs from the exact distribution (_s_counts), above it from _s_tail. None below
    three pairs."""
    if rho is None or n < 3:
        return None
    rho = max(-1.0, min(1.0, rho))
    whole = n ** 3 - n
    s = 2 * round(whole * (1 - rho) / 12)
    d = abs(s - whole / 6)
    if n > SPEARMAN_EXACT:
        return _s_tail(n, d)
    counts = _s_counts(n)
    far = sum(c for total, c in enumerate(counts) if c and abs(total - whole / 6) >= d)
    return far / math.factorial(n)


def kruskal_wallis(groups, weights=None):
    """Kruskal-Wallis H of two or more groups (tie corrected, chi-square with k - 1 df) and epsilon squared
    H (n + 1) / (n^2 - 1) (Tomczak & Tomczak, 2014)."""
    if weights is not None:
        return None
    groups = [list(g) for g in groups if g]
    if len(groups) < 2:
        return None
    flat = [v for g in groups for v in g]
    n = len(flat)
    ranks, ties = _ranks(flat)
    correction = 1 - ties / (n ** 3 - n)
    if correction <= 0:
        return None
    h, start = 0.0, 0
    for g in groups:
        h += sum(ranks[start:start + len(g)]) ** 2 / len(g)
        start += len(g)
    h = (12 / (n * (n + 1)) * h - 3 * (n + 1)) / correction
    df = len(groups) - 1
    return {"h": h, "df": df, "p": chi2_sf(h, df), "e2": min(h * (n + 1) / (n * n - 1), 1.0), "n": n}


def _rescaled(groups):
    """The groups times one common power of two that brings the largest |value| near 1, when it lies far from 1
    (below 1e-50 or above 1e50); else unchanged. Squares of variances of values around 1e-100 underflow to 0 and
    the degrees of freedom of Welch became 0/0 (audit 2026-10-23, M4). t, df, p, F, ω² and the standardised
    difference do not change with the scale, and a power of two scales every value exactly."""
    top = max((abs(v) for g in groups for v in g if v is not None), default=0.0)
    if not top or not math.isfinite(top) or 1e-50 <= top <= 1e50:
        return groups
    # math.ldexp per value: 2 ** 1027 (a top of 6e-310, subnormal) is no float of its own, the scaled values are
    # (audit storage and calculations 2026-10-28, M4-R)
    shift = -math.frexp(top)[1]
    return [[None if v is None else math.ldexp(v, shift) for v in g] for g in groups]


def welch_t(x, y, weights=None):
    """Welch's t test of two groups (Welch, 1947) with the standardised difference d = delta* = (M1 - M2) / s and
    s = sqrt((SD1^2 + SD2^2) / 2), the average of both variances, and its 95 % interval from the variance of
    Bonett (2008), which does not assume equal variances either. Hedges' g with the pooled SD and its interval of
    Hedges (1981) assumed equal variances beside a test that does not: with unequal spread and unequal groups its
    interval held 72 to 75 % instead of 95 %, and its target moved with the ratio of the group sizes (math audit
    2026-10-06, L-2). d is positive when the first group's mean is higher."""
    if weights is not None:
        return None
    x, y = _rescaled([list(x), list(y)])  # nothing it returns is in the unit of the values (M4)
    a, b = describe(x), describe(y)
    n1, n2 = a["n"], b["n"]
    if n1 < 2 or n2 < 2:
        return None
    v1, v2 = a["sd"] ** 2 / n1, b["sd"] ** 2 / n2
    if v1 + v2 == 0:
        return None
    t = (a["mean"] - b["mean"]) / math.sqrt(v1 + v2)
    df = (v1 + v2) ** 2 / (v1 ** 2 / (n1 - 1) + v2 ** 2 / (n2 - 1))
    s1, s2 = a["sd"] ** 2, b["sd"] ** 2
    s = (s1 + s2) / 2  # the average variance
    d = (a["mean"] - b["mean"]) / math.sqrt(s)
    var = (d * d * (s1 ** 2 / (n1 - 1) + s2 ** 2 / (n2 - 1)) / (8 * s * s)
           + (s1 / (n1 - 1) + s2 / (n2 - 1)) / s)
    se = math.sqrt(var)
    # the point value with the small-sample correction J = 1 − 3 / (4 m − 1) as Hedges' g (Hedges, 1981), m the
    # degrees of freedom of the average variance (Satterthwaite; n1 + n2 − 2 for equal variances): uncorrected it was
    # too large for small groups (+0.07 at 5/5, +0.13 at 8/30), corrected within ±0.03. The interval stays Bonett's
    # around the uncorrected value, which holds its 95 % (audit 2026-10-10, B-25).
    m = (s1 + s2) ** 2 / (s1 ** 2 / (n1 - 1) + s2 ** 2 / (n2 - 1)) if s1 or s2 else n1 + n2 - 2
    g = d * (1 - 3 / (4 * m - 1)) if m > 1 else d
    return {"t": t, "df": df, "p": t_sf2(t, df), "d": g, "d_raw": d, "d_ci": (d - Z975 * se, d + Z975 * se),
            "n": n1 + n2}


def welch_anova(groups, weights=None):
    """Welch's F test of two or more group means with unequal variances (Welch, 1951) and omega squared of the
    one-way ANOVA (Olejnik & Algina, 2003), set to 0 when negative. Welch weighs each group by n / SD^2: a group
    without variance (e.g. five times "5") has no weight then, and the Brown-Forsythe F* takes over ("method":
    "brown_forsythe", "constant": the positions of those groups; Brown & Forsythe, 1974). None if no group varies
    or below two groups of two (app/compare.py names the groups without variance)."""
    if weights is not None:
        return None
    groups = _rescaled([list(g) for g in groups])  # F, p and ω² do not change with the scale (M4)
    stats = [describe(g) for g in groups if g]
    k = len(stats)
    if k < 2 or any(s["n"] < 2 for s in stats):
        return None
    constant = [i for i, s in enumerate(stats) if not s["sd"]]
    n = sum(s["n"] for s in stats)
    grand = sum(s["n"] * s["mean"] for s in stats) / n
    ss_between = sum(s["n"] * (s["mean"] - grand) ** 2 for s in stats)
    ss_within = sum((s["n"] - 1) * s["sd"] ** 2 for s in stats)
    if not ss_within:
        return None
    ms_within = ss_within / (n - k)
    omega2 = max(0.0, (ss_between - (k - 1) * ms_within) / (ss_between + ss_within + ms_within))
    if constant:
        # Brown-Forsythe: F* = sum n_i (m_i - m)^2 / sum (1 - n_i / N) s_i^2, denominator df after Satterthwaite
        parts = [(1 - s["n"] / n) * s["sd"] ** 2 for s in stats]
        denominator = sum(parts)
        f = ss_between / denominator
        df1, df2 = k - 1, 1 / sum((c / denominator) ** 2 / (s["n"] - 1) for c, s in zip(parts, stats))
        return {"f": f, "df1": df1, "df2": df2, "p": f_sf(f, df1, df2), "omega2": omega2, "n": n,
                "method": "brown_forsythe", "constant": constant}
    w = [s["n"] / s["sd"] ** 2 for s in stats]
    total_w = sum(w)
    grand_w = sum(wi * s["mean"] for wi, s in zip(w, stats)) / total_w
    a = sum(wi * (s["mean"] - grand_w) ** 2 for wi, s in zip(w, stats)) / (k - 1)
    tmp = sum((1 - wi / total_w) ** 2 / (s["n"] - 1) for wi, s in zip(w, stats))
    f = a / (1 + 2 * (k - 2) / (k * k - 1) * tmp)
    df1, df2 = k - 1, (k * k - 1) / (3 * tmp)
    return {"f": f, "df1": df1, "df2": df2, "p": f_sf(f, df1, df2), "omega2": omega2, "n": n, "method": "welch"}
