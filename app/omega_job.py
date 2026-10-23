"""The percentile bootstrap of McDonald's omega for the scales of the results page as an estimation of its own (Ralf
2026-10-07, thread "Serverlast bei den Berechnungen"): about 20 s at 2000 participations, which held a web process
while the page's omega part waited. Started by results.omega_part_state, run like HB, segments and the SEM bootstrap
in the estimation process at low priority (app/estimate.py) or on the Rechenknecht (app/compute.py), with the same
marker, places and progress (maxdiff_hb._estimate_and_store). No database and no ids: per scale the complete answers
and per row the number of its row in the table (the bootstrap draws persons with all their passes in a loop)."""
from app import intervals


def run(inp, progress=None):
    """{"key", "found": {scale key: {"ci", "failed", "pooled"}}}; inp: {"key", "scales": {key: {"matrix", "persons",
    "pooled"}}}. The same numbers as compute(..., omega_ci=True): same cases, same seed."""
    scales = inp["scales"]
    found = {}
    for n, (ref, s) in enumerate(sorted(scales.items())):
        ci, failed = intervals.omega_boot(s["matrix"], persons=s.get("persons"))
        found[ref] = {"ci": ci, "failed": failed, "pooled": bool(s.get("pooled"))}
        if progress:
            progress((n + 1) / len(scales))
    return {"key": inp["key"], "found": found}
