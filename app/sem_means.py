"""Covariance-based models with a mean structure (SEM extensions 2026-10-17, point 4): the same model in several groups
with intercepts of the statements and means of the constructs, for the test of measurement invariance (configural,
metric, scalar, strict; Meredith, 1993; Vandenberg & Lance, 2000) and the comparison of latent means. Numbers only,
like app/sem.py, whose RAM form (_Ram) it extends: mu = F (I - A)^-1 m with m the intercepts of the observed
variables and the means of the constructs.

Estimation: maximum likelihood over all groups, F = sum_g n_g / N (log|Sigma_g| + tr(S_g Sigma_g^-1) - log|S_g| - p
+ (xbar_g - mu_g)' Sigma_g^-1 (xbar_g - mu_g)), Fisher scoring with the expected information tr(Sigma^-1 dSigma
Sigma^-1 dSigma) + 2 dmu' Sigma^-1 dmu, columns in units of their SD over all groups (as sem.fit_ml_groups). The
first indicator of each construct has the loading 1 (marker); the means of the constructs are 0 in the first group
and, from the scalar level on, free in the others (Meredith, 1993; the usual identification of the latent means).
Fit for several groups: CFI and TLI against the model of free variances and means in every group, RMSEA
with sqrt(G) (Steiger, 1998), SRMR the mean of the groups' SRMR weighted by their size (covariances only). Robust:
the Satorra-Bentler scaling factor with the first and second moments (dense, up to MAX_ROBUST statements), and the
scaled difference test between two levels (Satorra & Bentler, 2001)."""
import math

import numpy as np

from app import sem

LEVELS = ("configural", "metric", "scalar", "strict")
MAX_ROBUST = 30  # statements up to which the scaling factor is computed (dense moments of p + p(p+1)/2 values)

MEREDITH = sem.MEREDITH
VANDENBERG = ("Vandenberg, R. J., & Lance, C. E. (2000). A review and synthesis of the measurement invariance literature: "
              "Suggestions, practices, and recommendations for organizational research. Organizational Research Methods, "
              "3(1), 4–70. https://doi.org/10.1177/109442810031002")
CHEN = ("Chen, F. F. (2007). Sensitivity of goodness of fit indexes to lack of measurement invariance. Structural "
        "Equation Modeling, 14(3), 464–504. https://doi.org/10.1080/10705510701301834")
SATORRA_BENTLER_2001 = ("Satorra, A., & Bentler, P. M. (2001). A scaled difference chi-square test statistic for moment "
                        "structure analysis. Psychometrika, 66(4), 507–514. https://doi.org/10.1007/BF02296192")
STEIGER_1998 = ("Steiger, J. H. (1998). A note on multiple sample extensions of the RMSEA fit index. Structural Equation "
                "Modeling, 5(4), 411–419. https://doi.org/10.1080/10705519809540115")
SOURCES = (MEREDITH, VANDENBERG, CHEN, SATORRA_BENTLER_2001, STEIGER_1998)


class _Groups:
    """The parameters of the model in G groups at one level of invariance: per group the free parameters of the
    covariance part (sem._Ram) and the mean part (intercepts of the observed variables, means of the constructs),
    mapped to one joint vector; a shared parameter has one place for all groups, a fixed mean the place -1."""

    def __init__(self, spec, cols, groups, level):
        self.ram = ram = sem._Ram(spec, cols)
        self.p, self.m, self.G = ram.p, ram.m, groups
        rank = LEVELS.index(level)
        indicators = {i for items in spec["latent"].values() for i in items}
        # the mean part of one group: an intercept per observed variable, a mean per construct
        self.mean_labels = [("intercept", c) for c in ram.manifest] + [("mean", lv) for lv in ram.latent]
        shared_cov = {k for k, (_, _, _, label) in enumerate(ram.free)
                      if (label[0] == "loading" and rank >= 1) or (label[0] == "residual" and rank >= 3)}
        shared_mean = {k for k, (kind, name) in enumerate(self.mean_labels)
                       if kind == "intercept" and name in indicators and rank >= 2}
        self.index = []  # per group: [joint place of each covariance parameter] + [of each mean parameter, -1 fixed]
        joint = 0
        first = {}
        for g in range(groups):
            cov_idx = []
            for k in range(ram.q):
                if k in shared_cov and g:
                    cov_idx.append(first[("c", k)])
                else:
                    first.setdefault(("c", k), joint)
                    cov_idx.append(joint)
                    joint += 1
            mean_idx = []
            for k, (kind, name) in enumerate(self.mean_labels):
                if kind == "mean" and (g == 0 or rank < 2):  # 0 in the first group; free in the others from scalar on
                    mean_idx.append(-1)
                elif k in shared_mean and g:
                    mean_idx.append(first[("m", k)])
                else:
                    first.setdefault(("m", k), joint)
                    mean_idx.append(joint)
                    joint += 1
            self.index.append((np.array(cov_idx), np.array(mean_idx)))
        self.Q = joint

    def local(self, theta, g):
        cov_idx, mean_idx = self.index[g]
        means = np.where(mean_idx >= 0, theta[np.maximum(mean_idx, 0)], 0.0)
        return theta[cov_idx], means

    def implied(self, theta, g):
        """(Sigma, mu, B, the means of all variables) of group g."""
        tc, tm = self.local(theta, g)
        sigma, B, _ = self.ram.implied(tc)
        m = np.zeros(self.m)
        m[:] = tm  # the manifest intercepts and the latent means, in the order of ram.names
        mu_all = B @ m
        return sigma, mu_all[:self.p], B, mu_all

    def derivatives(self, theta, g):
        """Sigma, mu and the stacks dSigma (Q, p, p), dmu (Q, p) of group g with respect to the joint vector."""
        tc, tm = self.local(theta, g)
        sigma, d_cov = self.ram.derivatives(tc)
        _, mu, B, mu_all = self.implied(theta, g)
        FB = B[:self.p]
        cov_idx, mean_idx = self.index[g]
        dS = np.zeros((self.Q, self.p, self.p))
        dm = np.zeros((self.Q, self.p))
        for k, (kind, i, j, _) in enumerate(self.ram.free):
            dS[cov_idx[k]] += d_cov[k]
            if kind == "A":
                dm[cov_idx[k]] += FB[:, i] * mu_all[j]
        for k, place in enumerate(mean_idx):
            if place >= 0:
                dm[place] += FB[:, k]
        return sigma, mu, dS, dm


def _f_group(sigma, mu, s, xbar, logdet_s):
    sign, logdet = np.linalg.slogdet(sigma)
    if sign <= 0:
        return math.inf
    inv = np.linalg.inv(sigma)
    e = xbar - mu
    return logdet + float(np.sum(s * inv)) - logdet_s - len(mu) + float(e @ inv @ e)


def fit_groups(xs, cols, spec, level="configural", robust=True):
    """The model in the groups xs (complete cases, columns cols) at one level of invariance. Returns {"converged",
    "level", "n": [per group], "chi2", "df", "p", "cfi", "tli", "rmsea", "rmsea_ci", "srmr", "scaled": {"c", "chi2",
    "p"} | None, "latent_means": {construct: [{"est", "se", "z", "p", "d"} per group but the first]}, "problems"}."""
    spec = sem.check(spec)
    xs = [np.asarray(x, dtype=float) for x in xs]
    if len(xs) < 2 or any(len(x) < 3 or x.shape[1] != len(cols) for x in xs):
        raise sem.SemError("data")
    pooled = np.vstack([x - x.mean(axis=0) for x in xs])
    unit = pooled.std(axis=0)
    if np.any(unit <= 0):
        raise sem.SemError("constant")
    xs = [x / unit for x in xs]
    G = len(xs)
    model = _Groups(spec, cols, G, level)
    p, Q = model.p, model.Q
    ns = np.array([len(x) for x in xs], dtype=float)
    N = ns.sum()
    w = ns / N
    ss = [np.cov(x, rowvar=False, bias=True) for x in xs]
    means = [x.mean(axis=0) for x in xs]
    logdets = []
    for s in ss:
        sign, logdet = np.linalg.slogdet(s)
        if sign <= 0:
            raise sem.SemError("singular")
        logdets.append(logdet)
    # start: the covariance part of each group's own start, the intercepts the group means
    theta = np.zeros(Q)
    for g in range(G - 1, -1, -1):  # the first group's values win where a parameter is shared
        cov_idx, mean_idx = model.index[g]
        theta[cov_idx] = model.ram.start(ss[g])
        for k, (kind, name) in enumerate(model.mean_labels):
            if mean_idx[k] >= 0 and kind == "intercept":
                theta[mean_idx[k]] = means[g][model.ram.manifest.index(name)]

    def total(t):
        f = 0.0
        for g in range(G):
            sigma, mu, _, _ = model.implied(t, g)
            fg = _f_group(sigma, mu, ss[g], means[g], logdets[g])
            if not math.isfinite(fg):
                return math.inf
            f += w[g] * fg
        return f

    f = total(theta)
    converged = False
    for _ in range(sem.MAX_ITER):
        grad, info = np.zeros(Q), np.zeros((Q, Q))
        for g in range(G):
            sigma, mu, dS, dm = model.derivatives(theta, g)
            inv = np.linalg.inv(sigma)
            e = means[g] - mu
            resid = sigma - ss[g] - np.outer(e, e)
            mS = inv[None] @ dS
            grad += w[g] * (np.einsum("ij,kji->k", inv @ resid, mS) - 2 * dm @ (inv @ e))
            info += w[g] * (sem._trace_products(mS) + 2 * dm @ inv @ dm.T)
        try:
            step = -np.linalg.solve(info, grad)
        except np.linalg.LinAlgError:
            step = -np.linalg.lstsq(info, grad, rcond=None)[0]
        if np.all(np.abs(step) < sem.TOL * (1 + np.abs(theta))):
            converged = True
            break
        t = 1.0
        for _ in range(40):
            new = theta + t * step
            f_new = total(new)
            if f_new <= f + 1e-12:
                break
            t /= 2
        else:
            break
        theta, f = new, f_new
        if np.all(np.abs(t * step) < sem.TOL * (1 + np.abs(theta))):
            converged = True
            break
    if not converged:
        return {"converged": False, "level": level, "problems": ["converge"]}
    chi2 = float(N * f)
    df = G * (p * (p + 1) // 2 + p) - Q
    out = {"converged": True, "level": level, "n": [int(n) for n in ns], "chi2": chi2, "df": df,
           "p": sem._chi2_p(chi2, df) if df > 0 else None, "q": Q}
    # fit: the baseline with free variances and means in every group (independence model per group)
    base = float(sum(n * (np.sum(np.log(np.diag(s))) - ld) for n, s, ld in zip(ns, ss, logdets)))
    base_df = G * p * (p - 1) // 2
    if df > 0:
        d_model, d_base = max(chi2 - df, 0.0), max(base - base_df, 0.0)
        out["cfi"] = 1.0 if max(d_model, d_base) == 0 else 1 - d_model / max(d_model, d_base)
        out["tli"] = (base / base_df - chi2 / df) / (base / base_df - 1) if base / base_df > 1 else None
        out["rmsea"] = math.sqrt(max(chi2 - df, 0.0) / (N * df)) * math.sqrt(G)
        lo, hi = sem.rmsea_ci(chi2, df, N)
        out["rmsea_ci"] = (lo * math.sqrt(G), hi * math.sqrt(G)) if lo is not None else (None, None)
    srmr = 0.0
    info = np.zeros((Q, Q))
    for g in range(G):
        sigma, mu, dS, dm = model.derivatives(theta, g)
        sd = np.sqrt(np.diag(ss[g]))
        resid = (ss[g] - sigma) / np.outer(sd, sd)
        rows, cols_ = np.tril_indices(p)
        # with the mean structure the residual means count too (SRMR with means; checked against reference values)
        mean_resid = (means[g] - mu) / sd
        srmr += w[g] * math.sqrt(float((np.sum(resid[rows, cols_] ** 2) + np.sum(mean_resid ** 2)) / (len(rows) + p)))
        inv = np.linalg.inv(sigma)
        mS = inv[None] @ dS
        info += ns[g] * (sem._trace_products(mS) / 2 + dm @ inv @ dm.T)
    out["srmr"] = srmr
    problems = []
    if not sem._identified(info):
        problems.append("identified")
        vcov = np.linalg.pinv(info)
    else:
        vcov = np.linalg.inv(info)
    out["scaled"] = None
    if robust and p <= MAX_ROBUST:
        c, robust_vcov = _scaling(model, theta, xs, ns, info, df)
        if robust_vcov is not None:  # robust (sandwich) errors (Satorra & Bentler, 1994)
            vcov = robust_vcov
        if df > 0 and c and c > 0:
            out["scaled"] = {"c": c, "chi2": chi2 / c, "p": sem._chi2_p(chi2 / c, df)}
    # the means of the constructs in the groups after the first, in the units of their marker and in SD of the first
    # group (a standardised difference)
    out["latent_means"] = {}
    for lv in model.ram.latent:
        k = model.mean_labels.index(("mean", lv))
        found = []
        _, _, full0 = model.ram.implied(model.local(theta, 0)[0])
        sd0 = math.sqrt(max(full0[model.ram.index[lv], model.ram.index[lv]], 1e-300))
        for g in range(1, G):
            place = model.index[g][1][k]
            if place < 0:
                continue
            est, se = float(theta[place]), math.sqrt(max(vcov[place, place], 0.0))
            z = est / se if se > 0 else None
            back = float(unit[model.ram.index[spec["latent"][lv][0]]])  # in the units of the marker statement
            found.append({"est": est * back, "se": se * back, "z": z, "p": sem._normal_p(z) if z is not None else None,
                          "d": est / sd0})
        if found:
            out["latent_means"][lv] = found
    for g in range(G):
        tc, _ = model.local(theta, g)
        problems += sem._inadmissible(model.ram, tc, sem._standardised(model.ram, tc))
    out["problems"] = sorted(set(problems))
    out["admissible"] = not out["problems"]
    return out


def _scaling(model, theta, xs, ns, info, df):
    """The Satorra-Bentler scaling factor with the first and second moments, over the groups: c = tr(U Gamma) / df
    with U = W - W Delta (Delta' W Delta)^-1 Delta' W per group, weighted by the group's share (dense matrices); and
    the robust covariance of the parameters info^-1 (sum_g n_g Delta' W Gamma W Delta) info^-1. (c | None, vcov)."""
    p = model.p
    rows, cols = np.tril_indices(p)
    dup = np.zeros((p * p, len(rows)))
    for k, (i, j) in enumerate(zip(rows, cols)):
        dup[i * p + j, k] = dup[j * p + i, k] = 1
    N = ns.sum()
    trace = 0.0
    big_w, big_d, gammas = [], [], []
    for g, x in enumerate(xs):
        sigma, mu, dS, dm = model.derivatives(theta, g)
        inv = np.linalg.inv(sigma)
        w = np.zeros((p + len(rows), p + len(rows)))
        w[:p, :p] = inv
        w[p:, p:] = 0.5 * dup.T @ np.kron(inv, inv) @ dup
        delta = np.vstack([dm.T, np.stack([d[rows, cols] for d in dS], axis=1)])
        c = x - x.mean(axis=0)
        moments = np.hstack([c, c[:, rows] * c[:, cols]])
        gamma = np.cov(moments, rowvar=False, bias=True)
        big_w.append(w)
        big_d.append(delta)
        gammas.append(gamma)
    # (Delta' W Delta) over all groups, weighted by n_g / N: the information per person
    a = sum((ns[g] / N) * big_d[g].T @ big_w[g] @ big_d[g] for g in range(len(xs)))
    try:
        a_inv = np.linalg.inv(a)
    except np.linalg.LinAlgError:
        a_inv = np.linalg.pinv(a)
    meat = np.zeros_like(info)
    for g in range(len(xs)):
        wg, dg = big_w[g], big_d[g]
        share = ns[g] / N
        # block g of U_total Gamma_total with W_total = diag(share W_g), Gamma_total = diag(Gamma_g / share)
        u = wg - share * wg @ dg @ a_inv @ dg.T @ wg
        trace += float(np.sum(u * gammas[g].T))
        wd = wg @ dg
        meat += ns[g] * wd.T @ gammas[g] @ wd
    try:
        bread = np.linalg.inv(info)
    except np.linalg.LinAlgError:
        bread = np.linalg.pinv(info)
    return (trace / df if df > 0 else None), bread @ meat @ bread


def scaled_difference(fit0, fit1):
    """The scaled difference test of two nested levels (fit0 the more restricted; Satorra & Bentler, 2001):
    {"chi2", "df", "p", "scaled": bool}. Without scaling factors the plain ML difference."""
    ddf = fit0["df"] - fit1["df"]
    if ddf <= 0:
        return None
    t = fit0["chi2"] - fit1["chi2"]
    s0, s1 = fit0.get("scaled"), fit1.get("scaled")
    if s0 and s1:
        cd = (fit0["df"] * s0["c"] - fit1["df"] * s1["c"]) / ddf
        if cd > 0:
            return {"chi2": max(t / cd, 0.0), "df": ddf, "p": sem._chi2_p(max(t / cd, 0.0), ddf), "scaled": True}
    return {"chi2": max(t, 0.0), "df": ddf, "p": sem._chi2_p(max(t, 0.0), ddf), "scaled": False}


# Chen (2007): a level does not hold when the CFI drops by .010 or more together with a rise of the RMSEA by .015 or
# more or of the SRMR by .030 (metric) or .010 (scalar, strict); N > 300. Orientation, not a test.
D_CFI, D_RMSEA = -0.010, 0.015
D_SRMR = {"metric": 0.030, "scalar": 0.010, "strict": 0.010}


def invariance(xs, cols, spec, robust=True):
    """The four levels in a row: [{"level", "fit", "diff": scaled_difference against the level before, "d_cfi",
    "d_rmsea", "d_srmr", "holds": bool | None}] and "highest": the highest level that holds in a row (configural
    when the model fits in every group at all)."""
    out = []
    before = None
    highest = None
    for level in LEVELS:
        try:
            fit = fit_groups(xs, cols, spec, level, robust)
        except (sem.SemError, np.linalg.LinAlgError, ValueError, ArithmeticError):
            fit = {"converged": False, "level": level, "problems": ["estimate"]}
        row = {"level": level, "fit": fit}
        if before is not None and fit.get("converged") and before["fit"].get("converged"):
            prev = before["fit"]
            row["diff"] = scaled_difference(fit, prev)
            row["d_cfi"] = (fit.get("cfi") or 0) - (prev.get("cfi") or 0) if fit.get("cfi") is not None else None
            row["d_rmsea"] = (fit.get("rmsea") or 0) - (prev.get("rmsea") or 0) if fit.get("rmsea") is not None else None
            row["d_srmr"] = fit["srmr"] - prev["srmr"]
            # not held: the CFI drops by .010 or more AND the RMSEA or the SRMR rise beyond their bound (Chen, 2007)
            worse = row["d_cfi"] is not None and row["d_cfi"] <= D_CFI
            row["holds"] = not (worse and ((row["d_rmsea"] or 0) >= D_RMSEA or row["d_srmr"] >= D_SRMR[level]))
        elif level == "configural":
            row["holds"] = bool(fit.get("converged") and fit.get("admissible"))
        else:
            row["holds"] = None
        if row["holds"] and (highest == LEVELS[LEVELS.index(level) - 1] if level != "configural" else True):
            highest = level
        out.append(row)
        before = row
    return {"levels": out, "highest": highest}


# --- full information maximum likelihood (SEM extensions 2026-10-17, point 2) ------------------------------------

ENDERS = ("Enders, C. K., & Bandalos, D. L. (2001). The relative performance of full information maximum likelihood "
          "estimation for missing data in structural equation models. Structural Equation Modeling, 8(3), 430–457. "
          "https://doi.org/10.1207/S15328007SEM0803_5")
YUAN_BENTLER = ("Yuan, K.-H., & Bentler, P. M. (2000). Three likelihood-based methods for mean and covariance structure "
                "analysis with nonnormal missing data. Sociological Methodology, 30(1), 165–200. "
                "https://doi.org/10.1111/0081-1750.00078")
FIML_SOURCES = (ENDERS, YUAN_BENTLER)


def _patterns(x):
    """The patterns of missing values of x (NaN = missing): [{"obs", "rows", "n", "mean", "cov", "x"}], cases without
    any value left out."""
    mask = ~np.isnan(x)
    found = {}
    for i, row in enumerate(mask):
        if row.any():
            found.setdefault(row.tobytes(), []).append(i)
    out = []
    for idx in found.values():
        obs = np.where(mask[idx[0]])[0]
        xo = x[np.ix_(idx, obs)]
        mean = xo.mean(axis=0)
        c = xo - mean
        out.append({"obs": obs, "rows": np.array(idx), "n": len(idx), "mean": mean, "cov": c.T @ c / len(idx), "x": xo})
    return out


def _f_patterns(pats, sigma, mu, N):
    """-2 log L / N without constants: sum_k n_k / N (log|Sigma_oo| + tr(Sigma_oo^-1 S_k) + e_k' Sigma_oo^-1 e_k)."""
    f = 0.0
    for pt in pats:
        o = pt["obs"]
        so = sigma[np.ix_(o, o)]
        sign, logdet = np.linalg.slogdet(so)
        if sign <= 0:
            return math.inf
        inv = np.linalg.inv(so)
        e = pt["mean"] - mu[o]
        f += pt["n"] / N * (logdet + float(np.sum(pt["cov"] * inv)) + float(e @ inv @ e))
    return f


def _grad_info(pats, sigma, mu, dS, dm, N):
    """Gradient of the F above and its expected information (per person), from the stacks dSigma (Q, p, p), dmu (Q, p)."""
    Q = len(dS)
    grad, info = np.zeros(Q), np.zeros((Q, Q))
    for pt in pats:
        o = pt["obs"]
        so = sigma[np.ix_(o, o)]
        inv = np.linalg.inv(so)
        d_o = dS[:, o][:, :, o]
        m_o = dm[:, o]
        e = pt["mean"] - mu[o]
        resid = so - pt["cov"] - np.outer(e, e)
        mS = inv[None] @ d_o
        w = pt["n"] / N
        grad += w * (np.einsum("ij,kji->k", inv @ resid, mS) - 2 * m_o @ (inv @ e))
        info += w * (sem._trace_products(mS) + 2 * m_o @ inv @ m_o.T)
    return grad, info


def _scores(pats, sigma, mu, dS, dm, N):
    """The derivatives of each case's log-likelihood (N, Q) and the expected information per person (Q, Q)."""
    Q = len(dS)
    scores = np.zeros((N, Q))
    info = np.zeros((Q, Q))
    for pt in pats:
        o = pt["obs"]
        so = sigma[np.ix_(o, o)]
        inv = np.linalg.inv(so)
        d_o = dS[:, o][:, :, o]
        m_o = dm[:, o]
        a = (pt["x"] - mu[o]) @ inv  # Sigma_oo^-1 (x - mu) of each case
        trace = np.einsum("ij,kji->k", inv, d_o)
        quad = np.einsum("ni,kij,nj->nk", a, d_o, a)
        scores[pt["rows"]] = -0.5 * trace[None, :] + 0.5 * quad + a @ m_o.T
        mS = inv[None] @ d_o
        info += pt["n"] / N * (sem._trace_products(mS) / 2 + m_o @ inv @ m_o.T)
    return scores, info


def _observed_info(make, theta, pats, N):
    """The observed information per person, 1/2 the Hessian of F (= -2 log L / N), by central differences of the
    analytic gradient; make(theta) gives (sigma, mu, dS, dm). The robust errors with missing values use it (Yuan &
    Bentler, 2000)."""
    Q = len(theta)
    out = np.zeros((Q, Q))
    for k in range(Q):
        h = 1e-5 * max(1.0, abs(theta[k]))
        up, down = theta.copy(), theta.copy()
        up[k] += h
        down[k] -= h
        out[:, k] = (_grad_info(pats, *make(up), N)[0] - _grad_info(pats, *make(down), N)[0]) / (2 * h)
    return 0.5 * (out + out.T) / 2


def saturated(x, max_iter=2000, tol=1e-10):
    """The unrestricted means and covariances of x with missing values by EM: (mu, sigma, converged)."""
    n, p = x.shape
    mu = np.nanmean(x, axis=0)
    sigma = np.diag(np.nanvar(x, axis=0))
    pats = _patterns(x)
    n = sum(pt["n"] for pt in pats)
    for _ in range(max_iter):
        t1 = np.zeros(p)
        t2 = np.zeros((p, p))
        for pt in pats:
            o = pt["obs"]
            m = np.setdiff1d(np.arange(p), o)
            xo = pt["x"]
            full = np.empty((pt["n"], p))
            full[:, o] = xo
            extra = np.zeros((p, p))
            if len(m):
                b = sigma[np.ix_(m, o)] @ np.linalg.inv(sigma[np.ix_(o, o)])
                full[:, m] = mu[m] + (xo - mu[o]) @ b.T
                extra[np.ix_(m, m)] = sigma[np.ix_(m, m)] - b @ sigma[np.ix_(o, m)]
            t1 += full.sum(axis=0)
            t2 += full.T @ full + pt["n"] * extra
        new_mu = t1 / n
        new_sigma = t2 / n - np.outer(new_mu, new_mu)
        change = max(float(np.max(np.abs(new_mu - mu))), float(np.max(np.abs(new_sigma - sigma))))
        mu, sigma = new_mu, new_sigma
        if change < tol:
            return mu, sigma, True
    return mu, sigma, False


def _saturated_stacks(p):
    """dSigma and dmu of the unrestricted model (the means, then the lower triangle of Sigma)."""
    rows, cols = np.tril_indices(p)
    Q = p + len(rows)
    dS = np.zeros((Q, p, p))
    dm = np.zeros((Q, p))
    for k in range(p):
        dm[k, k] = 1.0
    for k, (i, j) in enumerate(zip(rows, cols)):
        dS[p + k, i, j] = dS[p + k, j, i] = 1.0
    return dS, dm


def fit_fiml(x, cols, spec, light=False, start=None):
    """The covariance-based model on all cases with at least one value in its columns (NaN = missing), by full
    information maximum likelihood (Enders & Bandalos, 2001), robust as MLR: sandwich errors from the casewise
    derivatives and the test scaled after Yuan and Bentler (2000), c = (tr(A1^-1 B1) - tr(A0^-1 B0)) / df with A the
    information and B the cross-products of the cases' derivatives of the unrestricted (1) and the model (0). Returns
    what sem.fit_ml returns, with "n" all cases used, "n_complete" and "missing": "fiml"."""
    spec = sem.check(spec)
    x = np.asarray(x, dtype=float)
    x = x[~np.all(np.isnan(x), axis=1)]
    N, p = x.shape
    if N < 3 or p != len(cols):
        raise sem.SemError("data")
    with np.errstate(all="ignore"):
        unit = np.nanstd(x, axis=0)
    if np.any(~np.isfinite(unit)) or np.any(unit <= 0):
        raise sem.SemError("constant")
    x = x / unit
    model = _Groups(spec, cols, 1, "configural")
    ram, q, Q = model.ram, model.ram.q, model.Q
    factor = ram.factors(unit)
    pats = _patterns(x)
    if light and start is not None:  # a draw of the bootstrap: from the estimate of the whole sample, no EM needed
        mu1, sigma1 = np.nanmean(x, axis=0), None
    else:
        mu1, sigma1, _ = saturated(x)
        if np.linalg.slogdet(sigma1)[0] <= 0:
            raise sem.SemError("singular")
    theta = np.zeros(Q)
    cov_idx, mean_idx = model.index[0]
    theta[cov_idx] = ram.start(sigma1) if start is None else np.asarray(start, dtype=float)[:q] / factor
    for k, (kind, name) in enumerate(model.mean_labels):
        if mean_idx[k] >= 0:
            theta[mean_idx[k]] = mu1[ram.manifest.index(name)]

    def total(t):
        sigma, mu, _, _ = model.implied(t, 0)
        return _f_patterns(pats, sigma, mu, N)

    f = total(theta)
    converged = False
    for _ in range(sem.MAX_ITER):
        sigma, mu, dS, dm = model.derivatives(theta, 0)
        grad, info = _grad_info(pats, sigma, mu, dS, dm, N)
        try:
            step = -np.linalg.solve(info, grad)
        except np.linalg.LinAlgError:
            step = -np.linalg.lstsq(info, grad, rcond=None)[0]
        if np.all(np.abs(step) < sem.TOL * (1 + np.abs(theta))):
            converged = True
            break
        t = 1.0
        for _ in range(40):
            new = theta + t * step
            f_new = total(new)
            if f_new <= f + 1e-12:
                break
            t /= 2
        else:
            break
        theta, f = new, f_new
        if np.all(np.abs(t * step) < sem.TOL * (1 + np.abs(theta))):
            converged = True
            break
    tc = theta[cov_idx]
    if not converged:
        return {"method": "ml", "converged": False, "n": N, "theta": (tc * factor).tolist(), "problems": ["converge"]}
    std = sem._standardised(ram, tc)
    if light:
        return {"method": "ml", "converged": True, "n": N, "theta": (tc * factor).tolist(), "std": std,
                "admissible": not sem._inadmissible(ram, tc, std)}
    sigma, mu, dS, dm = model.derivatives(theta, 0)
    scores0, _ = _scores(pats, sigma, mu, dS, dm, N)
    a0 = _observed_info(lambda t: model.derivatives(t, 0), theta, pats, N)
    problems = []
    if not sem._identified(a0):
        problems.append("identified")
        a0_inv = np.linalg.pinv(a0)
    else:
        a0_inv = np.linalg.inv(a0)
    b0 = scores0.T @ scores0 / N
    vcov = a0_inv @ b0 @ a0_inv / N
    f1 = _f_patterns(pats, sigma1, mu1, N)
    chi2 = float(N * (f - f1))
    df = p * (p + 1) // 2 + p - Q
    out = {"method": "ml", "robust": True, "missing": "fiml", "converged": True, "n": N,
           "n_complete": int(np.sum(~np.any(np.isnan(x), axis=1))), "p_vars": p, "q": q, "df": df, "chi2": chi2,
           "p": sem._chi2_p(chi2, df) if df > 0 else None, "theta": (tc * factor).tolist(), "scaled": None}
    if df > 0:
        dS1, dm1 = _saturated_stacks(p)
        scores1, _ = _scores(pats, sigma1, mu1, dS1, dm1, N)
        rows1, cols1 = np.tril_indices(p)

        def make1(t):  # the unrestricted model from its vector: the means, then the lower triangle of Sigma
            sig = np.zeros((p, p))
            sig[rows1, cols1] = t[p:]
            sig = sig + np.tril(sig, -1).T
            return sig, t[:p], dS1, dm1

        a1 = _observed_info(make1, np.concatenate([mu1, sigma1[rows1, cols1]]), pats, N)
        b1 = scores1.T @ scores1 / N
        try:
            c = (float(np.trace(np.linalg.solve(a1, b1))) - float(np.trace(a0_inv @ b0))) / df
        except np.linalg.LinAlgError:
            c = None
        if c and c > 0:
            out["scaled"] = {"c": c, "chi2": chi2 / c, "p": sem._chi2_p(chi2 / c, df)}
    # fit against the independence model with FIML: means and variances free, no covariances
    sigma_b = np.diag(np.nanvar(x, axis=0))
    base_f = _f_patterns(pats, sigma_b, np.nanmean(x, axis=0), N)
    base_chi2 = float(N * (base_f - f1))
    base_df = p * (p - 1) // 2
    out["baseline"] = (base_chi2, base_df)
    if df > 0 and base_df > 0:
        d_model, d_base = max(chi2 - df, 0.0), max(base_chi2 - base_df, 0.0)
        out["cfi"] = 1.0 if max(d_model, d_base) == 0 else 1 - d_model / max(d_model, d_base)
        out["tli"] = (base_chi2 / base_df - chi2 / df) / (base_chi2 / base_df - 1) if base_chi2 / base_df > 1 else None
        out["rmsea"] = math.sqrt(max(chi2 - df, 0.0) / (df * N))
        out["rmsea_ci"] = sem.rmsea_ci(chi2, df, N)
    sd = np.sqrt(np.diag(sigma1))
    rows, cols_ = np.tril_indices(p)
    resid = (sigma1 - sigma) / np.outer(sd, sd)
    mean_resid = (mu1 - mu) / sd  # with the means: the SRMR of FIML counts the residual means too
    out["srmr"] = math.sqrt(float((np.sum(resid[rows, cols_] ** 2) + np.sum(mean_resid ** 2)) / (len(rows) + p)))
    out["residuals"] = sem._largest_residuals(sigma1, sigma, ram.manifest)
    corr = sigma1 / np.outer(sd, sd)  # the correlations of the unrestricted model stand in for the data's
    out.update(sem._tables(ram, spec, tc, vcov[np.ix_(cov_idx, cov_idx)], factor, unit, corr))
    problems += out.pop("inadmissible")
    out["problems"] = sorted(set(problems))
    out["admissible"] = not out["problems"]
    return out
