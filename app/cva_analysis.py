"""Analysis of the ratings-based conjoint (CVA, app/items/cva.py; concept aca-cva-konzept.md, C5).

Per person an ordinary least squares regression of the answers on the levels shown (the usual
estimator for ratings, Green & Srinivasan, 1990): effects coding, so the part-worths of an attribute sum to 0 and the
constant is the mean answer. Single profiles: y = rating, x = levels of the profile. Pairs: y = the category from the
point of view of the first profile (-m .. m), x = levels of the first minus levels of the second, the constant is the
pull towards the side shown first. Ranking: y = n + 1 - rank (the offer put first gets n), x as for single profiles:
the OLS on ranks of classic conjoint (Green & Srinivasan, 1990), the usual estimator for them too. Askurious keeps the
answer scale, so a part-worth reads as "points on the rating scale" (only the share-of-preference
simulator, a later stage, depends on that scale).

A person gets values when the tasks they answered exceed the parameters by at least one and identify all of them.
Over persons: the mean of the zero-centred part-worths and of the importances with a 95 % t interval over persons
(every person rated a whole plan of their own version, so the mean of person values is the estimate of the
sample; Green & Srinivasan, 1990), the R² per person, and two flags: the same answer to every task (nothing to
estimate) and R² under R2_LOW (Festlegung von Askurious).

Holdouts (app/holdouts.py, stage 4): fixed offers the same for everyone, never in the regression. Per holdout the
answer each person's part-worths predict (constant + sum of the part-worths, pairs: of the differences), compared
with the answer given: mean absolute deviation in points of the scale, the correlation over persons (does the model
tell apart who likes the offer more), for pairs also the share of persons whose preferred side it predicts (answers
"same" left out). With two holdouts or more of single offers the hit rate: the share of persons whose best predicted
holdout is among their best rated ones; chance is the share the best rated make up (Orme, 2009, for choice
holdouts; Festlegung von Askurious for ratings)."""
import math

import numpy as np

from app.intervals import t_quantile

R2_LOW = 0.3   # a fit below this is flagged on the results page (Festlegung von Askurious)


def effects_row(profile, levels):
    """Effects coding of one profile: per attribute L - 1 columns, the last level -1 in all of them."""
    out = []
    for lv, n in zip(profile, levels):
        cols = [0.0] * (n - 1)
        if lv == n - 1:
            cols = [-1.0] * (n - 1)
        elif lv is not None and 0 <= lv < n - 1:
            cols[lv] = 1.0
        out += cols
    return out


def part_worths(beta, levels):
    """The zero-centred part-worths per attribute from the effects coefficients (without the constant)."""
    out, k = [], 0
    for n in levels:
        b = list(beta[k:k + n - 1])
        out.append(b + [-sum(b)])
        k += n - 1
    return out


def importances(pw):
    ranges = [max(p) - min(p) for p in pw]
    total = sum(ranges)
    return [r / total if total > 0 else None for r in ranges]


def rows_of(item, answer, versions=None):
    """[(x, y)] of one answer: the model row and the answer of every task answered, in page order."""
    answer = answer if isinstance(answer, dict) else {}
    kind = item.kind
    levels = kind.level_counts(item)
    versions = versions if versions is not None else ((item.config.get("design") or {}).get("versions") or [])
    v = answer.get("v")
    if not isinstance(v, int) or not 0 <= v < len(versions):
        return []
    pairs = kind.form(item) == "pairs"
    ranking = kind.form(item) == "ranking"
    n = len(versions[v])
    out = []
    for t, task in enumerate(versions[v]):
        y = answer.get(f"t{t}")
        if not isinstance(y, (int, float)) or isinstance(y, bool):
            continue
        if ranking:
            y = n + 1 - y  # the offer put first counts most
        if pairs:
            a, b = task
            x = [1.0] + [p - q for p, q in zip(effects_row(a, levels), effects_row(b, levels))]
        else:
            x = [1.0] + effects_row(task, levels)
        out.append((x, float(y)))
    return out


def fit_person(rows, levels):
    """{"constant", "pw", "importance", "r2", "n", "flat"} or None when the tasks do not identify every part-worth."""
    p = 1 + sum(n - 1 for n in levels)
    if len(rows) < p + 1:
        return None
    X = np.array([r[0] for r in rows])
    y = np.array([r[1] for r in rows])
    if np.linalg.matrix_rank(X) < p:
        return None
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    sst = float(((y - y.mean()) ** 2).sum())
    flat = sst == 0
    r2 = None if flat else max(0.0, 1 - float((resid ** 2).sum()) / sst)
    pw = part_worths([float(b) for b in beta[1:]], levels)
    return {"constant": float(beta[0]), "pw": pw, "importance": importances(pw), "r2": r2, "n": len(rows),
            "flat": flat}


def person_values(item, answer):
    """fit_person for one stored answer (the export uses it)."""
    return fit_person(rows_of(item, answer), item.kind.level_counts(item))


def _mean_ci(values):
    values = [v for v in values if v is not None and math.isfinite(v)]
    n = len(values)
    if not n:
        return None, None, None, None
    m = sum(values) / n
    if n < 2:
        return m, None, None, None
    sd = math.sqrt(sum((v - m) ** 2 for v in values) / (n - 1))
    se = sd / math.sqrt(n)
    q = t_quantile(0.975, n - 1)
    return m, se, m - q * se, m + q * se


def analyse(item, responses, lang):
    """The results of one CVA over the given responses (choice_model.responses_for)."""
    from app.choice_model import charts
    from app.items.base import plain

    kind = item.kind
    levels = kind.level_counts(item)
    attrs = kind.attributes(item, lang)
    fits, answered = [], 0
    versions = (item.config.get("design") or {}).get("versions") or []
    for r in responses:
        answer = (r.answers or {}).get(str(item.id))
        rows = rows_of(item, answer, versions)
        if not rows:
            continue
        answered += 1
        fitted = fit_person(rows, levels)
        if fitted is not None:
            fits.append({**fitted, "pid": r.pid})
    out = {"persons": answered, "estimated": len(fits), "form": kind.form(item), "model": None,
           "flat": sum(1 for f in fits if f["flat"]),
           "low": sum(1 for f in fits if f["r2"] is not None and f["r2"] < R2_LOW), "r2_low": R2_LOW}
    usable = [f for f in fits if not f["flat"]]
    # the part-worths per person for the market simulator (app/simulator.py, basis "persons")
    out["person_pw"] = [[f["pid"], f["pw"]] for f in usable]
    out["holdout"] = holdout_view(item, responses, usable)
    if not usable:
        return out
    n = len(usable)
    q = t_quantile(0.975, n - 1) if n > 1 else None
    model_attrs = []
    for j, a in enumerate(attrs):
        level_rows = []
        for k, label in enumerate(a["level_labels"]):
            m, se, lo, hi = _mean_ci([f["pw"][j][k] for f in usable])
            level_rows.append({"label": plain(label), "beta": m, "se": se or 0.0, "lo": lo, "hi": hi})
        imp, imp_se, imp_lo, imp_hi = _mean_ci([f["importance"][j] for f in usable])
        model_attrs.append({"label": plain(a["label"]), "code": a["code"], "levels": level_rows, "importance": imp,
                            "importance_lo": imp_lo, "importance_hi": imp_hi,
                            "ordered": kind.levels_ordered(item, a)})
    r2s = sorted(f["r2"] for f in usable if f["r2"] is not None)
    constant = _mean_ci([f["constant"] for f in usable])
    model = {"attributes": model_attrs, "q": q or 1.96, "t_df": n - 1 if n > 1 else None, "warnings": [],
             "persons": n, "price": None, "wtp_ok": False, "constant": constant[0],
             "r2_mean": sum(r2s) / len(r2s) if r2s else None, "r2_median": r2s[len(r2s) // 2] if r2s else None}
    if n < 2:
        model["warnings"].append(_one_person())
    model["charts"] = charts(model)
    out["model"] = model
    return out


def _one_person():
    from app.lang import de_en

    return de_en("Nur eine Person mit eigenen Werten: Intervalle gibt es erst ab zwei Personen.",
                 "Only one person with values of their own: intervals need at least two people.")


def predict(fit, profiles):
    """The answer one person's values predict for a task: the rating of the offer, or for a pair the category from the
    point of view of the first offer."""
    u = [sum(fit["pw"][a][lv] for a, lv in enumerate(p)) for p in profiles]
    return fit["constant"] + (u[0] - u[1] if len(u) == 2 else u[0])


def _pearson(x, y):
    if len(x) < 3:
        return None
    x, y = np.array(x, dtype=float), np.array(y, dtype=float)
    if x.std() == 0 or y.std() == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def holdout_view(item, responses, fits):
    """Per holdout the fit of the predictions (module docstring), or None without holdouts or values."""
    from app import holdouts, intervals

    active = holdouts.cbc_active(item)
    if not active or not fits:
        return None
    by_pid = {f["pid"]: f for f in fits}
    given = {}  # pid -> {h: answer}
    for r in responses:
        answer = (r.answers or {}).get(str(item.id))
        if r.pid in by_pid and isinstance(answer, dict):
            given[r.pid] = {h: answer.get(f"h{h}") for h, _ in active
                            if isinstance(answer.get(f"h{h}"), (int, float)) and not isinstance(answer.get(f"h{h}"), bool)}
    pairs = item.kind.form(item) == "pairs"
    rows, per_person = [], {}
    for h, profiles in active:
        pred, obs = [], []
        for pid, answers in given.items():
            if h in answers:
                p = predict(by_pid[pid], profiles)
                pred.append(p)
                obs.append(float(answers[h]))
                per_person.setdefault(pid, {})[h] = (p, float(answers[h]))
        n = len(obs)
        row = {"h": h + 1, "n": n, "obs": sum(obs) / n if n else None, "pred": sum(pred) / n if n else None,
               "mae": sum(abs(p - o) for p, o in zip(pred, obs)) / n if n else None, "r": _pearson(pred, obs),
               "side": None}
        if pairs:
            sided = [(p, o) for p, o in zip(pred, obs) if o != 0]
            # a prediction of exactly 0 counts half (no side)
            hits = sum(1.0 if p * o > 0 else (0.5 if p == 0 else 0.0) for p, o in sided)
            ci = intervals.wilson(hits, len(sided)) if sided else None
            row["side"] = {"n": len(sided), "hit": hits / len(sided), "lo": ci[0] if ci else None,
                           "hi": ci[1] if ci else None} if sided else None
        rows.append(row)
    hit = None
    if not pairs and len(active) >= 2:
        hits = chance = 0.0
        complete = [v for v in per_person.values() if len(v) == len(active)]
        for v in complete:
            values = list(v.values())
            best = max(o for _, o in values)
            top = {k for k, (_, o) in enumerate(values) if o == best}
            p_best = max(p for p, _ in values)
            picked = [k for k, (p, _) in enumerate(values) if p == p_best]
            hits += sum(1 for k in picked if k in top) / len(picked)
            chance += len(top) / len(values)
        if complete:
            ci = intervals.wilson(hits, len(complete))
            hit = {"n": len(complete), "hit": hits / len(complete), "lo": ci[0] if ci else None,
                   "hi": ci[1] if ci else None, "chance": chance / len(complete)}
    return {"rows": rows, "pairs": pairs, "hit": hit}
