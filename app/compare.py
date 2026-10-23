"""Group comparisons on the results page ("Nach Gruppen aufteilen"; concept statistik-erweiterungen-konzept.md,
section 4, decisions K7 to K9 as recommended).

The groups come from a group variable (app/groupvars.py): the conditions of a randomizer, the answers of a single
choice question with two to eight valid answers outside loops (boards.groupers), the cells of a quota or the values
of a stored link parameter. Every question of the page is then shown per group,
with a test and an effect size by its measurement level:
  nominal  (single choice, frequencies)   cross table with column percentages, chi-square test, Cramér's V
  multiple choice                         per option the share ticked per group, chi-square test, Cramér's V
  categorised open answers (app/coding)   as single choice (one category per answer) or multiple choice (several),
                                          of the persons whose answer in that column is categorised
  ordinal                                 distribution and median per group; Mann-Whitney U / Kruskal-Wallis H
                                          with the rank-biserial correlation / epsilon squared
  metric   (numbers, metric scales)       n, M, SD, 95 % interval per group; Welch's t / Welch's ANOVA with
                                          delta* in the average SD (95 % interval after Bonett, 2008) / omega
                                          squared
  scale scores                            as metric, also in the table of all scales
A group with fewer than MIN_GROUP answers is not shown on its own and takes no part in the test (as the dashboards
keep small cells apart). The p values are exploratory: no correction for multiple tests (K9), no post hoc tests.
Dashboards stay descriptive (K8). What quality assurance marks is left out, as everywhere on the results page.

The figures are computed from the same table as the results (export.rows of the data pool) and kept next to
them (results_store, a key of their own). In the weighted view the shares of the cross tables and M, SD and
interval of numbers and scales, the distributions and medians of ordered answers and the shares of multiple choice
are weighted (the weights of the results page, without the marked participations, as there); the counts stay the
numbers of answers, and the tests, which need unweighted data, are left out (audit 2026-10-05, I-1). Where Cochran's rule warns, the exact p takes the lead: Fisher's exact test for 2 x 2, a
Monte Carlo permutation test for larger tables (I-3)."""
import math

from app import export, scales
from app import group_tests as gt
from app import results as res
from app.intervals import wilson
from app.items.base import plain
from app.lang import de_en

MIN_GROUP = 5  # as boards.MIN_CELL: a group with fewer answers is not shown on its own

PEARSON = ("Pearson, K. (1900). On the criterion that a given system of deviations from the probable in the case of a "
           "correlated system of variables is such that it can be reasonably supposed to have arisen from random "
           "sampling. The London, Edinburgh, and Dublin Philosophical Magazine and Journal of Science, 50(302), "
           "157–175. https://doi.org/10.1080/14786440009463897")
CRAMER = "Cramér, H. (1946). Mathematical methods of statistics. Princeton University Press."
BERGSMA = ("Bergsma, W. (2013). A bias-correction for Cramér's V and Tschuprow's T. Journal of the Korean Statistical "
           "Society, 42(3), 323–328. https://doi.org/10.1016/j.jkss.2012.10.002")
COCHRAN = ("Cochran, W. G. (1954). Some methods for strengthening the common χ² tests. Biometrics, 10(4), 417–451. "
           "https://doi.org/10.2307/3001616")
MANN_WHITNEY = ("Mann, H. B., & Whitney, D. R. (1947). On a test of whether one of two random variables is "
                "stochastically larger than the other. The Annals of Mathematical Statistics, 18(1), 50–60. "
                "https://doi.org/10.1214/aoms/1177730491")
KERBY = ("Kerby, D. S. (2014). The simple difference formula: An approach to teaching nonparametric correlation. "
         "Comprehensive Psychology, 3, 11.IT.3.1. https://doi.org/10.2466/11.IT.3.1")
KRUSKAL_WALLIS = ("Kruskal, W. H., & Wallis, W. A. (1952). Use of ranks in one-criterion variance analysis. Journal of "
                  "the American Statistical Association, 47(260), 583–621. https://doi.org/10.1080/01621459.1952.10483441")
TOMCZAK = ("Tomczak, M., & Tomczak, E. (2014). The need to report effect size estimates revisited. An overview of some "
           "recommended measures of effect size. Trends in Sport Sciences, 21(1), 19–25.")
WELCH = ("Welch, B. L. (1947). The generalization of 'Student's' problem when several different population variances "
         "are involved. Biometrika, 34(1–2), 28–35. https://doi.org/10.1093/biomet/34.1-2.28")
WELCH_ANOVA = ("Welch, B. L. (1951). On the comparison of several mean values: An alternative approach. Biometrika, "
               "38(3–4), 330–336. https://doi.org/10.1093/biomet/38.3-4.330")
BONETT = ("Bonett, D. G. (2008). Confidence intervals for standardized linear contrasts of means. Psychological "
          "Methods, 13(2), 99–109. https://doi.org/10.1037/1082-989X.13.2.99")
HEDGES = ("Hedges, L. V. (1981). Distribution theory for Glass's estimator of effect size and related estimators. "
          "Journal of Educational Statistics, 6(2), 107–128. https://doi.org/10.3102/10769986006002107")
BROWN_FORSYTHE = ("Brown, M. B., & Forsythe, A. B. (1974). The small sample behavior of some statistics which test "
                  "the equality of several means. Technometrics, 16(1), 129–132. "
                  "https://doi.org/10.1080/00401706.1974.10489158")
FISHER = "Fisher, R. A. (1934). Statistical methods for research workers (5th ed.). Oliver & Boyd."
AGRESTI = "Agresti, A. (2013). Categorical data analysis (3rd ed.). Wiley."
HOPE = ("Hope, A. C. A. (1968). A simplified Monte Carlo significance test procedure. Journal of the Royal Statistical "
        "Society: Series B (Methodological), 30(3), 582–598. https://doi.org/10.1111/j.2517-6161.1968.tb00759.x")
OLEJNIK = ("Olejnik, S., & Algina, J. (2003). Generalized eta and omega squared statistics: Measures of effect size "
           "for some common research designs. Psychological Methods, 8(4), 434–447. https://doi.org/10.1037/1082-989X.8.4.434")


def choices(survey):
    """[(section, [(key, label)])] of the ways the results can be split by (app/groupvars.py)."""
    from app import groupvars

    return groupvars.choices(survey)


def group_vars(survey):
    """{key: group variable} (app/groupvars.py), for the groups of "Nur zeigen"."""
    from app import groupvars

    return groupvars.all_of(survey)


def chosen(survey, value):
    """The group variable named in the address (?gruppen=...), None if it is none of choices()."""
    from app import groupvars

    return groupvars.get(survey, value)


def questions(survey):
    """[(item id, label)] of the single choice questions the results can be split by (boards.groupers): the
    profiles and covariates of the segments (app/segments.py) take only questions."""
    from app import boards

    return [(item.id, f"{item.code}: {plain(item.text('question', survey.default_language))[:70]}")
            for item in boards.groupers(survey).values()]


def question(survey, value):
    """The grouping question of that id, None if it is none of questions()."""
    from app import boards

    return boards.groupers(survey).get(f"item:{value}") if value and str(value).isdigit() else None


def groups_of(item):
    """[(code, label)] of the valid answers of the grouping question, in the order of the question."""
    gone = {str(m) for m in item.kind.missing_codes(item, item.code)}
    lang = item.survey.default_language
    return [(str(o["code"]), plain(o["label"])) for o in item.kind.options(item, lang) if str(o["code"]) not in gone]


def table_of(survey, f, progress=None):
    """export.rows of the data pool as the results page reads it, without what quality assurance marks."""
    table = export.rows(survey, f["all"], f["sources"], f["speeder"], f.get("speed_real", True),
                        speed_sources=f["sources"], with_hb=False, with_quality=False, progress=progress)
    return table.keep(lambda r: r.get("qc_marked") != 1) if f.get("drop") else table


# --- per column -----------------------------------------------------------------------------------------------------

def _split(values, member, codes, weights=None):
    """{group code: [values of its members]} (members without a valid group answer left out); with weights (one
    per row of the table) also {group code: [their weights]}."""
    out = {c: [] for c in codes}
    wts = {c: [] for c in codes}
    for i, (v, g) in enumerate(zip(values, member)):
        if g in out and v is not None:
            out[g].append(v)
            if weights is not None:
                wts[g].append(weights[i])
    return (out, wts) if weights is not None else out


def _kept(by_group, groups):
    """The groups with enough answers to be shown on their own: [(code, label)]."""
    return [(c, label) for c, label in groups if len(by_group[c]) >= MIN_GROUP]


def nominal(values, member, groups, rows, weights=None):
    """Cross table of answers (rows: the frequency rows of the column, with label and "rest") by group. With weights
    the column percentages are weighted, the counts (cells, n) stay the numbers of answers, as on the page."""
    split = _split(values, member, [c for c, _ in groups], weights)
    by_group, wts = split if weights is not None else (split, None)
    kept = _kept(by_group, groups)
    counts = {c: gt.count(by_group[c]) for c, _ in kept}
    weighed = counts if wts is None else {c: gt.count(by_group[c], wts[c]) for c, _ in kept}
    wn = [sum(weighed[c].values()) for c, _ in kept]
    n = [sum(counts[c].values()) for c, _ in kept]
    lines = []
    for r in rows:
        cells = [counts[c].get(r["value"], 0) for c, _ in kept]
        if sum(cells):
            share = [weighed[c].get(r["value"], 0) / total if total else 0 for (c, _), total in zip(kept, wn)]
            line = {"value": r["value"], "label": r.get("label") or r["value"], "rest": bool(r.get("rest")),
                    "count": sum(cells), "cells": cells, "shares": share}
            if wts is not None:
                line["w"] = sum(weighed[c].get(r["value"], 0) for c, _ in kept)
            else:
                # the 95 % interval of each share (Wilson, from 10 answers in the group), drawn as an error bar
                line["ci"] = [wilson(k, m) for k, m in zip(cells, n)]
            lines.append(line)
    lines = res.by_count(lines)
    return {"kind": "nominal", "groups": kept, "n": n, "rows": lines, "weighted": wts is not None,
            "test": gt.chi2_test([line["cells"] for line in lines], weights) if len(kept) >= 2 else None}


def ordinal(values, member, groups, rows, weights=None):
    """Distribution and median per group of ordered answers (rows in scale order), tested on the ranks; with
    weights the distribution and median weighted and no test."""
    order = {r["value"]: i for i, r in enumerate(rows)}
    split = _split(values, member, [c for c, _ in groups], weights)
    by_group, wts = split if weights is not None else (split, None)
    kept = _kept(by_group, groups)
    labels = {r["value"]: r.get("label") or "" for r in rows}
    per = []
    for c, _ in kept:
        stats = res.ordinal_stats(by_group[c], labels, numeric_order=False, weights=None if wts is None else wts[c])
        per.append({"n": stats["n"], "median": stats["median"]["value"] if stats.get("median") else None,
                    "median_hi": stats["median_hi"]["value"] if stats.get("median_hi") else None,
                    "shares": {r["value"]: r["share"] for r in stats["rows"]}})
    ranks = [[order[v] for v in by_group[c] if v in order] for c, _ in kept]
    test = None
    if len(kept) == 2:
        test = gt.mann_whitney(*ranks, weights=weights)
    elif len(kept) > 2:
        test = gt.kruskal_wallis(ranks, weights)
    return {"kind": "ordinal", "groups": kept, "per": per, "weighted": weights is not None,
            "levels": [{"value": r["value"], "label": r.get("label") or ""} for r in rows], "test": test}


def metric(values, member, groups, weights=None):
    """n, M, SD and interval per group, Welch's t (two groups) or Welch's ANOVA (more)."""
    split = _split(values, member, [c for c, _ in groups], weights)
    by_group, wts = split if weights is not None else (split, None)
    kept = _kept(by_group, groups)
    per = [gt.describe(by_group[c], None if wts is None else wts[c]) for c, _ in kept]
    test = None
    if len(kept) == 2:
        test = gt.welch_t(by_group[kept[0][0]], by_group[kept[1][0]], weights)
    elif len(kept) > 2:
        test = gt.welch_anova([by_group[c] for c, _ in kept], weights)
    # the groups without variance (all the same answer), named next to the test or to why there is none: Welch's
    # ANOVA gives way to Brown-Forsythe then, and without any variance there is no test (audit 2026-10-05)
    flat = [label for (c, label), p in zip(kept, per) if p["n"] > 1 and not p["sd"]] if weights is None else []
    return {"kind": "metric", "groups": kept, "per": per, "test": test, "flat": flat, "weighted": weights is not None}


def _ticks(table, member, groups, options, weights=None):
    """Answers several of which can be given (options [(label, rest, column, value)], 0/1 per column; anything else: not
    asked or not answered): the groups with at least MIN_GROUP persons who answered, their n and per option the
    share ticked per group with its interval and a chi-square test (ticked or not by group). With weights the shares
    weighted, the counts as they are, no test."""
    cols = [col for _, _, col, _ in options]
    answered = [any(res._num(r.get(c)) in (0, 1) for c in cols) for r in table]
    persons = _split([1 if a else None for a in answered], member, [c for c, _ in groups])
    kept = [(c, label) for c, label in groups if len(persons[c]) >= MIN_GROUP]
    lines = []
    for label, rest, col, value in options:
        split = _split([x if x in (0, 1) else None for x in (res._num(r.get(col)) for r in table)], member,
                       [c for c, _ in kept], weights)
        seen, wts = split if weights is not None else (split, None)
        ticked = [sum(seen[c]) for c, _ in kept]
        base = [len(seen[c]) for c, _ in kept]
        if not sum(ticked):
            continue
        if wts is None:
            shares = [t / b if b else 0 for t, b in zip(ticked, base)]
        else:
            sums = [(sum(w for x, w in zip(seen[c], wts[c]) if x == 1), sum(wts[c])) for c, _ in kept]
            shares = [t / b if b else 0 for t, b in sums]
        lines.append({"value": value, "label": label, "rest": rest, "count": sum(ticked), "cells": ticked, "base": base,
                      "shares": shares,
                      # unweighted: the 95 % interval of each share (Wilson), drawn as an error bar as for
                      # single choice (UI/UX audit 2026-10-07, W63)
                      **({"ci": [wilson(t, b) for t, b in zip(ticked, base)]} if wts is None else {}),
                      "test": gt.chi2_test([ticked, [b - t for b, t in zip(base, ticked)]], weights)
                      if len(kept) >= 2 else None})
    return kept, [len(persons[c]) for c, _ in kept], lines


def multi(item, table, member, groups, passes, weights=None):
    """Multiple choice: per option the share of those who saw it and answered the question who ticked it, per group;
    a chi-square test per option (ticked or not by group). With weights the shares weighted, the counts as they are,
    no test."""
    lang = item.survey.default_language
    others = res.other_codes(item)
    out = []
    for n in passes or [None]:
        suffix = f"_{n}" if n else ""
        options = [(plain(o["label"]), str(o["code"]) in others, f"{item.code}_{o['code']}{suffix}", str(o["code"]))
                   for o in item.kind.options(item, lang)]
        kept, sizes, lines = _ticks(table, member, groups, options, weights)
        out.append({"kind": "multi", "pass": n, "groups": kept, "n": sizes, "weighted": weights is not None,
                    "rows": res.by_count(lines)})
    return out


def _coding_order(rows):
    """Categories as on the results page (coding.results): the most frequent first, "Sonstiges" and "nicht
    verwertbar" last and in this order, whatever their counts."""
    return sorted(rows, key=lambda r: (bool(r.get("rest")), 0 if r.get("rest") else -(r["w"] if "w" in r else r["count"]),
                                       int(r["value"]) if r.get("rest") else 0))


def categories(codings, table, member, groups, weights=None):
    """The categories of open answers (app/coding.py; codings: the lists of coding.results for one element) per
    group. The base is the persons with a categorised answer in that column. One category per answer: a cross table
    as for single choice; several: per category the share of the persons who got it, as for multiple choice."""
    out = []
    for cl in codings or []:
        col = cl["code"]
        if cl["single"]:
            values = [None if v in (None, "") else res._key(v) for v in (r.get(f"{col}_kat") for r in table)]
            block = nominal(values, member, groups, cl["rows"], weights)
        else:
            options = [(row["label"], row["rest"], f"{col}_k{row['value']}", row["value"]) for row in cl["rows"]]
            kept, sizes, lines = _ticks(table, member, groups, options, weights)
            block = {"kind": "multi", "pass": None, "groups": kept, "n": sizes, "weighted": weights is not None,
                     "rows": lines}
        block["rows"] = _coding_order(block["rows"])
        block.update(code=col, label=cl.get("label") or "", coding=True, single=cl["single"])
        out.append(block)
    return out


# --- the whole page -------------------------------------------------------------------------------------------------

def _column_values(item, table, col, base, n, gone, kind):
    """The values of one column of the results page for the comparison: answer keys (nominal, ordinal) or numbers
    (metric), the codes that count as missing left out; shares of a personal total in percent, as on the page."""
    raw = [r.get(col) for r in table]
    if kind in ("freq", "ord"):
        return [None if v in (None, "") or res.bad_number(v) or res._key(v) in gone else res._key(v) for v in raw]
    share = getattr(item.kind, "share_of", None)
    total_col = share(item, base) if share else None
    if total_col:
        totals = [res._num(r.get(f"{total_col}_{n}" if n else total_col)) for r in table]
        return [100 * a / t if a is not None and t else None
                for a, t in zip((res.valid_number(v, gone) for v in raw), totals)]
    return [res.valid_number(v, gone) for v in raw]


def _scale_scores(item, codes, table):
    """The scale score of every row (one participation; scales outside loops only), None where there is none."""
    hidden = {c: {str(m) for m in item.kind.missing_codes(item, c)} for c in codes}
    out = []
    for r in table:
        row = scales.values(item, codes, r, "", hidden)
        out.append(scales.score(row, scales.shown(item, codes, r, "", hidden)))
    return out


def compute(survey, by, data, table, weights=None, sources=("real",), diary=False):
    """The group comparisons of every question of `data` (results.compute of the same `table`), split by the
    single choice question `by`. Only numbers, texts and ids: kept with the results (results_store).
    weights: one weight per row of `table` for stage 3 (weighting; the caller keeps only the weighted rows, as
    results.compute); with weights the cross tables and means are weighted and no test is computed (that needs the
    effective n). sources: those of the data pool, for the check of a randomizer's assignment (groupvars), which
    counts everybody who reached it, also under "Nur zeigen". diary: a diary study, its weights those of the persons
    (diary.person_weights); the page then says why there is no test (code review 2026-10-15, C4)."""
    from app import groupvars

    by.prepare(table)
    groups = by.groups
    member = [by.member(r) for r in table]
    sizes = {c: sum(1 for g in member if g == c) for c, _ in groups}
    out = {"by": by.id, "code": by.code, "question": by.title, "experiment": by.experiment, "note": by.note,
           "bins": getattr(by, "bounds", None) if by.id.startswith("klassen:") else None,
           "bins_origin": getattr(by, "origin", None), "item_id": by.item.id if by.item is not None else None,
           "assignment": groupvars.assignment_check(by, survey, sources) if by.experiment else None,
           "groups": [{"code": c, "label": label, "n": sizes[c], "few": sizes[c] < MIN_GROUP} for c, label in groups],
           "without": sum(1 for g in member if g is None), "untouched": getattr(by, "untouched", False), "items": {}, "scales": [], "weighted": weights is not None,
           "diary": diary}
    shown = [(c, label) for c, label in groups if sizes[c] >= MIN_GROUP]
    if len(shown) < 2:
        out["too_few"] = True
        return out
    for entry in data.get("items") or []:
        item = entry["item"]
        if by.item is not None and item.id == by.item.id:
            continue
        blocks = []
        loop_rows = export._loop_rows(item) or []
        passes = [n for n, _ in loop_rows]
        if entry.get("multi"):
            blocks += multi(item, table, member, shown, passes, weights)
        bases = {}
        for c, _ in item.kind.export_columns(item):
            for col, n in ([(f"{c}_{n}", n) for n in passes] or [(c, None)]):
                bases[col] = (c, n)
        for column in entry.get("columns") or []:
            kind = column.get("type")
            if kind not in ("freq", "ord", "num") or column["code"] not in bases:
                continue
            base, n = bases[column["code"]]
            gone = {str(m) for m in item.kind.missing_codes(item, base)}
            values = _column_values(item, table, column["code"], base, n, gone, kind)
            if kind == "freq":
                block = nominal(values, member, shown, column["rows"], weights)
            elif kind == "ord":
                block = ordinal(values, member, shown, column["rows"], weights)
            else:
                block = metric(values, member, shown, weights)
                block["unit"] = column.get("unit")
            block.update(code=column["code"], label=column.get("label") or "", **({"pass": n} if n is not None else {}))
            blocks.append(block)
        if not passes:
            found = scales.groups(item) if entry.get("scale") or entry.get("subscales") else []
            for name, codes_ in found:
                block = metric(_scale_scores(item, codes_, table), member, shown, weights)
                block.update(kind="scale", code=item.code, label=(de_en("Subskala „{name}“", "Subscale “{name}”", name=name) if name
                                                       else de_en("Skalenwert", "Scale score")), sub=name)
                blocks.append(block)
                out["scales"].append(block)
        if entry.get("codings"):  # categories of open answers (app/coding.py), after the columns of the element
            blocks += categories(entry["codings"], table, member, shown, weights)
        if blocks:
            for b in blocks if diary else ():
                b["diary"] = True
            out["items"][item.id] = blocks
    return out


def corrected(test):
    """(Ṽ, w̃) of a χ² test with its table shape: Cramér's V corrected for its bias (Bergsma, 2013) and the corrected
    φ (Cohen's w) it rests on, or (None, None) without the shape. Without a difference in the population V is not
    near 0 but about √(df / n): 17 answers in two groups of together 155 give V ≈ 0.32 from chance alone, and the
    overview put such a row second (UI/UX audit 2026-10-09, X53). φ̃² = max(0, φ² − (r − 1)(c − 1) / (n − 1)),
    r̃ = r − (r − 1)² / (n − 1), c̃ likewise, Ṽ = √(φ̃² / min(r̃ − 1, c̃ − 1))."""
    shape, n, chi2 = test.get("shape"), test.get("n"), test.get("chi2")
    if not shape or not n or n < 2 or chi2 is None:
        return None, None
    r, c = shape
    phi2 = max(0.0, chi2 / n - (r - 1) * (c - 1) / (n - 1))
    rr, cc = r - (r - 1) ** 2 / (n - 1), c - (c - 1) ** 2 / (n - 1)
    room = min(rr - 1, cc - 1)
    return (min(1.0, math.sqrt(phi2 / room)) if room > 0 else 0.0), math.sqrt(phi2)


# the words of the overview, by the size on the common scale (Cohen, 1988: r and w .1 small, .3 medium, .5 large)
SMALL, MEDIUM, LARGE = 0.1, 0.3, 0.5


def verdict(size, p):
    """The word of a row of the overview: "none" (p ≥ .05 or below a small effect), "small", "medium", "large"."""
    if p is None or p >= 0.05 or size < SMALL:
        return "none"
    return "small" if size < MEDIUM else "medium" if size < LARGE else "large"


def _effect(kind, groups, test):
    """(effect key, value, signed, size, test name) of one test: the effect size the block reports and its size on a
    common scale for sorting (r): Cramér's V by the bias-corrected φ (Cohen's w; Bergsma, 2013) when the table shape
    is known, else as it is, the rank-biserial r as it is, ε² and ω² by their root, the standardised difference δ* as
    r = δ* / √(δ*² + 4) (Cohen, 1988, p. 23)."""
    if kind in ("nominal", "multi"):
        v = test.get("v")
        w = corrected(test)[1]
        return "cramer_v", v, False, abs(v or 0) if w is None else w, "χ²"
    if kind == "ordinal" and groups == 2:
        r = test.get("r")
        return "r", r, True, abs(r or 0), "Mann-Whitney U"
    if kind == "ordinal":
        e2 = test.get("e2")
        return "epsilon2", e2, False, math.sqrt(max(e2 or 0, 0)), "Kruskal-Wallis H"
    if groups == 2:
        d = test.get("d")
        return "g", d, True, abs(d or 0) / math.sqrt((d or 0) ** 2 + 4), "Welch t"
    w2 = test.get("omega2")
    return "omega2", w2, False, math.sqrt(max(w2 or 0, 0)), ("Brown-Forsythe F*" if test.get("method") == "brown_forsythe"
                                                             else "Welch F")


def _p(test):
    """The p that counts: the exact one where the χ² test has many small expected frequencies and one was computed."""
    if test.get("cochran") and isinstance(test.get("exact_p"), (int, float)):
        return test["exact_p"]
    return test.get("p")


def _holm_of(ps, i):
    """Holm's (1979) adjusted p of the i-th of the p values ps (None counts as 1): the j-th smallest times (k − j + 1),
    never smaller than the one before, at most 1."""
    ps = [1.0 if v is None else v for v in ps]
    k, last = len(ps), 0.0
    for j, n in enumerate(sorted(range(k), key=lambda n: ps[n])):
        last = min(1.0, max(last, (k - j) * ps[n]))
        if n == i:
            return last
    return None


def overview(cmp, items):
    """"Unterschiede auf einen Blick" (UI/UX audit 2026-10-07, S6): one row per tested comparison of the page, the
    largest effect first (on the common scale of _effect; equal ones in the order of the page). items: the entries of
    the results (results.compute), in the order of the page. A multiple choice gives one row per pass, for the option
    with the largest effect (after the correction; the row says it was picked). Each row has its word ("verdict") and
    for V the corrected Ṽ ("corrected"). Returns {"rows": [...], "untested": number of comparisons without a test}."""
    if not cmp or cmp.get("too_few"):
        return {"rows": [], "untested": 0}
    rows, untested, order = [], 0, 0
    for entry in items or []:
        item = entry["item"]
        blocks = (cmp.get("items") or {}).get(item.id) or []
        for b in blocks:
            test, option, adjusted = b.get("test"), None, None
            if b["kind"] == "multi":
                tested = [r for r in b.get("rows") or [] if r.get("test")]
                if tested:
                    best = max(tested, key=lambda r: _effect("multi", 2, r["test"])[3])
                    test, option = best["test"], {"label": best["label"], "of": len(b["rows"])}
                    # the option was picked from k tested ones: its p with Holm's correction over them (code review
                    # 2026-10-15, C11), so a chance finding among many options is not read as a difference
                    adjusted = _holm_of([_p(r["test"]) for r in tested], tested.index(best))
            if not test:
                untested += 1
                continue
            key, value, signed, size, name = _effect(b["kind"], len(b["groups"]), test)
            p = _p(test) if adjusted is None else adjusted
            rows.append({"verdict": verdict(size, p), "corrected": corrected(test)[0] if key == "cramer_v" else None,"item_id": item.id, "code": item.code, "question": entry.get("question") or item.code,
                         "block": b.get("code"), "label": b.get("label") or "", "pass": b.get("pass"),
                         "kind": b["kind"], "several": len(blocks) > 1, "option": option, "test": name,
                         "coding": bool(b.get("coding")),
                         "effect": key, "value": value, "signed": signed, "size": size, "p": p,
                         "order": order})
            order += 1
    rows.sort(key=lambda r: (-r["size"], r["order"]))
    return {"rows": rows, "untested": untested}


def sources(cmp):
    """[(heading, [citations])] of the procedures the comparison shows, for the one fold of the page."""
    from app import groupvars

    if not cmp:
        return []
    from app.lang import de_en

    check = []
    if cmp.get("assignment"):
        exact = ((cmp["assignment"].get("completion") or {}).get("exact"))
        check.append((de_en("Prüfung der Zuteilung eines Randomisierers (Sample Ratio Mismatch, Abschluss je "
                            "Versuchsbedingung)",
                            "Check of the assignment of a randomiser (sample ratio mismatch, completion per condition)"),
                      [groupvars.FABIJAN, PEARSON] + ([FISHER] if exact == "fisher" else [HOPE] if exact else [])))
    if cmp.get("bins") is not None:
        check.append((de_en("Zahlen in Klassen", "Numbers in classes"), [groupvars.MACCALLUM]))
    if cmp.get("too_few"):
        return check
    blocks = [b for blocks in cmp["items"].values() for b in blocks]
    tests = [b for b in blocks if b.get("test")] + [r for b in blocks if b["kind"] == "multi" for r in b["rows"] if r.get("test")]
    groups = []
    if any(b["kind"] in ("nominal", "multi") for b in blocks):
        groups.append((de_en("Gruppenvergleich nominaler Antworten: χ²-Unabhängigkeitstest, Cramérs V und die Regel für "
                             "kleine erwartete Häufigkeiten",
                             "Group comparison of nominal answers: χ² test of independence, Cramér's V and the rule for "
                             "small expected frequencies"), [PEARSON, CRAMER, COCHRAN]))
    exact = {t.get("exact") for t in ([b["test"] for b in blocks if b.get("test")] + [r["test"] for b in blocks
                                      if b["kind"] == "multi" for r in b["rows"] if r.get("test")])
             if t.get("cochran") and t.get("exact_p") is not None}
    if "fisher" in exact:
        groups.append((de_en("Exakter Test nach Fisher (2 × 2-Tafeln mit kleinen erwarteten Häufigkeiten)",
                             "Fisher's exact test (2 × 2 tables with small expected frequencies)"), [FISHER, AGRESTI]))
    if "monte_carlo" in exact:
        groups.append((de_en("Monte-Carlo-Permutationstest (größere Tafeln mit kleinen erwarteten Häufigkeiten)",
                             "Monte Carlo permutation test (larger tables with small expected frequencies)"), [HOPE, AGRESTI]))
    two = [b for b in blocks if b.get("test") and len(b["groups"]) == 2]
    more = [b for b in blocks if b.get("test") and len(b["groups"]) > 2]
    if any(b["kind"] == "ordinal" for b in two):
        groups.append((de_en("Gruppenvergleich geordneter Antworten (zwei Gruppen): Mann-Whitney-U und rangbiseriale "
                             "Korrelation",
                             "Group comparison of ordered answers (two groups): Mann-Whitney U and rank-biserial "
                             "correlation"), [MANN_WHITNEY, KERBY]))
    if any(b["kind"] == "ordinal" for b in more):
        groups.append((de_en("Gruppenvergleich geordneter Antworten (mehr als zwei Gruppen): Kruskal-Wallis-H und ε²",
                             "Group comparison of ordered answers (more than two groups): Kruskal-Wallis H and ε²"),
                       [KRUSKAL_WALLIS, TOMCZAK]))
    if any(b["kind"] in ("metric", "scale") for b in two):
        groups.append((de_en("Mittelwertvergleich (zwei Gruppen): Welch-t-Test und standardisierter Unterschied δ* "
                             "mit 95-%-Intervall, der Wert für kleine Gruppen korrigiert (Faktor J wie bei Hedges’ g)",
                             "Comparison of means (two groups): Welch's t-test and standardised difference δ* with "
                             "95% interval, the value corrected for small groups (factor J as for Hedges’ g)"),
                       [WELCH, BONETT, HEDGES]))
    if any(b["kind"] in ("metric", "scale") for b in more):
        groups.append((de_en("Mittelwertvergleich (mehr als zwei Gruppen): Welch-ANOVA und ω²",
                             "Comparison of means (more than two groups): Welch's ANOVA and ω²"), [WELCH_ANOVA, OLEJNIK]))
    if tests:
        from app.samplesize import COHEN

        groups.append((de_en("Unterschiede auf einen Blick: Effekte verschiedener Tests auf einer Skala (r) geordnet und "
                             "gedeutet, Cramérs V um seine Verzerrung bereinigt",
                             "Differences at a glance: effects of different tests ordered and read on one scale (r), "
                             "Cramér's V corrected for its bias"), [COHEN, BERGSMA]))
    if any((b.get("test") or {}).get("method") == "brown_forsythe" for b in more):
        groups.append((de_en("Mittelwertvergleich, wenn eine Gruppe nicht streut: Brown-Forsythe-F*",
                             "Comparison of means when one group does not vary: Brown-Forsythe F*"), [BROWN_FORSYTHE]))
    return check + (groups if tests else [])
