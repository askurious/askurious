"""Results page (work in progress): descriptive statistics on the same table as the data export.

Building on export.rows() means loops, conditions and branches come for free: every loop pass is
its own column (code_1, code_2, ...), and questions skipped by a condition are simply missing.
How a question is summarised is a property of its type (ItemType.result_kind):
  "auto"         numbers: n, M, Md, SD, min, max, Gini coefficient; other values: number answered
  "categorical"  frequencies with value labels from the codebook, plus the Gini-Simpson index
  "scale"        like "auto" per statement, plus Cronbach's alpha and the scale mean (complete cases)
The measurement level of a column (ItemType.column_level, set per question in the editor) narrows this:
  nominal        frequencies and mode, never means
  ordinal        frequencies with cumulative shares, median, quartiles and range; no M, SD, alpha or scale mean
  metric         as above (M, Md, SD, ...)
  "map"          markers of all participants on a map (heatmap or all markers), see items/map_marker.py
  "postal"       postal codes counted per zone, region and code, see items/postal_code.py
  dates, times   earliest, median and latest answer and their distribution, see items/date_time.py (a year: numbers)
  "acbc", "cbc"  conjoint: counts per level and an aggregate logit, see app/acbc_analysis.py, app/cbc_analysis.py
  None           not summarised (feedback elements)
Van Westendorp items (PSM) additionally get their curves, price points and, with the
Newton-Miller-Smith questions, trial and revenue curves (app/psm_analysis.py). MaxDiff items
("maxdiff") get best-minus-worst scores per statement and an aggregate MNL (app/maxdiff_analysis.py). MaxDiff,
CBC and ACBC get, once estimated on request, individual utilities by hierarchical Bayes (app/maxdiff_hb.py,
app/conjoint_hb.py). Shares, means, alpha, omega, r and PSM price points carry 95 % confidence intervals
(app/intervals.py), every scale an item analysis (scales.item_analysis). Not there yet: comparisons between groups
(e.g. randomizer conditions) and tests.
Filter: data sources, completed only or all, and optionally without speeders (Leiner 2019)."""
import math
import re
import statistics
import threading

from app import constructs, efa, export, intervals, psm_analysis, scales, units
from app import rows as row_logic
from app.items.base import DONT_KNOW_CODE, LEVEL_LABELS, MAX_NUMBER, plain
from app.lang import de_en

GENDER_CODES = re.compile(r"gender|geschlecht|sex", re.I)

# The sources of the figures on the page, in full (one fold at its end: page_sources())
HYNDMAN = ("Hyndman, R. J., & Fan, Y. (1996). Sample quantiles in statistical packages. The American Statistician, "
           "50(4), 361–365. https://doi.org/10.1080/00031305.1996.10473566")
GINI = ("Gini, C. (1921). Measurement of inequality of incomes. The Economic Journal, 31(121), 124–126. "
        "https://doi.org/10.2307/2223319")
SIMPSON = "Simpson, E. H. (1949). Measurement of diversity. Nature, 163(4148), 688. https://doi.org/10.1038/163688a0"
JOST = "Jost, L. (2006). Entropy and diversity. Oikos, 113(2), 363–375. https://doi.org/10.1111/j.2006.0030-1299.14714.x"


def _num(v):
    if v in (None, "") or isinstance(v, bool):
        return None if v in (None, "") else float(v)
    if type(v) is int and -MAX_NUMBER <= v <= MAX_NUMBER:
        return float(v)  # most answers are whole numbers: no detour through text (a million calls per page)
    if type(v) is float:
        return v if math.isfinite(v) and abs(v) <= MAX_NUMBER else None
    try:
        number = float(str(v).replace(",", "."))
    except ValueError:
        return None
    # "nan"/"inf" and numbers beyond MAX_NUMBER, stored before text.py rejected them: they would break the statistics
    return number if math.isfinite(number) and abs(number) <= MAX_NUMBER else None


def gini(values, weights=None):
    """Gini coefficient of non-negative numbers (0 = all equal, towards 1 = concentrated).
    None as soon as one value is negative: the coefficient needs a true zero, and dropping the
    negatives would report the Gini of a different sample. weights (one per value): the weighted coefficient
    Σ Σ w_i w_j |x_i − x_j| / (2 W² M_w), the same number as without weights when all are equal (code review
    2026-10-15, C9)."""
    if weights is not None:
        pairs = sorted((x, w) for x, w in zip(values, weights) if x is not None and w)
        if any(x < 0 for x, _ in pairs):
            return None
        whole = sum(w for _, w in pairs)
        total = sum(x * w for x, w in pairs)
        if len(pairs) < 2 or total == 0:
            return None
        # Σ_{i<j} w_i w_j (x_j − x_i) over the sorted values: each x counts plus with the weight below it and minus
        # with the weight above it
        below = spread = 0.0
        for x, w in pairs:
            spread += w * x * (below - (whole - below - w))
            below += w
        return spread / (whole * total)
    xs = sorted(v for v in values if v is not None)
    if any(v < 0 for v in xs):
        return None
    n, total = len(xs), sum(xs)
    if n < 2 or total == 0:
        return None
    return (2 * sum((i + 1) * x for i, x in enumerate(xs))) / (n * total) - (n + 1) / n


def gini_simpson(counts):
    """Probability that two random answers differ (0 = everyone the same)."""
    n = sum(counts)
    return 1 - sum((c / n) ** 2 for c in counts) if n else None


def cronbach_alpha(matrix):
    """Alpha for rows of complete answers (list of lists, one value per statement)."""
    k = len(matrix[0]) if matrix else 0
    if k < 2 or len(matrix) < 3:
        return None
    item_vars = [statistics.variance([row[j] for row in matrix]) for j in range(k)]
    total_var = statistics.variance([sum(row) for row in matrix])
    if total_var == 0:
        return None
    return k / (k - 1) * (1 - sum(item_vars) / total_var)


def weighted_median(values, weights):
    """Median of numbers with weights (None and weight 0 left out): the smallest value at which the cumulative weight
    reaches half of the total; where it reaches exactly half, the mean of that value and the next one. With all
    weights equal this is statistics.median."""
    pairs = sorted((x, w) for x, w in zip(values, weights) if x is not None and w)
    whole = sum(w for _, w in pairs)
    if not whole:
        return None
    cum = 0.0
    for i, (x, w) in enumerate(pairs):
        cum += w
        if abs(2 * cum - whole) <= 1e-9 * whole:
            return (x + pairs[i + 1][0]) / 2 if i + 1 < len(pairs) else x
        if 2 * cum > whole:
            return x
    return pairs[-1][0]


def numeric_stats(values, weights=None, persons=None):
    """n, M, Md, SD, min, max, Gini. weights (one per value, app/weights.py): M, Md and SD weighted ("weighted":
    True; the median as weighted_median, audit 2026-10-05), the rest as it is (min and max do not depend on
    weights); n stays the number of answers."""
    xs = [v for v in values if v is not None]
    if not xs:
        # every answer a code that counts as missing (an IAT nobody could do, all "weiß nicht"): the table shows dashes
        return {"n": 0, "mean": None, "median": None, "sd": None, "min": None, "max": None, "gini": None, "ci": None,
                "whole": False}
    mean, sd = statistics.fmean(xs), statistics.stdev(xs) if len(xs) > 1 else None
    out = {"n": len(xs), "mean": mean, "median": statistics.median(xs), "sd": sd, "min": min(xs), "max": max(xs),
           "gini": gini(xs), "ci": intervals.mean_ci(mean, sd, len(xs)),
           # whole answers (age, minutes): median and range without decimals (UI/UX audit 2026-10-05, V71)
           "whole": units.whole(xs)}
    if weights is not None:
        from app import weights as weighting

        mean, sd = weighting.mean(values, weights), weighting.sd(values, weights)
        # the interval with the linearization standard error (weights.mean_se, audit 2026-10-05, K-7) and the
        # degrees of freedom of the effective sample size after Kish (concept, section 2)
        n_eff = weighting.n_eff([w for x, w in zip(values, weights) if x is not None])
        out.update(mean=mean, sd=sd, median=weighted_median(values, weights), weighted=True, n_eff=n_eff,
                   gini=gini(values, weights),
                   ci=intervals.mean_ci(mean, sd, n_eff, se=weighting.mean_se(values, weights)))
    if persons is not None:
        from app import person_statistics

        out["ci"], out["n_eff"] = person_statistics.mean_ci(values, weights or [1.0] * len(values), persons)
        out["persons"] = out["n_eff"]
    return out


def bar_top(rows):
    """The largest share of a list of frequency bars (UI/UX audit 2026-10-05, V57): bars are scaled to it, the axis
    stays at 0 and the longest bar fills the track, so 3 % and 10 % differ visibly. With 95 % intervals the upper end
    of the widest one, so its error bar fits too (Ralf, 2026-10-06). Never 0."""
    return max([r.get("share") or 0 for r in rows or []] + [(r.get("ci") or (0, 0))[1] or 0 for r in rows or []] + [0]) or 1


def diverging_colour(t):
    """(CSS colour, end) at the place t (0 to 1) of a diverging scale (UI/UX audit 2026-10-07, S11): the seven tokens
    --div-1 (low end) over the neutral middle --div-4 to --div-7 (high end) of app.css and board.css; between two of
    them a mix in OKLab, so the scale stays symmetric around the middle. end: one of the two ends."""
    place = 1 + 6 * min(1.0, max(0.0, t))
    low = min(6, int(place + 1e-9))
    share = place - low
    if share < 1e-6:
        return f"var(--div-{low})", low in (1, 7)
    if share > 1 - 1e-6:
        return f"var(--div-{low + 1})", low + 1 == 7
    return f"color-mix(in oklab, var(--div-{low}) {round((1 - share) * 100)}%, var(--div-{low + 1}))", False


def level_colour(i, k):
    """(CSS colour, end) of level i of k ordered levels in a stacked bar (UI/UX audit 2026-10-05, V55/V78): on the
    diverging scale from --div-1 over the neutral --div-4 to --div-7 (S11); two levels: the two ends. The text on
    every step is --div-ink."""
    if k <= 1:
        return "var(--div-7)", True
    return diverging_colour(i / (k - 1))


def sequential_colour(value, low, high):
    """var(--seq-1) to var(--seq-5) for a value between low and high (maps, heat maps; S11): five classes of even
    width, the highest one when low and high are the same."""
    if high is None or low is None or high <= low:
        return "var(--seq-5)"
    step = 1 + int(4 * min(1.0, max(0.0, (value - low) / (high - low))) + 0.5)
    return f"var(--seq-{step})"


def _nice(step):
    if step <= 0:
        return 1
    power = 10 ** math.floor(math.log10(step))
    return next(m * power for m in (1, 2, 2.5, 5, 10) if m * power >= step)


def round_ticks(lo, hi, most=4):
    """Round values for the ticks of an axis from lo to hi (UI/UX audit 2026-10-07, W64: 45, 50, 55, 60 instead of
    47, 53, 60 at uneven places): steps of 1, 2, 2.5 or 5 × 10^k, at most `most` + 1 of them inside [lo, hi]."""
    if not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return [lo]
    step = _nice((hi - lo) / most)
    # the first and the last index once, with a tolerance relative to the step, and never more than most + 2
    # ticks: an absolute 1e-9 let a span of 1e-12 run to thousands of ticks, 1e-100 to an endless loop
    # (audit 2026-10-23, M9)
    first = math.ceil(lo / step - 1e-9)
    last = math.floor(hi / step + 1e-9)
    places = units.decimals(step) + 1
    return [units.round_half_up(i * step, places) for i in range(first, min(last, first + most + 1) + 1)]


def histogram(columns, most=24):
    """The distribution of the numbers of one question's columns on one common axis (UI/UX audit 2026-10-05, V54:
    small multiples): per column {"bars": [{"lo", "hi", "count"}], "top": the highest count of all columns,
    "single": classes of one whole value each, "ticks": [(position 0..1, value)]}. Whole numbers over a short span
    get one class per value, else about twelve equal classes of a round width; a value on an edge belongs to the
    upper class. [] per column without numbers."""
    every = [x for xs in columns for x in xs]
    if not every:
        return [None for _ in columns]
    lo, hi = min(every), max(every)
    whole = all(float(x).is_integer() for x in every)
    if whole and hi - lo <= 30:
        start, step, count, single = lo, 1, int(hi - lo) + 1, True
    else:
        step = _nice((hi - lo) / 12) if hi > lo else 1
        start = units.round_half_up(math.floor(lo / step + 1e-9) * step, units.decimals(step) + 1)
        count = max(1, units.class_of(hi, start, step) + 1)
        single = False
    out = []
    for xs in columns:
        counts = [0] * count
        for x in xs:
            counts[units.class_of(x, start, step, count)] += 1
        out.append([{"lo": units.round_half_up(start + i * step, units.decimals(step) + 1),
                     "hi": units.round_half_up(start + (i + 1) * step, units.decimals(step) + 1), "count": n}
                    for i, n in enumerate(counts)])
    top = max(b["count"] for bars in out for b in bars) or 1
    # about five labelled ticks on class edges (or values)
    every_k = max(1, math.ceil(count / 6))
    if single:
        ticks = [((i + 0.5) / count, start + i) for i in range(0, count, every_k)]
    else:
        ticks = [(i / count, units.round_half_up(start + i * step, units.decimals(step) + 1)) for i in range(0, count + 1, every_k)]
    return [{"bars": bars, "top": top, "single": single, "ticks": ticks, "n": len(xs)} if xs else None
            for bars, xs in zip(out, columns)]


def _value_labels(book_row):
    pairs = re.findall(r"(-?\d+(?:\.\d+)?)\s*=\s*([^;]+)", (book_row or ("", "", "", ""))[3] or "")
    from app.items.base import answer_label

    # without the colon of "Anderes, und zwar:" in charts and tables (UI audit 2026-10-12, U27)
    return {v: answer_label(lab) for v, lab in pairs}


def _key(v):
    return str(int(v)) if isinstance(v, (int, float)) and float(v).is_integer() else str(v)


def bad_number(v):
    """A number no statistic can use: "nan", "inf" or beyond MAX_NUMBER, stored before text.py rejected them. It
    counts as missing (and is reported as invalid), so one such value does not turn a column of numbers into text."""
    if v in (None, "") or isinstance(v, bool):
        return False
    try:
        number = float(str(v).replace(",", "."))
    except ValueError:
        return False
    return not math.isfinite(number) or abs(number) > MAX_NUMBER


def valid_number(v, missing=()):
    """The number of an export value for a statistic, None where there is none or it is one of the codes of its
    column that count as missing (`missing` as strings: "weiß nicht", -77 not shown). Every mean, share or score
    goes through this (or filters the same way), so a missing code never counts as a number."""
    if v in (None, "") or (missing and _key(v) in missing):
        return None
    return _num(v)


def frequencies(values, labels, numeric_order=False, missing=(), weights=None, persons=None):
    """Frequency rows of the valid answers; codes in `missing` ("weiß nicht") are counted apart
    (out["missing"]) and enter neither n nor the shares. weights (one per value, app/weights.py): the shares are
    weighted, each row has its weighted count as "w"; count and n stay the numbers of answers."""
    counts, sums, squares, held = {}, {}, {}, {}
    for k, v in enumerate(values):
        if v in (None, ""):
            continue
        counts[_key(v)] = counts.get(_key(v), 0) + 1
        if weights is not None:
            held[_key(v)] = held.get(_key(v), 0) + (1 if weights[k] else 0)
            sums[_key(v)] = sums.get(_key(v), 0.0) + (weights[k] or 0.0)
            squares[_key(v)] = squares.get(_key(v), 0.0) + (weights[k] or 0.0) ** 2
    apart = [{"value": k, "label": labels.get(k, ""), "count": counts.pop(k, 0)} for k in sorted(missing)]
    labels = {k: v for k, v in labels.items() if k not in missing}
    extra = [k for k in counts if k not in labels]
    order = (sorted(list(labels) + extra, key=_sort_key) if numeric_order
             else list(labels) + sorted(extra, key=_sort_key))
    n = sum(counts.values())
    rows = [{"value": k, "label": labels.get(k, ""), "count": counts.get(k, 0),
             "share": counts.get(k, 0) / n if n else 0, "ci": intervals.wilson(counts.get(k, 0), n)}
            for k in order if counts.get(k, 0) or k in labels]
    out = {"n": n, "rows": rows, "gini_simpson": gini_simpson([r["count"] for r in rows]) if n else None,
           "missing": [m for m in apart if m["count"]]}
    if weights is not None:
        whole = sum(sums.get(r["value"], 0.0) for r in rows)
        square = sum(squares.get(r["value"], 0.0) for r in rows)
        from app import weights as weighting

        n_eff = whole * whole / square if square else 0.0  # Kish
        answers = sum(held.get(r["value"], 0) for r in rows)  # with a weight above 0
        for r in rows:
            r["w"] = sums.get(r["value"], 0.0)
            r["share"] = r["w"] / whole if whole else 0
            # Wilson with the sample size of the linearization SE of the share (audit 2026-10-05, K-7)
            n_lin = weighting.share_n(r["share"], answers, whole, squares.get(r["value"], 0.0), square)
            r["ci"] = intervals.wilson(r["share"] * n_lin, n_lin) if n_lin else None
        out.update(weighted=True, wn=whole, n_eff=n_eff, gini_simpson=gini_simpson([r["w"] for r in rows]) if whole else None)
    if persons is not None:
        from app import person_statistics

        valid = [( _key(v), w, p) for v, w, p in zip(values, weights or [1.0] * len(values), persons)
                 if v not in (None, "") and _key(v) not in missing]
        xs, ws, owners = zip(*valid) if valid else ([], [], [])
        out["persons"] = len({p for _, w, p in valid if w})
        out["n_eff"] = out["persons"]
        for row in rows:
            row["ci"] = person_statistics.share_ci(xs, ws, owners, row["value"])
    return out


def _sort_key(value):
    n = _num(value)
    return (0, n, "") if n is not None else (1, 0, value)


def _quantile(rows, n, p, key="count"):
    """Nearest-rank quantile over frequency rows in scale order: the first value whose cumulative
    count reaches p * n, so an ordinal median is always a category of the scale. key "w": the weighted counts."""
    total = 0
    for r in rows:
        total += r[key]
        if r[key] and total >= p * n:
            return r
    return None


def _median_upper(rows, n, low, key="count"):
    """The level above the median when exactly half of the answers lie up to `low` (even n): the median
    then falls between two levels and both are named; None otherwise."""
    total = 0
    for i, r in enumerate(rows):
        total += r[key]
        if r is low:
            half = 2 * total == n if key == "count" else abs(2 * total - n) < 1e-9 * max(n, 1)
            return next((x for x in rows[i + 1:] if x[key]), None) if half else None
    return None


def ordinal_stats(values, labels, missing=(), numeric_order=True, weights=None, persons=None):
    """Frequencies in scale order with cumulative shares, plus median, quartiles, mode and range. The
    median is a level of the scale; when it falls between two levels, "median_hi" is the upper one.
    numeric_order=False: the scale order is the order of `labels` (answer options with letter codes).
    weights: shares, cumulative shares, median, quartiles and mode from the weighted counts (frequencies)."""
    out = frequencies(values, labels, numeric_order=numeric_order, missing=missing, weights=weights, persons=persons)
    rows, n = out["rows"], out["n"]
    key, whole = ("w", out["wn"]) if weights is not None else ("count", n)
    cum = 0
    for r in rows:
        cum += r[key]  # from the counts: summed shares can fall a rounding step short (67 of 200 as 33 %)
        r["cum"] = cum / whole if whole else 0
    if not n or not whole:
        return out
    seen = [r for r in rows if r["count"]]
    top = max(r[key] for r in rows)
    median = _quantile(rows, whole, 0.5, key)
    out.update(median=median, median_hi=_median_upper(rows, whole, median, key), q1=_quantile(rows, whole, 0.25, key),
               q3=_quantile(rows, whole, 0.75, key),
               mode=[r for r in rows if r[key] == top], min=seen[0], max=seen[-1])
    return out


# answers that stand for no answer or for "something else": in a list sorted by frequency they come last, whatever
# their count (Ralf, 2026-10-03); answer codes declared as missing are counted apart anyway (frequencies). An option
# with a text field ("Anderes, und zwar") is one of them whatever its wording (rows with "rest", see other_codes)
RESIDUAL = {"keine angabe", "keine angaben", "k. a", "k.a", "ka", "weiß nicht", "weiss nicht", "weiß ich nicht", "weiss ich nicht",
            "ich weiß nicht", "ich weiss nicht", "ich weiß es nicht", "ich weiss es nicht", "weiß es nicht", "weiss es nicht",
            "kann ich nicht sagen", "kann ich nicht beurteilen", "kann ich nicht einschätzen", "kann ich nicht einschaetzen",
            "möchte ich nicht sagen", "möchte ich nicht beantworten", "möchte ich nicht angeben", "ich möchte nicht antworten",
            "ich möchte keine angabe machen", "keine angabe machen", "keine antwort", "keine meinung", "trifft nicht zu",
            "nichts davon", "keines davon", "keine davon", "keins davon", "keine der genannten", "keines der genannten",
            "keine der genannten antworten", "keine der genannten optionen", "keine der genannten möglichkeiten",
            "prefer not to say", "prefer not to answer", "rather not say", "don't know", "do not know", "i don't know",
            "no answer", "not applicable", "n/a", "none of these", "none of the above"}
RESIDUAL_START = ("anderes", "andere", "sonstiges", "sonstige", "etwas anderes", "other")
# "Weiß nicht / keine Angabe", "Keine Angabe (weiß nicht)": every part of such a label is looked at on its own
RESIDUAL_SPLIT = re.compile(r"\s*(?:/|;|\(|\)|\boder\b|\bbzw\.?|\bor\b)\s*")


def _residual_part(text):
    text = text.strip(" .:…,-–")
    return text in RESIDUAL or any(text == start or text.startswith(start + " ") or text.startswith(start + ",")
                                   or text.startswith(start + ":") for start in RESIDUAL_START)


def residual(label):
    """Whether an answer label is one of those that come last in a list sorted by frequency."""
    from app.items.base import plain

    text = " ".join(plain(str(label or "")).lower().replace("’", "'").split()).strip(" .:…")
    if not text:
        return False
    return _residual_part(text) or any(_residual_part(part) for part in RESIDUAL_SPLIT.split(text) if part.strip(" .:…,-–"))


def other_codes(item):
    """The codes of the options with a text field ("Anderes, und zwar"): residual answers by their structure."""
    if not getattr(item.kind, "supports_text", False):
        return set()
    return {str(o["code"]) for o in (item.config or {}).get("options", []) if o.get("text")}


def by_count(rows):
    """Frequency rows of answers without an order: the most frequent first, the residual answers (residual, or
    "rest": an option with a text field) last; equal counts keep the order of the questionnaire. Weighted rows
    (with "w") by their weighted count."""
    return sorted(rows, key=lambda r: (bool(r.get("rest")) or residual(r.get("label")),
                                       -(r["w"] if "w" in r else (r.get("count") or 0))))


def multi_lists(item, table, passes=(), lang=None, weights=None, owners=None, column_weights=None):
    """Multiple choice as one list (per loop pass): per option how many ticked it, out of the people who saw the
    option and answered the question (0 or 1 in its column; -77 = option not shown, empty = question not
    answered), sorted by frequency with the residual answers last. The shares add up to more than 100 %.
    weights (one per row of the table): the shares weighted, the counts as they are."""
    from app.items.base import answer_label

    lang = lang or item.survey.default_language
    others = other_codes(item)
    options = item.kind.options(item, lang)
    out = []
    for n in passes or [None]:
        suffix = f"_{n}" if n else ""
        cols = [f"{item.code}_{o['code']}{suffix}" for o in options]
        rows = []
        for o, col in zip(options, cols):
            current_weights = (column_weights or {}).get(col, weights)
            seen = [x for x in (_num(r.get(col)) for r in table) if x in (0, 1)]
            count = sum(1 for x in seen if x == 1)
            row = {"value": str(o["code"]), "label": answer_label(plain(o["label"])), "count": count, "n": len(seen),
                   "share": count / len(seen) if seen else 0, "rest": str(o["code"]) in others,
                   "ci": intervals.wilson(count, len(seen))}  # on the base of the option
            if current_weights is not None:
                from app import weights as weighting

                pairs = [(x, w or 0.0) for x, w in ((_num(r.get(col)), w) for r, w in zip(table, current_weights)) if x in (0, 1)]
                whole = sum(w for _, w in pairs)
                row["w"] = sum(w for x, w in pairs if x == 1)
                row["share"] = row["w"] / whole if whole else 0
                square = sum(w * w for _, w in pairs)
                n_lin = weighting.share_n(row["share"], sum(1 for _, w in pairs if w), whole,
                                          sum(w * w for x, w in pairs if x == 1), square)
                row["ci"] = intervals.wilson(row["share"] * n_lin, n_lin) if n_lin else None
            if owners is not None:
                from app import person_statistics

                valid = [(_num(r.get(col)), w, p) for r, w, p in zip(table, current_weights if current_weights is not None else [1.0] * len(table), owners)
                         if _num(r.get(col)) in (0, 1)]
                xs, ws, people = zip(*valid) if valid else ([], [], [])
                row["ci"] = person_statistics.share_ci(xs, ws, people, 1)
            rows.append(row)
        persons = sum(1 for r in table if any(_num(r.get(c)) in (0, 1) for c in cols))
        out.append({"pass": n, "n": persons, "partial": any(row["n"] != persons for row in rows),
                    "rows": by_count(rows)})
    return out


def nominal_stats(values, labels, missing=(), weights=None, persons=None):
    out = frequencies(values, labels, missing=missing, weights=weights, persons=persons)
    if out["n"]:
        key = "w" if weights is not None else "count"
        top = max(r[key] for r in out["rows"])
        out["mode"] = [r for r in out["rows"] if r[key] == top]
    return out


VIEWER_MIN = 5  # what a single person typed or where one person lives: the role "Ansehen" sees it from this count on


def for_viewers(data):
    """The results as the role "Ansehen" reads them: aggregated. Answers of single participations (what was typed,
    exact places and days) need "Bearbeiten", as the data export does. Typed words, postal codes and days that occur
    fewer than VIEWER_MIN times are left out; entry["few_hidden"] says so on the page. Changes `data` in place."""
    for entry in data["items"]:
        if entry.get("map"):
            cells = {}
            for i, p in enumerate(entry["map"]["points"]):
                key = (round(p["lat"], 2), round(p["lon"], 2))
                cell = cells.setdefault(key, {"people": set(), "weight": 0})
                cell["people"].add(p.get("p", i))
                cell["weight"] += p.get("w", 1)
            entry["map"]["points"] = [{"lat": lat, "lon": lon, "k": 1, "w": cell["weight"]}
                                      for (lat, lon), cell in cells.items() if len(cell["people"]) >= VIEWER_MIN]
            entry["few_hidden"] = True
        for column in entry["columns"]:
            column.pop("texts", None)  # texts typed next to an option ("Anderes: ...")
        if entry.get("cloze"):
            for gap in entry["cloze"]["gaps"]:
                gap["answers"] = [(word, n) for word, n in gap["answers"] if n >= VIEWER_MIN]
            entry["few_hidden"] = True
        if entry.get("terms"):
            # by persons: one person naming the same word in five fields is still one person
            entry["terms"] = [term for term in entry["terms"] if (term[3] if len(term) > 3 else term[1]) >= VIEWER_MIN]
            entry["few_hidden"] = True
        if entry.get("postal") and entry["postal"].get("n"):
            postal = entry["postal"]
            often = {code for code, n in postal["counts"].items() if n >= VIEWER_MIN}
            postal["counts"] = {code: n for code, n in postal["counts"].items() if code in often}
            postal["places"] = {code: place for code, place in postal["places"].items() if code in often}
            postal["top"] = [row for row in postal["top"] if row["count"] >= VIEWER_MIN]
            entry["few_hidden"] = True
        if entry.get("dates") and entry["dates"].get("n"):
            dates = entry["dates"]
            dates["min"] = dates["max"] = None  # the earliest and the latest answer are answers of one person each
            if dates.get("step") == "Tag":
                dates["rows"] = [row for row in dates["rows"] if row["count"] >= VIEWER_MIN]
                dates["filled"] = False
            entry["few_hidden"] = True
    # the panel on gender shows its question apart from the list: the texts typed there are answers of single
    # participations as well (the same entry as in the list, unless a caller built it anew)
    for column in (data.get("gender") or {}).get("columns", []):
        column.pop("texts", None)
    return data


MAX_TEXTS = 30  # typed texts listed per column ("Anderes: ..."), the most frequent first


MAX_FREE_TEXTS = 300  # answers to an open question listed in their fold (V61), the most frequent first


def typed_texts(values, most=None):
    """Texts typed next to an answer option, counted: {"rows": [(text, count)], "more": further different
    texts}. Spellings are merged only by upper and lower case; the first spelling met is shown. most: how many
    (MAX_TEXTS)."""
    most = most or MAX_TEXTS
    counts, shown = {}, {}
    for v in values:
        if v in (None, ""):
            continue
        key = str(v).casefold()
        counts[key] = counts.get(key, 0) + 1
        shown.setdefault(key, str(v))
    ranked = sorted(counts, key=lambda k: (-counts[k], k))
    return {"rows": [(shown[k], counts[k]) for k in ranked[:most]], "more": max(len(ranked) - most, 0)}


def compute(*args, **kwargs):
    """_compute() with an export plan open (items.base.export_plan): what the element types prepare per element (the
    columns of a side-by-side matrix, code review 2026-10-15, A9) is built once for the whole page, not per column."""
    from app.items.base import export_plan

    with export_plan():
        return _compute(*args, **kwargs)


def _compute(survey, sources=("real",), include_incomplete=False, drop_speeders=False,
             speeder=None, speed_real=True, progress=None, only=None, table=None, weighted=False, omega_ci=None):
    """progress(share, text): told how far the computation is (app/progress.py); reading the responses
    is the first half, the items are the second.
    only: ids of the items to summarise, the others are left out (the method section needs a few of them and
    no conjoint model); table: export.rows() of the same pool with all its rows, if the caller has built it.
    omega_ci: True computes the bootstrap interval of omega here (the pooled construct view of the administration,
    app/construct_pool.py; fill_omega). Otherwise it is left for later (performance audit 2026-10-06, P1: 200 draws per
    scale were most of the time of the page): without `only` a scale whose interval would be drawn carries
    "omega_wait", and the results page fetches the intervals as a part of their own (omega_part, fill_omega);
    "wait" marks them so also with `only` (the construct pool, which starts their job).
    weighted: with the weighting of the survey (app/weights.py): only the weighted participations (completed, not
    marked), shares, M and SD weighted (K13: not alpha, omega, scale scores, choice models, PSM); out["weighting"].
    A diary study (app/diary.py) without that weighting weighs each entry, per question, 1 / the entries of its person
    in its part that answer it (diary.question_weights), so every person counts once in every question;
    out["diary"]: persons, entries, by_person."""
    progress = progress or (lambda share, text: None)
    if table is None:
        # the median for the speeder flag comes from the chosen data pool
        table = export.rows(survey, include_incomplete, sources, speeder, speed_real, speed_sources=sources, with_hb=False,
                            with_quality=False, progress=lambda n, total: progress(0.5 * n / total, de_en("Antworten lesen: Teilnahme {n} von {total}",
                                                                                         "Reading answers: participation {n} of {total}", n=n, total=total)))
    speeders = sum(1 for r in table if r.get("speeder") == 1)
    marked = sum(1 for r in table if r.get("qc_marked") == 1)  # speeders and other marked participations
    from app import quotas

    # the quota report beside the results leaves the marked out of what is reached, as the results do (real ones)
    quota_marked = quotas.marked_done(table) if drop_speeders and survey.quotas and "real" in (sources or ("real",)) else None
    weighting = _weighting(survey, table) if weighted else None
    if drop_speeders:
        table = table.keep(lambda r: r.get("qc_marked") != 1)
    wts = None
    # HB estimates are made unweighted from this table (surveys/estimates.py): whether one is current is judged here, before the
    # weighting leaves out persons without a weight; the weights act in the market simulator only (audit 2026-10-05, W-6)
    hb_table = table
    if weighting is not None:
        before = len(table)
        # weights exist for the completed, unmarked participations only: with "Markierte einbeziehen" the marked
        # ones stay out of the weighted view, and the page says so (audit 2026-10-05)
        marked_out = sum(1 for r in table if r.get("qc_marked") == 1 and r.get("resp_id") not in weighting["weights"])
        table = table.keep(lambda r: r.get("resp_id") in weighting["weights"])
        wts = [weighting["weights"][r["resp_id"]] for r in table]
        weighting = {k: v for k, v in weighting.items() if k != "weights"}
        weighting["left_out"] = before - len(table) - marked_out  # unfinished ones in a pool "also unfinished": no weight
        weighting["marked_out"] = marked_out
        weighting["pids"] = {r.get("pid"): w for r, w in zip(table, wts)}  # the market simulator weights HB persons
    diary_info, person_ids = None, None
    if getattr(survey, "sampling", None) is not None:  # a diary study (app/diary.py): persons, not entries
        from app import diary

        numbered = getattr(table, "numbered", {}) or {}
        resps = [numbered.get(r.get("resp_id")) for r in table]
        person_ids = [x.enrolment_id if x is not None and x.enrolment_id else ("row", n)
                      for n, x in enumerate(resps)]
        persons = {x.enrolment_id for x in resps if x is not None and x.enrolment_id}
        entries = sum(1 for x in resps if x is not None and x.enrolment_id and (x.occasion or {}).get("part", "entry") != "entry")
        if weighting is None:  # every person counts once in every part, however often they answered
            person = diary.person_weights(table, numbered)
            wts = [person.get(r.get("resp_id"), 1.0) for r in table]
        diary_info = {"persons": len(persons), "entries": entries, "by_person": weighting is None}
    book = {row[0]: row for row in export.codebook(survey, speeder, speed_real, sources)}
    lang = survey.default_language
    # a further word of a live vote ("Mehrere Antworten pro Gerät") is a row of its own but no other person (audit T-19)
    people = sum(1 for r in table if r.get("weiteres_wort") != 1)
    out = {"n_rows": people, "speeders": speeders, "marked": marked, "speed_rule": export.speeder_rule(survey), "quota_marked": quota_marked, "items": [], "gender": None,
           "versions": sorted({r.get("qversion") or 1 for r in table}), "weighting": weighting,
           "diary": diary_info}
    items = export._items(survey)
    from app import coding

    coded, coded_loops = coding.plan(survey), coding.plan(survey, loops=True)
    person_scores = []  # (item, subscale, statistics, score per participation) of every scale outside loops
    for number, item in enumerate(items, start=1):
        kind = item.kind.result_kind
        cols = [c for c, _ in item.kind.export_columns(item)] if kind else []
        if not cols or (only is not None and item.id not in only):
            continue
        progress(0.5 + 0.45 * (number - 1) / len(items), de_en("Auswerten: Element {n} von {total}", "Analysing: element {n} of {total}",
                                                                 n=number, total=len(items)))
        labels = {c: plain(label) for c, label in item.kind.export_columns(item)}
        entry = {"item": item, "kind": kind, "question": plain(item.text("question", lang)) or item.kind.label,
                 "columns": [], "scale": None}
        # a question in a loop has one column per pass (code_1, code_2, ...), as in the export
        loop_rows = export._loop_rows(item) or []
        passes = [n for n, _ in loop_rows]
        if loop_rows:
            from app import lang as ui_lang
            from app import loops

            captions = {n: loops.row_caption(item.block, rid, n, lang=ui_lang.current()) for n, rid in loop_rows}
            entry["pass_names"] = [(n, captions[n]) for n in passes]
        if diary_info is not None and diary_info["by_person"]:
            # every person once in this question: 1 / their entries with a valid value in it (decision 6, D-4)
            gone_of = {c: {str(m) for m in item.kind.missing_codes(item, c)} for c in cols}
            missing = {f"{c}_{n}": gone_of[c] for c in cols for n in passes} or gone_of
            per_question = diary.question_weights(table, numbered, list(missing), missing)
            wts = [per_question.get(r.get("resp_id"), 1.0) for r in table]
        entry["level"] = item.kind.level(item)
        unseen_cols = set()
        hist_values = {}  # the numbers of each metric column, for the histograms (V54)
        # named in the heading where the researcher chose it (types with a switch in the editor)
        # German, as everything kept in the results store; the page shows it in its language (e.level is the key)
        entry["level_label"] = LEVEL_LABELS[entry["level"]] if len(item.kind.level_choices(item)) > 1 else ""
        # attention checks are no answer to the question: the page "Qualitätssicherung" evaluates them
        checks = item.kind.check_codes(item) if hasattr(item.kind, "check_codes") else set()
        rest_codes = other_codes(item)  # "Anderes, und zwar": last in a list sorted by frequency
        entry["checks"] = len(checks)
        for c in cols:
            if c in checks:
                continue
            ckind, unit, own_labels = item.kind.result_column(item, c)
            if not ckind:
                continue
            level = item.kind.column_level(item, c)
            gone = {str(m) for m in item.kind.missing_codes(item, c)}  # "weiß nicht" etc.: not in the statistics
            # amounts of a total that differs per person (constant sum from an earlier answer) are summarised as
            # shares in percent: the amounts of people with a large total would decide every mean
            share = getattr(item.kind, "share_of", None)
            total_col = share(item, c) if share else None
            if total_col:
                unit = "%"
            for col, n in ([(f"{c}_{n}", n) for n in passes] or [(c, None)]):
                if diary_info is not None and diary_info["by_person"]:
                    per_column = diary.question_weights(table, numbered, [col], {col: gone})
                    wts = [per_column.get(r.get("resp_id"), 1.0) for r in table]
                values = [r.get(col) for r in table]
                amount = None
                if total_col:
                    totals = [_num(r.get(f"{total_col}_{n}" if n else total_col)) for r in table]
                    # a code that counts as missing (-77: option not shown) is no amount; it stays as it is, so
                    # the share statistics below leave it out and count it as not shown
                    amounts = [valid_number(v, gone) for v in values]
                    paid = [a for a, t in zip(amounts, totals) if a is not None and t]
                    amount = statistics.fmean(paid) if paid else None
                    values = [v if v not in (None, "") and _key(v) in gone else
                              100 * a / t if a is not None and t else None  # unrounded: the page rounds M, SD, median (R-7)
                              for v, a, t in zip(values, amounts, totals)]
                nums = [_num(v) for v in values]
                # nan/inf/1e300 from old data count as missing and are reported as invalid, instead of turning the
                # whole column into text (D-17)
                invalid = sum(1 for v in values if bad_number(v))
                if invalid:
                    values = [None if bad_number(v) else v for v in values]
                unseen = str(row_logic.NOT_SHOWN) in gone
  # a row only some people saw: -77 is no answer
                if unseen:
                    unseen_cols.add(col)
                answered = sum(1 for v in values if v not in (None, "") and not (unseen and _key(v) == str(row_logic.NOT_SHOWN)))
                valid = [n for v, n in zip(values, nums) if v not in (None, "") and _key(v) not in gone]
                numeric = all(n is not None for n in valid)
                vlabels = own_labels or _value_labels(book.get(col) or book.get(c))
                every = item.kind.scale_values(item, c)
                if every:  # also the levels nobody chose (stars, a row of numbers)
                    vlabels = {**{str(v): "" for v in every}, **vlabels}
                if not answered:
                    stats = {"type": "empty", "n": 0}
                elif getattr(item.kind, "lists_texts", None) and item.kind.lists_texts(item, c):
                    stats = {"type": "text", "n": answered, "texts": typed_texts(values)}
                elif level == "ordinal" and (numeric or own_labels) and valid:
                    # answer options: their order in the question is the scale, whatever their codes (Hauptschule 1,
                    # Realschule 3, Abitur 2 after an option was put in between); as frequencies they would be
                    # sorted by count and lose it. Without labels of their own: the order of the numbers
                    stats = {"type": "ord", **ordinal_stats(values, vlabels, gone, numeric_order=not own_labels,
                                                            weights=wts, persons=person_ids)}
                    if col in scales.reversed_codes(item) and stats.get("rows"):
                        # a reverse statement is recoded (1 = the last label); shown in the order of its labels like
                        # its neighbours, so agreement stays on the same side, the shares summed from that end (R51)
                        stats["reversed"], whole = True, 0
                        for r in reversed(stats["rows"]):
                            whole += r["share"]
                            r["cum_shown"] = whole
                elif ckind == "categorical" or (level == "nominal" and numeric):
                    stats = {"type": "freq", **nominal_stats(values, vlabels, gone, weights=wts, persons=person_ids)}
                elif numeric:
                    # without the codes that count as missing (-77: a row this person did not see)
                    clean = [None if v not in (None, "") and _key(v) in gone else n for v, n in zip(values, nums)] if gone else nums
                    stats = {"type": "num", **numeric_stats(clean, weights=wts, persons=person_ids)}
                    hist_values[col] = [x for x in clean if x is not None]
                else:
                    # the answers to an open question, read on the page in a fold (V61); the role "Ansehen" does not
                    # get them (for_viewers)
                    stats = ({"type": "text", "n": answered, "texts": typed_texts(values, MAX_FREE_TEXTS), "free": True}
                             if item.type == "text_input" else {"type": "text", "n": answered})
                if invalid and stats.get("type") in ("num", "ord", "freq"):
                    stats["invalid"] = invalid
                if stats.get("type") == "num" and str(DONT_KNOW_CODE) in gone:
                    # "weiß nicht" (V41): no number; the frequency tables list it as missing, the table of numbers here
                    stats["dont_know"] = sum(1 for v in values if v not in (None, "") and _key(v) == str(DONT_KNOW_CODE))
                # the Gini coefficient needs a true zero (amounts, counts, times, distances): on a rating scale it
                # changes with the coding (1..5 or 0..4) and says nothing
                ratio = bool(item.kind.is_ratio(item) or unit in ("ms", "m"))
                if stats.get("type") == "num" and not ratio:
                    stats["gini"] = None
                label = labels.get(c, "") + (f" [{captions[n]}]" if n else "")
                if total_col:
                    label = label.replace("Betrag „", "Anteil „").replace(
                        "(von der persönlichen Summe)", "(in % der persönlichen Summe"
                        + (f"; mittlerer Betrag {item.kind.amount(item, units.round_half_up(amount), lang)})" if amount is not None else ")"))
                if unseen:
                    # the label of the export names the condition; here the line of the variable says how many
                    # people did not see it (the table of numbers has no line for what counts as missing)
                    label = re.sub(r" \[nur gezeigt, wenn [^\]]*\]", "", label)
                    stats["not_shown"] = sum(1 for v in values if v not in (None, "") and _key(v) == str(row_logic.NOT_SHOWN))
                if stats.get("type") == "freq" and rest_codes:
                    for row in stats["rows"]:
                        row["rest"] = row["value"] in rest_codes
                entry["columns"].append({"code": col, "label": label, "unit": unit, "ratio": ratio, **stats})
        # the distribution of every metric column, on one axis per unit within the question (small multiples)
        by_unit = {}
        for c in entry["columns"]:
            if c.get("type") == "num" and hist_values.get(c["code"]):
                by_unit.setdefault(c["unit"], []).append(c)
        for group in by_unit.values():
            for c, h in zip(group, histogram([hist_values[c["code"]] for c in group])):
                c["hist"] = h
        all_cols = [c["code"] for c in entry["columns"]]
        entry["answered"] = sum(1 for r in table if any(
            r.get(c) not in (None, "") and not (c in unseen_cols and _key(r.get(c)) == str(row_logic.NOT_SHOWN)) for c in all_cols))
        entry["passes"] = len(passes)
        if item.type == "multiple_choice":
            column_weights = None
            if diary_info is not None and diary_info["by_person"]:
                wts = [per_question.get(r.get("resp_id"), 1.0) for r in table]
                column_weights = {}
                for col in missing:
                    per_column = diary.question_weights(table, numbered, [col], {col: missing[col]})
                    column_weights[col] = [per_column.get(r.get("resp_id"), 1.0) for r in table]
            # one list of the options, not a 0/1 frequency per option: their columns leave the list of columns
            # (the texts typed next to "Anderes, und zwar" stay)
            entry["multi"] = multi_lists(item, table, passes, lang, weights=wts, owners=person_ids,
                                        column_weights=column_weights)
            options = {f"{item.code}_{o['code']}" for o in item.config.get("options", [])}
            entry["columns"] = [c for c in entry["columns"] if c["code"] not in options
                                and not any(c["code"] == f"{o}_{n}" for o in options for n in passes)]
        # which statements form the scale(s), and how a person's score is taken: app/scales.py (also the export)
        scored = scales.scored_codes(item) if kind == "scale" else cols
        if kind == "scale" and len(scored) >= 2 and entry["level"] != "metric":
            entry["scale_hidden"] = True  # alpha, omega and the scale mean assume metric answers
        else:
            found = scales.groups(item)

            def scale_of(codes, name=None):
                matrix = []  # in a loop every pass of a person is one case
                # statements that only some people saw (app/rows.py): their -77 is no answer. Alpha and omega need
                # the same statements for everyone, so they stay with the people who saw and answered all of them;
                # the scale mean of a person is taken over the statements they saw (scales.score: all of them
                # answered, at least scales.MIN_SHOWN)
                hidden = {c: {str(m) for m in item.kind.missing_codes(item, c)} for c in codes}
                partial = any(hidden.values())
                seen, scores, means = [], [], []
                # in a loop: per complete row its person (the omega bootstrap draws persons) and per person the
                # scores of their passes (the interval of M is cluster-robust over persons, audit 2026-10-05, K-2/I-2)
                owners, pass_scores, mean_owners = [], [], []
                for number, r in enumerate(table):
                    mine = []
                    for suffix in ([f"_{n}" for n in passes] or [""]):
                        row = scales.values(item, codes, r, suffix, hidden)
                        if all(v is not None for v in row):
                            matrix.append(row)
                            owners.append(person_ids[number] if person_ids is not None else number)
                        shown = scales.shown(item, codes, r, suffix, hidden)
                        person = scales.score(row, shown)
                        if person is not None:
                            means.append(person)
                            mean_owners.append(person_ids[number] if person_ids is not None else number)
                            mine.append(person)
                            seen.append(sum(shown))
                    if mine:
                        pass_scores.append(mine)
                    if not passes:  # one score per participation, for the correlations of the overview
                        scores.append(person)
                if person_ids is not None:
                    from app import person_statistics

                    matrix = person_statistics.matrix_means(matrix, owners)
                    means = list(person_statistics.collapse(means, mean_owners).values())
                    pass_scores = [[mean] for mean in means]
                bootstrap_owners = owners if passes and person_ids is None else None
                alpha = cronbach_alpha(matrix)
                out = {"k": len(codes), "n": len(matrix), "alpha": alpha,
                       "omega": scales.mcdonald_omega(matrix),
                       # two statements: alpha says little, the Spearman-Brown coefficient is the estimate to report
                       **({"sb": scales.spearman_brown(matrix)} if len(codes) == 2 else {}),
                       # in a loop the cases are passes: several of one person, not independent (Geldhof et al., 2014)
                       "pooled": bool(passes), "by_person": person_ids is not None,
                       **({"mean": statistics.fmean(means), "sd": statistics.stdev(means) if len(means) > 1 else None}
                          if means else {})}
                # 95 % intervals (app/intervals.py): the bootstrap of omega only when asked for (omega_ci); the
                # results page marks it as waiting and fetches it apart (fill_omega), the method section (only=...)
                # needs none of them
                out["omega_small"] = out["omega"] is not None and scales.omega_small(len(matrix), len(codes))
                # in a loop the cases are passes, several per person (audit 2026-10-05, K-2/I-2): no Feldt interval
                # (it takes the passes as independent cases), the omega bootstrap draws persons with all their
                # passes, the interval of M is cluster-robust over the persons around M (t with persons - 1)
                out["alpha_ci"] = None if passes else intervals.feldt(alpha, len(matrix), len(codes))
                out["omega_ci"] = None
                if omega_ci == "collect" and out["omega"] is not None:  # for the bootstrap as a job (draw_omega)
                    out["omega_input"] = {"matrix": matrix, "persons": bootstrap_owners}
                elif omega_ci and omega_ci != "wait" and out["omega"] is not None:
                    out["omega_ci"], failed = intervals.omega_boot(matrix, persons=bootstrap_owners)
                    if failed:  # draws without an omega: below every value; the page names their share
                        out["omega_failed"] = failed
                elif (only is None or omega_ci == "wait") and out["omega"] is not None and intervals.omega_boot_runs(
                        matrix, bootstrap_owners):
                    out["omega_wait"] = True
                if len(codes) == 2:
                    out["sb_ci"] = None if passes else intervals.spearman_brown(matrix)
                if means and passes:
                    out["persons"] = len(pass_scores)
                    out["mean_ci"] = intervals.cluster_mean_ci(pass_scores)
                elif means:
                    out["mean_ci"] = intervals.mean_ci(out["mean"], out["sd"], len(means))
                # item analysis: per statement M, SD, difficulty, corrected item-total correlation, alpha without it, loading
                rows = scales.item_analysis(matrix, [scales.item_range(item, c) for c in codes])
                for c, row in zip(codes, rows):
                    text = re.sub(r" \[[^\]]*\]", "", labels.get(c, "")).replace(" (rekodiert)", "")
                    row.update(code=c, text=text, reverse=c in reverse,
                               low_rit=row["rit"] is not None and row["rit"] < scales.ITEM_RIT_LOW,
                               high_without=None not in (row["alpha_without"], alpha) and row["alpha_without"] > alpha)
                out["item_stats"] = rows
                if partial:
                    out["partial"] = {"n": len(means) if person_ids is not None else len(seen),
                                      "k": statistics.fmean(seen) if seen else None}
                if not passes:
                    person_scores.append((item, name, out, scores))
                return out

            # an instrument with subscales (BFI-10, IE-4, ...) measures several things: one alpha and one mean
            # over all statements would mix them (alpha 0.53 where every subscale has about 0.8)
            # reverse statements are recoded before every figure of the scale (constructs and own questions alike)
            entry["recoded"] = any(s.get("reverse") for s in (item.config or {}).get(
                "statements" if item.type == "likert" else "pairs", []) if "check" not in s)
            reverse = scales.reversed_codes(item)
            if found and found[0][0] is not None:
                entry["subscales"] = [{"name": name, **scale_of(codes, name)} for name, codes in found]
            elif found:
                entry["scale"] = scale_of(found[0][1])
            # the exploratory factor analysis of all statements (SEM extensions 2026-10-17, point 19): once per
            # participation, not in a loop or over people's means
            every = [c for _, codes in found for c in codes] if found else []
            if len(every) >= efa.MIN_ITEMS and not passes and person_ids is None:
                hidden = {c: {str(m) for m in item.kind.missing_codes(item, c)} for c in every}
                rows_ = [scales.values(item, every, r, "", hidden) for r in table]
                rows_ = [r for r in rows_ if all(v is not None for v in r)]
                try:
                    found_efa = efa.analyze(rows_) if rows_ else None
                except (ValueError, ArithmeticError, efa.np.linalg.LinAlgError):
                    found_efa = None
                if found_efa:
                    sub_of = {c: name for name, codes in found for c in codes}
                    found_efa["items"] = [{"code": c, "text": re.sub(r" \[[^\]]*\]", "", labels.get(c, "")).replace(" (rekodiert)", ""),
                                           "sub": sub_of.get(c)} for c in every]
                    entry["efa"] = found_efa
        if item.type == "semantic":  # a polarity profile in place of a list of frequencies per pair
            entry["profile"] = item.kind.profile(item, entry["columns"], passes, captions if loop_rows else {}, lang)
        if item.type == "psm":
            entry["psm"] = psm_curves(table, item, passes)
        if item.type == "position2d":
            entry["pos2d"] = item.kind.map_points(item, table, lang, passes)
        if item.type == "association":
            entry["terms"] = item.kind.terms(item, table, passes=passes)
        if item.type == "ranking":
            entry["ranks_norm"] = item.kind.results(item, table, lang, passes)
            if not entry["ranks_norm"]:
                entry["ranks_mean"] = item.kind.summary(item, table, lang, passes)
        if item.type == "card_sort":  # cards per group, pairs sorted together, names of own groups
            entry["cardsort"] = item.kind.results(item, table, lang, passes)
            entry["answered"] = entry["cardsort"]["persons"]
        if item.type == "q_sort":  # every statement in one table, most agreed first
            entry["qsort"] = item.kind.results(item, table, lang, passes)
            entry["answered"] = entry["qsort"]["persons"]
        if item.type == "image_mark" and item.kind.on_areas(item):  # hot spot: shares per area, on the image and as bars
            entry["hotspot"] = item.kind.results(item, table, lang, passes)
            entry["answered"] = entry["hotspot"]["persons"]
        if item.type == "highlight":
            entry["highlight"] = item.kind.results(item, table, lang)
            entry["answered"] = entry["highlight"]["persons"]
        if item.type == "cloze":
            entry["cloze"] = item.kind.results(item, table, lang)
            entry["answered"] = entry["cloze"]["persons"]
        if item.type == "map_marker":
            entry["map"] = item.kind.results(item, table, passes, lang)
            entry["answered"] = entry["map"]["persons"]
        if item.type == "geolocation":  # the shared positions on the map of the map with markers
            entry["map"] = item.kind.results(item, table, passes, lang)
        if item.type == "media":  # how many started and saw it to the end, the curve "up to where"
            entry["media"] = item.kind.results(item, table, lang, passes)
            entry["answered"] = entry["media"]["persons"]
        if item.type in ("iat", "discounting"):  # one line under the figures: valid D scores, nonsystematic data
            entry[item.type] = item.kind.results(item, table)
        if kind == "maxdiff":
            # choice models stay unweighted and rest on the persons of HB, holdouts and segments: in the weighted view
            # the counts and the aggregate model would else change with the switch (math audit 2026-10-06, L-6)
            entry["maxdiff"] = item.kind.results(item, hb_table, lang)
            entry["answered"] = entry["maxdiff"]["persons"]
            from app import maxdiff_hb

            entry["maxdiff"]["hb"] = maxdiff_hb.view(item, hb_table, item.kind.labels(item, lang))
            from app import holdouts

            entry["maxdiff"]["holdout"] = holdouts.maxdiff_view(item, hb_table, entry["maxdiff"])
            from app import segments

            entry["maxdiff"]["lc"] = segments.view(item, hb_table, lang)
        if kind == "paired":  # Bradley-Terry values, the table option x option, consistency (unweighted as MaxDiff, L-6)
            entry["paired"] = item.kind.results(item, hb_table, lang)
            entry["answered"] = entry["paired"]["persons"]
            from app import paired_hb

            entry["paired"]["hb"] = paired_hb.view(item, hb_table, [plain(x) for x in item.kind.labels(item, lang)])
        if kind == "aca":  # adaptive conjoint: self-explicated and pairs per person, their means (app/aca_analysis.py)
            entry["aca"] = item.kind.results(item, hb_table, lang)
            entry["answered"] = entry["aca"]["persons"]
        if kind == "cva":  # ratings-based conjoint: regressions per person, their means (unweighted as the choice models)
            entry["cva"] = item.kind.results(item, hb_table, lang)
            entry["answered"] = entry["cva"]["persons"]
            from app import cva_hb

            entry["cva"]["hb"] = cva_hb.view(item, hb_table, lang)  # HB regression (stage 2)
            from app import segments

            entry["cva"]["lc"] = segments.view(item, hb_table, lang)  # latent class regression (stage 4)
        if kind in ("acbc", "cbc"):
            entry[kind] = item.kind.results(item, hb_table, lang)  # as MaxDiff above (L-6)
            entry["answered"] = entry[kind]["counts"]["persons"]
            from app import conjoint_hb

            entry[kind]["hb"] = conjoint_hb.view(item, hb_table, lang)
            if kind == "cbc":  # holdout tasks: not for ACBC (W13)
                from app import holdouts

                entry[kind]["holdout"] = holdouts.cbc_view(item, hb_table, entry[kind])
            from app import segments

            if item.type in segments.TYPES:
                entry[kind]["lc"] = segments.view(item, hb_table, lang)
        if item.type == "postal_code":
            entry["postal"] = item.kind.results(item, table, passes)
            entry["answered"] = entry["postal"]["persons"]
        if item.type == "federal_state":
            entry["states"] = item.kind.results(item, table, passes, lang)
        if item.type == "date_time" and not entry["columns"]:  # a year alone is a number, summarised above
            entry["dates"] = item.kind.results(item, table, passes)
            entry["answered"] = entry["dates"]["persons"]
        if item.id in coded:  # categories of open answers (app/coding.py)
            entry["codings"] = coding.results(item, coded[item.id], table, wts)
        if item.id in coded_loops:  # in a loop: one list per pass (not in the group comparison)
            entry["codings_loop"] = [cl for n in passes for cl in coding.results(item, coded_loops[item.id], table, wts, f"_{n}")]
        out["items"].append(entry)
        info = constructs.info(item)
        # the construct after Spiel et al. or GES_1 of the RatSWD (app/ratswd.py)
        is_gender = (info and ("gender" in (info.get("key") or "").lower() or info.get("key") == "ratswd_ges1")) \
            or GENDER_CODES.search(item.code or "")
        if out["gender"] is None and kind == "categorical" and is_gender and entry["columns"] \
                and item.type != "multiple_choice":
            out["gender"] = entry
    if getattr(survey, "sampling", None) is not None:  # a diary study: the questions by part (Ralf, 2026-10-11)
        from app import diary

        rank = {k: n for n, k in enumerate(diary.part_keys(survey))}
        for entry in out["items"]:
            part = diary.part_of(entry["item"].block)
            entry["part"], entry["part_label"] = part, diary.part_label(part, survey)
        out["items"].sort(key=lambda e: rank.get(e["part"], 0))  # stable: the order within a part stays
    from app import segments

    for entry in out["items"]:  # segments: profiles over other questions, HB means and hit rates need the answers
        for kind in ("cbc", "acbc", "maxdiff", "cva"):
            lc_view = (entry.get(kind) or {}).get("lc")
            if lc_view and lc_view.get("result"):
                segments.enrich(entry["item"], lc_view, out["items"], table, out.get("weighting"))
    out["overview"] = overview(person_scores, lang, persons=person_ids)
    from app import vignettes

    # vignettes: means per level and a regression
    out["vignettes"] = vignettes.analysis(survey, table) if only is None and vignettes.nodes(survey) else []
    progress(0.95, de_en("Seite aufbauen", "Building the page"))
    return out


def _weighting(survey, table):
    """The weights of the pool (app/weights.py) with what the page says about them, or None without a weighting."""
    from app import weights

    return weights.compute(survey, table)


def refresh_estimates(data, survey):
    """Kept results (results_store) with the hierarchical Bayes estimates as they are now: the view of every
    MaxDiff and conjoint question read anew from the stored estimate and its marker (maxdiff_hb.revisit), so
    starting, running and ending an estimation does not compute the whole page again (Ralf, 2026-10-03). True
    when that is not enough: an estimate made with other settings since on other answers (only they tell whether it fits),
    or a question with holdout tasks whose estimate became current or stopped being so (the hit rates need the
    answers)."""
    from app import conjoint_hb, cva_hb, holdouts, maxdiff_hb, paired_hb, segments

    lang = survey.default_language
    again = False
    for e in data.get("items") or []:
        item = e["item"]
        for kind in ("maxdiff", "cbc", "acbc", "paired", "cva"):
            part = e.get(kind)
            if part and part.get("lc") and "input_key" in part["lc"]:
                # the segments as stored now, with the solution and names chosen since (item.config["lc"])
                part["lc"] = segments.revisit(item, part["lc"], lang)
                again = again or bool(part["lc"].get("unknown"))
            if not part or not part.get("hb") or "input_key" not in part["hb"]:
                continue
            before = part["hb"].get("state")
            if kind == "maxdiff":
                part["hb"] = maxdiff_hb.revisit(item, part["hb"], item.kind.labels(item, lang))
                active = holdouts.maxdiff_active(item)
            elif kind == "paired":
                part["hb"] = paired_hb.revisit(item, part["hb"], [plain(x) for x in item.kind.labels(item, lang)])
                active = False
            elif kind == "cva":
                part["hb"] = cva_hb.revisit(item, part["hb"], lang)
                active = False
            else:
                part["hb"] = conjoint_hb.revisit(item, part["hb"], lang)
                active = kind == "cbc" and holdouts.cbc_active(item)
            if part["hb"].get("unknown") or (active and (before == "current") != (part["hb"].get("state") == "current")):
                again = True
    return again


# --- the omega intervals, apart from the rest (performance audit 2026-10-06, P1) -----------------------------------
# The percentile bootstrap of omega (200 draws per scale) was most of the time of a cold results page. compute() leaves
# it out and marks the scales whose interval would be drawn ("omega_wait"); the page shows "wird berechnet" there and
# fetches the intervals as a part of their own (surveys._omega_part, static/omega.js), as it does for HB, simulator
# and segments. They are kept under the fingerprint of the kept results (results_store, name "omega"), so they are
# drawn once per state of the data, with the same seed and the same cases as before: the same numbers. No button
# (Ralf: no "compute anew" for results).

_omega_locks = {}  # (survey id, fingerprint) -> lock: one drawing per process, the others wait for it
_omega_guard = threading.Lock()  # around reading and trimming _omega_locks (code review 2026-10-15, A12)


def _omega_ref(item, name):
    """The key of a scale (a question, or one of its subscales) in the kept intervals and on the page."""
    return f"{item.id}:{name or ''}"


def _scales_of(entry):
    """[(subscale name or None, figures)] of a results entry."""
    if entry.get("subscales"):
        return [(s["name"], s) for s in entry["subscales"]]
    return [(None, entry["scale"])] if entry.get("scale") else []


def omega_waiting(data):
    """The keys of the scales of `data` whose omega interval is still to be drawn."""
    return {_omega_ref(e["item"], name) for e in data.get("items") or [] if e.get("item") is not None
            for name, s in _scales_of(e) if s.get("omega_wait")}


def apply_omega(data, found):
    """Put the intervals `found` ({key: {"ci", "failed", "pooled"}}) into the scales and the overview of `data` that
    wait for them. Changes `data` in place."""
    codes = {}
    for e in data.get("items") or []:
        if e.get("item") is None:
            continue
        for name, s in _scales_of(e):
            ref = _omega_ref(e["item"], name)
            codes[(e["item"].code, name)] = ref
            if s.get("omega_wait") and ref in found:
                _put_omega(s, found[ref])
    for row in (data.get("overview") or {}).get("rows") or []:
        ref = codes.get((row.get("code"), row.get("sub")))
        if row.get("omega_wait") and ref in found:
            _put_omega(row, found[ref])


def _put_omega(figures, one):
    figures.pop("omega_wait", None)
    figures["omega_ci"] = one.get("ci")
    if one.get("failed"):
        figures["omega_failed"] = one["failed"]


def draw_omega(survey, f, item_ids):
    """{key: {"ci", "failed", "pooled"}} of every scale of these questions, drawn on the data pool f as compute() reads
    it for the page (the same table, the same cases, the same seed)."""
    from app import omega_job

    return omega_job.run({"key": None, "scales": omega_inputs(survey, f, item_ids)})["found"]


def omega_inputs(survey, f, item_ids):
    """{key: {"matrix", "persons", "pooled"}}: what the bootstrap of every scale of these questions needs (numbers
    only: the complete answers and per row the number of its row in the table), as compute() reads them."""
    data = compute(survey, f["sources"], f["all"], f["drop"], f["speeder"], f.get("speed_real", True),
                   only=set(item_ids), weighted=bool(f.get("weighted")), omega_ci="collect")
    found = {}
    for e in data["items"]:
        for name, s in _scales_of(e):
            if s.get("omega") is not None:
                found[_omega_ref(e["item"], name)] = {**s["omega_input"], "pooled": bool(s.get("pooled"))}
    return found


def scale_items(survey):
    """Ids of the questions that can have a scale (app/scales.py), for drawing without kept results."""
    return {i.id for b in survey.blocks for i in b.items if i.kind.result_kind and scales.groups(i)}


def omega_intervals(survey, f, key, wanted=None):
    """The omega intervals of the data pool f under the fingerprint key: kept ones, plus those of `wanted` (keys of
    omega_waiting) that are missing, drawn now and kept with them; without `wanted` every scale of the survey.
    key None: drawn, not kept."""
    import threading

    from app import results_store

    def kept():
        hit = results_store.load(survey, key, f, name="omega") if key else None
        return dict(hit[0]) if hit else {}

    def done(found):
        return bool(found) if wanted is None else not (wanted - set(found))

    found = kept()
    if done(found):
        return found
    with _omega_guard:
        lock = _omega_locks.setdefault((survey.id, key), threading.Lock())
    with lock:  # a second request of this process (another tab) waits and takes what the first one kept
        found = kept()
        if done(found):
            return found
        ids = {int(ref.split(":", 1)[0]) for ref in wanted - set(found)} if wanted is not None else scale_items(survey)
        found.update(draw_omega(survey, f, ids))
        if key:
            results_store.save(survey, key, found, f, name="omega", basis=key)
    _trim_omega_locks()
    return found


def _trim_omega_locks(most=64):
    """Above `most` locks drop those no drawing holds; clear() dropped held ones too, and a request coming then drew
    beside the one still running (code review 2026-10-15, A12)."""
    with _omega_guard:
        if len(_omega_locks) > most:
            for k, held in list(_omega_locks.items()):
                if not held.locked():
                    del _omega_locks[k]


def omega_part_state(survey, f, key, wanted=None):
    """For the omega part of the results page: (the intervals, None) once all wanted ones are there, else ({}, the
    marker of their bootstrap: {"state": "running"|"error", "progress", ...}). The bootstrap runs as an estimation of
    its own (app/omega_job.py: the process at low priority, the Rechenknecht, or a thread), not in this request; its
    result is kept under the fingerprint as omega_intervals keeps it. OMEGA_BACKGROUND off: drawn in the request."""
    import hashlib
    import json
    import os

    from flask import current_app

    from app import estimate, jobs, maxdiff_hb, results_store

    app = current_app._get_current_object()
    if not app.config.get("OMEGA_BACKGROUND") or not key:
        return omega_intervals(survey, f, key, wanted), None
    hit = results_store.load(survey, key, f, name="omega")
    found = dict(hit[0]) if hit else {}
    ids = ({int(ref.split(":", 1)[0]) for ref in wanted - set(found)} if wanted is not None
           else (scale_items(survey) if not found else set()))
    if not ids:
        return found, None
    job = hashlib.sha1(json.dumps([key, f, sorted(ids)], sort_keys=True, default=str).encode()).hexdigest()[:20]
    folder = os.path.join(app.config["UPLOAD_FOLDER"], "hb", survey.uid)
    paths = os.path.join(folder, f"omega-{job}.json"), os.path.join(folder, f"omega-{job}.run.json")
    marker = jobs.read(paths)  # on the Rechenknecht: a finished result is fetched here
    done = maxdiff_hb._read(paths[0])
    if done and done.get("key") == job:  # the bootstrap is through: kept with the others, its file gone
        found.update(done["found"])
        results_store.save(survey, key, found, f, name="omega", basis=key)
        estimate._remove(paths[0])
        return found, None
    if marker and marker.get("state") == "running":
        marker = jobs.settle(paths, marker)  # silent and its process gone: once more in a thread
        if marker and marker.get("state") == "running":
            return {}, marker
    if marker and marker.get("state") == "error":
        estimate._remove(paths[1])  # shown once; the next look starts again
        return {}, marker
    inp = {"key": job, "scales": omega_inputs(survey, f, ids)}
    pool = {"omega": key}
    jobs.start(app, "omega", paths, inp, pool, name=f"omega-{survey.id}")  # False: one runs already
    return {}, maxdiff_hb._read(paths[1]) or {"state": "running", "progress": 0.0}


def fill_omega(survey, data, f, key, items=None):
    """The omega intervals into `data` (results of compute() for the pool f, kept under key) where they wait: kept
    ones, else drawn now (the Word table of the scales and the pooled constructs need them at once). items: only the
    scales of these question ids."""
    wanted = omega_waiting(data)
    if items is not None:
        wanted = {ref for ref in wanted if int(ref.split(":", 1)[0]) in items}
    if wanted:
        apply_omega(data, omega_intervals(survey, f, key, wanted))
    return data


# short references (APA, Ralf 2026-10-05; numbered footnotes before): at the place in the results the short reference
# of a group's sources, linked to the list in the one fold at the end of the page; key -> the start of the group's
# heading in page_sources, German and English (the headings are written in the language of the page); the groups of
# the simulator (app/simulator.py) start the same way
FOOTNOTES = {
    "ord": ("Median und Quartile", "Median and quartiles"), "gini": ("Gini-Koeffizient", "Gini coefficient"),
    "simpson": ("Gini-Simpson", "Gini-Simpson"), "distance": ("Entfernungen auf der Karte", "Distances on the map"),
    "rel": ("Reliabilität: Cronbachs", "Reliability: Cronbach's"), "sb": ("Reliabilität bei zwei", "Reliability with two"),
    "pooled": ("Reliabilität über Schleifen", "Reliability over loop"), "partial": ("Skalenmittelwert über", "Scale mean over"),
    "wilson": ("95-%-Konfidenzintervalle der Anteile", "95 % confidence intervals of the shares"),
    "feldt": ("95-%-Konfidenzintervall von Cronbachs", "95 % confidence interval of Cronbach's"),
    "omega_ci": ("95-%-Konfidenzintervall von McDonalds", "95 % confidence interval of McDonald's"),
    "items": ("Itemanalyse", "Item analysis"), "corr": ("Korrelationen", "Pearson correlations"),
    "psm": ("Preisbereitschaft", "Willingness to pay: Van Westendorp"),
    "psm_ci": ("95-%-Konfidenzintervalle der Preispunkte", "95 % confidence intervals of the price points"),
    "nms": ("Kaufbereitschaft und Umsatzindex", "Purchase intention and revenue index"),
    "maxdiff": ("MaxDiff (Best", "MaxDiff (best"), "anchor": ("MaxDiff: direkte", "MaxDiff: direct"),
    "paired": ("Paarvergleich: Bradley", "Paired comparison: Bradley"),
    "paired_ties": ("Paarvergleich: „gleich“", "Paired comparison: “same”"),
    "paired_order": ("Paarvergleich: Positionseffekt", "Paired comparison: position effect"),
    "paired_consistency": ("Paarvergleich: Konsistenz", "Paired comparison: consistency"),
    "cva": ("Bewertungs-Conjoint", "Ratings-based conjoint"),
    "aca": ("Adaptive Conjoint (ACA)", "Adaptive conjoint (ACA)"),
    "cva_hb": ("Bewertungs-Conjoint: Hierarchical", "Ratings-based conjoint: hierarchical"),
    "mnl": ("Aggregiertes Logit", "Aggregate logit"), "wtp": ("Zahlungsbereitschaft", "Willingness to pay: Fieller"),
    "acbc": ("Adaptive Conjoint", "Adaptive conjoint"), "hb": ("Individuelle Nutzen", "Individual utilities"),
    "convergence": ("Konvergenz der HB", "Convergence of the HB"), "holdout": ("Holdout", "Holdout"),
    "speeder": ("Speeder", "Speeder"), "weighting": ("Gewichtung: Raking", "Weighting: raking"),
    "mean_ci": ("95-%-Konfidenzintervalle der Mittelwerte", "95 % confidence intervals of the means"),
    "random": ("Voraussetzung der Konfidenzintervalle", "Assumption of the confidence intervals"),
    "clock": ("Uhrzeiten auf der Uhr", "Times on the clock"),
    "aapor": ("Gewichtung und Repräsentativität", "Weighting and representativeness"),
    "sampler": ("HB-Sampler", "HB sampler"), "ridge": ("Nie oder immer gewählt", "Never or always chosen"),
    "icc": ("Vignetten: Intraklassen", "Vignettes: intraclass"),
    "lc": ("Segmente: Latent-Class", "Segments: latent class"), "lc_crit": ("Segmente: Zahl der", "Segments: number of"),
    "lc_start": ("Segmente: Startwerte", "Segments: starting values"),
    "lc_size": ("Segmente: kleinstes Segment", "Segments: smallest segment"),
    "lc_entropy": ("Segmente: Entropie", "Segments: entropy"), "lc_kmax": ("Segmente: höchstens", "Segments: at most"),
    "lc_se": ("Segmente: Standardfehler", "Segments: standard errors"),
    "lc_scale": ("Segmente: Skalenunterschiede", "Segments: scale differences"),
    "lc_profile": ("Segmente beschreiben", "Describing segments"),
    "lc_bch": ("Segmente: Anteile und Mittelwerte", "Segments: shares and means"),
    "lc_cov": ("Segmente: Kovariaten", "Segments: covariates"),
    "iat": ("IAT: D-Wert", "IAT: D score"), "dd": ("Belohnungsaufschub: Anpassungsverfahren", "Delay discounting: adjusting"),
    "dd_quick": ("Belohnungsaufschub: Kurzform", "Delay discounting: short form"),
    "jb": ("Belohnungsaufschub: nicht systematische", "Delay discounting: non-systematic"),
    "sim": ("Marktsimulator", "Market simulator"), "sim_draws": ("Präferenzanteile je Person", "Preference shares per person"),
    "sim_ci": ("95-%-Intervalle der Präferenzanteile", "95 % intervals of the preference shares"),
    "sim_opt": ("Produkt optimieren", "Optimising a product"),
    "sim_curse": ("Produkt optimieren: Anteil der besten", "Optimising a product: share of the best"),
    "price_linear": ("Preis linear oder in Stufen", "Price linear or in levels"),
    "compare_order": ("Unterschiede auf einen Blick", "Differences at a glance"),
}
# a group of the fold that rests on intervals or p values (its heading, German or English): then the page says that
# they assume a random sample (audit 2026-10-05), with the AAPOR report that the weighting cites as well
INFERENCE = re.compile(r"Konfidenzintervall|Intervall|[Tt]est|ANOVA|Mann-Whitney|Kruskal|Brown-Forsythe|[Ii]nterval")


def with_sampling_note(groups):
    """groups of page_sources (and of the group comparison and the simulator) plus one that says what intervals and
    p values assume, when one of them is on the page; with a weighting, its AAPOR group carries the sentence."""
    from app import weights

    if not any(INFERENCE.search(heading) for heading, _ in groups):
        return groups
    note = de_en("Voraussetzung der Konfidenzintervalle und p-Werte: eine Zufallsstichprobe. Bei Gelegenheitsstichproben "
                 "(meist der Fall) gelten sie nur unter dieser Annahme und unterschätzen die Unsicherheit",
                 "Assumption of the confidence intervals and p values: a random sample. With convenience samples "
                 "(mostly the case) they hold only under this assumption and underestimate the uncertainty")
    out = [(note + de_en("; Gewichtung und Repräsentativität nicht zufälliger Stichproben",
                         "; weighting and representativeness of non-probability samples"), cited)
           if cited == [weights.AAPOR] else (heading, cited) for heading, cited in groups]
    return out if out != groups else groups + [(note, [weights.AAPOR])]


def footnotes(groups):
    """{key: [citations]} of the groups of page_sources that are on the page, for the short references in the text."""
    out = {}
    for heading, cited in groups:
        for key, starts in FOOTNOTES.items():
            if key not in out and any(heading.startswith(start) or (key == "aapor" and start.lower() in heading.lower())
                                      for start in starts):
                out[key] = list(cited)
    return out


def page_sources(data):
    """The sources of the results page, one group per procedure that the page shows: [(heading, [citations])], for
    the one fold at its end (_sources.html, grouped). A group is left out when nothing on the page uses it."""
    from app import acbc_design, choice_model, hb, hb_convergence, maxdiff_analysis, maxdiff_hb, quality
    from app.items import map_marker, psm

    entries = list(data.get("items") or [])
    columns = [c for e in entries for c in e.get("columns") or []]

    def hb_result(e, kind):
        return bool(((e.get(kind) or {}).get("hb") or {}).get("result"))

    scales_shown = any(e.get("scale") or e.get("subscales") for e in entries)
    partial = any((e.get("scale") or {}).get("partial") or any(s.get("partial") for s in e.get("subscales") or [])
                  for e in entries)
    overview_rows = ((data.get("overview") or {}).get("rows") or [])
    choice = [e[k] for e in entries for k in ("cbc", "acbc") if e.get(k)]
    models = [x.get("model") for x in choice if x.get("model")]
    maxdiffs = [e["maxdiff"] for e in entries if e.get("maxdiff") and e["maxdiff"].get("persons")]
    psms = [e["psm"] for e in entries if e.get("psm")]
    groups = []
    if any(e.get("efa") for e in entries):  # the exploratory factor analysis of a scale (SEM extensions 2026-10-17)
        groups.append((de_en("Explorative Faktorenanalyse: Parallelanalyse, iterierte Hauptachsen, Oblimin",
                             "Exploratory factor analysis: parallel analysis, iterated principal axes, oblimin"),
                       list(efa.SOURCES)))
    if data.get("vignettes"):
        from app import doe, vignettes

        groups.append((de_en("Vignetten: Regression mit nach Person geclusterten Standardfehlern (CR1)",
                             "Vignettes: regression with standard errors clustered by person (CR1)"),
                       [vignettes.CAMERON_MILLER, doe.AUSPURG_HINZ]))
        groups.append((de_en("Vignetten: Intraklassenkorrelation ICC(1) aus der einfaktoriellen Varianzanalyse, für ungleich "
                             "viele Vignetten je Person mit der mittleren Gruppengröße n₀",
                             "Vignettes: intraclass correlation ICC(1) from the one-way analysis of variance, for unequal "
                             "numbers of vignettes per person with the mean group size n₀"),
                       [vignettes.SHROUT_FLEISS, vignettes.DONNER]))
    if any(c.get("type") == "ord" for c in columns):
        groups.append((de_en("Median und Quartile geordneter Antworten: die erste Stufe, bis zu der der Anteil erreicht ist "
                             "(Typ 1 nach Hyndman & Fan)",
                             "Median and quartiles of ordered answers: the first level up to which the share is reached "
                             "(type 1 after Hyndman & Fan)"), [HYNDMAN]))
    if any((e.get("dates") or {}).get("over_midnight") for e in entries):
        from app.items import date_time

        groups.append((de_en("Uhrzeiten auf der Uhr: zirkulärer Median (Bogen der Antworten, geschnitten an der größten Lücke)",
                             "Times on the clock: circular median (arc of the answers, cut at the largest gap)"),
                       [date_time.MARDIA_JUPP]))
    if any(e.get("iat") for e in entries):
        from app.items import iat

        groups.append((de_en("IAT: D-Wert nach dem verbesserten Algorithmus mit eingebauter Fehlerkorrektur; Block 5 mit 28 "
                             "Durchgängen",
                             "IAT: D score by the improved algorithm with built-in error correction; block 5 with 28 "
                             "trials"), list(iat.SOURCES)))
    dds = [e["discounting"] for e in entries if e.get("discounting")]
    if dds:
        from app.items import discounting

        if any(d["mode"] == "adjusting" for d in dds):
            groups.append((de_en("Belohnungsaufschub: Anpassungsverfahren, k der Hyperbel (kleinste Quadrate der "
                                 "Indifferenzpunkte) und Fläche unter der Kurve",
                                 "Delay discounting: adjusting procedure, k of the hyperbola (least squares of the "
                                 "indifference points) and area under the curve"),
                           [discounting.DU, discounting.MAZUR, discounting.MYERSON]))
            groups.append((de_en("Belohnungsaufschub: nicht systematische Daten", "Delay discounting: non-systematic data"),
                           [discounting.JOHNSON_BICKEL]))
        if any(d["mode"] == "quick" for d in dds):
            groups.append((de_en("Belohnungsaufschub: Kurzform mit fünf Fragen, ED50 aus der fünften Wahl",
                                 "Delay discounting: short form with five questions, ED50 from the fifth choice"),
                           [discounting.KOFFARNUS]))
    if any(c.get("type") == "num" and c.get("ratio") and c.get("gini") is not None for c in columns):
        groups.append((de_en("Gini-Koeffizient", "Gini coefficient"), [GINI]))
    if any(c.get("type") == "freq" and c.get("gini_simpson") is not None for c in columns):
        groups.append((de_en("Gini-Simpson-Index", "Gini-Simpson index"), [SIMPSON, JOST]))
    if any(c.get("type") == "num" and c.get("unit") == "m" and c.get("n") for c in columns):
        groups.append((de_en("Entfernungen auf der Karte: Großkreis (Haversine) mit dem mittleren Erdradius",
                             "Distances on the map: great circle (haversine) with the mean radius of the earth"),
                       [map_marker.SINNOTT, map_marker.MORITZ]))
    shown = {c.get("code") for c in columns if c.get("type") == "num" and c.get("n")}
    maps = [e["item"].code for e in entries if getattr(e.get("item"), "type", None) == "map_marker"]
    if any(f"{code}_path_m" in shown for code in maps):
        groups.append((de_en("Kürzester Pfad durch alle Marker: exakt (Held-Karp), bei vielen Markern Näherung (2-opt)",
                             "Shortest path through all markers: exact (Held-Karp), with many markers an approximation (2-opt)"),
                       [map_marker.HELD_KARP, map_marker.CROES]))
    if any(f"{code}_mst_m" in shown for code in maps):
        groups.append((de_en("Minimaler Spannbaum der Marker", "Minimum spanning tree of the markers"), [map_marker.PRIM]))
    if any(f"{code}_density" in shown for code in maps):
        groups.append((de_en("Dichte der Marker: Anteil verbundener Paare (Graphendichte)",
                             "Density of the markers: share of connected pairs (graph density)"), [map_marker.WASSERMAN_FAUST]))
    if scales_shown or overview_rows:
        groups.append((de_en("Reliabilität: Cronbachs α und McDonalds ω (Ein-Faktor-Modell, Maximum Likelihood)",
                             "Reliability: Cronbach's α and McDonald's ω (one-factor model, maximum likelihood)"),
                       list(scales.SOURCES)))
    every = [s for e in entries for s in ([e["scale"]] if e.get("scale") else []) + list(e.get("subscales") or [])]
    if any(s.get("k") == 2 for s in every):
        groups.append((de_en("Reliabilität bei zwei Aussagen: Spearman-Brown-Koeffizient",
                             "Reliability with two statements: Spearman-Brown coefficient"), [scales.EISINGA]))
    if any(s.get("pooled") for s in every):
        from app.weights import KISH

        groups.append((de_en("Reliabilität über Schleifendurchgänge: Fälle derselben Person sind nicht unabhängig; die "
                             "Intervalle rechnen deshalb über Personen (Designeffekt der Durchgänge)",
                             "Reliability over loop passes: cases of the same person are not independent; so the "
                             "intervals are computed over people (design effect of the passes)"), [scales.GELDHOF, KISH]))
    if partial:
        groups.append((de_en("Skalenmittelwert über die gezeigten Aussagen (anteilige Mittelung)",
                             "Scale mean over the statements shown (proration)"), [row_logic.MAZZA]))
    # 95 % confidence intervals (app/intervals.py)
    shares = [r for c in columns if c.get("type") in ("freq", "ord") for r in c.get("rows") or []]
    shares += [r for e in entries for m in e.get("multi") or [] for r in m.get("rows") or []]
    if any(r.get("ci") for r in shares):
        groups.append((de_en("95-%-Konfidenzintervalle der Anteile: Wilson-Intervall",
                             "95% confidence intervals of the shares: Wilson interval"), [intervals.WILSON]))
    means = [c for c in columns if c.get("type") == "num"] + every
    if any(m.get("ci") or m.get("mean_ci") for m in means):
        groups.append((de_en("95-%-Konfidenzintervalle der Mittelwerte: t-Verteilung",
                             "95% confidence intervals of the means: t distribution"), [intervals.STUDENT]))
    if any(s.get("alpha_ci") for s in every) or any(r.get("alpha_ci") for r in overview_rows):
        groups.append((de_en("95-%-Konfidenzintervall von Cronbachs α über die F-Verteilung (Feldt-Intervall)",
                             "95% confidence interval of Cronbach's α via the F distribution (Feldt interval)"),
                       [intervals.FELDT]))
    # an interval still being computed (omega_wait, fill_omega) is there once it is: the group from the start
    if any(s.get("omega_ci") or s.get("omega_wait") for s in every) \
            or any(r.get("omega_ci") or r.get("omega_wait") for r in overview_rows):
        groups.append((de_en("95-%-Konfidenzintervall von McDonalds ω: Perzentil-Bootstrap mit {draws} Ziehungen; "
                             "Ziehungen ohne schätzbares ω zählen als kleiner als jeder Wert",
                             "95% confidence interval of McDonald's ω: percentile bootstrap with {draws} draws; "
                             "draws without an estimable ω count as smaller than any value", draws=intervals.OMEGA_DRAWS),
                       [intervals.KELLEY, intervals.EFRON]))
    if any(s.get("item_stats") for s in every):
        groups.append((de_en("Itemanalyse: Schwierigkeit, korrigierte Trennschärfe, α ohne die Aussage; hervorgehoben werden "
                             "Trennschärfen unter {low} und Aussagen, ohne die α steigt (Festlegung von Askurious)",
                             "Item analysis: difficulty, corrected item-total correlation, α without the statement; highlighted "
                             "are item-total correlations under {low} and statements without which α rises (set by Askurious)",
                             low=units.number(scales.ITEM_RIT_LOW, 2, units.page_lang())), [scales.LIENERT]))
    if len(overview_rows) > 1:
        groups.append((de_en("Korrelationen nach Pearson und ihr t-Test", "Pearson correlations and their t test"),
                       list(scales.CORRELATION_SOURCES)))
    if psms:
        groups.append((de_en("Preisbereitschaft: Price Sensitivity Meter nach Van Westendorp",
                             "Willingness to pay: Van Westendorp's Price Sensitivity Meter"), [psm.CITATION]))
    if any(a.get("ci") and any(a["ci"].values()) for a in psms):
        groups.append((de_en("95-%-Konfidenzintervalle der Preispunkte: Perzentil-Bootstrap über Personen mit {draws} Ziehungen",
                             "95% confidence intervals of the price points: percentile bootstrap over people with {draws} draws",
                             draws=intervals.PSM_DRAWS), [intervals.EFRON]))
    if any(a.get("nms") for a in psms):
        groups.append((de_en("Kaufbereitschaft und Umsatzindex nach Newton et al. (Umrechnung der Antworten in "
                             "Kaufwahrscheinlichkeiten: Festlegung von Askurious)",
                             "Purchase intention and revenue index after Newton et al. (conversion of the answers "
                             "into purchase probabilities: Askurious default)"), [psm.NMS_CITATION]))
    if maxdiffs:
        groups.append((de_en("MaxDiff (Best-Worst-Skalierung): Zählwerte und sequenzielles Best-Worst-Logit",
                             "MaxDiff (best-worst scaling): counts and sequential best-worst logit"),
                       list(maxdiff_analysis.SOURCES)))
    paireds = [e["paired"] for e in entries if e.get("paired") and e["paired"].get("persons")]
    if paireds:
        from app import paired_analysis as pa
        from app.paired_design import FORD

        groups.append((de_en("Paarvergleich: Bradley-Terry-Modell; Thurstones Fall V ergäbe dieselbe Reihenfolge; verbundener "
                             "Versuchsplan nach Ford", "Paired comparison: Bradley-Terry model; Thurstone's case V would give the "
                             "same order; connected design after Ford"), [pa.BRADLEY_TERRY, pa.THURSTONE, pa.CATTELAN, FORD]))
        if any(p["answer"] != "choice" for p in paireds):
            groups.append((de_en("Paarvergleich: „gleich“ und Stärke im Modell benachbarter Kategorien",
                                 "Paired comparison: “same” and strength in the adjacent-categories model"),
                           [pa.DAVIDSON, pa.AGRESTI, pa.SCHEFFE]))
        groups.append((de_en("Paarvergleich: Positionseffekt", "Paired comparison: position effect"), [pa.DAVIDSON_BEAVER]))
        if any(p.get("consistency") for p in paireds):
            groups.append((de_en("Paarvergleich: Konsistenz (zirkuläre Triaden) und Übereinstimmung",
                                 "Paired comparison: consistency (circular triads) and agreement"), [pa.KENDALL]))
    if any(e.get("aca") for e in entries):
        from app import aca_design

        groups.append((de_en("Adaptive Conjoint (ACA): Selbstauskunft und adaptive Paare, Schätzung und Kalibrierung; "
                             "unterschätzt den Preis",
                             "Adaptive conjoint (ACA): self-explication and adaptive pairs, estimation and calibration; "
                             "underestimates the price"),
                       [aca_design.JOHNSON, aca_design.GREEN_KRIEGER_AGARWAL, aca_design.PINNELL]))
    if any(hb_result(e, "cva") for e in entries):
        from app import cva_hb

        groups.append((de_en("Bewertungs-Conjoint: Hierarchical-Bayes-Regression (lineares Modell je Person, Gibbs-Sampler mit "
                             "konjugierten Verteilungen); Konvergenz über rang-normalisiertes split-R̂ und effektive "
                             "Stichprobengröße wie bei den Wahlmodellen",
                             "Ratings-based conjoint: hierarchical Bayes regression (linear model per person, Gibbs sampler "
                             "with conjugate distributions); convergence by rank-normalised split-R̂ and effective sample "
                             "size as for the choice models"),
                       [cva_hb.LENK, hb.ROSSI, hb_convergence.VEHTARI]))
    if any(e.get("cva") and e["cva"].get("model") for e in entries):
        from app import cva_design

        groups.append((de_en("Bewertungs-Conjoint: Regression je Person, Mittel über Personen mit t-Intervall",
                             "Ratings-based conjoint: regression per person, means over people with a t interval"),
                       [cva_design.GREEN_SRINIVASAN]))
    if any(m.get("anchor_n") for m in maxdiffs):
        groups.append((de_en("MaxDiff: direkte Verankerung mit der Ankerfrage", "MaxDiff: direct anchoring with the anchor question"),
                       [maxdiff_analysis.LATTERY]))
    if models or any(m.get("mnl") and not m["mnl"].get("disconnected") for m in maxdiffs) \
            or any(p.get("model") and not p["model"].get("disconnected") for p in paireds):
        groups.append((de_en("Aggregiertes Logit-Modell (bedingtes Logit) mit Standardfehlern je Person (cluster-robust); "
                             "unter {n} Personen 95-%-Intervalle mit dem t-Quantil für G − 1 Freiheitsgrade",
                             "Aggregate logit model (conditional logit) with standard errors per person (cluster-robust); "
                             "under {n} people 95% intervals with the t quantile for G − 1 degrees of freedom",
                             n=choice_model.T_UNTIL), list(choice_model.SOURCES)))
    if any(m.get("ridge") for m in models) or any(r.get("separated") for m in maxdiffs
                                                    for r in ((m.get("mnl") or {}).get("rows") or [])):
        groups.append((de_en("Nie oder immer gewählt (Separation): Ohne Grenze gäbe es keine Maximum-Likelihood-Schätzung, "
                             "deshalb eine schwache Ridge-Grenze",
                             "Never or always chosen (separation): without a bound there would be no maximum likelihood "
                             "estimate, hence a weak ridge bound"), [choice_model.ALBERT_ANDERSON]))
    if any(m.get("wtp_ok") for m in models) or any(
            True in (((((e.get(k) or {}).get("lc") or {}).get("result") or {}).get("price") or {}).get("wtp") or [])
            for e in entries for k in ("cbc", "acbc")):
        groups.append((de_en("Zahlungsbereitschaft: 95-%-Intervall nach Fieller; bei nur knapp bestimmtem Preiskoeffizienten "
                             "(|z| unter 4, Grenze von Askurious festgelegt) Hinweis, dass sie eher zu klein ausfällt",
                             "Willingness to pay: Fieller's 95% interval; with a barely determined price coefficient "
                             "(|z| under 4, a bound set by Askurious) a note that it tends to come out too small"),
                       [choice_model.FIELLER, choice_model.HOLE]))
    if any(m.get("price_linear") for m in models):
        groups.append((de_en("Preis linear oder in Stufen: Likelihood-Quotienten-Test der geschachtelten Logit-Modelle",
                             "Price linear or in levels: likelihood ratio test of the nested logit models"),
                       [choice_model.TRAIN]))
    if any(e.get("acbc") for e in entries):
        groups.append((de_en("Adaptive Conjoint (ACBC): Ablauf aus Konfiguration, Auswahl und Turnier",
                             "Adaptive conjoint (ACBC): flow of build-your-own, screening and tournament"),
                       [acbc_design.JOHNSON_ORME_2007]))
    if any(hb_result(e, k) for e in entries for k in ("maxdiff", "cbc", "acbc", "paired")):
        groups.append((de_en("Individuelle Nutzen: Hierarchical Bayes (multinomiales Logit); Root Likelihood (RLH) je Person, "
                             "die Grenze {factor} × Zufall für mögliche Zufallsklicker ist eine Festlegung von Askurious",
                             "Individual utilities: hierarchical Bayes (multinomial logit); root likelihood (RLH) per person, "
                             "the bound {factor} × chance for possible random clickers is set by Askurious",
                             factor=units.number(maxdiff_hb.RLH_FACTOR, 1, units.page_lang())), list(hb.SOURCES)))
        groups.append((de_en("HB-Sampler: Langevin-Schritt je Person mit eigener, im Burn-in auf eine Ziel-Akzeptanz "
                             "geregelter Schrittweite (Random-Walk-Schritte: Ziel 0,3), im letzten Viertel des Burn-ins mit "
                             "abnehmendem Gewinn nachgeregelt und gemittelt (Robbins-Monro), gemeinsame Verschiebung und "
                             "Streckung aller Personen",
                             "HB sampler: Langevin step per person with its own step size, tuned in the burn-in to a target "
                             "acceptance (random walk steps: target 0.3), in the last quarter of the burn-in adjusted with "
                             "decreasing gain and averaged (Robbins-Monro), joint shift and stretch of all people"),
                       [hb.ROBERTS_ROSENTHAL, hb.ROBERTS_GELMAN_GILKS, hb.LIU_SABATTI, hb.ANDRIEU_THOMS,
                        hb_convergence.VEHTARI]))
        groups.append((de_en("Konvergenz der HB-Kette: rang-normalisiertes split-R̂ und effektive Stichprobengröße (Geyers "
                             "monotone Anfangsfolge, nach Vehtari et al., 2021); Regel R̂ < {rhat} und ESS ≥ {ess}; dazu ein Geweke-artiger "
                             "Vergleich der Hälften (Batch-Mittel statt Spektraldichten)",
                             "Convergence of the HB chain: rank-normalised split-R̂ and effective sample size (Geyer's "
                             "initial monotone sequence, after Vehtari et al., 2021); rule R̂ < {rhat} and ESS ≥ {ess}; plus a Geweke-like "
                             "comparison of the halves (batch means instead of spectral densities)",
                             rhat=units.number(hb_convergence.RHAT_LIMIT, 2, units.page_lang()), ess=hb_convergence.ESS_MIN),
                       list(hb_convergence.SOURCES) + [hb.GEWEKE]))
    lc_logit = any(((e.get(k) or {}).get("lc") or {}).get("result") for e in entries for k in ("maxdiff", "cbc", "acbc"))
    lc_reg = any(((e.get("cva") or {}).get("lc") or {}).get("result") for e in entries)  # app/lc_regression.py
    if lc_logit or lc_reg:
        from app import latent_class as lc, lc_regression

        if lc_logit:
            groups.append((de_en("Segmente: Latent-Class-Logit (Mischung von Logit-Modellen, Personen in genau einem "
                                 "Segment), geschätzt mit dem EM-Algorithmus, je Segment ein gewichteter Newton-Schritt und "
                                 "eine schwache Normal-Priori",
                                 "Segments: latent class logit (mixture of logit models, people in exactly one segment), "
                                 "estimated with the EM algorithm, per segment a weighted Newton step and a weak normal "
                                 "prior"), [lc.KAMAKURA, lc.WEDEL, lc.GREENE, lc.DEMPSTER, lc.MENG, lc.FRALEY]))
        if lc_reg:
            groups.append((de_en("Segmente: Latent-Class-Regression der Bewertungs-Conjoint (Mischung linearer Regressionen, "
                                 "Personen in genau einem Segment), geschätzt mit dem EM-Algorithmus, je Segment gewichtete "
                                 "kleinste Quadrate und eine eigene Reststreuung",
                                 "Segments: latent class regression of the ratings-based conjoint (mixture of linear "
                                 "regressions, people in exactly one segment), estimated with the EM algorithm, per segment "
                                 "weighted least squares and a residual spread of its own"),
                           [lc_regression.DESARBO, lc.WEDEL, lc.DEMPSTER, lc.MCLACHLAN]))
        groups.append((de_en("Segmente: Zahl der Segmente nach BIC (N = Personen), daneben AIC3, CAIC und Entropie R²",
                             "Segments: number of segments by BIC (N = people), beside it AIC3, CAIC and entropy R²"),
                       [lc.SCHWARZ, lc.ANDREWS_CURRIM, lc.BOZDOGAN, lc.NYLUND, lc.RAMASWAMY]))
        groups.append((de_en("Segmente: kleinstes Segment mindestens 5 % der Personen bzw. 50 Personen, je nachdem, was mehr "
                             "ist (Faustregeln, keine festen Grenzen)",
                             "Segments: smallest segment at least 5% of the people or 50 people, whichever is more "
                             "(rules of thumb, no fixed bounds)"), [lc.NYLUND_GIBSON, lc.WELLER, lc.SORGENTE]))
        groups.append((de_en("Segmente: Entropie R² ab 0,8 gut, unter 0,6 schwach",
                             "Segments: entropy R² good from 0.8, weak under 0.6"), [lc.NYLUND_GIBSON, lc.WELLER]))
        groups.append((de_en("Segmente: höchstens 6 Segmente, 5 Personen je Parameter und mindestens 60 Personen sind "
                             "Festlegungen von Askurious (mehr Segmente sind in der Praxis kaum nutzbar); die Wahl der Zahl "
                             "der Segmente folgt den Informationskriterien",
                             "Segments: at most 6 segments, 5 people per parameter and at least 60 people are set by "
                             "Askurious (more segments are hardly usable in practice); the number of segments is chosen by "
                             "the information criteria"), [lc.NYLUND, lc.ANDREWS_CURRIM]))
        groups.append((de_en("Segmente: Startwerte aus {starts} zufälligen Zuordnungen je Segmentzahl, kurze Läufe, die "
                             "besten zu Ende gerechnet; Stabilität als Wiederfinden der besten Lösung",
                             "Segments: starting values from {starts} random assignments per number of segments, short runs, "
                             "the best computed to the end; stability as finding the best solution again", starts=lc.STARTS),
                       [lc.MCLACHLAN, lc.BIERNACKI, lc.HIPP, lc.MASYN]))
        if lc_logit:
            groups.append((de_en("Segmente: Standardfehler aus der Ableitung der Mischungs-Likelihood je Person, robust "
                                 "(Sandwich über Personen)",
                                 "Segments: standard errors from the derivative of the mixture likelihood per person, "
                                 "robust (sandwich over people)"), [lc.LOUIS, lc.WHITE]))
        if lc_reg:
            groups.append((de_en("Segmente: Standardfehler der Latent-Class-Regression robust über Personen (Sandwich) bei "
                                 "gegebener Zugehörigkeit, daher etwas zu klein (Festlegung von Askurious)",
                                 "Segments: standard errors of the latent class regression robust over people (sandwich) "
                                 "for the given membership, therefore somewhat too small (set by Askurious)"), [lc.WHITE]))
        groups.append((de_en("Segmente: Skalenunterschiede (gleiche Vorlieben, andere Entschiedenheit) und Vergleich mit HB",
                             "Segments: scale differences (same preferences, different decisiveness) and comparison with HB"),
                       [lc.SWAIT, lc.ANDREWS_HB]))
        if any((((e.get(k) or {}).get("lc") or {}).get("result") or {}).get("covariates")
               for e in entries for k in ("maxdiff", "cbc", "acbc")):
            groups.append((de_en("Segmente: Kovariaten der Segmentanteile (multinomiales Logit, Wald-Test)",
                                 "Segments: covariates of the segment shares (multinomial logit, Wald test)"), [lc.DAYTON]))
        lc_views = [(e.get(k) or {}).get("lc") or {} for e in entries for k in ("maxdiff", "cbc", "acbc", "cva")]
        if any(v.get("profile") for v in lc_views):
            from app import segments

            groups.append((de_en("Segmente beschreiben: mit Gewichtung der Seite oder ohne BCH-Korrektur anteilige Zuordnung "
                                 "nach der Zugehörigkeitswahrscheinlichkeit, die wie die Zuordnung zum wahrscheinlichsten "
                                 "Segment Unterschiede unterschätzt; gewichtete Anteile",
                                 "Describing segments: with the weighting of the page or without the BCH correction, "
                                 "proportional assignment by the membership probability, which like the assignment to the "
                                 "most likely segment underestimates differences; weighted shares"),
                           [segments.BOLCK, segments.VERMUNT, segments.PATTERSON]))
            if any(v.get("bch") for v in lc_views):
                groups.append((de_en("Segmente: Anteile und Mittelwerte in anderen Fragen mit BCH-Gewichten (Korrektur der "
                                     "Fehlzuordnung, Anteile auf 0 bis 100 % begrenzt) und Wald-Test mit robuster Varianz über "
                                     "Personen um das gemeinsame Mittel, berichtet als F(df, N − K) (Festlegung von Askurious "
                                     "nach Simulation); seltene Antworten zusammengefasst, bis jede Zelle Segment × Antwort "
                                     "mindestens 5 Personen erwartet",
                                     "Segments: shares and means in other questions with BCH weights (correction of the "
                                     "misassignment, shares bounded to 0 to 100%) and a Wald test with robust variance over "
                                     "people around the common mean, reported as F(df, N − K) (set by Askurious after "
                                     "simulation); rare answers put together until every cell segment × answer expects at "
                                     "least 5 people"),
                               [segments.BOLCK, segments.VERMUNT, segments.BAKK]))
    if any((e.get(k) or {}).get("holdout") for e in entries for k in ("maxdiff", "cbc")):
        from app import holdouts

        groups.append((de_en("Holdout-Aufgaben: Trefferquote und mittlere absolute Abweichung der vorhergesagten Anteile; "
                             "95-%-Intervall der Trefferquote nach Wilson",
                             "Holdout tasks: hit rate and mean absolute error of the predicted shares; Wilson 95% "
                             "interval of the hit rate"), [holdouts.ORME, intervals.WILSON]))
    if any((e.get("cva") or {}).get("holdout") for e in entries):
        from app import holdouts

        groups.append((de_en("Holdout-Aufgaben der Bewertungs-Conjoint: vorhergesagte gegen gegebene Antworten (mittlere "
                             "absolute Abweichung, Korrelation über Personen, Trefferquote mit 95-%-Intervall nach Wilson; "
                             "das Maß für Bewertungen ist eine Festlegung von Askurious)",
                             "Holdout tasks of the ratings-based conjoint: predicted against given answers (mean absolute "
                             "deviation, correlation over people, hit rate with Wilson 95% interval; the measure for ratings "
                             "is set by Askurious)"), [holdouts.ORME, intervals.WILSON]))
    if data.get("speeders") and data.get("speed_rule") == "rsi":
        groups.append((de_en("Speeder: Relative Speed Index (Tempo je Seite gegenüber dem Median der Seite) als Hinweis auf "
                             "sinnlose Daten; die Grenze ist eine Einstellung der Qualitätssicherung",
                             "Speeders: Relative Speed Index (speed per page against the median of the page) as a sign of "
                             "meaningless data; the bound is a setting of quality assurance"), [quality.LEINER]))
    elif data.get("speeders"):
        groups.append((de_en("Speeder: Bearbeitungszeit als Hinweis auf sinnlose Daten; die Grenze (Anteil des Medians) ist "
                             "eine Einstellung der Qualitätssicherung",
                             "Speeders: completion time as a sign of meaningless data; the bound (share of the median) is "
                             "a setting of quality assurance"), [quality.LEINER]))
    if data.get("weighting"):
        from app import weights

        groups.append((de_en("Gewichtung: Raking (iterative proportionale Anpassung) und effektive Fallzahl nach Kish; "
                             "Konfidenzintervalle gewichteter Werte über den Linearisierungs-Standardfehler",
                             "Weighting: raking (iterative proportional fitting) and Kish's effective sample size; "
                             "confidence intervals of weighted values via the linearisation standard error"),
                       [weights.DEMING_STEPHAN, weights.KISH, weights.LUMLEY]))
        groups.append((de_en("Gewichtung und Repräsentativität nicht zufälliger Stichproben",
                             "Weighting and representativeness of non-probability samples"), [weights.AAPOR]))
    return groups


def overview(person_scores, lang, persons=None):
    """The scales of the survey in one table, as for a manuscript: k, n, M, SD, alpha, omega and the Pearson
    correlations of the scale scores (pairwise). Scales in a loop have one score per pass: not in it."""
    rows = []
    for item, name, stats, _ in person_scores:
        info = constructs.info(item)
        title = (info or {}).get("label") or plain(item.text("question", lang)) or item.code
        rows.append({"label": title, "sub": name, "code": item.code, **{k: stats.get(k) for k in (
            "k", "n", "mean", "sd", "alpha", "omega", "omega_small", "sb", "alpha_ci", "omega_ci", "omega_failed", "sb_ci",
            "item_stats", "omega_wait")},
            "partial": bool(stats.get("partial")),
            # the n of M and SD where not everybody saw every item: more than the complete cases of n (C10)
            "n_mean": (stats.get("partial") or {}).get("n")})
    score_values = [scores for _, _, _, scores in person_scores]
    within = None
    if persons is not None:
        from app import person_statistics

        collapsed = [person_statistics.collapse(scores, persons) for scores in score_values]
        within = [[person_statistics.within_correlation(score_values[i], score_values[j], persons)
                   for j in range(i)] for i in range(len(rows))]
        order = list(dict.fromkeys(persons))
        score_values = [[scores.get(person) for person in order] for scores in collapsed]
    corr = [[scales.pearson(score_values[i], score_values[j]) for j in range(i)] for i in range(len(rows))]
    # 95 % intervals of the correlations (Fisher z), beside corr so that its (r, n, p) stays as it is
    corr_ci = [[intervals.fisher_r(r, n) for r, n, _ in line] for line in corr]
    return {"rows": rows, "corr": corr, "corr_ci": corr_ci, "between_persons": persons is not None,
            "within_corr": within}


PSM_SERIES = (("too_cheap", "zu billig"), ("not_cheap", "nicht günstig"), ("not_expensive", "nicht teuer"),
              ("too_expensive", "zu teuer"))


def _round_half_up(v, places):
    """12.5 -> 13 (Python's round() gives 12: half to even)."""
    return float(units.round_half_up(v, places))


class _Price:
    """Formats a price of a PSM item for the page. A class, not a function made on the spot: the results are
    kept between two visits (app/results_store.py), and that needs things that can be written down."""

    def __init__(self, item, places):
        self.item, self.places = item, places

    def __call__(self, v):
        """Every price with the decimals of the question (84,50 € next to 92,67 €, not 84,5 €), in the language of the
        page at the time it is shown, not of the visit that computed the kept results (€1,299.50 in English;
        Mathe-Audit 2026-10-06, Z-2)."""
        from app import units

        if v is None:
            return "–"
        lang = units.page_lang()
        value, currency = _round_half_up(v, self.places), self.item.config.get("currency")
        if currency in units.CURRENCIES:
            return units.fmt(value, "currency", currency, "", lang, self.places)
        return units.fmt(value, "number", "EUR", self.item.text("unit", "de") or "", lang, self.places)


def psm_curves(table, item, passes=()):
    """Van Westendorp analysis plus chart geometry for the results page."""
    a = psm_analysis.analyse(table, item, passes)
    if not a["curves"]:
        return a
    cv, rng = a["curves"], a["range"]
    a["unit"] = item.kind.unit_label(item, "de")
    places = item.config.get("decimals", 2)
    # intersections are interpolated: shown with the item's decimals, like the answers
    a["fmt"] = _Price(item, places)
    a["series"] = PSM_SERIES
    # the table of the curves: at most psm_analysis.MAX_POINTS prices, evenly spread (P9 of the audit of 2026-10-06)
    a["table_rows"] = psm_analysis.thin(range(len(cv["prices"])))
    # 95 % bootstrap intervals of the price points over persons (results page only, not the dashboards)
    a["ci"] = psm_analysis.point_intervals([c for c in psm_analysis.cases(table, item, passes) if psm_analysis.consistent(c)])
    a["ci_min"] = intervals.MIN_BOOT
    a["chart"] = psm_analysis.chart(cv["prices"], [(k, cv[k]) for k, _ in PSM_SERIES], rng, markers=a["points"])
    if a["nms"]:
        n = a["nms"]
        nobody = n.get("nobody")  # nobody would buy: no marks for an optimum
        # drawn through the curve with its jumps at tied prices (R-1): two points at the same price
        line = n.get("curve") or {"prices": cv["prices"], "trial": n["trial"], "revenue": n["revenue"]}
        a["trial_chart"] = psm_analysis.chart(line["prices"], [("trial", line["trial"])], rng,
                                              markers=None if nobody else {"max. Kauf": n["max_trial"]["price"]})
        top = (max(max(line["revenue"]), n["max_revenue"]["value"]) if not nobody else 0) or 1
        a["revenue_chart"] = psm_analysis.chart(line["prices"], [("revenue", line["revenue"])], rng, y_max=top * 1.1,
                                                markers=None if nobody else {"max. Umsatz": n["max_revenue"]["price"]})
    return a
