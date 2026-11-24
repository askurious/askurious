"""Several chains per HB estimation (Ralf, 2026-11-24): CHAINS runs of app/hb.py, each from its own random stream, side by
side in processes of their own, merged into one result.

Each chain runs the full burn-in and 1/CHAINS of the iterations, so the kept draws of all chains together are as many as
those of one chain before; with free cores the estimation takes about as long as the burn-in plus a quarter of the
iterations. hb.estimate runs unchanged in every chain; every keyword argument (prior, prior_groups and any later one)
goes through to it as given. The chains differ only in their random stream: default_rng([seed, chain]), independent
streams from one seed sequence, so a run stays reproducible and gives the same result in processes or one after another.

Merged: the posterior means per person (beta, person_stat), mu, the diagonal of Sigma, spread and the means of the
hierarchical prior are means over the chains (every chain keeps the same number of draws); the traces (mu, log-likelihood,
diagonal of Sigma, summaries) are the chains one after another, `chains` says how many, so app/hb_convergence.py cuts
them apart again and compares the chains (multi-chain split-R-hat and ESS of Vehtari et al., 2021). The kept draws of all
betas for predictions ("draws", hb.KEEP_DRAWS) come evenly from every chain. RLH and log-likelihood at the merged posterior
means are computed anew. The split-R-hat per person compares the halves of all chains (2 x CHAINS parts) from sums and sums
of squares collected in every chain (an addition to person_stat, so hb.estimate needs no change). The Geweke-type check
runs per chain; its limits are Bonferroni-corrected over all chains (log-likelihood: CHAINS comparisons, means: CHAINS x k),
so a healthy run is flagged no more often than one chain was before. Keys a later version of hb.estimate adds are merged by
kind: numbers and arrays of the same shape averaged, names ending in "_trace" put one after another, anything else taken from
the first chain.

The callbacks summary, person_stat and spread travel to the processes and must be picklable (module-level functions or
functools.partial of them, not lambdas or closures). Processes: HB_CHAIN_PROCESSES from the environment, else as many as
the process may use cores, at most CHAINS; with one the chains run one after another in this process. The processes are
started fresh ("spawn": safe in a process with threads, the same on Linux and Windows) and inherit the low priority of an
estimation process (nice 19, app/estimate.py).
"""
import math
import multiprocessing
import os
import queue as queue_module
import time
import traceback

import numpy as np

from app import hb

CHAINS = 4           # chains per estimation (Ralf, 2026-11-24)
WAIT = 5.0           # seconds between two looks at the chain processes while no message comes
# the keys hb.estimate returns that are merged in their own way (all others by kind, merge())
_OWN = {"beta", "person_stat", "rlh", "chance_rlh", "loglik", "persons", "sets", "kept", "checked", "converged",
        "geweke_ll", "geweke_mu", "geweke_mu_flagged", "geweke_ll_limit", "geweke_mu_limit", "geweke_ll_hint",
        "ll_halves", "person_rhat", "summaries", "draws", "settings", "seconds", "acceptance", "shift_acceptance",
        "rho"}


def chains_of(cfg):
    """How many chains the settings ask for: "chains" (settings since 2026-11-24), else one, as every result before."""
    try:
        return max(1, int((cfg or {}).get("chains") or 1))
    except (TypeError, ValueError):
        return 1


def processes(chains):
    """Processes for `chains` chains: HB_CHAIN_PROCESSES, else the cores this process may use; at most `chains`."""
    wanted = os.environ.get("HB_CHAIN_PROCESSES")
    try:
        n = int(wanted) if wanted else None
    except ValueError:
        n = None
    if n is None:
        try:
            n = len(os.sched_getaffinity(0))
        except (AttributeError, OSError):
            n = os.cpu_count() or 1
    return max(1, min(int(chains), n))


class Moments:
    """person_stat for one chain: the caller's own person_stat (if any) and, per half of the kept draws, the betas and
    their squares, so that averaged over the kept draws (as hb.estimate does) they give the sums of each half. The halves
    are those of hb.estimate: the first and the last `half` kept draws, the middle one dropped for an odd number."""

    def __init__(self, inner, kept, k):
        self.inner, self.kept, self.k = inner, int(kept), int(k)
        self.half = self.kept // 2
        self.index = 0

    def __call__(self, B):
        index, self.index = self.index, self.index + 1
        zero = np.zeros_like(B)
        if index < self.half:
            parts = [B, B * B, zero, zero]
        elif index >= self.kept - self.half:
            parts = [zero, zero, B, B * B]
        else:
            parts = [zero] * 4
        own = [np.asarray(self.inner(B), dtype=float)] if self.inner is not None else []
        return np.column_stack(own + parts)

    def split(self, stat, kept):
        """(the caller's person_stat or None, sums 2 x n x k, squares 2 x n x k) from the averaged stat of one chain."""
        k = self.k
        moments = stat[:, -4 * k:] * kept
        inner = stat[:, :-4 * k] if self.inner is not None else None
        sums = np.stack([moments[:, :k], moments[:, 2 * k:3 * k]])
        squares = np.stack([moments[:, k:2 * k], moments[:, 3 * k:]])
        return inner, sums, squares


def person_rhat(sums, squares, h):
    """Classic split-R-hat per person and parameter over M parts (the halves of all chains) from their sums and sums of
    squares (arrays M x n x k, h draws per part; Gelman et al., 2013; Vehtari et al., 2021, eq. 1 to 4, without rank
    normalisation): persons x parameters, nan where a part does not vary or fewer than 4 draws per part. For M = 2 the
    value of hb.person_rhat."""
    n, k = sums.shape[1:]
    if h < 4:
        return np.full((n, k), np.nan)
    means = sums / h
    variances = np.maximum((squares - h * means ** 2) / (h - 1), 0.0)
    W = variances.mean(axis=0)
    between = h * means.var(axis=0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        value = np.sqrt(((h - 1) / h * W + between / h) / W)
    return np.where(W > 0, value, np.nan)


def _chain_args(chains, chain, kwargs, k):
    """The arguments of hb.estimate for one chain: its random stream, its share of the iterations and of the kept draws
    of all betas, and person_stat with the moments for the split-R-hat per person."""
    draws = int(kwargs.get("draws", 10000)) // chains
    thin = int(kwargs.get("thin", 10))
    keep = kwargs.get("keep", hb.KEEP_DRAWS)
    # the betas the callbacks see: without the columns common to all persons (hb.estimate, `fixed`)
    moments = Moments(kwargs.get("person_stat"), draws // thin, k - len(_fixed(kwargs)))
    return {**kwargs, "seed": [int(kwargs.get("seed", 1)), chain], "draws": draws,
            "keep": math.ceil(keep / chains) if keep else keep, "person_stat": moments}, moments


def _run_chain(messages, chain, person_sets, k, kwargs):
    """In a process of its own: one chain, its progress and its result (or the error) as messages."""
    last = [-1.0]

    def progress(fraction):
        if fraction - last[0] >= 0.02 or fraction >= 1:
            last[0] = fraction
            messages.put(("progress", chain, float(fraction)))

    try:
        messages.put(("done", chain, hb.estimate(person_sets, k, progress=progress, **kwargs)))
    except BaseException as e:  # noqa: BLE001 - every end of a chain must reach the waiting process
        messages.put(("error", chain, f"{type(e).__name__}: {e}\n{traceback.format_exc()}"))


def _fixed(kwargs):
    """The columns common to all persons (hb.estimate's `fixed`), sorted, or []."""
    fixed = kwargs.get("fixed")
    return sorted({int(j) for j in fixed}) if fixed is not None and len(fixed) else []


def estimate(person_sets, k, chains=CHAINS, progress=None, workers=None, **kwargs):
    """hb.estimate in `chains` chains, merged (module text). Every keyword argument goes to hb.estimate as given, but
    seed, draws and keep, which are shared out over the chains; workers: processes (None: processes()). With one chain
    exactly hb.estimate, plus "chains": 1."""
    chains = max(1, int(chains))
    if chains == 1:
        return {**hb.estimate(person_sets, k, progress=progress, **kwargs), "chains": 1}
    started = time.monotonic()
    args = [_chain_args(chains, c, kwargs, k) for c in range(chains)]
    workers = processes(chains) if workers is None else max(1, min(int(workers), chains))
    if workers == 1:
        results = []
        for c, (kw, _) in enumerate(args):
            share = (lambda c: (lambda f: progress((c + f) / chains)))(c) if progress else None
            results.append(hb.estimate(person_sets, k, progress=share, **kw))
    else:
        results = _in_processes(person_sets, k, [kw for kw, _ in args], workers, progress)
    out = merge(results, [m for _, m in args], person_sets, k, kwargs)
    out["seconds"] = time.monotonic() - started
    return out


def _in_processes(person_sets, k, chain_kwargs, workers, progress):
    """The chains in up to `workers` processes at once; their results in chain order. An error in a chain, a process that
    ends without its result or an exception of progress (a cancelled run) ends the other processes."""
    ctx = multiprocessing.get_context("spawn")
    messages = ctx.Queue()
    waiting = list(range(len(chain_kwargs)))
    running, results, fractions = {}, {}, [0.0] * len(chain_kwargs)
    reported = -1.0
    try:
        while len(results) < len(chain_kwargs):
            while waiting and len(running) < workers:
                c = waiting.pop(0)
                p = ctx.Process(target=_run_chain, args=(messages, c, person_sets, k, chain_kwargs[c]),
                                name=f"hb-chain-{c}", daemon=True)
                p.start()
                running[c] = p
            try:
                kind, c, value = messages.get(timeout=WAIT)
            except queue_module.Empty:
                for c, p in running.items():
                    if p.exitcode is not None and c not in results:
                        try:  # its last message may still be on its way
                            kind, c2, value = messages.get(timeout=WAIT)
                        except queue_module.Empty:
                            raise RuntimeError(f"HB chain {c} ended without a result (exit code {p.exitcode})")
                        _take(kind, c2, value, results, fractions, running)
                        break
                continue
            _take(kind, c, value, results, fractions, running)
            if progress:
                mean = sum(fractions) / len(fractions)
                if mean - reported >= 0.02 or mean >= 1:
                    reported = mean
                    progress(mean)
    finally:
        for p in running.values():
            if p.is_alive():
                p.terminate()
        for p in running.values():
            p.join(timeout=WAIT)
        messages.close()
    return [results[c] for c in range(len(chain_kwargs))]


def _take(kind, c, value, results, fractions, running):
    if kind == "error":
        raise RuntimeError(f"HB chain {c} failed: {value}")
    if kind == "progress":
        fractions[c] = value
        return
    results[c] = value
    fractions[c] = 1.0
    p = running.pop(c, None)
    if p is not None:
        p.join(timeout=WAIT)


def _mean(values):
    arrays = [np.asarray(v, dtype=float) for v in values]
    return sum(arrays) / len(arrays)


def _numeric(v):
    if isinstance(v, (bool, np.bool_)):
        return False
    if isinstance(v, (int, float, np.integer, np.floating)):
        return True
    return isinstance(v, np.ndarray) and v.dtype.kind in "fiu"


def _fit(person_sets, k, beta, fixed, gamma):
    """(log-likelihood per person, RLH, chance RLH) at the posterior means: the betas and, for the columns common to all
    persons, their means gamma (columns ordered as hb.estimate orders them: the others first)."""
    if fixed:
        free = [j for j in range(k) if j not in set(fixed)]
        order = free + fixed
        from app.choice_model import ChoiceSet

        person_sets = [[ChoiceSet(np.asarray(s.X, dtype=float)[:, order], s.chosen) for s in sets] for sets in person_sets]
        beta = np.hstack([beta, np.broadcast_to(gamma, (len(beta), len(fixed)))])
    data = hb.Data(person_sets, k)
    ll = data.loglik(beta)
    with np.errstate(invalid="ignore", divide="ignore"):
        rlh = np.exp(ll / data.sets_per_person)
    return ll, rlh, np.exp(data.chance_loglik() / data.sets_per_person)


def merge(results, moments, person_sets, k, kwargs):
    """One result of the shape of hb.estimate from the chains' results (module text). With columns common to all
    persons (`fixed`): their means averaged, their interval from the draws of all chains, their kept draws ("fixed_draws")
    taken like "draws"."""
    first = results[0]
    chains = len(results)
    kept = [r["kept"] for r in results]
    total = sum(kept)
    beta = _mean([r["beta"] for r in results])
    out = {}
    # keys a later hb.estimate adds (and mu, sigma_diag, spread, prior_a, the traces): by kind
    for key, value in first.items():
        if key in _OWN:
            continue
        values = [r.get(key) for r in results]
        if key.endswith("_trace") and all(isinstance(v, np.ndarray) for v in values):
            out[key] = np.concatenate(values)
        elif all(v is not None and _numeric(v) for v in values) and len({np.shape(v) for v in values}) == 1:
            out[key] = _mean(values) if isinstance(value, np.ndarray) else float(_mean(values))
        elif all(isinstance(v, (bool, np.bool_)) for v in values):
            out[key] = all(values)
        else:
            out[key] = value
    # the caller's person_stat and the halves of every chain
    inner, sums, squares = [], [], []
    for r, m, n_kept in zip(results, moments, kept):
        own, s, q = m.split(r["person_stat"], n_kept)
        inner.append(own)
        sums.append(s)
        squares.append(q)
    h = moments[0].half
    person_stat = None if inner[0] is None else _mean(inner)
    # Geweke-type check per chain (mu and the common parameters), limits Bonferroni-corrected over all chains
    ll_limit = hb.geweke_limits(chains)[1]
    mu_limit = hb.geweke_limits(chains * max(len(first["geweke_mu"] or []), 1))[1]
    z_ll = [r["geweke_ll"] for r in results]
    checked = all(r["checked"] for r in results)
    z_ll_max = max((z for z in z_ll if z is not None), key=abs, default=None)
    z_mu = []
    for j in range(len(first["geweke_mu"] or [])):
        column = [r["geweke_mu"][j] for r in results if r["geweke_mu"] and r["geweke_mu"][j] is not None]
        z_mu.append(max(column, key=abs) if column else None)
    flagged = sum(1 for r in results for z in (r["geweke_mu"] or []) if z is not None and abs(z) > mu_limit)
    halves = [r["ll_halves"] for r in results]
    summaries = [r["summaries"] for r in results]
    draws = [r["draws"] for r in results if r["draws"] is not None]
    keep = kwargs.get("keep", hb.KEEP_DRAWS)
    draws = np.concatenate(draws) if draws else None
    chosen = None
    if draws is not None and keep and len(draws) > keep:
        chosen = np.unique(np.linspace(0, len(draws) - 1, keep).round().astype(int))
        draws = draws[chosen]
    fixed = _fixed(kwargs)
    if fixed and "fixed_trace" in out:
        trace = out["fixed_trace"]
        if len(trace):
            out["fixed_lo"], out["fixed_hi"] = np.percentile(trace, [2.5, 97.5], axis=0)
        f_draws = [r.get("fixed_draws") for r in results if r.get("fixed_draws") is not None]
        f_draws = np.concatenate(f_draws) if f_draws else None
        out["fixed_draws"] = f_draws[chosen] if f_draws is not None and chosen is not None else f_draws
    fit_ll, rlh, chance = _fit(person_sets, k, beta, fixed, out.get("fixed"))
    settings = {**first["settings"], "seed": kwargs.get("seed", 1), "draws": int(kwargs.get("draws", 10000)),
                "chains": chains}
    out.update({
        "beta": beta, "person_stat": person_stat,
        "rlh": rlh, "chance_rlh": chance, "loglik": float(fit_ll.sum()), "persons": first["persons"],
        "sets": first["sets"],
        "kept": total, "chains": chains, "chain_kept": kept[0],
        "acceptance": _avg([r["acceptance"] for r in results]),
        "shift_acceptance": _avg([r["shift_acceptance"] for r in results]),
        "rho": _avg([r["rho"] for r in results]),
        "checked": checked, "converged": bool(checked and flagged == 0), "geweke_ll": z_ll_max, "geweke_mu": z_mu,
        "geweke_mu_flagged": flagged, "geweke_ll_limit": round(ll_limit, 3), "geweke_mu_limit": round(mu_limit, 3),
        "geweke_ll_hint": bool(checked and z_ll_max is not None and abs(z_ll_max) > ll_limit),
        "ll_halves": ([float(np.mean([x[0] for x in halves])), float(np.mean([x[1] for x in halves]))]
                      if all(halves) else None),
        "person_rhat": person_rhat(np.concatenate(sums), np.concatenate(squares), h),
        "summaries": np.concatenate(summaries) if all(s is not None for s in summaries) else None,
        "draws": draws, "settings": settings,
    })
    return out


def _avg(values):
    values = [v for v in values if v is not None]
    return float(np.mean(values)) if values else None


# --- texts -----------------------------------------------------------------------------------------------------

def length_text(settings, lang):
    """Length of the run for the codebook and the method lines: "4 Ketten mit je 5.000 Burn-in und 5.000 Iterationen"
    ("4 chains of 5,000 burn-in and 5,000 iterations each"); for a result of one chain the words as before
    ("5000 Burn-in, 20000 Iterationen")."""
    from app.units import number

    s = settings or {}
    chains = chains_of(s)
    if chains == 1:
        return (f"{s['burn']} burn-in, {s['draws']} iterations" if lang == "en"
                else f"{s['burn']} Burn-in, {s['draws']} Iterationen")
    burn, each = number(int(s["burn"]), 0, lang), number(int(s["draws"]) // chains, 0, lang)
    return (f"{chains} chains of {burn} burn-in and {each} iterations each" if lang == "en"
            else f"{chains} Ketten mit je {burn} Burn-in und {each} Iterationen")
