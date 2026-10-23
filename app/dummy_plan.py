"""Dummy answers with the structure the study plan expects (Ralf, 2026-10-07): without it every dummy answers each
question at random, so the questions do not hang together, a structural equation model collapses and every
hypothesis shows noise. With a plan, the answers to the variables of its hypotheses are drawn from the population
the power analysis uses (sem.population: every construct with standardised loadings of sem.PLAN_LOADING, a path
with the expected effect of its hypothesis, else DEFAULT_BETA) and rounded to the answer points. Everything else
stays random (app/dummy.py).

Limits: a randomisation in the plan stays independent of the answers (its condition is drawn by the flow, not
here); a nominal question with more than two answers, free text and the like keep their random answers."""
import math

DEFAULT_BETA = 0.3   # a hypothesis without an expected effect: a medium path (Festlegung von Askurious)
MAX_BETA = 0.7       # an expected effect above this is taken as 0.7, so the planned model stays possible
DIRECT = ("sem", "r", "rho")  # analyses whose expected effect is a standardised path or a correlation


def _beta(h):
    """The standardised path a hypothesis plans: its expected effect where that is a path or a correlation, else
    DEFAULT_BETA; negative for a negative direction."""
    value = h.get("effect")
    beta = abs(float(value)) if value is not None and h.get("analysis") in DIRECT else DEFAULT_BETA
    beta = min(beta, MAX_BETA)
    return -beta if h.get("direction") == "neg" else beta


def _cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _writers(model, cols):
    """{column: (item id, form field, function z -> form value)} for the columns that get planned answers."""
    out = {}
    for code, info in model["vars"].items():
        if "error" in info or "item" not in info:
            continue  # a randomisation, or what cannot enter: stays random
        item = info["item"]
        c = item.config or {}
        key = f"i{item.id}"
        if info["kind"] == "latent":
            rows = c.get("statements") or c.get("pairs") or []
            points = int(c.get("points") or 5)
            name = getattr(item.kind, "statement_code", None) or getattr(item.kind, "pair_code", None)
            for i, r in enumerate(rows):
                column = name(item, i, r) if name else r.get("code")
                if column not in cols:
                    continue

                def answer(z, points=points, rev=bool(r.get("reverse"))):
                    cat = min(points, int(_cdf(z) * points) + 1)
                    return str(points + 1 - cat if rev else cat)
                out[column] = (item.id, f"{key}_{r['id']}", answer)
        elif code in cols:
            if info.get("coding"):
                zero, one = info["coding"]
                out[code] = (item.id, key, lambda z, zero=zero, one=one: one if z > 0 else zero)
            elif item.type == "rating":
                lo, hi = item.kind.bounds(item)

                def scaled(z, lo=lo, hi=hi, rev=bool(c.get("reverse"))):
                    # the export (and so hypotheses and SEM) flips a reversed scale: draw the
                    # mirror so the planned direction survives, as for reversed Likert statements
                    x = int(round(lo + _cdf(z) * (hi - lo)))
                    return str(int(round(lo + hi - x)) if rev else x)
                out[code] = (item.id, key, scaled)
            elif item.type == "text_input" and c.get("validation") in ("integer", "decimal"):
                lo = c.get("min") if c.get("min") is not None else 0
                hi = c.get("max") if c.get("max") is not None else lo + 100
                whole = c.get("validation") == "integer"
                out[code] = (item.id, key, lambda z, lo=lo, hi=hi, whole=whole:
                             str(int(round(lo + _cdf(z) * (hi - lo)))) if whole else f"{lo + _cdf(z) * (hi - lo):.2f}")
            elif item.type == "single_choice":
                codes = [o["code"] for o in c.get("options") or [] if not o.get("missing") and not o.get("text")]
                if len(codes) >= 2:
                    out[code] = (item.id, key, lambda z, codes=codes: codes[min(len(codes) - 1, int(_cdf(z) * len(codes)))])
    return out


def rows(survey, n, seed=None):
    """[{item id: {form field: value}}] for n dummies, or None when the plan gives no structure to draw from."""
    import numpy as np

    from app import sem, sem_model, study_plan

    if not survey.study_plan or n <= 0:
        return None
    try:
        model = sem_model.build(survey, every=True)
    except Exception:  # a plan the model cannot read: random answers as before
        return None
    if not model or model.get("error") or model.get("mode") != "structural":
        return None
    betas = {}
    for number, h in enumerate(study_plan.plan_of(survey)["hypotheses"], 1):
        entry = model["hyps"].get(number)
        if entry and not entry.get("errors"):
            for pair in entry.get("paths") or []:
                betas.setdefault(tuple(pair), _beta(h))
    if not betas:
        return None
    spec = model["spec"]
    sigma, scale = None, 1.0
    while sigma is None and scale > 0.05:  # paths that leave a target no room are scaled down together
        sigma = sem.population(spec, {k: v * scale for k, v in betas.items()})
        scale *= 0.7
    if sigma is None:
        return None
    cols = sem.columns(spec)
    writers = _writers(model, cols)
    if not writers:
        return None
    rng = np.random.default_rng(seed)
    z = rng.multivariate_normal(np.zeros(len(cols)), sigma, size=n, method="eigh")
    out = []
    for row in z:
        planned = {}
        for k, col in enumerate(cols):
            if col in writers:
                item_id, field, answer = writers[col]
                planned.setdefault(item_id, {})[field] = answer(float(row[k]))
        out.append(planned)
    return out
