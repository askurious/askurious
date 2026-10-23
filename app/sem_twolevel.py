"""Two-level structural equation models for diary studies (longitudinal and multilevel concept 2026-10-17, E9):
entries within persons, y_ij = mu + eta_Bj + eta_Wij, a model for the covariances within persons (Sigma_W) and one
for those between persons with the means (Sigma_B, mu), each written as a parameter table (app/sem_table.py) and
estimated together (McDonald & Goldstein, 1989; Rosseel, 2021). Parameters with the same label are the same
in both levels (e.g. equal loadings for a construct over the levels). Numbers only, numpy.

Estimation: full maximum likelihood for unequal numbers of entries per person (McDonald & Goldstein, 1989; Muthén,
1994), on the entries with every variable answered: per person j with n_j entries, the mean ybar_j and the scatter
A_j around it, -2 log L = sum_j [(n_j - 1) log|Sigma_W| + tr(Sigma_W^-1 A_j) + log|V_j| + n_j (ybar_j - mu)'
V_j^-1 (ybar_j - mu)] + const with V_j = Sigma_W + n_j Sigma_B; persons with the same n_j share V. Fisher scoring
with the expected information, errors from the observed information (Askurious default, as for the FIML models of
app/sem_means.py). Fit against the saturated two-level model (free Sigma_W, Sigma_B and mu); SRMR per level
(correlations, without the means). Checked against independently computed reference values
(tests/analysis/test_sem_twolevel.py)."""
import math

import numpy as np

from app import sem, sem_table

ROSSEEL_2021 = ("Rosseel, Y. (2021). Evaluating the observed log-likelihood function in two-level structural equation "
                "modeling with missing data: From formulas to R code. Psych, 3(2), 197–232. "
                "https://doi.org/10.3390/psych3020017")
MCDONALD_GOLDSTEIN = ("McDonald, R. P., & Goldstein, H. (1989). Balanced versus unbalanced designs for linear structural "
                      "relations in two-level data. British Journal of Mathematical and Statistical Psychology, 42(2), "
                      "215–232. https://doi.org/10.1111/j.2044-8317.1989.tb00911.x")
GELDHOF = ("Geldhof, G. J., Preacher, K. J., & Zyphur, M. J. (2014). Reliability estimation in a multilevel "
           "confirmatory factor analysis framework. Psychological Methods, 19(1), 72–91. https://doi.org/10.1037/a0032138")
SOURCES = (ROSSEEL_2021, MCDONALD_GOLDSTEIN, GELDHOF)


class _Stats:
    """The data per size of person: {n: (persons, sum of A_j, sum of ybar_j, sum of ybar_j ybar_j')}."""

    def __init__(self, y, cluster):
        self.N, self.p = y.shape
        order = np.argsort(cluster, kind="stable")
        y, cluster = y[order], cluster[order]
        cuts = np.flatnonzero(np.diff(cluster)) + 1
        self.by = {}
        self.A = np.zeros((self.p, self.p))
        self.means = []
        for idx in np.split(np.arange(self.N), cuts):
            yj = y[idx]
            m = yj.mean(axis=0)
            c = yj - m
            self.A += c.T @ c
            n = len(idx)
            e = self.by.setdefault(n, [0, np.zeros(self.p), np.zeros((self.p, self.p))])
            e[0] += 1
            e[1] += m
            e[2] += np.outer(m, m)
            self.means.append((n, m, c.T @ c))
        self.J = len(self.means)


class Model:
    """Two parameter tables (within, between) on the same columns, joined by their labels."""

    def __init__(self, within, between, cols):
        self.w = sem_table.Model(within, cols)
        self.b = sem_table.Model(between, cols)
        self.p = len(cols)
        self.map_w = np.array(self.w.place)
        joint = {lab: k for lab, k in self.w.labels.items()}
        Q = self.w.Q
        map_b = []
        local_to_joint = {}
        for (op, lhs, rhs, value, label), k in zip(self.b.table, self.b.place):
            if k < 0:
                map_b.append(-1)
                continue
            if k in local_to_joint:
                map_b.append(local_to_joint[k])
            elif label is not None and label in joint:
                local_to_joint[k] = joint[label]
                map_b.append(joint[label])
            else:
                local_to_joint[k] = Q
                map_b.append(Q)
                Q += 1
        self.map_b_rows = np.array(map_b)
        # the joint place of each local parameter of the between model
        self.b_local = np.full(self.b.Q, -1)
        for k_local, k_joint in local_to_joint.items():
            self.b_local[k_local] = k_joint
        self.Q = Q

    def split(self, theta):
        return theta[:self.w.Q], theta[self.b_local]

    def derivatives(self, theta):
        """Sigma_W, Sigma_B, mu and their stacks with respect to the joint vector."""
        tw, tb = self.split(theta)
        sw, _, dSw, _ = self.w.derivatives(tw)
        sb, mu, dSb_l, dm_l = self.b.derivatives(tb)
        dSw_j = np.zeros((self.Q, self.p, self.p))
        dSw_j[:self.w.Q] = dSw
        dSb = np.zeros((self.Q, self.p, self.p))
        dm = np.zeros((self.Q, self.p))
        for k_local, k_joint in enumerate(self.b_local):
            dSb[k_joint] += dSb_l[k_local]
            dm[k_joint] += dm_l[k_local]
        return sw, sb, mu, dSw_j, dSb, dm


def deviance(W, B, mu, st):
    """-2 log L without the constant N p log(2 pi); inf where a matrix is not positive definite."""
    sign, ldw = np.linalg.slogdet(W)
    if sign <= 0:
        return math.inf
    winv = np.linalg.inv(W)
    out = (st.N - st.J) * ldw + float(np.sum(winv * st.A))
    for n, (m_n, s1, s2) in st.by.items():
        V = W + n * B
        sign, ldv = np.linalg.slogdet(V)
        if sign <= 0:
            return math.inf
        vinv = np.linalg.inv(V)
        D = s2 - np.outer(mu, s1) - np.outer(s1, mu) + m_n * np.outer(mu, mu)
        out += m_n * ldv + n * float(np.sum(vinv * D))
    return out


def _grad_info(W, B, mu, dW, dB, dm, st):
    """The gradient of -2 log L / 2 and its expected information."""
    Q = len(dW)
    winv = np.linalg.inv(W)
    GW = (st.N - st.J) * winv - winv @ st.A @ winv
    GB = np.zeros_like(W)
    gm = np.zeros(len(mu))
    info = 0.5 * (st.N - st.J) * sem._trace_products(winv[None] @ dW)
    for n, (m_n, s1, s2) in st.by.items():
        V = W + n * B
        vinv = np.linalg.inv(V)
        D = s2 - np.outer(mu, s1) - np.outer(s1, mu) + m_n * np.outer(mu, mu)
        part = m_n * vinv - n * vinv @ D @ vinv
        GW += part
        GB += n * part
        gm += -2 * n * vinv @ (s1 - m_n * mu)
        dV = dW + n * dB
        info += 0.5 * m_n * sem._trace_products(vinv[None] @ dV) + m_n * n * dm @ vinv @ dm.T
    grad = 0.5 * (dW.reshape(Q, -1) @ GW.reshape(-1) + dB.reshape(Q, -1) @ GB.reshape(-1) + dm @ gm)
    return grad, info


def _optimise(model, st, theta):
    def total(t):
        try:
            W, B, mu, *_ = model.derivatives(t)
        except np.linalg.LinAlgError:
            return math.inf
        return deviance(W, B, mu, st)

    f = total(theta)
    if not math.isfinite(f):
        return theta, f, False
    for _ in range(sem.MAX_ITER):
        W, B, mu, dW, dB, dm = model.derivatives(theta)
        grad, info = _grad_info(W, B, mu, dW, dB, dm, st)
        try:
            step = -np.linalg.solve(info, grad)
        except np.linalg.LinAlgError:
            step = -np.linalg.lstsq(info, grad, rcond=None)[0]
        if np.all(np.abs(step) < sem.TOL * (1 + np.abs(theta))):
            return theta, f, True
        t = 1.0
        for _ in range(40):
            new = theta + t * step
            f_new = total(new)
            if f_new <= f + 1e-10:
                break
            t /= 2
        else:
            return theta, f, False
        theta, f = new, f_new
        if np.all(np.abs(t * step) < sem.TOL * (1 + np.abs(theta))):
            return theta, f, True
    return theta, f, False


def _saturated_tables(cols):
    within = [sem_table.row("~~", a, b) for i, a in enumerate(cols) for b in cols[i:]]
    between = [sem_table.row("~~", a, b) for i, a in enumerate(cols) for b in cols[i:]] + [
        sem_table.row("~1", c) for c in cols]
    return within, between


def _start(model, y, st):
    """Start values: within variances half the pooled within variance, between half the variance of the person means,
    loadings 1, means the grand means."""
    wvar = np.diag(st.A) / max(st.N - st.J, 1)
    bmeans = np.array([m for _, m, _ in st.means])
    bvar = np.maximum(bmeans.var(axis=0) - wvar * np.mean([1 / n for n, _, _ in st.means]), 0.05 * wvar)
    tw = model.w.start(wvar, np.zeros(model.p))
    tb = model.b.start(bvar, y.mean(axis=0))
    theta = np.zeros(model.Q)
    theta[:model.w.Q] = tw
    for k_local, k_joint in enumerate(model.b_local):
        if k_joint >= model.w.Q:
            theta[k_joint] = tb[k_local]
    return theta


def fit(y, cluster, cols, within, between):
    """The two-level model on the entries y (n x p, NaN rows dropped) of the persons `cluster`. Returns {"converged",
    "n", "persons", "chi2", "df", "p", "cfi", "tli", "rmsea", "srmr_within", "srmr_between", "within": rows,
    "between": rows (as sem_table.fit), "problems", "admissible"}."""
    y = np.asarray(y, dtype=float)
    cluster = np.asarray(cluster)
    keep = np.all(np.isfinite(y), axis=1)
    y, cluster = y[keep], cluster[keep]
    _, cluster = np.unique(cluster, return_inverse=True)
    if len(y) < 10 or cluster.max() < 2:
        raise sem.SemError("data")
    st = _Stats(y, cluster)
    model = Model(within, between, cols)
    model.w.prepare(np.ones(len(model.w.cells)))
    model.b.prepare(np.ones(len(model.b.cells)))
    theta, f, converged = _optimise(model, st, _start(model, y, st))
    if not converged:
        return {"converged": False, "n": int(st.N), "persons": int(st.J), "problems": ["converge"]}
    sw_t, sb_t = _saturated_tables(cols)
    sat = Model(sw_t, sb_t, cols)
    sat.w.prepare(np.ones(len(sat.w.cells)))
    sat.b.prepare(np.ones(len(sat.b.cells)))
    t_sat, f_sat, ok_sat = _optimise(sat, st, _start(sat, y, st))
    p = model.p
    df = p * (p + 1) + p - model.Q
    chi2 = float(f - f_sat) if ok_sat else None
    out = {"converged": True, "n": int(st.N), "persons": int(st.J), "df": df, "chi2": chi2, "q": model.Q,
           "p": sem._chi2_p(chi2, df) if chi2 is not None and df > 0 else None, "deviance": float(f)}
    # the baseline: variances within, variances and means between (the independence model of both levels)
    bw = [sem_table.row("~~", c, c) for c in cols]
    bb = [sem_table.row("~~", c, c) for c in cols] + [sem_table.row("~1", c) for c in cols]
    base = Model(bw, bb, cols)
    base.w.prepare(np.ones(len(base.w.cells)))
    base.b.prepare(np.ones(len(base.b.cells)))
    t_base, f_base, ok_base = _optimise(base, st, _start(base, y, st))
    if chi2 is not None and ok_base and df > 0:
        base_chi2, base_df = float(f_base - f_sat), p * (p + 1) + p - base.Q
        d_model, d_base = max(chi2 - df, 0.0), max(base_chi2 - base_df, 0.0)
        out["cfi"] = 1.0 if max(d_model, d_base) == 0 else 1 - d_model / max(d_model, d_base)
        out["tli"] = (base_chi2 / base_df - chi2 / df) / (base_chi2 / base_df - 1) if base_chi2 / base_df > 1 else None
        out["rmsea"] = math.sqrt(max(chi2 - df, 0.0) / (df * st.N))
    W, B, mu, dW, dB, dm = model.derivatives(theta)
    if ok_sat:
        Ws, Bs, mus, *_ = sat.derivatives(t_sat)
        out["srmr_within"] = _srmr(Ws, W)
        out["srmr_between"] = _srmr(Bs, B)
    hess = _observed(model, theta, st)
    problems = []
    if not sem._identified(hess):
        problems.append("identified")
        vcov = np.linalg.pinv(hess)
    else:
        vcov = np.linalg.inv(hess)
    tw, tb = model.split(theta)
    vw = vcov[:model.w.Q, :model.w.Q]
    vb = vcov[np.ix_(model.b_local, model.b_local)]
    ones_w, ones_b = np.ones(model.w.Q), np.ones(model.b.Q)
    out["within"] = sem_table._rows(model.w, tw, vw, np.ones(len(model.w.cells)), ones_w)
    out["between"] = sem_table._rows(model.b, tb, vb, np.ones(len(model.b.cells)), ones_b)
    problems += sem_table._problems(model.w, tw) + sem_table._problems(model.b, tb)
    out["problems"] = sorted(set(problems))
    out["admissible"] = not out["problems"]
    out["theta"] = theta.tolist()
    return out


def _observed(model, theta, st):
    """Half the Hessian of -2 log L by central differences of the analytic gradient (the observed information)."""
    Q = len(theta)
    out = np.zeros((Q, Q))
    for k in range(Q):
        h = 1e-5 * max(1.0, abs(theta[k]))
        up, down = theta.copy(), theta.copy()
        up[k] += h
        down[k] -= h
        gu = _grad_info(*model.derivatives(up), st)[0]
        gd = _grad_info(*model.derivatives(down), st)[0]
        out[:, k] = (gu - gd) / (2 * h)
    return (out + out.T) / 2


def _srmr(s, sigma):
    """The SRMR of one level (within or between): the differences of the correlations, over
    p(p + 1) / 2 places (the diagonal counts with 0), without the means."""
    def cor(m):
        d = np.sqrt(np.maximum(np.diag(m), 1e-300))
        return m / np.outer(d, d)

    r = cor(s) - cor(sigma)
    i, j = np.tril_indices(len(s), -1)
    return math.sqrt(float(np.sum(r[i, j] ** 2)) / (len(s) * (len(s) + 1) / 2))


def find(fit_out, level, op, lhs, rhs=None):
    return sem_table.find({"rows": fit_out.get(level) or []}, op, lhs, rhs)


def omega(fit_out, level, factor, items):
    """Reliability of the sum of `items` at one level (Geldhof et al., 2014): (sum of loadings)² var(factor) /
    ((sum of loadings)² var(factor) + sum of residual variances)."""
    lam = [find(fit_out, level, "=~", factor, i)["est"] for i in items]
    phi = find(fit_out, level, "~~", factor, factor)["est"]
    theta = [find(fit_out, level, "~~", i, i)["est"] for i in items]
    s = sum(lam) ** 2 * phi
    return s / (s + sum(theta)) if s + sum(theta) > 0 else None
