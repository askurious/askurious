"""Analysis of the adaptive conjoint (ACA, app/items/aca.py; concept aca-cva-konzept.md, A4, A6): per person the final
part-worths of app/aca_design.final (self-explicated and pairs combined, Johnson, 1987), with calibration scaled to
logit units; over persons the means with a 95 % t interval (as for the ratings-based conjoint, app/cva_analysis.py), the
importances from the part-worths next to those of the self-explication alone, and the fit of the calibration.

With calibration on, persons without a calibration (fewer than three of its answers, e.g. broken off before it) keep
part-worths in the prior's units (ranges 1 to 4), the others are in logit units: averaging both, or giving both to the
simulator, mixes two scales. So only the calibrated persons enter the means, the importances and the simulator, and
the page names how many were left out; scaling the others with the median slope of the rest would put a scale on
them that they never gave (audit data storage and calculations 2026-10-21, D2). While nobody is calibrated yet, all
persons are shown in the prior's units, and the page says so."""
from app import aca_design
from app.cva_analysis import _mean_ci
from app.intervals import t_quantile


def person(item, answer):
    """{"pw", "importance", "prior_importance", "pairs", "cal_r2"} of one answer, or None before the importance page."""
    if not isinstance(answer, dict) or not answer.get("i"):
        return None
    kind = item.kind
    plan = kind.plan(item, answer)
    pw = plan["final"]
    cal_r2 = None
    if plan["cal"]:
        found = aca_design.calibrate(pw, plan["cal"], [(answer.get("c") or {}).get(str(k)) for k in range(len(plan["cal"]))])
        if found:
            b, _, cal_r2 = found
            pw = [[x * b for x in row] for row in pw]
    return {"pw": pw, "importance": aca_design.importances(pw), "prior_importance": aca_design.importances(plan["prior"]),
            "pairs": plan["answered"], "cal_r2": cal_r2, "calibrated": cal_r2 is not None}


def analyse(item, responses, lang):
    from app.choice_model import charts
    from app.items.base import plain

    kind = item.kind
    attrs = kind.attributes(item, lang)
    fits = []
    for r in responses:
        found = person(item, (r.answers or {}).get(str(item.id)))
        if found is not None:
            fits.append({**found, "pid": r.pid})
    uncalibrated = 0
    if kind.n_cal(item):
        calibrated = [f for f in fits if f["calibrated"]]
        uncalibrated = len(fits) - len(calibrated)
        if calibrated:  # one scale: the persons in logit units only (D2)
            fits = calibrated
    out = {"persons": len(fits), "model": None, "person_pw": [[f["pid"], f["pw"]] for f in fits],
           "price": kind.price_like(item, lang), "calibrated": bool(kind.n_cal(item)) and any(f["calibrated"] for f in fits),
           "uncalibrated": uncalibrated}
    if not fits:
        return out
    n = len(fits)
    rows = []
    for j, a in enumerate(attrs):
        levels = []
        for k, label in enumerate(a["level_labels"]):
            m, se, lo, hi = _mean_ci([f["pw"][j][k] for f in fits])
            levels.append({"label": plain(label), "beta": m, "se": se or 0.0, "lo": lo, "hi": hi})
        imp = _mean_ci([f["importance"][j] for f in fits])[0]
        prior_imp = _mean_ci([f["prior_importance"][j] for f in fits])[0]
        rows.append({"label": plain(a["label"]), "code": a["code"], "levels": levels, "importance": imp,
                     "prior_importance": prior_imp, "ordered": kind.levels_ordered(item, a)})
    r2 = [f["cal_r2"] for f in fits if f["cal_r2"] is not None]
    model = {"attributes": rows, "q": t_quantile(0.975, n - 1) if n > 1 else 1.96, "t_df": n - 1 if n > 1 else None,
             "warnings": [], "persons": n, "price": None, "wtp_ok": False,
             "pairs_mean": sum(f["pairs"] for f in fits) / n,
             "cal_r2": sum(r2) / len(r2) if r2 else None}
    model["charts"] = charts(model)
    out["model"] = model
    return out
