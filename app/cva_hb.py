"""Individual part-worths of the ratings-based conjoint by hierarchical Bayes for a linear model (HB regression;
concept aca-cva-konzept.md, stage 2, Ralf 2026-10-17: "mach weiter").

Model (Lenk et al., 1996; Rossi et al., 2005, ch. 3): per person y_i = X_i b_i + e_i, e_i ~ N(0, s_i^2 I), with the
rows of cva_analysis.rows_of (effects coding, a constant; pairs: the difference of the two offers). The answers are
centred on the middle of the scale, so the constant of an average person is near 0. Upper level b_i ~ N(mu, Sigma),
mu ~ N(0, MU_PRIOR_SD^2 P), Sigma ~ IW(k + 5, 4 P) (prior mean P), s_i^2 ~ IG(2, u^2) with u = range of the scale / 6
(a sixth of the scale as the typical error). P: per attribute (I - 11'/L) u^2 for its L - 1 parameters (independent
levels with sd u, as conjoint_hb._prior on the logit scale), (range / 2)^2 for the constant. All steps are conjugate
(Gibbs): b_i, s_i^2, mu, Sigma in turn.

Unlike the regression per person (app/cva_analysis.py) a person needs no more answers than parameters: few answers
borrow from the others. Per person: zero-centred part-worths (posterior mean, in points of the answer scale), the
constant (in points of the scale, the middle added back), importances from the posterior means, R² of the posterior
means. Means over persons with the 95 % posterior interval of the mean (mean over persons at every kept draw).
Convergence as for the choice models (app/hb_convergence.py: split-R-hat and ESS of mu, the log-likelihood and the
shown means; Geweke-type check of the halves).

Storage, background run, status and staleness as for MaxDiff (app/maxdiff_hb.py: same files and settings).
"""
import hashlib
import json
import math
import os
import time
from datetime import datetime

import numpy as np

from app import hb, hb_convergence, maxdiff_hb
from app.lang import de_en
from app.maxdiff_hb import load, pool_table, running, settings  # noqa: F401 (shared)

FORMAT = 1
MU_PRIOR_SD = 5.0
LENK = ("Lenk, P. J., DeSarbo, W. S., Green, P. E., & Young, M. R. (1996). Hierarchical Bayes conjoint analysis: Recovery "
        "of partworth heterogeneity from reduced experimental designs. Marketing Science, 15(2), 173–191. "
        "https://doi.org/10.1287/mksc.15.2.173")


# --- input ------------------------------------------------------------------------------------------------------

def model_input(item, table, config=None):
    """Everything the estimation needs and the key of the included answers; None with fewer than hb.MIN_PERSONS persons
    with an answered task or without a current design."""
    from app import cva_analysis
    from app.choice_model import responses_for

    kind = item.kind
    if not kind.design_current(item):
        return None
    versions = item.config["design"]["versions"]
    persons = []
    for r in responses_for(item, table):
        rows = cva_analysis.rows_of(item, (r.answers or {}).get(str(item.id)), versions)
        if rows:
            persons.append({"pid": r.pid, "x": [x for x, _ in rows], "y": [y for _, y in rows]})
    if len(persons) < hb.MIN_PERSONS:
        return None
    lo, hi = kind.scale_range(item)
    cfg = config or settings()
    data = {"format": FORMAT, "type": "cva", "design": item.config["design"].get("sig"), "form": kind.form(item),
            "scale": [lo, hi], "levels": kind.level_counts(item), "persons": persons}
    data_key = hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()
    key = hashlib.sha1(json.dumps({**data, "settings": cfg}, sort_keys=True).encode()).hexdigest()
    return {**data, "key": key, "data_key": data_key, "settings": cfg}


def _prior(levels, unit, span):
    p = 1 + sum(n - 1 for n in levels)
    P = np.zeros((p, p))
    P[0, 0] = (span / 2) ** 2
    off = 1
    for n in levels:
        P[off:off + n - 1, off:off + n - 1] = (np.eye(n - 1) - 1.0 / n) * unit ** 2
        off += n - 1
    return P


def _effects_matrix(levels):
    """M with part-worths = beta[1:] @ M.T: per attribute the L levels from its L - 1 effects."""
    rows, off = [], 0
    k = sum(n - 1 for n in levels)
    for n in levels:
        for lv in range(n):
            r = np.zeros(k)
            if lv == n - 1:
                r[off:off + n - 1] = -1.0
            else:
                r[off + lv] = 1.0
            rows.append(r)
        off += n - 1
    return np.array(rows)


def run(inp, progress=None):
    """Gibbs sampler of the module text; returns the JSON-ready result (without pool and item)."""
    started = time.monotonic()
    cfg = inp["settings"]
    burn, draws, thin, seed = cfg["burn"], cfg["draws"], cfg["thin"], cfg["seed"]
    rng = np.random.default_rng(seed)
    levels = inp["levels"]
    lo, hi = inp["scale"]
    span = float(hi - lo)
    unit = span / 6.0
    middle = 0.0 if inp["form"] == "pairs" else (lo + hi) / 2.0
    persons = inp["persons"]
    n = len(persons)
    X = [np.array(p["x"], dtype=float) for p in persons]
    Y = [np.array(p["y"], dtype=float) - (0.0 if inp["form"] == "pairs" else middle) for p in persons]
    k = X[0].shape[1]
    XtX = np.array([x.T @ x for x in X])
    Xty = np.array([x.T @ y for x, y in zip(X, Y)])
    yty = np.array([y @ y for y in Y])
    counts = np.array([len(y) for y in Y], dtype=float)
    P = _prior(levels, unit, span)
    df0 = k + 5
    S0 = (df0 - k - 1) * P
    mu_prec = np.linalg.inv(MU_PRIOR_SD ** 2 * P)
    a0, b0 = 2.0, unit ** 2
    mu = np.zeros(k)
    Sigma = P.copy()
    B = np.tile(mu, (n, 1))
    s2 = np.full(n, unit ** 2)
    M = _effects_matrix(levels)
    spans, start = [], 0
    for a, count in enumerate(levels):
        spans.append((a, start, count))
        start += count
    total = burn + draws
    report = max(1, total // 50)
    keep_every = max(1, (draws // thin) // hb.KEEP_DRAWS)
    sums, s2_sums = np.zeros((n, k)), np.zeros(n)
    mus, lls, summaries, kept_draws = [], [], [], []
    for it in range(total):
        s_inv = np.linalg.inv(Sigma)
        prec = XtX / s2[:, None, None] + s_inv
        rhs = Xty / s2[:, None] + s_inv @ mu
        L = np.linalg.cholesky(prec)
        mean = np.linalg.solve(prec, rhs[:, :, None])[:, :, 0]
        z = rng.standard_normal((n, k))
        B = mean + np.linalg.solve(np.transpose(L, (0, 2, 1)), z[:, :, None])[:, :, 0]
        sse = yty - 2 * (B * Xty).sum(axis=1) + np.einsum("ni,nij,nj->n", B, XtX, B)
        sse = np.maximum(sse, 0.0)
        s2 = 1.0 / rng.gamma(a0 + counts / 2, 1.0 / (b0 + sse / 2))
        mu = hb._draw_mu(rng, B.mean(axis=0), Sigma, n, mu_prec)
        D = B - mu
        Sigma = hb._inv_wishart(rng, df0 + n, S0 + D.T @ D)
        if it >= burn and (it - burn + 1) % thin == 0:
            ll = float((-0.5 * counts * np.log(2 * math.pi * s2) - sse / (2 * s2)).sum())
            sums += B
            s2_sums += s2
            mus.append(mu.copy())
            lls.append(ll)
            summaries.append((B[:, 1:] @ M.T).mean(axis=0))
            if (len(mus) - 1) % keep_every == 0 and len(kept_draws) < hb.KEEP_DRAWS:
                kept_draws.append(B.copy())
        if progress and (it + 1) % report == 0:
            progress((it + 1) / total)
    n_kept = len(mus)
    beta = sums / max(n_kept, 1)
    mus, lls = np.array(mus), np.array(lls)
    S = np.array(summaries) if summaries else None
    z_ll = hb.geweke(lls)
    z_mu = [hb.geweke(mus[:, j]) for j in range(k)] if n_kept else []
    flagged = sum(1 for z in z_mu if z is not None and abs(z) > hb.GEWEKE_LIMIT)
    checked = z_ll is not None
    geweke_ok = checked and abs(z_ll) <= hb.GEWEKE_LIMIT and flagged <= 1
    diag = hb_convergence.diagnose(mus, lls, geweke_ok=geweke_ok, shown=S)
    util = beta[:, 1:] @ M.T
    ranges = np.column_stack([util[:, s:s + c].max(axis=1) - util[:, s:s + c].min(axis=1) for _, s, c in spans])
    total_range = ranges.sum(axis=1, keepdims=True)
    importance = np.divide(ranges, total_range, out=np.zeros_like(ranges), where=total_range > 0)
    r2 = []
    for x, y, b in zip(X, Y, beta):
        sst = float(((y - y.mean()) ** 2).sum())
        r2.append(None if sst == 0 else max(0.0, 1 - float(((y - x @ b) ** 2).sum()) / sst))
    lo_s, hi_s = np.percentile(S, [2.5, 97.5], axis=0) if S is not None and len(S) else (None, None)
    half = n_kept // 2

    def rnd(x, d=4):
        return None if x is None else np.round(np.asarray(x, dtype=float), d).tolist()

    return {"format": FORMAT, "type": "cva", "key": inp["key"], "data_key": inp.get("data_key"), "settings": cfg,
            "levels": levels, "scale": [lo, hi], "form": inp["form"], "middle": middle,
            "spans": [[a, s, c] for a, s, c in spans], "pids": [p["pid"] for p in persons],
            "util": rnd(util), "constant": rnd(beta[:, 0] + middle), "importance": rnd(importance),
            "r2": [None if v is None else round(v, 4) for v in r2], "sigma": rnd(np.sqrt(s2_sums / max(n_kept, 1))),
            "util_lo": rnd(lo_s), "util_hi": rnd(hi_s), "mu": rnd(mus.mean(axis=0) if n_kept else mu),
            "sigma_util": round(float(util.var(axis=0).mean()), 4),
            "converged": diag["diag_ok"], "geweke_ok": geweke_ok, "checked": checked, "geweke_ll": z_ll,
            "geweke_mu_flagged": flagged, "ll_halves": [float(lls[:half].mean()), float(lls[-half:].mean())] if half else None,
            "kept": n_kept, "sets": int(counts.sum()), "seconds": round(time.monotonic() - started, 1),
            "acceptance": None, **diag,
            "draws": hb.pack_draws(np.array(kept_draws) if kept_draws else None)}


def draw_parameters(result):
    """Per kept draw and person the zero-centred part-worths (draws x persons x levels) for the market simulator, in
    points of the answer scale; without stored draws the posterior means as one draw."""
    B = hb.unpack_draws(result.get("draws"))
    if B is None:
        return np.array(result["util"], dtype=float)[None]
    return B[:, :, 1:] @ _effects_matrix(result["levels"]).T


# --- start, current result ---------------------------------------------------------------------------------------

def _stored(item):
    stored = load(item)
    return stored if stored and stored.get("format") == FORMAT and stored.get("type") == "cva" else None


def start(item, table, pool, background=True, config=None):
    """As maxdiff_hb.start: "started", "running", "current" or "nodata"."""
    from flask import current_app

    from app import jobs

    app = current_app._get_current_object()
    inp = model_input(item, table, config)
    if inp is None:
        return "nodata"
    stored = load(item)
    if stored and stored.get("key") == inp["key"]:
        maxdiff_hb.restamp(maxdiff_hb._paths(item)[0], stored, pool)
        return "current"
    if not jobs.start(app, "cva_hb", maxdiff_hb._paths(item), inp, pool, background, name=f"hb-item-{item.id}"):
        return "running"
    return "started"


def current(item):
    """The stored result if it still fits the data of its own data pool, else None (cached per request)."""
    from flask import g, has_request_context

    path = maxdiff_hb._paths(item)[0]
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return None
    cache = g.setdefault("_hb_current", {}) if has_request_context() else {}
    if (item.id, stamp) in cache:
        return cache[(item.id, stamp)]
    result = maxdiff_hb._read(path)
    if result and not (result.get("format") == FORMAT and result.get("type") == "cva" and maxdiff_hb.fits(
            item, path, result, lambda table: model_input(item, table, result.get("settings")))):
        result = None
    cache[(item.id, stamp)] = result
    return result


# --- results page ------------------------------------------------------------------------------------------------

def view(item, table, lang):
    stored = _stored(item)
    inp = model_input(item, table, stored.get("settings") if stored else None)
    out = {"available": inp is not None, "marker": running(item), "result": None, "state": None, "stale_created": None,
           "min_persons": hb.MIN_PERSONS, "input_key": inp["key"] if inp else None,
           "data_key": inp["data_key"] if inp else None, "seen_key": stored.get("key") if stored else None}
    return _state(item, out, stored, lang)


def revisit(item, before, lang):
    stored = _stored(item)
    out = {**before, "marker": running(item), "result": None, "state": None, "stale_created": None,
           "unknown": bool(stored and stored.get("key") not in (before.get("seen_key"), before.get("input_key")))}
    if out["unknown"] and before.get("data_key") and stored.get("data_key") == before["data_key"]:
        out["unknown"], out["input_key"] = False, stored["key"]
    return _state(item, out, stored, lang)


def _state(item, out, stored, lang):
    if stored:
        out["state"] = "current" if out.get("input_key") and stored["key"] == out["input_key"] else "stale"
        if out["state"] == "current":
            out["result"] = summary(item, stored, lang)
        else:
            out["stale_created"] = maxdiff_hb.created_local(stored)
    marker = out["marker"]
    if marker and marker["state"] == "running":
        out["state"] = "running"
    return out


def summary(item, res, lang):
    """The view model of a stored result: importances and part-worths (means over persons, posterior interval of the
    mean), R² per person, convergence."""
    from app.choice_model import charts
    from app.items.base import plain
    from app.timeutil import to_local

    attrs = item.kind.attributes(item, lang)
    util = np.array(res["util"], dtype=float)
    imp = np.array(res["importance"], dtype=float)
    rows = []
    for j, (a, start, count) in enumerate(res["spans"]):
        block = util[:, start:start + count]
        levels = []
        for lv in range(count):
            lo = None if res["util_lo"] is None else res["util_lo"][start + lv]
            hi = None if res["util_hi"] is None else res["util_hi"][start + lv]
            levels.append({"label": plain(attrs[a]["level_labels"][lv]), "beta": float(block[:, lv].mean()),
                           "se": None if lo is None else (hi - lo) / 3.92, "lo": lo, "hi": hi,
                           "median": float(np.median(block[:, lv]))})
        rows.append({"code": attrs[a]["code"], "label": plain(attrs[a]["label"]), "levels": levels,
                     "ordered": item.kind.levels_ordered(item, attrs[a]), "importance": float(imp[:, j].mean()),
                     "importance_sd": float(imp[:, j].std())})
    r2 = np.array([v for v in res["r2"] if v is not None], dtype=float)
    n = len(util)
    warnings = []
    if hb_convergence.warning(res):
        warnings.append(hb_convergence.warning(res))
    if n < 30:
        warnings.append(de_en("Nur {n} Personen: Die obere Ebene (Mittelwerte, Streuung) ist unsicher.",
                              "Only {n} people: the upper level (means, spread) is uncertain.", n=n))
    from app.cva_analysis import R2_LOW

    return {"persons": n, "attributes": rows, "charts": charts({"attributes": rows}),
            "r2": {"mean": float(r2.mean()) if len(r2) else None, "median": float(np.median(r2)) if len(r2) else None,
                   "low": int((r2 < R2_LOW).sum()), "limit": R2_LOW},
            "constant": float(np.mean(res["constant"])), "sigma_mean": float(np.mean(res["sigma"])),
            "settings": res["settings"], "kept": res["kept"], "sets": res["sets"], "seconds": res["seconds"],
            "created": to_local(datetime.fromisoformat(res["created"])), "pool": res.get("pool") or {},
            "warnings": warnings, "convergence": hb_convergence.view(res),
            "convergence_warning": hb_convergence.warning(res)}


# --- export ------------------------------------------------------------------------------------------------------

def _level_columns(item, result):
    attrs = item.kind.attributes(item)
    out = []
    for a, start, count in result["spans"]:
        out += [(f"{item.code}_hb_{attrs[a]['code']}_{lv + 1}", attrs[a], lv, start + lv) for lv in range(count)]
    return out


def export_columns(item, result):
    attrs = item.kind.attributes(item)
    code = item.code
    cols = [(col, f"HB-Teilnutzen (Punkte der Skala, nullzentriert je Merkmal): {attr['label']} = {attr['level_labels'][lv]}")
            for col, attr, lv, _ in _level_columns(item, result)]
    cols.append((f"{code}_hb_constant", "HB-Konstante (Punkte der Skala)"))
    cols += [(f"{code}_hb_imp_{attrs[a]['code']}", f"HB-Wichtigkeit: {attrs[a]['label']}") for a, _, _ in result["spans"]]
    cols.append((f"{code}_hb_r2", "HB: R² der Person (Posterior-Mittel)"))
    return cols


def export_values(item, result):
    attrs = item.kind.attributes(item)
    code = item.code
    levels = _level_columns(item, result)
    out = {}
    for n, pid in enumerate(result["pids"]):
        row = {col: result["util"][n][pos] for col, _, _, pos in levels}
        row[f"{code}_hb_constant"] = result["constant"][n]
        for j, (a, _, _) in enumerate(result["spans"]):
            row[f"{code}_hb_imp_{attrs[a]['code']}"] = result["importance"][n][j]
        row[f"{code}_hb_r2"] = result["r2"][n]
        out[pid] = row
    return out


def describe(result, survey=None, lang=None):
    from app.items.base import reader
    from app.timeutil import to_local

    s = result["settings"]
    when = to_local(datetime.fromisoformat(result["created"]))
    basis = maxdiff_hb.pool_text(result.get("pool") or {}, survey, lang)
    if reader(lang) == "en":
        return (f"Hierarchical Bayes regression (normal linear model per person, effects coding), estimated on "
                f"{when:%Y-%m-%d %H:%M}; {len(result['pids'])} people ({basis}); {s['burn']} burn-in, {s['draws']} "
                f"iterations, thinning {s['thin']}, seed {s['seed']}; posterior mean per person"
                f"{hb_convergence.describe(result, 'en')}")
    return (f"Hierarchical-Bayes-Regression (lineares Modell je Person, Effektkodierung), geschätzt am "
            f"{when:%d.%m.%Y %H:%M}; {len(result['pids'])} Personen ({basis}); {s['burn']} Burn-in, {s['draws']} "
            f"Iterationen, Thinning {s['thin']}, Seed {s['seed']}; Posterior-Mittel je Person"
            f"{hb_convergence.describe(result, 'de')}")


def codebook_info(item, column, result, lang=None):
    from app.items.base import in_lang

    note = describe(result, item.survey, lang)
    code = item.code
    if column == f"{code}_hb_r2":
        return "Zahl", in_lang(lang, "0 bis 1", "0 to 1"), [note, in_lang(
            lang, "Anteil der Streuung der eigenen Antworten, den die Posterior-Mittel der Person erklären",
            "share of the variance of the person's own answers that their posterior means explain")]
    if column.startswith(f"{code}_hb_imp_"):
        return "Zahl", in_lang(lang, "0 bis 1, Summe 1 über die Merkmale", "0 to 1, sum 1 over the attributes"), [note]
    if column == f"{code}_hb_constant":
        return "Zahl", in_lang(lang, "Punkte der Antwortskala", "points of the answer scale"), [note]
    return "Zahl", in_lang(lang, "Punkte der Antwortskala, Mittel der Stufen des Merkmals = 0",
                           "points of the answer scale, mean of the levels of the attribute = 0"), [note]
