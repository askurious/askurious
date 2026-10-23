"""The estimate of the structural equation model of the results page as an estimation of its own (Ralf 2026-10-17,
thread "Rechenknecht"): with FIML, ordinal data, groups, a competing model and the test of measurement invariance it
held the whole results page for up to minutes. The page prepares the model and the numbers (sem_model.prepare, with
the database), this runs sem_model.numbers in the estimation process at low priority (app/estimate.py) or on the
Rechenknecht (app/compute.py), with the same marker, places and progress as HB and the bootstraps
(maxdiff_hb._estimate_and_store); the page takes the result over (surveys.results._sem_result). No database and no
participation numbers: the ids of the construct scores stay with the page.

The estimates have tuples and tuple keys (paths (a, b)); JSON has neither, so they travel tagged (pack, unpack)."""
import math

from app import sem_model


def pack(x):
    """x as JSON: tuples as {"~t": [...]}, dicts with keys other than text as {"~d": [[key, value], ...]}, numpy
    numbers as Python numbers, NaN and infinities as {"~f": "nan" | "inf" | "-inf"}."""
    if isinstance(x, dict):
        if all(isinstance(k, str) for k in x):
            return {("~" + k if k.startswith("~") else k): pack(v) for k, v in x.items()}
        return {"~d": [[pack(k), pack(v)] for k, v in x.items()]}
    if isinstance(x, tuple):
        return {"~t": [pack(v) for v in x]}
    if isinstance(x, list):
        return [pack(v) for v in x]
    if hasattr(x, "item") and not isinstance(x, (str, bytes)):
        x = x.item() if getattr(x, "ndim", 0) == 0 else x.tolist()
        return pack(x)
    if isinstance(x, float) and not math.isfinite(x):
        return {"~f": "nan" if math.isnan(x) else ("inf" if x > 0 else "-inf")}
    return x


def unpack(x):
    if isinstance(x, dict):
        if set(x) == {"~t"}:
            return tuple(unpack(v) for v in x["~t"])
        if set(x) == {"~d"}:
            return {_key(unpack(k)): unpack(v) for k, v in x["~d"]}
        if set(x) == {"~f"}:
            return float(x["~f"])
        return {(k[1:] if k.startswith("~~") else k): unpack(v) for k, v in x.items()}
    if isinstance(x, list):
        return [unpack(v) for v in x]
    return x


def _key(k):
    return tuple(k) if isinstance(k, list) else k


def run(inp, progress=None):
    """{"key", "found": sem_model.numbers of the packed job}."""
    return {"key": inp["key"], "found": pack(sem_model.numbers(unpack(inp["job"]), progress))}
