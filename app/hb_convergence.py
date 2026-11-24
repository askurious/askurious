"""Convergence of an HB estimation (app/hb.py, app/hb_chains.py): effective sample size, split-R-hat and the stored trace.

Since 2026-11-24 every estimation runs several chains (app/hb_chains.py, CHAINS = 4), each from its own random stream,
and R-hat is the multi-chain split-R-hat of Vehtari et al. (2021): the kept draws of every chain are cut into two
halves, and all halves are compared like chains, after rank normalisation (pooled ranks, z = Phi^-1((r - 3/8) /
(S + 1/4))); reported is the larger of the R-hat of the rank-normalised draws and of the rank-normalised folded draws
|x - median| (the latter finds parts that differ in spread). A result of one chain (all results before, settings without
"chains") is judged as before from the two halves of that chain. The effective sample size is the bulk ESS of the same
paper: the autocorrelations of the rank-normalised parts combined over all parts, summed with Geyer's (1992) initial
positive sequence (pairs of consecutive autocorrelations while their sum is positive), made monotone (no pair above the
one before it; since the audit of 2026-10-05, H-4: without it, single chains came out far too low). Own implementation
from the formulas of both papers. Both per population mean mu_j (MaxDiff: the zero-centred statement means) and the ESS
also for the log-likelihood.

Rule (Vehtari et al., 2021): max R-hat < 1.01 and min ESS >= 400, over mu, the log-likelihood and the means the
page shows (since the audit of 2026-10-05, W-2) and, since 2026-11-23, the diagonal of Sigma (the variances between
persons per parameter, on which the SD and quartiles of the report rest); the Geweke-type check of app/hb.py (per
chain, limits corrected over all chains) stays an additional condition. The individual utilities get no rule, only a
hint: the share of persons with at least one utility whose classic split-R-hat (from running sums over the halves of
every chain, app/hb.person_rhat, app/hb_chains.person_rhat) exceeds PERSON_RHAT_LIMIT; the hint shows above
PERSON_SHARE_HINT. A result stored before keeps its judgement and shows none of the new values. The log-likelihood trace
is stored thinned to at most TRACE_POINTS points (shared out over the chains) for the small line on the results page,
one line per chain.
"""
import math
from statistics import NormalDist

import numpy as np

RHAT_LIMIT = 1.01
ESS_MIN = 400
TRACE_POINTS = 500
# individual utilities (Festlegung von Askurious, 2026-11-23): 1.05 as the looser per-person limit, since a person's
# few choices give wide and often skewed posteriors and N x k values are compared at once; the hint shows only above 5 %
# of persons, because with N x k values a few exceed 1.05 by chance even in a settled chain (simulated healthy CBC,
# 200 persons, default length: tests/analysis/test_hb_convergence.py), while a chain that has not settled lifts the
# share far above that
PERSON_RHAT_LIMIT = 1.05
PERSON_SHARE_HINT = 0.05
# "neu schätzen mit doppelter Länge" up to 4 times the configured length, two doublings (Festlegung von Askurious): 8 times
# until the default grew from 15 000 to 25 000 iterations (audit 2026-10-05, H-2); 4 keeps the longest run near the
# old longest (100 000 instead of 120 000 iterations, about 4 minutes for 200 persons on the test computer)
LONGER_MAX = 4
VEHTARI = ("Vehtari, A., Gelman, A., Simpson, D., Carpenter, B., & Bürkner, P.-C. (2021). Rank-normalization, "
           "folding, and localization: An improved R̂ for assessing convergence of MCMC (with discussion). "
           "Bayesian Analysis, 16(2), 667–718. https://doi.org/10.1214/20-BA1221")
GEYER = ("Geyer, C. J. (1992). Practical Markov chain Monte Carlo. Statistical Science, 7(4), 473–483. "
         "https://doi.org/10.1214/ss/1177011137")
SOURCES = (VEHTARI, GEYER)

_inv_cdf = np.vectorize(NormalDist().inv_cdf)


def _halves(x, chains=1):
    """Every chain cut into two halves (the middle draw dropped for an odd length): array 2 chains x n. x holds the
    chains one after another, each with the same number of draws."""
    x = np.asarray(x, dtype=float).reshape(chains, -1)
    h = x.shape[1] // 2
    return np.concatenate([x[:, :h], x[:, x.shape[1] - h:]])


def _ranks(x):
    """Ranks 1..S of all values, ties get their average rank."""
    flat = x.ravel()
    order = flat.argsort(kind="mergesort")
    ranks = np.empty(len(flat))
    ranks[order] = np.arange(1, len(flat) + 1)
    _, inverse, counts = np.unique(flat, return_inverse=True, return_counts=True)
    sums = np.bincount(inverse, weights=ranks)
    return (sums / counts)[inverse].reshape(x.shape)


def rank_normalize(chains):
    """Normal scores of the pooled ranks (Vehtari et al., 2021, eq. 14)."""
    chains = np.asarray(chains, dtype=float)
    return _inv_cdf((_ranks(chains) - 0.375) / (chains.size + 0.25))


def _autocov(x):
    """Autocovariances of one chain at lags 0..n-1 (divided by n), by FFT."""
    n = len(x)
    y = x - x.mean()
    size = 1 << (2 * n - 1).bit_length()
    f = np.fft.rfft(y, size)
    return np.fft.irfft(f * np.conj(f), size)[:n] / n


def ess_chains(chains):
    """Effective sample size of M chains (array M x n), written from the formulas of Vehtari et al. (2021, eq. 10
    and 11) and Geyer (1992, sec. 3.3); None if the draws do not vary.

    rho_t = 1 - (W - mean_m acov_m(t)) / var+ with rho_0 = 1; the lag pairs P_k = rho_2k + rho_2k+1 are summed as
    long as they stay positive (initial positive sequence), each capped by the one before (initial monotone
    sequence), and tau = 2 * sum(P_k) - 1, ESS = M n / tau. Festlegung von Askurious: ESS at most M n, the number of
    draws in the M parts together (the kept draws, less the middle one for an odd length): a chain cannot carry more
    independent information than it has draws, so antithetic draws (tau under 1, ESS above the draws) are not
    counted as better than independent ones, and tau <= 0 gives M n instead of no number."""
    chains = np.asarray(chains, dtype=float)
    m, n = chains.shape
    if n < 4:
        return None
    acov = np.array([_autocov(c) for c in chains])
    W = (acov[:, 0] * n / (n - 1)).mean()
    var_plus = W * (n - 1) / n + (chains.mean(axis=1).var(ddof=1) if m > 1 else 0.0)
    if not var_plus > 0:
        return None
    rho = 1 - (W - acov.mean(axis=0)) / var_plus
    rho[0] = 1.0
    pairs = rho[:n - n % 2].reshape(-1, 2).sum(axis=1)
    stop = np.flatnonzero(pairs <= 0)
    kept = pairs[:stop[0] if stop.size else pairs.size]
    tau = 2 * np.minimum.accumulate(kept).sum() - 1
    draws = m * n
    return float(draws if tau <= 0 else min(draws / tau, draws))


def rhat_chains(chains):
    """Classic potential scale reduction of M chains (array M x n); None if the draws do not vary."""
    chains = np.asarray(chains, dtype=float)
    n = chains.shape[1]
    W = chains.var(axis=1, ddof=1).mean()
    if not W > 0:
        return None
    B = n * chains.mean(axis=1).var(ddof=1)
    return float(math.sqrt(((n - 1) / n * W + B / n) / W))


def _judgeable(x, chains):
    return (len(x) >= 8 * chains and len(x) % chains == 0 and np.all(np.isfinite(x)) and np.ptp(x) > 0)


def rhat(x, chains=1):
    """Rank-normalised split-R-hat of `chains` chains (x: their draws one after another): max of the bulk and the folded
    value (Vehtari et al., 2021)."""
    x = np.asarray(x, dtype=float)
    if not _judgeable(x, chains):
        return None
    bulk = rhat_chains(rank_normalize(_halves(x, chains)))
    folded = rhat_chains(rank_normalize(_halves(np.abs(x - np.median(x)), chains)))
    values = [v for v in (bulk, folded) if v is not None]
    return max(values) if values else None


def ess(x, chains=1):
    """Bulk effective sample size of `chains` chains (rank-normalised halves of every chain, Vehtari et al., 2021)."""
    x = np.asarray(x, dtype=float)
    if not _judgeable(x, chains):
        return None
    return ess_chains(rank_normalize(_halves(x, chains)))


def thin_trace(trace, points=TRACE_POINTS, chains=1):
    """At most `points` values of the trace, evenly spaced, first and last kept; with several chains (one after another
    in trace) points // chains of each, again one after another."""
    trace = np.asarray(trace, dtype=float)
    if chains > 1 and len(trace) % chains == 0:
        return np.concatenate([thin_trace(part, points // chains) for part in trace.reshape(chains, -1)])
    if len(trace) <= points:
        return trace
    return trace[np.unique(np.linspace(0, len(trace) - 1, points).round().astype(int))]


def diagnose(means, ll_trace, geweke_ok=True, shown=None, sigma=None, person=None, chains=1):
    """Convergence of the kept draws: means (draws x parameters, the population means to judge), the
    log-likelihood trace and shown (draws x values: the means over persons the page shows at every kept draw, e.g.
    part-worths or scores; audit 2026-10-05, W-2: judged too, they are what is read), sigma (draws x parameters:
    the diagonal of Sigma, judged like mu) and person (persons x parameters: split-R-hat of the individual utilities,
    app/hb.person_rhat; only the hint). chains: how many chains the draws come from, one after another in every array
    (app/hb_chains.py). JSON-ready dict for the stored result."""
    means = np.asarray(means, dtype=float)
    ll = np.asarray(ll_trace, dtype=float)
    cols = means.T if means.ndim == 2 and len(means) else []
    chains = max(1, int(chains or 1))
    rhats = [rhat(c, chains) for c in cols]
    esss = [ess(c, chains) for c in cols]
    ess_ll = ess(ll, chains)
    shown = np.asarray(shown, dtype=float) if shown is not None else None
    extra = list(shown.T) if shown is not None and shown.ndim == 2 and len(shown) else []
    rhat_shown = [rhat(c, chains) for c in extra]
    ess_shown = [ess(c, chains) for c in extra]
    sigma = np.asarray(sigma, dtype=float) if sigma is not None else None
    var_cols = list(sigma.T) if sigma is not None and sigma.ndim == 2 and len(sigma) else []
    rhat_sigma = [rhat(c, chains) for c in var_cols]
    ess_sigma = [ess(c, chains) for c in var_cols]

    def valid(v):
        return v is not None and math.isfinite(v)

    required = rhats + rhat_shown + rhat_sigma + esss + [ess_ll] + ess_shown + ess_sigma
    complete = bool(len(cols)) and all(valid(v) for v in required)
    complete = complete and len(ll) == len(means) and (shown is None or
                (shown.ndim == 2 and shown.shape[0] == len(means) and shown.shape[1] > 0))
    complete = complete and (sigma is None or (sigma.ndim == 2 and sigma.shape[0] == len(means)
                                               and sigma.shape[1] > 0))
    rh = [v for v in rhats + rhat_shown + rhat_sigma if valid(v)]
    es = [v for v in esss + [ess_ll] + ess_shown + ess_sigma if valid(v)]
    rhat_max = max(rh) if rh else None
    ess_min = min(es) if es else None
    ok = bool(complete and rhat_max is not None and ess_min is not None and rhat_max < RHAT_LIMIT and ess_min >= ESS_MIN
              and geweke_ok)

    def r(v, d):
        return round(float(v), d) if valid(v) else None

    out = {"rhat": [r(v, 4) for v in rhats], "ess": [r(v, 1) for v in esss], "ess_ll": r(ess_ll, 1),
           "ess_shown": r(min([v for v in ess_shown if valid(v)], default=None), 1),
           "diag_complete": bool(complete),
           "rhat_max": r(rhat_max, 4), "ess_min": r(ess_min, 1), "diag_ok": ok,
           "ll_trace": [r(v, 2) for v in thin_trace(ll, chains=chains)]}
    if chains > 1:  # only for several chains, so a result of one chain stays as it was
        out["chains"] = chains
    if sigma is not None:
        out.update(rhat_sigma_max=r(max([v for v in rhat_sigma if valid(v)], default=None), 4),
                   ess_sigma_min=r(min([v for v in ess_sigma if valid(v)], default=None), 1))
    if person is not None:
        out.update(person_check(person))
    return out


def person_check(person_rhat):
    """Hint on the individual utilities: how many persons have at least one utility with split-R-hat above
    PERSON_RHAT_LIMIT (persons without any value to judge, e.g. no parameter that moves, are left out)."""
    values = np.asarray(person_rhat, dtype=float)
    if values.ndim != 2 or not values.size:
        return {"person_rhat_flagged": None, "person_rhat_checked": 0, "person_rhat_share": None}
    finite = np.isfinite(values)
    judged = finite.any(axis=1)
    flagged = (np.where(finite, values, 0.0) > PERSON_RHAT_LIMIT).any(axis=1)
    checked = int(judged.sum())
    count = int((flagged & judged).sum())
    return {"person_rhat_flagged": count, "person_rhat_checked": checked,
            "person_rhat_share": round(count / checked, 4) if checked else None}


def person_note(res):
    """The short note on the individual utilities for the simulator (None without the hint)."""
    if not person_hint(res):
        return None
    from app.lang import de_en
    from app.units import number, page_lang, percent_text

    return de_en("Bei {p} der Personen hat mindestens ein individueller Nutzen ein split-R̂ über {l}: Die Werte, mit "
                 "denen hier gerechnet wird, haben sich vielleicht noch nicht eingependelt; ggf. die HB-Schätzung mit "
                 "doppelter Länge wiederholen.",
                 "For {p} of the people at least one individual utility has a split R̂ above {l}: the values used "
                 "here may not have settled yet; repeat the HB estimation with double length if needed.",
                 p=percent_text(res["person_rhat_share"], page_lang()), l=number(PERSON_RHAT_LIMIT, 2, page_lang()))


def person_hint(res):
    """True when the share of persons with an individual utility above PERSON_RHAT_LIMIT exceeds PERSON_SHARE_HINT
    (False for a result stored before the check)."""
    share = res.get("person_rhat_share")
    return share is not None and share > PERSON_SHARE_HINT


def trace_chart(trace, width=720, height=90, chains=1):
    """Geometry of the small log-likelihood line: SVG polyline points (one line per chain, "lines"; "points" the first)
    and the axis labels."""
    t = np.asarray(trace or [], dtype=float)
    chains = max(1, int(chains or 1))
    if chains > 1 and len(t) % chains:
        chains = 1
    if len(t) < 2 * chains or not np.all(np.isfinite(t)):
        return None
    x0, x1, y0, y1 = 60, width - 8, 8, height - 18
    lo, hi = float(t.min()), float(t.max())
    span = hi - lo or 1.0
    lines = []
    for part in t.reshape(chains, -1):
        xs = x0 + np.arange(len(part)) / (len(part) - 1) * (x1 - x0)
        ys = y1 - (part - lo) / span * (y1 - y0)
        lines.append(" ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys)))
    return {"points": lines[0], "lines": lines, "chains": chains, "width": width, "height": height,
            "x0": x0, "x1": x1, "y0": y0, "y1": y1, "lo": lo, "hi": hi}


def longer_settings(settings, defaults):
    """The settings of a run with doubled burn-in and iterations, or None beyond LONGER_MAX x the configured
    length."""
    burn, draws = int(settings["burn"]) * 2, int(settings["draws"]) * 2
    if burn + draws > LONGER_MAX * (int(defaults["burn"]) + int(defaults["draws"])):
        return None
    # the chains of the configured settings (a result of one chain from before runs again with them, 2026-11-24)
    chains = defaults.get("chains") or settings.get("chains")
    return {**settings, "burn": burn, "draws": draws, **({"chains": chains} if chains else {})}


def warning(res):
    """The note for a result that fails the rule, None if it passes (or predates the rule)."""
    from app.units import number, page_lang

    if res.get("diag_ok", res.get("converged")):
        return None
    from app.lang import de_en

    lang = page_lang()  # "R̂ max. 1.050 (limit under 1.01)" on an English page (Mathe-Audit 2026-10-06, Z-7)

    # in plain words, one level of brackets, numbers with the page's separators (UI/UX audit 2026-10-07, W71); the
    # terms R̂ and ESS have their glossary circles in the note on the page (_conjoint_hb.html, _maxdiff_hb.html)
    parts = []
    if res.get("diag_complete") is False:
        parts.append(de_en("mindestens eine Konvergenzdiagnose fehlt, etwa weil ein Wert in der Kette nicht variiert",
                           "at least one convergence diagnostic is missing, for example because a value does not vary in the chain"))
    if res.get("rhat_max") is not None and res["rhat_max"] >= RHAT_LIMIT:
        parts.append(de_en("die Teilketten stimmen noch nicht überein (R̂ max. {r}, nötig unter {limit})",
                           "the parts of the chain do not agree yet (R̂ max. {r}, needed under {limit})",
                           r=number(res['rhat_max'], 3, lang), limit=number(RHAT_LIMIT, 2, lang)))
    if res.get("ess_min") is not None and res["ess_min"] < ESS_MIN:
        # the ESS is an equivalent number, not a count of certain independent draws (audit 2026-10-10, W-4)
        parts.append(de_en("die {kept} Ziehungen tragen nur so viel Information wie {e} unabhängige Ziehungen, nötig "
                           "sind {limit} (ESS min.)",
                           "the {kept} draws carry only as much information as {e} independent draws, {limit} are "
                           "needed (ESS min.)",
                           e=number(res['ess_min'], 0, lang), kept=number(res.get('kept') or 0, 0, lang),
                           limit=number(ESS_MIN, 0, lang)))
    if not res.get("geweke_ok", True):
        parts.append(de_en("erste und zweite Hälfte der behaltenen Ziehungen unterscheiden sich (Geweke-artiger Vergleich)",
                           "first and second half of the kept draws differ (Geweke-type comparison)"))
    reason = "; ".join(parts) or de_en("zu wenige behaltene Ziehungen für die Prüfung", "too few kept draws for the check")
    return de_en("Die Schätzung hat sich vielleicht noch nicht eingependelt: {reason}. Mit doppelter Länge neu schätzen "
                 "oder die Werte nur als Richtung lesen.",
                 "The estimation may not have settled yet: {reason}. Estimate again with double length or read the "
                 "values only as a direction.", reason=reason)


def view(res):
    """What the HB block shows about convergence: rule, values, trace line, settings of a longer run (measured
    against the configured length; outside an app the stored settings count as configured)."""
    from flask import has_app_context

    from app import maxdiff_hb

    settings = res["settings"]
    defaults = maxdiff_hb.settings() if has_app_context() else settings
    advise = not res.get("diag_ok") or bool(res.get("geweke_ll_hint")) or person_hint(res)
    return {"ok": bool(res.get("diag_ok")), "complete": res.get("diag_complete", True),
            "rhat_max": res.get("rhat_max"), "ess_min": res.get("ess_min"),
            "ess_ll": res.get("ess_ll"), "geweke_ok": res.get("geweke_ok", True), "kept": res.get("kept"),
            # limits and hint of the Geweke-type check (app/hb.py, geweke_check); None for a result from before
            "geweke_ll": res.get("geweke_ll"), "geweke_ll_limit": res.get("geweke_ll_limit"),
            "geweke_ll_hint": bool(res.get("geweke_ll_hint")), "geweke_mu_limit": res.get("geweke_mu_limit"),
            "geweke_mu_flagged": res.get("geweke_mu_flagged"),
            "rhat_limit": RHAT_LIMIT, "ess_limit": ESS_MIN,
            "chart": trace_chart(res.get("ll_trace"), chains=res.get("chains") or 1),
            # independent chains (app/hb_chains.py); 1 for every result from before 2026-11-24
            "chains": res.get("chains") or 1,
            # the diagonal of Sigma and the hint on the individual utilities; None for a result from before
            "rhat_sigma_max": res.get("rhat_sigma_max"), "ess_sigma_min": res.get("ess_sigma_min"),
            "person_rhat_share": res.get("person_rhat_share"), "person_rhat_flagged": res.get("person_rhat_flagged"),
            "person_rhat_checked": res.get("person_rhat_checked"), "person_hint": person_hint(res),
            "person_limit": PERSON_RHAT_LIMIT, "person_share_hint": PERSON_SHARE_HINT, "advise_longer": advise,
            "person_note": person_note(res),
            # the button also when the rule passes but a hint (log-likelihood, individual utilities) advises a longer run
            "longer": longer_settings(settings, defaults) if advise else None}


def describe(res, lang=None):
    """Codebook part on convergence ("" for a result without the values); `lang`: the reader's language (German or
    English, None: the page's; app.items.base.reader)."""
    from app.items.base import reader
    from app.units import number

    if res.get("diag_complete") is False:
        return ("; convergence not assessable: at least one diagnostic is missing" if reader(lang) == "en" else
                "; Konvergenz nicht beurteilbar: Mindestens eine Diagnose fehlt")
    if res.get("rhat_max") is None or res.get("ess_min") is None:
        return ""
    share = res.get("person_rhat_share")
    if reader(lang) == "en":
        what = " of the population means and variances" if res.get("rhat_sigma_max") is not None else ""
        person = (f"; individual utilities: {number(share * 100, 1, 'en')} % of persons with a split R̂ above "
                  f"{number(PERSON_RHAT_LIMIT, 2, 'en')} (note only"
                  f"{'; above 5 %: estimate again with double length' if person_hint(res) else ''})"
                  if share is not None else "")
        chains = (f" over {res['chains']} chains" if (res.get("chains") or 1) > 1 else "")
        return (f"; convergence{what}{chains} {'unremarkable' if res.get('diag_ok') else 'to be checked'}: rank-normalised split R̂ max. "
                f"{number(res['rhat_max'], 3, 'en')}, effective sample size (ESS) min. {number(res['ess_min'], 0, 'en')} of "
                f"{res.get('kept')} draws (rule R̂ < {number(RHAT_LIMIT, 2, 'en')} and ESS ≥ {ESS_MIN}, Vehtari et al., 2021)"
                f"{person}")
    what = " der Populationsmittel und -varianzen" if res.get("rhat_sigma_max") is not None else ""
    person = (f"; individuelle Nutzen: {number(share * 100, 1)} % der Personen mit einem split-R̂ über "
              f"{number(PERSON_RHAT_LIMIT, 2)} (nur Hinweis"
              f"{'; über 5 %: mit doppelter Länge neu schätzen' if person_hint(res) else ''})"
              if share is not None else "")
    chains = (f" über {res['chains']} Ketten" if (res.get("chains") or 1) > 1 else "")
    return (f"; Konvergenz{what}{chains} {'unauffällig' if res.get('diag_ok') else 'prüfen'}: rang-normalisiertes split-R̂ max. "
            f"{number(res['rhat_max'], 3)}, effektive Stichprobengröße (ESS) min. {number(res['ess_min'])} von "
            f"{res.get('kept')} Ziehungen (Regel R̂ < {number(RHAT_LIMIT, 2)} und ESS ≥ {ESS_MIN}, Vehtari et al., 2021)"
            f"{person}")
