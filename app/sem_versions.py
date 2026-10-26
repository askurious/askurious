"""Versions of the structural equation model of a survey (model versions 2026-10-26, concept
pls-formativ-versionen-konzept.md, E9-E14 as recommended).

Every estimate with other model settings (method, the model settings of the study plan, the hypotheses of the model)
becomes a version when the results page shows it; the same settings make none. A version keeps its figures as they
were (N, paths, R², measurement, redundancy, BIC or fit, PLSpredict): "mit aktuellen Daten neu rechnen" makes a new
one. The main model is the version whose settings the pre-registration snapshot froze at the start of the fieldwork
(without a snapshot, or none matching: the first version); every other is exploratory, and the methods draft and the
Word tables say so (MacCallum et al., 1992; Simmons et al., 2011). Two versions side by side: what changed, the
paths, R², the measurement and, with PLS, BIC per explained construct with BIC weights (Sharma et al., 2019).

At most MAX versions per survey: the oldest one without a name goes, never the main model. No answers and no
people are stored, only settings and aggregated figures; the rows go with the survey (lifecycle.delete)."""
import hashlib
import json
import math

from app.extensions import db
from app.lang import de_en
from app.models import SemVersion

MAX = 30
SIMMONS = ("Simmons, J. P., Nelson, L. D., & Simonsohn, U. (2011). False-positive psychology: Undisclosed flexibility "
           "in data collection and analysis allows presenting anything as significant. Psychological Science, 22(11), "
           "1359–1366. https://doi.org/10.1177/0956797611417632")
SHARMA = ("Sharma, P. N., Sarstedt, M., Shmueli, G., Kim, K. H., & Thiele, K. O. (2019). PLS-based model selection: "
          "The role of alternative explanations in information systems research. Journal of the Association for "
          "Information Systems, 20(4), 346–397. https://doi.org/10.17705/1jais.00538")


def settings_from_plan(plan):
    """The settings of the model a plan dict names: {"method", "sem", "hyps"}; the same plan gives the same dict."""
    from app import sem_model, study_plan

    raw = plan if isinstance(plan, dict) else {}
    method = raw.get("sem_method") if raw.get("sem_method") in study_plan.SEM_METHODS else ""
    hyps = [[h.get(k) or "" for k in ("kind", "iv", "mv", "wv", "dv", "analysis")]
            for h in raw.get("hypotheses") or [] if isinstance(h, dict) and sem_model.wanted(h)]
    return {"method": method or "cb", "sem": study_plan.sem_options(raw.get("sem")), "hyps": hyps}


def settings_of(survey):
    return settings_from_plan(survey.study_plan or {})


def key_of(settings):
    return hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()[:32]


def _bic(r2, k, n):
    """BIC of an explained construct in PLS (Sharma et al., 2019): n·ln(1 − R²) + (k + 1)·ln(n), k its predictors."""
    if r2 is None or n is None or n < 2 or r2 >= 1:
        return None
    return n * math.log(1 - r2) + (k + 1) * math.log(n)


def figures_of(result, boot=None):
    """The figures of an estimate to keep with its version (plain JSON)."""
    from app import sem_model

    fit = result.get("fit") or {}
    names = result.get("nodes") or {}
    spec = result.get("spec") or {}
    n = result.get("n")
    paths = []
    for r in fit.get("paths") or []:
        row = sem_model.path_row(result, r["from"], r["to"], boot) or r
        paths.append([r["from"], r["to"], row.get("std"), row.get("p")])
    mtable, _ = sem_model.measurement_table(result)
    measure = {c["name"]: {"alpha": c.get("alpha"), "cr": c.get("cr"), "ave": c.get("ave")} for c in mtable}
    redundancy = {r["lv"]: r.get("path") for r in result.get("redundancy") or [] if r.get("path") is not None}
    out = {"n": n, "method": result.get("method"), "boot_key": result.get("boot_key"),
           "boot": (boot or {}).get("draws"), "paths": paths,
           "r2": {v: r2 for v, r2 in (fit.get("r2") or {}).items()}, "measure": measure, "redundancy": redundancy,
           "labels": {k: v[1] for k, v in names.items()}}
    if result.get("method") == "pls":
        preds = {}
        for a, b in spec.get("paths") or ():
            preds[b] = preds.get(b, 0) + 1
        out["bic"] = {v: _bic(r2, preds.get(v, 0), n) for v, r2 in out["r2"].items()}
        pred = sem_model.predict_table(result)
        if pred and pred.get("items"):
            out["predict"] = [sum(1 for i in pred["items"] if i["better"]), len(pred["items"])]
    else:
        q = fit.get("q")
        chi2 = fit.get("chi2")
        out["fit"] = {k: fit.get(k) for k in ("chi2", "df", "cfi", "rmsea", "srmr")}
        if chi2 is not None and q:
            out["fit"]["aic"] = chi2 + 2 * q
            out["fit"]["bic"] = chi2 + q * math.log(n) if n else None
    return json.loads(json.dumps(out, default=float))


def versions(survey):
    return db.session.scalars(db.select(SemVersion).filter_by(survey_id=survey.id).order_by(SemVersion.number)).all()


def record(survey, result, boot=None, force=False):
    """Keep the estimate shown as a version: a new one for settings not seen yet (or force: the current data with the
    same settings), else the figures of the version of these settings once the bootstrap of the same data is there.
    The version, or None (no estimate, an error, the weighted page)."""
    if not result or result.get("error") or not result.get("fit") or result.get("weighted"):
        return None
    settings = settings_of(survey)
    key = key_of(settings)
    every = versions(survey)
    same = [v for v in every if v.key == key]
    if same and not force:
        last = same[-1]
        figures = last.figures or {}
        if boot and not figures.get("boot") and figures.get("boot_key") == result.get("boot_key"):
            last.figures = figures_of(result, boot)  # the intervals and p of the same data came
            db.session.commit()
        return last
    v = SemVersion(survey_id=survey.id, number=(every[-1].number + 1) if every else 1, key=key, name="",
                   settings=json.loads(json.dumps(settings)), figures=figures_of(result, boot))
    db.session.add(v)
    db.session.flush()
    _prune(survey, every + [v])
    db.session.commit()
    return v


def _prune(survey, every):
    main = main_of(survey, every)
    over = len(every) - MAX
    for v in every:
        if over <= 0:
            break
        if v.name or (main and v.id == main.id):
            continue
        db.session.delete(v)
        over -= 1


def prereg_key(survey):
    """The key of the model settings in the first pre-registration snapshot (app/prereg.py), or None."""
    from app import prereg

    try:
        listing = prereg.listing(survey)
    except OSError:
        return None
    if not listing:
        return None
    frozen = prereg.frozen_definition(survey, listing[-1][0])  # the oldest: the start of the fieldwork
    if not isinstance(frozen, dict):
        return None
    plan = frozen.get("study_plan") or (frozen.get("settings") or {}).get("study_plan")
    return key_of(settings_from_plan(plan)) if isinstance(plan, dict) else None


def main_of(survey, every=None, frozen=False):
    """The main model: the version of the pre-registered settings, else the first one."""
    every = versions(survey) if every is None else every
    if not every:
        return None
    key = prereg_key(survey)
    found = [v for v in every if key and v.key == key]
    return found[0] if found else every[0]


def page(survey):
    """What the results page shows: {"versions": [...], "main", "current", "exploratory"} or None without any."""
    every = versions(survey)
    if not every:
        return None
    main = main_of(survey, every)
    key = key_of(settings_of(survey))
    current = next((v for v in reversed(every) if v.key == key), None)
    rows = []
    for k, v in enumerate(every):
        before = every[k - 1] if k else None
        rows.append({"v": v, "main": v.id == main.id, "changes": changes(before.settings if before else None, v.settings,
                                                                          v.figures, before.figures if before else None),
                     "r2": _first_r2(v.figures), "n": (v.figures or {}).get("n"), "score": _score(v.figures)})
    return {"versions": rows, "main": main, "current": current,
            "exploratory": bool(current and main and current.id != main.id and current.key != main.key),
            "prereg": prereg_key(survey) is not None}


def _first_r2(figures):
    r2 = (figures or {}).get("r2") or {}
    return max(r2.values()) if r2 else None


def _score(figures):
    """The one model-selection figure shown in the list: the sum of the BIC of the explained constructs (PLS) or the
    BIC of the model (covariance-based)."""
    f = figures or {}
    if f.get("bic"):
        values = [v for v in f["bic"].values() if v is not None]
        return sum(values) if values else None
    return (f.get("fit") or {}).get("bic")


def _names(codes):
    return ", ".join(codes) if codes else "–"


def changes(old, new, figures=None, old_figures=None):
    """[sentence] of what the settings `new` change against `old` (None: the first version)."""
    if old is None:
        return [de_en("erste Version", "first version")]
    out = []
    if old.get("method") != new.get("method"):
        out.append(de_en("Verfahren: {m}", "method: {m}", m="PLS" if new.get("method") == "pls" else "CB"))
    if old.get("hyps") != new.get("hyps"):
        out.append(de_en("Hypothesen im Modell geändert", "hypotheses in the model changed"))
    a, b = old.get("sem") or {}, new.get("sem") or {}
    words = {"controls": de_en("Kontrollvariablen", "control variables"), "formative": de_en("formativ", "formative"),
             "second": de_en("höhere Ordnung", "higher order"), "single": de_en("Einzelfragen", "single questions"),
             "alternative": de_en("Konkurrenzmodell", "competing model"), "invariance": de_en("Messinvarianz", "invariance"),
             "missing": "FIML", "ordinal": "WLSMV", "global": de_en("Globalmaß", "global measure"),
             "higher_formative": de_en("höhere Ordnung formativ", "higher order formative")}
    for k, word in words.items():
        x, y = a.get(k), b.get(k)
        if x == y:
            continue
        if isinstance(x, list) and isinstance(y, list):
            plus, minus = [c for c in y if c not in x], [c for c in x if c not in y]
            out.append(word + ": " + " ".join(([f"+{_names(plus)}"] if plus else []) + ([f"−{_names(minus)}"] if minus else [])))
        else:
            out.append(de_en("{w} geändert", "{w} changed", w=word))
    if a.get("hoc") != b.get("hoc"):
        out.append(de_en("Konstrukte höherer Ordnung geändert", "higher-order constructs changed"))
    gone = {c: r for c, r in (b.get("dropped") or {}).items() if c not in (a.get("dropped") or {})}
    back = [c for c in (a.get("dropped") or {}) if c not in (b.get("dropped") or {})]
    for c, reason in gone.items():
        out.append(de_en("{c} weggelassen · Grund: „{r}“", "{c} left out · reason: “{r}”", c=c, r=reason))
    if back:
        out.append(de_en("wieder aufgenommen: {c}", "taken in again: {c}", c=_names(back)))
    if not out:
        out.append(de_en("dieselben Einstellungen, neue Daten (N = {n})", "the same settings, new data (N = {n})",
                         n=(figures or {}).get("n")))
    return out


def compare(a, b):
    """Two versions side by side: {"changes", "rows": [(label, value a, value b, difference)], "weights"}: paths,
    R², reliability and AVE, redundancy, BIC (PLS: per explained construct, with BIC weights; covariance-based: AIC,
    BIC and fit), PLSpredict."""
    fa, fb = a.figures or {}, b.figures or {}
    labels = {**(fa.get("labels") or {}), **(fb.get("labels") or {})}

    def name(v):
        return labels.get(v, v)

    rows = []
    pa = {(p[0], p[1]): p for p in fa.get("paths") or []}
    pb = {(p[0], p[1]): p for p in fb.get("paths") or []}
    for pair in list(dict.fromkeys(list(pa) + list(pb))):
        x, y = pa.get(pair), pb.get(pair)
        rows.append({"label": f"{name(pair[0])} → {name(pair[1])} (β)", "a": x[2] if x else None,
                     "b": y[2] if y else None, "pa": x[3] if x else None, "pb": y[3] if y else None})
    for v in list(dict.fromkeys(list(fa.get("r2") or {}) + list(fb.get("r2") or {}))):
        rows.append({"label": f"R² {name(v)}", "a": (fa.get("r2") or {}).get(v), "b": (fb.get("r2") or {}).get(v)})
    for v in list(dict.fromkeys(list(fa.get("measure") or {}) + list(fb.get("measure") or {}))):
        ma, mb = (fa.get("measure") or {}).get(v) or {}, (fb.get("measure") or {}).get(v) or {}
        rows.append({"label": f"CR {name(v)}", "a": ma.get("cr"), "b": mb.get("cr")})
        rows.append({"label": f"AVE {name(v)}", "a": ma.get("ave"), "b": mb.get("ave")})
    for v in list(dict.fromkeys(list(fa.get("redundancy") or {}) + list(fb.get("redundancy") or {}))):
        rows.append({"label": de_en("Redundanz {c} (Pfad)", "redundancy {c} (path)", c=name(v)),
                     "a": (fa.get("redundancy") or {}).get(v), "b": (fb.get("redundancy") or {}).get(v)})
    weights = []
    for v in list(dict.fromkeys(list(fa.get("bic") or {}) + list(fb.get("bic") or {}))):
        x, y = (fa.get("bic") or {}).get(v), (fb.get("bic") or {}).get(v)
        rows.append({"label": de_en("BIC {c} (kleiner ist besser)", "BIC {c} (smaller is better)", c=name(v)), "a": x, "b": y})
        if x is not None and y is not None:
            low = min(x, y)
            ea, eb = math.exp(-(x - low) / 2), math.exp(-(y - low) / 2)
            weights.append({"label": name(v), "a": ea / (ea + eb), "b": eb / (ea + eb)})
    for k, word in (("aic", "AIC"), ("bic", "BIC"), ("cfi", "CFI"), ("rmsea", "RMSEA"), ("srmr", "SRMR")):
        x, y = (fa.get("fit") or {}).get(k), (fb.get("fit") or {}).get(k)
        if x is not None or y is not None:
            rows.append({"label": word, "a": x, "b": y})
    for r in rows:
        r.setdefault("pa", None)
        r.setdefault("pb", None)
        r["diff"] = r["b"] - r["a"] if r.get("a") is not None and r.get("b") is not None else None
    pred = [fa.get("predict"), fb.get("predict")]
    return {"changes": changes(a.settings, b.settings, fb, fa), "rows": rows, "weights": weights, "predict": pred,
            "n": [fa.get("n"), fb.get("n")]}


def adopt(survey, version):
    """Set the model settings of the plan (method and the model settings) to those of `version`; the hypotheses stay."""
    plan = dict(survey.study_plan or {})
    s = version.settings or {}
    plan["sem"] = dict(s.get("sem") or {})
    plan["sem_method"] = s.get("method") or plan.get("sem_method") or ""
    survey.study_plan = plan
