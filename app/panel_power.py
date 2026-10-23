"""The power of the hypotheses "Wirkung über die Zeit" by simulation (longitudinal and multilevel concept 2026-10-17,
E11; Muthén & Muthén, 2002; Mulder, 2023): panels of the planned size drawn from a random-intercept cross-lagged panel
model (from three waves; the cross-lagged panel model with two), each estimated with the paths equal over the
intervals (app/sem_table.py, app/panel_tables.cross_table), and per hypothesis the share of panels in which its
cross-lagged path is significant in the expected direction. Run by the job kind "sem_power" (app/sem_power_job.py)
in the estimation process or on the Rechenknecht; numbers only, no database.

The population (Festlegungen von Askurious, in line with the simulation settings of Mulder, 2023): every variable with
variance 1 in every wave, of it RI_SHARE stable between persons (the random intercepts, correlated RI_CORR), the rest
within persons with the stability STABILITY, the expected standardised cross-lagged effects of the hypotheses (others
0), residuals of one wave correlated WITHIN_CORR; scores without measurement error and without dropout, so the power is
an upper bound. Effects so large that stability and cross-lagged paths would explain more than the within variance
cannot keep the variance at 1: too_large() names them, and the plan refuses the simulation instead of simulating
another effect (audit 2026-10-21, E5).

Input: {"key", "panel": {"vars": [names], "waves", "betas": [[from, to, beta]], "hyps": [{"number", "from", "to",
"direction"}]}, "n", "reps", "seed"}. Output: {"key", "n", "reps", "failed", "power": [[number, share]], "kind"}."""
import math

import numpy as np

from app import sem, sem_table

RI_SHARE = 0.5
RI_CORR = 0.3
STABILITY = 0.4
WITHIN_CORR = 0.2
ALPHA = 0.05
FLOOR = 0.05  # the least residual variance of a wave, as a share of the within variance


def _within(names, waves, betas):
    """(the covariances of the within parts per wave, the names whose residual variance had to be raised to FLOOR):
    every variable keeps variance 1 - RI_SHARE within while stability and paths explain less than that."""
    k = len(names)
    within = 1.0 - (RI_SHARE if waves >= 3 else 0.0)
    B = np.eye(k) * STABILITY
    for a, b, beta in betas:
        B[names.index(b), names.index(a)] = beta
    covs = [np.full((k, k), WITHIN_CORR * within) + np.eye(k) * (1 - WITHIN_CORR) * within]
    raised = set()
    for _ in range(1, waves):
        explained = B @ covs[-1] @ B.T
        left = within - np.diag(explained)
        raised.update(names[i] for i in np.flatnonzero(left < FLOOR * within))
        psi_d = np.maximum(left, FLOOR * within)
        psi = np.full((k, k), WITHIN_CORR) * np.sqrt(np.outer(psi_d, psi_d))
        np.fill_diagonal(psi, psi_d)
        covs.append(explained + psi)
    return covs, [n for n in names if n in raised]


def too_large(names, waves, betas):
    """The variables whose variance the expected effects would push above 1 (stability and cross-lagged paths explain
    more than the within variance minus FLOOR); [] when the planning assumptions hold (audit 2026-10-21, E5)."""
    return _within(list(names), int(waves), [(a, b, float(v)) for a, b, v in betas])[1]


def population(names, waves, betas):
    """(the covariance matrix of the columns <v<k>>_t<t>, kind): the variables in the order of names, wave by wave
    inside each variable as panel_models.cross_table orders them."""
    k = len(names)
    kind = "riclpm" if waves >= 3 else "clpm"
    ri = RI_SHARE if kind == "riclpm" else 0.0
    B = np.eye(k) * STABILITY
    for a, b, beta in betas:
        B[names.index(b), names.index(a)] = beta
    covs, _ = _within(names, waves, betas)
    # the joint covariance of the within parts over the waves: cov(w_t, w_s) = B^(t-s) cov(w_s) for t > s
    big = np.zeros((k * waves, k * waves))
    for t in range(waves):
        for s in range(t + 1):
            block = np.linalg.matrix_power(B, t - s) @ covs[s]
            big[t * k:(t + 1) * k, s * k:(s + 1) * k] = block
            big[s * k:(s + 1) * k, t * k:(t + 1) * k] = block.T
    if ri:
        r = np.full((k, k), RI_CORR * ri) + np.eye(k) * (1 - RI_CORR) * ri
        big += np.kron(np.ones((waves, waves)), r)
    order = [t * k + v for v in range(k) for t in range(waves)]  # variable by variable, wave by wave
    return big[np.ix_(order, order)], kind


def _hit(p, value, direction, alpha=ALPHA):
    if p is None or p >= alpha:
        return False
    return not ((direction == "pos" and value <= 0) or (direction == "neg" and value >= 0))


def run(inp, progress=None):
    from app import panel_tables

    spec = inp["panel"]
    names, waves = list(spec["vars"]), int(spec["waves"])
    betas = [(a, b, float(v)) for a, b, v in spec["betas"]]
    sigma, kind = population(names, waves, betas)
    variables = [{"id": f"v{i + 1}", "name": n, "label": n, "items": 0} for i, n in enumerate(names)]
    cols, tab, same = panel_tables.cross_table(variables, waves, kind, equal=waves >= 3)
    n, reps = int(inp["n"]), int(inp.get("reps") or 500)
    rng = np.random.default_rng(inp.get("seed", 1))
    chol = np.linalg.cholesky(sigma + 1e-10 * np.eye(len(sigma)))
    hits = {h["number"]: 0 for h in spec["hyps"]}
    failed = 0
    ids = {v["name"]: v for v in variables}
    for rep in range(reps):
        x = rng.standard_normal((n, len(cols))) @ chol.T
        try:
            fit = sem_table.fit(x, cols, tab, same=same, robust=False)
            if not fit.get("converged") or not fit.get("admissible"):
                raise sem.SemError("converge")
        except (sem.SemError, np.linalg.LinAlgError, ValueError, ArithmeticError):
            failed += 1
        else:
            for h in spec["hyps"]:
                a, b = ids[h["from"]], ids[h["to"]]
                lhs = f"w_{b['id']}_t2" if kind == "riclpm" else f"{b['id']}_t2"
                rhs = f"w_{a['id']}_t1" if kind == "riclpm" else f"{a['id']}_t1"
                row = sem_table.find(fit, "~", lhs, rhs)
                if row and _hit(row["p"], row["std"], h.get("direction")):
                    hits[h["number"]] += 1
        if progress and (rep + 1) % 25 == 0:
            progress((rep + 1) / reps)
    return {"key": inp["key"], "n": n, "reps": reps, "failed": failed, "kind": kind,
            "power": [[number, hits[number] / reps] for number in hits]}


def check_population(names, waves, betas):
    """Whether the planned population is a valid covariance matrix (positive definite) with variance 1 everywhere."""
    if too_large(names, waves, betas):
        return False
    sigma, _ = population(names, waves, betas)
    return bool(np.all(np.isfinite(sigma)) and np.linalg.eigvalsh(sigma)[0] > 1e-8 and not math.isnan(sigma.sum()))
