"""Statistics whose independent units are persons, including repeated diary entries."""
import math
import statistics


def collapse(values, persons):
    """One mean per person; missing values contribute neither a value nor a person."""
    found = {}
    for value, person in zip(values, persons):
        if value is not None:
            found.setdefault(person, []).append(value)
    return {person: statistics.fmean(xs) for person, xs in found.items()}


def matrix_means(matrix, owners):
    found = {}
    for row, person in zip(matrix, owners):
        found.setdefault(person, []).append(row)
    return [[statistics.fmean(column) for column in zip(*rows)] for rows in found.values()]


def within_correlation(xs, ys, owners):
    """Descriptive correlation of person-centred pairs, each person with equal total weight. No iid p-value."""
    groups = {}
    for x, y, owner in zip(xs, ys, owners):
        if x is not None and y is not None:
            groups.setdefault(owner, []).append((x, y))
    xy = xx = yy = 0.0
    people = observations = 0
    for pairs in groups.values():
        if len(pairs) < 2:
            continue
        mx, my = statistics.fmean(x for x, _ in pairs), statistics.fmean(y for _, y in pairs)
        people += 1
        observations += len(pairs)
        for x, y in pairs:
            xy += (x - mx) * (y - my) / len(pairs)
            xx += (x - mx) ** 2 / len(pairs)
            yy += (y - my) ** 2 / len(pairs)
    return (max(-1.0, min(1.0, xy / math.sqrt(xx * yy))) if xx and yy else None), people, observations


def variance(values, weights, persons):
    """Taylor linearization with person score sums, G/(G-1), and independent cluster count G."""
    pairs = [(v, w, p) for v, w, p in zip(values, weights, persons) if v is not None and w]
    whole = sum(w for _, w, _ in pairs)
    if not whole:
        return None, 0
    mean = sum(v * w for v, w, _ in pairs) / whole
    scores = {}
    for value, weight, person in pairs:
        scores[person] = scores.get(person, 0.0) + weight * (value - mean)
    size = len(scores)
    return (size / (size - 1) * sum(s * s for s in scores.values()) / whole ** 2
            if size > 1 else None), size


def mean_ci(values, weights, persons):
    from app import intervals, weights as weighting

    var, size = variance(values, weights, persons)
    if var is None or size < intervals.MIN_N:
        return None, size
    mean = weighting.mean(values, weights)
    half = intervals.t_quantile(0.975, size - 1) * math.sqrt(var)
    return (mean - half, mean + half), size


def share_ci(values, weights, persons, key):
    from app import intervals, weights as weighting

    binary = [float(v == key) for v in values]
    var, size = variance(binary, weights, persons)
    if size < intervals.MIN_N:
        return None
    p = weighting.mean(binary, weights)
    effective = p * (1 - p) / var if var else size
    # Wilson's smoothing may use a smaller effective size, never more independent units than persons.
    effective = min(effective, size)
    return intervals.wilson(p * effective, effective)
