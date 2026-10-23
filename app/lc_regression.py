"""Latent class regression (finite mixture of linear regressions) for the ratings-based conjoint (app/items/cva.py),
numpy only: the segments of a CVA (concept aca-cva-konzept.md, stage 4).

Model (DeSarbo & Cron, 1988; Wedel & Kamakura, 2000, ch. 7): K classes with shares pi_c, one coefficient vector b_c
and one residual variance s2_c each; a person stays in one class for all tasks, so the likelihood of person n in
class c is the product of normal densities N(y_nt; x_nt b_c, s2_c) over the person's tasks, and
    LL = sum_n log sum_c pi_c L_nc,     tau_nc = pi_c L_nc / sum_k pi_k L_nk (posterior membership).
The rows x_nt are those of the regression per person (app/cva_analysis.py rows_of: a constant and the effects coding,
pairs the difference of the two offers), so a class's coefficients read like a person's part-worths. Unlike the
regression per person a class needs no person to identify every part-worth alone.

Estimated by EM (Dempster, Laird & Rubin, 1977) with a closed M-step: per class a least squares fit whose rows count
with the membership tau_nc of their person, and its weighted residual variance. A tiny ridge (RIDGE times the mean
diagonal) keeps a class whose persons never saw a level solvable, and the variance stays above VAR_FLOOR of the
variance of all answers: a class of persons with the same answer everywhere would otherwise have a likelihood without
bound (McLachlan & Peel, 2000, 3.9). Starting values, criteria, the choice of K and the limits on persons and classes
as for the latent class logit (app/latent_class.py: STARTS random memberships plus a split of the K - 1 solution,
short runs, the KEEP best to convergence; BIC, AIC3, CAIC and entropy R² with N = persons; at most one class per
PER_PARAMETER persons and parameter). Parameters per class: the coefficients and the variance.

Standard errors of a class's coefficients: the sandwich over persons conditional on the memberships (White, 1982;
clustered by person with the factor G / (G - 1)), which allows for the tasks of a person not being independent but
not for the uncertainty of the memberships themselves; so they are somewhat too small (Festlegung von Askurious, the
same for every class). Intervals of the class shares are not given.
"""
import math
import time

import numpy as np

from app import latent_class as lc

RIDGE = 1e-6       # times the mean diagonal of X'WX, added to keep a class solvable
VAR_FLOOR = 1e-3   # the residual variance of a class stays above this share of the variance of all answers

DESARBO = ("DeSarbo, W. S., & Cron, W. L. (1988). A maximum likelihood methodology for clusterwise linear regression. "
           "Journal of Classification, 5(2), 249–282. https://doi.org/10.1007/BF01897167")


class Data:
    """The rows of all persons stacked: X (rows x p), y, owner (person of every row), n persons."""

    def __init__(self, persons):
        self.X = np.array([x for p in persons for x in p["X"]], dtype=float)
        self.y = np.array([v for p in persons for v in p["y"]], dtype=float)
        self.owner = np.array([n for n, p in enumerate(persons) for _ in p["y"]], dtype=int)
        self.n = len(persons)
        self.p = self.X.shape[1]
        self.tasks = np.bincount(self.owner, minlength=self.n)
        var = float(self.y.var()) if len(self.y) else 1.0
        self.floor = max(var, 1e-9) * VAR_FLOOR

    def person_ll(self, B, s2):
        """persons x classes: log L_nc."""
        out = np.empty((self.n, len(B)))
        for c in range(len(B)):
            r = self.y - self.X @ B[c]
            row = -0.5 * (math.log(2 * math.pi * s2[c]) + r * r / s2[c])
            out[:, c] = np.bincount(self.owner, weights=row, minlength=self.n)
        return out


def n_params(p):
    """Parameters per class: p coefficients and the variance."""
    return p + 1


def _posterior(L, pi):
    a = L + np.log(np.maximum(pi, 1e-300))
    m = a.max(axis=1, keepdims=True)
    e = np.exp(a - m)
    s = e.sum(axis=1, keepdims=True)
    return float((m[:, 0] + np.log(s[:, 0])).sum()), e / s


def _m_step(data, tau):
    C = tau.shape[1]
    B = np.zeros((C, data.p))
    s2 = np.zeros(C)
    for c in range(C):
        w = tau[data.owner, c]
        XtW = data.X.T * w
        A = XtW @ data.X
        A += np.eye(data.p) * RIDGE * max(float(np.trace(A)) / data.p, 1e-12)
        B[c] = np.linalg.solve(A, XtW @ data.y)
        r = data.y - data.X @ B[c]
        s2[c] = max(float((w * r * r).sum() / max(w.sum(), 1e-12)), data.floor)
    pi = np.maximum(tau.mean(axis=0), 1e-12)
    return B, s2, pi / pi.sum()


def _run(data, tau, iterations, tol=lc.TOL):
    """EM from the memberships tau: (ll, B, s2, pi, tau, iterations, converged)."""
    B, s2, pi = _m_step(data, tau)
    ll, tau = _posterior(data.person_ll(B, s2), pi)
    for it in range(1, iterations + 1):
        B, s2, pi = _m_step(data, tau)
        new, tau = _posterior(data.person_ll(B, s2), pi)
        if abs(new - ll) <= tol * max(1.0, abs(ll)):
            return new, B, s2, pi, tau, it, True
        ll = new
    return ll, B, s2, pi, tau, iterations, False


def _robust(data, tau, B, s2):
    """Block-diagonal covariance of the coefficients (classes x p), the sandwich over persons given tau; padded with
    zeros for the C - 1 share parameters, the shape latent_class gives (no intervals of the shares from them)."""
    C, p = B.shape
    cov = np.zeros((C * p + C - 1, C * p + C - 1))
    G = data.n
    for c in range(C):
        w = tau[data.owner, c]
        XtW = data.X.T * w
        A = XtW @ data.X
        A += np.eye(p) * RIDGE * max(float(np.trace(A)) / p, 1e-12)
        r = data.y - data.X @ B[c]
        g = np.zeros((data.n, p))
        np.add.at(g, data.owner, data.X * (w * r)[:, None])
        Ai = np.linalg.pinv(A)
        V = Ai @ (g.T @ g) @ Ai * (G / (G - 1) if G > 1 else 1.0)
        cov[c * p:(c + 1) * p, c * p:(c + 1) * p] = V
    return cov


def estimate(persons, kmax=None, starts=lc.STARTS, seed=1, progress=None, finished=None, on_solution=None):
    """All solutions K = 1 .. kmax for persons [{"X": rows, "y": answers}]. Returns {"solutions", "persons", "sets",
    "seconds"}; each solution in the form of latent_class.estimate (K, ll, objective, criteria, pi, B, tau, cov,
    share_ci None, converged, iterations, found, runs, rlh None per class, G None) plus "sigma", the residual standard
    deviation per class. finished and on_solution as there (a run that resumes)."""
    t0 = time.time()
    data = Data(persons)
    k = n_params(data.p)
    kmax = kmax or lc.k_max(data.n, k)
    finished = [s for s in (finished or []) if s["K"] <= kmax]
    work = sum(C * (1 if C == 1 else starts + 1 + lc.KEEP) for C in range(len(finished) + 1, kmax + 1)) or 1
    done = 0

    def tick(C):
        nonlocal done
        done += C
        if progress:
            progress(min(done / work, 0.99))

    solutions = list(finished)
    previous = solutions[-1] if solutions else None
    for C in range(len(solutions) + 1, kmax + 1):
        rng = np.random.default_rng([seed, C])
        if C == 1:
            finals = [_run(data, np.ones((data.n, 1)), lc.MAX_ITER)]
            tick(C)
        else:
            shorts = []
            for _ in range(starts):
                shorts.append(_run(data, rng.dirichlet(np.ones(C), data.n), lc.SHORT))
                tick(C)
            if previous is not None:  # the K - 1 solution with its largest class split in two
                tau = np.asarray(previous["tau"], dtype=float)
                big = int(np.argmax(previous["pi"]))
                half = tau[:, big] * rng.uniform(0.3, 0.7, data.n)
                tau = np.column_stack([tau, half])
                tau[:, big] -= half
                shorts.append(_run(data, tau, lc.SHORT))
            tick(C)
            shorts.sort(key=lambda r: -r[0])
            finals = []
            for r in shorts[:lc.KEEP]:
                finals.append(_run(data, r[4], lc.MAX_ITER))
                tick(C)
            finals.sort(key=lambda r: -r[0])
        ll, B, s2, pi, tau, it, conv = finals[0]
        order = np.argsort(-pi, kind="stable")      # segment 1 = the largest
        B, s2, pi, tau = B[order], s2[order], pi[order], tau[:, order]
        found = sum(1 for r in finals if abs(r[0] - ll) < lc.SAME_LL)
        sol = {"K": C, "ll": ll, "objective": ll, **lc.criteria(ll, C, k, data.n, tau), "pi": pi, "B": B, "tau": tau,
               "cov": _robust(data, tau, B, s2), "converged": conv, "iterations": it, "found": found,
               "runs": len(finals), "rlh": [None] * C, "share_ci": None, "G": None,
               "sigma": [float(math.sqrt(v)) for v in s2]}
        solutions.append(sol)
        previous = sol
        if on_solution:
            on_solution(sol)
    return {"solutions": solutions, "persons": data.n, "sets": int(data.tasks.sum()), "seconds": time.time() - t0}
