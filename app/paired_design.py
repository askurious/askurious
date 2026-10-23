"""Designs for paired comparisons (app/items/paired.py; concept paarvergleich-konzept.md, E4 and E5).

K options give K (K - 1) / 2 pairs. Up to FULL_UP_TO options every person sees all of them (complete design);
above that every version holds P pairs (by default 2 K, every option about four times per person), chosen so that
every option appears about equally often within the version, every pair about equally often over the versions, and
all options are connected through shared pairs within each version (otherwise the values of a person, and with one
version those of all persons, have no common scale; Ford, 1957).

Within each version every option stands about equally often first (left) and second (right), and two pairs in a row
share no option where this can be avoided (a rule of Askurious: the same option twice in a row would look like a
repetition). The order of the version is the order of the pages; versions are balanced over the participants as for
MaxDiff (ItemType.balanced_version).

Pure functions, seeded: the same input gives the same design.
"""
import random
from itertools import combinations

from app.lang import de_en

MIN_OPTIONS, MAX_OPTIONS = 3, 20
FULL_UP_TO = 8          # up to this many options: all pairs for every person (28 pairs at 8)
PER_OPTION = 4          # default appearances of an option per person in an incomplete design: 2 K pairs
MAX_PAIRS = 190         # all pairs of 20 options
CANDIDATES = 40         # tries per version; the most balanced connected one is kept
FORD = ("Ford, L. R., Jr. (1957). Solution of a ranking problem from binary comparisons. The American Mathematical "
        "Monthly, 64(8, Pt. 2), 28–33. https://doi.org/10.2307/2308513")


def all_pairs(n):
    return n * (n - 1) // 2


def suggested_pairs(n):
    """Pairs per person when left empty: all of them up to FULL_UP_TO options, else about PER_OPTION per option."""
    if n <= FULL_UP_TO:
        return all_pairs(n)
    return min(all_pairs(n), PER_OPTION * n // 2)


def generate(n, pairs, n_versions=1, seed=None):
    """versions[v] = [[first, second], ...] in page order (0-based option indices). Raises ValueError for
    impossible shapes."""
    if n < MIN_OPTIONS:
        raise ValueError(de_en("Ein Paarvergleich braucht mindestens {n} Optionen.",
                               "A paired comparison needs at least {n} options.", n=MIN_OPTIONS))
    if not 1 <= pairs <= all_pairs(n):
        raise ValueError(de_en("Paare je Person: 1 bis {m} bei {n} Optionen.", "Pairs per person: 1 to {m} with {n} options.",
                               m=all_pairs(n), n=n))
    rng = random.Random(seed)
    total = {p: 0 for p in combinations(range(n), 2)}  # how often each pair stands in the versions so far
    versions = []
    for _ in range(max(1, n_versions)):
        chosen = list(total) if pairs == all_pairs(n) else _choose(n, pairs, total, rng)
        for p in chosen:
            total[p] += 1
        versions.append(_order(_orient(chosen, n, rng), rng))
    return versions


def _choose(n, pairs, total, rng):
    """The pairs of one incomplete version: CANDIDATES greedy tries, the best by (connected, spread of the option
    frequencies, spread of the pair counts over the versions)."""
    best, best_key = None, None
    for _ in range(CANDIDATES):
        freq = [0] * n
        left = set(total)
        out = []
        for _ in range(pairs):
            low = min(freq[i] + freq[j] for i, j in left)
            pick = [p for p in left if freq[p[0]] + freq[p[1]] == low]
            fewest = min(total[p] for p in pick)
            p = rng.choice(sorted(q for q in pick if total[q] == fewest))
            left.discard(p)
            out.append(p)
            freq[p[0]] += 1
            freq[p[1]] += 1
        counts = [total[p] + 1 for p in out]
        key = (not connected(n, [out]), max(freq) - min(freq), max(counts) - min(counts), rng.random())
        if best_key is None or key < best_key:
            best, best_key = out, key
    return best


def _orient(chosen, n, rng):
    """Which option of each pair comes first: the one that stood first less often so far (random among ties)."""
    first = [0] * n
    shown = [0] * n
    out = []
    for i, j in rng.sample(chosen, len(chosen)):
        a = (first[i] / (shown[i] or 1), rng.random())
        b = (first[j] / (shown[j] or 1), rng.random())
        pair = (i, j) if a <= b else (j, i)
        first[pair[0]] += 1
        shown[i] += 1
        shown[j] += 1
        out.append(list(pair))
    return out


def _order(pairs, rng):
    """Page order: at each step a random pair that shares no option with the one before, where one is left."""
    left = list(pairs)
    rng.shuffle(left)
    out = []
    while left:
        prev = set(out[-1]) if out else set()
        k = next((n for n, p in enumerate(left) if not prev & set(p)), 0)
        out.append(left.pop(k))
    return out


def connected(n, versions):
    """Whether the pairs of all given versions together connect all n options."""
    parent = list(range(n))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for version in versions:
        for i, j in version:
            parent[root(i)] = root(j)
    return len({root(i) for i in range(n)}) == 1


def diagnostics(n, versions):
    """Balance figures for the editor: per version the frequency range of the options, the largest difference between
    first and second place of an option, whether every version connects all options, and over all versions how often
    each pair was used."""
    freq_lo, freq_hi, side = n * n, 0, 0
    pair_count = {p: 0 for p in combinations(range(n), 2)}
    for version in versions:
        freq, first = [0] * n, [0] * n
        for a, b in version:
            freq[a] += 1
            freq[b] += 1
            first[a] += 1
            pair_count[(min(a, b), max(a, b))] += 1
        freq_lo, freq_hi = min(freq_lo, min(freq)), max(freq_hi, max(freq))
        side = max(side, max(abs(2 * first[i] - freq[i]) for i in range(n)))
    counts = list(pair_count.values())
    return {"freq_min": freq_lo, "freq_max": freq_hi, "side_spread": side,
            "pair_min": min(counts), "pair_max": max(counts),
            "connected_each": all(connected(n, [v]) for v in versions), "connected": connected(n, versions)}
