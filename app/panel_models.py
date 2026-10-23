"""Models over the waves of a panel on the results page (longitudinal and multilevel concept 2026-10-17, Ralf:
"umsetzen", all as recommended). The waves are linked as for the export (app/waves.py, by the personal code; with
the tick "tolerant" also codes that differ in one part); every linked person counts, also one seen in a single wave
(full information maximum likelihood, E7). The models are written as parameter tables (app/sem_table.py) and checked
against independently computed reference values.

What is modelled is chosen in the panel "Wellen" of the page "Studienplan" (E2): the variables that stand in every
wave under the same code (a scale or subscale with the same statements, or a metric or ordinal question), stored in
survey.waves["model"] beside the list of waves and written to every wave like it; no part of the plan, so choosing it
after the start is no deviation from a pre-registration.

Per variable (E4, E6):
  over time    the mean and SD of the score in every wave;
  invariance   for a scale with at least three statements: the same factor in every wave with correlated residuals
               of the same statement over the waves, configural, metric (equal loadings), scalar (+ equal
               intercepts), judged as in the groups (Chen, 2007), with the latent means of the scalar model;
  growth       from three waves: a linear latent growth curve of the score (intercept and slope with their means,
               variances and correlation; Bollen & Curran, 2006), quadratic from four waves with the tick, the time
               as 0, 1, 2 … or, with the tick, the weeks between the medians of the waves' start dates; predictors of
               intercept and slope from the first wave with the tick (the other chosen variables).
Scores are the means of the statements (scales.score), with the note that their measurement error is not corrected.

Shown in a block "Längsschnitt über die Wellen" on the results page of every wave, to people with at least read
access to every wave (E3), computed on a button and kept under the fingerprints of all waves (results_store, name
"panel"); computing it is written to the access log like the merged export, since it reads the linked answers."""
import hashlib
import json
import math
import re
import statistics
from datetime import datetime

import numpy as np

from app import sem, sem_means, sem_table
from app.lang import de_en
from app.panel_tables import LEVELS, _node, cross_table, growth_table, invariance_table  # noqa: F401 (tests use them here)

VERSION = 1
MIN_N = 100  # linked persons with a value for invariance and growth (set by Askurious, E10)
MIN_ITEMS = 3  # statements of a scale for the invariance over time
MAX_COLUMNS = 40  # columns of one invariance model (statements x waves)
MIN_RICLPM = 200  # linked persons for the random-intercept cross-lagged panel model (Mulder, 2023; E10)
MAX_CROSS = 4  # variables in one cross-lagged panel model

BOLLEN_CURRAN = ("Bollen, K. A., & Curran, P. J. (2006). Latent curve models: A structural equation perspective. Wiley. "
                 "https://doi.org/10.1002/0471746096")
WIDAMAN = ("Widaman, K. F., Ferrer, E., & Conger, R. D. (2010). Factorial invariance within longitudinal structural "
           "equation models: Measuring the same construct across time. Child Development Perspectives, 4(1), 10–18. "
           "https://doi.org/10.1111/j.1750-8606.2009.00110.x")
HAMAKER = ("Hamaker, E. L., Kuiper, R. M., & Grasman, R. P. P. P. (2015). A critique of the cross-lagged panel model. "
           "Psychological Methods, 20(1), 102–116. https://doi.org/10.1037/a0038889")
MULDER_HAMAKER = ("Mulder, J. D., & Hamaker, E. L. (2021). Three extensions of the random intercept cross-lagged panel "
                  "model. Structural Equation Modeling, 28(4), 638–648. https://doi.org/10.1080/10705511.2020.1784738")
MULDER = ("Mulder, J. D. (2023). Power analysis for the random intercept cross-lagged panel model using the powRICLPM "
          "R-package. Structural Equation Modeling, 30(4), 645–658. https://doi.org/10.1080/10705511.2022.2122467")
SOURCES = (BOLLEN_CURRAN, WIDAMAN, HAMAKER, MULDER_HAMAKER, MULDER, sem_means.CHEN, sem_means.SATORRA_BENTLER_2001, sem_means.ENDERS,
           sem_means.YUAN_BENTLER)
FLAGS = ("latent", "quadratic", "real_time", "tolerant", "predictors", "equal")


# --- the choice ----------------------------------------------------------------------------------------------------

def settings(survey):
    """{"vars": [names], "quadratic", "real_time", "tolerant", "predictors": bool} of the waves of `survey`."""
    raw = ((survey.waves or {}).get("model") or {}) if survey is not None else {}
    out = {"vars": [str(v) for v in raw.get("vars") or [] if isinstance(v, str)][:20]}
    for k in FLAGS:
        out[k] = bool(raw.get(k))
    return out


def save_settings(survey, user, form):
    """The choice from the form of the panel "Wellen" (fields panel_var, panel_<flag>), written to every wave of the
    list the person may edit, like the list itself (waves.save)."""
    from app import waves
    from app.extensions import db
    from app.models import Survey

    if not waves.of(survey):
        return
    names = {c["name"] for c in candidates(user, survey)}
    model = {"vars": [v for v in dict.fromkeys(form.getlist("panel_var")) if v in names]}
    for k in FLAGS:
        if form.get("panel_" + k) == "1":
            model[k] = True
    for row in waves.of(survey):
        s = db.session.get(Survey, row["survey"])
        if s is not None and waves.may_download(user, s) and s.waves:
            s.waves = {**s.waves, "model": model}


# --- the waves and their variables ---------------------------------------------------------------------------------

def usable(user, survey):
    """(rows [{"n", "survey", "label"}] of the waves to link, labels of the waves the person may not read or that
    cannot be linked). Read access to every wave is needed: the block shows nothing of only some of them."""
    from app import waves
    from app.extensions import db
    from app.models import Survey

    rows, missing, first = [], [], None
    for n, row in enumerate(waves.of(survey), start=1):
        s = db.session.get(Survey, row["survey"])
        label = row["label"] or de_en("Welle {n}", "Wave {n}", n=n)
        if s is None or not s.can(user, "viewer"):
            missing.append(label)
            continue
        sch = waves.scheme(s)
        if sch is None or (first is not None and sch != first):
            missing.append(label)
            continue
        first = first or sch
        rows.append({"n": n, "survey": s, "label": label})
    return rows, missing


def _variables(s):
    """{name: {"name", "label", "kind": "scale" | "item", "code", "codes"}} of one survey."""
    from app import sem_model, study_plan

    out = {}
    for code, _label in study_plan.measures(s):
        info = sem_model.variable(s, code)
        if info.get("kind") == "latent" and info.get("parts"):
            for name, title, items in info["parts"]:
                out[name] = {"name": name, "label": f"{title} · {info.get('label') or ''}".strip(" ·"), "kind": "scale",
                             "code": code, "codes": [c for c, _, _ in items]}
        elif "error" not in info and info.get("kind") == "observed" and not info.get("coding") \
                and not info.get("groups") and "item" in info:
            out[code] = {"name": code, "label": f"{code} · {info.get('label') or ''}".strip(" ·"), "kind": "item",
                         "code": code, "codes": [code]}
    return out


def candidates(user, survey, rows=None):
    """The variables in every usable wave under the same name, with the same statements: [{"name", "label", "kind",
    "code", "codes"}] in the order of the first wave."""
    rows = usable(user, survey)[0] if rows is None else rows
    if len(rows) < 2:
        return []
    per = [_variables(r["survey"]) for r in rows]
    return [v for name, v in per[0].items()
            if all(name in p and p[name]["codes"] == v["codes"] and p[name]["kind"] == v["kind"] for p in per[1:])]


# --- the data -----------------------------------------------------------------------------------------------------

def _pool(s):
    from app.surveys import stored_pool

    return {**stored_pool(s, True), "drop": True, "speed_real": True, "weighted": False}


def key(user, survey):
    """The fingerprint of what the block is computed from: the waves, their data pools and the choice."""
    from app import results_store

    rows, missing = usable(user, survey)
    basis = {"v": VERSION, "settings": settings(survey), "missing": missing,
             "hyps": [[h.get("iv"), h.get("dv")] for r in rows for _, h in lagged_hypotheses(r["survey"])],
             "waves": [(r["survey"].id, r["label"], results_store.fingerprint(r["survey"], _pool(r["survey"])))
                       for r in rows]}
    return hashlib.sha256(json.dumps(basis, sort_keys=True, default=str).encode()).hexdigest()


def _start_weeks(tables):
    """The weeks from the median start of the first wave to the median start of each wave."""
    out = []
    for table in tables:
        found = []
        for r in table:
            try:
                found.append(datetime.strptime(str(r.get("started") or ""), "%Y-%m-%d %H:%M:%S").timestamp())
            except ValueError:
                continue
        out.append(statistics.median(found) if found else None)
    if any(t is None for t in out):
        return None
    return [round((t - out[0]) / (7 * 86400), 2) for t in out]


def linked(user, survey, rows, chosen, items=False):
    """The linked persons: (report of the matching, {name: array persons x waves of scores}, {name: array persons x
    (waves x statements)} when items, weeks per wave or None)."""
    from app import compare, hypotheses, scales, sem_model, waves

    st = settings(survey)
    tables, codes = [], []
    for r in rows:
        s = r["survey"]
        table = compare.table_of(s, _pool(s))
        code = waves.code_item(s).code
        tables.append(table)
        codes.append([("".join(str(t.get(code) or "").split()).upper() or None) for t in table])
    persons = waves.link(codes, list(waves.scheme(rows[0]["survey"])), st["tolerant"])
    rep = waves.report(persons, len(rows))
    persons = [p for p in persons if p["quality"] in waves.CODED]
    T, N = len(rows), len(persons)
    scores, item_values = {}, {}
    for var in chosen:
        mat = np.full((N, T), np.nan)
        imat = np.full((N, T * len(var["codes"])), np.nan) if items and var["kind"] == "scale" else None
        for w, r in enumerate(rows):
            s = r["survey"]
            if var["kind"] == "scale":
                item = sem_model.variable(s, var["code"])["item"]
                hidden = scales._missing(item, var["codes"])
                vals = [scales.values(item, var["codes"], t, hidden=hidden) for t in tables[w]]
                score = [scales.score(v, scales.shown(item, var["codes"], t, "", hidden)) for v, t in zip(vals, tables[w])]
            else:
                found, _ = hypotheses._values(s, var["code"], tables[w])
                score = [v if isinstance(v, float) else None for v in found or []]
                vals = None
            for k, p in enumerate(persons):
                row = p["rows"].get(w)
                if row is None:
                    continue
                if score[row] is not None:
                    mat[k, w] = score[row]
                if imat is not None:
                    for j, v in enumerate(vals[row]):
                        if v is not None:
                            imat[k, w * len(var["codes"]) + j] = v
        scores[var["name"]] = mat
        if imat is not None:
            item_values[var["name"]] = imat
    return rep, scores, item_values, _start_weeks(tables)


# --- the models ---------------------------------------------------------------------------------------------------

def _safe(x, cols, tab, same):
    try:
        return sem_table.fit(x, cols, tab, same=same)
    except (sem.SemError, np.linalg.LinAlgError, ValueError, ArithmeticError):
        return {"converged": False, "problems": ["estimate"]}


def _fit_summary(fit):
    if not fit.get("converged"):
        return {"converged": False, "problems": fit.get("problems") or []}
    keys = ("n", "n_complete", "df", "chi2", "p", "cfi", "tli", "rmsea", "rmsea_ci", "srmr", "problems", "admissible")
    out = {k: fit.get(k) for k in keys}
    out["converged"] = True
    out["scaled"] = fit.get("scaled")
    return out


def invariance(imat, codes, waves_n):
    """The three levels over the waves for one scale: {"levels": [{"level", "fit", "diff", "d_cfi", "d_rmsea",
    "d_srmr", "holds"}], "highest", "means": [{"wave", "est", "se", "z", "p", "d"}] of the scalar model}."""
    items = [f"s{k + 1}" for k in range(len(codes))]
    out, before, highest, means = [], None, None, []
    for level in LEVELS:
        cols, tab = invariance_table(items, waves_n, level)
        same = [[f"{it}_t{t}" for t in range(1, waves_n + 1)] for it in items]
        fit = _safe(imat, cols, tab, same)
        row = {"level": level, "fit": _fit_summary(fit)}
        if before is not None and fit.get("converged") and before["full"].get("converged"):
            prev = before["full"]
            row["diff"] = sem_means.scaled_difference(fit, prev)
            row["d_cfi"] = fit["cfi"] - prev["cfi"] if fit.get("cfi") is not None and prev.get("cfi") is not None else None
            row["d_rmsea"] = fit["rmsea"] - prev["rmsea"] if fit.get("rmsea") is not None and prev.get("rmsea") is not None else None
            row["d_srmr"] = fit["srmr"] - prev["srmr"]
            worse = row["d_cfi"] is not None and row["d_cfi"] <= sem_means.D_CFI
            row["holds"] = not (worse and ((row["d_rmsea"] or 0) >= sem_means.D_RMSEA
                                           or row["d_srmr"] >= sem_means.D_SRMR[level]))
        elif level == "configural":
            row["holds"] = bool(fit.get("converged") and fit.get("admissible"))
        else:
            row["holds"] = None
        if row["holds"] and (level == "configural" or highest == LEVELS[LEVELS.index(level) - 1]):
            highest = level
        if level == "scalar" and fit.get("converged"):
            var1 = sem_table.find(fit, "~~", "F1", "F1")
            sd1 = math.sqrt(var1["est"]) if var1 and var1["est"] > 0 else None
            for t in range(2, waves_n + 1):
                m = sem_table.find(fit, "~1", f"F{t}")
                means.append({"wave": t, "est": m["est"], "se": m["se"], "z": m["z"], "p": m["p"],
                              "d": m["est"] / sd1 if sd1 else None})
        out.append(row)
        before = {**row, "full": fit}
    return {"levels": out, "highest": highest, "means": means}


def _est(fit, op, lhs, rhs=None):
    found = sem_table.find(fit, op, lhs, rhs)
    if found is None:
        return None
    return {k: found.get(k) for k in ("est", "se", "z", "p", "std")}


# --- cross-lagged panel models (E5) --------------------------------------------------------------------------------

def _path_name(kind, v, t):
    return f"w_{v['id']}_t{t}" if kind == "riclpm" else _node(v, t)


def _zero_test(fit, keys):
    """The Wald test that the free rows `keys` are all 0: {"chi2", "df", "p"}."""
    theta, vcov = np.asarray(fit["theta"]), np.asarray(fit["vcov"])
    places = {(r["op"], r["lhs"], r["rhs"]): r["place"] for r in fit["rows"] if r["free"]}
    idx = sorted({places[k] for k in keys if k in places})
    if not idx:
        return None
    d = theta[idx]
    try:
        chi2 = float(d @ np.linalg.solve(vcov[np.ix_(idx, idx)], d))
    except np.linalg.LinAlgError:
        return None
    return {"chi2": chi2, "df": len(idx), "p": sem._chi2_p(chi2, len(idx))}


def cross(variables, x, cols_in, T, kind, equal):
    """Estimate one cross-lagged panel model. variables: [{"id", "name", "label", "items"}], x: the data in the
    columns cols_in (the columns of cross_table). Returns {"kind", "equal", "fit", "paths": [{"from", "to", "auto",
    "intervals": [{"t", "est", "se", "p", "std"}], "est", "se", "p", "std" (equal or two waves: the one path),
    "joint" (else: the test that all its intervals are 0)}], "wald" (are the paths equal over the intervals, from the
    free model), "ri_corr", "ri_var", "latent": [names]}."""
    cols, tab, same = cross_table(variables, T, kind, equal)
    x = x[:, [cols_in.index(c) for c in cols]]
    fit = _safe(x, cols, tab, same)
    out = {"kind": kind, "equal": bool(equal and T >= 3), "fit": _fit_summary(fit),
           "latent": [v["label"] for v in variables if v.get("items")], "names": [v["label"] for v in variables]}
    if not fit.get("converged"):
        return out
    paths = []
    for b in variables:
        for a in variables:
            keys = [("~", _path_name(kind, b, t), _path_name(kind, a, t - 1)) for t in range(2, T + 1)]
            intervals = [{"t": t, **_est(fit, *key)} for t, key in zip(range(2, T + 1), keys)]
            entry = {"from": a["name"], "to": b["name"], "from_label": a["label"], "to_label": b["label"],
                     "auto": a is b, "intervals": intervals}
            if equal or T == 2:
                entry.update({k: intervals[0][k] for k in ("est", "se", "z", "p")})
                entry["std"] = float(np.mean([i["std"] for i in intervals]))
            elif not entry["auto"]:
                entry["joint"] = _zero_test(fit, keys)
            paths.append(entry)
    out["paths"] = paths
    if not equal and T >= 3:
        out["wald"] = sem_table.wald_equal(fit, [[("~", _path_name(kind, b, t), _path_name(kind, a, t - 1))
                                                  for t in range(2, T + 1)] for b in variables for a in variables])
    if kind == "riclpm":
        ids = [(variables[i], variables[j]) for i in range(len(variables)) for j in range(i + 1, len(variables))]
        out["ri_corr"] = [{"a": a["label"], "b": b["label"], **_est(fit, "~~", f"RI_{a['id']}", f"RI_{b['id']}")}
                          for a, b in ids]
        out["ri_var"] = [{"name": v["label"], **_est(fit, "~~", f"RI_{v['id']}", f"RI_{v['id']}")} for v in variables]
    return out


def lagged_hypotheses(survey):
    """[(number, h)] of the hypotheses "Wirkung über die Zeit" in the plan of `survey`."""
    from app import study_plan

    return [(n, h) for n, h in enumerate(study_plan.plan_of(survey)["hypotheses"], 1) if h.get("kind") == "lagged"]


def var_of(code, by_name):
    """The variable a hypothesis names: by its name, or the one subscale of that element; None when there is none or
    several."""
    if code in by_name:
        return by_name[code]
    found = [v for v in by_name.values() if v["code"] == code]
    return found[0] if len(found) == 1 else None


# --- the models as R code (E14) -----------------------------------------------------------------------------------

def _r(name):
    """An R-safe name: letters, digits and _."""
    return re.sub(r"\W", "_", str(name))


def _score(var, t):
    """The column of a variable's value in wave t of the merged wide file: the scale score made by the code, or the
    question's own column (t<n>_<code>)."""
    return _r(f"t{t}_{var['name']}") if var["kind"] == "scale" else f"t{t}_{var['code']}"


def _prep(var, T):
    """The lines that make the scale scores of a variable per wave (the mean of its statements)."""
    if var["kind"] != "scale":
        return []
    return [f"daten${_score(var, t)} <- rowMeans(daten[, c("
            + ", ".join(f'"t{t}_{c}"' for c in var["codes"]) + ")])" for t in range(1, T + 1)]


def _renamed(tab, names):
    return [(op, names.get(lhs, lhs), names.get(rhs, rhs) if rhs is not None else None, value, label)
            for op, lhs, rhs, value, label in tab]


def r_script(tab, names, prep, lang):
    """The R code of one model: the merged wide file of the waves, the scores, the model and its estimate."""
    en = lang == "en"
    head = ("# data: tab Export, \"Merge waves\", wide format (CSV)" if en else
            "# Daten: Reiter Export, „Wellen zusammenführen“, Breitformat (CSV)")
    lines = [head, "library(lavaan)", 'daten <- read.csv("wellen.csv")', *prep, "modell <- '",
             sem_table.lavaan_syntax(_renamed(tab, names)), "'",
             'fit <- lavaan(modell, data = daten, estimator = "MLR", missing = "ml")',
             "summary(fit, fit.measures = TRUE, standardized = TRUE)"]
    return "\n".join(lines) + "\n"


def _r_invariance(var, T, lang):
    items = [f"s{k + 1}" for k in range(len(var["codes"]))]
    _, tab = invariance_table(items, T, "scalar")
    names = {f"s{k + 1}_t{t}": f"t{t}_{c}" for k, c in enumerate(var["codes"]) for t in range(1, T + 1)}
    return r_script(tab, names, [], lang)


def _r_growth(var, times, quadratic, preds, T, lang):
    cols = [f"y_t{t + 1}" for t in range(T)]
    tab = growth_table(cols, times, quadratic, [f"p{k + 1}" for k in range(len(preds))])
    names = {f"y_t{t + 1}": _score(var, t + 1) for t in range(T)}
    names.update({f"p{k + 1}": _score(p, 1) for k, p in enumerate(preds)})
    prep = _prep(var, T) + [line for p in preds for line in _prep(p, 1)]
    return r_script(tab, names, prep, lang)


def _r_cross(vs, by_name, T, kind, equal, lang):
    _, tab, _ = cross_table(vs, T, kind, equal)
    names, prep = {}, []
    for v in vs:
        var = by_name[v["name"]]
        nice = _r(var["name"])
        names[f"RI_{v['id']}"] = f"RI_{nice}"
        for t in range(1, T + 1):
            names[f"w_{v['id']}_t{t}"] = f"w_{nice}_t{t}"
            names[f"F_{v['id']}_t{t}"] = f"F_{nice}_t{t}"
            names[f"{v['id']}_t{t}"] = _score(var, t)
            for j, c in enumerate(var["codes"]):
                names[f"{v['id']}_{j + 1}_t{t}"] = f"t{t}_{c}"
        if not v.get("items"):
            prep += _prep(var, T)
    return r_script(tab, names, prep, lang)


def growth(mat, times, quadratic=False, preds=None):
    """A growth curve of one variable: {"fit", "means": {I, S[, Q]}, "variances", "corr": r(I, S), "predictors":
    [{"name", "I", "S"[, "Q"]}], "times"}."""
    T = mat.shape[1]
    cols = [f"y_t{t + 1}" for t in range(T)]
    names = list((preds or {}).keys())
    x = mat
    if names:
        x = np.column_stack([mat] + [preds[n] for n in names])
    tab = growth_table(cols, times, quadratic, [f"p{k + 1}" for k in range(len(names))])
    fit = _safe(x, cols + [f"p{k + 1}" for k in range(len(names))], tab, [cols])
    factors = ["I", "S"] + (["Q"] if quadratic else [])
    out = {"fit": _fit_summary(fit), "times": list(times), "quadratic": quadratic}
    if not fit.get("converged"):
        return out
    out["means"] = {f: _est(fit, "~1", f) for f in factors}
    out["variances"] = {f: _est(fit, "~~", f, f) for f in factors}
    out["corr"] = _est(fit, "~~", "I", "S")
    out["predictors"] = [{"name": n, **{f: _est(fit, "~", f, f"p{k + 1}") for f in factors}}
                         for k, n in enumerate(names)]
    return out


def describe(mat):
    """[{"n", "mean", "sd"}] of the scores in every wave."""
    out = []
    for w in range(mat.shape[1]):
        v = mat[:, w][~np.isnan(mat[:, w])]
        out.append({"n": int(len(v)), "mean": float(v.mean()) if len(v) else None,
                    "sd": float(v.std(ddof=1)) if len(v) > 1 else None})
    return out


def compute(user, survey):
    """The block: {"waves": [labels], "report", "vars": [{"name", "label", "kind", "describe",
    "invariance" | None, "growth" | None, "notes": [keys]}], "times", "real_time"} or {"error": key}. Here in one go
    (tests); the results page runs numbers() as a job (start)."""
    inp = gather(user, survey)
    return inp if inp.get("error") else numbers(inp)


def gather(user, survey):
    """What the models are computed from, read here with the database: the linked scores and statements of the waves
    as plain lists (no participation numbers, no codes), the chosen variables and the settings; or {"error": key}.
    The input of numbers() and of the job "panel" (app/panel_job.py, Ralf 2026-10-17, thread "Rechenknecht")."""
    rows, missing = usable(user, survey)
    if missing:
        return {"error": "access", "missing": missing}
    if len(rows) < 2:
        return {"error": "waves"}
    st = settings(survey)
    by_name = {c["name"]: c for c in candidates(user, survey, rows)}
    chosen = [by_name[v] for v in st["vars"] if v in by_name]
    # the hypotheses "Wirkung über die Zeit" of the waves' plans: their variables join the cross-lagged model
    pairs, unknown = [], []
    for r in rows:
        for _, h in lagged_hypotheses(r["survey"]):
            a, b = var_of(h.get("iv"), by_name), var_of(h.get("dv"), by_name)
            if a is None or b is None or a is b:
                unknown.append((h.get("iv"), h.get("dv")))
            elif (a["name"], b["name"]) not in pairs:
                pairs.append((a["name"], b["name"]))
    in_cross = list(dict.fromkeys([n for pair in pairs for n in pair] + [c["name"] for c in chosen]))[:MAX_CROSS]
    if not chosen and not pairs:
        return {"error": "vars"}
    extra = [by_name[n] for n in in_cross if n not in {c["name"] for c in chosen}]
    rep, scores, items, weeks = linked(user, survey, rows, chosen + extra, items=True)
    T = len(rows)
    times = list(range(T))
    real = bool(st["real_time"] and weeks is not None and all(b > a for a, b in zip(weeks, weeks[1:])))
    if real:
        times = weeks
    keep = ("name", "label", "kind", "code", "codes")
    return {"waves": [r["label"] for r in rows], "report": rep, "times": times, "real_time": real,
            "real_time_asked": st["real_time"], "settings": st, "lang": _lang(),
            "chosen": [{k: c[k] for k in keep} for c in chosen], "cross": [{k: by_name[n][k] for k in keep} for n in in_cross],
            "pairs": [list(p) for p in pairs], "unknown": [list(u) for u in unknown],
            "scores": {name: _listed(mat) for name, mat in scores.items()},
            "items": {name: _listed(mat) for name, mat in items.items()}}


def _lang():
    from app import lang

    return "en" if lang.english() else "de"


def _listed(mat):
    """A matrix as lists, gaps as None (JSON for the estimation process and the Rechenknecht)."""
    return [[None if math.isnan(v) else float(v) for v in row] for row in np.asarray(mat, dtype=float).tolist()]


def _matrix(rows):
    return np.array([[np.nan if v is None else v for v in row] for row in rows], dtype=float).reshape(len(rows), -1)


def numbers(inp, progress=None):
    """The models of the block from gather()'s input: numbers only, no database and no Flask (it runs in the
    estimation process or on the Rechenknecht too). progress(share) after each model."""
    scores = {name: _matrix(m) for name, m in inp["scores"].items()}
    items = {name: _matrix(m) for name, m in inp["items"].items()}
    st, lang, times = inp["settings"], inp["lang"], inp["times"]
    T = len(inp["waves"])
    chosen, pairs = inp["chosen"], [tuple(p) for p in inp["pairs"]]
    by_name = {c["name"]: c for c in chosen + inp["cross"]}
    in_cross = [c["name"] for c in inp["cross"]]
    out = {"waves": inp["waves"], "report": inp["report"], "times": times, "real_time": inp["real_time"],
           "real_time_asked": inp["real_time_asked"], "vars": []}
    steps, done = len(chosen) + (1 if len(in_cross) >= 2 else 0), 0

    def step():
        nonlocal done
        done += 1
        if progress and steps:
            progress(done / steps)

    for var in chosen:
        mat = scores[var["name"]]
        n = int(np.sum(np.sum(~np.isnan(mat), axis=1) > 0))
        entry = {"name": var["name"], "label": var["label"], "kind": var["kind"], "describe": describe(mat), "n": n,
                 "n_two": int(np.sum(np.sum(~np.isnan(mat), axis=1) >= 2)), "notes": [], "invariance": None,
                 "growth": None}
        if var["name"] in items and len(var["codes"]) >= MIN_ITEMS:
            if T * len(var["codes"]) > MAX_COLUMNS:
                entry["notes"].append("too_many")
            elif n < MIN_N:
                entry["notes"].append("few_invariance")
            else:
                entry["invariance"] = invariance(items[var["name"]], var["codes"], T)
                entry["invariance"]["r"] = _r_invariance(var, T, lang)
        elif var["kind"] == "scale":
            entry["notes"].append("items_invariance")
        if T < 3:
            entry["notes"].append("two_waves")
        elif n < MIN_N:
            entry["notes"].append("few_growth")
        else:
            preds = {}
            others = [o for o in chosen if o["name"] != var["name"]] if st["predictors"] else []
            if others:
                preds = {o["label"]: scores[o["name"]][:, 0] for o in others}
            quadratic = bool(st["quadratic"] and T >= 4)
            entry["growth"] = growth(mat, times, quadratic, preds)
            entry["growth"]["r"] = _r_growth(var, times, quadratic, others, T, lang)
            if st["quadratic"] and T < 4:
                entry["notes"].append("quadratic_four")
        out["vars"].append(entry)
        step()
    inv_of = {e["name"]: e.get("invariance") for e in out["vars"]}
    if len(in_cross) >= 2:
        out["cross"] = _cross_block([by_name[n] for n in in_cross], scores, items, inv_of, st, T, pairs)
        c = out["cross"]
        if c.get("fit"):
            vs = [{"id": f"v{k}", "name": n, "items": len(by_name[n]["codes"]) if by_name[n]["label"] in c["latent"] else 0}
                  for k, n in enumerate(in_cross, 1)]
            c["r"] = _r_cross(vs, by_name, T, c["kind"], c["equal"], lang)
        step()
    out["unknown_hyps"] = [tuple(u) for u in inp["unknown"]]
    return out


def _cross_block(variables, scores, items, inv_of, st, T, pairs):
    """The cross-lagged panel model of the variables (E5): the RI-CLPM from three waves and MIN_RICLPM persons, else
    the CLPM; latent with the tick for scales whose loadings hold over time (metric invariance). Plus the result per
    pair of a hypothesis {"<from>><to>": {"p", "std", "est", "test", "df"}}."""
    notes = []
    vs, data, cols = [], [], []
    for k, var in enumerate(variables, 1):
        inv = inv_of.get(var["name"])
        latent = bool(st["latent"] and var["name"] in items and len(var["codes"]) >= MIN_ITEMS
                      and inv and inv.get("highest") in ("metric", "scalar"))
        if st["latent"] and not latent and var["kind"] == "scale":
            notes.append(("observed", var["label"]))
        v = {"id": f"v{k}", "name": var["name"], "label": var["label"], "items": len(var["codes"]) if latent else 0}
        vs.append(v)
        if latent:
            for t in range(1, T + 1):
                for j in range(v["items"]):
                    cols.append(f"{v['id']}_{j + 1}_t{t}")
                    data.append(items[var["name"]][:, (t - 1) * v["items"] + j])
        else:
            for t in range(1, T + 1):
                cols.append(f"{v['id']}_t{t}")
                data.append(scores[var["name"]][:, t - 1])
    x = np.column_stack(data)
    n = int(np.sum(np.any(~np.isnan(x), axis=1)))
    out = {"n": n, "notes": notes, "pairs": pairs}
    if n < MIN_N:
        out["error"] = "few"
        return out
    kind = "riclpm" if T >= 3 and n >= MIN_RICLPM else "clpm"
    if T >= 3 and kind == "clpm":
        notes.append(("riclpm_few", MIN_RICLPM))
    out.update(cross(vs, x, cols, T, kind, bool(st["equal"])))
    found = {}
    for path in out.get("paths") or ():
        if path["auto"] or (path["from"], path["to"]) not in pairs:
            continue
        if path.get("p") is not None:
            found[f"{path['from']}>{path['to']}"] = {"p": path["p"], "std": path["std"], "est": path["est"], "test": "z"}
        elif path.get("joint"):
            found[f"{path['from']}>{path['to']}"] = {
                "p": path["joint"]["p"], "df": path["joint"]["df"], "test": "joint",
                "std": float(np.mean([i["std"] for i in path["intervals"]]))}
    out["hyps"] = found
    return out


# --- the page -----------------------------------------------------------------------------------------------------

def page(user, survey):
    """What the results page shows: None without waves or a chosen variable; else {"missing": [labels]} when a wave
    cannot be read, or {"result": kept or None, "at"}."""
    from app import results_store, waves

    if not waves.of(survey) or not (settings(survey)["vars"] or lagged_hypotheses(survey)):
        return None
    rows, missing = usable(user, survey)
    if missing:
        return {"missing": missing}
    if len(rows) < 2:
        return None
    k = key(user, survey)
    kept = results_store.load(survey, k, None, name="panel")
    job = None
    if kept is None:
        kept, job = _taken_over(user, survey, k)
    return {"result": kept[0] if kept else None, "at": kept[1] if kept else None, "job": job, "min_n": MIN_N,
            "max_columns": MAX_COLUMNS, "src": {"bollen": BOLLEN_CURRAN, "widaman": WIDAMAN, "chen": sem_means.CHEN,
                                                "enders": sem_means.ENDERS, "yuan": sem_means.YUAN_BENTLER,
                                                "hamaker": HAMAKER, "mulder_hamaker": MULDER_HAMAKER, "mulder": MULDER}}


def _paths(survey, app=None):
    import os

    from flask import current_app

    folder = os.path.join((app or current_app).config["UPLOAD_FOLDER"], "hb", survey.uid)  # removed with the survey
    # "panel." in front: stopped and removed with the other jobs computed from the answers (jobs.BOOTSTRAP_FILES)
    return os.path.join(folder, "panel.json"), os.path.join(folder, "panel.run.json")


def state(survey, k):
    """{"state": "none" | "running" | "error", "progress", "queued", "message"} of the job for the fingerprint k."""
    from app import jobs

    paths = _paths(survey)
    marker = jobs.read(paths)
    if marker and marker.get("key") == k:
        if marker.get("state") == "running":
            marker = jobs.settle(paths, marker) or marker
        if marker.get("state") == "running":
            return {"state": "running", "progress": marker.get("progress") or 0.0, "queued": bool(marker.get("queued"))}
        if marker.get("state") in ("error", "aborted"):
            return {"state": "error", "message": marker.get("message", "")}
    return {"state": "none"}


def _taken_over(user, survey, k):
    """((result, at) or None, job state or None): a finished job of this fingerprint goes into the kept results
    (results_store, as the button did before) and its file away; else the state of a running or failed one."""
    from app import maxdiff_hb, results_store

    paths = _paths(survey)
    stored = maxdiff_hb._read(paths[0])
    if stored and stored.get("key") == k and isinstance(stored.get("result"), dict):
        results_store.save(survey, k, stored["result"], None, name="panel", basis=lambda: key(user, survey))
        maxdiff_hb._remove(paths[0])
        return results_store.load(survey, k, None, name="panel"), None
    found = state(survey, k)
    return None, (found if found["state"] != "none" else None)


def start(user, survey, background=True):
    """"Längsschnitt rechnen": read the waves here, compute the models as the job "panel" (app/panel_job.py) in the
    estimation process or on the Rechenknecht. "started", "running" (one runs already), or "kept" (nothing to
    estimate: the note why is kept at once)."""
    from flask import current_app

    from app import jobs, results_store

    k = key(user, survey)
    inp = gather(user, survey)
    if inp.get("error"):
        results_store.save(survey, k, inp, None, name="panel", basis=lambda: key(user, survey))
        return "kept"
    inp["key"] = k
    app = current_app._get_current_object()
    if not jobs.start(app, "panel", _paths(survey, app), inp, {}, background, name=f"panel-{survey.id}"):
        return "running"
    return "started"


def apply(hyps, plan_hyps, found, lang="de"):
    """Fill the results of the hypotheses "Wirkung über die Zeit" (app/hypotheses.evaluate) from the kept block:
    the cross-lagged path from what explains to what is explained, judged in the expected direction; without a kept
    block a note where it is computed."""
    from app import sem_model

    if not hyps:
        return hyps
    result = (found or {}).get("result") or {}
    block = result.get("cross") or {}
    by_name = {}
    for v in result.get("vars") or ():
        by_name[v["name"]] = v
    for out, h in zip(hyps, plan_hyps):
        if h.get("kind") != "lagged":
            continue
        out.update(analysis="panel", verdict="offen", sentence="", notes=[], groups=[])
        out.pop("sensitivity", None)
        key = None
        for pair in block.get("pairs") or ():
            a, b = pair
            if (a == h.get("iv") or a.split(":")[0] == h.get("iv")) and (b == h.get("dv") or b.split(":")[0] == h.get("dv")):
                key = f"{a}>{b}"
        entry = (block.get("hyps") or {}).get(key) if key else None
        if not result:
            out["notes"].append(de_en("Geprüft im Block „Längsschnitt über die Wellen“ (dort „Längsschnitt rechnen“).",
                                      "Tested in the block “Longitudinal analysis over the waves” (there “Compute "
                                      "longitudinal analysis”)."))
            continue
        if entry is None:
            out["notes"].append(de_en("Kein Ergebnis: Beide Größen müssen in allen Wellen mit denselben Aussagen stehen, "
                                      "und das Modell braucht mindestens {k} verknüpfte Personen.",
                                      "No result: both variables must stand in all waves with the same statements, and "
                                      "the model needs at least {k} linked people.", k=MIN_N))
            continue
        model = "RI-CLPM" if block.get("kind") == "riclpm" else "CLPM"
        out["n"] = block.get("n")
        out["p"] = entry["p"]
        out["effect"] = ("β", entry["std"])
        admissible = (block.get("fit") or {}).get("admissible", False)
        if admissible:
            out["verdict"] = sem_model._verdict(entry["p"], entry["std"], h.get("direction", ""))
        else:
            out["notes"].append(de_en("Kein Urteil: Die Lösung des Längsschnittmodells ist nicht zulässig.",
                                      "No verdict: the solution of the longitudinal model is not admissible."))
        if entry["test"] == "z":
            label = "cross-lagged path" if lang == "en" else "kreuzverzögerter Pfad"
            out["sentence"] = f"{label} β = {sem_model._num(entry['std'], lang)}, {sem_model._p(entry['p'], lang)} ({model})"
        else:
            out["sentence"] = ((f"cross-lagged paths, mean β = {sem_model._num(entry['std'], lang)}, joint test "
                                f"χ²({entry['df']}), {sem_model._p(entry['p'], lang)} ({model})") if lang == "en" else
                               (f"kreuzverzögerte Pfade, mittleres β = {sem_model._num(entry['std'], lang)}, gemeinsamer "
                                f"Test χ²({entry['df']}), {sem_model._p(entry['p'], lang)} ({model})"))
    return hyps


# --- the power of the hypotheses over time by simulation (E11) ---------------------------------------------------

POWER_REPS = 500


def _power_paths(survey, app=None):
    import os

    from flask import current_app

    folder = os.path.join((app or current_app).config["UPLOAD_FOLDER"], "hb", survey.uid)
    # "sem_power." in front: the files of the job stop and go like those of the model's simulation (jobs.BOOTSTRAP_FILES)
    return os.path.join(folder, "sem_power.panel.json"), os.path.join(folder, "sem_power.panel.run.json")


def power_input(survey):
    """What the simulation needs: the variables of the hypotheses "Wirkung über die Zeit" with an expected effect,
    the number of waves (of the list of waves, else 3), the planned N; None without such a hypothesis or an N."""
    from app import sem_model, waves

    n = sem_model.power_n(survey)
    judged, names, betas = [], [], []
    for number, h in lagged_hypotheses(survey):
        if h.get("effect") is None or not h.get("iv") or not h.get("dv") or h["iv"] == h["dv"]:
            continue
        for code in (h["iv"], h["dv"]):
            if code not in names:
                names.append(code)
        betas.append([h["iv"], h["dv"], float(h["effect"]) * (-1 if h.get("direction") == "neg" else 1)])
        judged.append({"number": number, "from": h["iv"], "to": h["dv"], "direction": h.get("direction") or ""})
    if not judged or not n or len(names) > MAX_CROSS:
        return None
    count = len(waves.of(survey))
    body = {"panel": {"vars": names, "waves": count if count >= 2 else 3, "betas": betas, "hyps": judged},
            "n": int(n), "reps": POWER_REPS, "seed": 1}
    body["key"] = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:24]
    return body


def power_state(survey):
    """As sem_model.power_state, for the hypotheses over time: {"state", "progress", "n", "waves", "result"}."""
    from app import jobs, maxdiff_hb, waves

    inp = power_input(survey)
    if inp is None:
        return None
    paths = _power_paths(survey)
    base = {"n": inp["n"], "waves": inp["panel"]["waves"], "assumed": len(waves.of(survey)) < 2}
    from app import panel_power

    # effects too large for variance 1 are said, not simulated as another effect (audit 2026-10-21, E5)
    big = panel_power.too_large(inp["panel"]["vars"], inp["panel"]["waves"], inp["panel"]["betas"])
    if big:
        return {"state": "too_large", "vars": big, **base}
    marker = jobs.read(paths)
    if marker and marker.get("key") == inp["key"]:
        if marker.get("state") == "running":
            marker = jobs.settle(paths, marker) or marker
        if marker.get("state") == "running":
            return {"state": "running", "progress": marker.get("progress") or 0.0, **base}
        if marker.get("state") in ("error", "aborted"):
            return {"state": "error", **base}
    stored = maxdiff_hb._read(paths[0])
    if stored and stored.get("key") == inp["key"] and stored.get("power"):
        return {"state": "done", **base, "result": {"n": stored["n"], "reps": stored["reps"], "failed": stored["failed"],
                                                    "kind": stored.get("kind"),
                                                    "power": {int(k): v for k, v in stored["power"]}}}
    return {"state": "none", **base}


def start_power(survey, background=True):
    """"started", "running", "current", "nothing" (as sem_model.start_power) or "too_large" (the expected effects break
    the planning assumptions, panel_power.too_large)."""
    from flask import current_app

    from app import jobs

    inp = power_input(survey)
    if inp is None:
        return "nothing"
    state = power_state(survey)
    if state and state["state"] == "done":
        return "current"
    if state and state["state"] == "too_large":
        return "too_large"
    app = current_app._get_current_object()
    if not jobs.start(app, "sem_power", _power_paths(survey, app), inp, {}, background, name=f"panel-power-{survey.id}"):
        return "running"
    return "started"


def sources(found):
    """[(heading, [citations])] for the list at the end of the results page."""
    if not found or not found.get("result") or found["result"].get("error"):
        return []
    return [(de_en("Längsschnitt über die Wellen", "Longitudinal analysis over the waves"), list(SOURCES))]
