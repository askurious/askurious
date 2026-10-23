"""Predicted utilities of choice alternatives from an estimated model (holdout checks; the market simulator
uses the same rule).

The utility of a concept is the sum of the part-worths of its levels (effects-coded attributes), plus the price
coefficient per currency unit times (price - centre of the price levels) when the price enters linearly, plus the
constant (ASC) for the "none" alternative alone. Share of preference: the logit rule exp(U_j) / sum exp(U_k) per
person, averaged over persons; first choice: the alternative with the highest utility (ties split evenly).

A model is a dict {"pw": {attribute index: [part-worth per level]}, "price": coefficient per currency unit or None,
"center": centre of the price, "asc": constant of "none" or None}; hb_models() and pooled_model() build them from a
stored HB result (app/conjoint_hb.py) and from the aggregate logit (app/cbc_analysis.py estimate()).

With HB the shares of a person are averaged over a sample of the kept draws of the person's utilities (draw_shares;
Rossi, Allenby & McCulloch, 2005: predictions as means over the posterior draws), not computed from the posterior
means: those are shrunken towards the mean of all persons, so persons look more alike than they are and the shares
lean towards the product of the majority (audit 2026-10-05, W-1: +3 to +4 percentage points for the leading product,
first choice +16 in one case). The market simulator (app/simulator.py) averages the per-person shares with
mean_shares(): optionally weighted, with a percentile bootstrap over persons (resampled with replacement, DRAWS draws,
fixed SEED) for the 95 % interval. That interval holds only the sampling spread over persons: the uncertainty of each
person's utilities is averaged into the shares, not added to the interval.
"""
import math

import numpy as np

DRAWS = 1000
SEED = 1


def concept_utility(model, profile, price=None):
    """Utility of one concept: profile = level index per attribute (config order); price in currency units for a
    linear price attribute (None: that attribute counts as part-worths, if it has any)."""
    u = sum(values[profile[a]] for a, values in model["pw"].items())
    if model.get("price") is not None and price is not None:
        u += model["price"] * (price - model.get("center", 0.0))
    return u


def none_utility(model):
    """Utility of "none of these" (the constant), 0 without one."""
    return model.get("asc") or 0.0


def task_utilities(model, profiles, prices=None, with_none=False):
    """Utilities of the alternatives of one task (and of "none" last)."""
    prices = prices or [None] * len(profiles)
    out = [concept_utility(model, p, x) for p, x in zip(profiles, prices)]
    return out + [none_utility(model)] if with_none else out


def shares(utilities):
    """Logit shares (share of preference of one person)."""
    top = max(utilities)
    e = [math.exp(u - top) for u in utilities]
    total = sum(e)
    return [x / total for x in e]


def first_choice(utilities):
    """1 for the alternative with the highest utility, ties split evenly."""
    top = max(utilities)
    best = [j for j, u in enumerate(utilities) if u >= top - 1e-12]
    return [1 / len(best) if j in best else 0.0 for j in range(len(utilities))]


def draw_shares(U):
    """U: utilities draws x persons x alternatives. (share of preference per person: the logit rule at every draw,
    averaged over the draws; first choice per person: the share of draws in which the alternative has the highest
    utility, ties split evenly), each persons x alternatives."""
    U = np.asarray(U, dtype=float)
    V = np.exp(U - U.max(axis=2, keepdims=True))
    P = (V / V.sum(axis=2, keepdims=True)).mean(axis=0)
    F = (U >= U.max(axis=2, keepdims=True) - 1e-12).astype(float)
    F = (F / F.sum(axis=2, keepdims=True)).mean(axis=0)
    return P, F


def draw_utilities(params, spans, profiles, prices=None, with_none=False, center=0.0):
    """Utilities draws x persons x alternatives of one task (and of "none" last) from conjoint_hb.draw_parameters():
    the same rule as concept_utility, at every draw."""
    util, price, asc = params["util"], params.get("price"), params.get("asc")
    prices = prices or [None] * len(profiles)
    out = []
    for profile, x in zip(profiles, prices):
        u = np.zeros(util.shape[:2])
        for a, start, _ in spans:
            if profile[a] is not None:
                u = u + util[:, :, start + profile[a]]
        if price is not None and x is not None:
            u = u + price * (x - center)
        out.append(u)
    if with_none:
        out.append(asc if asc is not None else np.zeros(util.shape[:2]))
    return np.stack(out, axis=2)


def hb_models(result):
    """{pid: model} of a stored conjoint HB result (posterior means per person)."""
    out = {}
    for n, pid in enumerate(result["pids"]):
        util = result["util"][n]
        out[pid] = {"pw": {a: util[start:start + count] for a, start, count in result["spans"]},
                    "price": result["price"][n] if result.get("price") is not None else None,
                    "center": (result.get("coding") or {}).get("price_center") or 0.0,
                    "asc": result["asc"][n] if result.get("asc") is not None else None}
    return out


def pooled_model(model, attribute_codes, price_center=0.0):
    """The model of the aggregate logit (choice_model.summarise() result); attribute_codes: code per attribute in
    config order (its rows name attributes by code)."""
    index = {code: a for a, code in enumerate(attribute_codes)}
    pw = {index[row["code"]]: [lv["beta"] for lv in row["levels"]] for row in model["attributes"] if row["code"] in index}
    price = (model.get("price") or {}).get("beta")
    return {"pw": pw, "price": price, "center": price_center, "asc": model.get("asc")}


BLOCK = 1_000_000  # bootstrap_means: numbers of the count matrix drawn at once


def _weighted_only(P, weights):
    """(P, weights) as arrays, with weights only the persons of a weight above 0: a person without a weight
    (weighting.pids misses them) drawn into a bootstrap sample took the place of a person who counts and made the
    interval too wide (code review 2026-10-15, C12)."""
    P = np.asarray(P, dtype=float)
    if weights is None:
        return P, np.ones(len(P))
    w = np.asarray(weights, dtype=float)
    keep = w > 0
    return P[keep], w[keep]


def mean_shares(P, weights=None, draws=DRAWS, seed=SEED):
    """(mean, lower, upper) per alternative of the person shares P (persons x alternatives), weighted if weights are
    given; lower and upper: 2.5th and 97.5th percentile of the mean over `draws` bootstrap samples of persons, None
    for a single row (the pooled model) or draws=0."""
    P, w = _weighted_only(P, weights)
    n = len(P)
    if n == 0 or w.sum() <= 0:
        return None, None, None
    mean = w @ P / w.sum()
    if n < 2 or not draws:
        return mean, None, None
    lo, hi = np.percentile(bootstrap_means(P, None if weights is None else w, draws, seed), [2.5, 97.5], axis=0)
    return mean, lo, hi


def bootstrap_means(P, weights=None, draws=DRAWS, seed=SEED):
    """The (weighted) mean of the person shares P (persons x alternatives) in each of `draws` bootstrap samples of
    persons: samples x alternatives. The same samples for every column, so differences between columns and ranks
    within a sample are paired (the interval of mean_shares comes from the same samples)."""
    P, w = _weighted_only(P, weights)
    n = len(P)
    rng = np.random.default_rng(seed)
    share = np.full(n, 1.0 / n)
    out = []
    # how often each person is drawn in each sample (the same as resampling indices, without a draws x n x J array),
    # in blocks of about BLOCK numbers: draws x n at once took 160 MB with 10 000 persons (code review 2026-10-15,
    # A8); the same random stream, so the same samples as in one piece
    step = max(1, BLOCK // max(n, 1))
    for start in range(0, draws, step):
        cw = rng.multinomial(n, share, size=min(step, draws - start)) * w
        totals = cw.sum(axis=1)
        ok = totals > 0
        out.append((cw[ok] @ P) / totals[ok, None])
    return np.concatenate(out) if out else np.empty((0, P.shape[1]))
