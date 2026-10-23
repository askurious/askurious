"""Experimental designs of the ratings-based conjoint (CVA, app/items/cva.py; concept aca-cva-konzept.md, C4).

Every person rates the whole plan of one version, so that a regression per person can estimate all part-worths:
the criterion is the information of the linear model y = constant + part-worths (main effects), det(X'X).

- Single profiles ("single"): n different allowed profiles from `doe.d_optimal` (coordinate exchange with an
  intercept), one D-optimal plan per version.
- Pairs ("pairs"): n pairs of different allowed profiles; the model rows are the difference of the two profiles
  plus a constant for the side shown first (a position effect). Coordinate exchange as in app/doe.py, but on the
  difference rows, with a plain recomputation of det(X'X) (plans of at most MAX_TASKS rows). The two profiles of a
  pair differ in at least two attributes (one only where there is a single attribute), so that no pair asks about
  one level alone, and no pair repeats in either order.

The number of tasks is a Festlegung von Askurious: at least one more than the parameters (else the fit of a person
cannot be judged), suggested twice the parameters and a warning under 1.5 times, so that the regression per person
keeps enough degrees of freedom for stable part-worths."""
import math
import random

import numpy as np

from app import doe
from app.conjoint_design import Budget, allowed_fn
from app.lang import de_en

MAX_TASKS = 40
SUGGEST_FACTOR = 2.0   # suggested tasks per parameter (Festlegung von Askurious)
WARN_FACTOR = 1.5      # warning under 1.5 times the parameters (Festlegung von Askurious)
MAX_ATTRIBUTES_HINT = 6  # full profiles with more attributes are hard to judge (Green & Srinivasan, 1990)
GREEN_SRINIVASAN = ("Green, P. E., & Srinivasan, V. (1990). Conjoint analysis in marketing: New developments with "
                    "implications for research and practice. Journal of Marketing, 54(4), 3–19. "
                    "https://doi.org/10.1177/002224299005400402")


def n_params(levels):
    """Constant plus L - 1 per attribute."""
    return 1 + sum(n - 1 for n in levels)


def minimum(levels):
    return n_params(levels) + 1


def suggested(levels, available=None):
    """Tasks suggested for these levels: twice the parameters, at most MAX_TASKS and the profiles available."""
    n = max(minimum(levels), math.ceil(SUGGEST_FACTOR * n_params(levels)))
    n = min(n, MAX_TASKS)
    return min(n, available) if available else n


def generate(levels, n, form, versions, forbidden=None, seed=None, budget=None):
    """`versions` plans of n tasks: [[profile, ...]] for "single", [[[left, right], ...]] for "pairs"."""
    budget = budget or Budget(20)
    rng = random.Random(seed)
    out = []
    for _ in range(versions):
        if form == "pairs":
            out.append(pairs(levels, n, forbidden, seed=rng.getrandbits(32), budget=budget))
        else:
            out.append(doe.d_optimal(levels, n, forbidden, seed=rng.getrandbits(32), seconds=4.0, budget=budget))
    return out


def _diff_rows(coder, D):
    X = coder.rows(D.reshape(-1, D.shape[-1]))[:, 1:].reshape(len(D), 2, -1)
    return np.hstack([np.ones((len(D), 1)), X[:, 0] - X[:, 1]])


def _logdet(X):
    """log det(X'X); a start that cannot estimate everything yet gets a small ridge, so the exchange can still climb."""
    sign, value = np.linalg.slogdet(X.T @ X)
    if sign > 0:
        return value
    sign, value = np.linalg.slogdet(X.T @ X + 1e-6 * np.eye(X.shape[1]))
    return value - 1e6 if sign > 0 else -np.inf


def _differ(a, b, need):
    return sum(x != y for x, y in zip(a, b)) >= need


def pairs(levels, n, forbidden=None, seed=None, n_starts=4, max_iter=20, budget=None):
    """n pairs of profiles with large det(X'X) of the difference rows (see the module docstring)."""
    rng = random.Random(seed)
    ok = allowed_fn(forbidden)
    need = 2 if len(levels) > 1 else 1
    pool = doe.allowed_profiles(levels, forbidden) if doe.universe(levels) <= 200000 else None
    if pool is not None and len(pool) < 2:
        raise ValueError(de_en("Zu wenige erlaubte Kombinationen für Paare.", "Too few allowed combinations for pairs."))
    if n < n_params(levels):
        raise ValueError(de_en("Mindestens {p} Paare nötig, damit sich alle Teilnutzen schätzen lassen.",
                               "At least {p} pairs are needed so that all part-worths can be estimated.",
                               p=n_params(levels)))
    coder = doe._Coder(levels)
    budget = budget or Budget(10)

    def random_profile():
        if pool is not None:
            return list(rng.choice(pool))
        for _ in range(1000):
            row = [rng.randrange(m) for m in levels]
            if ok(row):
                return row
        raise ValueError(de_en("Keine erlaubte Kombination gefunden.", "No allowed combination found."))

    def key(a, b):
        return tuple(sorted((tuple(a), tuple(b))))

    best, best_value = None, -np.inf
    for start in range(n_starts):
        D, seen = [], set()
        tries = 0
        while len(D) < n and tries < 50 * n:
            tries += 1
            a, b = random_profile(), random_profile()
            if _differ(a, b, need) and key(a, b) not in seen:
                seen.add(key(a, b))
                D.append([a, b])
        if len(D) < n:
            raise ValueError(de_en("Es ließen sich nicht {n} verschiedene Paare finden. Bitte weniger Aufgaben verlangen "
                                   "oder weniger Kombinationen verbieten.", "{n} different pairs could not be found. "
                                   "Please request fewer tasks or prohibit fewer combinations.", n=n))
        D = np.array(D)
        X = _diff_rows(coder, D)
        value = _logdet(X)
        for _ in range(max_iter):
            improved = False
            for i in rng.sample(range(n), n):
                for side in (0, 1):
                    for j, m in enumerate(levels):
                        if budget.over():
                            break
                        cur = int(D[i, side, j])
                        for lv in range(m):
                            if lv == cur:
                                continue
                            cand = D[i, side].copy()
                            cand[j] = lv
                            other = D[i, 1 - side]
                            if not ok(cand) or not _differ(cand, other, need):
                                continue
                            k = key(cand, other)
                            if k in seen:
                                continue
                            old = D[i, side].copy()
                            D[i, side] = cand
                            trial = _diff_rows(coder, D[i:i + 1])[0]
                            saved = X[i].copy()
                            X[i] = trial
                            new = _logdet(X)
                            if new > value + 1e-9:
                                seen.discard(key(old, other))
                                seen.add(k)
                                value = new
                                improved = True
                            else:
                                D[i, side] = old
                                X[i] = saved
            if not improved or budget.over():
                break
        if value > best_value:
            best, best_value = D.copy(), value
        if budget.over():
            break
    if np.linalg.matrix_rank(_diff_rows(coder, best)) < n_params(levels):
        raise ValueError(de_en("Mit {n} Paaren lassen sich nicht alle Teilnutzen trennen. Mehr Aufgaben wählen oder weniger "
                               "Kombinationen verbieten.", "With {n} pairs the part-worths cannot all be separated. Choose "
                               "more tasks or prohibit fewer combinations.", n=n))
    return best.tolist()


def efficiency(versions, levels, form):
    """D-efficiency of the plans, 0 to 100, the smallest over the versions: for single profiles that of app/doe.py
    (100 = orthogonal and balanced); for pairs the same scale on the difference rows without the constant (a pair
    of two orthogonal profiles that differ in everything reaches about 200, so the figure is capped at 100)."""
    if not versions:
        return None
    coder = doe._Coder(levels)
    out = []
    for plan in versions:
        if form == "pairs":
            D = np.array(plan)
            X = _diff_rows(coder, D)[:, 1:]
            if X.shape[1] == 0:
                return None
            sign, logdet = np.linalg.slogdet(X.T @ X / len(X))
            out.append(0.0 if sign <= 0 else float(min(100.0, 100 * np.exp(logdet / X.shape[1]))))
        else:
            out.append(doe.d_efficiency(plan, levels))
    return min(out)


def level_frequency(versions, levels, form):
    """Per attribute (rarest, most frequent) level count over all versions (both sides of a pair counted)."""
    counts = [[0] * n for n in levels]
    for plan in versions or []:
        for task in plan:
            for profile in (task if form == "pairs" else [task]):
                for j, lv in enumerate(profile):
                    counts[j][lv] += 1
    return [(min(c), max(c)) for c in counts]
