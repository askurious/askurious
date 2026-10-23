"""Balanced incomplete designs for MaxDiff (best-worst scaling, case 1).

K statements, T sets of k statements each, V versions. A good design shows every statement about
equally often (1-way balance), every pair of statements about equally often together (2-way balance),
every statement about equally often in each position of a set (position balance), never twice in one
set, and connects all statements through shared sets (otherwise their scores are not comparable).

Construction: sets are filled with the statements shown least so far (random among ties), then improved
by random swaps: replacing a statement in a set, or exchanging two statements between two sets. Swaps
are kept if they lower the imbalance: squared frequencies (within the version and over all versions so
far, weighted high) plus squared pair counts. The display order within each set is then chosen so that
positions are balanced. Versions are built one after another, so the leftover imbalance (when T·k is no
multiple of K) falls on different statements in each version.

Pure functions, seeded: the same input gives the same design.
"""
import math
import random
from itertools import permutations

from app.conjoint_design import ORME
from app.lang import de_en

MIN_SET, MAX_SET = 3, 6
# The rules of sets_needed and suggested_sets are Festlegungen von Askurious: 500 / 1000 showings of every statement
# over the sample carry over the rule of thumb for choice experiments (500, better 1000 representations per level;
# Orme, 2010, ch. 7) to the statements of best-worst scaling; three to five showings per person give a person's own
# scores enough choices per statement. Best-worst scaling itself: Louviere, Flynn & Marley (2015).
LOUVIERE = ("Louviere, J. J., Flynn, T. N., & Marley, A. A. J. (2015). Best-worst scaling: Theory, methods and "
            "applications. Cambridge University Press. https://doi.org/10.1017/CBO9781107337855")
SETS_SOURCES = (LOUVIERE, ORME)


def allowed_set_sizes(n_items):
    """Set sizes that make sense for K statements: 3 to min(6, K - 1)."""
    return list(range(MIN_SET, min(MAX_SET, n_items - 1) + 1))


def suggested_sets(n_items, set_size):
    """About three appearances per statement and person: ceil(3 K / k) (Festlegung von Askurious, see above)."""
    return max(1, math.ceil(3 * n_items / max(set_size, 1)))


def sets_needed(n, n_items, set_size):
    """Sets per person for a sample of n so that every statement is shown at least 500 times, better 1000
    times, over all participants (Festlegung von Askurious after Orme, 2010, for aggregate analysis):
    T >= 500 * K / (n * k)."""
    if n <= 0 or set_size <= 0:
        return 0, 0
    shown = n * set_size
    return max(1, -(-500 * n_items // shown)), max(1, -(-1000 * n_items // shown))


def generate(n_items, set_size, n_sets, n_versions=1, seed=None, iterations=None):
    """versions[v][t] = [statement indices] in display order. Raises ValueError for impossible shapes."""
    if n_items < MIN_SET + 1:
        raise ValueError(de_en("MaxDiff braucht mindestens {n} Aussagen.", "MaxDiff needs at least {n} statements.",
                               n=MIN_SET + 1))
    if set_size not in allowed_set_sizes(n_items):
        raise ValueError(de_en("Aussagen je Set: {lo} bis {hi} bei {n} Aussagen.",
                               "Statements per set: {lo} to {hi} with {n} statements.",
                               lo=MIN_SET, hi=min(MAX_SET, n_items - 1), n=n_items))
    if n_sets < 1 or n_versions < 1:
        raise ValueError(de_en("Mindestens ein Set und eine Version.", "At least one set and one version."))
    rng = random.Random(seed)
    total_freq = [0] * n_items
    total_pairs = [[0] * n_items for _ in range(n_items)]
    total_pos = [[0] * set_size for _ in range(n_items)]
    versions = []
    for _ in range(n_versions):
        sets = _one_version(n_items, set_size, n_sets, rng, total_freq, total_pairs, iterations)
        # a version that does not connect all statements is rebuilt a few times (rare, needs T·(k-1) >= K-1)
        for _retry in range(5):
            if connected(n_items, [sets]) or n_sets * (set_size - 1) < n_items - 1:
                break
            sets = _one_version(n_items, set_size, n_sets, rng, total_freq, total_pairs, iterations)
        for s in sets:
            for i in s:
                total_freq[i] += 1
            for a in s:
                for b in s:
                    if a != b:
                        total_pairs[a][b] += 1
        ordered = _order_positions(sets, n_items, set_size, rng, total_pos)
        for s in ordered:
            for n, i in enumerate(s):
                total_pos[i][n] += 1
        versions.append(ordered)
    return versions


def _one_version(K, k, T, rng, total_freq, total_pairs, iterations=None):
    freq = [0] * K
    pairs = [[0] * K for _ in range(K)]

    # greedy start: least shown statements first (this version, then all versions), random among ties
    sets = []
    for _ in range(T):
        order = sorted(range(K), key=lambda i: (freq[i], total_freq[i], rng.random()))
        s = order[:k]
        for i in s:
            freq[i] += 1
        for a in s:
            for b in s:
                if a != b:
                    pairs[a][b] += 1
        sets.append(s)

    W_FREQ, W_TOTAL = 100 * K, 10 * K  # frequency balance comes first, pairs after

    def replace_delta(s, a, b):
        """Change of the imbalance when statement a in set s is replaced by b."""
        d = W_FREQ * 2 * (freq[b] - freq[a] + 1)
        d += W_TOTAL * 2 * (total_freq[b] + freq[b] - total_freq[a] - freq[a] + 1)
        for c in s:
            if c != a:
                d += 2 * (pairs[b][c] - pairs[a][c] + 1)
                d += 2 * (total_pairs[b][c] + pairs[b][c] - total_pairs[a][c] - pairs[a][c] + 1)
        return d

    def apply(t, a, b):
        s = sets[t]
        for c in s:
            if c != a:
                pairs[a][c] -= 1
                pairs[c][a] -= 1
                pairs[b][c] += 1
                pairs[c][b] += 1
        freq[a] -= 1
        freq[b] += 1
        s[s.index(a)] = b

    n_iter = iterations if iterations is not None else max(2000, 60 * T * k)
    for _ in range(n_iter):
        t = rng.randrange(T)
        s = sets[t]
        a = rng.choice(s)
        if rng.random() < 0.5 or T < 2:
            b = rng.randrange(K)
            if b in s:
                continue
            d = replace_delta(s, a, b)
            if d < 0 or (d == 0 and rng.random() < 0.3):
                apply(t, a, b)
        else:
            u = rng.randrange(T)
            if u == t:
                continue
            b = rng.choice(sets[u])
            if b in s or a in sets[u]:
                continue
            d1 = replace_delta(s, a, b)
            apply(t, a, b)
            d2 = replace_delta(sets[u], b, a)
            if d1 + d2 < 0 or (d1 + d2 == 0 and rng.random() < 0.3):
                apply(u, b, a)
            else:
                apply(t, b, a)  # undo
    return [list(s) for s in sets]


def _order_positions(sets, K, k, rng, total_pos=None, passes=3):
    """Display order within each set so that each statement appears about equally often in each position
    (within the version first, then over the versions before it: total_pos)."""
    pos = [[0] * k for _ in range(K)]
    order = [None] * len(sets)
    perms = list(permutations(range(k)))
    for p in range(passes):
        for t, s in enumerate(sets):
            if order[t] is not None:
                for n, i in enumerate(order[t]):
                    pos[i][n] -= 1
            best, best_cost = None, None
            rng.shuffle(perms)
            for perm in perms:
                cost = sum(10 * pos[s[perm[n]]][n] + (total_pos[s[perm[n]]][n] if total_pos else 0) for n in range(k))
                if best_cost is None or cost < best_cost:
                    best, best_cost = perm, cost
            order[t] = [s[best[n]] for n in range(k)]
            for n, i in enumerate(order[t]):
                pos[i][n] += 1
    return order


def connected(n_items, versions):
    """True if all statements are linked through shared sets (in the given versions together)."""
    seen = set()
    adj = [set() for _ in range(n_items)]
    for sets in versions:
        for s in sets:
            for a in s:
                adj[a].update(s)
    stack = [0]
    while stack:
        i = stack.pop()
        if i in seen:
            continue
        seen.add(i)
        stack.extend(adj[i] - seen)
    return len(seen) == n_items


def diagnostics(n_items, versions):
    """Balance figures: statement frequency, pair co-occurrence and positions, per version (worst version)
    and over all versions; connectedness; duplicates in a set (should be 0)."""
    def stats(vs):
        freq = [0] * n_items
        pairs = {}
        k = max((len(s) for sets in vs for s in sets), default=0)
        pos = [[0] * k for _ in range(n_items)]
        dup = 0
        for sets in vs:
            for s in sets:
                dup += len(s) - len(set(s))
                for n, i in enumerate(s):
                    freq[i] += 1
                    pos[i][n] += 1
                for x in range(len(s)):
                    for y in range(x + 1, len(s)):
                        key = (min(s[x], s[y]), max(s[x], s[y]))
                        pairs[key] = pairs.get(key, 0) + 1
        all_pairs = [pairs.get((a, b), 0) for a in range(n_items) for b in range(a + 1, n_items)]
        pos_spread = max((max(p) - min(p) for p in pos if k), default=0)
        return {"freq_min": min(freq), "freq_max": max(freq), "pair_min": min(all_pairs, default=0),
                "pair_max": max(all_pairs, default=0), "pos_spread": pos_spread, "duplicates": dup}

    per_version = [stats([sets]) for sets in versions]
    total = stats(versions)
    return {
        "total": total,
        "version": {key: (min(s[key] for s in per_version) if key.endswith("_min") else max(s[key] for s in per_version))
                    for key in per_version[0]} if per_version else {},
        "connected": connected(n_items, versions),
        "connected_each": all(connected(n_items, [sets]) for sets in versions),
    }
