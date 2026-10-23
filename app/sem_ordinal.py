"""Statements as ordered categories (SEM extensions 2026-10-17, point 3): the covariance-based model on polychoric
correlations with diagonally weighted least squares and robust corrections, the estimator known as "WLSMV"
(Muthén, 1984; Muthén et al., 1997). Numbers only, numpy and the standard library.

  thresholds     per statement from its cumulative shares, tau_j = Phi^-1(P(X <= j)) (Olsson, 1979, two steps)
  polychorics    per pair the ML correlation of the bivariate normal behind the two statements, the thresholds held
                 fixed; the bivariate normal distribution as an integral over the angle of the correlation (Drezner
                 & Wesolowsky, 1990), near |r| = 1 by Owen's (1956) T function, both by Gauss-Legendre quadrature
  Gamma          the asymptotic covariance of the polychorics from the cases' influence functions, the estimation of
                 the thresholds taken into account by the chain rule of the two-step estimator, every information
                 as the mean outer product of the cases' scores (Muthén, 1984; Muthén et al., 1997)
  estimation     F = (r - rho(theta))' W (r - rho(theta)) with W = diag(Gamma)^-1 over the correlations, the "delta"
                 form: every statement has the variance 1 (its error variance is what the model leaves), so only the
                 correlations carry information; Gauss-Newton steps
  errors, test   sandwich (Delta' W Delta)^-1 Delta' W Gamma W Delta (Delta' W Delta)^-1 / (N - 1); the test mean- and
                 variance-adjusted ("scaled and shifted", Asparouhov & Muthén, 2010), CFI, TLI and RMSEA from it
                 against the model of uncorrelated statements, all on the scaled and shifted statistic
Complete cases only (Askurious default for ordinal data: one set of cases for every pair keeps Gamma one covariance);
every column of the model must be ordinal (at most MAX_CATEGORIES answers; a 0/1 group counts), an observed
variable may not have paths leaving it while it is explained itself (its error variance would reach further), and
interaction terms, single questions with a reliability, formative constructs and FIML do not combine with it."""
import math
from statistics import NormalDist

import numpy as np

from app import sem

MAX_CATEGORIES = 10
BOUND = 8.0  # stands in for an infinite threshold
_N = NormalDist()

MUTHEN = ("Muthén, B. (1984). A general structural equation model with dichotomous, ordered categorical, and continuous "
          "latent variable indicators. Psychometrika, 49(1), 115–132. https://doi.org/10.1007/BF02294210")
MUTHEN_1997 = ("Muthén, B. O., du Toit, S. H. C., & Spisic, D. (1997). Robust inference using weighted least squares and "
               "quadratic estimating equations in latent variable modeling with categorical and continuous outcomes. "
               "Unpublished manuscript. https://www.statmodel.com/download/Article_075.pdf")
OLSSON = ("Olsson, U. (1979). Maximum likelihood estimation of the polychoric correlation coefficient. Psychometrika, "
          "44(4), 443–460. https://doi.org/10.1007/BF02296207")
ASPAROUHOV = ("Asparouhov, T., & Muthén, B. (2010). Simple second order chi-square correction. Technical appendix. "
              "https://www.statmodel.com/download/WLSMV_new_chi21.pdf")
DREZNER = ("Drezner, Z., & Wesolowsky, G. O. (1990). On the computation of the bivariate normal integral. Journal of "
           "Statistical Computation and Simulation, 35(1–2), 101–107. https://doi.org/10.1080/00949659008811236")
OWEN = ("Owen, D. B. (1956). Tables for computing bivariate normal probabilities. The Annals of Mathematical "
        "Statistics, 27(4), 1075–1090. https://doi.org/10.1214/aoms/1177728074")
SOURCES = (MUTHEN, OLSSON, MUTHEN_1997, ASPAROUHOV, DREZNER, OWEN)

# Gauss-Legendre points and weights on (-1, 1) for the two integrals of bvn (Askurious default: 24 points keep the
# error below 1e-13 for |h|, |k| <= 8, where the thresholds end)
_POINTS = 24
_NODES, _WEIGHTS = np.polynomial.legendre.leggauss(_POINTS)
_ANGLE_LIMIT = 0.95  # Askurious default: up to |r| = 0.95 the angle integral, above it Owen's T
_erf = np.vectorize(math.erf)


def phi(x):
    return np.exp(-0.5 * np.asarray(x, dtype=float) ** 2) / math.sqrt(2 * math.pi)


def Phi(x):
    return 0.5 * (1 + _erf(np.asarray(x, dtype=float) / math.sqrt(2)))


def _owen_t(h, a):
    """Owen's T(h, a) = 1/(2 pi) int_0^a exp(-h^2 (1 + t^2) / 2) / (1 + t^2) dt for 0 <= a <= 1 (flat arrays)."""
    t = a[None, :] * (_NODES[:, None] + 1) / 2  # (points, m)
    q = 1 + t * t
    return a / 2 * (_WEIGHTS @ (np.exp(-0.5 * (h * h)[None, :] * q) / q)) / (2 * math.pi)


def _owen_t_ratio(x, y):
    """T(x, y / x) for flat arrays x, y, not both 0 in one place. Up to |y / x| = 1 the integral itself; beyond it
    T(x, a) = sign(a) (1/4 - (Phi(|x|) - 1/2) (Phi(|y|) - 1/2) - T(|y|, |x / y|)) (Owen, 1956), which keeps the
    integral on (0, 1) and needs no division by x (x = 0 gives T(0, +-inf) = +-1/4)."""
    out = np.zeros(x.shape)
    near = np.abs(y) <= np.abs(x)
    if near.any():
        a = y[near] / x[near]
        out[near] = np.sign(a) * _owen_t(x[near], np.abs(a))
    far = ~near
    if far.any():
        xf, yf = x[far], y[far]
        sign = np.sign(yf) * np.where(xf < 0, -1.0, 1.0)
        cdf = Phi(np.concatenate([np.abs(xf), np.abs(yf)])) - 0.5
        m = len(xf)
        out[far] = sign * (0.25 - cdf[:m] * cdf[m:] - _owen_t(np.abs(yf), np.abs(xf / yf)))
    return out


def bvn(h, k, r):
    """P(X < h, Y < k) of the standard bivariate normal with correlation r (arrays h, k of one shape).

    |r| <= 0.95: d P / d r is the density; with r = sin(t) and integrated over the angle from 0,
    P = Phi(h) Phi(k) + 1/(2 pi) int_0^asin(r) exp(-(h^2 + k^2 - 2 h k sin t) / (2 cos^2 t)) dt (Drezner &
    Wesolowsky, 1990), smooth enough there for 24 points (cos t >= 0.31).
    |r| > 0.95: that integrand grows steep near |t| = pi/2, so Owen's (1956) reduction to two univariate T functions,
    P = Phi(h)/2 + Phi(k)/2 - T(h, a_h) - T(k, a_k) - beta with a_h = (k - r h) / (h sqrt(1 - r^2)) and a_k alike,
    beta = 1/2 when h k < 0 or h k = 0 with h + k < 0; T on (0, 1) by Gauss-Legendre.
    |r| = 1: the limits Phi(min(h, k)) and max(Phi(h) + Phi(k) - 1, 0)."""
    h, k = np.broadcast_arrays(np.asarray(h, dtype=float), np.asarray(k, dtype=float))
    shape = h.shape
    h, k = h.ravel(), k.ravel()
    if r >= 1:
        return np.clip(Phi(np.minimum(h, k)), 0.0, 1.0).reshape(shape)
    if r <= -1:
        return np.clip(Phi(h) + Phi(k) - 1, 0.0, 1.0).reshape(shape)
    cdf = Phi(np.concatenate([h, k]))
    ch, ck = cdf[:len(h)], cdf[len(h):]
    if abs(r) <= _ANGLE_LIMIT:
        alpha = math.asin(r)
        sin = np.sin(alpha * (_NODES + 1) / 2)[:, None]  # (points, 1)
        cos2 = 1 - sin * sin
        f = np.exp(-(h * h + k * k - 2 * h * k * sin) / (2 * cos2))
        out = ch * ck + alpha / 2 * (_WEIGHTS @ f) / (2 * math.pi)
    else:
        s = math.sqrt((1 - r) * (1 + r))
        zero = (h == 0) & (k == 0)
        hh, kk = np.where(zero, 1.0, h), np.where(zero, 1.0, k)  # placeholders, set below
        # k - r h as (k - h) + (1 - r) h (or (k + h) - (1 + r) h for r < 0): 1 - r is exact here, so near |r| = 1
        # the small difference keeps its digits
        if r > 0:
            yh, yk = (kk - hh) + (1 - r) * hh, (hh - kk) + (1 - r) * kk
        else:
            yh, yk = (kk + hh) - (1 + r) * hh, (hh + kk) - (1 + r) * kk
        t = _owen_t_ratio(hh, yh / s) + _owen_t_ratio(kk, yk / s)
        beta = np.where((h * k < 0) | ((h * k == 0) & (h + k < 0)), 0.5, 0.0)
        out = np.where(zero, 0.25 + math.asin(r) / (2 * math.pi), (ch + ck) / 2 - t - beta)
    return np.clip(out, 0.0, 1.0).reshape(shape)


def bvn_density(h, k, r):
    """The density of the standard bivariate normal with correlation r at (h, k)."""
    h, k = np.asarray(h, dtype=float), np.asarray(k, dtype=float)
    q = 1 - r * r
    return np.exp(-(h * h - 2 * r * h * k + k * k) / (2 * q)) / (2 * math.pi * math.sqrt(q))


def _codes(column):
    """The categories of a column as 0 .. K-1 and K."""
    values = np.unique(column)
    return np.searchsorted(values, column), len(values)


def thresholds(codes, K):
    """tau_1 .. tau_{K-1} from the cumulative shares of the categories 0 .. K-1."""
    counts = np.bincount(codes, minlength=K)
    cum = np.cumsum(counts)[:-1] / len(codes)
    return np.array([_N.inv_cdf(min(max(c, 1e-10), 1 - 1e-10)) for c in cum])


def _bounds(tau):
    return np.concatenate([[-BOUND], tau, [BOUND]])


def _cells(ta, tb, r):
    """The probabilities of all cells (Ka, Kb) and their derivatives by r, by the upper and the lower thresholds."""
    a, b = _bounds(ta), _bounds(tb)
    H, K = np.meshgrid(a, b, indexing="ij")
    F = bvn(H, K, r)  # (Ka+1, Kb+1) cumulative at every corner
    pi = F[1:, 1:] - F[:-1, 1:] - F[1:, :-1] + F[:-1, :-1]
    D = bvn_density(H, K, r)
    dpi = D[1:, 1:] - D[:-1, 1:] - D[1:, :-1] + D[:-1, :-1]
    return np.maximum(pi, 1e-300), dpi, (H, K, F)


def polychoric(ca, Ka, cb, Kb, ta, tb, start=0.0):
    """The ML correlation of two ordinal columns (codes ca, cb) with their thresholds fixed (Olsson, 1979): Fisher
    scoring on r. Returns (r, the cell table n_ij)."""
    n = np.zeros((Ka, Kb))
    np.add.at(n, (ca, cb), 1)
    r = float(np.clip(start, -0.95, 0.95))
    for _ in range(100):
        pi, dpi, _ = _cells(ta, tb, r)
        grad = float(np.sum(n * dpi / pi))
        info = float(np.sum(n.sum() * dpi * dpi / pi))
        if info <= 0:
            break
        step = grad / info
        new = float(np.clip(r + step, -0.999, 0.999))
        if abs(new - r) < 1e-10:
            r = new
            break
        r = new
    return r, n


def _edge_slope(t, other, r):
    """The slope of Phi2(t, other; r) along t: phi(t) times the conditional probability
    Phi((other - r t) / sqrt(1 - r^2)) of the other coordinate below its bound."""
    return phi(t) * Phi((other - r * t) / math.sqrt(1 - r * r))


def _on_thresholds(category, at_upper, at_lower, K):
    """Per case the derivatives by the K - 1 inner thresholds of a quantity that rises with the upper bound of the
    case's category (by at_upper) and falls with its lower bound (by at_lower). Category c lies between the bounds c
    and c + 1; the outer bounds (+-BOUND) are no parameters and drop."""
    n = len(category)
    out = np.zeros((n, K + 1))
    rows = np.arange(n)
    out[rows, category + 1] += at_upper
    out[rows, category] -= at_lower
    return out[:, 1:-1]


def _threshold_influence(category, tau):
    """The influence of each case on the thresholds of one statement, (N, K - 1): its score of the univariate
    likelihood, sum log P(category), times the inverse information. The thresholds are this likelihood's maximum
    (Olsson, 1979, first step); the information is taken as the mean outer product of the scores (Muthén et al.,
    1997)."""
    bounds = _bounds(tau)
    K = len(tau) + 1
    cdf, dens = Phi(bounds), phi(bounds)
    p_cat = (cdf[1:] - cdf[:-1])[category]
    score = _on_thresholds(category, dens[category + 1] / p_cat, dens[category] / p_cat, K)
    info = score.T @ score / len(category)
    return np.linalg.solve(info, score.T).T


def _correlation_influence(ca, cb, ta, tb, r, inf_a, inf_b):
    """The influence of each case on the polychoric correlation of one pair, (N,), with the thresholds estimated
    first (two-step estimator, Olsson, 1979; Muthén, 1984). The correlation solves mean s_r(r, tau) = 0; a first-order
    expansion in r and in the thresholds gives
        r_hat - r = (mean s_r - E[s_r u_a] (tau_a_hat - tau_a) - E[s_r u_b] (tau_b_hat - tau_b)) / E[s_r^2],
    with u the derivatives of log pi by the thresholds (by the information identity, minus the expected derivative
    of s_r by them), the expectations as means over the cases (Muthén et al., 1997)."""
    pi, dpi, (H, K, _) = _cells(ta, tb, r)
    p_case = pi[ca, cb]
    s_r = dpi[ca, cb] / p_case
    # a threshold of a moves a row of corners: d pi / d tau_a,m = slope at (tau_a,m, upper b) - slope at (.., lower b)
    along_a = _edge_slope(H, K, r)
    along_b = _edge_slope(K, H, r)
    row = along_a[:, 1:] - along_a[:, :-1]  # (Ka+1, Kb): bound of a by category of b
    col = along_b[1:, :] - along_b[:-1, :]  # (Ka, Kb+1): category of a by bound of b
    u_a = _on_thresholds(ca, row[ca + 1, cb] / p_case, row[ca, cb] / p_case, len(ta) + 1)
    u_b = _on_thresholds(cb, col[ca, cb + 1] / p_case, col[ca, cb] / p_case, len(tb) + 1)
    n = len(ca)
    return (s_r - inf_a @ (s_r @ u_a / n) - inf_b @ (s_r @ u_b / n)) / float(s_r @ s_r / n)


def moments(x):
    """Thresholds, polychoric correlations and their asymptotic covariance Gamma of the complete ordinal data x
    (N, p): {"tau": [arrays], "r": matrix, "pairs": [(a, b)], "gamma": (pairs x pairs), "codes", "K"}.
    Gamma is the mean product of the cases' influence values on the correlations (Muthén, 1984; Muthén et al., 1997):
    at the estimates the influence values have mean zero, so this is their covariance; Askurious default: divided by
    N, the empirical distribution of the cases."""
    x = np.asarray(x, dtype=float)
    N, p = x.shape
    codes, Ks, taus = [], [], []
    for j in range(p):
        c, K = _codes(x[:, j])
        if K < 2:
            raise sem.SemError("constant")
        codes.append(c)
        Ks.append(K)
        taus.append(thresholds(c, K))
    inf_tau = [_threshold_influence(codes[j], taus[j]) for j in range(p)]
    pairs = [(a, b) for a in range(p) for b in range(a + 1, p)]
    R = np.eye(p)
    influence = np.empty((N, len(pairs)))
    for col, (a, b) in enumerate(pairs):
        r, _ = polychoric(codes[a], Ks[a], codes[b], Ks[b], taus[a], taus[b],
                          start=float(np.corrcoef(x[:, a], x[:, b])[0, 1]))
        R[a, b] = R[b, a] = r
        influence[:, col] = _correlation_influence(codes[a], codes[b], taus[a], taus[b], r, inf_tau[a], inf_tau[b])
    gamma = influence.T @ influence / N
    return {"tau": taus, "r": R, "pairs": pairs, "gamma": gamma, "K": Ks, "N": N}


class _Delta:
    """The covariance-based model in the delta form: the RAM of sem._Ram without the error variances of the
    statements and of explained observed variables (1 minus what the model explains) and with the variance of an
    observed exogenous variable fixed at 1."""

    def __init__(self, spec, cols):
        self.full = sem._Ram(spec, cols)
        self.ram = ram = sem._Ram(spec, cols)
        endo = sem.endogenous(spec)
        drop = []
        for k, (kind, i, j, label) in enumerate(ram.free):
            if label[0] == "residual":
                drop.append(k)
            elif label[0] == "variance" and label[1] in spec["observed"]:
                drop.append(k)
                if label[1] not in endo:
                    ram.S0[i, i] = 1.0
        self.kept = [k for k in range(len(ram.free)) if k not in drop]
        ram.free = [ram.free[k] for k in self.kept]
        ram.q = len(ram.free)

    def expand(self, theta):
        """The full parameter vector (sem._Ram of the spec): an error variance is 1 minus the common part, the
        variance of an observed exogenous variable 1."""
        full = np.zeros(self.full.q)
        full[self.kept] = theta
        sigma, _, _ = self.ram.implied(theta)
        endo = sem.endogenous(self.full.spec)
        for k, (kind, i, j, label) in enumerate(self.full.free):
            if k in self.kept:
                continue
            if label[0] == "residual" or label[1] in endo:
                full[k] = 1.0 - sigma[i, i]
            else:
                full[k] = 1.0
        return full


def delta_ok(spec):
    """Whether the delta form can take the model: no observed variable that is explained and explains."""
    spec = sem.check(spec)
    endo = sem.endogenous(spec)
    return not any(o in endo and any(a == o for a, _ in spec["paths"]) for o in spec["observed"])


def fit_wlsmv(x, cols, spec, light=False, start=None, found=None):
    """The model on the complete cases x (N, p) with every column ordinal. Returns what sem.fit_ml returns (values on
    the scale of the latent responses: every statement with variance 1), with "estimator": "wlsmv"."""
    spec = sem.check(spec)
    x = np.asarray(x, dtype=float)
    N, p = x.shape
    if N < 3 or p != len(cols):
        raise sem.SemError("data")
    found = found or moments(x)
    model = _Delta(spec, cols)
    ram = model.ram
    rows, cols_ = np.tril_indices(p, -1)
    r = found["r"]
    s = r[rows, cols_]
    gamma = found["gamma"]
    # the pairs of moments() run a < b by rows; the lower triangle runs b > a by rows: put Gamma in that order
    order = {pair: k for k, pair in enumerate(found["pairs"])}
    idx = [order[(c, rr)] for rr, c in zip(rows, cols_)]
    gamma = gamma[np.ix_(idx, idx)]
    w = 1.0 / np.maximum(np.diag(gamma), 1e-12)
    theta = (np.asarray(start, dtype=float) if start is not None else
             model.full.start(r)[model.kept])
    converged = False
    for _ in range(sem.MAX_ITER):
        sigma, d = ram.derivatives(theta)
        delta = d[:, rows, cols_].T  # (pairs, q)
        e = s - sigma[rows, cols_]
        a = delta.T @ (w[:, None] * delta)
        try:
            step = np.linalg.solve(a, delta.T @ (w * e))
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(a, delta.T @ (w * e), rcond=None)[0]
        f_old = float(e @ (w * e))
        t = 1.0
        for _ in range(40):
            new = theta + t * step
            e_new = s - ram.implied(new)[0][rows, cols_]
            if float(e_new @ (w * e_new)) <= f_old + 1e-14:
                break
            t /= 2
        theta = new
        if np.all(np.abs(t * step) < sem.TOL * (1 + np.abs(theta))):
            converged = True
            break
    full = model.expand(theta)
    if not converged:
        return {"method": "ml", "estimator": "wlsmv", "converged": False, "n": N, "problems": ["converge"]}
    std = sem._standardised(model.full, full)
    if light:
        return {"method": "ml", "estimator": "wlsmv", "converged": True, "n": N, "theta": theta.tolist(), "std": std,
                "admissible": not sem._inadmissible(model.full, full, std)}
    sigma, d = ram.derivatives(theta)
    delta = d[:, rows, cols_].T
    e = s - sigma[rows, cols_]
    a = delta.T @ (w[:, None] * delta)
    problems = []
    if not sem._identified(a):
        problems.append("identified")
        a_inv = np.linalg.pinv(a)
    else:
        a_inv = np.linalg.inv(a)
    wd = w[:, None] * delta
    # Askurious default (Ralf, 2026-10-17): N - 1 in the statistic and the errors, the divisor of the sample moments
    vcov = a_inv @ (wd.T @ gamma @ wd) @ a_inv / (N - 1)
    df = len(s) - ram.q
    T = (N - 1) * float(e @ (w * e))
    out = {"method": "ml", "estimator": "wlsmv", "robust": True, "converged": True, "n": N, "p_vars": p, "q": ram.q,
           "df": df, "chi2": T, "p": sem._chi2_p(T, df) if df > 0 else None, "theta": full.tolist(), "scaled": None}
    W = np.diag(w)
    if df > 0:
        u = W - wd @ a_inv @ wd.T
        ug = u @ gamma
        tr1, tr2 = float(np.trace(ug)), float(np.sum(ug * ug.T))
        scale = math.sqrt(df / tr2)
        shift = df - scale * tr1
        t_ss = scale * T + shift
        out["scaled"] = {"chi2": t_ss, "p": sem._chi2_p(t_ss, df), "a": scale, "b": shift, "c": 1 / scale}
        # the baseline: uncorrelated statements (no parameters), scaled and shifted the same way
        bg = W @ gamma
        b1, b2 = float(np.trace(bg)), float(np.sum(bg * bg.T))
        base_df = len(s)
        base_t = (N - 1) * float(s @ (w * s))
        b_scale = math.sqrt(base_df / b2)
        base_ss = b_scale * base_t + base_df - b_scale * b1
        out["baseline"] = (base_ss, base_df)
        d_model, d_base = max(t_ss - df, 0.0), max(base_ss - base_df, 0.0)
        out["cfi"] = 1.0 if max(d_model, d_base) == 0 else 1 - d_model / max(d_model, d_base)
        out["tli"] = (base_ss / base_df - t_ss / df) / (base_ss / base_df - 1) if base_ss / base_df > 1 else None
        out["rmsea"] = math.sqrt(max(t_ss - df, 0.0) / (df * (N - 1)))
        out["rmsea_ci"] = sem.rmsea_ci(t_ss, df, N - 1)
    out["srmr"] = math.sqrt(float(np.sum(e ** 2)) / (p * (p + 1) / 2))  # Askurious default: the diagonal (residual 0) counts
    out["residuals"] = sem._largest_residuals(r, ram.implied(theta)[0] + np.diag(1 - np.diag(ram.implied(theta)[0])),
                                             model.full.manifest)
    # the covariance of the full vector by the delta method (the error variances follow the other parameters)
    J = np.zeros((model.full.q, ram.q))
    for k in range(ram.q):
        h = 1e-6 * max(1.0, abs(theta[k]))
        up, down = theta.copy(), theta.copy()
        up[k] += h
        down[k] -= h
        J[:, k] = (model.expand(up) - model.expand(down)) / (2 * h)
    vfull = J @ vcov @ J.T
    out.update(sem._tables(model.full, spec, full, vfull, np.ones(model.full.q), np.ones(p), r))
    problems += out.pop("inadmissible")
    out["problems"] = sorted(set(problems))
    out["admissible"] = not out["problems"]
    out["thresholds"] = {c: t.tolist() for c, t in zip(model.full.manifest, found["tau"])}
    return out


def ordinal_columns(x, max_categories=MAX_CATEGORIES):
    """Whether every column of x has at most max_categories different values (and at least two)."""
    x = np.asarray(x, dtype=float)
    return all(2 <= len(np.unique(x[:, j])) <= max_categories for j in range(x.shape[1]))
