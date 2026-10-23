"""Covariance-based models written as a parameter table (longitudinal and multilevel concept 2026-10-17, package 1):
the models over the waves of a panel (invariance over time, latent growth curves, the cross-lagged panel models) need
what the constructs-and-paths form of app/sem.py cannot say: loadings with a fixed value other than the marker, the
same parameter in several places (equal loadings over time, equal cross-lagged paths), covariances between the
residuals of statements and the means in a single group. Numbers only, numpy like app/sem.py.

A model is a list of rows in the operators of the common SEM syntax (so that the R export reads the same model),
nothing added on its own: (op, lhs, rhs, value, label) with op "=~" (lhs measured by rhs), "~" (lhs regressed on rhs),
"~~" (variance or covariance; of the residual for a dependent variable) and "~1" (intercept or mean of lhs, rhs
None). value None means free, a number fixes the row at it; rows with the same label share one parameter (an
equality constraint). Every variable not among the columns is latent.

Estimation: full information maximum likelihood with the mean structure over all cases with at least one value
(NaN = missing; Enders & Bandalos, 2001), Fisher scoring with step halving in RAM form, mu = F (I - A)^-1 m; robust
(MLR: sandwich errors with the observed information, test scaled after Yuan & Bentler, 2000), the pieces
of app/sem_means.py. With complete data that is ML with means. The columns enter in units of a shared SD per group
of columns (`same`: the same statement in every wave), so that equal parameters stay equal; the values are given back
in the units of the data. Checked against independently computed reference values
(tests/analysis/test_sem_reference_values.py)."""
import math

import numpy as np

from app import sem, sem_means

OPS = ("=~", "~", "~~", "~1")


class TableError(sem.SemError):
    pass


def row(op, lhs, rhs=None, value=None, label=None):
    """One row of the table, as a tuple (op, lhs, rhs, value, label)."""
    if op not in OPS:
        raise TableError("unknown")
    return (op, lhs, rhs, None if value is None else float(value), label)


class Model:
    """The RAM form of a table: variables manifest (the columns) then latent; per row its cell ("A" j -> i, "S" i, j or
    "m" i) and the joint parameter it belongs to (-1 when fixed)."""

    def __init__(self, table, cols):
        self.table = [row(*r) if len(r) != 5 else (r[0], r[1], r[2], None if r[3] is None else float(r[3]), r[4])
                      for r in table]
        self.manifest = list(cols)
        latent = []
        for op, lhs, rhs, _, _ in self.table:
            for v in (lhs, rhs):
                if v is not None and v not in self.manifest and v not in latent:
                    latent.append(v)
        self.latent = latent
        self.names = self.manifest + latent
        self.index = {v: k for k, v in enumerate(self.names)}
        self.p, self.m = len(self.manifest), len(self.names)
        self.cells = []  # per row: (kind, i, j)
        self.place = []  # per row: the joint parameter, -1 fixed
        labels, Q = {}, 0
        seen = set()
        for op, lhs, rhs, value, label in self.table:
            if op == "=~":
                cell = ("A", self.index[rhs], self.index[lhs])
            elif op == "~":
                cell = ("A", self.index[lhs], self.index[rhs])
            elif op == "~~":
                i, j = self.index[lhs], self.index[rhs]
                cell = ("S", max(i, j), min(i, j))
            else:
                cell = ("m", self.index[lhs], self.index[lhs])
            if cell in seen:
                raise TableError("duplicate")
            seen.add(cell)
            self.cells.append(cell)
            if value is not None:
                self.place.append(-1)
            elif label is not None and label in labels:
                self.place.append(labels[label])
            else:
                if label is not None:
                    labels[label] = Q
                self.place.append(Q)
                Q += 1
        self.Q = Q
        self.place = np.array(self.place)
        self.labels = labels

    def unit_factors(self, unit):
        """Per row the factor from the scaled columns back to the units of the data: a latent variable has the unit
        of its first indicator (the first "=~" row); A[i, j] g_i / g_j, S[i, j] g_i g_j, m[i] g_i."""
        g = np.ones(self.m)
        g[:self.p] = unit
        done = set()
        for (op, lhs, rhs, _, _) in self.table:
            if op == "=~" and lhs not in done and rhs in self.index:
                done.add(lhs)
                g[self.index[lhs]] = g[self.index[rhs]] if self.index[rhs] < self.p or rhs in done else 1.0
        # latent variables measured only by other latent ones, in a second pass
        for (op, lhs, rhs, _, _) in self.table:
            if op == "=~" and self.index[rhs] >= self.p:
                g[self.index[lhs]] = g[self.index[rhs]]
        out = []
        for kind, i, j in self.cells:
            out.append(g[i] / g[j] if kind == "A" else g[i] * g[j] if kind == "S" else g[i])
        return np.array(out)

    def fixed(self, scale):
        """The fixed parts of A, S and m in the scaled units (scale: the factor of each row)."""
        A, S, m = np.zeros((self.m, self.m)), np.zeros((self.m, self.m)), np.zeros(self.m)
        for (op, _, _, value, _), (kind, i, j), f in zip(self.table, self.cells, scale):
            if value is None:
                continue
            v = value / f
            if kind == "A":
                A[i, j] = v
            elif kind == "S":
                S[i, j] = S[j, i] = v
            else:
                m[i] = v
        return A, S, m

    def matrices(self, theta):
        A, S, m = self.A0.copy(), self.S0.copy(), self.m0.copy()
        for (kind, i, j), k in zip(self.cells, self.place):
            if k < 0:
                continue
            if kind == "A":
                A[i, j] = theta[k]
            elif kind == "S":
                S[i, j] = S[j, i] = theta[k]
            else:
                m[i] = theta[k]
        return A, S, m

    def prepare(self, scale):
        self.scale = scale
        self.A0, self.S0, self.m0 = self.fixed(scale)

    def implied(self, theta):
        """(Sigma, mu, B, the covariance and the means of all variables)."""
        A, S, m = self.matrices(theta)
        B = np.linalg.inv(np.eye(self.m) - A)
        full = B @ S @ B.T
        mu_all = B @ m
        return full[:self.p, :self.p], mu_all[:self.p], B, full, mu_all

    def derivatives(self, theta):
        """Sigma, mu and the stacks dSigma (Q, p, p), dmu (Q, p)."""
        sigma, mu, B, full, mu_all = self.implied(theta)
        FB, FC = B[:self.p], full[:self.p]
        dS = np.zeros((self.Q, self.p, self.p))
        dm = np.zeros((self.Q, self.p))
        for (kind, i, j), k in zip(self.cells, self.place):
            if k < 0:
                continue
            if kind == "A":
                d = np.outer(FB[:, i], FC[:, j])
                dS[k] += d + d.T
                dm[k] += FB[:, i] * mu_all[j]
            elif kind == "S":
                if i == j:
                    dS[k] += np.outer(FB[:, i], FB[:, i])
                else:
                    d = np.outer(FB[:, i], FB[:, j])
                    dS[k] += d + d.T
            else:
                dm[k] += FB[:, i]
        return sigma, mu, dS, dm

    def start(self, var, mean):
        """Start values in the scaled units: loadings 1, regressions and covariances 0, the variance of a manifest
        variable half of its own, of a latent one half of its first indicator's (a tenth when its first loading is
        fixed at 0, a slope), intercepts the means of the data, the mean of a latent variable the mean of its first
        indicator with a fixed loading and a fixed intercept (else 0)."""
        theta = np.zeros(self.Q)
        first = {}
        for (op, lhs, rhs, value, _) in self.table:
            if op == "=~" and lhs not in first:
                first[lhs] = (rhs, value)
        fixed_icept = {lhs for (op, lhs, _, value, _) in self.table if op == "~1" and value is not None}

        def var_of(v, depth=0):
            k = self.index[v]
            if k < self.p:
                return var[k]
            ref = first.get(v)
            if ref is None or depth > 5:
                return 1.0
            return var_of(ref[0], depth + 1)

        done = set()
        for (op, lhs, rhs, value, _), (kind, i, j), k in zip(self.table, self.cells, self.place):
            if k < 0 or k in done:
                continue
            done.add(k)
            if op == "=~":
                theta[k] = 1.0
            elif op == "~~" and i == j:
                v = self.names[i]
                ref = first.get(v)
                theta[k] = 0.1 * var_of(v) if ref is not None and ref[1] == 0 else 0.5 * var_of(v)
            elif op == "~1":
                if i < self.p:
                    theta[k] = mean[i]
                else:
                    ref = first.get(self.names[i])
                    if ref and ref[1] not in (None, 0) and ref[0] in fixed_icept and self.index[ref[0]] < self.p:
                        theta[k] = mean[self.index[ref[0]]] / ref[1]
        return theta


def _gradient(pats, sigma, mu, dS, dm, N):
    """The gradient of F = -2 log L / N alone (sem_means._grad_info without the information): per pattern
    vec(S_oo^-1 (Sigma_oo - C - e e') S_oo^-1) against the stack dSigma, so p² per parameter instead of p³."""
    Q = len(dS)
    grad = np.zeros(Q)
    for pt in pats:
        o = pt["obs"]
        so = sigma[np.ix_(o, o)]
        inv = np.linalg.inv(so)
        e = pt["mean"] - mu[o]
        m = inv @ (so - pt["cov"] - np.outer(e, e)) @ inv
        d_o = dS[:, o][:, :, o]
        grad += pt["n"] / N * (d_o.reshape(Q, -1) @ m.reshape(-1) - 2 * dm[:, o] @ (inv @ e))
    return grad


def _observed_info(make, theta, pats, N):
    """Half the Hessian of F by central differences of the analytic gradient (as sem_means._observed_info)."""
    Q = len(theta)
    out = np.zeros((Q, Q))
    for k in range(Q):
        h = 1e-5 * max(1.0, abs(theta[k]))
        up, down = theta.copy(), theta.copy()
        up[k] += h
        down[k] -= h
        out[:, k] = (_gradient(pats, *make(up), N) - _gradient(pats, *make(down), N)) / (2 * h)
    return 0.5 * (out + out.T) / 2


def _units(x, cols, same):
    """The SD of each column; the columns of one group in `same` share the mean of their SDs."""
    with np.errstate(all="ignore"):
        unit = np.nanstd(x, axis=0)
    if np.any(~np.isfinite(unit)) or np.any(unit <= 0):
        raise sem.SemError("constant")
    for group in same or ():
        idx = [cols.index(c) for c in group if c in cols]
        if idx:
            unit[idx] = float(np.mean(unit[idx]))
    return unit


def _shared_scale(model, unit):
    """The factor of each row; rows of one label need the same factor, else the columns share one SD."""
    scale = model.unit_factors(unit)
    for k in range(model.Q):
        f = scale[model.place == k]
        if len(f) > 1 and np.max(np.abs(f - f[0])) > 1e-9 * abs(f[0]):
            return None
    return scale


def fit(x, cols, table, same=None, robust=True, light=False, start=None):
    """The model of the table on the data x (n x p, columns cols, NaN = missing). Returns {"converged", "n",
    "n_complete", "df", "chi2", "p", "scaled": {"c", "chi2", "p"} | None, "cfi", "tli", "rmsea", "rmsea_ci", "srmr",
    "rows": [{"op", "lhs", "rhs", "label", "free", "est", "se", "z", "p", "std"}] per row of the table, "theta",
    "vcov" (of the joint parameters, in the units of the data), "problems", "admissible"}."""
    x = np.asarray(x, dtype=float)
    x = x[~np.all(np.isnan(x), axis=1)]
    N, p = x.shape
    if N < 3 or p != len(cols):
        raise sem.SemError("data")
    model = Model(table, cols)
    unit = _units(x, cols, same)
    scale = _shared_scale(model, unit)
    if scale is None:  # equal parameters between columns of different SDs: one SD for all
        unit = np.full(p, float(np.median(unit)))
        scale = model.unit_factors(unit)
    model.prepare(scale)
    xs = x / unit
    Q = model.Q
    pats = sem_means._patterns(xs)
    mu1, sigma1, _ = sem_means.saturated(xs) if not light else (np.nanmean(xs, axis=0), None, True)
    if sigma1 is not None and np.linalg.slogdet(sigma1)[0] <= 0:
        raise sem.SemError("singular")
    jscale = _joint(model, scale)
    if start is not None:
        theta = np.asarray(start, dtype=float) / jscale
    else:
        theta = model.start(np.nanvar(xs, axis=0), np.nanmean(xs, axis=0))

    def total(t):
        try:
            sigma, mu, _, _, _ = model.implied(t)
        except np.linalg.LinAlgError:
            return math.inf
        return sem_means._f_patterns(pats, sigma, mu, N)

    f = total(theta)
    if not math.isfinite(f):  # a start that is not positive definite: larger variances
        for k in set(model.place[[i for i, c in enumerate(model.cells) if c[0] == "S" and c[1] == c[2]]]):
            if k >= 0:
                theta[k] *= 2
        f = total(theta)
    converged = False
    for _ in range(sem.MAX_ITER):
        if not math.isfinite(f):
            break
        sigma, mu, dS, dm = model.derivatives(theta)
        grad, info = sem_means._grad_info(pats, sigma, mu, dS, dm, N)
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
        return {"converged": False, "n": N, "theta": (theta * jscale).tolist(), "problems": ["converge"]}
    if light:
        return {"converged": True, "n": N, "theta": (theta * jscale).tolist(), "f": f,
                "admissible": not _problems(model, theta)}
    sigma, mu, dS, dm = model.derivatives(theta)
    a0 = _observed_info(model.derivatives, theta, pats, N)
    problems = []
    if not sem._identified(a0):
        problems.append("identified")
        a0_inv = np.linalg.pinv(a0)
    else:
        a0_inv = np.linalg.inv(a0)
    if robust:
        scores0, _ = sem_means._scores(pats, sigma, mu, dS, dm, N)
        b0 = scores0.T @ scores0 / N
        vcov = a0_inv @ b0 @ a0_inv / N
    else:
        vcov = a0_inv / N  # a0 is half the Hessian of F = -2 log L / N
    f1 = sem_means._f_patterns(pats, sigma1, mu1, N)
    chi2 = float(N * (f - f1))
    df = p * (p + 1) // 2 + p - Q
    out = {"converged": True, "robust": robust, "n": N, "n_complete": int(np.sum(~np.any(np.isnan(x), axis=1))),
           "p_vars": p, "q": Q, "df": df, "chi2": chi2, "p": sem._chi2_p(chi2, df) if df > 0 else None,
           "scaled": None, "theta": (theta * jscale).tolist(), "vcov": (vcov * np.outer(jscale, jscale)).tolist()}
    if df < 0:
        problems.append("identified")
    if robust and df > 0:
        dS1, dm1 = sem_means._saturated_stacks(p)
        scores1, _ = sem_means._scores(pats, sigma1, mu1, dS1, dm1, N)
        rows1, cols1 = np.tril_indices(p)

        def make1(t):
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
    sigma_b = np.diag(np.nanvar(xs, axis=0))
    base_chi2 = float(N * (sem_means._f_patterns(pats, sigma_b, np.nanmean(xs, axis=0), N) - f1))
    base_df = p * (p - 1) // 2
    out["baseline"] = (base_chi2, base_df)
    if df > 0 and base_df > 0:
        d_model, d_base = max(chi2 - df, 0.0), max(base_chi2 - base_df, 0.0)
        out["cfi"] = 1.0 if max(d_model, d_base) == 0 else 1 - d_model / max(d_model, d_base)
        out["tli"] = (base_chi2 / base_df - chi2 / df) / (base_chi2 / base_df - 1) if base_chi2 / base_df > 1 else None
        out["rmsea"] = math.sqrt(max(chi2 - df, 0.0) / (df * N))
        out["rmsea_ci"] = sem.rmsea_ci(chi2, df, N)
    sd = np.sqrt(np.diag(sigma1))
    r, c_ = np.tril_indices(p)
    resid = (sigma1 - sigma) / np.outer(sd, sd)
    mean_resid = (mu1 - mu) / sd
    out["srmr"] = math.sqrt(float((np.sum(resid[r, c_] ** 2) + np.sum(mean_resid ** 2)) / (len(r) + p)))
    out["residuals"] = sem._largest_residuals(sigma1, sigma, model.manifest)
    out["rows"] = _rows(model, theta, vcov, scale, jscale)
    out["r2"] = _r2(model, theta)
    problems += _problems(model, theta)
    out["problems"] = sorted(set(problems))
    out["admissible"] = not out["problems"]
    return out


def _joint(model, scale):
    """The factor of each joint parameter (that of its first row)."""
    out = np.ones(model.Q)
    for k in range(model.Q - 1, -1, -1):
        out[k] = scale[np.where(model.place == k)[0][0]]
    return out


def _std_values(model, theta):
    """The completely standardised value of every row (Bollen, 1989): loadings and regressions times sd_from / sd_to,
    (residual) variances by the total variance, covariances by the root of both (residual) variances, means by the total
    SD."""
    A, S, m = model.matrices(theta)
    _, _, _, full, mu_all = model.implied(theta)
    sd = np.sqrt(np.maximum(np.diag(full), 1e-300))
    out = []
    for kind, i, j in model.cells:
        if kind == "A":
            out.append(A[i, j] * sd[j] / sd[i])
        elif kind == "S" and i == j:
            out.append(S[i, i] / full[i, i])
        elif kind == "S":
            denom = math.sqrt(max(S[i, i], 1e-300) * max(S[j, j], 1e-300))
            out.append(S[i, j] / denom)
        else:
            out.append(m[i] / sd[i])
    return np.array(out)


def _rows(model, theta, vcov, scale, jscale):
    """The rows of the table with estimate, error, z, p (free rows) and the standardised value with its error by the
    delta method (numerical derivatives)."""
    std = _std_values(model, theta)
    jac = np.zeros((len(model.cells), model.Q))
    for k in range(model.Q):
        h = 1e-6 * max(1.0, abs(theta[k]))
        up, down = theta.copy(), theta.copy()
        up[k] += h
        down[k] -= h
        jac[:, k] = (_std_values(model, up) - _std_values(model, down)) / (2 * h)
    std_var = np.einsum("rk,kl,rl->r", jac, vcov, jac)
    out = []
    for (op, lhs, rhs, value, label), k, f, s, sv in zip(model.table, model.place, scale, std, std_var):
        entry = {"op": op, "lhs": lhs, "rhs": rhs, "label": label, "free": bool(k >= 0), "place": int(k), "std": float(s),
                 "std_se": math.sqrt(max(float(sv), 0.0)) if k >= 0 else None}
        if k >= 0:
            est, se = float(theta[k] * jscale[k]), math.sqrt(max(float(vcov[k, k]), 0.0)) * jscale[k]
            z = est / se if se > 0 else None
            entry.update(est=est, se=se, z=z, p=sem._normal_p(z) if z is not None else None)
        else:
            entry.update(est=value, se=None, z=None, p=None)
        out.append(entry)
    return out


def _r2(model, theta):
    """R² of every variable with a regression or a loading leading to it."""
    _, S, _ = model.matrices(theta)
    _, _, _, full, _ = model.implied(theta)
    targets = []
    for (op, lhs, rhs, _, _), (kind, i, _) in zip(model.table, model.cells):
        if kind == "A" and model.names[i] not in targets:
            targets.append(model.names[i])
    out = {}
    for v in targets:
        k = model.index[v]
        if full[k, k] > 0:
            out[v] = float(1 - S[k, k] / full[k, k])
    return out


def _problems(model, theta):
    """"heywood": a free (residual) variance below 0; "latent_pd": the covariances of the latent variables not
    positive definite (e.g. a random intercept and a slope correlating above 1)."""
    A, S, _ = model.matrices(theta)
    out = []
    if any(kind == "S" and i == j and k >= 0 and S[i, i] < 0 for (kind, i, j), k in zip(model.cells, model.place)):
        out.append("heywood")
    lat = [model.index[v] for v in model.latent]
    if len(lat) > 1:
        _, _, _, full, _ = model.implied(theta)
        sub = full[np.ix_(lat, lat)]
        dg = np.diag(sub)
        if np.all(dg > 1e-12):
            corr = sub / np.sqrt(np.outer(dg, dg))
            if np.linalg.eigvalsh(corr)[0] < -1e-8:
                out.append("latent_pd")
    return out


def find(fit_out, op, lhs, rhs=None):
    """The row of a fit for (op, lhs, rhs); "~~" in either order."""
    for r in fit_out.get("rows") or ():
        if r["op"] == op and ((r["lhs"], r["rhs"]) == (lhs, rhs) or (op == "~~" and (r["lhs"], r["rhs"]) == (rhs, lhs))):
            return r
    return None


def wald_equal(fit_out, groups):
    """The Wald test that the free rows in each group are equal ([[(op, lhs, rhs), ...], ...]): {"chi2", "df", "p"}
    from the joint covariance of the estimates (in a fit without the equality)."""
    theta, vcov = np.asarray(fit_out["theta"]), np.asarray(fit_out["vcov"])
    places = {(r["op"], r["lhs"], r["rhs"]): r["place"] for r in fit_out["rows"] if r["free"]}
    contrasts = []
    for group in groups:
        idx = [places[g] for g in group if g in places]
        for a in idx[1:]:
            c = np.zeros(len(theta))
            c[idx[0]], c[a] = 1.0, -1.0
            contrasts.append(c)
    if not contrasts:
        return None
    C = np.array(contrasts)
    d = C @ theta
    try:
        chi2 = float(d @ np.linalg.solve(C @ vcov @ C.T, d))
    except np.linalg.LinAlgError:
        return None
    return {"chi2": chi2, "df": len(C), "p": sem._chi2_p(chi2, len(C))}


def lavaan_syntax(table, cols=None):
    """The table as lavaan syntax for lavaan() (nothing added on its own; meanstructure from the "~1" rows)."""
    lines = []
    for op, lhs, rhs, value, label in table:
        if value is not None:
            pre = f"{value:g}*"
        elif label is not None:
            pre = f"{label}*"
        else:
            pre = ""
        if op == "~1":
            lines.append(f"{lhs} ~ {pre}1")
        else:
            lines.append(f"{lhs} {op} {pre}{rhs}")
    return "\n".join(lines)
