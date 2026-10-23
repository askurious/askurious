"""D-efficient choice designs for choice-based conjoint (CBC).

Coordinate exchange (Meyer & Nachtsheim, 1995; several random starts) on the information of the
multinomial logit at beta = 0 with effects coding: per task the covariance of the alternatives' rows around
their task mean, summed over the tasks (Huber & Zwerina, 1996). Only differences within a task inform a
choice, so a level shared by all alternatives of a task adds nothing; maximising this information keeps
such overlap low (det(X'X) over all profiles, the earlier criterion, ignored the tasks). No task contains
two identical alternatives. Versions (blocked designs) are generated as one large design and split, so the
information is maximised across all versions together.

D-efficiency (d_efficiency) is relative to an ideal design and lies between 0 and 1: 1 when every attribute
has as few repeated levels within a task as possible and the attributes are orthogonal within the tasks (the
bound by Fischer's and the AM-GM inequality); 0 when the parameters cannot all be estimated. It does not see how
often each level occurs over the whole design (only differences within a task inform a choice): the exchange
keeps that balanced with a small term of its own (W_BAL), and the editor shows the level frequency next to it.

Prohibitions ("forbidden"): pairs of levels of two attributes that must never appear together in one
profile, e.g. a very low price with a premium feature. The design starts from allowed profiles only,
the exchange only moves to allowed profiles, and the duplicate repair keeps them allowed; the
D-efficiency then drops somewhat (the statistics show by how much).
"""
import random
import time

import numpy as np
from app.lang import de_en

# the sample size rule (sample_size): Orme (2010), ch. 7, pp. 64-65, from Johnson's rule of thumb; the factor 1.5 for
# the recommended size is a margin of this platform (Orme: better 1000 than 500 representations per level)
ORME = ("Orme, B. K. (2010). Getting started with conjoint analysis: Strategies for product design and pricing research "
        "(2nd ed.). Research Publishers.")
JOHNSON_ORME = ("Johnson, R. M., & Orme, B. K. (1996). How many questions should you ask in choice-based conjoint "
                "studies? Sawtooth Software Research Paper Series. Sawtooth Software.")
HUBER_ZWERINA = ("Huber, J., & Zwerina, K. (1996). The importance of utility balance in efficient choice designs. "
                 "Journal of Marketing Research, 33(3), 307–317. https://doi.org/10.1177/002224379603300305")
SOURCES = (ORME, JOHNSON_ORME)
# Level balance: the MNL information only sees differences within a task, so (a, a, b) and (b, b, a) are equally good
# and the exchange often ended with one level twice as often as the other at D-efficiency 1 (audit 2026-10-05, C-3).
# The exchange therefore maximises log det - W_BAL * imbalance (imbalance(): squared deviations of the level counts
# from an even spread, per profile; Huber & Zwerina, 1996, name level balance as a criterion of its own). At 0.3 the
# designs are as balanced as the numbers allow and lose at most a few thousandths of D-efficiency; at 1 and more the
# term starts to cost information.
W_BAL = 0.3
# Starts for a small design; larger ones get fewer (_starts), down to MIN_STARTS: 20 starts of 300 profiles with 24
# levels took 80 s before the exchange used the rank-two update (code review 2026-10-15, B1).
N_STARTS = 20
MIN_STARTS = 4
WORK_FULL_STARTS = 4000  # profiles x levels up to which all N_STARTS run


class Budget:
    """A time limit for a design search (code review 2026-10-15, B1/B2): the loops ask over() and stop with the best
    design so far; cut says whether that happened, so the editor can say the search was cut short. seconds None or
    0: no limit."""

    def __init__(self, seconds=None):
        self.until = time.monotonic() + seconds if seconds else float("inf")
        self.cut = False

    def over(self):
        if time.monotonic() > self.until:
            self.cut = True
        return self.cut


def _starts(levels, n_profiles):
    """Random starts for a design of this size: N_STARTS for small ones, fewer for large ones (each start costs about
    profiles x levels candidate evaluations per pass)."""
    work = n_profiles * sum(levels)
    return max(MIN_STARTS, min(N_STARTS, round(N_STARTS * WORK_FULL_STARTS / max(work, 1))))


def allowed_fn(forbidden):
    """A test "is this profile allowed" for pairs ((attr, level), (attr, level)) that must not meet."""
    pairs = [((a, la), (b, lb)) for (a, la), (b, lb) in (forbidden or [])]
    if not pairs:
        return lambda row: True
    return lambda row: not any(row[a] == la and row[b] == lb for (a, la), (b, lb) in pairs)


def n_allowed_profiles(levels, forbidden):
    """Number of different allowed profiles (all combinations minus the forbidden ones)."""
    from itertools import product

    ok = allowed_fn(forbidden)
    total = 1
    for n in levels:
        total *= n
    if total > 200000:  # too many to count: at least one per level is enough for the checks here
        return total
    return sum(1 for row in product(*[range(n) for n in levels]) if ok(row))


def generate(levels, tasks, alternatives, versions=1, n_starts=None, seed=None, forbidden=None, budget=None):
    """Return (design, d_efficiency).

    design: list[version][task][alternative] -> list of level indices (one per attribute)
    forbidden: pairs ((attr index, level index), (attr index, level index)) never shown together
    n_starts: random starts, None = by the size of the design (_starts)
    budget: a Budget; once it is over, the exchange stops and the best design so far is returned (budget.cut). The same
    inputs and seed give the same design as long as the budget is not reached.
    """
    rng = random.Random(seed)
    n_profiles = versions * tasks * alternatives
    if n_profiles == 0 or not levels or any(n < 2 for n in levels):
        raise ValueError(de_en("Mindestens ein Merkmal mit mindestens zwei Stufen, eine Aufgabe und zwei Alternativen.",
                               "At least one attribute with at least two levels, one task and two alternatives."))
    ok = allowed_fn(forbidden)
    if forbidden and n_allowed_profiles(levels, forbidden) < alternatives:
        raise ValueError(de_en("Zu viele Ausschlüsse: Es bleiben weniger erlaubte Kombinationen als Alternativen je "
                               "Aufgabe.", "Too many exclusions: fewer allowed combinations remain than alternatives "
                               "per task."))
    if forbidden and coupled(levels, forbidden):
        raise ValueError(de_en("Die verbotenen Kombinationen koppeln Stufen verschiedener Merkmale so eng, dass sich ihre "
                               "Wirkungen in keinem Versuchsplan trennen lassen (z. B. kommt jede Stufe des einen Merkmals nur "
                               "mit einer Stufe des anderen vor). Bitte weniger Kombinationen verbieten.", "The "
                               "prohibitions tie levels of different attributes so closely that no experimental design can tell their "
                               "effects apart (e.g. every level of one attribute occurs with only one level of the other). "
                               "Please prohibit fewer combinations."))
    budget = budget or Budget()
    if n_starts is None:
        n_starts = _starts(levels, n_profiles)
    best, best_eff, best_score = None, -1.0, None
    for start in range(n_starts):
        if start and best is not None and budget.over():
            break
        D = _init_balanced(levels, n_profiles, rng)
        _repair(D, levels, ok, rng)
        D = _coordinate_exchange(D, levels, alternatives, rng, ok=ok, budget=budget)
        _remove_duplicates_within_tasks(D, levels, alternatives, rng, ok=ok)
        if not all(ok(row) for row in D):
            continue
        eff = d_efficiency(D, levels, alternatives)
        # the starts compete on the same criterion as the exchange: k log(efficiency) is log det minus a constant
        score = sum(n - 1 for n in levels) * np.log(max(eff, 1e-300)) - W_BAL * imbalance(D, levels)
        if best is None or score > best_score:
            best, best_eff, best_score = D.copy(), eff, score
    if best is None:
        raise ValueError(de_en("Mit diesen verbotenen Kombinationen ließ sich kein gültiger Versuchsplan finden. Bitte weniger "
                               "Kombinationen verbieten.", "No valid experimental design could be found with these prohibitions. "
                               "Please prohibit fewer combinations."))
    if versions > 1:
        best = balance_versions(best, levels, versions, tasks, alternatives, rng, budget=budget)
        best_eff = d_efficiency(best, levels, alternatives)  # same information, summed in the new task order
    shaped = best.reshape(versions, tasks, alternatives, len(levels)).tolist()
    return shaped, best_eff


def balance_versions(D, levels, versions, tasks, alternatives, rng, max_pass=50, budget=None):
    """Swap whole tasks between versions until every version shows the levels of each attribute as evenly as
    possible (a version used to be a consecutive piece of the design: some levels could be missing in it). The
    information is a sum over tasks, so swapping tasks changes neither it nor the D-efficiency. Stops early once the
    budget is over (the versions are then less evenly balanced, the design as good)."""
    T = D.reshape(versions * tasks, alternatives, len(levels)).copy()
    owner = np.repeat(np.arange(versions), tasks)  # task -> version
    # level counts of each task, all attributes in one row; every version has as many tasks, so the even spread (mu)
    # is the same for all
    per_task = np.array([np.concatenate([np.bincount(T[a][:, j], minlength=n) for j, n in enumerate(levels)])
                         for a in range(len(T))])
    mu = np.concatenate([np.full(n, tasks * alternatives / n) for n in levels])

    def badness(c):  # squared deviations from an even spread, summed over attributes
        return float(((c - mu) ** 2).sum())

    per = [per_task[owner == v].sum(axis=0) for v in range(versions)]
    for _ in range(max_pass):
        improved = False
        for a in rng.sample(range(len(T)), len(T)):
            if budget is not None and budget.over():
                break
            for b in rng.sample(range(len(T)), min(len(T), 40)):
                va, vb = owner[a], owner[b]
                if va == vb:
                    continue
                ta, tb = per_task[a], per_task[b]
                new_a = per[va] - ta + tb
                new_b = per[vb] - tb + ta
                if badness(new_a) + badness(new_b) < badness(per[va]) + badness(per[vb]) - 1e-9:
                    owner[a], owner[b] = vb, va
                    per[va], per[vb] = new_a, new_b
                    improved = True
        if not improved or (budget is not None and budget.cut):
            break
    order = np.concatenate([np.nonzero(owner == v)[0] for v in range(versions)])
    return T[order].reshape(-1, len(levels))


def sample_size(tasks, alternatives, max_levels):
    """Orme (2010) heuristic: n >= 500 * L / (T * J); recommended = 1.5 x minimum (own margin, see ORME)."""
    if tasks <= 0 or alternatives <= 0:
        return 0, 0
    raw = 500 * max_levels / (tasks * alternatives)
    return int(np.ceil(raw)), int(np.ceil(raw * 1.5))


def tasks_needed(n, alternatives, max_levels):
    """sample_size() solved for the tasks of a given sample: T >= 500 * L / (n * J); recommended with 1.5 x 500."""
    if n <= 0 or alternatives <= 0:
        return 0, 0
    per_task = n * alternatives
    return max(1, -(-500 * max_levels // per_task)), max(1, -(-750 * max_levels // per_task))


def _init_balanced(levels, n_profiles, rng):
    D = np.zeros((n_profiles, len(levels)), dtype=int)
    for j, nl in enumerate(levels):
        reps, rest = divmod(n_profiles, nl)
        seq = list(range(nl)) * reps + list(range(rest))
        rng.shuffle(seq)
        D[:, j] = seq
    return D


def _repair(D, levels, ok, rng, tries=500):
    """Make every profile allowed by changing single levels at random (keeps the balance roughly)."""
    for i in range(D.shape[0]):
        for _ in range(tries):
            if ok(D[i]):
                break
            j = rng.randrange(len(levels))
            D[i, j] = rng.randrange(levels[j])


def _encode(D, levels):
    """Effects coding without intercept: per attribute L - 1 columns, the last level -1 in all of them."""
    return np.hstack([np.vstack([np.eye(n - 1), -np.ones(n - 1)])[D[:, j]] for j, n in enumerate(levels)])


def _task_info(X, alternatives):
    """Information of the MNL at beta = 0 per task (tasks x k x k): covariance of the rows around the task mean."""
    T = X.reshape(-1, alternatives, X.shape[1])
    d = T - T.mean(axis=1, keepdims=True)
    return np.einsum("tja,tjb->tab", d, d) / alternatives


def _log_bound(levels, alternatives, n_tasks):
    """log det of the information of an ideal design with n_tasks tasks (see the module docstring): per attribute
    the within-task spread of its levels is at most 1 - sum(c^2) / J^2 with the levels as evenly spread over the J
    alternatives as possible; in effects coding det = L * (n_tasks * spread / (L - 1))^(L - 1)."""
    total = 0.0
    for n in levels:
        q, r = divmod(alternatives, n)
        spread = 1 - (r * (q + 1) ** 2 + (n - r) * q ** 2) / alternatives ** 2
        total += np.log(n) + (n - 1) * np.log(n_tasks * spread / (n - 1))
    return total


def imbalance(D, levels):
    """Sum over attributes of the squared deviations of the level counts from an even spread, per profile."""
    D = np.asarray(D).reshape(-1, len(levels))
    return sum(float(((np.bincount(D[:, j], minlength=n) - len(D) / n) ** 2).sum()) for j, n in enumerate(levels)) / len(D)


def level_frequency(D, levels):
    """Per attribute (fewest, most) profiles with one of its levels, over the whole design."""
    D = np.asarray(D).reshape(-1, len(levels))
    return [(int(c.min()), int(c.max())) for c in (np.bincount(D[:, j], minlength=n) for j, n in enumerate(levels))]


def d_efficiency(D, levels, alternatives):
    """D-efficiency between 0 and 1 relative to an ideal design (see the module docstring); D as profiles in task
    order (any nesting of versions, tasks and alternatives)."""
    D = np.asarray(D).reshape(-1, len(levels))
    k = sum(n - 1 for n in levels)
    info = _task_info(_encode(D, levels), alternatives).sum(axis=0)
    sign, logdet = np.linalg.slogdet(info)
    if sign <= 0 or np.linalg.matrix_rank(info) < k:  # singular: not all part-worths can be estimated
        return 0.0
    return float(min(1.0, np.exp((logdet - _log_bound(levels, alternatives, len(D) // alternatives)) / k)))


def coupled(levels, forbidden):
    """True if the allowed profiles cannot separate all part-worths: their effects-coded rows do not span the
    differences any design would need (prohibitions that tie the levels of two attributes together)."""
    from itertools import product

    ok = allowed_fn(forbidden)
    total = 1
    for n in levels:
        total *= n
    if total > 200000:
        return False
    rows = np.array([row for row in product(*[range(n) for n in levels]) if ok(row)])
    X = _encode(rows, levels)
    return int(np.linalg.matrix_rank(X - X.mean(axis=0))) < X.shape[1]


def _coordinate_exchange(D, levels, alternatives, rng, max_iter=100, ok=lambda row: True, budget=None):
    """Change single levels while the log det of the MNL information grows. A small ridge keeps the criterion
    finite for designs that cannot (yet) estimate everything, so the exchange can still move towards one that can.

    A candidate is scored without a k x k determinant (code review 2026-10-15, B1): changing row x of a task with J
    alternatives and mean m by delta changes the task information by (u delta' + delta u' + (1 - 1/J) delta delta') / J
    with u = x - m, a matrix of rank two, so by the determinant lemma log det grows by log det(I + C U'AU) with
    U = [u, delta], C = [[0, 1], [1, 1 - 1/J]] / J and A the inverse of the information. delta is non-zero only in
    the columns of the attribute changed. Stops early once the budget is over."""
    n_profiles = D.shape[0]
    k = sum(n - 1 for n in levels)
    E = [np.vstack([np.eye(n - 1), -np.ones(n - 1)]) for n in levels]
    starts = np.cumsum([0] + [n - 1 for n in levels])
    ridge = 1e-6 * np.eye(k)
    X = _encode(D, levels).astype(float)
    parts = _task_info(X, alternatives)
    info = parts.sum(axis=0)
    J = alternatives
    c = 1 - 1 / J

    def refresh(m):  # inverse and log det of the information with the ridge
        m = m + ridge
        return np.linalg.inv(m), np.linalg.slogdet(m)[1]

    A, logdet = refresh(info)

    # how often each level of each attribute occurs over the whole design, for the balance term (see W_BAL)
    counts = [np.bincount(D[:, j], minlength=nl) for j, nl in enumerate(levels)]

    for _ in range(max_iter):
        improved = False
        order = list(range(n_profiles))
        rng.shuffle(order)
        for i in order:
            if budget is not None and budget.over():
                return D
            t = i // alternatives
            rows = slice(t * alternatives, (t + 1) * alternatives)
            u = X[i] - X[rows].mean(axis=0)
            for j, nl in enumerate(levels):
                current = D[i, j]
                cols = slice(starts[j], starts[j + 1])
                cnt = counts[j]
                Au = A @ u
                uAu = u @ Au
                Acc = A[cols, cols]
                best_lv, best_c, best_delta = current, logdet, None
                for lv in range(nl):
                    if lv == current:
                        continue
                    D[i, j] = lv
                    if not ok(D[i]):  # never move to a forbidden combination
                        continue
                    delta = E[j][lv] - X[i, cols]
                    uAd = Au[cols] @ delta
                    dAd = delta @ Acc @ delta
                    # det(I + C U'AU) of the 2 x 2 matrix, C U'AU = [[uAd, dAd], [uAu + c uAd, uAd + c dAd]] / J
                    det2 = (1 + uAd / J) * (1 + (uAd + c * dAd) / J) - dAd * (uAu + c * uAd) / J ** 2
                    if det2 <= 0:
                        continue
                    # minus the change of the balance term: the sum of squared counts changes by 2 (n_lv + 1 - n_current)
                    crit = logdet + np.log(det2) - W_BAL * 2 * (int(cnt[lv]) + 1 - int(cnt[current])) / n_profiles
                    if crit > best_c + 1e-9:
                        best_lv, best_c, best_delta = lv, crit, delta
                D[i, j] = best_lv
                if best_delta is not None:
                    X[i, cols] = E[j][best_lv]
                    part = _task_info(X[rows], alternatives)[0]
                    info = info - parts[t] + part
                    parts[t] = part
                    A, logdet = refresh(info)
                    u = X[i] - X[rows].mean(axis=0)
                    cnt[current] -= 1
                    cnt[best_lv] += 1
                    improved = True
        if not improved:
            break
    return D


def _remove_duplicates_within_tasks(D, levels, alternatives, rng, ok=lambda row: True):
    """Identical alternatives in one task carry no information and confuse participants."""
    for start in range(0, D.shape[0], alternatives):
        for _ in range(200):
            rows = [tuple(D[start + a]) for a in range(alternatives)]
            dup = next((a for a in range(alternatives) if rows.count(rows[a]) > 1), None)
            if dup is None:
                break
            j = rng.randrange(len(levels))
            old = D[start + dup, j]
            D[start + dup, j] = (old + 1 + rng.randrange(levels[j] - 1)) % levels[j]
            if not ok(D[start + dup]):
                D[start + dup, j] = old  # that would be forbidden: try another change
