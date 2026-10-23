"""Linear mixed models for diary studies (longitudinal and multilevel concept 2026-10-17, E8): entries within persons,
y_ij = X_ij beta + Z_ij b_i + e_ij with b_i ~ N(0, G) per person and e_ij ~ N(0, sigma²), a random intercept and,
on request, a random slope. Numbers only (numpy), checked against independently computed reference values
(tests/analysis/test_mixed.py).

Estimation: restricted maximum likelihood with the profiled REML deviance (Bates et al., 2015): G = sigma² L L' with L
the lower-triangular relative factor (theta), sigma² and beta profiled out, the REML deviance per person through the
Woodbury identity (H_i = I + Z_i L L' Z_i', so only q x q matrices per person), theta by Nelder-Mead. Tests of the fixed
effects with the degrees of freedom after Satterthwaite (1946; for mixed models Kuznetsova et al., 2017):
df = 2 (c'Vc)² / (g' A g) with g the gradient of c'Vc in the variance parameters (theta, sigma) and A = 2 H^-1, H the
Hessian of the REML deviance in them, both by central differences.

Predictors are split as Enders and Tofighi (2007) advise: within persons (the entry's value minus the person's
mean, CWC) and between persons (the person's mean). ICC(1) of the outcome from the model without predictors."""
import math

import numpy as np

from app.scales import _betainc

BATES = ("Bates, D., Mächler, M., Bolker, B., & Walker, S. (2015). Fitting linear mixed-effects models using lme4. "
         "Journal of Statistical Software, 67(1), 1–48. https://doi.org/10.18637/jss.v067.i01")
KUZNETSOVA = ("Kuznetsova, A., Brockhoff, P. B., & Christensen, R. H. B. (2017). lmerTest package: Tests in linear mixed "
              "effects models. Journal of Statistical Software, 82(13), 1–26. https://doi.org/10.18637/jss.v082.i13")
SATTERTHWAITE = ("Satterthwaite, F. E. (1946). An approximate distribution of estimates of variance components. "
                 "Biometrics Bulletin, 2(6), 110–114. https://doi.org/10.2307/3002019")
ENDERS_TOFIGHI = ("Enders, C. K., & Tofighi, D. (2007). Centering predictor variables in cross-sectional multilevel "
                  "models: A new look at an old issue. Psychological Methods, 12(2), 121–138. "
                  "https://doi.org/10.1037/1082-989X.12.2.121")
LAIRD_WARE = ("Laird, N. M., & Ware, J. H. (1982). Random-effects models for longitudinal data. Biometrics, 38(4), "
              "963–974. https://doi.org/10.2307/2529876")
SOURCES = (LAIRD_WARE, BATES, KUZNETSOVA, SATTERTHWAITE, ENDERS_TOFIGHI)


class MixedError(ValueError):
    pass


class _Data:
    """The cross-products per person that the REML deviance needs."""

    def __init__(self, y, X, Z, groups):
        self.n, self.p = X.shape
        self.q = Z.shape[1]
        order = np.argsort(groups, kind="stable")
        y, X, Z, groups = y[order], X[order], Z[order], groups[order]
        cuts = np.flatnonzero(np.diff(groups)) + 1
        self.blocks = []
        for idx in np.split(np.arange(self.n), cuts):
            Zi, Xi, yi = Z[idx], X[idx], y[idx]
            self.blocks.append((Zi.T @ Zi, Zi.T @ Xi, Zi.T @ yi))
        self.XtX, self.Xty, self.yty = X.T @ X, X.T @ y, float(y @ y)
        self.m = len(self.blocks)


def _lower(theta, q):
    L = np.zeros((q, q))
    L[np.tril_indices(q)] = theta  # the lower triangle row by row
    return L


def _parts(theta, d):
    """(log det of the H_i summed, X'H^-1X, X'H^-1y, y'H^-1y) for the relative factor theta."""
    L = _lower(theta, d.q)
    logdet = 0.0
    xhx, xhy, yhy = d.XtX.copy(), d.Xty.copy(), d.yty
    I = np.eye(d.q)
    for ztz, ztx, zty in d.blocks:
        M = I + L.T @ ztz @ L
        sign, ld = np.linalg.slogdet(M)
        logdet += ld
        Minv = np.linalg.inv(M)
        A = L @ Minv @ L.T  # Z'H^-1 = Z' - Z'Z A Z'  with A = L M^-1 L'
        lx, ly = ztx, zty
        xhx -= lx.T @ A @ lx
        xhy -= lx.T @ A @ ly
        yhy -= float(ly @ A @ ly)
    return logdet, xhx, xhy, yhy


def _profiled(theta, d):
    """The REML deviance with beta and sigma² profiled out (Bates et al., 2015)."""
    logdet, xhx, xhy, yhy = _parts(theta, d)
    sign, ldx = np.linalg.slogdet(xhx)
    if sign <= 0:
        return math.inf
    beta = np.linalg.solve(xhx, xhy)
    r2 = yhy - float(xhy @ beta)
    if r2 <= 0:
        return math.inf
    k = d.n - d.p
    return logdet + ldx + k * (1 + math.log(2 * math.pi * r2 / k))


def _deviance(varpar, d):
    """The REML deviance (-2 log restricted likelihood) at theta and sigma (varpar = theta + [sigma])."""
    theta, sigma = varpar[:-1], varpar[-1]
    if sigma <= 0:
        return math.inf
    logdet, xhx, xhy, yhy = _parts(theta, d)
    sign, ldx = np.linalg.slogdet(xhx)
    if sign <= 0:
        return math.inf
    beta = np.linalg.solve(xhx, xhy)
    r2 = yhy - float(xhy @ beta)
    s2 = sigma * sigma
    return (d.n - d.p) * math.log(2 * math.pi * s2) + logdet + ldx + r2 / s2


def _vcov(varpar, d):
    theta, sigma = varpar[:-1], varpar[-1]
    _, xhx, _, _ = _parts(theta, d)
    return sigma * sigma * np.linalg.inv(xhx)


def _nelder_mead(f, x0, step=0.5, tol=1e-10, max_iter=4000):
    n = len(x0)
    pts = [np.asarray(x0, dtype=float)]
    for i in range(n):
        p = pts[0].copy()
        p[i] += step if p[i] == 0 else step * max(abs(p[i]), 0.1)
        pts.append(p)
    vals = [f(p) for p in pts]
    for _ in range(max_iter):
        order = np.argsort(vals)
        pts, vals = [pts[i] for i in order], [vals[i] for i in order]
        if abs(vals[-1] - vals[0]) <= tol * (abs(vals[0]) + tol) and max(np.max(np.abs(p - pts[0])) for p in pts) < 1e-8:
            break
        centre = np.mean(pts[:-1], axis=0)
        xr = centre + (centre - pts[-1])
        fr = f(xr)
        if fr < vals[0]:
            xe = centre + 2 * (centre - pts[-1])
            fe = f(xe)
            pts[-1], vals[-1] = (xe, fe) if fe < fr else (xr, fr)
        elif fr < vals[-2]:
            pts[-1], vals[-1] = xr, fr
        else:
            xc = centre + 0.5 * (pts[-1] - centre) if fr >= vals[-1] else centre + 0.5 * (xr - centre)
            fc = f(xc)
            if fc < min(fr, vals[-1]):
                pts[-1], vals[-1] = xc, fc
            else:
                pts = [pts[0] + 0.5 * (p - pts[0]) for p in pts]
                vals = [f(p) for p in pts]
    i = int(np.argmin(vals))
    return pts[i], vals[i]


def _canonical(theta, q):
    """The relative factor with a non-negative diagonal (the likelihood does not see the sign of a column)."""
    L = _lower(theta, q)
    for j in range(q):
        if L[j, j] < 0:
            L[:, j] = -L[:, j]
    return L[np.tril_indices(q)]


def fit(y, X, groups, names, Z=None, z_names=("(Intercept)",)):
    """REML fit of y on the columns of X (with an intercept column first), random effects Z (default: a random
    intercept) per group. Returns {"n", "groups", "fixed": [{"name", "est", "se", "df", "t", "p"}], "sigma",
    "G": q x q covariance of the random effects, "theta", "reml", "converged", "singular"}."""
    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    groups = np.asarray(groups)
    Z = np.ones((len(y), 1)) if Z is None else np.asarray(Z, dtype=float)
    keep = np.isfinite(y) & np.all(np.isfinite(X), axis=1) & np.all(np.isfinite(Z), axis=1)
    y, X, Z, groups = y[keep], X[keep], Z[keep], groups[keep]
    _, groups = np.unique(groups, return_inverse=True)
    if len(y) <= X.shape[1] + 2 or groups.max() < 2:
        raise MixedError("data")
    d = _Data(y, X, Z, groups)
    q = d.q
    start = np.eye(q)[np.tril_indices(q)]
    theta, crit = _nelder_mead(lambda t: _profiled(t, d), start)
    # a restart from the optimum (Askurious default: Nelder-Mead can stop early on a flat deviance)
    theta, crit = _nelder_mead(lambda t: _profiled(t, d), theta, step=0.1)
    theta = _canonical(theta, q)
    logdet, xhx, xhy, yhy = _parts(theta, d)
    beta = np.linalg.solve(xhx, xhy)
    r2 = yhy - float(xhy @ beta)
    sigma = math.sqrt(r2 / (d.n - d.p))
    varpar = np.concatenate([theta, [sigma]])
    vcov = _vcov(varpar, d)
    L = _lower(theta, q)
    G = sigma * sigma * L @ L.T
    singular = bool(np.any(np.abs(np.diag(L)) < 1e-4))
    dfs = _satterthwaite(varpar, d, singular)
    fixed = []
    for k, name in enumerate(names):
        se = math.sqrt(max(vcov[k, k], 0.0))
        t = beta[k] / se if se > 0 else None
        df = dfs[k]
        fixed.append({"name": name, "est": float(beta[k]), "se": se, "df": df, "t": t,
                      "p": _t_p(t, df) if t is not None and df else None})
    return {"n": int(d.n), "groups": int(d.m), "fixed": fixed, "sigma": sigma, "G": G.tolist(), "theta": theta.tolist(),
            "reml": float(crit), "singular": singular, "converged": bool(np.isfinite(crit)), "z_names": list(z_names)}


def _satterthwaite(varpar, d, singular):
    """The Satterthwaite degrees of freedom of each fixed effect (Satterthwaite, 1946;
    Kuznetsova et al., 2017)."""
    k = len(varpar)
    steps = np.array([1e-4 * max(1.0, abs(v)) for v in varpar])
    # the Hessian of the REML deviance in (theta, sigma) by central differences
    H = np.zeros((k, k))
    f0 = _deviance(varpar, d)
    for i in range(k):
        for j in range(i, k):
            if i == j:
                up, down = varpar.copy(), varpar.copy()
                up[i] += steps[i]
                down[i] -= steps[i]
                H[i, i] = (_deviance(up, d) - 2 * f0 + _deviance(down, d)) / steps[i] ** 2
            else:
                vals = []
                for si, sj in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
                    v = varpar.copy()
                    v[i] += si * steps[i]
                    v[j] += sj * steps[j]
                    vals.append(_deviance(v, d))
                H[i, j] = H[j, i] = (vals[0] - vals[1] - vals[2] + vals[3]) / (4 * steps[i] * steps[j])
    try:
        A = 2 * np.linalg.inv(H)
    except np.linalg.LinAlgError:
        A = 2 * np.linalg.pinv(H)
    p = d.p
    grads = np.zeros((p, k))
    for i in range(k):
        up, down = varpar.copy(), varpar.copy()
        up[i] += steps[i]
        down[i] -= steps[i]
        grads[:, i] = (np.diag(_vcov(up, d)) - np.diag(_vcov(down, d))) / (2 * steps[i])
    V = np.diag(_vcov(varpar, d))
    out = []
    for j in range(p):
        g = grads[j]
        denom = float(g @ A @ g)
        out.append(float(2 * V[j] ** 2 / denom) if denom > 0 else None)
    return out


def _t_p(t, df):
    """Two-sided t probability, using the shared beta integral (scales._betainc).

    I_{df / (df + t²)}(df/2, 1/2); no separate continued-fraction implementation here.
    """
    x = df / (df + t * t)
    return float(_betainc(df / 2, 0.5, x))


def icc(y, groups):
    """ICC(1) of y from the model with a random intercept only: tau² / (tau² + sigma²)."""
    y = np.asarray(y, dtype=float)
    res = fit(y, np.ones((len(y), 1)), groups, ["(Intercept)"])
    tau2 = res["G"][0][0]
    return tau2 / (tau2 + res["sigma"] ** 2)


def decompose(x, groups):
    """(within, between): x minus the mean of its person (CWC), the mean of the person; NaN stays NaN."""
    x = np.asarray(x, dtype=float)
    groups = np.asarray(groups)
    within, between = np.full(len(x), np.nan), np.full(len(x), np.nan)
    for g in np.unique(groups):
        idx = np.flatnonzero(groups == g)
        vals = x[idx]
        ok = np.isfinite(vals)
        if ok.any():
            m = vals[ok].mean()
            between[idx] = m
            within[idx[ok]] = vals[ok] - m
    return within, between
