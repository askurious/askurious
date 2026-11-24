"""Interactions between attributes of a CBC or ACBC ("Wechselwirkungen zwischen Merkmalen", 2026-11-24).

Does a combination of two levels do more (or less) than the sum of their part-worths? Three steps:

1. Search, for every pair of attributes. Interaction columns: the products of the effects-coded columns of the two
   attributes ((La - 1)(Lb - 1) columns; a linearly coded price is one column, so (Lother - 1)); the alternative
   "none" / the threshold has none. Two tests side by side (Hainmueller, Hopkins & Yamamoto, 2014, and Egami & Imai,
   2019, for interactions in conjoint experiments):
   - "einfach": the pooled conditional logit with the main effects against main effects plus the pair: likelihood
     ratio 2 dLL against chi-square with as many degrees of freedom as interaction columns (app/choice_model.fit),
     and the cluster-robust Wald test of the interaction block, since one person answers several tasks (Cameron &
     Miller, 2015).
   - "nach HB" (only with a current HB result, app/conjoint_hb.py): one column holds each person's posterior-mean
     main-effect utility of the alternative (its coefficient takes the scale), plus the interaction columns, against
     that column alone; LR and robust Wald as above. Different preferences between persons look like interactions in
     the pooled model or hide real ones; with each person's own utilities as the base only the interaction is left.
   Holm (1979) corrects over all pairs, on the p of "nach HB" when there is one, else on "einfach" (both are the LR
   tests). Effect size: the increase of McFadden's (1974) R^2 against equal choice probabilities (the "percent
   certainty"), in percentage points. A pair whose interaction columns the choices cannot determine (prohibitions in
   the design, levels only in fixed combinations: app/choice_model.undetermined, or no finite covariance) is "nicht
   schätzbar". Per pair the count of every combination of levels among the concepts shown (MIN_CELL warns).
2. Confirmation, for every pair with a Holm p below ALPHA (Gelman, Hwang & Vehtari, 2014: judge a model by how it
   predicts data it was not fitted to). With holdout tasks (app/holdouts.py, CBC) those, else per person one task
   held out at random, in `splits` divisions (3, selectable 5). On the training tasks HB with the main effects and HB
   with the pair's interaction as parameters common to all persons (app/hb.py `fixed`; Train, 2009; Rossi et al.,
   2005), each predicting the held-out choices over its kept draws: summed log-likelihood and hit rate per division.
   "bestätigt" only when the log-likelihood improves in the mean and in at least two thirds of the divisions (2 of 3,
   4 of 5, the one of the holdouts); otherwise "auffällig, nicht bestätigt". The HB test "nach HB" alone is too
   optimistic, since the HB utilities were estimated on the same tasks.
3. Taking over (app/conjoint_hb.py): a confirmed pair can go into the HB model as a fixed interaction
   (item.config[CONFIG_KEY], attribute ids); simulator, export and report then use it.

Search and confirmation run as one background job like HB (app/jobs.py, kind "conjoint_ww"), stored next to the HB
result (item_<id>.ww.json). The result holds no values per person. It stays current while the answers, the data pool
and the HB settings stay (the taken-over interactions do not count: taking one over leaves the search as it is).
"""
import hashlib
import json
import math
import os

import numpy as np

from app.choice_model import CAMERON, MCFADDEN, TRAIN, ChoiceSet, fit, undetermined
from app.lang import de_en

FORMAT = 1
CONFIG_KEY = "hb_interactions"  # item.config: [[attribute id, attribute id], ...] taken into the HB model
ALPHA = 0.05      # Holm p below this: confirmation; the usual level
MIN_CELL = 30     # combinations of two levels shown fewer times: a warning (Askurious's choice, about 30 per cell)
SPLITS = (3, 5)   # divisions of the confirmation without holdout tasks; 3 is the default (E4)
KEEP = 100        # kept draws of an HB run of the confirmation that predict the held-out choices
PARALLEL = 4      # HB runs of the confirmation side by side by default (WW_PARALLEL; as many as hb_chains.CHAINS)
HOLM = ("Holm, S. (1979). A simple sequentially rejective multiple test procedure. Scandinavian Journal of Statistics, "
        "6(2), 65–70.")
HAINMUELLER = ("Hainmueller, J., Hopkins, D. J., & Yamamoto, T. (2014). Causal inference in conjoint analysis: "
               "Understanding multidimensional choices via stated preference experiments. Political Analysis, 22(1), "
               "1–30. https://doi.org/10.1093/pan/mpt024")
EGAMI_IMAI = ("Egami, N., & Imai, K. (2019). Causal interaction in factorial experiments: Application to conjoint "
              "analysis. Journal of the American Statistical Association, 114(526), 529–540. "
              "https://doi.org/10.1080/01621459.2018.1476246")
GELMAN_HWANG_VEHTARI = ("Gelman, A., Hwang, J., & Vehtari, A. (2014). Understanding predictive information criteria for "
                        "Bayesian models. Statistics and Computing, 24(6), 997–1016. "
                        "https://doi.org/10.1007/s11222-013-9416-2")
ROSSI = ("Rossi, P. E., Allenby, G. M., & McCulloch, R. (2005). Bayesian statistics and marketing. Wiley. "
         "https://doi.org/10.1002/0470863692")
SOURCES = (HAINMUELLER, EGAMI_IMAI, HOLM, CAMERON, MCFADDEN, GELMAN_HWANG_VEHTARI, TRAIN, ROSSI)


# --- coding of the pairs ------------------------------------------------------------------------------------------

def attribute_columns(coding, a):
    """The columns of attribute a in the design: its effects columns, or the price column for a linear price."""
    if a in coding.offsets:
        return list(range(coding.offsets[a], coding.offsets[a] + coding.levels[a] - 1))
    return [coding.price] if coding.price is not None else []


def pairs_of(coding, linear_price=True):
    """[(a, b)] with a < b of the attributes that can interact: every effects-coded attribute with two levels or more,
    and the linearly coded price attribute of a CBC (linear_price; ACBC's summed price is no attribute)."""
    usable = [a for a in range(len(coding.levels))
              if (a in coding.offsets and coding.levels[a] >= 2)
              or (linear_price and a not in coding.offsets and coding.price is not None)]
    return [(a, b) for i, a in enumerate(usable) for b in usable[i + 1:]]


def interaction(X, coding, a, b):
    """The interaction columns of a and b for design rows X (... x k): the products of their columns, a major."""
    X = np.asarray(X, dtype=float)
    A = X[..., attribute_columns(coding, a)]
    B = X[..., attribute_columns(coding, b)]
    return (A[..., :, None] * B[..., None, :]).reshape(X.shape[:-1] + (A.shape[-1] * B.shape[-1],))


def level_codes(coding, a, price_levels=None):
    """Levels x columns: the code of every level of attribute a (effects coding; price: (number - centre) / scale)."""
    if a in coding.offsets:
        n = coding.levels[a]
        return np.vstack([np.eye(n - 1), -np.ones((1, n - 1))]) if n > 1 else np.zeros((1, 0))
    numbers = price_levels or []
    return np.array([[(x - coding.price_center) / coding.price_scale] for x in numbers])


def cell_matrix(coding, a, b, price_levels=None):
    """(La * Lb) x columns: the interaction columns of every combination of levels (row la * Lb + lb)."""
    A, B = level_codes(coding, a, price_levels), level_codes(coding, b, price_levels)
    return (A[:, None, :, None] * B[None, :, None, :]).reshape(len(A) * len(B), A.shape[1] * B.shape[1])


def prior_block(coding, a, b):
    """Prior covariance of the interaction columns of a and b, in units of MU_PRIOR_SD^2 (app/hb.py): the Kronecker
    product of the covariances the HB prior gives the zero-centred levels of each attribute (I - 11'/L for L levels,
    as app/conjoint_hb._prior; 1 for the standardised price), so that every cell of the interaction table has the same
    prior spread (Festlegung von Askurious)."""
    def one(x):
        if x in coding.offsets:
            n = coding.levels[x]
            return np.eye(n - 1) - 1.0 / n
        return np.eye(1)
    return np.kron(one(a), one(b))


def with_pairs(sets, coding, pairs):
    """The choice sets with the interaction columns of the pairs appended (in that order)."""
    out = []
    for s in sets:
        extra = [interaction(s.X, coding, a, b) for a, b in pairs]
        out.append(ChoiceSet(np.hstack([s.X] + extra) if extra else s.X, s.chosen, s.person))
    return out


def _level_of(X, coding, a, price_values):
    """Level per row of X (-1 for the "none" row or a level not shown)."""
    if a in coding.offsets:
        return coding.levels_of(X, a)
    col = X[..., coding.price]
    out = np.full(col.shape, -1)
    for lv, v in enumerate(price_values):
        out[np.abs(col - v) < 1e-9] = lv
    return out


def cell_counts(person_sets, coding, a, b, price_values=None):
    """La x Lb counts of the combinations among the concepts shown (rows without "none"); a task coded as two sets
    (dual response) counts once. price_values: the price column of each price level."""
    La = coding.levels[a] if a in coding.offsets else len(price_values or [])
    Lb = coding.levels[b] if b in coding.offsets else len(price_values or [])
    counts = np.zeros((La, Lb), dtype=int)
    for sets in person_sets:
        before = None
        for s in sets:
            X = np.asarray(s.X, dtype=float)
            rows = X if coding.asc is None else X[X[:, coding.asc] != 1.0]
            if before is not None and rows.shape == before.shape and np.array_equal(rows, before):
                continue
            before = rows
            la, lb = _level_of(rows, coding, a, price_values or []), _level_of(rows, coding, b, price_values or [])
            for x, y in zip(la, lb):
                if x >= 0 and y >= 0:
                    counts[x, y] += 1
    return counts


# --- tests -------------------------------------------------------------------------------------------------------

def holm(ps):
    """Holm-adjusted p values (step-down, monotone, at most 1); None stays None."""
    idx = [i for i, p in enumerate(ps) if p is not None]
    order = sorted(idx, key=lambda i: ps[i])
    m = len(order)
    out = [None] * len(ps)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * ps[i]))
        out[i] = running
    return out


def _chi2_p(x, df):
    from app.group_tests import chi2_sf

    return float(chi2_sf(max(float(x), 0.0), df)) if df > 0 else None


def chance_loglik(sets):
    """Log-likelihood with every alternative equally likely."""
    return -sum(math.log(len(s.X)) for s in sets if 0 <= s.chosen < len(s.X))


def compare(base_sets, full_sets, k, m, ll0=None, ll_chance=None):
    """LR and robust Wald test of the last m columns of full_sets (k + m columns) against base_sets (k columns).
    {"lr", "p", "wald", "wald_p", "gain" (McFadden R^2 increase in percentage points), "estimable", "beta", "cov",
    "ll"} (beta and cov of the full model; None where it cannot be estimated)."""
    if ll0 is None:
        ll0 = fit(base_sets, k)[2]
    free = undetermined(full_sets, k + m)
    if free[k:].any():
        return {"estimable": False}
    beta, cov, ll1, converged, _ = fit(full_sets, k + m)
    if cov is None or not np.all(np.isfinite(beta)):
        return {"estimable": False}
    lr = max(2 * (ll1 - ll0), 0.0)
    g, V = beta[k:], cov[k:, k:]
    try:
        wald = float(g @ np.linalg.solve(V, g))
    except np.linalg.LinAlgError:
        wald = None
    ll_chance = chance_loglik(full_sets) if ll_chance is None else ll_chance
    return {"estimable": True, "lr": float(lr), "p": _chi2_p(lr, m), "wald": wald,
            "wald_p": _chi2_p(wald, m) if wald is not None else None, "df": m,
            "gain": float(100 * (ll1 - ll0) / -ll_chance) if ll_chance else None,
            "beta": beta, "cov": cov, "ll": float(ll1), "converged": bool(converged)}


def _rnd(x, d=4):
    return None if x is None or not np.isfinite(x) else round(float(x), d)


def search(person_sets, coding, pairs, hb_beta=None, price_levels=None, progress=None):
    """Rows of the search (see the module text), sorted by the Holm p, pairs that cannot be estimated last.
    person_sets: [[ChoiceSet]] per person; hb_beta: persons x k posterior-mean main-effect parameters or None."""
    k = coding.k
    sets = [ChoiceSet(s.X, s.chosen, p) for p, mine in enumerate(person_sets) for s in mine]
    ll_chance = chance_loglik(sets)
    base = fit(sets, k)
    hb_sets = None
    if hb_beta is not None:
        hb_beta = np.asarray(hb_beta, dtype=float)
        hb_sets = [ChoiceSet((np.asarray(s.X) @ hb_beta[s.person])[:, None], s.chosen, s.person) for s in sets]
        hb_base = fit(hb_sets, 1)
    price_values = None
    if coding.price is not None and price_levels:
        price_values = [(x - coding.price_center) / coding.price_scale for x in price_levels]
    rows = []
    for n, (a, b) in enumerate(pairs):
        Z = [interaction(s.X, coding, a, b) for s in sets]
        m = Z[0].shape[1] if Z else 0
        full = [ChoiceSet(np.hstack([s.X, z]), s.chosen, s.person) for s, z in zip(sets, Z)]
        simple = compare(sets, full, k, m, base[2], ll_chance)
        by_hb = None
        if hb_sets is not None:
            hb_full = [ChoiceSet(np.hstack([h.X, z]), h.chosen, h.person) for h, z in zip(hb_sets, Z)]
            by_hb = compare(hb_sets, hb_full, 1, m, hb_base[2], ll_chance)
        counts = cell_counts(person_sets, coding, a, b, price_values)
        estimable = simple["estimable"] and (by_hb is None or by_hb["estimable"])
        row = {"a": a, "b": b, "cols": m, "estimable": bool(estimable), "counts": counts.tolist(),
               "min_cell": int(counts.min()) if counts.size else 0,
               "simple": _test_out(simple), "hb": _test_out(by_hb) if by_hb is not None else None}
        if estimable and (a in coding.offsets or price_values) and (b in coding.offsets or price_values):
            driving, mains = (by_hb, "hb") if by_hb is not None else (simple, "simple")
            row["cells"] = _cells(coding, a, b, driving, mains, price_levels, hb_beta)
        rows.append(row)
        if progress:
            progress((n + 1) / max(len(pairs), 1))
    used = [(r["hb"] or r["simple"]).get("p") if r["estimable"] else None for r in rows]
    adjusted = holm(used)
    for r, p in zip(rows, adjusted):
        r["p_holm"] = _rnd(p, 6)
        test = (r["hb"] or r["simple"]) if r["estimable"] else None
        r["gain"] = _rnd(test.get("gain")) if test else None
    # ties of the rounded p (several pairs at 0) are ordered by the strength of the test, the LR statistic
    rows.sort(key=lambda r: (not r["estimable"], r["p_holm"] if r["p_holm"] is not None else 2.0,
                             -(((r["hb"] or r["simple"]) or {}).get("lr") or 0.0), r["a"], r["b"]))
    return rows


def _test_out(t):
    if not t or not t.get("estimable"):
        return {"estimable": False}
    return {"estimable": True, "lr": _rnd(t["lr"]), "p": _rnd(t["p"], 6), "wald": _rnd(t["wald"]),
            "wald_p": _rnd(t["wald_p"], 6), "df": t["df"], "gain": _rnd(t["gain"])}


def _cells(coding, a, b, test, basis, price_levels, hb_beta):
    """The interaction values of every combination with their 95 % interval (robust covariance) and the utility of
    the combination in the model of the test: main effects plus interaction ("simple": the pooled part-worths;
    "hb": the coefficient of the HB utility times the mean HB part-worths over persons)."""
    from app.intervals import Z as Z975

    k_base = coding.k if basis == "simple" else 1
    beta, cov = test["beta"], test["cov"]
    g, V = beta[k_base:], cov[k_base:, k_base:]
    C = cell_matrix(coding, a, b, price_levels)
    w = C @ g
    se = np.sqrt(np.maximum(np.einsum("ij,jk,ik->i", C, V, C), 0.0))
    A, B = level_codes(coding, a, price_levels), level_codes(coding, b, price_levels)
    if basis == "simple":
        main = beta[:coding.k]
        scale = 1.0
    else:
        main = np.asarray(hb_beta, dtype=float).mean(axis=0)
        scale = float(beta[0])
    ua = A @ main[attribute_columns(coding, a)] * scale
    ub = B @ main[attribute_columns(coding, b)] * scale
    La, Lb = len(A), len(B)
    W = w.reshape(La, Lb)
    S = se.reshape(La, Lb)
    return {"basis": basis, "w": np.round(W, 4).tolist(), "lo": np.round(W - Z975 * S, 4).tolist(),
            "hi": np.round(W + Z975 * S, 4).tolist(),
            "comb": np.round(ua[:, None] + ub[None, :] + W, 4).tolist(),
            "scale": _rnd(scale)}


# --- confirmation ------------------------------------------------------------------------------------------------

def tasks_of(sets, coding):
    """The sets of one person grouped into tasks: consecutive sets with the same concepts (dual response: two sets)."""
    out, before = [], None
    for s in sets:
        X = np.asarray(s.X, dtype=float)
        rows = X if coding.asc is None else X[X[:, coding.asc] != 1.0]
        if out and before is not None and rows.shape == before.shape and np.array_equal(rows, before):
            out[-1].append(s)
        else:
            out.append([s])
        before = rows
    return out


def divisions(person_sets, coding, count, seed):
    """[(training sets per person, held-out sets per person)]: per division one task of every person with two tasks or
    more held out at random."""
    out = []
    for d in range(count):
        rng = np.random.default_rng([int(seed), 7919, d])
        train, test = [], []
        for sets in person_sets:
            tasks = tasks_of(sets, coding)
            if len(tasks) < 2:
                train.append(list(sets))
                test.append([])
                continue
            out_task = int(rng.integers(len(tasks)))
            train.append([s for t, task in enumerate(tasks) if t != out_task for s in task])
            test.append(list(tasks[out_task]))
        out.append((train, test))
    return out


def predict(test_sets, draws, fixed_draws=None):
    """(summed log-likelihood, hits, choices) of the held-out sets: per set the choice probabilities averaged over the
    kept draws (betas draws x persons x k, fixed parameters draws x m for the columns after the k)."""
    ll, hits, total = 0.0, 0.0, 0
    k = draws.shape[2]
    for n, sets in enumerate(test_sets):
        for s in sets:
            X = np.asarray(s.X, dtype=float)
            if not 0 <= s.chosen < len(X):
                continue
            U = X[:, :k] @ draws[:, n, :].T                        # alternatives x draws
            if fixed_draws is not None and X.shape[1] > k:
                U = U + X[:, k:] @ fixed_draws.T
            U = U - U.max(axis=0, keepdims=True)
            P = np.exp(U)
            P = (P / P.sum(axis=0, keepdims=True)).mean(axis=1)
            ll += math.log(max(P[s.chosen], 1e-300))
            best = np.flatnonzero(P >= P.max() - 1e-12)
            hits += (s.chosen in best) / len(best)
            total += 1
    return ll, hits, total


def needed(count):
    """Divisions that must improve: two thirds, rounded up (2 of 3, 4 of 5, 1 of 1)."""
    return math.ceil(2 * count / 3)


def parallel_setting():
    """WW_PARALLEL from the environment (default PARALLEL; 1: one run after another in this process). Read from the
    environment, not the app's configuration: the confirmation runs on the Rechenknecht too, which has none."""
    try:
        return max(1, int(os.environ.get("WW_PARALLEL") or PARALLEL))
    except ValueError:
        return PARALLEL


def _cpu_limit():
    """The cores this process may use: its affinity (os.cpu_count where there is none) and a container's CPU quota
    (cgroup v2 cpu.max, e.g. "cpus: 16" of the Rechenknecht in deploy/docker/compose.yaml)."""
    try:
        n = len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        n = os.cpu_count() or 1
    try:
        with open("/sys/fs/cgroup/cpu.max", encoding="ascii") as f:
            quota, period = f.read().split()[:2]
        if quota != "max":
            n = min(n, max(1, int(int(quota) // int(period))))
    except (OSError, ValueError):
        pass
    return n


def _load():
    """The machine's load average over the last minute, None where there is none (Windows)."""
    try:
        return os.getloadavg()[0]
    except (AttributeError, OSError):
        return None


def processes(runs, wanted=None, cpus=None, limit=None, load=...):
    """Processes for the `runs` HB runs of a confirmation: WW_PARALLEL, at most the runs, and at most the free cores:
    the machine's cores minus its load average over the last minute, plus the core of this process (its own load is
    counted in it), and never more than this process may use (affinity, container quota); at least 1. wanted, cpus, limit
    and load (None: no load average) are given by tests; left out they are read from this machine."""
    wanted = parallel_setting() if wanted is None else max(1, int(wanted))
    n = min(wanted, max(1, int(runs)))
    if n <= 1:
        return 1
    cpus = (os.cpu_count() or 1) if cpus is None else cpus
    limit = _cpu_limit() if limit is None else limit
    load = _load() if load is ... else load
    free = limit if load is None else min(limit, int(math.floor(cpus - load)) + 1)
    return max(1, min(n, free))


def confirm(person_sets, coding, pairs, cfg, prior_cov, prior_kwargs, splits=3, holdout_sets=None, progress=None,
            workers=None):
    """{pair: {"ll_with", "ll_without", "hit_with", "hit_without", "choices", "delta", "mean", "lo", "hi", "better",
    "confirmed"}} for the pairs (see the module text). holdout_sets: held-out sets per person (with the interaction
    columns computed on the fly), or None for divisions. workers: processes for the HB runs (None: processes())."""
    from app import hb, hb_chains

    if holdout_sets is not None:
        parts = [(person_sets, holdout_sets)]
    else:
        parts = divisions(person_sets, coding, splits, cfg.get("seed", 1))
    k = coding.k

    def args(seed, fixed=None, fixed_prior=None):
        return {"burn": cfg["burn"], "draws": cfg["draws"], "thin": cfg["thin"], "seed": seed, "prior_cov": prior_cov,
                "keep": KEEP, "fixed": fixed, "fixed_prior": fixed_prior, **prior_kwargs}

    # every HB run with its own fixed seed, in the order of the divisions and, within one, without and with each pair
    tasks = []
    for d, (train, _) in enumerate(parts):
        seed = int(cfg.get("seed", 1)) + d
        tasks.append((train, k, args(seed)))
        for a, b in pairs:
            m = len(attribute_columns(coding, a)) * len(attribute_columns(coding, b))
            tasks.append((with_pairs_people(train, coding, [(a, b)]), k + m,
                          args(seed, list(range(k, k + m)), prior_block(coding, a, b))))
    runs = len(tasks)
    workers = processes(runs) if workers is None else max(1, min(int(workers), runs))
    if workers > 1:
        results = hb_chains.in_processes(tasks, workers, progress, "Confirmation run")
    else:
        results = []
        for n, (train, kk, kw) in enumerate(tasks):
            step = (lambda n: (lambda f: progress((n + f) / max(runs, 1))))(n) if progress else None
            results.append(hb.estimate(train, kk, progress=step, **kw))

    out = {tuple(p): {"ll_with": [], "ll_without": [], "hit_with": [], "hit_without": [], "choices": []} for p in pairs}
    n = 0
    for d, (train, test) in enumerate(parts):
        r0 = results[n]
        n += 1
        ll0, hit0, total = predict(test, r0["draws"])
        for a, b in pairs:
            r1 = results[n]
            n += 1
            ll1, hit1, _ = predict(with_pairs_people(test, coding, [(a, b)]), r1["draws"], r1["fixed_draws"])
            row = out[(a, b)]
            row["ll_with"].append(round(ll1, 3))
            row["ll_without"].append(round(ll0, 3))
            row["hit_with"].append(round(hit1 / total, 4) if total else None)
            row["hit_without"].append(round(hit0 / total, 4) if total else None)
            row["choices"].append(total)
    for row in out.values():
        delta = [w - wo for w, wo in zip(row["ll_with"], row["ll_without"])]
        row["delta"] = [round(x, 3) for x in delta]
        row["mean"] = round(float(np.mean(delta)), 3) if delta else None
        row["lo"], row["hi"] = (round(min(delta), 3), round(max(delta), 3)) if delta else (None, None)
        row["better"] = sum(1 for x in delta if x > 0)
        row["needed"] = needed(len(delta))
        row["confirmed"] = bool(delta) and row["mean"] > 0 and row["better"] >= row["needed"]
    return out


def with_pairs_people(person_sets, coding, pairs):
    return [with_pairs(sets, coding, pairs) for sets in person_sets]


# --- the background job ------------------------------------------------------------------------------------------

def run(inp, progress=None):
    """Search and confirmation from build_input(); returns the JSON-ready result (no values per person)."""
    import time

    from app import conjoint_hb, maxdiff_hb

    started = time.monotonic()
    coding = conjoint_hb._coding(inp["coding"])
    person_sets = [[ChoiceSet(X, chosen) for X, chosen in p["sets"]] for p in inp["persons"]]
    pairs = [tuple(p) for p in inp["pairs"]]
    hb_beta = None
    if inp.get("hb_beta") is not None:
        hb_beta = np.array(inp["hb_beta"], dtype=float)

    def part(lo, hi):
        return (lambda f: progress(lo + (hi - lo) * f)) if progress else None

    flagged_share = 0.05
    rows = search(person_sets, coding, pairs, hb_beta, inp.get("price_levels"), part(0.0, flagged_share))
    flagged = [(r["a"], r["b"]) for r in rows if r["estimable"] and r["p_holm"] is not None and r["p_holm"] < ALPHA]
    conf = None
    if flagged:
        cfg = inp["settings"]
        holdout_sets = None
        if inp.get("holdouts"):
            # by the position of the person in "persons": no participation numbers travel to the compute node
            holdout_sets = [[] for _ in person_sets]
            for n, sets in inp["holdouts"]:
                if 0 <= n < len(holdout_sets):
                    holdout_sets[n] = [ChoiceSet(X, chosen) for X, chosen in sets]
        conf = confirm(person_sets, coding, flagged, cfg, conjoint_hb._prior(coding),
                       maxdiff_hb.prior_args(cfg, conjoint_hb._prior_groups(coding)), inp.get("splits", 3),
                       holdout_sets, part(flagged_share, 1.0))
    for r in rows:
        c = conf.get((r["a"], r["b"])) if conf else None
        r["confirm"] = c
        if not r["estimable"]:
            r["status"] = "na"
        elif r["p_holm"] is None or r["p_holm"] >= ALPHA:
            r["status"] = "none"
        else:
            r["status"] = "confirmed" if c and c["confirmed"] else "notable"
    if progress:
        progress(1.0)
    return {"format": FORMAT, "key": inp["key"], "data_key": inp["data_key"], "hb_key": inp.get("hb_key"),
            "type": inp["type"], "with_hb": hb_beta is not None, "rows": rows,
            "confirm": {"mode": "holdouts" if inp.get("holdouts") else "split",
                        "splits": 1 if inp.get("holdouts") else int(inp.get("splits", 3)),
                        "needed": needed(1 if inp.get("holdouts") else int(inp.get("splits", 3)))},
            "flagged": len(flagged), "persons": len(person_sets), "seconds": round(time.monotonic() - started, 1)}


# --- input in the request -----------------------------------------------------------------------------------------

def hb_main_beta(result, coding):
    """{pid: main-effect parameters (k)} from a stored HB result: the posterior-mean part-worths of every level but the
    last of each attribute (its effects columns), the constant and the price coefficient on the scale of the coding."""
    out = {}
    util = result.get("util") or []
    for n, pid in enumerate(result.get("pids") or []):
        b = np.zeros(coding.k)
        for a, start, count in result["spans"]:
            if a in coding.offsets:
                b[coding.offsets[a]:coding.offsets[a] + count - 1] = util[n][start:start + count - 1]
        if coding.asc is not None and result.get("asc") is not None:
            b[coding.asc] = result["asc"][n]
        if coding.price is not None and result.get("price") is not None:
            b[coding.price] = result["price"][n] * coding.price_scale
        out[pid] = b
    return out


def _holdout_input(item, table, coding):
    """[(pid, [(X, chosen)])] of the CBC holdout tasks, coded as the HB input, or None without any."""
    from app import holdouts

    if item.type != "conjoint":
        return None
    active = holdouts.cbc_active(item)
    if not active:
        return None
    kind = item.kind
    pa = kind.price_attribute(item)
    numbers = kind.level_numbers(item, pa) if pa is not None and coding.price is not None else None
    with_none = bool(item.config.get("none"))
    J = int(item.config["alternatives"])
    by_person = {}
    for h, profiles in active:
        given = holdouts._answers(table, f"{item.code}_ho{h + 1}")
        X = [coding.row(p, numbers[p[pa]] if numbers and p[pa] is not None else None).tolist() for p in profiles]
        if with_none and coding.asc is not None:
            X.append(coding.asc_row().tolist())
        for pid, v in given.items():
            chosen = J if v == 0 else v - 1
            if 0 <= chosen < len(X):
                by_person.setdefault(pid, []).append((X, chosen))
    return sorted(by_person.items(), key=lambda kv: str(kv[0])) or None


def build_input(item, table, splits=3, hb_result=None):
    """Everything the job needs, or None (no HB input: too few persons or no design)."""
    from app import conjoint_hb

    hb_inp = conjoint_hb.model_input(item, table)
    if hb_inp is None:
        return None
    coding = conjoint_hb._coding(hb_inp["coding"])
    pairs = pairs_of(coding, linear_price=item.type == "conjoint")
    if not pairs:
        return None
    pa = item.kind.price_attribute(item) if item.type == "conjoint" else None
    price_levels = item.kind.level_numbers(item, pa) if pa is not None and coding.price is not None else None
    holdout = _holdout_input(item, table, coding)
    if holdout:  # keyed by the position of the person in "persons", never by the participation number
        index = {p["pid"]: n for n, p in enumerate(hb_inp["persons"])}
        holdout = [[index[pid], sets] for pid, sets in holdout if pid in index] or None
    splits = splits if splits in SPLITS else SPLITS[0]
    hb_beta, hb_key = None, None
    if hb_result:
        betas = hb_main_beta(hb_result, coding)
        if all(p["pid"] in betas for p in hb_inp["persons"]):
            hb_beta = [betas[p["pid"]].round(6).tolist() for p in hb_inp["persons"]]
            hb_key = hb_result.get("key")
    data_key = search_data_key(hb_inp)
    key = hashlib.sha1(json.dumps({"data": data_key, "hb": hb_key, "splits": None if holdout else splits,
                                   "holdouts": holdout, "format": FORMAT}, sort_keys=True, default=str).encode()).hexdigest()
    return {"key": key, "data_key": data_key, "hb_key": hb_key, "type": item.type, "coding": hb_inp["coding"],
            "persons": hb_inp["persons"], "pairs": [list(p) for p in pairs], "price_levels": price_levels,
            "hb_beta": hb_beta, "holdouts": holdout, "splits": splits, "settings": hb_inp["settings"]}


def search_data_key(hb_inp):
    """What the search depends on: the answers and the HB settings, not the interactions taken into the HB model."""
    settings = {k: v for k, v in (hb_inp.get("settings") or {}).items()}
    return hashlib.sha1(json.dumps({"data": hb_inp.get("ww_data_key") or hb_inp.get("data_key"), "settings": settings},
                                   sort_keys=True, default=str).encode()).hexdigest()


# --- storage, start, state -------------------------------------------------------------------------------------

def _paths(item):
    from app import maxdiff_hb

    d = maxdiff_hb.folder(item)
    return os.path.join(d, f"item_{item.id}.ww.json"), os.path.join(d, f"item_{item.id}.ww.run.json")


def load(item):
    from app import maxdiff_hb

    result = maxdiff_hb._read(_paths(item)[0])
    return result if result and result.get("format") == FORMAT and result.get("type") == item.type else None


def running(item):
    from app import jobs

    return jobs.marker(_paths(item))


def start(item, table, pool, splits=3, background=True):
    """"started", "running", "current" or "nodata"."""
    from flask import current_app

    from app import conjoint_hb, jobs

    stored_hb = conjoint_hb._stored(item)  # the HB estimate, used when it fits these data and settings
    hb_inp = conjoint_hb.model_input(item, table, stored_hb.get("settings") if stored_hb else None)
    hb_result = stored_hb if stored_hb and hb_inp and stored_hb.get("key") == hb_inp["key"] else None
    inp = build_input(item, table, splits, hb_result)
    if inp is None:
        return "nodata"
    stored = load(item)
    if stored and stored.get("key") == inp["key"]:
        return "current"
    app = current_app._get_current_object()
    if not jobs.start(app, "conjoint_ww", _paths(item), inp, pool, background, name=f"ww-item-{item.id}"):
        return "running"
    return "started"


def adopted(item):
    """The pairs taken into the HB model: [(attribute id, attribute id)] as stored."""
    raw = (item.config or {}).get(CONFIG_KEY) or []
    return [tuple(p) for p in raw if isinstance(p, (list, tuple)) and len(p) == 2]


def adopted_indices(item):
    """The taken-over pairs as attribute indices (a < b) that still exist and can interact, in the stored order."""
    ids =[a.get("id") for a in (item.config or {}).get("attributes", [])]
    out = []
    for x, y in adopted(item):
        if x in ids and y in ids and x != y:
            a, b = sorted((ids.index(x), ids.index(y)))
            if (a, b) not in out:
                out.append((a, b))
    return out


def set_adopted(item, a_id, b_id, take=True):
    """Take a pair into the HB model or out of it (attribute ids); True if the configuration changed."""
    ids = [a.get("id") for a in (item.config or {}).get("attributes", [])]
    if a_id not in ids or b_id not in ids or a_id == b_id:
        return False
    pair = sorted((a_id, b_id), key=ids.index)
    now = [list(p) for p in adopted(item)]
    has = any(sorted(p, key=lambda x: ids.index(x) if x in ids else -1) == pair for p in now)
    if take == has:
        return False
    if take:
        now.append(pair)
    else:
        now = [p for p in now if sorted(p, key=lambda x: ids.index(x) if x in ids else -1) != pair]
    config = {k: v for k, v in (item.config or {}).items() if k != CONFIG_KEY}
    item.config = {**config, CONFIG_KEY: now} if now else config
    return True


STATUS = ("confirmed", "notable", "none", "na")


def status_label(status):
    return {"confirmed": de_en("bestätigt", "confirmed"),
            "notable": de_en("auffällig, nicht bestätigt", "notable, not confirmed"),
            "none": de_en("kein Hinweis", "no indication"),
            "na": de_en("nicht schätzbar", "not estimable")}[status]


def view(item, table, hb_view, lang):
    """The block "Wechselwirkungen zwischen Merkmalen" of the results page."""
    from app import conjoint_hb

    hb_inp = conjoint_hb.model_input(item, table)
    available = hb_inp is not None and bool(pairs_of(conjoint_hb._coding(hb_inp["coding"]), item.type == "conjoint"))
    out = {"available": available, "data_key": search_data_key(hb_inp) if hb_inp else None,
           "hb_current": bool(hb_view and hb_view.get("state") == "current"),
           "hb_seconds": ((hb_view or {}).get("result") or {}).get("seconds") if hb_view else None,
           "holdouts": item.type == "conjoint" and bool(_holdouts_active(item)), "splits": SPLITS}
    out["runtime"] = runtime_minutes(out["hb_seconds"], 1 if out["holdouts"] else SPLITS[0])
    return _state(item, out, lang)


def _holdouts_active(item):
    from app import holdouts

    return holdouts.cbc_active(item)


def revisit(item, before, lang):
    """The view anew from the stored result and the marker (results.refresh_estimates)."""
    return _state(item, {k: v for k, v in before.items() if k not in ("state", "result", "marker", "stale_created")}, lang)


def _state(item, out, lang):
    from app import maxdiff_hb

    stored = load(item)
    out["marker"] = running(item)
    out["state"], out["result"], out["stale_created"] = None, None, None
    if stored:
        if out.get("data_key") and stored.get("data_key") == out["data_key"]:
            out["state"] = "current"
            out["result"] = summary(item, stored, lang)
        else:
            out["state"] = "stale"
            out["stale_created"] = maxdiff_hb.created_local(stored)
    if out["marker"] and out["marker"].get("state") == "running":
        out["state"] = "running"
    out["adopted"] = [list(p) for p in adopted_indices(item)]
    out["sources"] = list(SOURCES)
    return out


def summary(item, res, lang):
    """Rows with labels for the template and the dialog tables."""
    from app.timeutil import to_local
    from datetime import datetime

    attrs = item.kind.attributes(item, lang)
    adopted_now = adopted_indices(item)
    rows = []
    for r in res["rows"]:
        if r["a"] >= len(attrs) or r["b"] >= len(attrs):
            continue
        A, B = attrs[r["a"]], attrs[r["b"]]
        cells = r.get("cells")
        la, lb = A["level_labels"], B["level_labels"]
        rows.append({**r, "label": f"{A['label']} × {B['label']}", "a_label": A["label"], "b_label": B["label"],
                     "a_id": A.get("id"), "b_id": B.get("id"), "a_levels": la, "b_levels": lb,
                     "status_label": status_label(r["status"]), "cells": cells,
                     "adopted": (r["a"], r["b"]) in adopted_now,
                     "few": 0 < r.get("min_cell", 0) < MIN_CELL or r.get("min_cell", 0) == 0})
    created = res.get("created")
    return {"rows": rows, "with_hb": res.get("with_hb"), "confirm": res.get("confirm"), "flagged": res.get("flagged"),
            "persons": res.get("persons"), "seconds": res.get("seconds"), "min_cell": MIN_CELL, "alpha": ALPHA,
            "hb_key": res.get("hb_key"),
            "created": to_local(datetime.fromisoformat(created)) if created else None}


def csv_text(item, res, lang):
    """The search table as CSV (semicolon, decimal comma for German), one row per pair."""
    import csv
    import io

    view_rows = summary(item, res, lang)["rows"]
    german = lang != "en"

    def num(x, d=4):
        if x is None:
            return ""
        s = f"{x:.{d}f}"
        return s.replace(".", ",") if german else s

    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";" if german else ",")
    w.writerow([de_en("Merkmalspaar", "Attribute pair"), de_en("Spalten", "Columns"), de_en("p einfach", "p simple"),
                de_en("p Wald einfach", "p Wald simple"), de_en("p nach HB", "p after HB"),
                de_en("p Wald nach HB", "p Wald after HB"), "p (Holm)",
                de_en("+ Sicherheit (Pp.)", "+ certainty (pp)"), de_en("Δ LL Vorhersage (Mittel)", "Δ LL prediction (mean)"),
                de_en("besser in Aufteilungen", "better in divisions"), de_en("kleinste Zelle", "smallest cell"),
                de_en("Ergebnis", "Result")])
    for r in view_rows:
        s, h, c = r["simple"] or {}, r["hb"] or {}, r.get("confirm") or {}
        w.writerow([r["label"], r["cols"], num(s.get("p")), num(s.get("wald_p")), num(h.get("p")), num(h.get("wald_p")),
                    num(r.get("p_holm")), num(r.get("gain"), 3), num(c.get("mean"), 2),
                    f"{c['better']}/{len(c['delta'])}" if c else "", r.get("min_cell", ""), r["status_label"]])
    return buf.getvalue()


def runtime_minutes(hb_seconds, splits, parallel=None):
    """(minutes of the base runs, minutes per notable pair) of the confirmation, roughly: per division one HB run
    without the interaction shared by all pairs and one with each pair, each about as long as the HB estimate
    (measured: 367 people, 4 attributes, 12 min for 3 divisions and 2 pairs against 64 s for the HB estimate), with
    `parallel` runs side by side (WW_PARALLEL when None) the time divided by them. None without an HB time."""
    if not hb_seconds:
        return None
    parallel = parallel_setting() if parallel is None else max(1, int(parallel))
    one = max(1, round(hb_seconds * splits * 1.25 / 60 / parallel))
    return one, one
