"""Van Westendorp analysis of a PSM item (items/psm.py), with the Newton-Miller-Smith extension.

Cases: one per person (per loop pass) with all four prices; only consistent cases
(TC <= C <= E <= TE, the weak order: a "cheap" and an "expensive" at the same rounded price is no contradiction;
audit 2026-10-05) enter the curves, inconsistent ones are counted and reported with their share.

Curves over every price p that occurs in the answers (shares of the consistent cases):
  too cheap       TC > p  (falls)           not cheap       C < p  (rises)
  too expensive   TE < p  (rises)           not expensive   E > p  (falls)
An answer exactly at p counts half on each side (mid-distribution), so "not cheap" is the exact
complement of "cheap" (C >= p, i.e. p is still cheap) and all four curves treat ties alike; a
one-sided rule (C <= p but E >= p) moved the price points with rounded answers.
Price points are the intersections, linearly interpolated between neighbouring prices; where two
curves run level over several prices, the middle of that stretch:
  PMC  point of marginal cheapness      too cheap × not cheap
  OPP  optimal price point              too cheap × too expensive
  IPP  indifference price point         not cheap × not expensive
  PME  point of marginal expensiveness  too expensive × not expensive
The range of acceptable prices runs from PMC to PME.

Newton-Miller-Smith: each person's purchase probability is 0 at their "too cheap" and "too
expensive" price, the calibrated weight of their answer at "cheap" and "expensive", linear in
between and 0 outside. Ties of the weak order (audit 2026-10-05, R-1): at C = E the price itself
and everything above it take the weight of "expensive"; at TC = C and E = TE the price takes the
weight of "cheap" or "expensive" (closed intervals), so a person who calls one price cheap buys there.
Trial = mean probability per price, revenue index = price × trial; the price with the highest trial
and the one with the highest revenue are reported. Between neighbouring prices trial is linear, so
revenue is a parabola there and its maximum can lie between two answered prices; a tie can make the
curve jump at a price, so the straight piece is taken from inside the stretch (its limits at both
ends), not from the values at the two prices."""
import bisect
import math

from app import units
from app.items.base import MAX_NUMBER

KEYS = ("tc", "c", "e", "te")


def _num(v):
    if v in (None, "") or isinstance(v, bool):
        return None
    try:
        number = float(str(v).replace(",", "."))
    except ValueError:
        return None
    return number if math.isfinite(number) and abs(number) <= MAX_NUMBER else None  # prices stored before the limit


def cases(table, item, passes=(), persons=None):
    """[{tc, c, e, te, nms_c, nms_e, person}] from export rows (loop passes are separate cases; person: the number of
    the row, for the bootstrap over persons). persons: the owner of each row where several rows belong to one person
    (the entries of a diary study, results.compute person_ids): their rows are drawn together (audit storage and
    calculations 2026-10-28, M1)."""
    out = []
    for n, r in enumerate(table):
        person = persons[n] if persons is not None else n
        for suffix in ([f"_{n}" for n in passes] or [""]):
            case = {k: _num(r.get(f"{item.code}_{k}{suffix}")) for k in KEYS}
            case.update({f"nms_{k}": _num(r.get(f"{item.code}_nms_{k}{suffix}")) for k in ("c", "e")})
            case["person"] = person
            if any(case[k] is not None for k in KEYS):
                out.append(case)
    return out


def consistent(case):
    """All four prices, none below the one before (TC <= C <= E <= TE)."""
    v = [case.get(k) for k in KEYS]
    return all(x is not None for x in v) and v[0] <= v[1] <= v[2] <= v[3]


def curves(good):
    """{"prices": [...], "too_cheap": [...], "not_cheap": [...], "not_expensive": [...], "too_expensive": [...]}."""
    prices = sorted({c[k] for c in good for k in KEYS})
    n = len(good)

    def share(key, above):
        """Per price the share of answers on the counted side of it, an answer exactly at the price counting
        half. The answers are sorted once and each price is looked up in them; counting every answer at every
        price took seconds where nearly every answer names a price of its own (dummy data)."""
        values = sorted(c[key] for c in good)
        out = []
        for p in prices:
            lo, hi = bisect.bisect_left(values, p), bisect.bisect_right(values, p)  # lo below p, hi - lo at p
            out.append(((n - hi if above else lo) + 0.5 * (hi - lo)) / n)
        return out

    return {"prices": prices,
            "too_cheap": share("tc", True),
            "not_cheap": share("c", False),
            "not_expensive": share("e", True),
            "too_expensive": share("te", False)}


def intersection(prices, falling, rising):
    """Price where a falling and a rising curve cross (first crossing), or None. Where both run
    level over several prices, the middle of that stretch rather than its first price."""
    for i, p in enumerate(prices):
        d = falling[i] - rising[i]
        if d == 0:
            j = i
            while j + 1 < len(prices) and falling[j + 1] == rising[j + 1]:
                j += 1
            return (p + prices[j]) / 2
        if d < 0:
            if i == 0:
                return p
            d0 = falling[i - 1] - rising[i - 1]
            p0 = prices[i - 1]
            return p0 + (p - p0) * d0 / (d0 - d)
    return None


POINTS = (("PMC", "tc", "c"), ("OPP", "tc", "te"), ("IPP", "e", "c"), ("PME", "e", "te"))  # (point, falling, rising)


def _crossing(prices, d):
    """intersection() on numpy arrays, d = falling - rising: the same rules, without a loop over the prices."""
    import numpy as np

    below = np.flatnonzero(d <= 0)
    if not below.size:
        return None
    i = int(below[0])
    if d[i] == 0:
        off = np.flatnonzero(d[i + 1:] != 0)
        j = i + int(off[0]) if off.size else len(d) - 1
        return float((prices[i] + prices[j]) / 2)
    if i == 0:
        return float(prices[0])
    d0 = d[i - 1]
    return float(prices[i - 1] + (prices[i] - prices[i - 1]) * d0 / (d0 - d[i]))


def _points_of(values):
    """The four price points of consistent cases given as arrays {tc, c, e, te}: curves() and intersection() in numpy."""
    import numpy as np

    n = len(values["tc"])
    prices = np.unique(np.concatenate([values[k] for k in KEYS]))
    curve = {}
    for key, above in (("tc", True), ("c", False), ("e", True), ("te", False)):
        v = np.sort(values[key])
        lo, hi = np.searchsorted(v, prices, "left"), np.searchsorted(v, prices, "right")
        curve[key] = ((n - hi if above else lo) + 0.5 * (hi - lo)) / n
    return {name: _crossing(prices, curve[f] - curve[r]) for name, f, r in POINTS}


def point_intervals(good, draws=None, seed=None):
    """95 % percentile bootstrap intervals of the price points (Efron & Tibshirani, 1993): persons are drawn with
    replacement, each with all of their consistent cases (loop passes), and the points are computed anew on the
    price grid of the draw. {point: (low, high) or None}; None below intervals.MIN_BOOT persons."""
    import numpy as np

    from app import intervals

    draws = draws or intervals.PSM_DRAWS
    persons = list(dict.fromkeys(c.get("person", i) for i, c in enumerate(good)))  # ids of rows or of persons
    if len(persons) < intervals.MIN_BOOT:
        return None
    index = {p: i for i, p in enumerate(persons)}
    owner = np.array([index[c.get("person", i)] for i, c in enumerate(good)])
    order = np.argsort(owner, kind="stable")
    arrays = {k: np.array([good[i][k] for i in order], dtype=float) for k in KEYS}
    counts = np.bincount(owner, minlength=len(persons))
    starts = np.concatenate([[0], np.cumsum(counts)[:-1]])
    rng = np.random.default_rng(intervals.SEED)
    found = {name: [] for name, _, _ in POINTS}
    m = len(persons)
    for _ in range(draws):
        pick = rng.integers(0, m, m)
        size = counts[pick]
        # the cases of the drawn persons: their first case plus 0, 1, ... for further loop passes
        rows = np.repeat(starts[pick], size) + (np.arange(size.sum()) - np.repeat(np.cumsum(size) - size, size))
        for name, value in _points_of({k: arrays[k][rows] for k in KEYS}).items():
            found[name].append(value)
    return {name: intervals.percentile(values, draws) for name, values in found.items()}


def probability(case, price, weights):
    """NMS purchase probability (0 … 1) of one person at `price`. Ties (R-1): C = E takes the weight of
    "expensive" at the price, TC = C and E = TE the weight of "cheap" or "expensive" (closed intervals)."""
    wc, we = weights[int(case["nms_c"]) - 1] / 100, weights[int(case["nms_e"]) - 1] / 100
    tc, c, e, te = (case[k] for k in KEYS)
    if price < tc or price > te:
        return 0.0
    if price == c == e:
        return we
    if price <= c:
        return wc if c == tc else 0.0 + (wc - 0.0) * (price - tc) / (c - tc)
    if price <= e:
        return wc + (we - wc) * (price - c) / (e - c)
    return we + (0.0 - we) * (price - e) / (te - e)


NMS_CELLS = 2_000_000  # persons x prices computed at once (16 MB)


def _trial(usable, prices, weights):
    """Mean purchase probability per price: probability() of every person at every price, computed for all
    persons at once per stretch of prices (numpy) instead of one call per person and price. The same
    operations in the same order, and the mean is summed exactly as before, so the numbers are the same."""
    import numpy as np

    tc, c, e, te = (np.array([case[k] for case in usable], dtype=float)[:, None] for k in KEYS)
    wc = np.array([weights[int(case["nms_c"]) - 1] / 100 for case in usable])[:, None]
    we = np.array([weights[int(case["nms_e"]) - 1] / 100 for case in usable])[:, None]
    grid = np.array(prices, dtype=float)
    step = max(NMS_CELLS // len(usable), 1)
    trial = []
    with np.errstate(divide="ignore", invalid="ignore"):  # inconsistent prices (never passed in) divide by zero
        for start in range(0, len(grid), step):
            p = grid[None, start:start + step]
            inside = (p >= tc) & (p <= te)
            values = np.where(~inside, 0.0, np.where((p == c) & (c == e), we, np.where(
                p <= c, np.where(c == tc, wc, 0.0 + (wc - 0.0) * (p - tc) / (c - tc)),
                np.where(p <= e, wc + (we - wc) * (p - c) / (e - c), we + (0.0 - we) * (p - e) / (te - e)))))
            trial += [math.fsum(column) / len(usable) for column in values.T.tolist()]
    return trial


def nms(good, prices, weights):
    usable = [c for c in good if c.get("nms_c") in (1, 2, 3, 4, 5) and c.get("nms_e") in (1, 2, 3, 4, 5)]
    if not usable:
        return None
    trial = _trial(usable, prices, weights)
    revenue = [p * t for p, t in zip(prices, trial)]
    if max(trial, default=0) <= 1e-12:  # nobody would buy at any of the prices: no optimum to report (P-2)
        return {"n": len(usable), "trial": trial, "revenue": revenue, "nobody": True,
                "max_trial": None, "max_revenue": None}
    best_t = max(range(len(prices)), key=lambda i: trial[i])
    # a plateau (the same highest share over several prices) is reported as a span, not as its first price (P-3)
    last_t = best_t
    while last_t + 1 < len(prices) and trial[last_t + 1] >= trial[best_t] - 1e-9:
        last_t += 1
    # the straight piece between two prices from two points inside the stretch: a tie can make the curve
    # jump at a price (R-1), so the values at the prices themselves are not its ends
    inner = [p0 + (p1 - p0) * f for p0, p1 in zip(prices, prices[1:]) for f in (1 / 3, 2 / 3)]
    inside = _trial(usable, inner, weights) if inner else []
    pieces = []  # (p0, p1, trial just right of p0, slope)
    for i, (p0, p1) in enumerate(zip(prices, prices[1:])):
        (a, ta), (b, tb) = zip(inner[2 * i:2 * i + 2], inside[2 * i:2 * i + 2])
        slope = (tb - ta) / (b - a)
        pieces.append((p0, p1, ta + slope * (p0 - a), slope))
    best_r = max(range(len(prices)), key=lambda i: revenue[i])
    top = (prices[best_r], revenue[best_r], trial[best_r], None)
    for p0, p1, t0, slope in pieces:
        if slope < 0:  # p * (t0 + slope * (p - p0)) peaks at its vertex
            p = (slope * p0 - t0) / (2 * slope)
            if p0 < p < p1 and p * (t0 + slope * (p - p0)) > top[1]:
                t = t0 + slope * (p - p0)
                top = (p, p * t, t, None)
    # at a jump (a tie C = E, R-1) the highest value can be a limit of a piece, just below or just above a price:
    # the curve drawn below reaches it, so the figures do too (audit 2026-10-12, W-3)
    limits = [(p1, t0 + slope * (p1 - p0), "below") for p0, p1, t0, slope in pieces] + [
        (p0, t0, "above") for p0, p1, t0, slope in pieces]
    peak = {"price": prices[best_t], "value": trial[best_t], "price_to": prices[last_t] if last_t > best_t else None,
            "side": None}
    for p, t, side in limits:
        if t > peak["value"] + 1e-9:
            peak = {"price": p, "value": t, "price_to": None, "side": side}
        if p * t > top[1] + 1e-9:
            top = (p, p * t, t, side)
    # the curve for the charts: at a jump the limit from the left, the value at the price, the limit from the right
    curve = []
    for i, p in enumerate(prices):
        ends = ([pieces[i - 1][2] + pieces[i - 1][3] * (p - pieces[i - 1][0])] if i else []) + [trial[i]]             + ([pieces[i][2]] if i < len(pieces) else [])
        for t in ends:
            if not curve or curve[-1][0] != p or abs(curve[-1][1] - t) > 1e-12:
                curve.append((p, t))
    return {"n": len(usable), "trial": trial, "revenue": revenue,
            "curve": {"prices": [p for p, _ in curve], "trial": [t for _, t in curve],
                      "revenue": [p * t for p, t in curve]},
            "max_trial": peak,
            "max_revenue": {"price": top[0], "value": top[1], "trial": top[2], "side": top[3]}}


def _axis_range(cv):
    """x-axis from the price where fewer than 98 % call it too cheap to the one where 98 % call it
    too expensive, so single extreme answers do not squash the chart."""
    prices = cv["prices"]
    lo = next((p for p, s in zip(prices, cv["too_cheap"]) if s < 0.98), prices[0])
    hi = next((p for p, s in zip(prices, cv["too_expensive"]) if s >= 0.98), prices[-1])
    lo = prices[max(prices.index(lo) - 1, 0)]
    return (lo, hi) if hi > lo else (prices[0], prices[-1])


MAX_TICKS = 50  # never more ticks on an axis, whatever the range


def nice_ticks(lo, hi, count=6):
    """Round tick values from lo to hi. Counted, not added up (code review 2026-10-07, C6): with a step below the
    resolution of the prices (very large prices on a narrow axis) adding never got past hi, and the tolerance is
    relative to the step, so a tiny span gives no flood of ticks."""
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / count
    mag = 10 ** int(f"{raw:e}".split("e")[1])
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    first = -(-lo // step) * step
    last = math.floor((hi - first) / step + 1e-9)
    ticks = []
    for i in range(min(last, MAX_TICKS - 1) + 1):
        t = round(first + i * step, 10) + 0.0  # + 0.0: no "-0" label
        if not ticks or t != ticks[-1]:  # equal once rounded (below the resolution of large numbers): once
            ticks.append(t)
    return ticks


def analyse(table, item, passes=()):
    """Everything the results page shows for one PSM item, or None without usable answers."""
    all_cases = cases(table, item, passes)
    complete = [c for c in all_cases if all(c[k] is not None for k in KEYS)]
    good = [c for c in complete if consistent(c)]
    out = {"n_cases": len(all_cases), "n_complete": len(complete), "n": len(good),
           "n_inconsistent": len(complete) - len(good), "curves": None, "points": {}, "nms": None,
           # the share of the complete answers that the order rule leaves out, shown next to the count
           "share_inconsistent": (len(complete) - len(good)) / len(complete) if complete else None}
    if len(good) < 2:
        return out
    cv = curves(good)
    p = cv["prices"]
    out["curves"] = cv
    out["points"] = {
        "PMC": intersection(p, cv["too_cheap"], cv["not_cheap"]),
        "OPP": intersection(p, cv["too_cheap"], cv["too_expensive"]),
        "IPP": intersection(p, cv["not_expensive"], cv["not_cheap"]),
        "PME": intersection(p, cv["not_expensive"], cv["too_expensive"]),
    }
    out["range"] = _axis_range(cv)
    if item.kind.nms(item):
        out["nms"] = nms(good, p, item.kind.nms_weights(item))
    return out


# ---- chart geometry (inline SVG on the results page) ----

W, H = 640, 300
# Points a chart and the table of the curves show at most (performance audit 2026-10-06, P9): with a price of its own
# for nearly every answer (5000 persons) the curves had thousands of points, 1.3 MB of the page, though the plot is
# 580 px wide. The price points, NMS optima and intervals are still computed from every price.
MAX_POINTS = 300


def thin(indices, most=MAX_POINTS):
    """At most `most` of the indices, evenly spread over them, the first and the last always among them."""
    indices = list(indices)
    if len(indices) <= most:
        return indices
    step = (len(indices) - 1) / (most - 1)
    return [indices[round(j * step)] for j in range(most)]
PAD = {"l": 44, "r": 16, "t": 34, "b": 36}  # price point labels sit above the plot


def chart(prices, series, x_range, y_max=1.0, markers=None):
    """Polylines in SVG coordinates. series: [(key, values)]; markers: {label: price}."""
    lo, hi = x_range
    y_max = y_max or 1.0
    iw, ih = W - PAD["l"] - PAD["r"], H - PAD["t"] - PAD["b"]

    def x(v):
        return PAD["l"] + (min(max(v, lo), hi) - lo) / (hi - lo or 1) * iw

    def y(v):
        return PAD["t"] + ih - v / y_max * ih

    idx = thin(i for i, pr in enumerate(prices) if lo <= pr <= hi)
    lines = []
    for key, values in series:
        pts = " ".join(f"{x(prices[i]):.1f},{y(values[i]):.1f}" for i in idx)
        end = idx[-1] if idx else 0
        lines.append({"key": key, "points": pts, "end_x": x(prices[end]), "end_y": y(values[end]),
                      "start_x": x(prices[idx[0]]) if idx else 0, "start_y": y(values[idx[0]]) if idx else 0})
    # direct labels: falling curves at their left end, rising ones at their right end, pushed apart
    for side in ("start", "end"):
        group = sorted((ln for ln in lines if (ln["start_y"] <= ln["end_y"]) == (side == "start")),
                       key=lambda ln: ln[f"{side}_y"])
        last = None
        for ln in group:
            ly = max(ln[f"{side}_y"] - 5, PAD["t"] + 10)
            if last is not None and ly < last + 14:
                ly = last + 14
            ln["label_x"], ln["label_y"], ln["anchor"] = (ln[f"{side}_x"] + 4, ly, "start") if side == "start" \
                else (ln[f"{side}_x"] - 4, ly, "end")
            last = ly
    y_values = [0, 0.25, 0.5, 0.75, 1.0] if y_max == 1.0 else nice_ticks(0, y_max, 4)
    places = next((d for d in range(5) if all(abs(round(v, d) - v) < 1e-9 for v in y_values)), 4)
    y_ticks = [(y(v), v, units.number(v, places)) for v in y_values]  # label with as many decimals as the step needs
    # every price on the axis with the same decimals, as many as the finest tick needs (0,125 not 0,12; P-1)
    x_values = nice_ticks(lo, hi)
    x_ticks = list(zip((x(v) for v in x_values), units.tick_labels(x_values, most=4)))
    marks = [{"label": k, "price": v, "x": x(v)} for k, v in (markers or {}).items() if v is not None and lo <= v <= hi]
    for n, m in enumerate(sorted(marks, key=lambda m: m["x"])):
        m["label_y"] = PAD["t"] - 20 + 12 * (n % 2)  # above the plot; neighbours alternate in height
    data = [[prices[i]] + [values[i] for _, values in series] for i in idx]
    # the values of the ticks too: the page writes their labels in its language (Mathe-Audit 2026-10-06, Z-2); the
    # German labels stay for results kept from before
    return {"w": W, "h": H, "pad": PAD, "lines": lines, "x_ticks": x_ticks, "y_ticks": y_ticks, "marks": marks,
            "x_values": [float(v) for v in x_values],
            "base_y": y(0), "top_y": PAD["t"], "data": data, "x_lo": x(lo), "x_hi": x(hi)}
