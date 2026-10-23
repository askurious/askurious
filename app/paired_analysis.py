"""Analysis of paired comparisons (app/items/paired.py; concept paarvergleich-konzept.md, E7, E8 and E10).

Answers. Every answered pair is a category c from the point of view of the option shown first: "Wahl" gives c = +1
(first chosen) or -1 (second chosen), "Wahl mit ‚gleich‘" also 0, "Stärke" -m .. +m (5 steps: m = 2, 7 steps: m = 3)
with 0 = "gleich".

Counts. Per option the comparisons, wins, losses and ties; the win share (wins + ties / 2) / comparisons and the score
(wins - losses) / comparisons from -1 to +1 (as the best-minus-worst score of MaxDiff); per pair the share of
comparisons the row option won (a tie counts half).

Model. One family for all three answer forms: the adjacent-categories model of Agresti (1992), which is the
Bradley-Terry model (Bradley & Terry, 1952) with two categories and the model of Davidson (1970) with three. In it
P(c) is proportional to exp(c / 2 · (u_first - u_second + delta) + theta_|c|), with theta_m = 0; delta is the
advantage of the position shown first (Davidson & Beaver, 1977), theta_0 the tendency to "gleich" (log nu of
Davidson) and theta_1 .. theta_(m-1) the use of the weaker steps. Written so, every answered pair is one choice among
its categories, and the conditional logit of app/choice_model.fit estimates it, with standard errors clustered by
person, t(G - 1) under T_UNTIL persons and the weak ridge of MaxDiff when an option always wins or always loses.
Thurstone's case V (normal instead of logistic) gives practically the same order and distances up to the scale
(Cattelan, 2012) and is not offered besides.

Shown are values w_i = 100 · exp(u_i) / sum exp(u) (sum 100): with "Wahl" and "Wahl mit ‚gleich‘" option i is
preferred to option j (leaving ties aside) with probability w_i / (w_i + w_j). 95 % intervals by simulation from the
asymptotic normal distribution of the estimates, as for MaxDiff.

Consistency (complete designs, E8). Per person the circular triads (A over B, B over C, C over A) among the triples
whose three pairs the person decided, and Kendall's coefficient of consistence zeta = 1 - T / T_max with T_max
= (K^3 - K) / 24 for odd K and (K^3 - 4K) / 24 for even K (Kendall & Babington Smith, 1940), for persons who decided
every pair. A person is marked when the triads are not fewer than random choices give: every triple is circular with
probability 1/4 under random choice (two of its eight orientations are circular), so the expectation is a quarter of
the decided triples. Over the persons who decided every pair, Kendall's coefficient of agreement
u = 8 S / (N (N - 1) K (K - 1)) - 1 with S the sum over ordered pairs of C(a_ij, 2), a_ij the number of persons
preferring i to j (same source).

Input is the wide export table (export.rows), as for MaxDiff.
"""
import math
from itertools import combinations

import numpy as np

from app.choice_model import MIN_PERSONS, Z95, ChoiceSet, critical, fit, once
from app.lang import de_en
from app.maxdiff_analysis import RIDGE, axis

DRAWS = 2000
BRADLEY_TERRY = ("Bradley, R. A., & Terry, M. E. (1952). Rank analysis of incomplete block designs: I. The method of "
                 "paired comparisons. Biometrika, 39(3/4), 324–345. https://doi.org/10.2307/2334029")
DAVIDSON = ("Davidson, R. R. (1970). On extending the Bradley-Terry model to accommodate ties in paired comparison "
            "experiments. Journal of the American Statistical Association, 65(329), 317–328. "
            "https://doi.org/10.1080/01621459.1970.10481082")
DAVIDSON_BEAVER = ("Davidson, R. R., & Beaver, R. J. (1977). On extending the Bradley-Terry model to incorporate "
                   "within-pair order effects. Biometrics, 33(4), 693–702. https://doi.org/10.2307/2529467")
AGRESTI = ("Agresti, A. (1992). Analysis of ordinal paired comparison data. Journal of the Royal Statistical Society: "
           "Series C (Applied Statistics), 41(2), 287–297. https://doi.org/10.2307/2347562")
CATTELAN = ("Cattelan, M. (2012). Models for paired comparison data: A review with emphasis on dependent data. "
            "Statistical Science, 27(3), 412–433. https://doi.org/10.1214/12-STS396")
THURSTONE = ("Thurstone, L. L. (1927). A law of comparative judgment. Psychological Review, 34(4), 273–286. "
             "https://doi.org/10.1037/h0070288")
KENDALL = ("Kendall, M. G., & Babington Smith, B. (1940). On the method of paired comparisons. Biometrika, 31(3/4), "
           "324–345. https://doi.org/10.2307/2332613")
SCHEFFE = ("Scheffé, H. (1952). An analysis of variance for paired comparisons. Journal of the American Statistical "
           "Association, 47(259), 381–400. https://doi.org/10.1080/01621459.1952.10501179")
SOURCES = (BRADLEY_TERRY, CATTELAN)
MAX_MATRIX = 15  # the table option x option on the page up to this many options


def categories(answer, steps=5):
    """The categories of an answer form: "choice" -1, +1; "tie" -1, 0, +1; "strength" -m .. +m."""
    if answer == "choice":
        return [-1, 1]
    if answer == "tie":
        return [-1, 0, 1]
    m = 3 if steps == 7 else 2
    return list(range(-m, m + 1))


# --- reading the wide export ------------------------------------------------------------------------------------

def _num(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return None


def persons_from_table(item, table):
    """Per row of the wide export (pid, version index or None, [(first, second, c)]) with 0-based option indices and c
    from the point of view of the option shown first. Parsed once per table; the result is shared, not to change."""
    return once(table, ("paired", item.id), lambda: _persons(item, table))


def _persons(item, table):
    code = item.code
    versions = (item.config.get("design") or {}).get("versions") or []
    strength = item.config.get("answer") == "strength"
    out = []
    for r in table:
        v = _num(r.get(f"{code}_version"))
        v = None if v is None else v - 1
        answered = []
        if v is not None and 0 <= v < len(versions):
            for a, b in versions[v]:
                lo, hi = min(a, b), max(a, b)
                won = _num(r.get(f"{code}_{lo + 1}_{hi + 1}"))
                if won is None or won < 0:
                    continue  # unanswered, or -77: not shown
                size = (_num(r.get(f"{code}_{lo + 1}_{hi + 1}_st")) or 1) if strength else 1
                if won == 0:
                    c = 0
                elif won == a + 1:
                    c = size
                elif won == b + 1:
                    c = -size
                else:
                    continue
                answered.append((a, b, c))
        out.append((r.get("pid"), v, answered))
    return out


# --- counts -----------------------------------------------------------------------------------------------------

def counts(n, persons):
    """Per option comparisons, wins, losses, ties (arrays), per ordered pair [i][j] the row's wins plus half its ties
    and the number of comparisons, and the number of persons with at least one answered pair."""
    comp, win, loss, tie = (np.zeros(n) for _ in range(4))
    pw, pn = np.zeros((n, n)), np.zeros((n, n))
    people = 0
    for _, _, answered in persons:
        people += bool(answered)
        for a, b, c in answered:
            comp[a] += 1
            comp[b] += 1
            pn[a, b] += 1
            pn[b, a] += 1
            if c > 0:
                win[a] += 1
                loss[b] += 1
                pw[a, b] += 1
            elif c < 0:
                win[b] += 1
                loss[a] += 1
                pw[b, a] += 1
            else:
                tie[a] += 1
                tie[b] += 1
                pw[a, b] += 0.5
                pw[b, a] += 0.5
    return comp, win, loss, tie, pw, pn, people


def person_scores(n, answered):
    """One person's (wins - losses) / comparisons per option (None if not compared), for the export."""
    comp, bal = [0] * n, [0] * n
    for a, b, c in answered:
        comp[a] += 1
        comp[b] += 1
        s = (c > 0) - (c < 0)
        bal[a] += s
        bal[b] -= s
    return [round(bal[i] / comp[i], 4) if comp[i] else None for i in range(n)]


# --- consistency ------------------------------------------------------------------------------------------------

def triads(n, answered):
    """(circular triads, decided triples, zeta or None) of one person. zeta only when every pair is decided."""
    beats = {}
    for a, b, c in answered:
        if c:
            beats[(a, b) if c > 0 else (b, a)] = True
    decided = lambda i, j: (i, j) in beats or (j, i) in beats  # noqa: E731
    circular = triples = 0
    for i, j, k in combinations(range(n), 3):
        if not (decided(i, j) and decided(j, k) and decided(i, k)):
            continue
        triples += 1
        if ((i, j) in beats and (j, k) in beats and (k, i) in beats) or \
           ((j, i) in beats and (k, j) in beats and (i, k) in beats):
            circular += 1
    zeta = None
    if n >= 3 and len(beats) == n * (n - 1) // 2:
        t_max = (n ** 3 - n) / 24 if n % 2 else (n ** 3 - 4 * n) / 24
        zeta = round(1 - circular / t_max, 4) if t_max else None
    return circular, triples, zeta


def random_like(circular, triples):
    """Not fewer circular triads than random choices give (a quarter of the decided triples), with at least one."""
    return triples > 0 and circular >= max(1, triples / 4)


def agreement(n, persons):
    """Kendall's u over the persons who decided every pair (None with fewer than two such persons)."""
    full = [answered for _, _, answered in persons
            if len({(min(a, b), max(a, b)) for a, b, c in answered if c}) == n * (n - 1) // 2]
    N = len(full)
    if N < 2 or n < 2:
        return None, N
    a = np.zeros((n, n))
    for answered in full:
        for x, y, c in answered:
            if c > 0:
                a[x, y] += 1
            elif c < 0:
                a[y, x] += 1
    s = float((a * (a - 1) / 2).sum())
    return round(8 * s / (N * (N - 1) * n * (n - 1)) - 1, 4), N


# --- the model --------------------------------------------------------------------------------------------------

def parameters(n, persons, cats, comp=None):
    """(estimated options, {option: column}, k, delta column, {|c|: theta column}): one utility per compared option
    except the reference (the last), then delta, then theta_0 .. theta_(m-1) where the answer form has them. `comp`:
    the comparisons per option from counts() when the caller has them already (audit 2026-10-10, P8)."""
    if comp is None:
        comp = counts(n, persons)[0]
    est = [i for i in range(n) if comp[i] > 0]
    col = {i: c for c, i in enumerate(est[:-1])}
    k = len(col)
    delta = k
    k += 1
    # only the steps someone answered: a step nobody used would need theta = −∞, which the ridge does not bound, and
    # then the model had no standard errors (code review 2026-10-15, C6); the strongest step used is the reference
    used = _used(persons, cats)
    m = max(used, default=max(cats))
    theta = {}
    for level in range(0, m):
        if level in used:
            theta[level] = k
            k += 1
    return est, col, k, delta, theta


def _used(persons, cats):
    """The steps (|c|) of the answer form that occur among the answers."""
    return {abs(c) for _, _, answered in persons for _, _, c in answered if c in cats}


def choice_sets(n, persons, cats, comp=None):
    """One ChoiceSet per answered pair (its categories as alternatives, those of the steps somebody answered), with the
    person for clustering."""
    est, col, k, delta, theta = parameters(n, persons, cats, comp)
    if len(est) < 2:
        return [], est, 0, delta, theta
    used = _used(persons, cats)
    cats = [c for c in cats if abs(c) in used]
    sets = []
    for p, (_, _, answered) in enumerate(persons):
        for a, b, c in answered:
            if c not in cats:
                continue
            rows = []
            for cat in cats:
                x = np.zeros(k)
                s = cat / 2
                if a in col:
                    x[col[a]] += s
                if b in col:
                    x[col[b]] -= s
                x[delta] = s
                if abs(cat) in theta:
                    x[theta[abs(cat)]] = 1.0
                rows.append(x)
            sets.append(ChoiceSet(rows, cats.index(c), p))
    return sets, est, k, delta, theta


def components(n, persons):
    """Groups of options linked through answered pairs; one group = connected."""
    parent = list(range(n))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen = set()
    for _, _, answered in persons:
        for a, b, _ in answered:
            seen.update((a, b))
            parent[root(a)] = root(b)
    groups = {}
    for i in sorted(seen):
        groups.setdefault(root(i), []).append(i)
    return list(groups.values())


def worths(u):
    """100 · exp(u) / sum exp(u) over the last axis."""
    e = np.exp(u - u.max(axis=-1, keepdims=True))
    return 100 * e / e.sum(axis=-1, keepdims=True)


def model(n, persons, labels, cats, counted=None):
    """The adjacent-categories Bradley-Terry model for the results page (dict), or None without data. `counted`: the
    result of counts() when the caller has it already, so one build counts once (audit 2026-10-10, P8)."""
    comp, win, loss, tie, pw, pn, people = counted or counts(n, persons)
    sets, est, k, delta, theta = choice_sets(n, persons, cats, comp)
    if not sets:
        return None
    blocks = components(n, persons)
    if len(blocks) > 1:
        groups = " | ".join(", ".join(str(i + 1) for i in g) for g in blocks)
        return {"disconnected": True, "persons": people, "warnings": [de_en(
            "Die Optionen zerfallen in {n} Gruppen, die nie miteinander verglichen wurden ({groups}). Zwischen den "
            "Gruppen gibt es keinen gemeinsamen Maßstab, deshalb hier keine Modellwerte (Ford, 1957). Der Versuchsplan braucht "
            "mehr Paare je Person oder mehr Versionen.",
            "The options fall apart into {n} groups that were never compared with each other ({groups}). There is no "
            "common scale between the groups, so no model values here (Ford, 1957). The experimental design needs more pairs per "
            "person or more versions.", n=len(blocks), groups=groups)]}
    m = len(est)
    M = np.zeros((m, k))  # zero-centred utilities u = M @ beta (the reference has 0)
    M[np.arange(m - 1), np.arange(m - 1)] = 1.0
    M[:, :m - 1] -= 1.0 / m
    penalty = RIDGE * (M.T @ M)
    separated = [i for i in est if comp[i] and (win[i] == comp[i] or loss[i] == comp[i])]
    ridge = bool(separated)
    info = {}
    beta, cov, ll, converged, iterations = fit(sets, k, ridge=penalty if ridge else 0.0, info=info)
    if not ridge and (not converged or cov is None or np.max(np.abs(beta)) > 15):
        ridge = True
        beta, cov, ll, converged, iterations = fit(sets, k, ridge=penalty, info=info)
    q = critical(info)
    keep = [j for j, i in enumerate(est) if i not in separated]
    if len(keep) < 2:
        keep = list(range(m))
    scaled = len(keep) < m
    if scaled:  # the separated options have no finite utility: centred on, and summed to 100 over, the others
        R = M + 1.0 / m * np.concatenate([np.ones((m, m - 1)), np.zeros((m, k - m + 1))], axis=1)
        M = R - R[keep].mean(axis=0)
    u = M @ beta
    w = worths(u[keep])
    u_se = w_lo = w_hi = None
    if cov is not None:
        u_se = np.sqrt(np.maximum(np.einsum("ik,kl,il->i", M, cov, M), 0))
        draws = np.random.default_rng(0).multivariate_normal(beta, cov * (q / Z95) ** 2, size=DRAWS, method="cholesky")
        d_w = worths((draws @ M.T)[:, keep])
        w_lo, w_hi = np.percentile(d_w, [2.5, 97.5], axis=0)
    d_est = float(beta[delta])
    d_se = math.sqrt(max(float(cov[delta, delta]), 0.0)) if cov is not None else None
    position = None
    if d_se:
        position = {"delta": d_est, "se": d_se, "factor": math.exp(d_est), "clear": abs(d_est) > q * d_se,
                    "first": d_est > 0}
    tie_share = float(tie.sum() / comp.sum()) if comp.sum() else 0.0  # every tie counted at both options
    warnings = []
    if people < MIN_PERSONS:
        warnings.append(de_en("Nur {n} Personen: Die Schätzung ist unsicher (Richtwert ab {m}).",
                              "Only {n} people: the estimation is uncertain (guideline: from {m}).", n=people, m=MIN_PERSONS)
                        if people != 1 else
                        de_en("Nur eine Person: Die Schätzung ist unsicher (Richtwert ab {m}).",
                              "Only one person: the estimation is uncertain (guideline: from {m}).", m=MIN_PERSONS))
    if not converged:
        warnings.append(de_en("Das Modell ist nicht konvergiert; die Werte sind nicht verlässlich.",
                              "The model has not converged; the values are not reliable."))
    if cov is None:
        warnings.append(de_en("Keine Standardfehler: Das Modell ist nicht identifiziert (zu wenige Daten).",
                              "No standard errors: the model is not identified (too little data)."))
    if separated:
        warnings.append(de_en("Option {which}: Hat jeden Vergleich gewonnen oder jeden verloren. Ihr Wert wäre unendlich "
                              "und ist durch eine schwache Regularisierung begrenzt; sie steht deshalb über bzw. unter allen "
                              "anderen, ohne Wert.",
                              "Option {which}: won every comparison or lost every one. Its value would be infinite and is "
                              "bounded by a weak regularisation; it therefore ranks above or below all others, without a "
                              "value.", which=", ".join(str(i + 1) for i in separated)))
    elif ridge:
        warnings.append(de_en("Einige Optionen liegen stets vor oder hinter anderen; die Schätzung ist durch eine schwache "
                              "Regularisierung begrenzt.",
                              "Some options are always ahead of or behind others; the estimation is bounded by a weak "
                              "regularisation."))
    if m < n:
        warnings.append(de_en("{n} Option(en) nie verglichen; sie fehlen im Modell.",
                              "{n} option(s) never compared; they are missing from the model.", n=n - m))
    place = {j: x for x, j in enumerate(keep)}
    rows = []
    for j, i in enumerate(est):
        x = place.get(j)
        out = i in separated and scaled
        rows.append({"n": i + 1, "label": labels[i], "score": None if x is None or out else float(w[x]),
                     "lo": None if w_lo is None or x is None or out else float(w_lo[x]),
                     "hi": None if w_hi is None or x is None or out else float(w_hi[x]),
                     "u": float(u[j]), "u_se": None if u_se is None or out else float(u_se[j]),
                     "separated": i in separated, "above": None,
                     "side": ("top" if win[i] == comp[i] else "bottom") if out else None})
    rows.sort(key=lambda r: (0 if r["side"] == "top" else 2 if r["side"] == "bottom" else 1, -(r["score"] or 0)))
    null_ll = sum(-math.log(len(s.X)) for s in sets)
    result = {"persons": people, "observations": len(sets), "loglik": ll, "null_loglik": null_ll,
              "rho2": 1 - ll / null_ll if null_ll else None, "converged": converged, "iterations": iterations,
              "ridge": ridge, "rows": rows, "warnings": warnings, "position": position, "tie_share": tie_share,
              "robust": cov is not None and info.get("robust", False), "q": q,
              "t_df": info["clusters"] - 1 if q != Z95 else None, "threshold": None}
    result["chart"] = chart(result)
    return result


def chart(model_, width=720, label_w=250, value_w=56, row_h=24, bar_h=14):
    """Bars of the values (0 at the left) with their 95 % intervals, as the MaxDiff scores (maxdiff_analysis.chart)."""
    from app.maxdiff_analysis import chart as md_chart

    return md_chart(model_, width, label_w, value_w, row_h, bar_h)


def analyse(item, table, labels):
    """Everything the results page shows of a paired comparison."""
    n = len(labels)
    persons = persons_from_table(item, table)
    answer = item.config.get("answer", "choice")
    cats = categories(answer, item.config.get("steps", 5))
    counted = counts(n, persons)
    comp, win, loss, tie, pw, pn, people = counted
    rows = []
    for i in range(n):
        rows.append({"n": i + 1, "label": labels[i], "comparisons": int(comp[i]), "wins": int(win[i]),
                     "losses": int(loss[i]), "ties": int(tie[i]),
                     "share": float((win[i] + tie[i] / 2) / comp[i]) if comp[i] else None,
                     "score": float((win[i] - loss[i]) / comp[i]) if comp[i] else None})
    out = {"persons": people, "pairs": int(pn.sum() / 2), "rows": rows, "answer": answer, "n": n}
    if not people:
        return out
    out["model"] = model(n, persons, labels, cats, counted)
    order = [r["n"] - 1 for r in (out["model"] or {}).get("rows") or []] if out["model"] and not out["model"].get(
        "disconnected") else sorted(range(n), key=lambda i: -(rows[i]["share"] or 0))
    order += [i for i in range(n) if i not in order]
    if n <= MAX_MATRIX:
        out["matrix"] = {"order": [i + 1 for i in order], "labels": [labels[i] for i in order],
                         "cells": [[None if i == j or not pn[i, j] else float(pw[i, j] / pn[i, j]) for j in order]
                                   for i in order]}
    if item.config.get("answer") == "strength":
        # Scheffé's average preference: the mean signed strength of an option over its comparisons
        strength = np.zeros(n)
        for _, _, answered in persons:
            for a, b, c in answered:
                strength[a] += c
                strength[b] -= c
        for i, r in enumerate(rows):
            r["strength"] = float(strength[i] / comp[i]) if comp[i] else None
    if item.kind.complete(item):
        tri = [triads(n, answered) for _, _, answered in persons if answered]
        zetas = [z for _, _, z in tri if z is not None]
        marked = sum(1 for c, t, _ in tri if random_like(c, t))
        u, N = agreement(n, persons)
        out["consistency"] = {"persons": len(tri), "with_zeta": len(zetas),
                              "zeta_mean": float(np.mean(zetas)) if zetas else None,
                              "zeta_median": float(np.median(zetas)) if zetas else None,
                              "perfect": sum(1 for z in zetas if z == 1),
                              "marked": marked, "u": u, "u_n": N,
                              "hist": _hist(zetas)}
    return out


def _hist(zetas, bins=10):
    """Counts of zeta in ten classes from 0 to 1 (1 itself in the last)."""
    out = [0] * bins
    for z in zetas:
        out[min(bins - 1, max(0, int(z * bins)))] += 1
    return out


__all__ = ["analyse", "axis", "categories", "counts", "model", "person_scores", "persons_from_table", "triads"]
