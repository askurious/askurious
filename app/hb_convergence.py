"""Convergence of one HB chain (app/hb.py): effective sample size, split-R-hat and the stored trace.

Only one chain runs (no doubled computing time), so R-hat is the split-R-hat of Vehtari et al. (2021): the
kept draws are cut into two halves that are compared like two chains, after rank normalisation (pooled ranks,
z = Phi^-1((r - 3/8) / (S + 1/4))); reported is the larger of the R-hat of the rank-normalised draws and of
the rank-normalised folded draws |x - median| (the latter finds halves that differ in spread). The effective
sample size is the bulk ESS of the same paper: the autocorrelations of the rank-normalised halves combined
over both halves, summed with Geyer's (1992) initial positive sequence (pairs of consecutive autocorrelations
while their sum is positive), made monotone (no pair above the one before it; since the audit of 2026-10-05, H-4:
without it, single chains came out far too low). Own implementation from the formulas of both papers.
Both per population mean mu_j (MaxDiff: the zero-centred statement means) and the ESS also for the log-likelihood.

Rule (Vehtari et al., 2021): max R-hat < 1.01 and min ESS >= 400, over mu, the log-likelihood and the means the
page shows (since the audit of 2026-10-05, W-2); the Geweke-type check of app/hb.py stays an additional condition. The log-likelihood trace is stored thinned to at most TRACE_POINTS points for the
small line on the results page.
"""
import math
from statistics import NormalDist

import numpy as np

RHAT_LIMIT = 1.01
ESS_MIN = 400
TRACE_POINTS = 500
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


def _halves(x):
    """The chain cut into two halves (the middle draw dropped for an odd length): array 2 x n."""
    x = np.asarray(x, dtype=float)
    h = len(x) // 2
    return np.stack([x[:h], x[-h:]])


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


def rhat(x):
    """Rank-normalised split-R-hat of one chain: max of the bulk and the folded value (Vehtari et al., 2021)."""
    x = np.asarray(x, dtype=float)
    if len(x) < 8 or not np.all(np.isfinite(x)) or not np.ptp(x) > 0:
        return None
    bulk = rhat_chains(rank_normalize(_halves(x)))
    folded = rhat_chains(rank_normalize(_halves(np.abs(x - np.median(x)))))
    values = [v for v in (bulk, folded) if v is not None]
    return max(values) if values else None


def ess(x):
    """Bulk effective sample size of one chain (rank-normalised halves, Vehtari et al., 2021)."""
    x = np.asarray(x, dtype=float)
    if len(x) < 8 or not np.all(np.isfinite(x)) or not np.ptp(x) > 0:
        return None
    return ess_chains(rank_normalize(_halves(x)))


def thin_trace(trace, points=TRACE_POINTS):
    """At most `points` values of the trace, evenly spaced, first and last kept."""
    trace = np.asarray(trace, dtype=float)
    if len(trace) <= points:
        return trace
    return trace[np.unique(np.linspace(0, len(trace) - 1, points).round().astype(int))]


def diagnose(means, ll_trace, geweke_ok=True, shown=None):
    """Convergence of the kept draws: means (draws x parameters, the population means to judge), the
    log-likelihood trace and shown (draws x values: the means over persons the page shows at every kept draw, e.g.
    part-worths or scores; audit 2026-10-05, W-2: judged too, they are what is read). JSON-ready dict for the
    stored result."""
    means = np.asarray(means, dtype=float)
    ll = np.asarray(ll_trace, dtype=float)
    cols = means.T if means.ndim == 2 and len(means) else []
    rhats = [rhat(c) for c in cols]
    esss = [ess(c) for c in cols]
    ess_ll = ess(ll)
    shown = np.asarray(shown, dtype=float) if shown is not None else None
    extra = list(shown.T) if shown is not None and shown.ndim == 2 and len(shown) else []
    rhat_shown = [rhat(c) for c in extra]
    ess_shown = [ess(c) for c in extra]

    def valid(v):
        return v is not None and math.isfinite(v)

    required = rhats + rhat_shown + esss + [ess_ll] + ess_shown
    complete = bool(len(cols)) and all(valid(v) for v in required)
    complete = complete and len(ll) == len(means) and (shown is None or
                (shown.ndim == 2 and shown.shape[0] == len(means) and shown.shape[1] > 0))
    rh = [v for v in rhats + rhat_shown if valid(v)]
    es = [v for v in esss + [ess_ll] + ess_shown if valid(v)]
    rhat_max = max(rh) if rh else None
    ess_min = min(es) if es else None
    ok = bool(complete and rhat_max is not None and ess_min is not None and rhat_max < RHAT_LIMIT and ess_min >= ESS_MIN
              and geweke_ok)

    def r(v, d):
        return round(float(v), d) if valid(v) else None

    return {"rhat": [r(v, 4) for v in rhats], "ess": [r(v, 1) for v in esss], "ess_ll": r(ess_ll, 1),
            "ess_shown": r(min([v for v in ess_shown if valid(v)], default=None), 1),
            "diag_complete": bool(complete),
            "rhat_max": r(rhat_max, 4), "ess_min": r(ess_min, 1), "diag_ok": ok,
            "ll_trace": [r(v, 2) for v in thin_trace(ll)]}


def trace_chart(trace, width=720, height=90):
    """Geometry of the small log-likelihood line: SVG polyline points and the axis labels."""
    t = np.asarray(trace or [], dtype=float)
    if len(t) < 2 or not np.all(np.isfinite(t)):
        return None
    x0, x1, y0, y1 = 60, width - 8, 8, height - 18
    lo, hi = float(t.min()), float(t.max())
    span = hi - lo or 1.0
    xs = x0 + np.arange(len(t)) / (len(t) - 1) * (x1 - x0)
    ys = y1 - (t - lo) / span * (y1 - y0)
    return {"points": " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys)), "width": width, "height": height,
            "x0": x0, "x1": x1, "y0": y0, "y1": y1, "lo": lo, "hi": hi}


def longer_settings(settings, defaults):
    """The settings of a run with doubled burn-in and iterations, or None beyond LONGER_MAX x the configured
    length."""
    burn, draws = int(settings["burn"]) * 2, int(settings["draws"]) * 2
    if burn + draws > LONGER_MAX * (int(defaults["burn"]) + int(defaults["draws"])):
        return None
    return {**settings, "burn": burn, "draws": draws}


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
    return {"ok": bool(res.get("diag_ok")), "complete": res.get("diag_complete", True),
            "rhat_max": res.get("rhat_max"), "ess_min": res.get("ess_min"),
            "ess_ll": res.get("ess_ll"), "geweke_ok": res.get("geweke_ok", True), "kept": res.get("kept"),
            "rhat_limit": RHAT_LIMIT, "ess_limit": ESS_MIN, "chart": trace_chart(res.get("ll_trace")),
            "longer": None if res.get("diag_ok") else longer_settings(settings, defaults)}


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
    if reader(lang) == "en":
        return (f"; convergence {'unremarkable' if res.get('diag_ok') else 'to be checked'}: rank-normalised split R̂ max. "
                f"{number(res['rhat_max'], 3, 'en')}, effective sample size (ESS) min. {number(res['ess_min'], 0, 'en')} of "
                f"{res.get('kept')} draws (rule R̂ < {number(RHAT_LIMIT, 2, 'en')} and ESS ≥ {ESS_MIN}, Vehtari et al., 2021)")
    return (f"; Konvergenz {'unauffällig' if res.get('diag_ok') else 'prüfen'}: rang-normalisiertes split-R̂ max. "
            f"{number(res['rhat_max'], 3)}, effektive Stichprobengröße (ESS) min. {number(res['ess_min'])} von "
            f"{res.get('kept')} Ziehungen (Regel R̂ < {number(RHAT_LIMIT, 2)} und ESS ≥ {ESS_MIN}, Vehtari et al., 2021)")
