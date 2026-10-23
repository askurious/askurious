"""Individual conjoint utilities (CBC and ACBC) by hierarchical Bayes (app/hb.py), stored per item.

Same choice sets and coding as the aggregate logit (app/choice_model.py): CBC per task a choice among the
options, with "none" as an alternative with its own constant; ACBC every rated screening concept as a
choice between the concept and the threshold "not possible", every decided tournament task as a choice
among its two or three concepts. Effects coding per attribute; a price (the ACBC summed price or a CBC
attribute marked as price) enters linearly. Prior covariance: per attribute the covariance of the
zero-centred utilities of independent levels with variance 1 (I - 11'/L for its L - 1 parameters), 1 for
the constant and the (standardised) price, so no level is treated differently from the others.

Per person: zero-centred part-worths per attribute (posterior mean), the constant, the price coefficient
per currency unit and the importance of every attribute: its range of part-worths as a share of the sum
of ranges (price: |coefficient| x price range), computed from the person's posterior mean part-worths
(not at every draw: the noise of single draws widens every range). The shrunken individual values make
attributes that vary little between persons (often the price) look somewhat more important. Means over
persons are taken afterwards; the part-worths, constant and price also get the 95 % posterior interval of
the mean (mean over persons at every kept draw), the importances none.
No individual willingness to pay: a ratio of two individual coefficients is too unstable.

Storage, background run, status and staleness as for MaxDiff (app/maxdiff_hb.py, same files and settings).
"""
import hashlib
import json
import math
import os
from datetime import datetime

import numpy as np

from app import hb, hb_convergence, maxdiff_hb
from app.lang import de_en
from app.units import fixed, number, page_lang
from app.choice_model import ChoiceSet, Coding, charts, once, price_unit, responses_for, undetermined
from app.maxdiff_hb import RLH_FACTOR, load, pool_table, rlh_chart, running, settings  # noqa: F401 (shared)
from app.simulator import without_sim

FORMAT = 7  # 2: price centred on the mean price shown; 3: proper prior on mu, importance from posterior means;
            # 4: thinned log-likelihood trace, split-R-hat and ESS (app/hb_convergence.py); 5: the sampler of the audit of
            # 2026-10-05 (W-2), kept draws per person for the simulator (W-1), convergence also of the shown means;
            # 6: dual response coded as one choice with "none" (audit 2026-10-06, W-1), results coded before are estimated anew
            # (audit 2026-10-10, W-1); 7: every monitored quantity needs finite convergence diagnostics
# the version of the coding of the choices (cbc_analysis.choice_sets, acbc_logit.choice_sets), part of the key: raise it
# with every change there, so that results estimated from the old coding are no longer current (audit 2026-10-10, W-1)
CODING = 2
# settings of the presentation only (the choices and their coding stay the same): not part of the key (S22)
DISPLAY_KEYS = ("display", "level_images", "stack_mobile", "scene", "auto_next", "shuffle_alts")
TYPES = ("conjoint", "acbc")


# --- input ------------------------------------------------------------------------------------------

def _collect(item, table):
    """(coding, [(pid, [ChoiceSet])], (lowest, highest price) or None, raw answers for the key)."""
    kind = item.kind
    responses = responses_for(item, table)
    if item.type == "acbc":
        from app import acbc_logit

        valid = []
        for r in responses:
            answer = (r.answers or {}).get(str(item.id))
            if isinstance(answer, dict) and answer.get("concepts") and kind.byo_profile(item, answer) and answer.get("s"):
                valid.append((r.pid, answer))
        sets, coding, _, prices = acbc_logit.choice_sets(item, [a for _, a in valid])
        pids = [pid for pid, _ in valid]
        raw = [[pid, answer] for pid, answer in valid]
        price_range = (min(prices), max(prices)) if coding.price is not None and prices else None
    else:
        from app import cbc_analysis

        sets, coding, _, _ = cbc_analysis.table_sets(item, table, responses)
        pids = [r.pid for r in responses]
        raw = [[r.pid, ((r.layout or {}).get("cj") or {}).get(str(item.id)),
                _without_holdouts((r.answers or {}).get(str(item.id)))]
               for r in responses]
        numbers = kind.level_numbers(item, kind.price_attribute(item)) if coding.price is not None else None
        price_range = (min(numbers), max(numbers)) if numbers else None
    by_person = {}
    for s in sets:
        by_person.setdefault(s.person, []).append(s)
    persons = [(pids[p], by_person[p]) for p in sorted(by_person)]
    used = {pid for pid, _ in persons}
    return coding, persons, price_range, [x for x in raw if x[0] in used]


def model_input(item, table, config=None):
    """Everything the estimation needs, as plain data, and the key of the included answers.
    None with fewer than hb.MIN_PERSONS persons with a choice."""
    if item.type == "conjoint" and not item.kind.design_current(item):
        return None
    # the part without the settings is built once per table and question (code review 2026-10-07, C8): the HB view,
    # the latent classes ({"lc": True}) and the export's check each asked for it with every person's choice sets
    base = once(table, ("hb_input", item.id), lambda: _base_input(item, table))
    if base is None:
        return None
    cfg = config or settings()
    digest = hashlib.sha1(_dumped(base["dumped"], cfg).encode()).hexdigest()
    return {**base["inp"], "key": digest, "settings": cfg}


def _dumped(parts, cfg=None):
    """json.dumps({**data, "settings": cfg}, sort_keys=True, default=str) (without cfg: of data alone), put together
    from the values dumped once (parts: {key: dumped value}), character for character the same."""
    parts = dict(parts)
    if cfg is not None:
        parts["settings"] = json.dumps(cfg, sort_keys=True, default=str)
    return "{" + ", ".join(f"{json.dumps(k)}: {parts[k]}" for k in sorted(parts)) + "}"


def _base_input(item, table):
    """{"inp": the input without key and settings, "dumped": the key's data as dumped values} or None."""
    coding, persons, price_range, raw = _collect(item, table)
    if len(persons) < hb.MIN_PERSONS or not coding.k:
        return None
    levels = item.kind.attributes(item, item.survey.default_language)
    data = {
        # the holdout tasks (app/holdouts.py) and the saved scenarios of the simulator never enter the estimation:
        # changing them leaves the result current
        "format": FORMAT, "coding": CODING, "type": item.type,
        "config": {k: v for k, v in without_sim(item.config).items() if k != "holdouts" and k not in DISPLAY_KEYS},
        "labels": [a["level_labels"] for a in levels] if coding.price is not None else None,
        "persons": raw}
    dumped = {k: json.dumps(v, sort_keys=True, default=str) for k, v in data.items()}
    data_key = hashlib.sha1(_dumped(dumped).encode()).hexdigest()  # as maxdiff_hb
    linear = [a for a in range(len(coding.levels)) if a not in coding.effects]
    return {"dumped": dumped, "inp": {
        "data_key": data_key, "type": item.type,
        "coding": {"levels": coding.levels, "price_scale": coding.price_scale, "asc": coding.asc is not None,
                   "linear": linear, "price_center": coding.price_center},
        "price_range": price_range,
        "persons": [{"pid": pid, "sets": [(s.X.tolist(), int(s.chosen)) for s in sets]} for pid, sets in persons]}}


def _without_holdouts(answer):
    return {k: v for k, v in answer.items() if not k.startswith(("h", "dh"))} if isinstance(answer, dict) else answer


def _coding(c):
    return Coding(c["levels"], c["price_scale"], asc=c["asc"], linear=tuple(c["linear"]),
                  price_center=c.get("price_center", 0.0))


def _prior(coding):
    """Prior covariance of the parameters (see the module text)."""
    P = np.eye(coding.k)
    for a in coding.effects:
        n, off = coding.levels[a], coding.offsets[a]
        P[off:off + n - 1, off:off + n - 1] = np.eye(n - 1) - 1.0 / n
    return P


def run(inp, progress=None):
    """Estimate from model_input(); returns the JSON-ready result (without pool and item)."""
    coding = _coding(inp["coding"])
    k = coding.k
    person_sets = [[ChoiceSet(X, chosen) for X, chosen in p["sets"]] for p in inp["persons"]]
    spans = []                                        # (attribute, first row of M, number of levels)
    rows = []
    for a in coding.effects:
        spans.append((a, len(rows), coding.levels[a]))
        rows += [coding.level_vector(a, lv) for lv in range(coding.levels[a])]
    M = np.array(rows)                                # part-worths = B @ M.T
    price_width = None
    if coding.price is not None and inp.get("price_range"):
        price_width = (inp["price_range"][1] - inp["price_range"][0]) / coding.price_scale

    def importance(B):                                # persons x (attributes [+ price])
        U = B @ M.T
        parts = [U[:, start:start + n].max(axis=1) - U[:, start:start + n].min(axis=1) for _, start, n in spans]
        if price_width is not None:
            parts.append(np.abs(B[:, coding.price]) * price_width)
        R = np.column_stack(parts)
        total = R.sum(axis=1, keepdims=True)
        return np.divide(R, total, out=np.zeros_like(R), where=total > 0)

    def summary(B):                                   # means over persons at one draw
        extra = ([B[:, coding.asc].mean()] if coding.asc is not None else []) \
            + ([B[:, coding.price].mean() / coding.price_scale] if coding.price is not None else [])
        return np.concatenate([(B @ M.T).mean(axis=0), extra])

    def spread(S):  # spread between persons in the scales the page shows (audit 2026-10-05, H-3)
        return [np.mean(np.diag(M @ S @ M.T)) if len(M) else np.nan,
                S[coding.asc, coding.asc] if coding.asc is not None else np.nan,
                S[coding.price, coding.price] / coding.price_scale ** 2 if coding.price is not None else np.nan]

    cfg = inp["settings"]
    r = hb.estimate(person_sets, k, burn=cfg["burn"], draws=cfg["draws"], thin=cfg["thin"], seed=cfg["seed"],
                    prior_cov=_prior(coding), summary=summary, progress=progress, spread=spread)
    beta = r["beta"]
    S = r["summaries"]
    # all parameters of mu and the means the page shows (part-worths, constant, price)
    diag = hb_convergence.diagnose(r["mu_trace"], r["ll_trace"], geweke_ok=r["converged"], shown=S)
    lo, hi = np.percentile(S, [2.5, 97.5], axis=0) if S is not None and len(S) else (None, None)
    t = len(M)
    # what the data leave open (as in choice_model.summarise): there the prior sets the values
    everything = [s for sets in person_sets for s in sets]
    separated = coding.separated(everything)
    free = undetermined(everything, k)
    loose = sorted({a for a, _ in separated} | {a for a in coding.effects
                                                if free[coding.offsets[a]:coding.offsets[a] + coding.levels[a] - 1].any()})

    def rnd(x, d=4):
        return None if x is None else np.round(np.asarray(x, dtype=float), d).tolist()

    def part(x, start, stop):
        return None if x is None else rnd(x[start:stop])

    pos = t
    out = {"format": FORMAT, "key": inp["key"], "data_key": inp.get("data_key"), "type": inp["type"], "settings": cfg,
           "coding": inp["coding"],
           "price_range": inp.get("price_range"), "has_price_importance": price_width is not None,
           "spans": [[a, start, n] for a, start, n in spans], "pids": [p["pid"] for p in inp["persons"]],
           # importance from each person's posterior mean part-worths: per draw, the noise of a single draw widens
           # every range, which lifts weak attributes and those with many levels (no interval: none that covers)
           "util": rnd(beta @ M.T), "importance": rnd(importance(beta)),
           "util_lo": part(lo, 0, t), "util_hi": part(hi, 0, t), "imp_lo": None, "imp_hi": None,
           "separated": [[int(a), int(lv)] for a, lv in separated], "loose": [int(a) for a in loose],
           "constant_side": coding.constant_side(everything),
           "asc": None, "asc_lo": None, "asc_hi": None, "price": None, "price_lo": None, "price_hi": None,
           "rlh": rnd(r["rlh"]), "chance": rnd(r["chance_rlh"]),
           "mu": rnd(r["mu"]), "sigma_diag": rnd(r["sigma_diag"]), "acceptance": r["acceptance"],
           # posterior means of the variance between persons: of the zero-centred part-worths (mean over the levels), of
           # the constant and of the price coefficient per currency unit (squared), each in its own scale (H-3)
           "sigma_util": _finite(r["spread"][0], 4), "sigma_asc": _finite(r["spread"][1], 4),
           "sigma_price": _finite(r["spread"][2], None),
           "converged": diag["diag_ok"], "geweke_ok": r["converged"], "checked": r["checked"],
           "geweke_ll": r["geweke_ll"], "geweke_mu_flagged": r["geweke_mu_flagged"], "ll_halves": r["ll_halves"],
           "kept": r["kept"], "sets": r["sets"], "seconds": round(r["seconds"], 1), **diag,
           # a sample of the kept draws of every person's betas: the simulator and the holdouts average shares over
           # them instead of computing them from the shrunken posterior means (audit 2026-10-05, W-1)
           "draws": hb.pack_draws(r["draws"])}
    if coding.asc is not None:
        out.update(asc=rnd(beta[:, coding.asc]), asc_lo=None if lo is None else round(float(lo[pos]), 4),
                   asc_hi=None if hi is None else round(float(hi[pos]), 4))
        pos += 1
    if coding.price is not None:
        # significant digits, not fixed decimals: for prices in the ten thousands a coefficient is about 1e-5
        out.update(price=[float(f"{x:.6g}") for x in beta[:, coding.price] / coding.price_scale],
                   price_lo=None if lo is None else float(f"{lo[pos]:.6g}"),
                   price_hi=None if hi is None else float(f"{hi[pos]:.6g}"))
    return out


def _finite(x, digits):
    """A float for the stored result (rounded to digits, or to 6 significant digits for None), None for nan."""
    x = float(x)
    if not np.isfinite(x):
        return None
    return round(x, digits) if digits is not None else float(f"{x:.6g}")


def draw_parameters(result):
    """Per kept draw and person, as the market simulator needs them: {"util": draws x persons x levels (zero-centred
    part-worths in the order of result["util"]), "price": draws x persons (per currency unit) or None, "asc": draws x
    persons or None}. Without stored draws (never for a result of FORMAT 5 or later) one "draw": the posterior means."""
    B = hb.unpack_draws(result.get("draws"))
    if B is None:
        util = np.array(result["util"], dtype=float)[None]
        price = None if result.get("price") is None else np.array(result["price"], dtype=float)[None]
        asc = None if result.get("asc") is None else np.array(result["asc"], dtype=float)[None]
        return {"util": util, "price": price, "asc": asc}
    coding = _coding(result["coding"])
    M = np.array([coding.level_vector(a, lv) for a, _, n in result["spans"] for lv in range(n)])
    return {"util": B @ M.T if len(M) else np.zeros(B.shape[:2] + (0,)),
            "price": None if coding.price is None else B[:, :, coding.price] / coding.price_scale,
            "asc": None if coding.asc is None else B[:, :, coding.asc]}


# --- start, current result ------------------------------------------------------------------------------

def start(item, table, pool, background=True, config=None):
    """As maxdiff_hb.start: "started", "running", "current" or "nodata"."""
    from flask import current_app

    from app import jobs

    app = current_app._get_current_object()
    inp = model_input(item, table, config)
    if inp is None:
        return "nodata"
    stored = load(item)
    if stored and stored.get("key") == inp["key"]:
        maxdiff_hb.restamp(maxdiff_hb._paths(item)[0], stored, pool)
        return "current"
    if not jobs.start(app, "conjoint_hb", maxdiff_hb._paths(item), inp, pool, background, name=f"hb-item-{item.id}"):
        return "running"
    return "started"


def current(item):
    """The stored result if it still fits the data of its own data pool, else None (cached per request)."""
    from flask import g, has_request_context

    path = maxdiff_hb._paths(item)[0]
    try:
        stamp = os.path.getmtime(path)
    except OSError:
        return None
    cache = g.setdefault("_hb_current", {}) if has_request_context() else {}
    if (item.id, stamp) in cache:
        return cache[(item.id, stamp)]
    result = maxdiff_hb._read(path)
    if result and not (result.get("format") == FORMAT and result.get("type") == item.type and maxdiff_hb.fits(
            item, path, result, lambda table: model_input(item, table, result.get("settings")))):
        result = None
    cache[(item.id, stamp)] = result
    return result


# --- export ---------------------------------------------------------------------------------------------

def _asc_name(result):
    return "threshold" if result["type"] == "acbc" else "none"


def _level_columns(item, result):
    """[(column, attribute row, level index, position in util)] for all effects-coded levels."""
    attrs = item.kind.attributes(item)
    out = []
    for a, start, n in result["spans"]:
        out += [(f"{item.code}_hb_{attrs[a]['code']}_{lv + 1}", attrs[a], lv, start + lv) for lv in range(n)]
    return out


def export_columns(item, result):
    """[(column, label)]: part-worths per level, constant, price coefficient, importances, RLH."""
    code = item.code
    attrs = item.kind.attributes(item)
    cols = [(col, f"HB-Teilnutzen (nullzentriert je Merkmal): {attr['label']} = {attr['level_labels'][lv]}")
            for col, attr, lv, _ in _level_columns(item, result)]
    if result["asc"] is not None:
        cols.append((f"{code}_hb_{_asc_name(result)}", "HB-Nutzen der Schwelle „kommt nicht infrage“"
                     if result["type"] == "acbc" else "HB-Konstante „Keine davon“"))
    if result["price"] is not None:
        cols.append((f"{code}_hb_price", "HB-Preiskoeffizient je Währungseinheit"))
    cols += [(f"{code}_hb_imp_{attrs[a]['code']}", f"HB-Wichtigkeit: {attrs[a]['label']}") for a, _, _ in result["spans"]]
    if result.get("has_price_importance"):
        cols.append((f"{code}_hb_imp_price", "HB-Wichtigkeit: Preis"))
    cols.append((f"{code}_hb_rlh", "HB: Root Likelihood (Anpassungsgüte der Person)"))
    return cols


def export_values(item, result):
    """{pid: {column: value}} for the persons in the result."""
    code = item.code
    attrs = item.kind.attributes(item)
    levels = _level_columns(item, result)
    imp = importances(result)  # loose attributes without importance, as on the page (D4)
    out = {}
    for n, pid in enumerate(result["pids"]):
        row = {col: result["util"][n][pos] for col, _, _, pos in levels}
        if result["asc"] is not None:
            row[f"{code}_hb_{_asc_name(result)}"] = result["asc"][n]
        if result["price"] is not None:
            row[f"{code}_hb_price"] = result["price"][n]
        for j, (a, _, _) in enumerate(result["spans"]):
            row[f"{code}_hb_imp_{attrs[a]['code']}"] = None if np.isnan(imp[n][j]) else round(float(imp[n][j]), 4)
        if result.get("has_price_importance"):
            row[f"{code}_hb_imp_price"] = round(float(imp[n][len(result["spans"])]), 4)
        row[f"{code}_hb_rlh"] = result["rlh"][n]
        out[pid] = row
    return out


def describe(result, survey=None, lang=None):
    """One line for the codebook: method, date, data pool (with the speeder rule) and settings, in the reader's
    language `lang` (None: the page's; app.items.base.reader)."""
    from app.items.base import reader
    from app.timeutil import to_local

    s = result["settings"]
    when = datetime.fromisoformat(result["created"])
    basis = maxdiff_hb.pool_text(result.get("pool") or {}, survey, lang)
    if reader(lang) == "en":
        coding = ("screening against the threshold, tournament tasks" if result["type"] == "acbc" else "choice tasks")
        return (f"Hierarchical Bayes (HB-MNL, {coding}, effects coding), estimated on {to_local(when):%Y-%m-%d %H:%M}; "
                f"{len(result['pids'])} people ({basis}); {s['burn']} burn-in, {s['draws']} iterations, "
                f"thinning {s['thin']}, seed {s['seed']}; posterior mean per person{hb_convergence.describe(result, 'en')}")
    coding = ("Screening gegen die Schwelle, Turnieraufgaben" if result["type"] == "acbc" else "Auswahlaufgaben")
    return (f"Hierarchical Bayes (HB-MNL, {coding}, Effektkodierung), geschätzt am {to_local(when):%d.%m.%Y %H:%M}; "
            f"{len(result['pids'])} Personen ({basis}); {s['burn']} Burn-in, {s['draws']} Iterationen, "
            f"Thinning {s['thin']}, Seed {s['seed']}; Posterior-Mittel je Person{hb_convergence.describe(result, 'de')}")


def codebook_info(item, column, result, lang=None):
    from app.items.base import in_lang, reader

    code = item.code
    note = describe(result, item.survey, lang)
    if column == f"{code}_hb_rlh":
        chance = fixed(float(np.mean(result['chance'])), 2)
        return "Zahl", in_lang(lang, "0 bis 1", "0 to 1"), [note, (
            f"geometric mean of the probabilities of the alternatives chosen; chance about {chance}" if reader(lang) == "en"
            else "geometrisches Mittel der Wahrscheinlichkeiten der gewählten Alternativen; "
                 f"Zufall etwa {chance}".replace(".", ","))]
    if column.startswith(f"{code}_hb_imp_"):
        return "Zahl", in_lang(lang, "0 bis 1, Summe 1 über die Merkmale", "0 to 1, sum 1 over the attributes"), [
            note, in_lang(lang, "Spannweite der Teilnutzen des Merkmals als Anteil an der Summe aller Spannweiten",
                          "range of the part-worths of the attribute as a share of the sum of all ranges")
                  + (in_lang(lang, "; Preis: |Koeffizient| × Preisspanne", "; price: |coefficient| × price range")
                     if result.get("has_price_importance") else "")
                  # H-8: the ACBC price range is that of all persons' concepts, not of the concepts one person saw
                  + (in_lang(lang, "; die Preisspanne ist die aller gezeigten Konzepte aller Personen (bei ACBC sah eine "
                                   "Person oft nur einen Teil davon)",
                             "; the price range is that of all concepts shown to all people (with ACBC a person often "
                             "saw only part of it)") if result.get("has_price_importance") and result["type"] == "acbc" else "")]
    if column == f"{code}_hb_price":
        return "Zahl", in_lang(lang, "Logit je Währungseinheit", "logit per currency unit"), [note]
    if column in (f"{code}_hb_none", f"{code}_hb_threshold"):
        # CBC centres the price on the mean of its levels (cbc_analysis.choice_sets, C-3), ACBC on the mean price of
        # the concepts shown (audit 2026-10-12, W-4)
        centre = (in_lang(lang, "bezogen auf ein Angebot zum mittleren Preis der Preisstufen",
                          "relative to an offer at the mean price of the price levels") if result.get("type") != "acbc" else
                  in_lang(lang, "bezogen auf ein Angebot zum mittleren gezeigten Preis", "relative to an offer at the mean price shown"))
        return "Zahl", "Logit", [note] + ([centre] if result["price"] is not None else [])
    return "Zahl", in_lang(lang, "Logit, Mittel der Stufen des Merkmals = 0", "logit, mean of the levels of the attribute = 0"), [note]


# --- results page --------------------------------------------------------------------------------------

def view(item, table, lang):
    """Status and, with a stored result, the view model for the results page."""
    stored = _stored(item)
    inp = model_input(item, table, stored.get("settings") if stored else None)
    out = {"available": inp is not None, "marker": running(item), "result": None, "state": None, "stale_created": None,
           "min_persons": hb.MIN_PERSONS, "input_key": inp["key"] if inp else None,
           "data_key": inp["data_key"] if inp else None, "seen_key": stored.get("key") if stored else None}
    return _state(item, out, stored, lang)


def revisit(item, before, lang):
    """As maxdiff_hb.revisit: the view anew from the stored estimate and the marker, the answers as remembered."""
    stored = _stored(item)
    out = {**before, "marker": running(item), "result": None, "state": None, "stale_created": None,
           "unknown": bool(stored and stored.get("key") not in (before.get("seen_key"), before.get("input_key")))}
    if out["unknown"] and before.get("data_key") and stored.get("data_key") == before["data_key"]:
        # the same answers, only other settings (a longer run): current without reading the answers again
        out["unknown"], out["input_key"] = False, stored["key"]
    return _state(item, out, stored, lang)


def _stored(item):
    stored = load(item)
    return None if stored and (stored.get("format") != FORMAT or stored.get("type") != item.type) else stored


def _state(item, out, stored, lang):
    if stored:
        out["state"] = "current" if out.get("input_key") and stored["key"] == out["input_key"] else "stale"
        if out["state"] == "current":
            out["result"] = summary(item, stored, lang)
        else:  # an estimate for other data is not shown: after "Neu berechnen" the page starts afresh
            out["stale_created"] = maxdiff_hb.created_local(stored)
    marker = out["marker"]
    if marker and marker["state"] == "running":
        out["state"] = "running"
    return out


def _stats(values):
    q = np.percentile(values, [25, 50, 75])
    return {"mean": float(values.mean()), "sd": float(values.std()), "q1": float(q[0]), "median": float(q[1]),
            "q3": float(q[2])}


def importances(res):
    """persons x (attributes [+ price]) of a stored result as shown: as in the aggregate model
    (choice_model.summarise) a loose attribute, whose values only the prior sets, gets no importance (nan), and the
    others are shares among themselves, since a share of a sum that holds its prior-set range would move all of
    them (audit data storage and calculations 2026-10-21, D4). Also for results stored before."""
    imp = np.array(res["importance"], dtype=float)
    loose = set(res.get("loose") or [])
    keep = np.array([a not in loose for a, _, _ in res["spans"]] + ([True] if res.get("has_price_importance") else []))
    if not len(imp) or keep.all():
        return imp
    out = np.where(keep, imp, 0.0)
    total = out.sum(axis=1, keepdims=True)
    out = np.divide(out, total, out=np.zeros_like(out), where=total > 0)
    out[:, ~keep] = np.nan
    return out


def summary(item, res, lang):
    """Rows, charts and model info of a stored result."""
    from app.timeutil import to_local

    attrs = item.kind.attributes(item, lang)
    util = np.array(res["util"], dtype=float)                 # persons x levels
    imp = importances(res)                                    # persons x (attributes [+ price]), loose ones nan
    n = len(util)
    rows = []
    separated = {(a, lv) for a, lv in res.get("separated") or []}
    loose = set(res.get("loose") or [])
    for j, (a, start, count) in enumerate(res["spans"]):
        block = util[:, start:start + count]
        best = block.argmax(axis=1)
        levels = []
        for lv in range(count):
            # no interval where the prior, not the data, sets the level of the values
            lo = None if res["util_lo"] is None or a in loose else res["util_lo"][start + lv]
            hi = None if res["util_hi"] is None or a in loose else res["util_hi"][start + lv]
            levels.append({"label": attrs[a]["level_labels"][lv], **_stats(block[:, lv]), "lo": lo, "hi": hi,
                           "separated": (a, lv) in separated,
                           "beta": float(block[:, lv].mean()),
                           # the bars reuse choice_model.charts(): whisker = the posterior interval of the mean
                           "se": None if lo is None else (hi - lo) / 3.92,
                           "best": float((best == lv).mean())})
        rows.append({"code": attrs[a]["code"], "label": attrs[a]["label"], "ordered": attrs[a].get("ordered"),
                     "levels": levels, "loose": a in loose,
                     "importance": None if a in loose else float(imp[:, j].mean()),
                     "importance_sd": None if a in loose else float(imp[:, j].std()),
                     "imp_lo": None if res["imp_lo"] is None else res["imp_lo"][j],
                     "imp_hi": None if res["imp_hi"] is None else res["imp_hi"][j]})
    price = None
    pa = None
    if res["price"] is not None:
        p = np.array(res["price"], dtype=float)
        j = len(res["spans"])
        pa = item.kind.price_attribute(item) if item.type == "conjoint" else None
        price = {**_stats(p), "lo": res["price_lo"], "hi": res["price_hi"], "negative": float((p < 0).mean()),
                 "unit": price_unit(res.get("price_range") or []),
                 "label": attrs[pa]["label"] if pa is not None else None,
                 "importance": float(imp[:, j].mean()) if res.get("has_price_importance") else None,
                 "importance_sd": float(imp[:, j].std()) if res.get("has_price_importance") else None,
                 "imp_lo": res["imp_lo"][j] if res.get("has_price_importance") and res["imp_lo"] else None,
                 "imp_hi": res["imp_hi"][j] if res.get("has_price_importance") and res["imp_hi"] else None}
    asc = None
    if res["asc"] is not None:
        asc = {**_stats(np.array(res["asc"], dtype=float)), "lo": res["asc_lo"], "hi": res["asc_hi"]}
    rlh = np.array(res["rlh"], dtype=float)
    chance = np.array(res["chance"], dtype=float)
    low = rlh < RLH_FACTOR * chance
    warnings = []
    if hb_convergence.warning(res):
        warnings.append(hb_convergence.warning(res))
    if n < 30:
        warnings.append(de_en("Nur {n} Personen: Die obere Ebene (Mittelwerte, Streuung) ist unsicher.",
                              "Only {n} people: the upper level (means, spread) is uncertain.", n=n))
    acc = res["acceptance"]
    if hb.acceptance_warning(acc):
        warnings.append(hb.acceptance_warning(acc))
    if res["type"] == "conjoint" and n:
        from app.items.conjoint import MIN_TASKS

        per_person = res["sets"] / n
        if per_person < MIN_TASKS:  # H-11
            warnings.append(de_en("Im Mittel nur {k} Auswahlaufgaben je Person: Die individuellen Werte bleiben nah am "
                                  "Mittel aller Personen, Anteile und Intervalle je Person sind unsicher (üblich sind "
                                  "mindestens {m}).",
                                  "On average only {k} choice tasks per person: the individual values stay close to "
                                  "the mean of all people; shares and intervals per person are uncertain (usual is at "
                                  "least {m}).", k=number(per_person, 1, page_lang()), m=MIN_TASKS))  # Z-7
    if separated:
        names = "; ".join(f"{attrs[a]['label']}: {attrs[a]['level_labels'][lv]}" for a, lv in sorted(separated))
        warnings.append(de_en("Nie oder immer gewählt, wo es einen Unterschied machte ({names}): Den Teilnutzen dieser "
                              "Stufe begrenzt nur die Priori, die Daten setzen keine Grenze. Die Werte dieses Merkmals "
                              "nur als Richtung lesen; sie stehen ohne Intervall.",
                              "Never or always chosen where it made a difference ({names}): only the prior bounds the "
                              "part-worth of this level; the data set no limit. Read the values of this attribute only as "
                              "a direction; they are shown without an interval.", names=names))
    if loose:  # as in the aggregate model (choice_model.summarise; D4)
        warnings.append(de_en("Ohne Wichtigkeit: {names}. Ihre Spannweite legen nicht die Daten fest, ein Anteil daran "
                              "würde auch die Wichtigkeit aller anderen Merkmale verschieben. Die übrigen Wichtigkeiten "
                              "sind deshalb Anteile nur unter den übrigen Merkmalen.",
                              "Without importance: {names}. Their range is not set by the data; a share of it would "
                              "also shift the importance of all other attributes. The other importances are therefore "
                              "shares among the other attributes only.",
                              names=", ".join(attrs[a]["label"] for a in sorted(loose))))
    tied = sorted(loose - {a for a, _ in separated})
    if tied:
        warnings.append(de_en("Nicht bestimmbar ({names}): Stufen wurden nie gezeigt oder traten nur in festen "
                              "Kombinationen auf. Die Werte dieser Merkmale kommen aus der Priori.",
                              "Not determinable ({names}): levels were never shown or only appeared in fixed "
                              "combinations. The values of these attributes come from the prior.",
                              names=", ".join(attrs[a]['label'] for a in tied)))
    if res.get("constant_side") and asc is not None:
        name = (de_en("Die Schwelle „kommt nicht infrage“", "The threshold “out of the question”") if res["type"] == "acbc"
                else de_en("„Keine davon“", "“None of these”"))
        if res['constant_side'] == 'never':
            warnings.append(de_en("{name} wurde nie gewählt: Die Konstante begrenzt nur die Priori.",
                                  "{name} was never chosen: only the prior bounds the constant.", name=name))
        else:
            warnings.append(de_en("{name} wurde immer gewählt: Die Konstante begrenzt nur die Priori.",
                                  "{name} was always chosen: only the prior bounds the constant.", name=name))
        asc["lo"] = asc["hi"] = None
    # the linear price as bars like in the aggregate model (cbc_analysis.estimate): mean coefficient x (price - mean
    # price of the levels), whisker from the posterior interval of the mean (Ralf, 2026-10-22: HB showed one
    # attribute fewer than the logit)
    price_levels = None
    if price is not None and item.type == "conjoint" and pa is not None:
        numbers = item.kind.level_numbers(item, pa)
        if numbers:
            centre = sum(numbers) / len(numbers)
            se = None if price["lo"] is None else (price["hi"] - price["lo"]) / 3.92
            price_levels = {"label": attrs[pa]["label"], "levels": [
                {"label": label, "beta": price["mean"] * (p - centre), "se": None if se is None else abs(p - centre) * se}
                for label, p in zip(attrs[pa]["level_labels"], numbers)]}
    return {"persons": n, "type": res["type"], "attributes": rows, "price": price, "asc": asc,
            "price_levels": price_levels,
            "charts": charts({"attributes": rows, "price_levels": price_levels}),
            "rlh": {"mean": float(rlh.mean()), "median": float(np.median(rlh)), "chance": float(chance.mean()),
                    "low": int(low.sum()), "low_share": float(low.mean()),
                    "chart": rlh_chart(rlh, float(RLH_FACTOR * chance.mean()), float(chance.mean())),
                    "limit": float(RLH_FACTOR * chance.mean())},
            "settings": res["settings"], "acceptance": acc, "target": hb.TARGET, "converged": res["converged"],
            "geweke_ll": res["geweke_ll"], "geweke_mu_flagged": res["geweke_mu_flagged"],
            "ll_halves": res["ll_halves"], "kept": res["kept"], "sets": res["sets"], "seconds": res["seconds"],
            "created": to_local(datetime.fromisoformat(res["created"])), "pool": res.get("pool") or {},
            "warnings": warnings, "rlh_factor": RLH_FACTOR,
            # spread between persons (H-3): None for an estimate from before, whose stored diagonal mixes the scales of
            # effects-coded parameters, the constant and the standardised price
            "sigma_mean": res.get("sigma_util"), "sigma_asc": res.get("sigma_asc"),
            "sigma_price_sd": None if res.get("sigma_price") is None else math.sqrt(res["sigma_price"]),
            "convergence": hb_convergence.view(res), "convergence_warning": hb_convergence.warning(res)}
