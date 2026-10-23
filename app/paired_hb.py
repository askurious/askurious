"""Individual values of a paired comparison by hierarchical Bayes (app/hb.py), stored per item (concept
paarvergleich-konzept.md, E11).

The same choice sets as the aggregate model (app/paired_analysis.py): every answered pair a choice among its categories
in the adjacent-categories model, with the position effect and the thresholds of "gleich" and of the weaker steps as
further parameters of every person. The last compared option is the reference; the prior covariance is I + 11' for the
differences of the options to it (iid options with variance 1, as for MaxDiff) and 1 for the other parameters.

Per person: zero-centred utilities (posterior mean) and values 100 · exp(u_i) / sum exp(u), computed at every kept
draw and averaged; means over persons afterwards, with the 95 % posterior interval of the mean. RLH over the person's
pairs; chance is one over the number of categories.

Storage, background run, status, staleness and the view of the results page are those of MaxDiff
(app/maxdiff_hb.py: same files, settings and summary); only the input, the model and the export are its own.
"""
import hashlib
import json

import numpy as np

from app import hb, hb_convergence, maxdiff_hb
from app.lang import de_en
from app.maxdiff_hb import (FORMAT, _matrices, created_local, load, pool_table, restamp, running, settings,  # noqa: F401
                            stamp)
from app.units import fixed


def _prepare(item, table, config=None):
    """(model_input or None, the note on options that fall apart into groups never compared or None)."""
    from app import paired_analysis as pa

    kind = item.kind
    n = kind.n_items(item)
    cats = kind.categories(item)
    rows = [(pid, v, answered) for pid, v, answered in pa.persons_from_table(item, table) if answered]
    blocks = pa.components(n, rows)
    if len(blocks) > 1:
        groups = " | ".join(", ".join(str(i + 1) for i in g) for g in blocks)
        return None, de_en("Die Optionen zerfallen in {n} Gruppen, die nie miteinander verglichen wurden ({groups}).",
                           "The options fall apart into {n} groups that were never compared with each other ({groups}).",
                           n=len(blocks), groups=groups)
    # the compared options: those in some answered pair, in one group here (the same as parameters()[0], without
    # counting all answers once more; audit 2026-10-10, P8)
    est = blocks[0] if blocks else []
    if len(est) < 2 or len(rows) < hb.MIN_PERSONS:
        return None, None
    cfg = config or settings()
    data = {"format": FORMAT, "type": "paired", "design": (item.config.get("design") or {}).get("sig"), "n": n,
            "cats": cats, "persons": [{"pid": pid, "a": [list(x) for x in answered]} for pid, _, answered in rows]}
    digest = hashlib.sha1(json.dumps({**data, "settings": cfg}, sort_keys=True).encode()).hexdigest()
    data_key = hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return {"key": digest, "data_key": data_key, "n_items": n, "cats": cats, "persons": data["persons"],
            "settings": cfg}, None


def model_input(item, table, config=None):
    return _prepare(item, table, config)[0]


def run(inp, progress=None):
    """Estimate from model_input(); the result has the keys of a MaxDiff result (maxdiff_hb.run), so its storage and
    summary serve it."""
    from app import paired_analysis as pa

    n, cats = inp["n_items"], inp["cats"]
    persons = [(p["pid"], 0, [tuple(x) for x in p["a"]]) for p in inp["persons"]]
    counted = pa.counts(n, persons)
    sets, est, k, _, _ = pa.choice_sets(n, persons, cats, counted[0])
    by_person = {}
    for s in sets:
        by_person.setdefault(s.person, []).append(s)
    # the persons with at least one valid answer, in one order for their sets and their pids: a person without one
    # would otherwise shift the pids of everybody after them (code review 2026-10-15, C7)
    order = sorted(by_person)
    person_sets = [by_person[p] for p in order]
    m = len(est)
    M, _ = _matrices(m, k, None)

    def values(B):
        return pa.worths(B @ M.T)

    prior = np.eye(k)
    prior[:m - 1, :m - 1] += 1.0  # differences to the reference of iid options with variance 1
    cfg = inp["settings"]
    r = hb.estimate(person_sets, k, burn=cfg["burn"], draws=cfg["draws"], thin=cfg["thin"], seed=cfg["seed"],
                    prior_cov=prior, summary=lambda B: values(B).mean(axis=0), person_stat=values, progress=progress,
                    spread=lambda S: [np.mean(np.diag(M @ S @ M.T))])
    beta = r["beta"]
    util = beta @ M.T
    diag = hb_convergence.diagnose(r["mu_trace"] @ M.T, r["ll_trace"], geweke_ok=r["converged"], shown=r["summaries"])
    data = hb.Data(person_sets, k)
    rlh = hb.rlh(data, beta)
    chance = np.exp(data.chance_loglik() / data.sets_per_person)
    S = r["summaries"]
    lo, hi = np.percentile(S, [2.5, 97.5], axis=0) if S is not None and len(S) else (None, None)
    comp, win, loss = counted[:3]
    separated = [int(i) for i in est if comp[i] and (win[i] == comp[i] or loss[i] == comp[i])]

    def rnd(x, d=4):
        return None if x is None else np.round(np.asarray(x, dtype=float), d).tolist()

    return {"format": FORMAT, "type": "paired", "key": inp["key"], "data_key": inp.get("data_key"), "settings": cfg,
            "n_items": n, "est": est, "separated": separated, "anchor_flat": False, "set_size": len(cats),
            "anchored": False, "pids": [inp["persons"][p]["pid"] for p in order], "util": rnd(util), "prob": rnd(r["person_stat"], 3),
            "thr_util": None, "thr_prob": None, "rlh": rnd(rlh), "chance": rnd(chance),
            "mean_lo": rnd(lo, 3), "mean_hi": rnd(hi, 3), "thr_lo": None, "thr_hi": None,
            "mu": rnd(r["mu"]), "sigma_diag": rnd(r["sigma_diag"]), "sigma_util": round(float(r["spread"][0]), 4),
            "acceptance": r["acceptance"], "converged": diag["diag_ok"], "geweke_ok": r["converged"],
            "checked": r["checked"], "geweke_ll": r["geweke_ll"], "geweke_mu_flagged": r["geweke_mu_flagged"],
            "ll_halves": r["ll_halves"], "kept": r["kept"], "sets": r["sets"], "seconds": round(r["seconds"], 1), **diag,
            "draws": hb.pack_draws(r["draws"])}


# --- status, view and the current result (as maxdiff_hb, with this input) -----------------------------------------

def view(item, table, labels):
    stored = load(item)
    inp, disconnected = _prepare(item, table, stored.get("settings") if stored else None)
    out = {"available": inp is not None, "disconnected": disconnected, "marker": running(item), "result": None,
           "state": None, "stale_created": None, "min_persons": hb.MIN_PERSONS,
           "input_key": inp["key"] if inp else None, "data_key": inp["data_key"] if inp else None,
           "seen_key": stored.get("key") if stored else None}
    return _state(item, out, stored, labels)


def revisit(item, before, labels):
    stored = load(item)
    out = {**before, "marker": running(item), "result": None, "state": None, "stale_created": None,
           "unknown": bool(stored and stored.get("key") not in (before.get("seen_key"), before.get("input_key")))}
    if out["unknown"] and before.get("data_key") and stored.get("data_key") == before["data_key"]:
        out["unknown"], out["input_key"] = False, stored["key"]
    return _state(item, out, stored, labels)


def _state(item, out, stored, labels):
    if stored and stored.get("type") != "paired":
        stored = None  # a result of the item when it was of another type
    out = maxdiff_hb._state(item, out, stored, labels)
    if out.get("result"):
        out["result"] = summary(stored, labels)
    return out


def summary(res, labels):
    """maxdiff_hb.summary without the MaxDiff words: options that won or lost every comparison named here."""
    out = maxdiff_hb.summary({**res, "separated": []}, labels)
    separated = set(res.get("separated") or [])
    for r in out["rows"]:
        if r["n"] - 1 in separated:
            r.update(separated=True, lo=None, hi=None)
    if separated:
        out["warnings"].append(de_en(
            "Option {which}: Hat bei allen jeden Vergleich gewonnen oder verloren. Ihren Wert begrenzt nur die Priori; "
            "ohne Intervall, nur als Richtung lesen.",
            "Option {which}: won or lost every comparison for everyone. Only the prior bounds its value; without an "
            "interval, read only as a direction.", which=", ".join(str(i + 1) for i in sorted(separated))))
    return out


def current(item):
    """The stored result if it still fits the data of its own data pool, else None (as maxdiff_hb.current)."""
    import os

    from flask import g, has_request_context

    path = maxdiff_hb._paths(item)[0]
    try:
        stamp_ = os.path.getmtime(path)
    except OSError:
        return None
    cache = g.setdefault("_hb_current", {}) if has_request_context() else {}
    if (item.id, stamp_) in cache:
        return cache[(item.id, stamp_)]
    result = maxdiff_hb._read(path)
    if result and (result.get("type") != "paired" or not maxdiff_hb.fits(
            item, path, result, lambda table: model_input(item, table, result.get("settings")))):
        result = None
    cache[(item.id, stamp_)] = result
    return result


def start(item, table, pool, background=True, config=None):
    """As maxdiff_hb.start: "started", "running", "current", "disconnected" or "nodata"."""
    from flask import current_app

    from app import jobs

    app = current_app._get_current_object()
    inp, disconnected = _prepare(item, table, config)
    if inp is None:
        return "disconnected" if disconnected else "nodata"
    stored = load(item)
    if stored and stored.get("key") == inp["key"]:
        restamp(maxdiff_hb._paths(item)[0], stored, pool)
        return "current"
    if not jobs.start(app, "paired_hb", maxdiff_hb._paths(item), inp, pool, background, name=f"hb-item-{item.id}"):
        return "running"
    return "started"


# --- export ----------------------------------------------------------------------------------------------------------

def export_columns(item, result):
    code, labels = item.code, item.kind.labels(item)
    cols = [(f"{code}_hb_util{i}", f"HB-Nutzen (nullzentriert): {label}") for i, label in enumerate(labels, start=1)]
    cols += [(f"{code}_hb_value{i}", f"HB-Wert (Summe 100): {label}") for i, label in enumerate(labels, start=1)]
    cols.append((f"{code}_hb_rlh", "HB: Root Likelihood (Anpassungsgüte der Person)"))
    return cols


def export_values(item, result):
    code = item.code
    out = {}
    for n, pid in enumerate(result["pids"]):
        row = {f"{code}_hb_util{i + 1}": None for i in range(result["n_items"])}
        row.update({f"{code}_hb_value{i + 1}": None for i in range(result["n_items"])})
        for j, i in enumerate(result["est"]):
            row[f"{code}_hb_util{i + 1}"] = result["util"][n][j]
            row[f"{code}_hb_value{i + 1}"] = result["prob"][n][j]
        row[f"{code}_hb_rlh"] = result["rlh"][n]
        out[pid] = row
    return out


def describe(result, survey=None, lang=None):
    from datetime import datetime

    from app.items.base import reader
    from app.timeutil import to_local

    s = result["settings"]
    when = to_local(datetime.fromisoformat(result["created"]))
    basis = maxdiff_hb.pool_text(result.get("pool") or {}, survey, lang)
    if reader(lang) == "en":
        return (f"Hierarchical Bayes (Bradley-Terry, adjacent categories, with position effect), estimated on "
                f"{when:%Y-%m-%d %H:%M}; {len(result['pids'])} people ({basis}); {s['burn']} burn-in, {s['draws']} "
                f"iterations, thinning {s['thin']}, seed {s['seed']}; posterior mean per person"
                f"{hb_convergence.describe(result, 'en')}")
    return (f"Hierarchical Bayes (Bradley-Terry, benachbarte Kategorien, mit Positionseffekt), geschätzt am "
            f"{when:%d.%m.%Y %H:%M}; {len(result['pids'])} Personen ({basis}); {s['burn']} Burn-in, {s['draws']} "
            f"Iterationen, Thinning {s['thin']}, Seed {s['seed']}; Posterior-Mittel je Person"
            f"{hb_convergence.describe(result, 'de')}")


def codebook_info(item, column, result, lang=None):
    from app.items.base import in_lang, reader

    note = describe(result, item.survey, lang)
    if column == f"{item.code}_hb_rlh":
        chance = fixed(float(np.mean(result["chance"])), 2)
        return "Zahl", in_lang(lang, "0 bis 1", "0 to 1"), [note, (
            f"geometric mean of the probabilities of the person's answers; chance about {chance}" if reader(lang) == "en"
            else f"geometrisches Mittel der Wahrscheinlichkeiten der Antworten der Person; Zufall etwa {chance}".replace(".", ","))]
    if "_hb_util" in column:
        return "Zahl", in_lang(lang, "Logit, Mittel der Optionen = 0", "logit, mean of the options = 0"), [note]
    return "Zahl", in_lang(lang, "0 bis 100, Summe 100 über die Optionen", "0 to 100, sum 100 over the options"), [
        note, in_lang(lang, "100 · exp(u) / Summe exp(u), je Ziehung berechnet und gemittelt",
                      "100 · exp(u) / sum of exp(u), computed per draw and averaged")]
