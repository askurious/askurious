"""Counting analysis of MaxDiff (best-worst scaling, case 1).

Per statement over all answered sets: how often it was shown, chosen as best and as worst;
the best-minus-worst score (best - worst) / shown lies between -1 (always worst) and +1 (always best).
Best and worst shares are best / shown and worst / shown. With the optional anchoring question the
share of persons who called the statement "really important".

Aggregate multinomial logit (mnl(), fitted with app/choice_model.fit): every answered set gives two
choices, best among the shown statements (statement dummies +1) and worst among the remaining ones
after removing the best (sequential best-worst, dummies -1). One reference statement is fixed at 0; the
utilities are shown zero-centred. With the anchoring question each statement is also a binary choice
between itself and a threshold alternative with its own utility ("really important" = the statement
wins; direct anchoring). Probability scores p_i = exp(u_i) / (exp(u_i) + a - 1), a = statements per set: the logit
probability (Louviere, Flynn & Marley, 2015) that statement i is chosen as best from a set whose other a - 1 statements
have the mean utility 0; rescaled to sum 100 (Festlegung von Askurious, so the scores read as shares); the threshold
goes through the same transformation. Standard errors cluster-robust
per person (app/choice_model.fit; with not more persons than parameters from the Hessian, "robust" says which),
95 % intervals of the scores by simulation from the asymptotic normal distribution. With the sandwich over fewer
than choice_model.T_UNTIL persons the intervals use t(G - 1) instead of 1.96 (audit 2026-10-05, C-2): for the
simulated scores the covariance is stretched by (t / z)^2. The model needs a connected design (every statement linked to every other through shared
sets); otherwise the blocks have no common scale and no scores are shown. Pooled over
all persons; individual utilities by hierarchical Bayes use the same coding (app/maxdiff_hb.py).

Input is the wide export table (export.rows), so the data pool and speeder filter of the results page apply.
"""
import math

import numpy as np

from app.choice_model import MIN_PERSONS, Z95, ChoiceSet, critical, fit
from app.lang import de_en

RIDGE = 0.1   # weak normal prior (sd about 3) on the centred utilities when a statement is always best or worst
DRAWS = 2000  # simulation draws for the intervals of the probability scores
LOUVIERE = ("Louviere, J. J., Flynn, T. N., & Marley, A. A. J. (2015). Best-worst scaling: Theory, methods and "
            "applications. Cambridge University Press. https://doi.org/10.1017/CBO9781107337855")
MARLEY = ("Marley, A. A. J., & Louviere, J. J. (2005). Some probabilistic models of best, worst, and best–worst choices. "
          "Journal of Mathematical Psychology, 49(6), 464–480. https://doi.org/10.1016/j.jmp.2005.05.003")
LATTERY = ("Lattery, K. (2010). Anchoring maximum difference scaling against a threshold – dual response and direct "
           "binary responses. In Proceedings of the Sawtooth Software Conference (pp. 91–106). Sawtooth Software.")
SOURCES = (LOUVIERE, MARLEY)


def counts(n_items, versions, answers):
    """answers: [(version index, {task index: (best index, worst index)})] with 0-based indices.
    Returns arrays shown, best, worst (length n_items) and the number of persons with at least one set."""
    shown = np.zeros(n_items)
    best = np.zeros(n_items)
    worst = np.zeros(n_items)
    persons = 0
    for v, tasks in answers:
        if v is None or not 0 <= v < len(versions):
            continue
        used = False
        for t, (b, w) in tasks.items():
            if t >= len(versions[v]) or b is None or w is None:
                continue
            s = versions[v][t]
            if b not in s or w not in s or b == w:
                continue  # as in the MNL (task_sets)
            np.add.at(shown, s, 1)
            best[b] += 1
            worst[w] += 1
            used = True
        persons += used
    return shown, best, worst, persons


def scores(shown, best, worst):
    """(best - worst) / shown, best / shown, worst / shown; NaN where a statement was never shown."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return (best - worst) / shown, best / shown, worst / shown


def person_scores(n_items, sets_of_version, tasks):
    """One person's (best - worst) / shown per statement (None if not shown), for the export. The same counting
    as counts() and scores(), in plain Python: it runs once per participation of an export, where the arrays cost
    more than the sums (code review 2026-10-07, R4)."""
    shown, best, worst = [0] * n_items, [0] * n_items, [0] * n_items
    for t, (b, w) in tasks.items():
        if t >= len(sets_of_version) or b is None or w is None:
            continue
        s = sets_of_version[t]
        if b not in s or w not in s or b == w:
            continue  # as in the MNL (task_sets)
        for i in s:
            shown[i] += 1
        best[b] += 1
        worst[w] += 1
    return [round((best[i] - worst[i]) / shown[i], 4) if shown[i] else None for i in range(n_items)]


def persons_from_table(item, table):
    """Parse the wide export rows of one MaxDiff item: per row (pid, version index or None,
    {task index: (best index, worst index)}, anchor answers or None), all 0-based. Parsed once per table
    (the model and the HB status of the results page both ask); the result is shared, not to be changed."""
    from app.choice_model import once

    return once(table, ("maxdiff", item.id), lambda: _persons_from_table(item, table))


def _persons_from_table(item, table):
    code, c = item.code, item.config
    n_sets = item.kind.n_sets(item)
    n_items = len(c.get("statements", []))
    out = []

    def num(x):
        try:
            return int(float(x))
        except (TypeError, ValueError):
            return None

    for r in table:
        v = num(r.get(f"{code}_version"))
        tasks = {}
        for t in range(n_sets):
            b, w = num(r.get(f"{code}_t{t + 1}_best")), num(r.get(f"{code}_t{t + 1}_worst"))
            if b is not None and w is not None:
                tasks[t] = (b - 1, w - 1)
        anchor = None
        if c.get("anchor"):
            row = [num(r.get(f"{code}_anchor{i + 1}")) for i in range(n_items)]
            if any(x is not None for x in row):
                anchor = [x or 0 for x in row]
        out.append((r.get("pid"), None if v is None else v - 1, tasks, anchor))
    return out


def from_table(item, table):
    """Parse the wide export rows of one MaxDiff item into counts() input and anchor answers, one entry
    per row (None where the anchoring question was not answered)."""
    persons = persons_from_table(item, table)
    return [(v, tasks) for _, v, tasks, _ in persons], [a for _, _, _, a in persons]


def parameters(n_items, versions, answers, anchored):
    """Parameters of the MNL: one utility per shown statement except the reference (the last shown one),
    then the threshold if anchored. Returns (estimated statement indices, {statement: column}, number of
    parameters, index of the threshold parameter or None); k = 0 with fewer than two shown statements."""
    shown = counts(n_items, versions, answers)[0]
    est = [i for i in range(n_items) if shown[i] > 0]
    if len(est) < 2:
        return est, {}, 0, None
    col = {i: c for c, i in enumerate(est[:-1])}  # the reference statement has no column (utility 0)
    thr = len(col) if anchored else None
    return est, col, len(col) + (1 if anchored else 0), thr


def task_sets(versions, v, tasks, col, k, person=None):
    """Choice sets of one person's answered sets: best among the shown statements, worst (dummies -1)
    among the rest."""
    def row(i, sign=1.0):
        x = np.zeros(k)
        if i in col:
            x[col[i]] = sign
        return x

    sets = []
    if v is None or not 0 <= v < len(versions):
        return sets
    for t, (b, w) in tasks.items():
        if t >= len(versions[v]) or b is None or w is None:
            continue
        s = list(versions[v][t])
        if b not in s or w not in s or b == w:
            continue
        sets.append(ChoiceSet([row(i) for i in s], s.index(b), person))             # best: largest utility
        rest = [i for i in s if i != b]
        sets.append(ChoiceSet([row(i, -1.0) for i in rest], rest.index(w), person))  # worst: largest -utility
    return sets


def anchor_sets(anchor, est, col, k, thr, person=None):
    """One person's anchor answers: per statement a choice between it and the threshold."""
    t_row = np.zeros(k)
    t_row[thr] = 1.0
    out = []
    for i in est:  # "really important" = the statement beats the threshold
        x = np.zeros(k)
        if i in col:
            x[col[i]] = 1.0
        out.append(ChoiceSet([x, t_row], 0 if i < len(anchor) and anchor[i] else 1, person))
    return out


def usable_anchors(versions, answers, anchors):
    """The anchor answers that enter the model: when they come one per person (aligned with answers),
    only of persons with at least one usable set; [(person, anchor)]."""
    if len(anchors) != len(answers):
        return [(("anchor", n), a) for n, a in enumerate(anchors) if a is not None]
    return [(p, a) for p, (a, (v, tasks)) in enumerate(zip(anchors, answers))
            if a is not None and counts(len(a), versions, [(v, tasks)])[3]]


def choice_sets(n_items, versions, answers, anchors):
    """Choice sets of the MNL (see parameters()), each with its person. Returns (sets, estimated
    statement indices, number of parameters, index of the threshold parameter or None)."""
    used = usable_anchors(versions, answers, anchors)
    est, col, k, thr = parameters(n_items, versions, answers, bool(used))
    if not k:
        return [], est, 0, None
    sets = [s for p, (v, tasks) in enumerate(answers) for s in task_sets(versions, v, tasks, col, k, p)]
    if sets and thr is not None:
        for p, a in used:
            sets += anchor_sets(a, est, col, k, thr, p)
    return sets, est, k, thr


def components(n_items, versions, answers):
    """Groups of statements linked through the answered sets (union-find); one group = connected design."""
    parent = list(range(n_items))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    seen = set()
    for v, tasks in answers:
        if v is None or not 0 <= v < len(versions):
            continue
        for t, (b, w) in tasks.items():
            if t >= len(versions[v]) or b is None or w is None:
                continue
            s = versions[v][t]
            if b not in s or w not in s or b == w:
                continue
            seen.update(s)
            for i in s[1:]:
                parent[root(i)] = root(s[0])
    groups = {}
    for i in sorted(seen):
        groups.setdefault(root(i), []).append(i)
    return list(groups.values())


def disconnected_note(blocks, anchored=False):
    """The note for statements that fall apart into groups (components) never shown together: no model scores,
    neither of the aggregate MNL nor of the HB estimate (app/maxdiff_hb.py)."""
    groups = " | ".join(", ".join(str(i + 1) for i in g) for g in blocks)
    return (de_en("Die Aussagen zerfallen in {n} Gruppen, die nie gemeinsam in einem Set vorkamen ({groups}). "
                  "Zwischen den Gruppen gibt es keinen gemeinsamen Maßstab, deshalb hier keine Modell-Scores; "
                  "die Zählwerte oben gelten nur innerhalb einer Gruppe. Der Versuchsplan braucht mehr Sets oder Versionen, "
                  "die die Gruppen verbinden.",
                  "The statements fall apart into {n} groups that never appeared together in a set ({groups}). "
                  "There is no common scale between the groups, so no model scores here; the counts above apply only "
                  "within a group. The experimental design needs more sets or versions that connect the groups.",
                  n=len(blocks), groups=groups)
            # M-13 (audit 2026-10-05): the anchor question would join the groups only through one binary answer per
            # statement; a scale resting on it alone is not shown (a choice of Askurious)
            + (de_en(" Auch die Ankerfrage verbindet die Gruppen hier nicht: Sie gibt je Aussage nur ein Ja oder Nein "
                     "gegen die Schwelle und reicht als einziger gemeinsamer Maßstab nicht aus.",
                     " The anchor question does not connect the groups here either: it gives only a yes or no against "
                     "the threshold per statement and is not enough as the only common scale.") if anchored else ""))


def prob_scores(u, a, extra=None):
    """Probability scores of zero-centred utilities u (last axis): the logit probability of being chosen as best
    against a - 1 statements of mean utility 0 (see the module docstring), rescaled to sum 100 over the
    statements; extra (the threshold) goes through the same transformation and factor."""
    p = np.exp(u) / (np.exp(u) + a - 1)
    f = 100 / p.sum(axis=-1, keepdims=True)
    if extra is None:
        return p * f, None
    return p * f, np.exp(extra) / (np.exp(extra) + a - 1) * f[..., 0]


def mnl(n_items, versions, answers, anchors, labels, set_size):
    """Aggregate MNL for the results page (dict), or None without enough data."""
    sets, est, k, thr = choice_sets(n_items, versions, answers, anchors)
    if not sets:
        return None
    shown, best, worst, persons = counts(n_items, versions, answers)
    blocks = components(n_items, versions, answers)
    if len(blocks) > 1:
        return {"disconnected": True, "persons": persons,
                "warnings": [disconnected_note(blocks, any(a is not None for a in anchors or []))]}
    m = len(est)
    # zero-centred utilities of the estimated statements: u = M @ beta (the reference has utility 0),
    # the threshold minus the mean utility: t_vec @ beta
    M = np.zeros((m, k))
    M[np.arange(m - 1), np.arange(m - 1)] = 1.0
    M[:, :m - 1] -= 1.0 / m
    t_vec = None
    if thr is not None:
        t_vec = np.zeros(k)
        t_vec[thr] = 1.0
        t_vec[:m - 1] -= 1.0 / m
    # the penalty (only for separated data) shrinks the centred utilities, not the distance to the reference
    penalty = RIDGE * (M.T @ M + (np.outer(t_vec, t_vec) if t_vec is not None else 0))
    separated = [i for i in est if best[i] == shown[i] or worst[i] == shown[i]]
    anchor_flat = thr is not None and len({bool(x) for _, a in usable_anchors(versions, answers, anchors)
                                           for x in a}) < 2
    ridge = bool(separated or anchor_flat)
    info = {}
    beta, cov, ll, converged, iterations = fit(sets, k, ridge=penalty if ridge else 0.0, info=info)
    if not ridge and (not converged or cov is None or np.max(np.abs(beta)) > 15):
        ridge = True  # separation the counts do not show (e.g. a group of statements always ahead)
        beta, cov, ll, converged, iterations = fit(sets, k, ridge=penalty, info=info)
    q = critical(info)  # 1.96, or t(G - 1) for the sandwich over few persons
    # A separated statement (always best or always worst) has no finite utility; the prior only keeps it
    # bounded. It is left out of the scale: the utilities are centred on, and the scores sum to 100 over, the
    # other statements, whose distances to each other the data do determine. Otherwise the arbitrary bounded
    # value would move every score, and the sandwich would report almost no uncertainty for it.
    keep = [j for j, i in enumerate(est) if i not in separated]
    if len(keep) < 2:
        keep = list(range(m))  # nothing left to compare: the old scale over all statements
    scaled = len(keep) < m
    if scaled:
        R = M + 1.0 / m * np.concatenate([np.ones((m, m - 1)), np.zeros((m, k - m + 1))], axis=1)  # raw utilities
        shift = R[keep].mean(axis=0)
        M = R - shift
        if t_vec is not None:
            t_vec = np.eye(k)[thr] - shift
    u = M @ beta
    threshold = None if t_vec is None else float(t_vec @ beta)
    score, t_score = prob_scores(u[keep], set_size, threshold)
    u_se = s_lo = s_hi = s_se = None
    t_se = t_lo = t_hi = None
    if cov is not None:
        u_se = np.sqrt(np.maximum(np.einsum("ik,kl,il->i", M, cov, M), 0))
        # stretched by (q / z)^2 so that the 2.5 and 97.5 percentiles lie q instead of z standard errors out
        draws = np.random.default_rng(0).multivariate_normal(beta, cov * (q / Z95) ** 2, size=DRAWS, method="cholesky")
        d_score, d_t = prob_scores((draws @ M.T)[:, keep], set_size, None if t_vec is None else draws @ t_vec)
        s_lo, s_hi = np.percentile(d_score, [2.5, 97.5], axis=0)
        s_se = d_score.std(axis=0) * Z95 / q  # the standard error itself is not stretched
        if t_vec is not None and not anchor_flat:  # a flat anchor leaves the threshold to the prior: no interval
            t_se = math.sqrt(max(float(t_vec @ cov @ t_vec), 0.0))
            t_lo, t_hi = (float(x) for x in np.percentile(d_t, [2.5, 97.5]))
    warnings = []
    if persons < MIN_PERSONS:
        if persons == 1:
            warnings.append(de_en("Nur {n} Person: Die Schätzung ist unsicher (Richtwert ab {m}, besser 200 bis 300).",
                                  "Only {n} person: the estimation is uncertain (guideline: from {m}, better 200 to "
                                  "300).", n=persons, m=MIN_PERSONS))
        else:
            warnings.append(de_en("Nur {n} Personen: Die Schätzung ist unsicher (Richtwert ab {m}, besser 200 bis 300).",
                                  "Only {n} people: the estimation is uncertain (guideline: from {m}, better 200 to "
                                  "300).", n=persons, m=MIN_PERSONS))
    if not converged:
        warnings.append(de_en("Das Modell ist nicht konvergiert; die Werte sind nicht verlässlich.",
                              "The model has not converged; the values are not reliable."))
    if cov is None:
        warnings.append(de_en("Keine Standardfehler: Das Modell ist nicht identifiziert (zu wenige Daten oder "
                              "Aussagen, die nie gemeinsam mit anderen gezeigt wurden).",
                              "No standard errors: the model is not identified (too little data or statements that "
                              "were never shown together with others)."))
    if separated:
        warnings.append(de_en("Aussage {which}: immer als wichtigste oder immer als am wenigsten wichtige gewählt. Der "
                              "Nutzen wäre unendlich und ist durch eine schwache Regularisierung begrenzt. Sie steht "
                              "deshalb über bzw. unter allen anderen, ohne Score; ",
                              "Statement {which}: always chosen as most important or always as least important. The "
                              "utility would be infinite and is bounded by a weak regularisation. It therefore ranks "
                              "above or below all others, without a score; ",
                              which=", ".join(str(i + 1) for i in separated))
                        + (de_en("die Scores (Summe 100) und die nullzentrierten Nutzen gelten für die übrigen Aussagen.",
                                 "the scores (sum 100) and the zero-centred utilities apply to the other statements.")
                           if scaled else de_en("es bleiben zu wenige Aussagen für einen Vergleich, die Werte nur als "
                                                "Richtung lesen.",
                                                "too few statements remain for a comparison; read the values only as "
                                                "a direction.")))
    elif ridge and not anchor_flat:
        warnings.append(de_en("Einige Aussagen liegen stets vor oder hinter anderen; die Schätzung ist durch eine "
                              "schwache Regularisierung begrenzt.",
                              "Some statements are always ahead of or behind others; the estimation is bounded by a "
                              "weak regularisation."))
    if anchor_flat:
        warnings.append(de_en("In der Ankerfrage wurden alle oder keine Aussagen angekreuzt; die Schwelle ist kaum "
                              "bestimmt.",
                              "In the anchor question all or no statements were ticked; the threshold is hardly "
                              "determined."))
    if m < n_items:
        warnings.append(de_en("{n} Aussage(n) nie gezeigt; sie fehlen im Modell.",
                              "{n} statement(s) never shown; they are missing from the model.", n=n_items - m))
    bw = scores(shown, best, worst)[0]
    rows = []
    sure = [None] * m  # is the statement clearly above or below the threshold (95 % interval of the difference)?
    if threshold is not None and cov is not None and anchor_flat:
        sure = [False] * m  # the threshold is set by the prior: no statement is clearly above or below it
    elif threshold is not None and cov is not None:
        D = M - t_vec
        se_d = np.sqrt(np.maximum(np.einsum("ik,kl,il->i", D, cov, D), 0))
        sure = [bool(abs(u[j] - threshold) > q * se_d[j]) for j in range(m)]
    place = {j: n for n, j in enumerate(keep)}  # row of the scores
    for j, i in enumerate(est):
        n = place.get(j)

        def at(values):
            return None if values is None or n is None else float(values[n])

        out = i in separated and scaled  # left out of the scale: "über allen" / "unter allen" instead of numbers
        rows.append({"n": i + 1, "label": labels[i], "score": at(score), "lo": at(s_lo), "hi": at(s_hi), "se": at(s_se),
                     "u": float(u[j]), "u_se": None if u_se is None or out else float(u_se[j]), "count": float(bw[i]),
                     "separated": i in separated, "side": ("top" if best[i] == shown[i] else "bottom") if out else None,
                     "above": None if threshold is None else bool(u[j] > threshold),
                     "above_sure": True if out and threshold is not None and not anchor_flat else sure[j]})
    rows.sort(key=lambda r: (0 if r["side"] == "top" else 2 if r["side"] == "bottom" else 1, -(r["score"] or 0)))
    null_ll = sum(-math.log(len(s.X)) for s in sets)
    result = {"persons": persons, "observations": len(sets), "set_size": set_size, "loglik": ll,
              "null_loglik": null_ll, "rho2": 1 - ll / null_ll if null_ll else None, "converged": converged,
              "iterations": iterations, "ridge": ridge, "rows": rows, "warnings": warnings,
              "robust": cov is not None and info.get("robust", False), "q": q,
              "t_df": info["clusters"] - 1 if q != Z95 else None,
              "anchor_n": len(usable_anchors(versions, answers, anchors)) if thr is not None else 0,
              "threshold": None if threshold is None else {"u": threshold, "u_se": t_se, "score": float(t_score),
                                                           "lo": t_lo, "hi": t_hi}}
    result["chart"] = chart(result)
    return result


def chart(model, width=720, label_w=250, value_w=56, row_h=24, bar_h=14):
    """Geometry of the horizontal bar chart of the probability scores (0 at the left, in row order),
    whiskers for the 95 % interval and a vertical line for the anchor threshold, in SVG pixels."""
    rows, t = [r for r in model["rows"] if r["score"] is not None], model["threshold"]  # not the statements off the scale
    reach = [max(r["score"], r["hi"] or 0) for r in rows] + ([t["score"]] if t else [])
    step, top = axis(max(reach, default=0))
    x0, x1 = label_w, width - value_w
    scale = (x1 - x0) / top
    tx = x0 + t["score"] * scale if t else None
    top_y = 20 if t else 6  # room for the threshold label
    out = []
    for n, r in enumerate(rows):
        y = top_y + n * row_h
        w = r["score"] * scale
        lo = hi = None
        if r["lo"] is not None:
            lo, hi = x0 + r["lo"] * scale, x0 + r["hi"] * scale
        text_x = max(x0 + w, hi or 0) + 4
        text_w = 7 * len(f"{r['score']:.1f}")  # about 7 px per character at 12 px
        if tx is not None and text_x - 3 <= tx <= text_x + text_w + 3:
            text_x = tx + 5  # the value would cross the threshold line: put it right of the line
        out.append({"n": r["n"], "label": r["label"], "score": r["score"], "x": x0, "w": round(max(w, 0.5), 1),
                    "y": y, "h": bar_h, "mid": y + bar_h / 2, "lo": None if lo is None else round(lo, 1),
                    "hi": None if hi is None else round(hi, 1), "text_x": round(text_x, 1), "above": r["above"]})
    height = top_y + len(rows) * row_h + 18
    count = round(top / step)
    ticks = [(round(x0 + i / count * (x1 - x0), 1), round(i * step, 6)) for i in range(count + 1)]
    return {"rows": out, "top_y": top_y, "width": width, "height": height, "label_w": label_w, "ticks": ticks,
            "tick_y": height - 4, "plot_bottom": height - 16,
            "threshold_x": round(tx, 1) if t else None}


def axis(x, max_ticks=5):
    """Round axis for 0..x: (step, end) with step 1, 2, 2.5 or 5 x 10^k and at most max_ticks steps."""
    if x <= 0:
        return 1.0, 1.0
    e = math.floor(math.log10(x / max_ticks))
    for k in (e, e + 1):
        for m in (1, 2, 2.5, 5):
            step = m * 10 ** k
            n = math.ceil(x / step - 1e-9)
            if n <= max_ticks:
                return step, n * step
    return 10.0 ** (e + 2), 10.0 ** (e + 2)


def analyse(item, table, labels):
    """Rows for the results page, sorted by score (highest first)."""
    design = (item.config.get("design") or {}).get("versions") or []
    n_items = len(labels)
    answers, anchors = from_table(item, table)
    shown, best, worst, persons = counts(n_items, design, answers)
    bw, bs, ws = scores(shown, best, worst)
    anchors_given = [a for a in anchors if a is not None]
    anchor = np.array(anchors_given).mean(axis=0) if anchors_given else None
    rows = []
    for i in range(n_items):
        rows.append({"n": i + 1, "label": labels[i], "shown": int(shown[i]), "best": int(best[i]), "worst": int(worst[i]),
                     "score": None if np.isnan(bw[i]) else float(bw[i]),
                     "best_share": None if np.isnan(bs[i]) else float(bs[i]),
                     "worst_share": None if np.isnan(ws[i]) else float(ws[i]),
                     "anchor": None if anchor is None else float(anchor[i])})
    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    set_size = len(design[0][0]) if design and design[0] else item.config.get("set_size") or 4
    # answers of a design version the current design does not have are left out (counts())
    skipped = sum(1 for v, tasks in answers if design and tasks and v is not None and not 0 <= v < len(design))
    return {"rows": rows, "persons": persons, "sets": int(best.sum()), "anchor_n": len(anchors_given), "skipped": skipped,
            "mnl": mnl(n_items, design, answers, anchors, labels, set_size) if persons else None}
