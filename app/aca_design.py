"""Adaptive conjoint (ACA): the arithmetic of the interview and of the part-worths (app/items/aca.py; concept
aca-cva-konzept.md, A2 to A4; Johnson, 1987; Green, Krieger & Agarwal, 1991).

Prior (self-explicated) part-worths: per attribute the desirabilities (the person's ratings of the levels, or for an
attribute with a fixed order the position: best level highest), centred, scaled to a range of 1 and multiplied by the
importance rating, rescaled to 1 to 4 (Johnson, 1987). So the ranges of the attributes are proportional to their
importance.

Pairs: two partial profiles of `size` attributes each (the first half of the pairs with the first size, then the
second; Askurious default two, then three: small pairs are easy to judge at first, larger ones carry more
information once the estimate has settled). Before every pair the estimate is updated by a regression of the
answers so far on the differences of the two concepts, augmented with one row per level that pulls the level towards
its prior (identity augmentation, Bayesian in effect). A pair takes the attributes shown least often
together so far, and among sampled level combinations the one whose two sides are nearest in utility (utility
balance; Johnson, 1987), never two identical levels of an attribute and, where possible, no dominated pair.
Everything is drawn from the seed of the person and the number of the pair, so a page can be built again from the
stored answer alone (replay, as for ACBC).

Final part-worths (Johnson, 1987; Green, Krieger & Agarwal, 1991): the pairs regression (identity-augmented,
without the prior) and the prior, both normalised to the same sum of attribute ranges, combined with the weights
n / (n + t) for the prior and t / (n + t) for the pairs (n levels, t answered pairs; Askurious default: the prior
counts like one answer per level, so the pairs take over as they grow), centred per attribute. With calibration
concepts (at least three answered): the logit of the purchase likelihood (5 to 95) regressed on the concept utility;
the part-worths are multiplied by the slope (at least 0.00001), so their sums approximate the logit of the likelihood."""
import math
import random

import numpy as np

JOHNSON = ("Johnson, R. M. (1987). Adaptive conjoint analysis. In Sawtooth Software Conference Proceedings "
           "(pp. 253–265). Sawtooth Software.")
GREEN_KRIEGER_AGARWAL = ("Green, P. E., Krieger, A. M., & Agarwal, M. K. (1991). Adaptive conjoint analysis: Some caveats "
                         "and suggestions. Journal of Marketing Research, 28(2), 215–222. "
                         "https://doi.org/10.1177/002224379102800207")
PINNELL = ("Pinnell, J. (1994). Multistage conjoint methods to measure price sensitivity. In Advanced Research Techniques "
           "Forum. American Marketing Association.")
SAMPLES = 300          # level combinations sampled per pair
MAX_PAIRS = 50         # Askurious default: more pairs tire the participant without changing the part-worths much
CAL_TRIM = (5.0, 95.0)


def suggested_pairs(levels):
    """Askurious default 3 (N - n - 1) - N (N levels, n attributes), within 6 and MAX_PAIRS: about three answers per
    part-worth beyond the self-explication, less the levels the prior already covers."""
    N, n = sum(levels), len(levels)
    return max(6, min(MAX_PAIRS, 3 * (N - n - 1) - N))


def importance_weight(rating, points):
    """An importance rating of 1..points rescaled to 1..4 (Johnson, 1987); a missing one counts as the middle."""
    if rating is None or points < 2:
        return 2.5
    return 1.0 + 3.0 * (rating - 1) / (points - 1)


def desirability(a, attr, ratings):
    """The raw desirabilities of attribute a: its ratings, or its order (best_first: the first level best)."""
    n = len(attr["levels"])
    order = attr.get("aca_order") or ""
    if order == "best_first":
        return [float(n - 1 - i) for i in range(n)]
    if order == "best_last":
        return [float(i) for i in range(n)]
    row = (ratings or {}).get(attr["id"]) or []
    values = [row[i] if i < len(row) and isinstance(row[i], (int, float)) else None for i in range(n)]
    known = [v for v in values if v is not None]
    mean = sum(known) / len(known) if known else 0.0
    return [float(v) if v is not None else mean for v in values]


def prior(attrs, answer, imp_points):
    """Prior part-worths per attribute (lists of floats)."""
    out = []
    ratings = (answer or {}).get("r") or {}
    imps = (answer or {}).get("i") or {}
    for a, attr in enumerate(attrs):
        d = desirability(a, attr, ratings)
        mean = sum(d) / len(d)
        c = [x - mean for x in d]
        span = max(c) - min(c)
        w = importance_weight(imps.get(attr["id"]), imp_points)
        out.append([x / span * w if span > 0 else 0.0 for x in c])
    return out


def _offsets(attrs):
    off, out = 0, []
    for attr in attrs:
        out.append(off)
        off += len(attr["levels"])
    return out, off


def pair_row(attrs, pair):
    """Level indicators of a pair: +1 for the levels of the first concept, -1 for those of the second."""
    offsets, total = _offsets(attrs)
    x = np.zeros(total)
    for side, sign in ((0, 1.0), (1, -1.0)):
        for a, lv in pair[side].items():
            x[offsets[a] + lv] += sign
    return x


def update(attrs, pw_prior, pairs, answers):
    """The estimate during the interview: regression of the answers on the pairs, pulled to the prior by one row per
    level (solve (D'D + I) u = D'y + u0)."""
    u0 = np.array([x for row in pw_prior for x in row])
    if not pairs:
        return u0
    D = np.array([pair_row(attrs, p) for p in pairs])
    y = np.array(answers, dtype=float)
    return np.linalg.solve(D.T @ D + np.eye(len(u0)), D.T @ y + u0)


def _split(attrs, u):
    offsets, _ = _offsets(attrs)
    return [list(u[o:o + len(attr["levels"])]) for o, attr in zip(offsets, attrs)]


def choose_pair(attrs, u, history, size, seed, k, allowed):
    """The k-th pair: ({attr index: level}, {attr index: level}). history: earlier pairs (for the balance of
    attributes and levels); allowed(profile dict) -> bool for the prohibitions within one concept."""
    rng = random.Random(f"aca:{seed}:{k}")
    usable = [a for a, attr in enumerate(attrs) if len(attr["levels"]) >= 2]
    size = max(1, min(size, len(usable)))
    used = {a: 0 for a in usable}
    together = {}
    level_used = {}
    for p in history:
        keys = sorted(p[0])
        for a in keys:
            used[a] = used.get(a, 0) + 1
            for side in p:
                level_used[(a, side[a])] = level_used.get((a, side[a]), 0) + 1
        for i in keys:
            for j in keys:
                if i < j:
                    together[(i, j)] = together.get((i, j), 0) + 1
    order = sorted(usable, key=lambda a: (used.get(a, 0), rng.random()))
    chosen = [order[0]]
    while len(chosen) < size:
        rest = [a for a in usable if a not in chosen]
        best = min(rest, key=lambda a: (sum(together.get((min(a, b), max(a, b)), 0) for b in chosen), used.get(a, 0),
                                        rng.random()))
        chosen.append(best)
    chosen.sort()
    pw = _split(attrs, u)
    best, best_score = None, None
    for _ in range(SAMPLES):
        left, right = {}, {}
        for a in chosen:
            n = len(attrs[a]["levels"])
            x, y = rng.sample(range(n), 2)
            left[a], right[a] = x, y
        if not (allowed(left) and allowed(right)):
            continue
        diffs = [pw[a][left[a]] - pw[a][right[a]] for a in chosen]
        dominated = len(chosen) > 1 and (all(d > 1e-9 for d in diffs) or all(d < -1e-9 for d in diffs))
        spread = sum(level_used.get((a, left[a]), 0) + level_used.get((a, right[a]), 0) for a in chosen)
        score = (dominated, abs(sum(diffs)) + 0.02 * spread)
        if best_score is None or score < best_score:
            best, best_score = (left, right), score
    if best is None:  # every sample hit a prohibition: the first allowed levels
        left, right = {}, {}
        for a in chosen:
            left[a], right[a] = 0, 1
        best = (left, right)
    return best


def pairs_estimate(attrs, pairs, answers):
    """The pairs regression with identity augmentation (no prior): (D'D + I) u = D'y."""
    _, total = _offsets(attrs)
    if not pairs:
        return np.zeros(total)
    D = np.array([pair_row(attrs, p) for p in pairs])
    y = np.array(answers, dtype=float)
    return np.linalg.solve(D.T @ D + np.eye(total), D.T @ y)


def _sum_ranges(pw):
    return sum(max(r) - min(r) for r in pw)


def final(attrs, pw_prior, pairs, answers):
    """The combined part-worths (zero-centred per attribute) before calibration."""
    _, n = _offsets(attrs)
    t = len(pairs)
    prior_rows = [list(r) for r in pw_prior]
    pairs_rows = _split(attrs, pairs_estimate(attrs, pairs, answers))
    target = _sum_ranges(prior_rows) or 1.0
    got = _sum_ranges(pairs_rows)
    if got > 0:
        pairs_rows = [[x * target / got for x in r] for r in pairs_rows]
    out = []
    for p_row, q_row in zip(prior_rows, pairs_rows):
        row = [p * n / (n + t) + q * t / (n + t) for p, q in zip(p_row, q_row)]
        mean = sum(row) / len(row)
        out.append([float(x - mean) for x in row])
    return out


def calibrate(pw, concepts, likelihoods):
    """(slope, intercept, r2) of logit(likelihood) on the concept utility, or None with fewer than three answers."""
    xs, ys = [], []
    for concept, p in zip(concepts, likelihoods):
        if p is None:
            continue
        p = min(max(float(p), CAL_TRIM[0]), CAL_TRIM[1])
        xs.append(sum(pw[a][lv] for a, lv in concept.items()))
        ys.append(math.log(p / (100 - p)))
    if len(xs) < 3:
        return None
    x, y = np.array(xs), np.array(ys)
    vx = ((x - x.mean()) ** 2).sum()
    if vx <= 0:
        return 0.00001, float(y.mean()), 0.0
    b = float(((x - x.mean()) * (y - y.mean())).sum() / vx)
    a = float(y.mean() - b * x.mean())
    sst = float(((y - y.mean()) ** 2).sum())
    r2 = 0.0 if sst == 0 else max(0.0, 1 - float(((y - a - b * x) ** 2).sum()) / sst)
    if b < 0.00001:
        return 0.00001, a, 0.0
    return b, a, r2


def calibration_concepts(attrs, pw, count, size, seed, allowed):
    """The concepts of the calibration (Johnson, 1987): the `size` most important attributes; first the worst, then the best
    combination, then concepts of middling attractiveness."""
    if count < 1:
        return []
    rng = random.Random(f"aca-cal:{seed}")
    ranges = [max(r) - min(r) for r in pw]
    top = sorted(range(len(attrs)), key=lambda a: (-ranges[a], a))[:max(1, min(size, len(attrs)))]
    worst = {a: min(range(len(pw[a])), key=lambda i: pw[a][i]) for a in top}
    best = {a: max(range(len(pw[a])), key=lambda i: pw[a][i]) for a in top}
    out = [worst, best]
    lo = sum(pw[a][worst[a]] for a in top)
    hi = sum(pw[a][best[a]] for a in top)
    for k in range(count - 2):
        target = lo + (hi - lo) * (k + 1) / (count - 1)
        pick, pick_score = None, None
        for _ in range(SAMPLES):
            c = {a: rng.randrange(len(pw[a])) for a in top}
            if c in out or not allowed(c):
                continue
            score = abs(sum(pw[a][c[a]] for a in top) - target)
            if pick_score is None or score < pick_score:
                pick, pick_score = c, score
        out.append(pick or {a: rng.randrange(len(pw[a])) for a in top})
    return out[:count]


def importances(pw):
    ranges = [max(r) - min(r) for r in pw]
    total = sum(ranges)
    return [r / total if total > 0 else None for r in ranges]
