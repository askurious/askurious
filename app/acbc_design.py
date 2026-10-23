"""Adaptive choice-based conjoint (ACBC): the concepts of one participant.

The three steps of adaptive choice-based conjoint (Johnson & Orme, 2007), in a reduced form:
  1. Build your own (BYO): the participant picks the preferred level of every attribute.
  2. Screening: concepts near the BYO profile, each "possible" or "not possible". Every concept
     changes between min_changes and max_changes attributes of the BYO profile; the attributes
     and levels that were changed least often so far are preferred, so levels stay balanced
     within the participant. Prohibited level pairs never appear, no concept repeats, and
     none equals the BYO profile.
  3. Choice tournament: the "possible" concepts compete in tasks of up to three; the chosen one
     goes on, until one winner is left. With n concepts that takes ceil((n - 1) / 2) tasks.

Summed price (optional): price = (base + sum of the price components of the levels) x
(1 + u), u uniform in [-variation, +variation] per concept, rounded to the price step. The
variation decorrelates price from the other attributes, so its effect can be estimated.

Everything is deterministic given the seed stored per response (layout["cj"]) and the answers,
so the concepts are computed once (when the BYO page is submitted) and stored in the answer.
"""
import math
import random

JOHNSON_ORME_2007 = ("Johnson, R. M., & Orme, B. K. (2007). A new approach to adaptive CBC. In Proceedings of the Sawtooth "
                     "Software Conference. Sawtooth Software.")


def allowed_fn(forbidden):
    from app.conjoint_design import allowed_fn as fn

    return fn(forbidden)


def screening_concepts(levels, byo, n, min_changes, max_changes, forbidden=None, seed=0):
    """n profiles (lists of level indices) near the BYO profile."""
    rng = random.Random(seed)
    ok = allowed_fn(forbidden)
    k_attr = len(levels)
    changeable = [j for j in range(k_attr) if levels[j] > 1]
    lo = max(1, min(min_changes, len(changeable)))
    hi = max(lo, min(max_changes, len(changeable)))
    level_counts = [[0] * nl for nl in levels]
    changes = [0] * k_attr
    seen = {tuple(byo)}
    out = []
    for _ in range(n):
        profile = None
        for attempt in range(300):
            k = rng.randint(lo, hi)
            # least changed attributes first (ties at random); later attempts purely at random
            if attempt < 50:
                attrs = sorted(changeable, key=lambda j: (changes[j], rng.random()))[:k]
            else:
                attrs = rng.sample(changeable, k)
            cand = list(byo)
            for j in attrs:
                options = [lv for lv in range(levels[j]) if lv != byo[j]]
                if attempt < 50:
                    cand[j] = min(options, key=lambda lv: (level_counts[j][lv], rng.random()))
                else:
                    cand[j] = rng.choice(options)
            if ok(cand) and tuple(cand) not in seen:
                profile = cand
                break
        if profile is None:  # too few distinct allowed profiles: any allowed one, repeats possible
            for _ in range(1000):
                cand = [rng.randrange(nl) for nl in levels]
                if ok(cand):
                    profile = cand
                    break
            else:
                profile = list(byo)
        seen.add(tuple(profile))
        out.append(profile)
        for j in range(k_attr):
            level_counts[j][profile[j]] += 1
            if profile[j] != byo[j]:
                changes[j] += 1
    return out


def summed_price(components, profile, base, variation=0.0, step=1.0, rng=None):
    """Base plus the price components of the chosen levels, varied by +/- variation (share)."""
    raw = base + sum(components[j][lv] for j, lv in enumerate(profile))
    if rng is not None and variation:
        raw *= 1 + rng.uniform(-variation, variation)
    step = step or 1.0
    return round(round(raw / step) * step, 2)


def tournament_tasks(n):
    """Number of tasks (of up to three concepts) to find one winner among n concepts."""
    return max(0, math.ceil((n - 1) / 2))


def tournament(pool, choices, shown=None):
    """Replay the tournament: pool = concept indices in presentation order, choices = {round: chosen
    concept}, shown = {round: group} as written when the round's page was saved (these win over the
    replay; audit data storage and calculations 2026-10-21, D1). Returns [(round, group, chosen or None)]
    for all rounds that can be shown so far, the queue after them, and the winner (None while undecided).
    A round without a (valid) choice ends the replay: it is the round to show next."""
    queue, rounds, r = list(pool), [], 0
    shown = shown or {}
    while len(queue) > 1 or r in shown:
        if r in shown:
            group = list(shown[r])
            chosen = choices.get(r)
            if chosen not in group:
                rounds.append((r, group, None))
                return rounds, queue, None
            rounds.append((r, group, chosen))
            queue = [c for c in queue if c not in group] + [chosen]
            r += 1
            continue
        size = 3 if len(queue) >= 3 else 2
        group = queue[:size]
        chosen = choices.get(r)
        if chosen not in group:
            rounds.append((r, group, None))
            return rounds, queue, None
        rounds.append((r, group, chosen))
        queue = queue[size:] + [chosen]
        r += 1
    return rounds, queue, (queue[0] if queue else None)


def tournament_pool(possible, cap, seed=0):
    """The concepts that enter the tournament: the "possible" ones in random order, at most cap."""
    pool = list(possible)
    random.Random(seed).shuffle(pool)
    return pool[:cap]
