"""Individual MaxDiff utilities by hierarchical Bayes (app/hb.py), stored per item.

Coding as in the aggregate MNL (app/maxdiff_analysis.py): per set best among the shown statements
(+1) and worst among the rest (-1), sequential; with the anchoring question per statement a choice
between it and the threshold. The last shown statement is the reference; the prior covariance of the
parameters is I + 11' (differences to the reference of statements with variance 1), so no statement is
treated differently from the others. Per person: zero-centred utilities (posterior mean, mean over the
statements = 0) and probability scores p = exp(u) / (exp(u) + a - 1) (as in app/maxdiff_analysis.py: the logit
probability against a - 1 statements of mean utility 0), rescaled to sum 100, computed at
every kept draw and averaged (posterior mean of the score, not the score of the mean utilities); means
over persons are taken after the conversion. The interval of a mean score is the 95 % posterior interval
(mean over persons at every kept draw); the spread across persons is shown separately.

HB takes seconds to minutes, so it runs on request ("Individuelle Nutzen schätzen (Hierarchical Bayes)") in a background thread
and the result is a JSON file in the instance folder (UPLOAD_FOLDER/hb/survey_<id>/item_<id>.json, no
database change). The key is a hash over the design, the included persons' answers and the settings: a
result whose key differs from the current data is stale. A marker file shows a running estimation; it
carries a heartbeat, so a run killed with its process (Passenger) is shown as aborted after a while.
Settings from the app config: HB_BURN, HB_DRAWS, HB_THIN, HB_SEED; HB_SYNC runs in the request (tests).
"""
import hashlib
import json
import os
import threading
import time
import traceback
from datetime import datetime, timezone

import numpy as np

from app import hb, hb_convergence
from app.maxdiff_analysis import (anchor_sets, axis, chart, components, counts, disconnected_note, parameters,
                                  persons_from_table, prob_scores, task_sets)
from app.lang import de_en
from app.units import fixed

FORMAT = 5  # 2: proper prior on mu, separated statements named; 3: log-likelihood trace, split-R-hat and ESS;
            # 4: the sampler of the audit of 2026-10-05 (W-2), kept draws per person for the simulator (W-1), convergence
            # also of the shown mean scores; 5: every monitored quantity needs finite convergence diagnostics
STALE_HEARTBEAT = 300   # seconds without a sign of life: the run is taken as aborted
LOCK_STALE = 60         # seconds: a start lock this old is left over from a killed process (claim)
RLH_FACTOR = 1.2        # RLH below this times the chance level: possibly random clicking (Festlegung von Askurious)
TOP = 3                 # "in the personal top 3"
MAX_DOTS = 400          # above this many persons the distribution chart shows boxes only


def settings(app=None):
    """Default length 5000 burn-in + 20 000 iterations (audit 2026-10-05, H-2: with 10 000 the chain failed the rule of
    app/hb_convergence.py on healthy data in 8 of 8 runs, so nearly every estimate needed a longer run)."""
    from flask import current_app

    c = (app or current_app).config
    return {"burn": int(c.get("HB_BURN", 5000)), "draws": int(c.get("HB_DRAWS", 20000)),
            "thin": int(c.get("HB_THIN", 10)), "seed": int(c.get("HB_SEED", 1))}


# --- input ------------------------------------------------------------------------------------------

def model_input(item, table, config=None):
    """Everything the estimation needs, as plain data, and the key of the included answers.
    None with fewer than hb.MIN_PERSONS persons with an answered set, fewer than two shown statements or statements
    that fall apart into groups never shown together (see _prepare)."""
    return _prepare(item, table, config)[0]


def _prepare(item, table, config=None):
    """(model_input or None, the note on a design that is not connected or None). Statements that fall apart into
    groups never shown together get no HB estimate, as they get no aggregate MNL (audit 2026-10-05, H-1): between the
    groups only the prior would set the distance, and a longer chain does not change that."""
    c = item.config
    versions = (c.get("design") or {}).get("versions") or []
    n_items = len(c.get("statements", []))
    rows = persons_from_table(item, table)
    anchored = bool(c.get("anchor")) and any(a is not None for _, _, _, a in rows)
    answers = [(v, tasks) for _, v, tasks, _ in rows]
    blocks = components(n_items, versions, answers)
    if len(blocks) > 1:
        return None, disconnected_note(blocks, anchored)
    est, col, k, thr = parameters(n_items, versions, answers, anchored)
    if not k:
        return None, None
    persons = []
    for pid, v, tasks, anchor in rows:
        sets = task_sets(versions, v, tasks, col, k)
        if not sets:
            continue
        persons.append({"pid": pid, "v": v, "tasks": {str(t): list(bw) for t, bw in sorted(tasks.items())},
                        "anchor": anchor if thr is not None else None})
    if len(persons) < hb.MIN_PERSONS:
        return None, None
    set_size = len(versions[0][0]) if versions and versions[0] else c.get("set_size") or 4
    cfg = config or settings()
    data = {"format": FORMAT, "design": (c.get("design") or {}).get("sig"), "versions": versions, "n": n_items,
            "persons": persons}
    digest = hashlib.sha1(json.dumps({**data, "settings": cfg}, sort_keys=True).encode()).hexdigest()
    # data_key: the answers without the settings, so a longer run on the same answers is known as current (revisit)
    data_key = hashlib.sha1(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return {"key": digest, "data_key": data_key, "versions": versions, "n_items": n_items, "est": est, "col": col, "k": k,
            "thr": thr, "set_size": set_size, "persons": persons, "settings": cfg}, None


def _matrices(m, k, thr):
    """M (statements x params): zero-centred utilities u = M beta; t_vec: threshold minus the mean utility."""
    M = np.zeros((m, k))
    M[np.arange(m - 1), np.arange(m - 1)] = 1.0
    M[:, :m - 1] -= 1.0 / m
    t_vec = None
    if thr is not None:
        t_vec = np.zeros(k)
        t_vec[thr] = 1.0
        t_vec[:m - 1] -= 1.0 / m
    return M, t_vec


def run(inp, progress=None):
    """Estimate from model_input(); returns the JSON-ready result (without pool and item)."""
    versions, est, col, k, thr = inp["versions"], inp["est"], inp["col"], inp["k"], inp["thr"]
    col = {int(i): c for i, c in col.items()}
    a = inp["set_size"]
    person_sets, bw_sets = [], []
    for p in inp["persons"]:
        tasks = {int(t): tuple(bw) for t, bw in p["tasks"].items()}
        sets = task_sets(versions, p["v"], tasks, col, k)
        bw_sets.append(sets)
        person_sets.append(sets + (anchor_sets(p["anchor"], est, col, k, thr) if p["anchor"] is not None else []))
    m = len(est)
    M, t_vec = _matrices(m, k, thr)
    # what the data leave open (as in maxdiff_analysis.mnl): there the prior sets the values
    shown, best, worst, _ = counts(inp["n_items"], versions,
                                   [(p["v"], {int(t): tuple(bw) for t, bw in p["tasks"].items()}) for p in inp["persons"]])
    separated = [int(i) for i in est if best[i] == shown[i] or worst[i] == shown[i]]
    # as in the pooled model: a separated statement is left out of the scale, the scores are centred on and sum to
    # 100 over the other statements; its own score follows with the same factor, outside the sum (audit data storage
    # and calculations 2026-10-21, D5). Without separated statements this is the scale over all statements.
    keep = [j for j, i in enumerate(est) if i not in separated]
    if len(keep) < 2:
        keep = list(range(m))

    def scores(B):  # persons x (statements [+ threshold])
        U = B @ M.T
        shift = U[:, keep].mean(axis=1, keepdims=True)
        p = np.exp(U - shift) / (np.exp(U - shift) + a - 1)
        f = 100 / p[:, keep].sum(axis=1, keepdims=True)
        if t_vec is None:
            return p * f
        t = B @ t_vec - shift[:, 0]
        return np.column_stack([p * f, np.exp(t) / (np.exp(t) + a - 1) * f[:, 0]])

    prior = np.eye(k) + np.ones((k, k))  # differences to the reference of iid statements with variance 1
    cfg = inp["settings"]
    r = hb.estimate(person_sets, k, burn=cfg["burn"], draws=cfg["draws"], thin=cfg["thin"], seed=cfg["seed"],
                    prior_cov=prior, summary=lambda B: scores(B).mean(axis=0), person_stat=scores, progress=progress,
                    # spread between persons in the scale the page shows: the variance of the zero-centred utilities,
                    # not of the differences to the reference (about twice as large; audit 2026-10-05, H-3)
                    spread=lambda S: [np.mean(np.diag(M @ S @ M.T))])
    beta = r["beta"]
    util = beta @ M.T
    # convergence judged on the zero-centred statement means (not on the differences to the reference)
    diag = hb_convergence.diagnose(r["mu_trace"] @ M.T, r["ll_trace"], geweke_ok=r["converged"], shown=r["summaries"])
    thr_util = None if t_vec is None else beta @ t_vec
    # scores per person: posterior means (so the mean over persons lies inside its posterior interval)
    prob = r["person_stat"][:, :m]
    thr_prob = r["person_stat"][:, m] if t_vec is not None else None
    # RLH over the best and worst choices only: the anchor answers are binary and would lift it
    data = hb.Data(bw_sets, k)
    rlh = hb.rlh(data, beta)
    chance = np.exp(data.chance_loglik() / data.sets_per_person)
    S = r["summaries"]
    lo, hi = np.percentile(S, [2.5, 97.5], axis=0) if S is not None and len(S) else (None, None)
    ticks = {bool(x) for p in inp["persons"] if p["anchor"] is not None for x in p["anchor"]}
    anchor_flat = thr is not None and len(ticks) < 2

    def rnd(x, d=4):
        return None if x is None else np.round(np.asarray(x, dtype=float), d).tolist()

    return {"format": FORMAT, "key": inp["key"], "data_key": inp.get("data_key"), "settings": cfg,
            "n_items": inp["n_items"], "est": est,
            "separated": separated, "anchor_flat": anchor_flat, "scores_without_separated": len(keep) < m,
            "set_size": a, "anchored": thr is not None, "pids": [p["pid"] for p in inp["persons"]],
            "util": rnd(util), "prob": rnd(prob, 3), "thr_util": rnd(thr_util), "thr_prob": rnd(thr_prob, 3),
            "rlh": rnd(rlh), "chance": rnd(chance), "mean_lo": rnd(None if lo is None else lo[:m], 3),
            "mean_hi": rnd(None if hi is None else hi[:m], 3),
            "thr_lo": None if lo is None or thr is None else round(float(lo[m]), 3),
            "thr_hi": None if hi is None or thr is None else round(float(hi[m]), 3),
            "mu": rnd(r["mu"]), "sigma_diag": rnd(r["sigma_diag"]), "sigma_util": round(float(r["spread"][0]), 4),
            "acceptance": r["acceptance"],
            "converged": diag["diag_ok"], "geweke_ok": r["converged"], "checked": r["checked"],
            "geweke_ll": r["geweke_ll"], "geweke_mu_flagged": r["geweke_mu_flagged"], "ll_halves": r["ll_halves"],
            "kept": r["kept"], "sets": r["sets"], "seconds": round(r["seconds"], 1), **diag,
            # a sample of the kept draws of every person's betas, for "Auswahl aus Aussagen" and the holdouts (W-1)
            "draws": hb.pack_draws(r["draws"])}


def draw_utilities(result):
    """Zero-centred utilities per kept draw and person (draws x persons x statements in result["est"] order); without
    stored draws one "draw", the posterior means."""
    B = hb.unpack_draws(result.get("draws"))
    if B is None:
        return np.array(result["util"], dtype=float)[None]
    m = len(result["est"])
    M, _ = _matrices(m, B.shape[2], None)
    return B @ M.T


# --- storage ------------------------------------------------------------------------------------------

def folder(item, app=None):
    from flask import current_app

    base = (app or current_app).config["UPLOAD_FOLDER"]
    return os.path.join(base, "hb", item.block.survey.uid)  # removed with the survey (lifecycle.delete)


def _paths(item, app=None):
    d = folder(item, app)
    paths = os.path.join(d, f"item_{item.id}.json"), os.path.join(d, f"item_{item.id}.run.json")
    _adopt_old_result(item, paths[0], app)
    return paths


def _adopt_old_result(item, path, app=None):
    """Results used to lie under the survey's number. Move this question's result to the survey's own folder,
    unless it is older than the survey: then a deleted survey with the same number left it."""
    from flask import current_app

    survey = item.block.survey
    old = os.path.join((app or current_app).config["UPLOAD_FOLDER"], "hb", f"survey_{survey.id}", f"item_{item.id}.json")
    try:
        if os.path.exists(path) or not os.path.isfile(old):
            return
        if datetime.fromtimestamp(os.path.getmtime(old), timezone.utc).replace(tzinfo=None) >= survey.created_at:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            os.replace(old, path)
    except OSError:
        pass


def _write(path, data, make=True):
    """Write the JSON file at once for every reader; make=False: not into a folder that is gone (a run whose survey
    was deleted meanwhile, C3), FileNotFoundError then."""
    if make:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)  # readers never see half a file


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def load(item):
    """The stored result of the question, read once per request (performance audit 2026-10-06, P6: the results page
    asked for it up to five times, at 6 MB of JSON with 5000 persons). Keyed by the file's time and size, so a result
    written in between (HB_SYNC) is read anew. Shared within the request: callers must not change it."""
    from flask import g, has_request_context

    path = _paths(item)[0]
    if not has_request_context():
        return _read(path)
    try:
        st = os.stat(path)
    except OSError:
        return None
    cache = g.setdefault("_hb_load", {})
    key = (path, st.st_mtime_ns, st.st_size)
    if key not in cache:
        cache[key] = _read(path)
    return cache[key]


def _now():
    return datetime.now(timezone.utc)


def running(item):
    """The marker of a running (or failed) estimation, None if there is none. state: running | aborted | error.
    A run on the Rechenknecht is followed, a silent one whose process died is computed once more (app/jobs.py)."""
    from app import jobs

    return jobs.marker(_paths(item))


SLOT_POLL = 1  # seconds between two looks for a free place while an estimation waits


def _slot_folder(app):
    return os.path.join(app.config["UPLOAD_FOLDER"], "hb", "_slots")


def _take_slot(app):
    """A free place among the HB_PARALLEL estimations of the whole server (security audit 2026-10-04, H3), or None:
    every one keeps a processor core busy for up to minutes, and the web requests of all surveys run in the same
    processes. The places are the files hb/_slots/<n>.lock under an exclusive lock of the operating system, so they
    count over every server process (Passenger runs several; a semaphore of one process let each of them start
    HB_PARALLEL; audit 2026-10-06, P7), and a process that dies gives its place back by itself. HB, LC and the
    conjoint estimation share them. Returns the open file that holds the place (_free_slot)."""
    limit = max(1, int(app.config.get("HB_PARALLEL", 2) or 1))
    folder = _slot_folder(app)
    os.makedirs(folder, exist_ok=True)
    for n in range(limit):
        f = open(os.path.join(folder, f"{n}.lock"), "a+b")
        if _lock_file(f):
            return f
        f.close()
    return None


def _lock_file(f):
    """An exclusive lock on the file without waiting: True if this open file holds it now. Two files opened
    separately exclude each other also within one process (flock, and LockFile on Windows)."""
    try:
        if os.name == "nt":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _free_slot(f):
    try:
        if os.name == "nt":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass
    finally:
        f.close()  # closing also ends a flock


BEAT_EVERY = 30  # seconds: the sign of life of a run between two progress steps (C1: long steps without a tick, a
#                  process at nice 19 on busy cores)


class Cancelled(Exception):
    """The run's marker is gone (a cleanup stopped it: erasure, retention, the survey deleted) or belongs to another
    run: it stops and writes nothing (code review 2026-10-07, C3)."""


def _estimate_and_store(app, item_ref, inp, pool, runner=None):
    """runner: the estimation function of another model with the same storage (app/conjoint_hb.py).
    The run marks the marker as its own (a token); every later write checks it is still its own and the folder still
    there, and the result is written only then (C3). A thread beats every BEAT_EVERY seconds (C1)."""
    import secrets

    result_path, marker_path = item_ref
    token = secrets.token_hex(8)
    marker = {"state": "running", "started": _now().isoformat(), "beat": _now().isoformat(), "progress": 0.0,
              "key": inp["key"], "pool": pool, "run": token}
    lock = threading.Lock()
    state = {"taken": False, "done": False}

    def own():
        current = _read(marker_path)
        if current is None:
            return os.path.exists(marker_path)  # there but not readable this moment: still taken as its own
        return current.get("run") == token or (not state["taken"] and "run" not in current)

    def put(**changes):
        with lock:
            if state["done"]:
                return
            if not own():
                raise Cancelled()
            marker.update(changes)
            try:
                _write(marker_path, marker, make=False)
            except FileNotFoundError as e:
                raise Cancelled() from e
            state["taken"] = True

    def progress(fraction):
        put(progress=round(fraction, 3), beat=_now().isoformat())

    stop = threading.Event()

    def heartbeat():
        while not stop.wait(BEAT_EVERY):
            try:
                put(beat=_now().isoformat())
            except Cancelled:
                return
            except OSError:
                pass

    slot = None
    try:
        put()  # this run takes the marker that claim (or a rescue) wrote
        slot = _take_slot(app)
        if slot is None:
            put(queued=True)  # the page says so at once
            beat = time.monotonic()
            while slot is None:  # waiting for a free place, with a sign of life (STALE_HEARTBEAT)
                time.sleep(SLOT_POLL)
                slot = _take_slot(app)
                if slot is None and time.monotonic() - beat >= 10:
                    beat = time.monotonic()
                    put(beat=_now().isoformat())
            marker.pop("queued", None)
            put(beat=_now().isoformat())
        threading.Thread(target=heartbeat, daemon=True, name="hb-beat").start()
        result = (runner or run)(inp, progress)
        result.update(pool=pool, created=_now().isoformat())
        with lock:
            state["done"] = True
            if not own():
                raise Cancelled()
            try:
                _write(result_path, result, make=False)  # not into a folder a cleanup removed meanwhile
            except FileNotFoundError as e:
                raise Cancelled() from e
    except Cancelled:
        app.logger.info("Estimation stopped: its marker was removed or taken by another run")
        return
    except Exception as e:  # shown on the results page instead of a silent death of the thread
        app.logger.error("HB estimation failed: %s", traceback.format_exc())
        with lock:
            state["done"] = True
            if own():
                try:
                    _write(marker_path, {**marker, "state": "error", "message": f"{type(e).__name__}: {e}"},
                           make=False)
                except OSError:
                    pass
        return
    finally:
        stop.set()
        if slot is not None:
            _free_slot(slot)
    _remove(marker_path)  # done; a marker already gone is no error (C4)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def claim(paths, inp, pool):
    """Write the marker of a new run unless one is running: True if this request may start it. A lock file made
    with O_EXCL around reading and writing the marker keeps two clicks at the same moment from starting two runs
    (audit 2026-10-05, W-9); a lock older than LOCK_STALE seconds is left over from a killed process."""
    lock = paths[1] + ".lock"
    os.makedirs(os.path.dirname(lock), exist_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            if _now().timestamp() - os.path.getmtime(lock) > LOCK_STALE:
                os.remove(lock)
                return claim(paths, inp, pool)
        except OSError:
            pass
        return False
    try:
        os.close(fd)
        marker = _read(paths[1])
        if marker and marker.get("state") == "running":
            from app import jobs

            if not jobs.stale(paths, marker):  # still beating, or its process still lives (C1)
                return False
        now = _now().isoformat()
        _write(paths[1], {"state": "running", "started": now, "beat": now, "progress": 0.0, "key": inp["key"],
                          "pool": pool})
        return True
    finally:
        try:
            os.remove(lock)
        except OSError:
            pass


def start(item, table, pool, background=True, config=None):
    """Start an estimation for the persons in table (the filtered results table); pool (sources, all,
    drop, speeder) is stored with the result so the export can check it later; config: other settings than
    the app's (a longer run, app/hb_convergence.longer_settings). Returns "started", "running" (already one
    running), "current" (the stored result fits), "disconnected" (statements in groups never shown together, H-1) or
    "nodata"."""
    from flask import current_app

    app = current_app._get_current_object()
    inp, disconnected = _prepare(item, table, config)
    if inp is None:
        return "disconnected" if disconnected else "nodata"
    stored = load(item)
    if stored and stored.get("key") == inp["key"]:
        restamp(_paths(item)[0], stored, pool)
        return "current"
    from app import jobs

    if not jobs.start(app, "maxdiff_hb", _paths(item), inp, pool, background, name=f"hb-item-{item.id}"):
        return "running"
    return "started"


# --- current result for the export -------------------------------------------------------------------

def pool_table(survey, pool):
    """The results table for a stored data pool (as results.compute builds it), without HB columns. Built once per
    request and pool (performance audit 2026-10-06, P6): the check of every stored estimate (current) reads it."""
    from flask import g, has_request_context

    from app import export

    cache = g.setdefault("_hb_pool_table", {}) if has_request_context() else {}
    key = (survey.id, json.dumps(pool, sort_keys=True, default=str))
    if key in cache:
        return cache[key]
    sources = pool.get("sources") or ["real"]
    table = export.rows(survey, pool.get("all", False), sources, pool.get("speeder", export.DEFAULT_SPEEDER_PCT),
                        True, speed_sources=sources, with_hb=False, with_quality=False)
    cache[key] = table.keep(lambda r: r.get("qc_marked") != 1) if pool.get("drop") else table
    return cache[key]


_FITS = {}  # the verdicts of fits(), per process
_FITS_MAX = 512


def _pool_part(pool):
    """The data pool of an estimate as the fingerprint reads it."""
    from app import export

    return {"sources": pool.get("sources") or ["real"], "all": bool(pool.get("all", False)),
            "drop": bool(pool.get("drop")), "speeder": pool.get("speeder", export.DEFAULT_SPEEDER_PCT)}


def data_fingerprint(survey, pool):
    """results_store.parts of the pool, (data, setup), once per request and pool."""
    from flask import g, has_request_context

    from app import results_store

    part = _pool_part(pool)
    cache = g.setdefault("_hb_fingerprint", {}) if has_request_context() else {}
    key = (survey.id, json.dumps(part, sort_keys=True, default=str))
    if key not in cache:
        cache[key] = results_store.parts(survey, part)
    return cache[key]


def stamp(survey, pool):
    """The pool of a new estimate with the fingerprint of its data (performance audit 2026-10-09b, V1), taken before
    its table is built: stored with the result, so fits() compares it with the current one instead of building the
    table. Data that change while the table is read give a newer fingerprint than the stored one, never an older
    one. "setup" (quality rules, speeder rule, questionnaire) and the version of the program go with it: when one
    of them changed (a display setting, a holdout, a new check of the model input), it is checked as before."""
    from app import results_store

    data, setup = data_fingerprint(survey, pool)
    return {**pool, "fingerprint": data, "setup": setup, "program": results_store.program()}


_STAMP = ("fingerprint", "setup", "program")


def restamp(path, stored, pool):
    """A stored estimate that still fits (start() found the same model input) takes the fingerprint of the new
    pool, if it is the same pool, so the exports read it as current again."""
    old = stored.get("pool") or {}
    new = {k: pool.get(k) for k in _STAMP}
    if new["fingerprint"] and any(old.get(k) != v for k, v in new.items()) and _pool_part(old) == _pool_part(pool):
        try:
            _write(path, {**stored, "pool": {**old, **new}}, make=False)
        except OSError:
            pass


def fits(item, path, result, build):
    """Whether a stored estimate (HB or latent classes) still fits the data of its own data pool: build(table) is
    the model input for the pool's results table (pool_table), None if there is none; it fits if its key is the
    stored one. Building that table takes seconds with thousands of participations, and the codebook, the data page
    and every export asked again on each request (performance audit 2026-10-09, N-6). So the verdict is kept per
    process under everything the table rests on: the fingerprint of the kept results for this pool
    (results_store.fingerprint: every participation of the pool with all that is stored about it, the
    questionnaire, quality rules and speeder threshold, the program) and the file of the estimate (time, size).
    The same fingerprint, the same table, the same verdict; any new or changed participation asks again.
    An estimate started since the performance audit 2026-10-09b (V1) keeps the fingerprint of its data (stamp):
    under the same program and setup it fits while the data are the same, and new or changed data make it stale;
    nothing is rebuilt for the check. After a new version of the program or a change of the setup (quality rules,
    questionnaire) it is checked as above."""
    from app import export, lang, results_store

    pool = result.get("pool") or {}
    if pool.get("fingerprint") and pool.get("program") == results_store.program():
        # stored when it was estimated (stamp), by this version of the program: with the same setup, the same data
        # fit and other data do not, without reading them
        data, setup = data_fingerprint(item.survey, pool)
        if pool.get("setup") == setup:
            return pool["fingerprint"] == data
    try:
        st = os.stat(path)
        key = (item.id, path, st.st_mtime_ns, st.st_size, lang.current(), results_store.fingerprint(item.survey, {
            "sources": pool.get("sources") or ["real"], "all": bool(pool.get("all", False)),
            "drop": bool(pool.get("drop")), "speeder": pool.get("speeder", export.DEFAULT_SPEEDER_PCT)}))
    except OSError:
        key = None
    if key is not None and key in _FITS:
        return _FITS[key]
    inp = build(pool_table(item.survey, pool))
    verdict = inp is not None and inp["key"] == result.get("key")
    if key is not None:
        if len(_FITS) >= _FITS_MAX:
            _FITS.clear()
        _FITS[key] = verdict
    return verdict


def current(item):
    """The stored result if it still fits the data of its own data pool, else None (cached per request; whether it
    fits is kept per process for the same data, see fits)."""
    from flask import g, has_request_context

    path = _paths(item)[0]
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return None
    # columns, rows and codebook of one download each ask: check once per request
    cache = g.setdefault("_hb_current", {}) if has_request_context() else {}
    if (item.id, stamp) in cache:
        return cache[(item.id, stamp)]
    result = _read(path)
    if result and not fits(item, path, result, lambda table: model_input(item, table, result.get("settings"))):
        result = None
    cache[(item.id, stamp)] = result
    return result


def export_columns(item, result):
    """[(column, label)] of the HB results, one per statement (statement number i), the threshold, RLH."""
    code, labels = item.code, item.kind.labels(item)
    cols = [(f"{code}_hb_util{i}", f"HB-Nutzen (nullzentriert): {label}") for i, label in enumerate(labels, start=1)]
    cols += [(f"{code}_hb_prob{i}", f"HB-Wahrscheinlichkeits-Score (Summe 100): {label}")
             for i, label in enumerate(labels, start=1)]
    if result.get("anchored"):
        cols += [(f"{code}_hb_util_anchor", "HB-Nutzen der Schwelle „wirklich wichtig“"),
                 (f"{code}_hb_prob_anchor", "HB-Score der Schwelle „wirklich wichtig“")]
    cols.append((f"{code}_hb_rlh", "HB: Root Likelihood (Anpassungsgüte der Person)"))
    return cols


def export_values(item, result):
    """{pid: {column: value}} for the persons in the result."""
    code = item.code
    out = {}
    for n, pid in enumerate(result["pids"]):
        row = {f"{code}_hb_util{i + 1}": None for i in range(result["n_items"])}
        row.update({f"{code}_hb_prob{i + 1}": None for i in range(result["n_items"])})
        for j, i in enumerate(result["est"]):
            row[f"{code}_hb_util{i + 1}"] = result["util"][n][j]
            row[f"{code}_hb_prob{i + 1}"] = result["prob"][n][j]
        if result.get("anchored"):
            row[f"{code}_hb_util_anchor"] = result["thr_util"][n]
            row[f"{code}_hb_prob_anchor"] = result["thr_prob"][n]
        row[f"{code}_hb_rlh"] = result["rlh"][n]
        out[pid] = row
    return out


POOL_NAMES = {"real": "echte Teilnahmen", "test": "Testdurchläufe", "dummy": "Dummy-Daten"}
POOL_NAMES_EN = {"real": "real participations", "test": "test runs", "dummy": "dummy data"}


def pool_text(pool, survey=None, lang=None):
    """The data pool of an estimate in words for the codebook, with the speeder rule when marked participations were
    left out (audit 2026-10-05, S23/H-9: the codebook named the pool but not the speeder threshold). `lang`: the
    reader's language (None: the page's; app.items.base.reader)."""
    from app.items.base import reader

    en = reader(lang) == "en"
    names = POOL_NAMES_EN if en else POOL_NAMES
    basis = " + ".join(names.get(x, x) for x in pool.get("sources") or ["real"])
    if pool.get("all"):
        basis += ", broken-off ones too" if en else ", auch abgebrochene"
    if pool.get("drop"):
        from app import export

        if survey is not None and export.speeder_rule(survey) == "rsi":
            rule = export.rule_text(survey, "en" if en else "de")
        elif en:
            rule = f"faster than {pool.get('speeder', export.DEFAULT_SPEEDER_PCT)}% of the median time"
        else:
            rule = f"schneller als {pool.get('speeder', export.DEFAULT_SPEEDER_PCT)} % der Median-Zeit"
        basis += (f", without marked participations (quality assurance; speeders: {rule})" if en
                  else f", ohne markierte Teilnahmen (Qualitätssicherung; Speeder: {rule})")
    return basis


def describe(result, survey=None, lang=None):
    """One line for the codebook: method, date, data pool (with the speeder rule) and settings, in the reader's
    language `lang` (None: the page's)."""
    from app.items.base import reader

    s = result["settings"]
    when = datetime.fromisoformat(result["created"])
    from app.timeutil import to_local

    basis = pool_text(result.get("pool") or {}, survey, lang)
    if reader(lang) == "en":
        return (f"Hierarchical Bayes (HB-MNL, sequential best-worst coding{', with anchor question' if result.get('anchored') else ''}), "
                f"estimated on {to_local(when):%Y-%m-%d %H:%M}; {len(result['pids'])} people ({basis}); "
                f"{s['burn']} burn-in, {s['draws']} iterations, thinning {s['thin']}, seed {s['seed']}; "
                f"posterior mean per person{hb_convergence.describe(result, 'en')}")
    return (f"Hierarchical Bayes (HB-MNL, sequenzielle Best-Worst-Kodierung{', mit Ankerfrage' if result.get('anchored') else ''}), "
            f"geschätzt am {to_local(when):%d.%m.%Y %H:%M}; {len(result['pids'])} Personen ({basis}); "
            f"{s['burn']} Burn-in, {s['draws']} Iterationen, Thinning {s['thin']}, Seed {s['seed']}; "
            f"Posterior-Mittel je Person{hb_convergence.describe(result, 'de')}")


def codebook_info(item, column, result, lang=None):
    from app.items.base import in_lang, reader

    code = item.code
    note = describe(result, item.survey, lang)
    if column == f"{code}_hb_rlh":
        chance = fixed(float(np.mean(result['chance'])), 2)
        return "Zahl", in_lang(lang, "0 bis 1", "0 to 1"), [note, (
            "geometric mean of the probabilities of the best and worst choices; "
            f"chance with {result['set_size']} statements per set about {chance}" if reader(lang) == "en" else
            "geometrisches Mittel der Wahrscheinlichkeiten der Best- und Worst-Wahlen; "
            f"Zufall bei {result['set_size']} Aussagen je Set etwa {chance}".replace(".", ","))]
    if "_hb_util" in column:
        return "Zahl", in_lang(lang, "Logit, Mittel der Aussagen = 0", "logit, mean of the statements = 0"), [note]
    return "Zahl", in_lang(lang, "0 bis 100, Summe 100 über die Aussagen", "0 to 100, sum 100 over the statements"), [
        note, in_lang(lang, "exp(u) / (exp(u) + {k} − 1), je Person auf Summe 100 skaliert",
                      "exp(u) / (exp(u) + {k} − 1), scaled to a sum of 100 per person", k=result['set_size'])]


# --- results page --------------------------------------------------------------------------------------

def created_local(result):
    """When a stored result was estimated, in local time (None if unknown)."""
    from app.timeutil import to_local

    try:
        return to_local(datetime.fromisoformat(result["created"]))
    except (KeyError, TypeError, ValueError):
        return None


def view(item, table, labels):
    """Status and, with a stored result, the view model for the results page."""
    stored = load(item)
    # stale means other data: compared with the settings the stored result was made with
    inp, disconnected = _prepare(item, table, stored.get("settings") if stored else None)
    marker = running(item)
    out = {"available": inp is not None, "disconnected": disconnected, "marker": marker, "result": None, "state": None,
           "stale_created": None,
           "min_persons": hb.MIN_PERSONS, "input_key": inp["key"] if inp else None,
           "data_key": inp["data_key"] if inp else None, "seen_key": stored.get("key") if stored else None}
    return _state(item, out, stored, labels)


def revisit(item, before, labels):
    """The view again from what the kept results remember (the key of the data, results_store): the stored
    estimate and the marker are read anew, the answers not. The results page shows an estimation that starts,
    runs or ends without computing everything again (Ralf, 2026-10-03). "unknown": an estimate made with other
    settings since whose key the answers alone can tell; not so for "Neu schätzen mit doppelter Länge" on the same
    answers (data_key, Ralf, 2026-10-04)."""
    stored = load(item)
    out = {**before, "marker": running(item), "result": None, "state": None, "stale_created": None,
           "unknown": bool(stored and stored.get("key") not in (before.get("seen_key"), before.get("input_key")))}
    if out["unknown"] and before.get("data_key") and stored.get("data_key") == before["data_key"]:
        # the same answers, only other settings (a longer run): current without reading the answers again
        out["unknown"], out["input_key"] = False, stored["key"]
    return _state(item, out, stored, labels)


def _state(item, out, stored, labels):
    if out.get("disconnected"):  # no estimate for this design, an earlier one is not shown either (H-1)
        stored = None
    if stored and stored.get("format") == FORMAT:
        out["state"] = "current" if out.get("input_key") and stored["key"] == out["input_key"] else "stale"
        if out["state"] == "current":
            out["result"] = summary(stored, labels)
        else:  # an estimate for other data is not shown: after "Neu berechnen" the page starts afresh
            out["stale_created"] = created_local(stored)
    marker = out["marker"]
    if marker and marker["state"] == "running":
        out["state"] = "running"
    return out


def _draw_shares(res, m):
    """(top, above) per statement in result["est"] order: per person the share of the kept draws in which the statement
    is among the TOP highest utilities resp. above the person's threshold, then averaged over the persons (the
    posterior expectation of the indicator). Counted on the posterior means per person, the shrinkage toward the
    common mean pushed the statements on top too high and those in the middle too low (audit 2026-10-06, L-3).
    (None, None) for a result without stored draws; above None without an anchor."""
    B = hb.unpack_draws(res.get("draws"))
    if B is None or B.shape[1] != len(res["prob"]):
        return None, None
    k = B.shape[2]
    M, t_vec = _matrices(m, k, k - 1 if res.get("anchored") else None)
    U = B @ M.T                                                  # draws x persons x statements, zero-centred
    ranks = (-U).argsort(axis=2).argsort(axis=2)
    top = (ranks < TOP).mean(axis=0).mean(axis=0)
    above = None
    if t_vec is not None:
        above = (U > (B @ t_vec)[:, :, None]).mean(axis=0).mean(axis=0)
    return top, above


def summary(res, labels):
    """Rows, charts and model info of a stored result."""
    from app.timeutil import to_local

    est = res["est"]
    prob = np.array(res["prob"], dtype=float)                  # persons x statements
    util = np.array(res["util"], dtype=float)
    n = len(prob)
    mean = prob.mean(axis=0)
    ranks = (-prob).argsort(axis=1).argsort(axis=1)            # 0 = the person's highest score
    thr_prob = np.array(res["thr_prob"], dtype=float) if res.get("anchored") else None
    top_share, above_share = _draw_shares(res, prob.shape[1])
    rows = []
    separated = set(res.get("separated") or [])
    for j, i in enumerate(est):
        q = np.percentile(prob[:, j], [2.5, 5, 25, 50, 75, 95, 97.5])
        bounded = i in separated  # the prior, not the data, sets its level: no interval
        rows.append({"n": i + 1, "label": labels[i] if i < len(labels) else "", "score": float(mean[j]),
                     "separated": bounded,
                     "lo": None if res["mean_lo"] is None or bounded else res["mean_lo"][j],
                     "hi": None if res["mean_hi"] is None or bounded else res["mean_hi"][j],
                     "u": float(util[:, j].mean()), "u_sd": float(util[:, j].std()),
                     "p2": float(q[0]), "p5": float(q[1]), "q1": float(q[2]), "median": float(q[3]),
                     "q3": float(q[4]), "p95": float(q[5]), "p97": float(q[6]),
                     "top": float((ranks[:, j] < TOP).mean()) if top_share is None else float(top_share[j]),
                     "above": None if thr_prob is None else float((prob[:, j] > thr_prob).mean())
                     if above_share is None else float(above_share[j]),
                     "above_mean": None if thr_prob is None else bool(mean[j] > thr_prob.mean()),
                     "values": prob[:, j]})
    order = sorted(range(len(rows)), key=lambda r: -rows[r]["score"])
    rows = [rows[r] for r in order]
    threshold = None
    if thr_prob is not None:
        flat = bool(res.get("anchor_flat"))
        threshold = {"score": float(thr_prob.mean()), "lo": None if flat else res["thr_lo"],
                     "hi": None if flat else res["thr_hi"]}
    rlh = np.array(res["rlh"], dtype=float)
    chance = np.array(res["chance"], dtype=float)
    low = rlh < RLH_FACTOR * chance
    warnings = []
    if hb_convergence.warning(res):
        warnings.append(hb_convergence.warning(res))
    if n < 30:
        warnings.append(de_en("Nur {n} Personen: Die obere Ebene (Mittelwerte, Streuung) ist unsicher.",
                              "Only {n} people: the upper level (means, spread) is uncertain.", n=n))
    acc = res["acceptance"]
    if hb.acceptance_warning(acc):
        warnings.append(hb.acceptance_warning(acc))
    if separated and res.get("scores_without_separated"):
        warnings.append(de_en("Aussage {which}: von allen immer als wichtigste oder immer als am wenigsten wichtige "
                              "gewählt. Ihren Nutzen begrenzt nur die Priori, die Daten setzen keine Grenze. Wie im "
                              "gepoolten Modell oben gilt die Summe 100 der Scores daher nur für die übrigen Aussagen; "
                              "der Score dieser Aussage steht mit demselben Faktor außerhalb der Summe, ohne Intervall, "
                              "nur als Richtung.",
                              "Statement {which}: always chosen by everyone as most important or always as least "
                              "important. Only the prior bounds its utility; the data set no limit. As in the pooled "
                              "model above, the scores therefore sum to 100 over the other statements only; the score "
                              "of this statement uses the same factor outside the sum, without an interval, only as a "
                              "direction.", which=", ".join(str(i + 1) for i in sorted(separated))))
    elif separated:  # estimated before 2026-10-22: the separated statement still in the sum 100
        warnings.append(de_en("Aussage {which}: von allen immer als wichtigste oder immer als am wenigsten wichtige "
                              "gewählt. Ihren Nutzen begrenzt nur die Priori, die Daten setzen keine Grenze; Wert ohne "
                              "Intervall, nur als Richtung lesen. Weil die Scores je Person auf Summe 100 gebracht "
                              "werden, hängt auch die Höhe der übrigen Scores davon ab; ihre Rangfolge bleibt gültig. Das "
                              "gepoolte Modell oben nimmt diese Aussage aus der Summe 100 heraus: Die Scores der beiden "
                              "Tabellen sind hier nicht direkt vergleichbar.",
                              "Statement {which}: always chosen by everyone as most important or always as least "
                              "important. Only the prior bounds its utility; the data set no limit; value without an "
                              "interval, read only as a direction. Because the scores are brought to sum 100 per "
                              "person, the level of the other scores depends on it too; their ranking stays valid. The "
                              "pooled model above takes this statement out of the sum 100: the scores of the two tables "
                              "are not directly comparable here.",
                              which=", ".join(str(i + 1) for i in sorted(separated))))
    if res.get("anchor_flat"):
        warnings.append(de_en("In der Ankerfrage wurden alle oder keine Aussagen angekreuzt: Die Schwelle begrenzt nur "
                              "die Priori, sie steht ohne Intervall.",
                              "In the anchor question all or no statements were ticked: only the prior bounds the "
                              "threshold; it is shown without an interval."))
    model = {"rows": [{**r, "above": r["above_mean"]} for r in rows], "threshold": threshold}
    return {"persons": n, "rows": rows, "threshold": threshold, "anchored": bool(res.get("anchored")),
            "shares_from_draws": top_share is not None,
            "chart": chart(model), "boxes": box_chart(rows, threshold),
            "rlh": {"mean": float(rlh.mean()), "median": float(np.median(rlh)), "chance": float(chance.mean()),
                    "limit": float(RLH_FACTOR * chance.mean()), "low": int(low.sum()),
                    "low_share": float(low.mean()), "chart": rlh_chart(rlh, float(RLH_FACTOR * chance.mean()),
                                                                       float(chance.mean()))},
            "settings": res["settings"], "acceptance": acc, "target": hb.TARGET, "converged": res["converged"],
            "geweke_ll": res["geweke_ll"], "geweke_mu_flagged": res["geweke_mu_flagged"],
            "ll_halves": res["ll_halves"], "kept": res["kept"], "sets": res["sets"], "seconds": res["seconds"],
            "created": to_local(datetime.fromisoformat(res["created"])), "pool": res.get("pool") or {},
            "set_size": res["set_size"], "warnings": warnings, "top_n": TOP, "rlh_factor": RLH_FACTOR,
            # posterior mean of the variance of the zero-centred utilities around their mean (H-3); None for an estimate
            # from before, whose stored diagonal belongs to the differences to the reference
            "sigma_mean": res.get("sigma_util"), "sigma_prior": (len(est) - 1) / len(est),
            "convergence": hb_convergence.view(res), "convergence_warning": hb_convergence.warning(res)}


def box_chart(rows, threshold, width=720, label_w=250, value_w=56, row_h=26, box_h=12):
    """Geometry of the distribution across persons per statement (rows in chart order): box from the
    first to the third quartile, median line, whiskers 5 to 95 %, and one dot per person (up to
    MAX_DOTS persons; jittered vertically, deterministic)."""
    top_value = max([float(r["values"].max()) for r in rows] + ([threshold["score"]] if threshold else []) + [1])
    step, top = axis(top_value)
    x0, x1 = label_w, width - value_w
    scale = (x1 - x0) / top
    top_y = 20 if threshold else 6
    rng = np.random.default_rng(0)
    out = []
    for n, r in enumerate(rows):
        y = top_y + n * row_h
        mid = y + row_h / 2 - 2
        dots = []
        if len(r["values"]) <= MAX_DOTS:
            jitter = rng.uniform(-box_h / 2 - 2, box_h / 2 + 2, len(r["values"]))
            dots = [(round(x0 + v * scale, 1), round(mid + j, 1)) for v, j in zip(r["values"], jitter)]
        out.append({"n": r["n"], "label": r["label"], "mid": mid, "y": mid - box_h / 2, "h": box_h,
                    "q1": round(x0 + r["q1"] * scale, 1), "q3": round(x0 + r["q3"] * scale, 1),
                    "median": round(x0 + r["median"] * scale, 1), "mean": round(x0 + r["score"] * scale, 1),
                    "p5": round(x0 + r["p5"] * scale, 1), "p95": round(x0 + r["p95"] * scale, 1),
                    "dots": dots, "score": r["score"], "median_value": r["median"],
                    "q1_value": r["q1"], "q3_value": r["q3"]})
    height = top_y + len(rows) * row_h + 18
    count = round(top / step)
    ticks = [(round(x0 + i / count * (x1 - x0), 1), round(i * step, 6)) for i in range(count + 1)]
    return {"rows": out, "top_y": top_y, "width": width, "height": height, "label_w": label_w, "ticks": ticks,
            "tick_y": height - 4, "plot_bottom": height - 16,
            "threshold_x": round(x0 + threshold["score"] * scale, 1) if threshold else None}


def rlh_chart(rlh, limit, chance, width=720, height=150, bins=20):
    """Histogram of the RLH (0 to 1 in 0.05 steps), per bin the persons below the flag limit stacked at the
    bottom, with lines for the chance level and the flag limit."""
    rlh = np.clip(rlh, 0, 1)
    counts, edges = np.histogram(rlh, bins=bins, range=(0, 1))
    low_counts, _ = np.histogram(rlh[rlh < limit], bins=bins, range=(0, 1))
    x0, x1, y0, y1 = 40, width - 16, 10, height - 24
    top = max(int(counts.max()), 1)
    bw = (x1 - x0) / bins
    unit = (y1 - y0) / top
    bars = []
    for b, (c, lc) in enumerate(zip(counts, low_counts)):
        if not c:
            continue
        x, w = round(x0 + b * bw + 1, 1), round(bw - 2, 1)
        bars.append({"x": x, "w": w, "y": round(y1 - c * unit, 1), "h": round((c - lc) * unit, 1),
                     "low_y": round(y1 - lc * unit, 1), "low_h": round(lc * unit, 1), "count": int(c), "low": int(lc),
                     "from": float(edges[b]), "to": float(edges[b + 1])})
    ticks = [(round(x0 + v * (x1 - x0), 1), v) for v in (0, 0.25, 0.5, 0.75, 1)]
    return {"bars": bars, "width": width, "height": height, "x0": x0, "x1": x1, "y0": y0, "y1": y1, "top": top,
            "ticks": ticks, "limit_x": round(x0 + limit * (x1 - x0), 1), "chance_x": round(x0 + chance * (x1 - x0), 1)}
