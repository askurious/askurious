"""The bootstrap of a structural equation model as an estimation of its own (app/sem_model.py): started from
the results page (app/sem_model.start_bootstrap), run like HB and the segments in the estimation process at low
priority (app/estimate.py) or, where one is set up, on the Rechenknecht (app/compute.py), with the same marker,
places and progress (maxdiff_hb._estimate_and_store). No database: the input holds the complete cases of the model
(numbers only, no ids), the model and the method."""
from app import sem


def flat(summary):
    return [None, None, None] if summary is None else [summary["ci"][0], summary["ci"][1], summary["se"]]


def run(inp, progress=None):
    """{"key", "draws", "failed", "inadmissible", "paths": [[from, to, lo, hi, se]],
    "indirect": [[x, m, y, lo, hi, se]], "cov": [[a, b, lo, hi, se]]}."""
    spec = {"latent": inp["spec"]["latent"], "observed": inp["spec"]["observed"],
            "paths": [tuple(p) for p in inp["spec"]["paths"]], "indirect": [tuple(t) for t in inp["spec"]["indirect"]]}
    if inp["spec"].get("cov"):  # pairs without a direction (modell-zeichnen-konzept.md, F4)
        spec["cov"] = [tuple(p) for p in inp["spec"]["cov"]]
    if inp["spec"].get("products"):  # interaction terms (moderation, F2): PLS takes them in two stages
        spec["products"] = inp["spec"]["products"]
    if inp["spec"].get("modmed"):  # moderated mediations (stage 3): the index per draw
        spec["modmed"] = [tuple(t) for t in inp["spec"]["modmed"]]
    if inp["spec"].get("single"):  # single questions with a fixed reliability (SEM extensions 2026-10-17, point 7)
        spec["single"] = inp["spec"]["single"]
    if inp["spec"].get("formative"):  # formative constructs (PLS mode B, point 9)
        spec["formative"] = inp["spec"]["formative"]
    if inp["spec"].get("missing") == "fiml":  # full information ML: the rows keep their gaps (None, point 2)
        spec["missing"] = "fiml"
    if inp["spec"].get("ordinal"):  # ordered categories, WLSMV (point 3)
        spec["ordinal"] = True
    if inp["spec"].get("higher"):  # factors of the second order (point 8)
        spec["higher"] = inp["spec"]["higher"]
    start = None
    if inp["method"] == "cb" and spec.get("ordinal"):
        from app import sem_ordinal

        start = sem_ordinal.fit_wlsmv(inp["rows"], inp["cols"], spec, light=True).get("theta")
    elif inp["method"] == "cb" and spec.get("missing") == "fiml":
        from app import sem_means

        start = sem_means.fit_fiml(inp["rows"], inp["cols"], spec, light=True).get("theta")
    elif inp["method"] == "cb":  # every draw starts at the estimate of the whole sample: a few steps instead of many
        start = sem.fit_ml(inp["rows"], inp["cols"], spec, robust=False, light=True, weights=inp.get("weights")).get("theta")
    red = inp.get("redundancy") or []
    share = 1.0 / (1 + 0.25 * len(red))  # the redundancy models are small: about a quarter of the main one each
    found = sem.bootstrap(inp["rows"], inp["cols"], spec, inp["method"], draws=inp["draws"], seed=inp.get("seed", 1),
                          progress=(lambda done, total: progress(share * done / total)) if progress else None,
                          start=start, weights=inp.get("weights"))
    redundancy = []
    for k, entry in enumerate(red):  # the paths of the redundancy analyses (formative check 2026-10-26)
        rspec = {**entry["spec"], "paths": [tuple(p) for p in entry["spec"]["paths"]]}
        base = share + k * (1 - share) / len(red)

        def step(done, total, base=base):
            if progress:
                progress(base + (1 - share) / len(red) * done / total)

        try:
            got = sem.bootstrap(entry["rows"], entry["cols"], rspec, "pls", draws=inp["draws"],
                                seed=inp.get("seed", 1), progress=step)
            redundancy.append([entry["lv"], *flat(next(iter(got["paths"].values())))])
        except sem.SemError:  # too few rows or a constant column: the row stays without an interval
            redundancy.append([entry["lv"], None, None, None])

    return {"key": inp["key"], "method": inp["method"], "draws": found["draws"], "failed": found["failed"],
            "inadmissible": found["inadmissible"],
            "paths": [[a, b, *flat(v)] for (a, b), v in found["paths"].items()],
            "indirect": [[x, m, y, *flat(v)] for (x, m, y), v in found["indirect"].items()],
            "cov": [[a, b, *flat(v)] for (a, b), v in found["cov"].items()],
            "modmed": [[*t, *flat(v)] for t, v in found["modmed"].items()],
            "weights": [[lv, i, *flat(v)] for (lv, i), v in found["weights"].items()],
            "loadings": [[lv, i, *flat(v)] for (lv, i), v in found["loadings"].items()],
            "redundancy": redundancy,
            "htmt": [[a, b, *(flat(v)[:2] + [v["upper95"]] if v else [None, None, None])] for (a, b), v in found["htmt"].items()]}
