"""Experimental designs for rating tasks: a sample of profiles (vignettes) for a linear model, split into decks.

A factorial survey shows one profile (a vignette) per page and asks normal questions about it; the analysis is a
linear model on the levels (main effects). The criterion is therefore the information of that model,
det(X'X) with an intercept, not the within-task information of a choice model (app/conjoint_design.py; with
one profile per task that information is zero).

- `full_factorial`: all allowed combinations.
- `d_optimal`: a sample of n different allowed profiles by coordinate exchange (Meyer & Nachtsheim, 1995) with the
  update of the determinant for exchanging one row (Fedorov, 1972), several random starts.
- `blocks`: the profiles split into decks of equal size so that the information *within* the decks is largest
  (deck as a fixed block, as the person is in the analysis): factors uncorrelated and levels balanced also within
  each deck (Atzmüller & Steiner, 2010; Auspurg & Hinz, 2015). The criterion is the within-task information of
  app/conjoint_design.py with the deck in place of the task.

Coding: orthonormal contrasts scaled so that a balanced, orthogonal design gives X'X = N * I, hence D-efficiency
100 (Kuhfeld, Tobias & Garratt, 1994). Prohibitions are pairs ((factor, level), (factor, level)) that never meet
in one profile, as for conjoint (conjoint_design.allowed_fn)."""
import itertools
import random

import numpy as np

from app.conjoint_design import Budget, allowed_fn
from app.lang import de_en

AUSPURG_HINZ = ("Auspurg, K., & Hinz, T. (2015). Factorial survey experiments (Quantitative Applications in the Social "
                "Sciences, Vol. 175). SAGE.")
ATZMUELLER_STEINER = ("Atzmüller, C., & Steiner, P. M. (2010). Experimental vignette studies in survey research. "
                      "Methodology, 6(3), 128–138. https://doi.org/10.1027/1614-2241/a000014")
DUELMER = ("Dülmer, H. (2016). The factorial survey: Design selection and its impact on reliability and internal "
           "validity. Sociological Methods & Research, 45(2), 304–347. https://doi.org/10.1177/0049124115582269")
SU_STEINER = ("Su, D., & Steiner, P. M. (2020). An evaluation of experimental designs for constructing vignette sets in "
              "factorial surveys. Sociological Methods & Research, 49(2), 455–497. "
              "https://doi.org/10.1177/0049124117746427")
KUHFELD = ("Kuhfeld, W. F., Tobias, R. D., & Garratt, M. (1994). Efficient experimental design with marketing research "
           "applications. Journal of Marketing Research, 31(4), 545–557. https://doi.org/10.1177/002224379403100408")
MEYER_NACHTSHEIM = ("Meyer, R. K., & Nachtsheim, C. J. (1995). The coordinate-exchange algorithm for constructing exact "
                    "optimal experimental designs. Technometrics, 37(1), 60–69. "
                    "https://doi.org/10.1080/00401706.1995.10485889")
SAUER = ("Sauer, C., Auspurg, K., & Hinz, T. (2020). Designing multi-factorial survey experiments: Effects of "
         "presentation style (text or table), answering scales, and vignette order. methods, data, analyses, 14(2), "
         "195–214. https://doi.org/10.12758/mda.2020.06")
GUTFLEISCH = ("Gutfleisch, T. (o. J.). An introduction to factorial survey experiments (FSE), Part II: Setting up the "
              "experiment and the survey [Kursfolien]. National Centre for Research Methods. "
              "https://repository.ncrm.ac.uk/resources/online/factorial_survey_experiments/downloads/slides/slides2.pdf")

FEDOROV = "Fedorov, V. V. (1972). Theory of optimal experiments. Academic Press."

FULL_MAX = 500  # all combinations only up to this many (the loop shows one page per profile and person)
EFFICIENCY_GOOD = 90  # D-efficiency of a vignette sample recommended by Auspurg & Hinz (2015), after Gutfleisch
PER_PERSON_HINT = 10  # "no more than 10 vignettes per respondent" (Gutfleisch, after Auspurg & Hinz, 2015)
# Vignettes of a D-optimal plan at most (vignettes per person x decks): 5000 took 22 s, the editor allowed 250 000
# (code review 2026-10-15, B2); 500 decks of 10 still fit
N_MAX = 5000


def n_params(levels, pairs=()):
    """Parameters of the model: intercept, L - 1 per factor and (La - 1)(Lb - 1) per two-way interaction in
    `pairs` (pairs of factor indices)."""
    return 1 + sum(n - 1 for n in levels) + sum((levels[a] - 1) * (levels[b] - 1) for a, b in pairs or ())


def _coding(n):
    """Orthonormal contrasts for n levels, scaled so that balanced levels give columns of mean 0 and variance 1."""
    H = np.zeros((n, n - 1))
    for j in range(1, n):
        H[:j, j - 1] = 1
        H[j, j - 1] = -j
    return H / np.sqrt((H ** 2).sum(axis=0) / n)


class _Coder:
    """Model rows: intercept, the contrasts of each factor, then for each pair of `pairs` the products of their
    contrasts (two-way interactions; products of orthonormal contrasts are orthonormal again in a full design)."""

    def __init__(self, levels, pairs=()):
        self.levels = list(levels)
        self.pairs = [tuple(p) for p in pairs or ()]
        self.codes = [_coding(n) for n in levels]
        self.starts = np.cumsum([1] + [n - 1 for n in levels])
        self.main = 1 + sum(n - 1 for n in levels)  # columns up to and with the main effects

    def rows(self, D):
        D = np.asarray(D, dtype=int).reshape(-1, len(self.levels))
        main = [self.codes[j][D[:, j]] for j in range(len(self.levels))]
        inter = [np.einsum("ni,nj->nij", main[a], main[b]).reshape(len(D), -1) for a, b in self.pairs]
        return np.hstack([np.ones((len(D), 1))] + main + inter)

    def row(self, profile):
        return self.rows([profile])[0]


def allowed_profiles(levels, forbidden=None):
    ok = allowed_fn(forbidden)
    return [list(p) for p in itertools.product(*[range(n) for n in levels]) if ok(p)]


def universe(levels):
    total = 1
    for n in levels:
        total *= n
    return total


def full_factorial(levels, forbidden=None):
    return allowed_profiles(levels, forbidden)


def d_efficiency(D, levels, pairs=()):
    """D-efficiency of the profiles D for the model (main effects and `pairs`), 0 to 100 (100: orthogonal and
    balanced)."""
    X = _Coder(levels, pairs).rows(D)
    sign, logdet = np.linalg.slogdet(X.T @ X / len(X))
    return 0.0 if sign <= 0 else float(min(100.0, 100 * np.exp(logdet / X.shape[1])))


def deck_efficiency(D, levels, size, pairs=()):
    """D-efficiency of the model within decks of `size` consecutive profiles (deck as a fixed block), 0 to
    100 on the same scale as d_efficiency."""
    X = _Coder(levels, pairs).rows(D)[:, 1:]
    if X.shape[1] == 0 or len(X) % size:
        return 0.0
    T = X.reshape(-1, size, X.shape[1])
    d = T - T.mean(axis=1, keepdims=True)
    sign, logdet = np.linalg.slogdet(np.einsum("tja,tjb->ab", d, d) / len(X))
    return 0.0 if sign <= 0 else float(min(100.0, 100 * np.exp(logdet / X.shape[1])))


def _hard(budget, seconds):
    """The hard limit checked inside the loops (code review 2026-10-15, B2): the caller's budget, else twice the
    time after which no further start begins."""
    return budget if budget is not None else Budget(2 * seconds)


def d_optimal(levels, n, forbidden=None, n_starts=4, seed=None, max_iter=30, seconds=6.0, pairs=(), budget=None):
    """n different allowed profiles with large det(X'X) (coordinate exchange). Raises ValueError if n is larger
    than the allowed profiles or smaller than the parameters. Starts after the first one only while `seconds`
    have not passed; the exchange stops with the best plan so far once `budget` is over (budget.cut)."""
    import time

    until = time.monotonic() + seconds
    budget = _hard(budget, seconds)
    rng = random.Random(seed)
    ok = allowed_fn(forbidden)
    pool = allowed_profiles(levels, forbidden) if universe(levels) <= 200000 else None
    available = len(pool) if pool is not None else universe(levels)
    if n > available:
        raise ValueError(de_en("Nur {available} erlaubte Kombinationen, aber {n} Vignetten verlangt.",
                               "Only {available} allowed combinations, but {n} vignettes requested.",
                               available=available, n=n))
    p = n_params(levels, pairs)
    if n < p:
        raise ValueError(de_en("Mindestens {p} Vignetten nötig, damit sich alle {k} Effekte schätzen lassen.",
                               "At least {p} vignettes are needed so that all {k} effects can be estimated.", p=p, k=p - 1))
    coder = _Coder(levels, pairs)
    best, best_eff = None, -1.0
    for _ in range(n_starts):
        D = _start(levels, n, rng, ok, pool)
        D = _exchange(D, levels, coder, ok, rng, max_iter, budget)
        eff = d_efficiency(D, levels, pairs)
        if eff > best_eff:
            best, best_eff = D.copy(), eff
        if time.monotonic() > until or budget.over():
            break
    return best.tolist()


def _start(levels, n, rng, ok, pool):
    """A random start of n different allowed profiles, levels about balanced where the prohibitions allow."""
    if pool is not None and len(pool) <= 4 * n:
        return np.array(rng.sample(pool, n))
    D, seen = [], set()
    columns = []
    for m in levels:
        reps, rest = divmod(n, m)
        seq = list(range(m)) * reps + rng.sample(range(m), rest)
        rng.shuffle(seq)
        columns.append(seq)
    for i in range(n):
        row = [columns[j][i] for j in range(len(levels))]
        tries = 0
        while (not ok(row) or tuple(row) in seen) and tries < 1000:
            j = rng.randrange(len(levels))
            row[j] = rng.randrange(levels[j])
            tries += 1
        if tries >= 1000:  # crowded: take any unused allowed profile
            row = next((list(q) for q in (pool or []) if tuple(q) not in seen), None)
            if row is None:  # no list of all profiles (too many) or none left (code review 2026-10-15, B3)
                raise ValueError(de_en("Es ließen sich nicht {n} verschiedene erlaubte Vignetten finden. Bitte weniger "
                                       "Vignetten verlangen oder weniger Kombinationen verbieten.", "{n} different allowed "
                                       "vignettes could not be found. Please request fewer vignettes or prohibit fewer "
                                       "combinations.", n=n))
        seen.add(tuple(row))
        D.append(row)
    return np.array(D)


def _exchange(D, levels, coder, ok, rng, max_iter, budget=None):
    """Change single levels while det(X'X) grows, never to a forbidden or a repeated profile. The ratio of the
    determinants for replacing row x by y is (1 + y'Ay)(1 - x'Ax) + (x'Ay)^2 with A the inverse of X'X. Stops early
    once the budget is over."""
    X = coder.rows(D)
    p = X.shape[1]
    M = X.T @ X + 1e-6 * np.eye(p)  # a small ridge keeps a start that cannot yet estimate everything finite
    A = np.linalg.inv(M)
    seen = {tuple(r) for r in D}
    for _ in range(max_iter):
        improved = False
        for i in rng.sample(range(len(D)), len(D)):
            if budget is not None and budget.over():
                return D
            for j, m in enumerate(levels):
                cur = int(D[i, j])
                x = X[i]
                Ax = A @ x
                xAx = x @ Ax
                cands, ys = [], []
                for lv in range(m):
                    if lv == cur:
                        continue
                    row = D[i].copy()
                    row[j] = lv
                    if tuple(row) in seen or not ok(row):
                        continue
                    cands.append(row)
                    ys.append(coder.row(row))
                if not cands:
                    continue
                Y = np.array(ys)
                AY = Y @ A
                ratio = (1 + np.einsum("ij,ij->i", AY, Y)) * (1 - xAx) + (AY @ x) ** 2
                k = int(np.argmax(ratio))
                if ratio[k] <= 1 + 1e-9:
                    continue
                seen.discard(tuple(D[i]))
                D[i] = cands[k]
                seen.add(tuple(D[i]))
                y = Y[k]
                # A after removing x and adding y (two Sherman-Morrison steps)
                A = A + np.outer(Ax, Ax) / (1 - xAx)
                Ay = A @ y
                A = A - np.outer(Ay, Ay) / (1 + y @ Ay)
                X[i] = y
                improved = True
        if not improved:
            break
    return D


def d_optimal_rows(profiles, levels, n, n_starts=4, seed=None, max_iter=30, seconds=6.0, pairs=(), budget=None):
    """Indices of n of the given profiles (rows of an existing table, repeats possible) with large det(X'X):
    exchange of a chosen row against a candidate (Fedorov, 1972). Raises ValueError if n is out of range. Stops
    with the best choice so far once `budget` is over."""
    import time

    budget = _hard(budget, seconds)
    if n > len(profiles):
        raise ValueError(de_en("Nur {rows} Zeilen, aber {n} verlangt.", "Only {rows} rows, but {n} requested.",
                               rows=len(profiles), n=n))
    p = n_params(levels, pairs)
    if n < p:
        raise ValueError(de_en("Mindestens {p} Zeilen nötig, damit sich alle {k} Effekte schätzen lassen.",
                               "At least {p} rows are needed so that all {k} effects can be estimated.", p=p, k=p - 1))
    until = time.monotonic() + seconds
    rng = random.Random(seed)
    X = _Coder(levels, pairs).rows(profiles)
    best, best_val = None, -np.inf
    for _ in range(n_starts):
        chosen = rng.sample(range(len(profiles)), n)
        inside = set(chosen)
        M = X[chosen].T @ X[chosen] + 1e-6 * np.eye(X.shape[1])
        A = np.linalg.inv(M)
        for _ in range(max_iter):
            improved = False
            for pos in rng.sample(range(n), n):
                if budget.over():
                    break
                others = np.array([c for c in range(len(profiles)) if c not in inside])
                if not len(others):
                    break
                x = X[chosen[pos]]
                Ax = A @ x
                xAx = x @ Ax
                Y = X[others]
                AY = Y @ A
                ratio = (1 + np.einsum("ij,ij->i", AY, Y)) * (1 - xAx) + (AY @ x) ** 2
                k = int(np.argmax(ratio))
                if ratio[k] <= 1 + 1e-9:
                    continue
                inside.discard(chosen[pos])
                chosen[pos] = int(others[k])
                inside.add(chosen[pos])
                y = X[chosen[pos]]
                A = A + np.outer(Ax, Ax) / (1 - xAx)
                Ay = A @ y
                A = A - np.outer(Ay, Ay) / (1 + y @ Ay)
                improved = True
            if not improved or time.monotonic() > until or budget.cut:
                break
        val = np.linalg.slogdet(X[chosen].T @ X[chosen])[1]
        if val > best_val:
            best, best_val = chosen[:], val
        if time.monotonic() > until or budget.cut:
            break
    return best


def blocks(D, levels, n_decks, n_starts=3, seed=None, max_pass=12, seconds=8.0, partners=96, pairs=(), budget=None):
    """Order of the profiles so that consecutive groups of len(D) / n_decks are the decks: pairwise swaps between
    decks while the within-deck information grows (each profile tries at most `partners` others per pass; all
    starts together stop after about `seconds`, the swaps at once when `budget` is over). Returns the list of decks
    (lists of indices into D)."""
    import time

    budget = _hard(budget, seconds)
    rng = random.Random(seed)
    until = time.monotonic() + seconds
    N = len(D)
    if n_decks <= 1:
        return [list(range(N))]
    if N % n_decks:
        raise ValueError(de_en("Die Vignetten lassen sich nicht gleichmäßig auf die Decks verteilen.",
                               "The vignettes cannot be spread evenly over the decks."))
    size = N // n_decks
    X = _Coder(levels, pairs).rows(D)[:, 1:]
    k = X.shape[1]
    ridge = 1e-6 * np.eye(k)
    best, best_val = None, -np.inf
    for _ in range(n_starts):
        order = list(range(N))
        rng.shuffle(order)
        deck_of = np.array([n // size for n in range(N)])  # position -> deck
        Xo = X[order]
        sums = np.array([Xo[t * size:(t + 1) * size].sum(axis=0) for t in range(n_decks)])
        W = Xo.T @ Xo - sum(np.outer(s, s) for s in sums) / size  # within-deck scatter
        val = np.linalg.slogdet(W + ridge)[1]
        for _ in range(max_pass):
            improved = False
            for a in rng.sample(range(N), N):
                if budget.over():
                    break
                sa = deck_of[a]
                others = np.nonzero(deck_of != sa)[0]
                if not len(others):
                    continue
                if len(others) > partners:
                    others = np.array(sorted(rng.sample(list(others), partners)))
                xa, xb = Xo[a], Xo[others]
                sb = deck_of[others]
                d = xb - xa  # moving b into a's deck and a into b's deck
                # new within scatter: the total scatter stays; only the two deck sums change
                Sa = sums[sa][None, :] + d
                Sb = sums[sb] - d
                old = (np.outer(sums[sa], sums[sa])[None] + np.einsum("ni,nj->nij", sums[sb], sums[sb]))
                new = np.einsum("ni,nj->nij", Sa, Sa) + np.einsum("ni,nj->nij", Sb, Sb)
                cand = W[None] + (old - new) / size + ridge[None]
                signs, logdets = np.linalg.slogdet(cand)
                logdets = np.where(signs > 0, logdets, -np.inf)
                m = int(np.argmax(logdets))
                if logdets[m] <= val + 1e-9:
                    continue
                b = int(others[m])
                sb_ = deck_of[b]
                sums[sa] = sums[sa] + Xo[b] - Xo[a]
                sums[sb_] = sums[sb_] - Xo[b] + Xo[a]
                W = cand[m] - ridge
                val = logdets[m]
                Xo[[a, b]] = Xo[[b, a]]
                order[a], order[b] = order[b], order[a]
                improved = True
            if not improved or time.monotonic() > until or budget.cut:
                break
        if val > best_val:
            best, best_val = order[:], val
        if time.monotonic() > until or budget.cut:
            break
    if best is None:
        best = order
    return [best[t * size:(t + 1) * size] for t in range(n_decks)]


def stats(D, levels, decks, pairs=(), between=()):
    """Figures of a plan: D-efficiency of the sample and within the decks, the largest correlation of two factors
    (overall and within decks), the spread of the level counts within the decks, distinct profiles.
    between: indices of factors that vary only between persons (one level per deck, a split plan). Within a deck they
    carry no information by design: the efficiency within the decks and the decks with a missing level count only
    the other factors (and the interactions among them, as the split plan is made), "deck_within" says so."""
    D = np.asarray(D, dtype=int)
    ordered = D[[i for deck in decks for i in deck]]
    size = len(decks[0]) if decks else len(D)
    coder = _Coder(levels, pairs)
    full = coder.rows(ordered)
    X = full[:, 1:coder.main]  # correlations of the main effects

    def max_corr(Xs):
        if len(Xs) < 3 or Xs.shape[1] < 2:
            return 0.0
        C = np.corrcoef(Xs, rowvar=False)
        C = np.nan_to_num(C)
        out, start = 0.0, 0
        bounds = []
        for n in levels:
            bounds.append((start, start + n - 1))
            start += n - 1
        for (a0, a1), (b0, b1) in itertools.combinations(bounds, 2):
            out = max(out, float(np.abs(C[a0:a1, b0:b1]).max()))
        return out

    within = X.reshape(-1, size, X.shape[1])
    within = (within - within.mean(axis=1, keepdims=True)).reshape(-1, X.shape[1])
    spread = 0
    missing = 0
    inside = [j for j in range(len(levels)) if j not in set(between)]
    for t in range(len(decks)):
        rows = ordered[t * size:(t + 1) * size]
        for j in inside:
            n = levels[j]
            counts = np.bincount(rows[:, j], minlength=n)
            spread = max(spread, int(counts.max() - counts.min()))
            if n <= size and counts.min() == 0:
                missing += 1
    if between and inside:
        pos = {j: k for k, j in enumerate(inside)}
        deck_eff = deck_efficiency(ordered[:, inside], [levels[j] for j in inside], size,
                                   [(pos[a], pos[b]) for a, b in pairs if a in pos and b in pos])
    else:
        deck_eff = deck_efficiency(ordered, levels, size, pairs)
    return {"d_eff": round(d_efficiency(D, levels, pairs), 1),
            "deck_eff": round(deck_eff, 1) if len(decks) > 1 else None, "deck_within": bool(between and inside),
            "max_corr": round(max_corr(X), 2), "max_corr_deck": round(max_corr(within), 2) if len(decks) > 1 else None,
            "level_spread": spread, "decks_missing_level": missing,
            "distinct": len({tuple(r) for r in D.tolist()}), "n": len(D)}
