"""Segments of a CBC or MaxDiff question by a latent class logit (app/latent_class.py), stored per item
(concept latente-klassen-konzept.md, stage 1; LK1 to LK17 as recommended).

The choice sets and their coding are those of the hierarchical Bayes estimate (app/conjoint_hb.py,
app/maxdiff_hb.py: model_input), so the data pool, the holdouts left out and the key of the answers are the same.
Every class carries the weak prior of the pooled logit (choice_model.Coding.penalty(), MaxDiff: maxdiff_analysis RIDGE
on the centred utilities). Solutions K = 1 .. K_max in one run; the page suggests the one with the smallest BIC
(latent_class.suggest) and the researcher picks the one that is shown, exported and simulated (item.config["lc"],
with names per segment). Like the saved scenarios of the simulator that choice is no change of the questionnaire: the
results cache, the HB key and the design lock leave it out (simulator.without_sim).

Storage, background run, status and staleness as for HB (app/maxdiff_hb.py: the same folder, marker with heartbeat,
start lock and slots): UPLOAD_FOLDER/hb/<survey>/item_<id>.lc.json, the marker item_<id>.lc.run.json. Every K is
written to item_<id>.lc.part.json as soon as it is done; a run killed with its process (Passenger) resumes from there
for the same answers and settings (LK17). Settings from the app config: LC_STARTS, LC_SEED, LC_KMAX; LC_SYNC runs in
the request (tests).

The ratings-based conjoint (app/items/cva.py, stage 4 of aca-cva-konzept.md) takes the same storage, page and export
with a latent class regression instead of the logit (app/lc_regression.py): its rows are those of the regression per
person (cva_analysis.rows_of, holdouts left out), without covariates, HB means and holdout hit rates.
"""
import hashlib
import json
import math
import os
from datetime import datetime

import numpy as np

from app import latent_class as lc, maxdiff_hb
from app.lang import de_en
from app.units import fixed, number, page_lang

FORMAT = 2  # 2: dual response coded as one choice with "none" (audit 2026-10-06, W-1), results coded before are estimated anew
           # (audit 2026-10-10, W-1)
KEY = "lc"           # item.config: {"k": shown K or None (the suggestion), "names": {"K": [name per segment]}, "prev"}
NAME_LEN = 40
TYPES = ("conjoint", "maxdiff", "acbc", "cva")  # ACBC in stage 3 (LK3), with a note on its adaptive concepts; CVA by
#                                                   a latent class regression (app/lc_regression.py)
PROFILE_MAX = 8      # questions that describe the segments at most
COVARIATES_MAX = 4   # questions that explain who belongs where (covariates of the class shares) at most
PROFILE_DEFAULT = 4  # without a choice: the first questions results can be split by (compare.questions)
SURE = 0.8           # tau from which a person counts as clearly in a segment
ENTROPY_LOW = lc.ENTROPY_LOW  # below: the segments overlap strongly (Weller et al., 2020)
RANDOM_FACTOR = 1.2  # RLH of a segment below this times chance: possibly random answers (as maxdiff_hb.RLH_FACTOR)
SCALE_CORR, SCALE_RATIO = 0.9, 1.5  # two segments alike in direction but not in strength: a scale difference


def settings(app=None):
    from flask import current_app

    c = (app or current_app).config
    return {"starts": int(c.get("LC_STARTS", lc.STARTS)), "seed": int(c.get("LC_SEED", 1)),
            "kmax": int(c.get("LC_KMAX", lc.K_MAX))}


# --- saved choice ---------------------------------------------------------------------------------------------

def box(item):
    return dict((item.config or {}).get(KEY) or {})


def clean(raw):
    """The saved choice of a survey file: only the known shape, never an error."""
    if not isinstance(raw, dict):
        return {}
    k = raw.get("k")
    out = {"k": k if isinstance(k, int) and 1 <= k <= lc.K_MAX else None}
    names = raw.get("names") if isinstance(raw.get("names"), dict) else {}
    out["names"] = {str(K): [_name(x) for x in v[:int(K)]] for K, v in names.items()
                    if str(K).isdigit() and 1 <= int(K) <= lc.K_MAX and isinstance(v, list)}
    # the questions that describe the segments are ids of this survey: a file gives other ids, so they are left out
    return out


def _name(text):
    # ";" and "=" separate the value labels of the codebook (export.value_re)
    return " ".join(str(text or "").replace(";", ",").replace("=", "-").split())[:NAME_LEN]


def _state_of(b):
    """The saved choice without its undo step."""
    out = {"k": b.get("k"), "names": b.get("names") or {}}
    if b.get("profile") is not None:
        out["profile"] = list(b["profile"])
    if b.get("covariates"):
        out["covariates"] = list(b["covariates"])
    return out


def choose(item, form, kmax):
    """Store the shown K, the names of its segments and the questions that describe them (form field lc_prof, sent
    with lc_prof_sent) from the form; True if something changed."""
    old = box(item)
    try:
        k = int(form.get("lc_k") or 0)
    except ValueError:
        k = 0
    k = k if 1 <= k <= kmax else old.get("k")
    names = dict(old.get("names") or {})
    shown = k or 0
    given = [_name(form.get(f"lc_name_{c}")) for c in range(shown)]
    if shown and any(f"lc_name_{c}" in form for c in range(shown)):
        names[str(shown)] = given
    new = {"k": k, "names": names}
    if form.get("lc_prof_sent"):
        new["profile"] = [int(x) for x in form.getlist("lc_prof") if str(x).isdigit()][:PROFILE_MAX]
    elif old.get("profile") is not None:
        new["profile"] = list(old["profile"])
    if form.get("lc_cov_sent"):
        chosen = [int(x) for x in form.getlist("lc_cov") if str(x).isdigit()][:COVARIATES_MAX]
        if chosen:
            new["covariates"] = chosen
    elif old.get("covariates"):
        new["covariates"] = list(old["covariates"])
    if new == _state_of(old):
        return False
    item.config = {**(item.config or {}), KEY: {**new, "prev": _state_of(old)}}
    return True


def undo_last(item):
    b = box(item)
    if b.get("prev") is None:
        return False
    item.config = {**item.config, KEY: {**b["prev"], "prev": _state_of(b)}}
    return True


def names_of(item, K):
    given = (box(item).get("names") or {}).get(str(K)) or []
    return [given[c] if c < len(given) and given[c] else f"Segment {c + 1}" for c in range(K)]


# --- input ----------------------------------------------------------------------------------------------------

def model_input(item, table, config=None):
    """Everything the estimation needs and the key of the included answers; None with fewer than
    lc.MIN_PERSONS persons or nothing to estimate."""
    cfg = config or settings()
    if item.type == "cva":
        return _cva_input(item, table, cfg)
    if item.type == "maxdiff":
        base = maxdiff_hb.model_input(item, table, {"lc": True})
        if base is None or len(base["persons"]) < lc.MIN_PERSONS:
            return None
        inp = {"type": "maxdiff", "versions": base["versions"], "n_items": base["n_items"], "est": base["est"],
               "col": base["col"], "k": base["k"], "thr": base["thr"], "set_size": base["set_size"],
               "persons": base["persons"]}
    else:
        from app import conjoint_hb

        base = conjoint_hb.model_input(item, table, {"lc": True})
        if base is None or len(base["persons"]) < lc.MIN_PERSONS:
            return None
        inp = {"type": item.type, "coding": base["coding"], "price_range": base["price_range"], "persons": base["persons"]}
    Z, columns = covariate_design(item, table, [p["pid"] for p in inp["persons"]])
    if Z is not None:
        inp.update(Z=Z, covariates=columns)
    # without covariates the key is the one of results estimated before they existed (they stay current)
    with_z = {"Z": Z, "covariates": columns} if Z is not None else {}
    key = hashlib.sha1(json.dumps({"format": FORMAT, "data": base["data_key"], "settings": cfg, **with_z},
                                  sort_keys=True).encode()).hexdigest()
    return {**inp, "key": key, "data_key": base["data_key"], "settings": cfg}


def _cva_input(item, table, cfg):
    """model_input of a ratings-based conjoint: per person the rows of the regression (cva_analysis.rows_of), persons
    with the same answer to every task left out as in its results (nothing to tell apart)."""
    from app import cva_analysis
    from app.choice_model import responses_for

    if not item.kind.design_current(item):
        return None
    versions = item.config["design"]["versions"]
    persons = []
    for r in responses_for(item, table):
        rows = cva_analysis.rows_of(item, (r.answers or {}).get(str(item.id)), versions)
        if len(rows) > 1 and len({y for _, y in rows}) > 1:
            persons.append({"pid": r.pid, "X": [x for x, _ in rows], "y": [y for _, y in rows]})
    if len(persons) < lc.MIN_PERSONS:
        return None
    data_key = hashlib.sha1(json.dumps([[p["pid"], p["X"], p["y"]] for p in persons]).encode()).hexdigest()
    key = hashlib.sha1(json.dumps({"format": FORMAT, "type": "cva", "data": data_key, "settings": cfg},
                                  sort_keys=True).encode()).hexdigest()
    return {"type": "cva", "levels": item.kind.level_counts(item), "form": item.kind.form(item), "persons": persons,
            "key": key, "data_key": data_key, "settings": cfg}


def covariate_design(item, table, pids):
    """The design of the class shares from the chosen covariates (box "covariates"): an intercept, per single choice
    question one column per answer but the first one somebody gave (the reference: a first answer nobody gave would
    make the dummies collinear with the intercept, audit 2026-10-05, S-3), per number the value standardised
    (missing: the mean); a column for "keine Angabe" where someone has none. Columns without variance or adding no
    rank are left out, so Z has full column rank. (Z as lists, [{"item", "label", "question"}] per column but the
    intercept) or (None, None) without covariates."""
    from app import compare, results as res
    from app.items.base import plain
    from app.models import Item
    from app.extensions import db

    ids = box(item).get("covariates") or []
    if not ids:
        return None, None
    by_pid = {r.get("pid"): r for r in table if r.get("pid")}
    rows = [by_pid.get(pid) or {} for pid in pids]
    lang = item.survey.default_language
    cols, meta = [], []
    for i in ids:
        other = db.session.get(Item, i)
        if other is None or other.block.survey_id != item.block.survey_id or other.id == item.id:
            continue
        question = f"{other.code}: {plain(other.text('question', lang))[:60]}"
        grouper = compare.question(item.survey, i)
        if grouper is not None:
            groups = compare.groups_of(grouper)
            values = [res._key(r.get(grouper.code)) if r.get(grouper.code) not in (None, "") else None for r in rows]
            known = {c for c, _ in groups}
            values = [v if v in known else None for v in values]
            given = [(code, label) for code, label in groups if any(v == code for v in values)]
            for code, label in given[1:]:
                cols.append([1.0 if v == code else 0.0 for v in values])
                meta.append({"item": i, "question": question, "label": label, "reference": given[0][1]})
            missing = [v is None for v in values]
        else:
            gone = {str(m) for m in other.kind.missing_codes(other, other.code)}
            x = [res.valid_number(r.get(other.code), gone) for r in rows]
            have = [v for v in x if v is not None]
            if len(have) < 2 or float(np.std(have)) == 0:
                continue
            m, sd = float(np.mean(have)), float(np.std(have))
            cols.append([0.0 if v is None else (float(v) - m) / sd for v in x])
            meta.append({"item": i, "question": question, "label": f"je Standardabweichung ({fixed(sd, 2)})".replace(".", ","),
                         "reference": None})
            missing = [v is None for v in x]
        if any(missing) and not all(missing):
            cols.append([1.0 if v else 0.0 for v in missing])
            meta.append({"item": i, "question": question, "label": "keine Angabe", "reference": None})
    # full column rank: a column without variance, or one the intercept and the columns before it already span, has
    # no identified coefficient (its value would only split an effect arbitrarily)
    keep, kept = [], np.ones((len(pids), 1))
    for col, m in zip(cols, meta):
        x = np.asarray(col, dtype=float)
        if float(np.std(x)) == 0:
            continue
        trial = np.column_stack([kept, x])
        if np.linalg.matrix_rank(trial) > kept.shape[1]:
            kept = trial
            keep.append((col, m))
    if not keep:
        return None, None
    cols, meta = [c for c, _ in keep], [m for _, m in keep]
    Z = [[1.0] + [c[n] for c in cols] for n in range(len(pids))]
    return Z, meta


def covariate_ids(item):
    return list(box(item).get("covariates") or [])


def _sets(inp):
    """(person sets, number of parameters, prior precision, coding or None, M or None, threshold row or None)."""
    from app.choice_model import ChoiceSet

    if inp["type"] == "maxdiff":
        from app.maxdiff_analysis import RIDGE, anchor_sets, task_sets

        col = {int(i): c for i, c in inp["col"].items()}
        k, thr = inp["k"], inp["thr"]
        person_sets = []
        for p in inp["persons"]:
            tasks = {int(t): tuple(bw) for t, bw in p["tasks"].items()}
            sets = task_sets(inp["versions"], p["v"], tasks, col, k)
            if p.get("anchor") is not None:
                sets += anchor_sets(p["anchor"], inp["est"], col, k, thr)
            person_sets.append(sets)
        M, t_vec = maxdiff_hb._matrices(len(inp["est"]), k, thr)
        P = RIDGE * (M.T @ M + (np.outer(t_vec, t_vec) if t_vec is not None else 0))
        return person_sets, k, P, None, M, t_vec
    from app import conjoint_hb

    coding = conjoint_hb._coding(inp["coding"])
    person_sets = [[ChoiceSet(X, chosen) for X, chosen in p["sets"]] for p in inp["persons"]]
    return person_sets, coding.k, coding.penalty(), coding, None, None


def _part_path(paths):
    return paths[0][:-len(".json")] + ".part.json"


def run(inp, progress=None):
    """Estimate from model_input(); the JSON-ready result (without pool and created)."""
    from app.choice_model import fit

    if inp["type"] == "cva":
        return _run_cva(inp, progress)

    person_sets, k, P, coding, M, t_vec = _sets(inp)
    everything = [s for sets in person_sets for s in sets]
    base = fit(everything, k, ridge=P)[0]  # every class starts at the pooled solution
    cfg = inp["settings"]
    part = inp.get("partial")
    finished = []
    stored = maxdiff_hb._read(part) if part else None
    if stored and stored.get("key") == inp["key"]:
        finished = [_unpack(s) for s in stored.get("solutions") or []]

    def keep(sol):
        if part and os.path.isdir(os.path.dirname(part)):  # not into a folder removed meanwhile (C3)
            done = maxdiff_hb._read(part) or {}
            rows = [x for x in (done.get("solutions") or []) if done.get("key") == inp["key"] and x["K"] < sol["K"]]
            maxdiff_hb._write(part, {"key": inp["key"], "solutions": rows + [_pack(sol)]})

    r = lc.estimate(person_sets, k, P, base=base, kmax=min(cfg["kmax"], lc.k_max(len(person_sets), k)),
                    starts=cfg["starts"], seed=cfg["seed"], progress=progress, finished=finished, on_solution=keep,
                    Z=inp.get("Z"))
    seps = []
    for sol in r["solutions"]:
        modal = np.asarray(sol["tau"]).argmax(axis=1)
        seps.append(_separated(person_sets, modal, sol["K"], coding, inp))
    out = {"format": FORMAT, "key": inp["key"], "data_key": inp["data_key"], "type": inp["type"], "settings": cfg,
           "pids": [p["pid"] for p in inp["persons"]], "k": k, "persons": r["persons"], "sets": r["sets"],
           "chance": round(r["chance"], 4), "seconds": round(r["seconds"], 1),
           "suggested": lc.suggest(r["solutions"]), "covariates": inp.get("covariates"),
           "solutions": [{**_pack(s), "separated": sep} for s, sep in zip(r["solutions"], seps)]}
    if inp["type"] == "maxdiff":
        out.update(n_items=inp["n_items"], est=inp["est"], thr=inp["thr"], set_size=inp["set_size"],
                   anchored=inp["thr"] is not None)
    else:
        out.update(coding=inp["coding"], price_range=inp["price_range"])
    if part:
        try:
            os.remove(part)
        except OSError:
            pass
    return out


def _run_cva(inp, progress=None):
    """run() of a ratings-based conjoint: the latent class regression (app/lc_regression.py)."""
    from app import lc_regression as lr

    cfg = inp["settings"]
    part = inp.get("partial")
    finished = []
    stored = maxdiff_hb._read(part) if part else None
    if stored and stored.get("key") == inp["key"]:
        finished = [_unpack(s) for s in stored.get("solutions") or []]

    def keep(sol):
        if part and os.path.isdir(os.path.dirname(part)):  # not into a folder removed meanwhile (C3)
            done = maxdiff_hb._read(part) or {}
            rows = [x for x in (done.get("solutions") or []) if done.get("key") == inp["key"] and x["K"] < sol["K"]]
            maxdiff_hb._write(part, {"key": inp["key"], "solutions": rows + [_pack(sol)]})

    k = lr.n_params(len(inp["persons"][0]["X"][0]))
    r = lr.estimate(inp["persons"], kmax=min(cfg["kmax"], lc.k_max(len(inp["persons"]), k)), starts=cfg["starts"],
                    seed=cfg["seed"], progress=progress, finished=finished, on_solution=keep)
    out = {"format": FORMAT, "key": inp["key"], "data_key": inp["data_key"], "type": "cva", "settings": cfg,
           "pids": [p["pid"] for p in inp["persons"]], "k": k - 1, "persons": r["persons"], "sets": r["sets"],
           "chance": None, "seconds": round(r["seconds"], 1), "suggested": lc.suggest(r["solutions"]),
           "covariates": None, "levels": inp["levels"], "form": inp["form"],
           "solutions": [{**_pack(s), "separated": [[] for _ in range(s["K"])]} for s in r["solutions"]]}
    if part:
        try:
            os.remove(part)
        except OSError:
            pass
    return out


def _separated(person_sets, modal, K, coding, inp):
    """Per segment the levels (CBC: [attribute, level]) or statements (MaxDiff: parameter column) never or always
    chosen by its persons where it made a difference: only the prior bounds them there."""
    out = []
    for c in range(K):
        sets = [s for p, sets in enumerate(person_sets) if modal[p] == c for s in sets]
        if not sets:
            out.append([])
        elif coding is not None:
            out.append([[int(a), int(lv)] for a, lv in coding.separated(sets)])
        else:
            out.append(_maxdiff_separated(sets, inp))
    return out


def _maxdiff_separated(sets, inp):
    """The statements of one segment that were always chosen as most or always as least important where they were
    shown: the rule of the pooled MNL (maxdiff_analysis.mnl, best == shown or worst == shown), the reference
    statement (no column of its own) included. A best set has the statements' columns at +1, a worst set at −1,
    an anchor set the threshold; best and worst are counted apart (audit 2026-10-12, W-2: before, together, so
    that a statement always least important was not marked and one never chosen at all was)."""
    by_col = {int(j): int(i) for i, j in inp["col"].items()}
    est = [int(i) for i in inp.get("est") or []]
    reference = next((i for i in reversed(est) if i not in by_col.values()), None)
    thr = inp.get("thr")
    shown, best, worst = {}, {}, {}
    for s in sets:
        X = np.asarray(s.X)
        if thr is not None and np.any(X[:, thr] != 0):
            continue  # an anchor set
        cells = X if thr is None else np.delete(X, thr, axis=1)
        if np.any(cells < 0):
            target = worst
        else:
            target = best
        for j, row in enumerate(cells):
            hit = np.flatnonzero(row)
            i = by_col.get(int(hit[0])) if len(hit) else reference
            if i is None:
                continue
            if target is best:
                shown[i] = shown.get(i, 0) + 1
            if j == s.chosen:
                target[i] = target.get(i, 0) + 1
    return sorted(i for i, n in shown.items() if n and (best.get(i, 0) == n or worst.get(i, 0) == n))


def _sig(x, digits=6):
    return [float(f"{v:.{digits}g}") for v in np.asarray(x, dtype=float).ravel()]


def _pack(sol):
    C = sol["K"]
    return {"K": C, "ll": round(sol["ll"], 4), "objective": round(sol["objective"], 4), "params": sol["params"],
            "bic": round(sol["bic"], 3), "aic3": round(sol["aic3"], 3), "caic": round(sol["caic"], 3),
            "entropy": None if sol["entropy"] is None else round(sol["entropy"], 4),
            "pi": _sig(sol["pi"]), "B": np.asarray(sol["B"]).round(6).tolist(),
            "tau": np.asarray(sol["tau"]).round(4).tolist(),
            "cov": None if sol["cov"] is None else _sig(sol["cov"]),
            "share_ci": None if sol["share_ci"] is None else [[round(a, 4), round(b, 4)] for a, b in sol["share_ci"]],
            "converged": bool(sol["converged"]), "iterations": int(sol["iterations"]), "found": int(sol["found"]),
            "runs": int(sol["runs"]), "rlh": [None if x is None else round(x, 4) for x in sol["rlh"]],
            "G": None if sol.get("G") is None else np.asarray(sol["G"]).round(6).tolist(),
            **({"sigma": [round(x, 6) for x in sol["sigma"]]} if sol.get("sigma") is not None else {})}


def _unpack(row):
    s = dict(row)
    s["pi"], s["B"], s["tau"] = np.array(row["pi"]), np.array(row["B"]), np.array(row["tau"])
    s["G"] = None if row.get("G") is None else np.array(row["G"])
    r = 1 if s["G"] is None else len(s["G"])
    m = len(s["B"]) * len(s["B"][0]) + (s["K"] - 1) * r
    s["cov"] = None if row.get("cov") is None else np.array(row["cov"]).reshape(m, m)
    s["share_ci"] = None if row.get("share_ci") is None else [tuple(x) for x in row["share_ci"]]
    return s


# --- storage, start -----------------------------------------------------------------------------------------------

def _paths(item, app=None):
    d = maxdiff_hb.folder(item, app)
    return os.path.join(d, f"item_{item.id}.lc.json"), os.path.join(d, f"item_{item.id}.lc.run.json")


def load(item):
    return maxdiff_hb._read(_paths(item)[0])


def running(item):
    """As maxdiff_hb.running (app/jobs.py)."""
    from app import jobs

    return jobs.marker(_paths(item))


def pending(item, key):
    """The K already done by an interrupted run for these answers and settings (resumed by start), else 0."""
    part = maxdiff_hb._read(_part_path(_paths(item)))
    return len(part.get("solutions") or []) if part and part.get("key") == key else 0


def start(item, table, pool, background=True, config=None):
    """As maxdiff_hb.start: "started", "running", "current" or "nodata"."""
    from flask import current_app

    from app import jobs

    app = current_app._get_current_object()
    inp = model_input(item, table, config)
    if inp is None:
        return "nodata"
    stored = _stored(item)
    if stored and stored.get("key") == inp["key"]:
        maxdiff_hb.restamp(_paths(item)[0], stored, pool)
        return "current"
    paths = _paths(item)
    inp["partial"] = _part_path(paths)
    if not jobs.start(app, "segments", paths, inp, pool, background, name=f"lc-item-{item.id}"):
        return "running"
    return "started"


def _stored(item):
    stored = load(item)
    return None if stored and (stored.get("format") != FORMAT or stored.get("type") != item.type) else stored


def current(item):
    """The stored result if it still fits the data of its own pool, else None (cached per request)."""
    from flask import g, has_request_context

    path = _paths(item)[0]
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return None
    cache = g.setdefault("_lc_current", {}) if has_request_context() else {}
    if (item.id, stamp) in cache:
        return cache[(item.id, stamp)]
    result = _stored(item)
    if result and not maxdiff_hb.fits(item, path, result, lambda table: model_input(item, table, result.get("settings"))):
        result = None
    cache[(item.id, stamp)] = result
    return result


def shown_k(item, result):
    """The K the page shows: the saved choice if the result has it, else the suggestion."""
    k = box(item).get("k")
    ks = [s["K"] for s in result["solutions"]]
    return k if k in ks else result["suggested"]


def solution(result, K):
    return next(s for s in result["solutions"] if s["K"] == K)


# --- export -------------------------------------------------------------------------------------------------------

def export_columns(item, result):
    """[(column, label)]: the most likely segment and the membership probabilities of the shown solution (K > 1)."""
    K = shown_k(item, result)
    if K < 2:
        return []
    code = item.code
    names = names_of(item, K)
    return [(f"{code}_lk", f"Latente Klasse: wahrscheinlichstes Segment ({K} Segmente)")] + [
        (f"{code}_lk_p{c + 1}", f"Latente Klasse: Wahrscheinlichkeit für {names[c]}") for c in range(K)]


def export_values(item, result):
    K = shown_k(item, result)
    if K < 2:
        return {}
    code = item.code
    tau = solution(result, K)["tau"]
    out = {}
    for n, pid in enumerate(result["pids"]):
        row = {f"{code}_lk": int(np.argmax(tau[n])) + 1}
        row.update({f"{code}_lk_p{c + 1}": tau[n][c] for c in range(K)})
        out[pid] = row
    return out


def describe(result, survey=None, K=None, lang=None):
    """One line for the codebook in the reader's language `lang` (None: the page's; app.items.base.reader)."""
    from app.items.base import reader
    from app.timeutil import to_local

    s = result["settings"]
    when = datetime.fromisoformat(result["created"])
    sol = solution(result, K) if K else None
    basis = maxdiff_hb.pool_text(result.get("pool") or {}, survey, lang)
    if result["type"] == "cva":
        return _describe_cva(result, when, sol, K, basis, s, lang)
    if reader(lang) == "en":
        coding = "sequential best-worst coding" if result["type"] == "maxdiff" else "choice tasks, effects coding"
        fit_text = ""
        if sol:
            fit_text = (f"; {K} segments, LL {fixed(sol['ll'], 1)}, BIC {fixed(sol['bic'], 1)}"
                        + (f", entropy R² {fixed(sol['entropy'], 2)}" if sol["entropy"] is not None else ""))
        return (f"Latent class logit (LC-MNL, {coding}, EM with a weak normal prior per segment), estimated on "
                f"{to_local(when):%Y-%m-%d %H:%M}; {len(result['pids'])} people ({basis}); {s['starts']} starts per K, "
                f"seed {s['seed']}{fit_text}"
                + (f"; covariates of the segment shares: {', '.join(sorted({m['question'] for m in result['covariates']}))}"
                   if result.get("covariates") else ""))
    coding = "sequenzielle Best-Worst-Kodierung" if result["type"] == "maxdiff" else "Auswahlaufgaben, Effektkodierung"
    fit_text = ""
    if sol:
        fit_text = (f"; {K} Segmente, LL {fixed(sol['ll'], 1)}, BIC {fixed(sol['bic'], 1)}"
                    + (f", Entropie R² {fixed(sol['entropy'], 2)}" if sol["entropy"] is not None else "")).replace(".", ",")
    return (f"Latent-Class-Logit (LC-MNL, {coding}, EM mit schwacher Normal-Priori je Segment), geschätzt am "
            f"{to_local(when):%d.%m.%Y %H:%M}; {len(result['pids'])} Personen ({basis}); {s['starts']} Starts je K, "
            f"Seed {s['seed']}{fit_text}"
            + (f"; Kovariaten der Segmentanteile: {', '.join(sorted({m['question'] for m in result['covariates']}))}"
               if result.get("covariates") else ""))


def _describe_cva(result, when, sol, K, basis, s, lang):
    from app.items.base import reader
    from app.timeutil import to_local

    if reader(lang) == "en":
        fit_text = (f"; {K} segments, LL {fixed(sol['ll'], 1)}, BIC {fixed(sol['bic'], 1)}"
                    + (f", entropy R² {fixed(sol['entropy'], 2)}" if sol["entropy"] is not None else "")) if sol else ""
        return (f"Latent class regression (finite mixture of linear regressions on the ratings, effects coding, EM), "
                f"estimated on {to_local(when):%Y-%m-%d %H:%M}; {len(result['pids'])} people ({basis}); {s['starts']} "
                f"starts per K, seed {s['seed']}{fit_text}")
    fit_text = (f"; {K} Segmente, LL {fixed(sol['ll'], 1)}, BIC {fixed(sol['bic'], 1)}"
                + (f", Entropie R² {fixed(sol['entropy'], 2)}" if sol["entropy"] is not None else "")).replace(".", ",") \
        if sol else ""
    return (f"Latent-Class-Regression (Mischung linearer Regressionen der Bewertungen, Effektkodierung, EM), geschätzt am "
            f"{to_local(when):%d.%m.%Y %H:%M}; {len(result['pids'])} Personen ({basis}); {s['starts']} Starts je K, "
            f"Seed {s['seed']}{fit_text}")


def codebook_info(item, column, result, lang=None):
    from app.items.base import in_lang

    K = shown_k(item, result)
    note = describe(result, item.survey, K, lang)
    if column == f"{item.code}_lk":
        names = names_of(item, K)
        return "Segment", "; ".join(f"{c + 1} = {names[c]}" for c in range(K)), [
            note, in_lang(lang, "Zuordnung zum wahrscheinlichsten Segment; Fehlzuordnungen verwischen Unterschiede zwischen "
                                "den Segmenten. Die Wahrscheinlichkeiten als Gewichte korrigieren die Fehlzuordnung nicht; "
                                "für Vergleiche BCH-Gewichte bzw. eine 3-Schritt-Korrektur nutzen (Vermunt, 2010)",
                          "assignment to the most likely segment; misassignments blur differences between the segments. "
                          "The probabilities as weights do not correct the misassignment; for comparisons use BCH "
                          "weights or a three-step correction (Vermunt, 2010)")]
    return "Zahl", in_lang(lang, "0 bis 1, Summe 1 über die Segmente", "0 to 1, sum 1 over the segments"), [
        note, in_lang(lang, "A-posteriori-Wahrscheinlichkeit der Zugehörigkeit; als Gewichte korrigieren die "
                            "Wahrscheinlichkeiten die Fehlzuordnung nicht und verwischen Unterschiede zwischen den "
                            "Segmenten wie die Zuordnung zum wahrscheinlichsten Segment; für Vergleiche BCH-Gewichte bzw. "
                            "eine 3-Schritt-Korrektur nutzen (Vermunt, 2010)",
                      "posterior probability of membership; as weights the probabilities do not correct the "
                      "misassignment and blur differences between the segments like the assignment to the most likely "
                      "segment; for comparisons use BCH weights or a three-step correction (Vermunt, 2010)")]


# --- results page -------------------------------------------------------------------------------------------------

def view(item, table, lang):
    stored = _stored(item)
    inp = model_input(item, table, stored.get("settings") if stored else None)
    out = {"available": inp is not None, "marker": running(item), "result": None, "state": None, "stale_created": None,
           "min_persons": lc.MIN_PERSONS, "min_class": lc.MIN_CLASS, "short": lc.SHORT, "keep": lc.KEEP,
           "input_key": inp["key"] if inp else None,
           "data_key": inp["data_key"] if inp else None, "seen_key": stored.get("key") if stored else None,
           "pending": pending(item, inp["key"]) if inp else 0}
    if inp:
        # two segments need lc.PER_PARAMETER persons per parameter of each (LK4): below, the run would give K = 1
        # only, so the page names the number instead of offering it (audit 2026-10-05, S-6)
        k = parameters(inp)
        out.update(n_persons=len(inp["persons"]), parameters=k, two_persons=2 * lc.PER_PARAMETER * k,
                   too_few_for_two=lc.k_max(len(inp["persons"]), k) < 2)
    return _state(item, out, stored, lang)


def parameters(inp):
    """Parameters per segment (k) of a model_input()."""
    if inp["type"] == "cva":  # the coefficients and the residual variance (app/lc_regression.py)
        return len(inp["persons"][0]["X"][0]) + 1
    if inp["type"] == "maxdiff":
        return int(inp["k"])
    from app import conjoint_hb

    return conjoint_hb._coding(inp["coding"]).k


def revisit(item, before, lang):
    stored = _stored(item)
    out = {**before, "marker": running(item), "result": None, "state": None, "stale_created": None,
           "unknown": bool(stored and stored.get("key") not in (before.get("seen_key"), before.get("input_key")))}
    if out["unknown"] and before.get("data_key") and stored.get("data_key") == before["data_key"]:
        out["unknown"], out["input_key"] = False, stored["key"]
    out["pending"] = pending(item, out["input_key"]) if out.get("input_key") else 0
    out = _state(item, out, stored, lang)
    if out.get("result") and before.get("enriched") != enriched_key(item, stored):
        out["unknown"] = True  # profiles, HB means and hit rates need the answers: the page computes anew
    return out


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


def _class_cov(cov, c, k):
    return None if cov is None else cov[c * k:(c + 1) * k, c * k:(c + 1) * k]


def summary(item, res, lang):
    """The view model of a stored result for the shown K."""
    from app.timeutil import to_local

    K = shown_k(item, res)
    sol = _unpack(solution(res, K))
    k = res["k"]
    names = names_of(item, K)
    chance = res["chance"]
    tau = sol["tau"]
    sure = (tau.max(axis=1) >= SURE)
    modal = tau.argmax(axis=1)
    segments = []
    for c in range(K):
        ci = sol["share_ci"][c] if sol["share_ci"] else None
        rlh = sol["rlh"][c]
        segments.append({"index": c, "name": names[c], "share": float(sol["pi"][c]),
                         "lo": None if ci is None else ci[0], "hi": None if ci is None else ci[1],
                         "persons": int((modal == c).sum()), "sure": int(((modal == c) & sure).sum()),
                         "rlh": rlh, "random": rlh is not None and rlh < RANDOM_FACTOR * chance})
    table = []
    for s in res["solutions"]:
        n = len(res["pids"])
        small = min(s["pi"]) if s["K"] > 1 else 1.0
        table.append({**{x: s[x] for x in ("K", "ll", "params", "bic", "aic3", "caic", "entropy", "found", "runs",
                                           "converged")},
                      "smallest": small, "smallest_n": small * n,
                      "flag": s["K"] > 1 and lc.too_small(small, n),
                      "unstable": s["K"] > 1 and s["found"] < min(2, s["runs"])})
    best = {crit: min(table, key=lambda r: r[crit])["K"] for crit in ("bic", "aic3", "caic")}
    warnings = []
    if sol["entropy"] is not None and sol["entropy"] < ENTROPY_LOW:
        warnings.append(de_en("Entropie R² {e}: Die Segmente überlappen stark, viele Personen lassen sich keinem "
                              "Segment klar zuordnen. Profile und Segmentgrößen nur als grobe Richtung lesen.",
                              "Entropy R² {e}: the segments overlap strongly; many people cannot be clearly assigned "
                              "to one segment. Read profiles and segment sizes only as a rough direction.",
                              e=number(sol['entropy'], 2, page_lang())))  # the page's decimal mark (Z-7)
    if not sol["converged"]:
        warnings.append(de_en("Die Schätzung dieser Lösung ist nicht konvergiert; die Werte sind nicht verlässlich.",
                              "The estimation of this solution has not converged; the values are not reliable."))
    if K > 1 and sol["found"] < min(2, sol["runs"]):
        warnings.append(de_en("Instabil: Nur {found} von {runs} Läufen fanden diese Lösung. Mit mehr Starts neu "
                              "schätzen.",
                              "Unstable: only {found} of {runs} runs found this solution. Estimate again with more "
                              "starts.", found=sol['found'], runs=sol['runs']))
    if K > 1 and lc.too_small(min(sol["pi"]), len(res["pids"])):
        warnings.append(de_en("Ein Segment ist klein (unter {p} % bzw. unter {n} Personen, je nachdem, was mehr "
                              "ist): Solche Lösungen sind oft schlecht reproduzierbar. Behalten, wenn das Segment "
                              "inhaltlich Sinn ergibt.",
                              "A segment is small (under {p}% or under {n} people, whichever is more): such "
                              "solutions are often poorly reproducible. Keep it if the segment makes sense in "
                              "substance.", p=round(100 * lc.MIN_SHARE), n=lc.MIN_CLASS))
    for s in segments:
        if s["random"]:
            # the decimal mark of the page (Mathe-Audit 2026-10-06, Z-7)
            warnings.append(de_en("{name}: Die Anpassung (RLH {rlh}) liegt nahe am Zufall ({chance}); möglicherweise "
                                  "zufällig Antwortende.",
                                  "{name}: the fit (RLH {rlh}) is close to chance ({chance}); possibly people "
                                  "answering at random.", name=s['name'], rlh=number(s['rlh'], 2, page_lang()),
                                  chance=number(chance, 2, page_lang())))
    out = {"K": K, "kmax": max(s["K"] for s in res["solutions"]), "suggested": res["suggested"], "best": best,
           "solutions": table, "segments": segments, "warnings": warnings, "entropy": sol["entropy"],
           "persons": res["persons"], "sets": res["sets"], "chance": chance, "settings": res["settings"],
           "seconds": res["seconds"], "type": res["type"], "created": to_local(datetime.fromisoformat(res["created"])),
           "names_saved": str(K) in (box(item).get("names") or {}), "can_undo": box(item).get("prev") is not None}
    out["covariates"] = _covariate_effects(res, sol, K, k, names)
    if res["type"] == "acbc":
        out["warnings"].append(de_en("ACBC: Die Konzepte der Auswahl- und Turnieraufgaben liegen nah an der "
                                     "Wunschvariante jeder Person. Die Segmente spiegeln deshalb auch, was sie im "
                                     "Konfigurator gewählt hat, und der Preiskoeffizient je Segment fällt eher zu klein "
                                     "aus (wie im aggregierten Modell).",
                                     "ACBC: the concepts of the choice and tournament tasks lie close to each person's "
                                     "preferred variant. The segments therefore also reflect what they chose in the "
                                     "configurator, and the price coefficient per segment tends to come out too small "
                                     "(as in the aggregate model)."))
    if res["type"] == "maxdiff":
        out.update(_maxdiff_view(item, res, sol, K, k, lang))
    elif res["type"] == "cva":
        out.update(_cva_view(item, res, sol, K, lang))
    else:
        out.update(_cbc_view(item, res, sol, K, k, lang))
    out["warnings"] += out.pop("more_warnings", [])
    return out


def _covariate_effects(res, sol, K, k, names):
    """Per covariate column and segment 2..K the coefficient of the share logit against segment 1 (log odds of being
    in that segment rather than in segment 1), its standard error, the odds ratio and p (Wald, normal)."""
    meta = res.get("covariates")
    G = sol.get("G")
    if not meta or G is None or K < 2:
        return None
    from app import group_tests as gt

    r = len(G)
    cov = sol["cov"]
    rows = []
    for j, m in enumerate(meta, start=1):
        cells = []
        for c in range(1, K):
            g = float(G[j][c])
            se = None
            if cov is not None:
                pos = K * k + (c - 1) * r + j
                se = math.sqrt(max(float(cov[pos, pos]), 0.0))
            z = g / se if se else None
            cells.append({"g": g, "se": se, "or": math.exp(g), "p": gt.norm_sf2(z) if z is not None else None})
        rows.append({**m, "label": _covariate_label(m.get("label")), "cells": cells})
    return {"rows": rows, "reference": names[0], "against": names[1:]}


def _covariate_label(label):
    """The label of a covariate column as stored with the estimate (covariate_design, in German), in the language of
    the page: "keine Angabe" and "je Standardabweichung (…)" are the code's own words, answers stay as written."""
    if label == "keine Angabe":
        return de_en("keine Angabe", "no answer")
    if isinstance(label, str) and label.startswith("je Standardabweichung ("):
        return de_en(label, "per standard deviation " + label[len("je Standardabweichung "):])
    return label


def _scale_hints(vectors, names):
    """Pairs of segments whose values point the same way but differ in strength (Swait & Louviere, 1993)."""
    hints = []
    for a in range(len(vectors)):
        for b in range(a + 1, len(vectors)):
            x, y = np.asarray(vectors[a]), np.asarray(vectors[b])
            if x.std() == 0 or y.std() == 0:
                continue
            r = float(np.corrcoef(x, y)[0, 1])
            spread = (np.abs(x).sum(), np.abs(y).sum())
            ratio = max(spread) / max(min(spread), 1e-9)
            if r > SCALE_CORR and ratio > SCALE_RATIO:
                hints.append(de_en("{a} und {b} haben fast dieselben Vorlieben (r = {r}), unterscheiden sich aber in "
                                   "der Stärke (Faktor {f}): eher ein Unterschied in der Entschiedenheit als in den "
                                   "Vorlieben.",
                                   "{a} and {b} have almost the same preferences (r = {r}) but differ in strength "
                                   "(factor {f}): rather a difference in decisiveness than in preferences.",
                                   a=names[a], b=names[b], r=number(r, 2, page_lang()),
                                   f=number(ratio, 1, page_lang())))
    return hints


def _cbc_view(item, res, sol, K, k, lang):
    from app import conjoint_hb
    from app.choice_model import Z95, price_unit, weak_price, wtp

    coding = conjoint_hb._coding(res["coding"])
    attrs = item.kind.attributes(item, lang)
    B, cov = sol["B"], sol["cov"]
    seps = [{(a, lv) for a, lv in sep} for sep in solution(res, K).get("separated") or [[]] * K]
    price_range = res.get("price_range")
    rows = [{"code": attrs[a]["code"], "label": attrs[a]["label"], "index": a,
             "levels": [{"label": attrs[a]["level_labels"][lv], "values": []} for lv in range(coding.levels[a])],
             "importance": []} for a in coding.effects]
    price = {"beta": [], "se": [], "z": [], "unit": price_unit(list(price_range or [])),
             "importance": []} if coding.price is not None else None
    asc = [] if coding.asc is not None else None
    vectors = []
    for c in range(K):
        b = B[c]
        cc = _class_cov(cov, c, k)
        pw = coding.part_worths(b, cc)
        beta_price = se_price = None
        if coding.price is not None:
            beta_price = b[coding.price] / coding.price_scale
            se_price = math.sqrt(cc[coding.price, coding.price]) / coding.price_scale if cc is not None else None
        z = beta_price / se_price if beta_price is not None and se_price else None
        wtp_ok = z is not None and z <= -Z95
        ranges = {a: max(pw[a][0]) - min(pw[a][0]) for a in coding.effects}
        price_part = abs(beta_price) * (price_range[1] - price_range[0]) if beta_price is not None and price_range else None
        total = sum(ranges.values()) + (price_part or 0)
        vec = []
        for row in rows:
            a = row["index"]
            values, ses = pw[a]
            bounded = any(x[0] == a for x in seps[c])
            for lv, level in enumerate(row["levels"]):
                w = lo = hi = None
                if wtp_ok and not bounded:  # the interval by Fieller, as for the pooled model (audit 2026-10-05, C-1)
                    w, lo, hi = wtp(coding, b, cc, a, lv)
                level["values"].append({"beta": values[lv], "se": None if bounded else ses[lv],
                                        "separated": (a, lv) in seps[c], "wtp": w, "wtp_lo": lo, "wtp_hi": hi})
                vec.append(values[lv])
            row["importance"].append(ranges[a] / total if total else None)
        vectors.append(vec)
        if price is not None:
            price["beta"].append(beta_price)
            price["se"].append(se_price)
            price["z"].append(z)
            price["importance"].append(price_part / total if price_part is not None and total else None)
        if asc is not None:
            asc.append({"beta": b[coding.asc],
                        "se": math.sqrt(cc[coding.asc, coding.asc]) if cc is not None else None})
    pa = item.kind.price_attribute(item) if item.type == "conjoint" else None
    if price is not None:
        price["label"] = attrs[pa]["label"] if pa is not None else de_en("Preis", "Price")
        price["wtp"] = [z is not None and z <= -Z95 for z in price["z"]]
        price["weak"] = [weak_price(z) for z in price["z"]]  # WTP shown but too small on average (C-1)
    wtps = [abs(v["wtp"]) for r in rows for lv in r["levels"] for v in lv["values"] if v["wtp"] is not None]
    more = _scale_hints(vectors, names_of(item, K)) if K > 1 else []
    if price is not None and K > 1 and not all(price["wtp"]):
        without = [names_of(item, K)[c] for c in range(K) if not price["wtp"][c]]
        more.append(de_en("Ohne Zahlungsbereitschaft: {which} (Preiskoeffizient nicht deutlich negativ).",
                          "Without willingness to pay: {which} (price coefficient not clearly negative).",
                          which=", ".join(without)))
    if any(seps):
        more.append(de_en("In einigen Segmenten wurde eine Stufe nie oder immer gewählt, wo es einen Unterschied "
                          "machte (markiert „begrenzt“): Ihren Teilnutzen begrenzt nur die schwache Priori; das Merkmal "
                          "steht dort ohne Standardfehler und ohne Zahlungsbereitschaft.",
                          "In some segments a level was never or always chosen where it made a difference (marked "
                          "“bounded”): only the weak prior bounds its part-worth; the attribute is shown there without "
                          "standard errors and without willingness to pay."))
    return {"attributes": rows, "price": price, "asc": asc, "wtp_decimals": 0 if wtps and max(wtps) >= 10 else 2,
            "importance_bars": _importance_bars(rows, price, K), "more_warnings": more}


def _cva_view(item, res, sol, K, lang):
    """Part-worths and importances per segment of a ratings-based conjoint, in points of the answer scale as in its
    results (cva_analysis.part_worths of the class coefficients without the constant)."""
    from app import cva_analysis

    attrs = item.kind.attributes(item, lang)
    levels = res["levels"]
    p = 1 + sum(n - 1 for n in levels)
    B, cov = sol["B"], sol["cov"]
    rows = [{"code": a["code"], "label": a["label"], "index": j,
             "levels": [{"label": a["level_labels"][lv], "values": []} for lv in range(levels[j])], "importance": []}
            for j, a in enumerate(attrs)]
    vectors, constants = [], []
    for c in range(K):
        b = np.asarray(B[c], dtype=float)
        pw = cva_analysis.part_worths(list(b[1:]), levels)
        V = None if cov is None else np.asarray(cov)[c * p:(c + 1) * p, c * p:(c + 1) * p][1:, 1:]
        imp = cva_analysis.importances(pw)
        start, vec = 0, []
        for j, row in enumerate(rows):
            n = levels[j]
            for lv, level in enumerate(row["levels"]):
                se = None
                if V is not None:  # the last level is minus the sum of the others (effects coding)
                    w = np.zeros(len(V))
                    if lv < n - 1:
                        w[start + lv] = 1.0
                    else:
                        w[start:start + n - 1] = -1.0
                    se = math.sqrt(max(float(w @ V @ w), 0.0))
                level["values"].append({"beta": pw[j][lv], "se": se, "separated": False, "wtp": None, "wtp_lo": None,
                                        "wtp_hi": None})
                vec.append(pw[j][lv])
            row["importance"].append(imp[j])
            start += n - 1
        vectors.append(vec)
        constants.append(float(b[0]))
    more = _scale_hints(vectors, names_of(item, K)) if K > 1 else []
    return {"attributes": rows, "price": None, "asc": None, "wtp_decimals": 2, "constants": constants,
            "sigma": sol.get("sigma"), "importance_bars": _importance_bars(rows, None, K), "more_warnings": more}


def _importance_bars(rows, price, K):
    """Per attribute (and the price) the importance of every segment, for grouped bars (share of the widest)."""
    groups = [(r["label"], r["importance"]) for r in rows]
    if price is not None and any(x is not None for x in price["importance"]):
        groups.append((price["label"], price["importance"]))
    top = max([x for _, v in groups for x in v if x is not None] + [1e-9])
    return [{"label": label, "bars": [{"segment": c, "value": v[c], "width": None if v[c] is None else round(100 * v[c] / top, 1)}
                                      for c in range(K)]} for label, v in groups]


def _maxdiff_view(item, res, sol, K, k, lang):
    from app.maxdiff_analysis import prob_scores

    labels = item.kind.labels(item, lang)
    est = res["est"]
    m = len(est)
    M, t_vec = maxdiff_hb._matrices(m, k, res.get("thr"))
    B, cov = sol["B"], sol["cov"]
    stmts = [{"label": labels[i] if i < len(labels) else f"Aussage {i + 1}", "index": i, "values": []} for i in est]
    vectors = []
    seps = solution(res, K).get("separated") or [[]] * K
    for c in range(K):
        u = M @ B[c]
        cc = _class_cov(cov, c, k)
        se = None if cc is None else np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", M, cc, M), 0))
        score, _ = prob_scores(u, res["set_size"])
        for j, s in enumerate(stmts):
            bounded = s["index"] in seps[c]  # no finite utility: no standard error, as the pooled MNL (W-2)
            s["values"].append({"util": float(u[j]), "se": None if se is None or bounded else float(se[j]),
                                "score": float(score[j]), "separated": bounded})
        vectors.append(list(u))
    overall = np.array([[v["score"] for v in s["values"]] for s in stmts]) @ np.asarray(sol["pi"])
    for s, o in zip(stmts, overall):
        s["overall"] = float(o)
    stmts.sort(key=lambda s: -s["overall"])
    top = max([v["score"] for s in stmts for v in s["values"]] + [1e-9])
    for s in stmts:
        for v in s["values"]:
            v["width"] = round(100 * v["score"] / top, 1)
    more = _scale_hints(vectors, names_of(item, K)) if K > 1 else []
    if any(seps):
        more.append(de_en("In einigen Segmenten war eine Aussage immer oder nie die wichtigste bzw. unwichtigste "
                          "(markiert „begrenzt“): Ihren Nutzen begrenzt nur die schwache Priori.",
                          "In some segments a statement was always or never the most or least important (marked "
                          "“bounded”): only the weak prior bounds its utility."))
    return {"statements": stmts, "set_size": res["set_size"], "more_warnings": more}


# --- stage 2: profiles, HB means per segment, holdout hit rates ---------------------------------------------------

BOLCK = ("Bolck, A., Croon, M., & Hagenaars, J. (2004). Estimating latent structure models with categorical variables: "
         "One-step versus three-step estimators. Political Analysis, 12(1), 3–27. https://doi.org/10.1093/pan/mph001")
VERMUNT = ("Vermunt, J. K. (2010). Latent class modeling with covariates: Two improved three-step approaches. Political "
           "Analysis, 18(4), 450–469. https://doi.org/10.1093/pan/mpq025")
BAKK = ("Bakk, Z., Tekle, F. B., & Vermunt, J. K. (2013). Estimating the association between latent class membership and "
        "external variables using bias-adjusted three-step approaches. Sociological Methodology, 43(1), 272–311. "
        "https://doi.org/10.1177/0081175012470644")
PATTERSON = ("Patterson, B. H., Dayton, C. M., & Graubard, B. I. (2002). Latent class analysis of complex sample survey "
             "data: Application to dietary data. Journal of the American Statistical Association, 97(459), 721–741. "
             "https://doi.org/10.1198/016214502388618456")


def enriched_key(item, stored):
    """What the profiles, HB means and hit rates of a view rest on: the result, the shown K, the chosen questions and
    the chosen covariates (a change of them asks for a new estimate)."""
    if not stored:
        return None
    return [stored.get("key"), shown_k(item, stored), box(item).get("profile"), covariate_ids(item)]


def profile_choices(item, entries):
    """[(item id, label)] of the questions of the page that can describe the segments: answers by frequency, order or
    number, multiple choice and scale scores, outside loops, not the question itself."""
    from app import export
    from app.items.base import plain

    lang = item.survey.default_language
    out = []
    for e in entries:
        other = e["item"]
        if other.id == item.id or export._loop_rows(other):
            continue
        usable = e.get("multi") or e.get("scale") or e.get("subscales") or any(
            c.get("type") in ("freq", "ord", "num") for c in e.get("columns") or [])
        if usable:
            out.append((other.id, f"{other.code}: {plain(other.text('question', lang))[:70]}"))
    return out


def profile_ids(item, choices):
    """The chosen questions, or without a choice the first PROFILE_DEFAULT the results can be split by."""
    known = {i for i, _ in choices}
    saved = box(item).get("profile")
    if saved is not None:
        return [i for i in saved if i in known][:PROFILE_MAX]
    from app import compare

    groupers = [i for i, _ in compare.questions(item.survey)]
    return [i for i in groupers if i in known][:PROFILE_DEFAULT]


def _weighted_shares(values, W, order):
    """Per answer key in `order` the share per segment: sum of the weights of the rows with that answer over the sum
    of the weights of all rows with an answer. W: rows x segments. With BCH weights (negative ones possible) a share can
    leave [0, 1]: it is set to the edge and marked ("clipped", audit 2026-10-05, S-1). "count": persons with the
    answer."""
    have = np.array([v is not None for v in values], dtype=bool)
    total = W[have].sum(axis=0)
    rows = []
    for key, label in order:
        hit = np.array([v == key for v in values], dtype=bool)
        cells = W[hit].sum(axis=0)
        shares, clipped = [], []
        for c, t in zip(cells, total):
            x = float(c / t) if t > 0 else None
            out = x is not None and not -1e-12 <= x <= 1 + 1e-12
            shares.append(None if x is None else min(max(x, 0.0), 1.0))
            clipped.append(out)
        rows.append({"label": label, "shares": shares, "clipped": clipped, "count": int(hit.sum())})
    return rows, total


def _weighted_mean(values, W):
    x = np.array([np.nan if v is None else float(v) for v in values])
    ok = ~np.isnan(x)
    out = []
    for c in range(W.shape[1]):
        w = W[ok, c]
        s = w.sum()
        if s <= 0:
            out.append({"mean": None, "sd": None})
            continue
        m = float((w * x[ok]).sum() / s)
        out.append({"mean": m, "sd": float(np.sqrt(max((w * (x[ok] - m) ** 2).sum() / s, 0.0)))})
    return out, W[ok].sum(axis=0)


def bch_weights(T):
    """BCH weights (Bolck, Croon & Hagenaars, 2004; Vermunt, 2010): with m_n the most likely segment of person n,
    D[s, k] = sum_n tau_ns [m_n = k] / sum_n tau_ns is the probability to be put into k when one belongs to s; person
    n counts for segment s with the weight (D^-1)[m_n, s], which undoes the misclassification on average. Weights can
    be negative. None if D cannot be inverted."""
    T = np.asarray(T, dtype=float)
    K = T.shape[1]
    m = T.argmax(axis=1)
    D = np.column_stack([T[m == k].sum(axis=0) for k in range(K)]) / np.maximum(T.sum(axis=0), 1e-12)[:, None]
    try:
        Dinv = np.linalg.inv(D)
    except np.linalg.LinAlgError:
        return None
    if not np.all(np.isfinite(Dinv)) or np.linalg.cond(D) > 1e8:
        return None
    return Dinv[m, :]


def wald(Y, W):
    """Wald test that the BCH-weighted means of the columns of Y (persons x q; a row with a missing value left out)
    are the same in every segment (W: persons x segments, from bch_weights). Variance from the influence of every
    person on all segment means (sandwich over persons), so persons that count for several segments are no problem.
    The influence is taken around the common mean, as the null hypothesis has it, not around the segment means: with
    a rare answer a small segment often shows (nearly) no variance of its own, and the test with the variance around
    its own mean rejected a true null hypothesis in 9 to 26 % instead of 5 % (audit 2026-10-05, S-2; an answer that
    follows the segment exactly even left no variance and no test). The statistic divided by its degrees of freedom
    is referred to F(df, n - K) instead of chi-square / df (Askurious's choice after simulation, rates 3.5 to 6 %).
    {"F", "df", "df2", "p", "n"} or None."""
    from app import group_tests as gt

    Y = np.asarray(Y, dtype=float)
    ok = ~np.isnan(Y).any(axis=1)
    Y, W = Y[ok], np.asarray(W, dtype=float)[ok]
    n, q = Y.shape
    K = W.shape[1]
    S = W.sum(axis=0)
    if n < 2 * K or K < 2 or np.any(S <= 1e-9):
        return None
    M = (W.T @ Y) / S[:, None]                                            # segments x q
    centre = Y.mean(axis=0)  # the common mean under H0 (the BCH weights of a person sum to 1 over the segments)
    Psi = (W[:, :, None] * (Y - centre)[:, None, :] / S[None, :, None]).reshape(n, K * q)
    V = Psi.T @ Psi
    C = np.zeros(((K - 1) * q, K * q))                                    # segment s minus segment 1, per column
    for s in range(1, K):
        for j in range(q):
            C[(s - 1) * q + j, s * q + j] = 1.0
            C[(s - 1) * q + j, j] = -1.0
    d = C @ M.ravel()
    Vd = C @ V @ C.T
    df = int(np.linalg.matrix_rank(Vd, tol=1e-10 * max(float(np.abs(Vd).max(initial=0.0)), 1e-300)))
    if df == 0:
        return None
    F = float(d @ np.linalg.pinv(Vd, rcond=1e-10) @ d) / df
    df2 = int(n - K)
    return {"F": F, "df": df, "df2": df2, "p": gt.f_sf(F, df, df2), "n": int(n)}


MIN_EXPECTED = 5  # a cell segment x answer of a test expects fewer persons: rare answers merged (audit 2026-10-05, S-2)


def _test_groups(values, keys, B, ordered):
    """The answers of a categorical BCH test, merged until every cell segment x answer expects at least MIN_EXPECTED
    persons if the segments answered alike (size of the segment from the BCH weights times the share of the answer,
    as for the chi-square test): [[key, ...], ...], or None when even two groups stay too small. Ordered answers join
    their smaller neighbour, nominal ones the rarest joins the next rarest. Answers nobody gave form no cell."""
    have = np.array([v is not None for v in values], dtype=bool)
    n = int(have.sum())
    if not n:
        return None
    smallest = float(np.asarray(B, dtype=float)[have].sum(axis=0).min())
    counts = {k: sum(1 for v in values if v == k) for k in keys}
    groups = [[k] for k in keys if counts[k]]

    def size(g):
        return sum(counts[k] for k in g)

    while len(groups) >= 2 and smallest * min(size(g) for g in groups) / n < MIN_EXPECTED:
        if len(groups) == 2:
            return None
        i = min(range(len(groups)), key=lambda j: (size(groups[j]), j))
        near = [j for j in (i - 1, i + 1) if 0 <= j < len(groups)] if ordered else [j for j in range(len(groups)) if j != i]
        j = min(near, key=lambda j: (size(groups[j]), j))
        lo, hi = sorted((i, j))
        groups[lo] = groups[lo] + groups[hi]
        del groups[hi]
    return groups if len(groups) >= 2 else None


def _category_test(values, order, B, ordered):
    """Wald test (BCH) that the segments give the answers of `order` alike, over the groups of _test_groups: the
    indicators of all groups but the last ("merged": the groups of several answers); {"small": True} when the cells
    are too small for any test."""
    groups = _test_groups(values, [k for k, _ in order], B, ordered)
    if groups is None:
        return {"small": True}
    where = {k: g for g, keys in enumerate(groups) for k in keys}
    Y = [[np.nan] * (len(groups) - 1) if where.get(v) is None else [float(where[v] == g) for g in range(len(groups) - 1)]
         for v in values]
    out = wald(Y, B)
    if out is None:
        return None
    labels = dict(order)
    out["merged"] = [" + ".join(str(labels[k]) for k in keys) for keys in groups if len(keys) > 1]
    return out


def _blocks(entry, table, W, B=None):
    """Profile blocks of one question: per column shares or means per segment. With B (BCH weights, unweighted pages
    only) the shares and means are BCH-weighted, which undoes the misclassification (shares set to [0, 1] and marked
    where they leave it), and every block carries a Wald test that the segments answer alike; without B weighted
    with W (rows x segments: the membership probabilities, proportional assignment, which understates the
    differences; audit 2026-10-05, S-1). The answer with a text field ("Anderes, und zwar") is a row and a category of
    its own (S-5)."""
    from app import compare, results as res, scales
    from app.items.base import plain

    item = entry["item"]
    V = B if B is not None else W
    blocks = []
    if entry.get("multi"):
        lang = item.survey.default_language
        for o in item.kind.options(item, lang):
            col = f"{item.code}_{o['code']}"
            vals = [x if x in (0, 1) else None for x in (res._num(r.get(col)) for r in table)]
            rows, total = _weighted_shares(vals, V, [(1, plain(o["label"]))])
            blocks.append({"kind": "shares", "label": plain(o["label"]), "code": col, "rows": rows, "n": total.tolist(),
                           "multi": True,
                           "test": _category_test(vals, [(1, de_en("angekreuzt", "ticked")),
                                                         (0, de_en("nicht angekreuzt", "not ticked"))], B, False)
                           if B is not None else None})
    bases = {c for c, _ in item.kind.export_columns(item)}
    for column in entry.get("columns") or []:
        kind = column.get("type")
        if kind not in ("freq", "ord", "num") or column["code"] not in bases:
            continue
        gone = {str(m) for m in item.kind.missing_codes(item, column["code"])}
        values = compare._column_values(item, table, column["code"], column["code"], None, gone, kind)
        if kind in ("freq", "ord"):
            source = column.get("rows") or []
            order = [(r["value"], r.get("label") or r["value"]) for r in source]
            rest = {r["value"] for r in source if r.get("rest")}
            if kind == "freq":  # nominal answers: most frequent first, ordinal ones in their order; "Anderes" last
                counts = {}
                for v in values:
                    if v is not None:
                        counts[v] = counts.get(v, 0) + 1
                order.sort(key=lambda kv: (kv[0] in rest, -counts.get(kv[0], 0)))
            rows, total = _weighted_shares(values, V, order)
            rows = [r for r in rows if r["count"]]  # answers nobody gave: left out
            test = None
            if B is not None and len(order) > 1:
                test = _category_test(values, order, B, kind == "ord")
            blocks.append({"kind": "shares", "label": column.get("label") or "", "code": column["code"], "rows": rows,
                           "n": total.tolist(), "test": test})
        else:
            per, total = _weighted_mean(values, V)
            blocks.append({"kind": "mean", "label": column.get("label") or "", "code": column["code"], "per": per,
                           "n": total.tolist(), "unit": column.get("unit"),
                           "test": wald([[np.nan] if v is None else [float(v)] for v in values], B) if B is not None else None})
    if entry.get("scale") or entry.get("subscales"):
        for name, codes in scales.groups(item):
            scores = compare._scale_scores(item, codes, table)
            per, total = _weighted_mean(scores, V)
            blocks.append({"kind": "mean", "label": de_en("Subskala „{name}“", "Subscale “{name}”", name=name) if name
                           else de_en("Skalenwert", "Scale score"), "code": item.code,
                           "per": per, "n": total.tolist(), "unit": None,
                           "test": wald([[np.nan] if v is None else [float(v)] for v in scores], B) if B is not None else None})
    return blocks


def enrich(item, view, entries, table, weighting=None):
    """Add to a current view what needs the answers of the page: the profiles over the chosen questions (shares and
    means with BCH weights, which undo the misclassification, Vermunt, 2010; without BCH weights and with a weighting
    of the page weighted with the membership probabilities: proportional assignment, which understates the
    differences, audit 2026-10-05, S-1), the HB means per segment and the holdout hit rate of every K. Kept with the
    results."""
    stored = _stored(item)
    if not view.get("result") or not stored:
        return
    K = shown_k(item, stored)
    sol = solution(stored, K)
    tau = {pid: row for pid, row in zip(stored["pids"], sol["tau"])}
    rows = [r for r in table if r.get("pid") in tau]
    view["enriched"] = enriched_key(item, stored)
    choices = profile_choices(item, entries)
    chosen = profile_ids(item, choices)
    from app import compare

    groupers = {i for i, _ in compare.questions(item.survey)}
    by_entry = {e["item"].id: e for e in entries}
    view["cov_choices"] = [(i, label) for i, label in choices
                           if i in groupers or any(c.get("type") == "num" and c.get("code") == by_entry[i]["item"].code
                                                   for c in by_entry[i].get("columns") or [])
                           ] if item.type != "cva" else []  # the latent class regression has no covariates
    view["cov_ids"] = [m["item"] for m in stored.get("covariates") or []]
    view.update(profile_choices=choices, profile_ids=chosen, profile=[], profile_weighted=None, sizes_weighted=None)
    if rows and K > 1:
        T = np.array([tau[r["pid"]] for r in rows], dtype=float)
        by_id = {e["item"].id: e for e in entries}
        label = dict(choices)
        B = bch_weights(T)
        view["profile"] = [{"id": i, "label": label[i], "blocks": _blocks(by_id[i], rows, T, B)} for i in chosen]
        view["bch"] = B is not None
        view["clipped"] = any(x for q in view["profile"] for b in q["blocks"] for r in b.get("rows") or []
                              for x in r.get("clipped") or [])
        if weighting and weighting.get("pids"):
            w = np.array([float((weighting.get("pids") or {}).get(r["pid"]) or 0.0) for r in rows])
            view["profile_weighted"] = [{"id": i, "label": label[i], "blocks": _blocks(by_id[i], rows, T * w[:, None])}
                                        for i in chosen]
            # segment sizes with the weights of the page (Patterson, Dayton & Graubard, 2002)
            total = float(w.sum())
            view["sizes_weighted"] = [float((T[:, c] * w).sum() / total) if total else None for c in range(K)]
    view["hb_means"] = _hb_means(item, stored, sol, K)
    view["hits"] = _hits(item, stored, table)


def _hb_means(item, stored, sol, K):
    """Means of the individual HB values per segment, weighted with the membership probabilities: CBC part-worths
    per level, MaxDiff probability scores; None without a current HB result of the same persons (and for the
    ratings-based conjoint, whose segments already are in points of the scale)."""
    if K < 2 or item.type == "cva":
        return None
    if item.type == "maxdiff":
        hb = maxdiff_hb.current(item)
        key = "prob"
    else:
        from app import conjoint_hb

        hb = conjoint_hb.current(item)
        key = "util"
    if not hb:
        return None
    index = {pid: n for n, pid in enumerate(hb["pids"])}
    pairs = [(index[pid], t) for pid, t in zip(stored["pids"], sol["tau"]) if pid in index]
    if len(pairs) < lc.MIN_PERSONS:
        return None
    U = np.array([hb[key][i] for i, _ in pairs], dtype=float)
    T = np.array([t for _, t in pairs], dtype=float)
    means = (T.T @ U) / T.sum(axis=0)[:, None]          # segments x values
    lang = item.survey.default_language
    if item.type == "maxdiff":
        labels = item.kind.labels(item, lang)
        rows = [{"label": labels[i] if i < len(labels) else f"Aussage {i + 1}", "values": means[:, j].tolist()}
                for j, i in enumerate(hb["est"])]
        rows.sort(key=lambda r: -float(np.dot(r["values"], sol["pi"])))
        return {"kind": "maxdiff", "rows": rows, "persons": len(pairs)}
    attrs = item.kind.attributes(item, lang)
    rows = [{"label": attrs[a]["label"], "levels": [{"label": attrs[a]["level_labels"][lv],
                                                      "values": means[:, start + lv].tolist()} for lv in range(n)]}
            for a, start, n in hb["spans"]]
    return {"kind": "cbc", "rows": rows, "persons": len(pairs)}


def _hits(item, stored, table):
    """{K: hit rate} over the holdout tasks: per person the mixture of the segments' choice probabilities
    (sum_c tau_nc P_c), the alternative with the highest one against the choice (ties in equal parts). None
    without holdouts."""
    from app import holdouts

    tasks = []  # ({pid: chosen index}, utility rows of the alternatives in the coding of the result)
    if item.type in ("acbc", "cva"):
        return None  # no holdouts for ACBC (W13); those of the CVA are rated, see cva_analysis.holdout_view
    if item.type == "maxdiff":
        col = {int(i): c for c, i in enumerate(stored["est"])}
        k = stored["k"]
        M, _ = maxdiff_hb._matrices(len(stored["est"]), k, stored.get("thr"))
        for h, s in holdouts.maxdiff_active(item):
            given = holdouts._answers(table, f"{item.code}_ho{h + 1}_best")
            chosen = {pid: s.index(v - 1) for pid, v in given.items() if v - 1 in s}
            # a statement never shown in the tasks has no utility: average (0, the utilities are centred)
            X = np.array([M[col[i]] if i in col else np.zeros(k) for i in s])
            tasks.append((chosen, X))
    else:
        from app import conjoint_hb

        coding = conjoint_hb._coding(stored["coding"])
        kind = item.kind
        pa = kind.price_attribute(item)
        numbers = kind.level_numbers(item, pa) if pa is not None and coding.price is not None else None
        J = int(item.config["alternatives"])
        for h, profiles in holdouts.cbc_active(item):
            given = holdouts._answers(table, f"{item.code}_ho{h + 1}")
            X = [coding.row(p, numbers[p[pa]] if numbers and p[pa] is not None else None) for p in profiles]
            if coding.asc is not None and item.config.get("none"):  # dual response: holdouts are forced choices
                X.append(coding.asc_row())
            chosen = {pid: (J if v == 0 else v - 1) for pid, v in given.items()}
            tasks.append(({pid: c for pid, c in chosen.items() if 0 <= c < len(X)}, np.array(X)))
    if not tasks:
        return None
    index = {pid: n for n, pid in enumerate(stored["pids"])}
    out = {}
    for s in stored["solutions"]:
        B, tau = np.array(s["B"]), np.array(s["tau"])
        hits = n = 0.0
        for chosen, X in tasks:
            V = X @ B.T                                        # alternatives x segments
            P = np.exp(V - V.max(axis=0))
            P /= P.sum(axis=0)
            for pid, c in chosen.items():
                if pid not in index:
                    continue
                mix = P @ tau[index[pid]]
                best = np.flatnonzero(mix >= mix.max() - 1e-12)
                hits += (c in best) / len(best)
                n += 1
        out[s["K"]] = hits / n if n else None
    return out if any(v is not None for v in out.values()) else None
