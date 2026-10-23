"""The power of the hypotheses of a planned structural equation model by simulation (SEM extensions 2026-10-17, point
16; Muthén & Muthén, 2002; Wang & Rhemtulla, 2021): data sets of the planned size drawn from the planned population
(app/sem.population: loadings .70, the expected standardised effects), each estimated, and per hypothesis the share
of data sets in which its test comes out significant in the expected direction. Run like the bootstrap in the
estimation process or on the Rechenknecht (app/jobs.py); no database, numbers only.

Input: {"key", "spec", "betas": [[a, b, beta]], "hyps": [{"number", "kind", "paths": [[a, b]], "cov": [[a, b]],
"direction"}], "n", "reps", "seed"}. Output: {"key", "n", "reps", "failed", "power": [[number, share | None]]}."""
import numpy as np

from app import sem

MUTHEN_MUTHEN = ("Muthén, L. K., & Muthén, B. O. (2002). How to use a Monte Carlo study to decide on sample size and "
                 "determine power. Structural Equation Modeling, 9(4), 599–620. https://doi.org/10.1207/S15328007SEM0904_8")
WANG_RHEMTULLA = ("Wang, Y. A., & Rhemtulla, M. (2021). Power analysis for parameter estimation in structural equation "
                  "modeling: A discussion and tutorial. Advances in Methods and Practices in Psychological Science, 4(1). "
                  "https://doi.org/10.1177/2515245920918253")
SOURCES = (MUTHEN_MUTHEN, WANG_RHEMTULLA)
REPS = 500
ALPHA = 0.05


def _hit(p, value, direction, alpha):
    """Significant in the expected direction ("pos", "neg" or none: either)."""
    if p is None or p >= alpha:
        return False
    return not ((direction == "pos" and value <= 0) or (direction == "neg" and value >= 0))


def judge(fit, h, alpha=ALPHA):
    """Whether the test of one hypothesis is significant in this estimate (as app/sem_model.apply judges it)."""
    paths = {(r["from"], r["to"]): r for r in fit.get("paths") or []}
    rows = [paths.get(tuple(p)) for p in h.get("paths") or []]
    if h.get("cov"):
        covs = {frozenset((r["a"], r["b"])): r for r in fit.get("covariances") or []}
        r = covs.get(frozenset(h["cov"][0]))
        return _hit(r["p"], r["std"], h.get("direction"), alpha) if r else False
    if any(r is None for r in rows) or not rows:
        return False
    if h.get("kind") == "mediation" and len(rows) == 2:  # joint significance of both paths (MacKinnon et al., 2002)
        value = rows[0]["std"] * rows[1]["std"]
        return all(r["p"] is not None and r["p"] < alpha for r in rows) and _hit(max(r["p"] for r in rows), value,
                                                                                h.get("direction"), alpha)
    if len(rows) == 1:
        return _hit(rows[0]["p"], rows[0]["std"], h.get("direction"), alpha)
    found = sem.wald(fit, [tuple(p) for p in h["paths"]])  # several paths of one hypothesis: tested together
    return bool(found and found[2] is not None and found[2] < alpha)


def run(inp, progress=None):
    if inp.get("panel"):  # the hypotheses "Wirkung über die Zeit" (app/panel_power.py)
        from app import panel_power

        return panel_power.run(inp, progress)
    spec = sem.check({**inp["spec"], "paths": [tuple(p) for p in inp["spec"]["paths"]],
                      "indirect": [tuple(t) for t in inp["spec"].get("indirect") or []],
                      **({"cov": [tuple(c) for c in inp["spec"]["cov"]]} if inp["spec"].get("cov") else {})})
    betas = {(a, b): float(v) for a, b, v in inp["betas"]}
    sigma = sem.population(spec, betas)
    if sigma is None:
        return {"key": inp["key"], "error": "population"}
    cols = sem.columns(spec)
    n, reps = int(inp["n"]), int(inp.get("reps") or REPS)
    rng = np.random.default_rng(inp.get("seed", 1))
    chol = np.linalg.cholesky(sigma + 1e-12 * np.eye(len(sigma)))
    hits = {h["number"]: 0 for h in inp["hyps"]}
    failed = 0
    for rep in range(reps):
        x = rng.standard_normal((n, len(cols))) @ chol.T
        try:
            fit = sem.fit_ml(x, cols, spec, robust=False)
            if not fit.get("converged") or not fit.get("admissible"):
                raise sem.SemError("converge")
        except (sem.SemError, np.linalg.LinAlgError, ValueError, ArithmeticError):
            failed += 1  # counts as not significant: the planned N should also give a solution
        else:
            for h in inp["hyps"]:
                if judge(fit, h):
                    hits[h["number"]] += 1
        if progress and (rep + 1) % 25 == 0:
            progress((rep + 1) / reps)
    return {"key": inp["key"], "n": n, "reps": reps, "failed": failed,
            "power": [[number, hits[number] / reps] for number in hits]}
