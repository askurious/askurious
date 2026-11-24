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

Interactions taken into the model (item.config["hb_interactions"], app/conjoint_interactions.py, 2026-11-24): every
pair adds the products of the two attributes' columns as parameters common to all persons (app/hb.py `fixed`, prior
conjoint_interactions.prior_block). The result then carries "interactions" (per pair the mean and 95 % interval of every
combination of levels and of the parameters) and "ww_draws" (the kept draws of the fixed parameters, aligned with
"draws"); the importances stay those of the main effects. Without interactions the input, its key and the result are
those of before.

Storage, background run, status and staleness as for MaxDiff (app/maxdiff_hb.py, same files and settings).
"""
import functools
import hashlib
import json
import math
import os
from datetime import datetime

import numpy as np

from app import hb, hb_chains, hb_convergence, maxdiff_hb
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
    cfg = maxdiff_hb.with_prior(config or settings(), item)
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
    from app import conjoint_interactions as ww

    data = {
        # the holdout tasks (app/holdouts.py) and the saved scenarios of the simulator never enter the estimation:
        # changing them leaves the result current
        "format": FORMAT, "coding": CODING, "type": item.type,
        "config": {k: v for k, v in without_sim(item.config).items()
                   if k != "holdouts" and k not in DISPLAY_KEYS and k != ww.CONFIG_KEY},
        "labels": [a["level_labels"] for a in levels] if coding.price is not None else None,
        "persons": raw}
    # the interactions taken into the model (only those the coding can carry), only when there are any: the key of a
    # model without them stays the one of before
    pairs = [p for p in ww.adopted_indices(item) if p in ww.pairs_of(coding, item.type == "conjoint")]
    dumped = {k: json.dumps(v, sort_keys=True, default=str) for k, v in data.items()}
    ww_data_key = hashlib.sha1(_dumped(dumped).encode()).hexdigest()
    if pairs:
        dumped["interactions"] = json.dumps([list(p) for p in pairs])
    data_key = hashlib.sha1(_dumped(dumped).encode()).hexdigest()  # as maxdiff_hb
    linear = [a for a in range(len(coding.levels)) if a not in coding.effects]
    inp = {"data_key": data_key, "ww_data_key": ww_data_key, "type": item.type,
           "coding": {"levels": coding.levels, "price_scale": coding.price_scale, "asc": coding.asc is not None,
                      "linear": linear, "price_center": coding.price_center},
           "price_range": price_range,
           "persons": [{"pid": pid, "sets": [(s.X.tolist(), int(s.chosen)) for s in sets]} for pid, sets in persons]}
    if pairs:
        pa = item.kind.price_attribute(item) if item.type == "conjoint" else None
        inp["interactions"] = [list(p) for p in pairs]
        inp["ww_price_levels"] = item.kind.level_numbers(item, pa) if pa is not None and coding.price is not None else None
    return {"dumped": dumped, "inp": inp}


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


def _prior_groups(coding):
    """The groups of the hierarchical prior along the blocks of _prior: one per effects-coded attribute (its levels
    keep one common spread), every other parameter (constant, price, linear attributes) alone."""
    groups, inside = [], set()
    for a in coding.effects:
        n, off = coding.levels[a], coding.offsets[a]
        groups.append(np.arange(off, off + n - 1))
        inside.update(range(off, off + n - 1))
    return groups + [np.array([j]) for j in range(coding.k) if j not in inside]


def _summary(B, M, asc, price, price_scale):
    """The means over persons at the betas B: part-worths, constant, price coefficient per currency unit."""
    extra = ([B[:, asc].mean()] if asc is not None else []) \
        + ([B[:, price].mean() / price_scale] if price is not None else [])
    return np.concatenate([(B @ M.T).mean(axis=0), extra])


def _spread(S, M, asc, price, price_scale):
    """The variances between persons for the covariance S: of the zero-centred part-worths (mean over the levels), of
    the constant and of the price coefficient per currency unit."""
    return [np.mean(np.diag(M @ S @ M.T)) if len(M) else np.nan,
            S[asc, asc] if asc is not None else np.nan,
            S[price, price] / price_scale ** 2 if price is not None else np.nan]


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

    # means over persons at one draw and the spread between persons in the scales the page shows (audit 2026-10-05,
    # H-3); module-level functions, so they travel to the chain processes
    parts = {"M": M, "asc": coding.asc, "price": coding.price, "price_scale": coding.price_scale}
    summary = functools.partial(_summary, **parts)
    spread = functools.partial(_spread, **parts)

    cfg = inp["settings"]
    pairs = [tuple(p) for p in inp.get("interactions") or []]
    main_sets = person_sets
    extra = {}
    if pairs:  # the interactions taken over: parameters common to all persons after the k of the main effects
        from app import conjoint_interactions as ww

        person_sets = [ww.with_pairs(sets, coding, pairs) for sets in person_sets]
        blocks = [ww.prior_block(coding, a, b) for a, b in pairs]
        m = sum(len(B) for B in blocks)
        prior_fixed = np.zeros((m, m))
        at = 0
        for B in blocks:
            prior_fixed[at:at + len(B), at:at + len(B)] = B
            at += len(B)
        extra = {"fixed": list(range(k, k + m)), "fixed_prior": prior_fixed}
    r = hb_chains.estimate(person_sets, k + (len(extra["fixed"]) if pairs else 0), chains=hb_chains.chains_of(cfg),
                           burn=cfg["burn"], draws=cfg["draws"], thin=cfg["thin"], seed=cfg["seed"],
                           prior_cov=_prior(coding), summary=summary, progress=progress, spread=spread,
                           **maxdiff_hb.prior_args(cfg, _prior_groups(coding)), **extra)
    beta = r["beta"]
    S = r["summaries"]
    # all parameters of mu (and the fixed interactions) and the means the page shows (part-worths, constant, price)
    diag = hb_convergence.diagnose(np.hstack([r["mu_trace"], r["fixed_trace"]]) if pairs else r["mu_trace"],
                                   r["ll_trace"], geweke_ok=r["converged"], shown=S,
                                   sigma=r["sigma_trace"], person=r["person_rhat"], chains=r["chains"])
    lo, hi = np.percentile(S, [2.5, 97.5], axis=0) if S is not None and len(S) else (None, None)
    t = len(M)
    # what the data leave open (as in choice_model.summarise): there the prior sets the values
    everything = [s for sets in main_sets for s in sets]
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
           **{x: r[x] for x in hb.GEWEKE_STORED},
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
    if pairs:
        out["interactions"] = _interaction_summary(coding, pairs, r, inp.get("ww_price_levels"))
        out["ww_draws"] = hb.pack_draws(r["fixed_draws"])
        out["fixed_acceptance"] = r["fixed_acceptance"]
    return out


def _interaction_summary(coding, pairs, r, price_levels):
    """Per taken-over pair: its columns, the mean and 95 % interval of each parameter, and of every combination of
    levels (La x Lb; price: per price level) from all kept draws."""
    from app import conjoint_interactions as ww

    out, at = [], 0
    trace = np.asarray(r["fixed_trace"], dtype=float)
    for a, b in pairs:
        m = len(ww.attribute_columns(coding, a)) * len(ww.attribute_columns(coding, b))
        part = trace[:, at:at + m]
        C = ww.cell_matrix(coding, a, b, price_levels)
        La = len(ww.level_codes(coding, a, price_levels))
        Lb = len(ww.level_codes(coding, b, price_levels))
        row = {"a": a, "b": b, "start": at, "cols": m,
               "mean": np.round(r["fixed"][at:at + m], 4).tolist(),
               "lo": np.round(r["fixed_lo"][at:at + m], 4).tolist(), "hi": np.round(r["fixed_hi"][at:at + m], 4).tolist()}
        if len(C) and len(part):
            cells = part @ C.T                                           # draws x cells
            lo, hi = np.percentile(cells, [2.5, 97.5], axis=0)
            row.update(cells=np.round(cells.mean(axis=0), 4).reshape(La, Lb).tolist(),
                       cells_lo=np.round(lo, 4).reshape(La, Lb).tolist(), cells_hi=np.round(hi, 4).reshape(La, Lb).tolist())
        out.append(row)
        at += m
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
    out = {"util": B @ M.T if len(M) else np.zeros(B.shape[:2] + (0,)),
           "price": None if coding.price is None else B[:, :, coding.price] / coding.price_scale,
           "asc": None if coding.asc is None else B[:, :, coding.asc]}
    if result.get("interactions"):
        out["ww"] = interaction_terms(result, hb.unpack_draws(result.get("ww_draws")))
    return out


def interaction_terms(result, fixed_draws=None):
    """The taken-over interactions as the simulator adds them to a concept (choice_predict.interaction_utilities):
    two attributes with levels {"kind": "levels", "a", "b", "T": draws x La x Lb}; the linear price with an attribute
    {"kind": "price", "attr": the other attribute, "S": draws x its levels, per currency unit: the term is
    S x (price - centre)}. fixed_draws: draws x all fixed parameters (None: one "draw", the posterior means)."""
    from app import conjoint_interactions as ww

    coding = _coding(result["coding"])
    out = []
    for row in result.get("interactions") or []:
        a, b, start, m = row["a"], row["b"], row["start"], row["cols"]
        G = (np.asarray(fixed_draws, dtype=float)[:, start:start + m] if fixed_draws is not None
             else np.array(row["mean"], dtype=float)[None, :])
        if a in coding.offsets and b in coding.offsets:
            A, Bc = ww.level_codes(coding, a), ww.level_codes(coding, b)
            G3 = G.reshape(len(G), A.shape[1], Bc.shape[1])
            out.append({"kind": "levels", "a": a, "b": b, "T": np.einsum("ia,dab,jb->dij", A, G3, Bc)})
        else:
            other = b if a not in coding.offsets else a
            codes = ww.level_codes(coding, other)
            out.append({"kind": "price", "attr": other, "S": (G @ codes.T) / coding.price_scale})
    return out


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
    if scale_of(item) == "diffs":  # the part-worths rescaled per person as well, when the page shows them so
        cols += [(_norm(item, col), f"HB-Teilnutzen normiert (Spannweiten je Person, Summe 100 je Merkmal): "
                                    f"{attr['label']} = {attr['level_labels'][lv]}")
                 for col, attr, lv, _ in _level_columns(item, result)]
        if result["asc"] is not None:
            cols.append((f"{code}_hb_norm_{_asc_name(result)}", "HB-Nutzen der Schwelle „kommt nicht infrage“, normiert"
                         if result["type"] == "acbc" else "HB-Konstante „Keine davon“, normiert"))
        if result["price"] is not None:
            cols.append((f"{code}_hb_norm_price", "HB-Preiskoeffizient je Währungseinheit, normiert"))
    cols += [(f"{code}_hb_imp_{attrs[a]['code']}", f"HB-Wichtigkeit: {attrs[a]['label']}") for a, _, _ in result["spans"]]
    if result.get("has_price_importance"):
        cols.append((f"{code}_hb_imp_price", "HB-Wichtigkeit: Preis"))
    cols.append((f"{code}_hb_rlh", "HB: Root Likelihood (Anpassungsgüte der Person)"))
    cols += [(col, label) for col, label, _, _, _ in _interaction_columns(item, result)]
    return cols


def _interaction_columns(item, result):
    """[(column, label, row of result["interactions"], level of a, level of b)]: one column per combination of levels of
    every taken-over pair, <code>_hb_ww_<attribute a>_<attribute b>_<level a>_<level b> (the same value for everyone)."""
    attrs = item.kind.attributes(item)
    out = []
    for row in result.get("interactions") or []:
        if row["a"] >= len(attrs) or row["b"] >= len(attrs) or not row.get("cells"):
            continue
        A, B = attrs[row["a"]], attrs[row["b"]]
        for i, la in enumerate(A["level_labels"][:len(row["cells"])]):
            for j, lb in enumerate(B["level_labels"][:len(row["cells"][i])]):
                out.append((f"{item.code}_hb_ww_{A['code']}_{B['code']}_{i + 1}_{j + 1}",
                            f"HB-Wechselwirkung (für alle gleich): {A['label']} = {la} × {B['label']} = {lb}", row, i, j))
    return out


def _norm(item, column):
    """The column of the rescaled values of a part-worth column (<code>_hb_<attribute>_<level>)."""
    return f"{item.code}_hb_norm_" + column[len(f"{item.code}_hb_"):]


def export_values(item, result):
    """{pid: {column: value}} for the persons in the result."""
    code = item.code
    attrs = item.kind.attributes(item)
    levels = _level_columns(item, result)
    imp = importances(result)  # loose attributes without importance, as on the page (D4)
    factors = diff_factors(result) if scale_of(item) == "diffs" else None
    ww_columns = _interaction_columns(item, result)
    out = {}
    for n, pid in enumerate(result["pids"]):
        row = {col: result["util"][n][pos] for col, _, _, pos in levels}
        if factors is not None:
            f = float(factors[n]) if np.isfinite(factors[n]) else None  # a plain float: the CSV writes its repr

            def scaled(x, digits=2):
                return None if f is None or x is None else round(float(x) * f, digits)

            row.update({_norm(item, col): scaled(result["util"][n][pos]) for col, _, _, pos in levels})
            if result["asc"] is not None:
                row[f"{code}_hb_norm_{_asc_name(result)}"] = scaled(result["asc"][n])
            if result["price"] is not None:
                row[f"{code}_hb_norm_price"] = None if f is None else float(f"{result['price'][n] * f:.6g}")
        if result["asc"] is not None:
            row[f"{code}_hb_{_asc_name(result)}"] = result["asc"][n]
        if result["price"] is not None:
            row[f"{code}_hb_price"] = result["price"][n]
        for j, (a, _, _) in enumerate(result["spans"]):
            row[f"{code}_hb_imp_{attrs[a]['code']}"] = None if np.isnan(imp[n][j]) else round(float(imp[n][j]), 4)
        if result.get("has_price_importance"):
            row[f"{code}_hb_imp_price"] = round(float(imp[n][len(result["spans"])]), 4)
        row[f"{code}_hb_rlh"] = result["rlh"][n]
        for col, _, ww_row, i, j in ww_columns:
            row[col] = ww_row["cells"][i][j]
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
                f"{len(result['pids'])} people ({basis}); {hb_chains.length_text(s, 'en')}, "
                f"thinning {s['thin']}, seed {s['seed']}{maxdiff_hb.prior_text(s, 'en')}; posterior mean per person"
                f"{hb_convergence.describe(result, 'en')}")
    coding = ("Screening gegen die Schwelle, Turnieraufgaben" if result["type"] == "acbc" else "Auswahlaufgaben")
    return (f"Hierarchical Bayes (HB-MNL, {coding}, Effektkodierung), geschätzt am {to_local(when):%d.%m.%Y %H:%M}; "
            f"{len(result['pids'])} Personen ({basis}); {hb_chains.length_text(s, 'de')}, "
            f"Thinning {s['thin']}, Seed {s['seed']}{maxdiff_hb.prior_text(s, 'de')}; Posterior-Mittel je Person"
            f"{hb_convergence.describe(result, 'de')}")


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
    if column.startswith(f"{code}_hb_norm_"):
        return "Zahl", in_lang(lang, "normiert: Spannweiten je Person summieren sich zu 100 × Zahl der Merkmale",
                               "rescaled: the ranges per person sum to 100 × number of attributes"), [
            note, in_lang(lang, "Alle Nutzen einer Person mit demselben Faktor 100 × Zahl der Merkmale / Summe ihrer "
                                "Spannweiten multipliziert (Preis: |Koeffizient| × Preisspanne); leer, wo die Summe 0 ist",
                          "all utilities of a person multiplied by the same factor 100 × number of attributes / sum of "
                          "their ranges (price: |coefficient| × price range); empty where the sum is 0")]
    if column == f"{code}_hb_price":
        return "Zahl", in_lang(lang, "Logit je Währungseinheit", "logit per currency unit"), [note]
    if column.startswith(f"{code}_hb_ww_"):
        return "Zahl", in_lang(lang, "Logit, für alle Personen gleich", "logit, the same for everyone"), [
            note, in_lang(lang, "Wechselwirkung zweier Merkmale, als fester Parameter für alle Personen geschätzt "
                                "(Effektkodierung, Zeilen und Spalten summieren sich zu 0; beim linearen Preis: Wert "
                                "bei diesem Preis); sie kommt zu den Teilnutzen der beiden Stufen hinzu",
                          "interaction of two attributes, estimated as a fixed parameter for everyone (effects coding, "
                          "rows and columns sum to 0; with the linear price: value at this price); it adds to the "
                          "part-worths of the two levels")]
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


SCALE_KEY = "hb_scale"  # item.config: "diffs" shows the part-worths rescaled per person; absent: raw logit values (a
#                         display setting of the results page, simulator.RESULT_KEYS: the stored result stays as it is)
SCALES = ("raw", "diffs")


def scale_of(item):
    """How the results page shows the HB part-worths of the question: "raw" (logit scale, default) or "diffs"."""
    return "diffs" if (item.config or {}).get(SCALE_KEY) == "diffs" else "raw"


def choose_scale(item, value):
    """Store the display chosen on the results page; True if it changed (anything else than SCALES changes nothing)."""
    if value not in SCALES or value == scale_of(item):
        return False
    config = {k: v for k, v in (item.config or {}).items() if k != SCALE_KEY}
    item.config = {**config, SCALE_KEY: "diffs"} if value == "diffs" else config
    return True


def diff_factors(res):
    """Per person the factor that rescales all their utilities (part-worths, constant, price coefficient) so that the
    ranges of their A attributes sum to 100 A, the mean range 100: 100 A / sum of the ranges. The range of an
    effects-coded attribute is max - min of its zero-centred part-worths, that of a linear price |coefficient| x price
    range, as in the importances (so the importances stay the same). nan for a person whose ranges sum to 0. This is
    the usual "zero-centred diffs" scaling of HB part-worths: every person then weighs the same in a mean, whatever the
    scale (consistency) of their choices."""
    util = np.array(res["util"], dtype=float)
    n = len(res.get("pids") or util)
    ranges = [util[:, s:s + count].max(axis=1) - util[:, s:s + count].min(axis=1) for _, s, count in res["spans"]]
    if res.get("price") is not None and res.get("has_price_importance") and res.get("price_range"):
        ranges.append(np.abs(np.array(res["price"], dtype=float)) * (res["price_range"][1] - res["price_range"][0]))
    if not ranges:
        return np.full(n, np.nan)
    R = np.column_stack(ranges)
    total = R.sum(axis=1)
    return np.where(total > 0, 100 * R.shape[1] / np.where(total > 0, total, 1), np.nan)


def _diffs(item, res, rows, price, asc, pa, attrs):
    """The part-worths of the page rescaled per person (diff_factors), from the stored result: means, SD and quartiles
    over the persons' rescaled values, the share preferring a level as before, the 95 % interval of the mean from the
    kept draws with every person's fixed factor. Persons whose ranges sum to 0 are left out. None without anyone."""
    f = diff_factors(res)
    ok = np.isfinite(f)
    if not ok.any():
        return None
    fk = f[ok]
    util = np.array(res["util"], dtype=float)[ok] * fk[:, None]
    draws = draw_parameters(res) if res.get("draws") else None

    def interval(series, had):  # series: draws x persons kept, or None
        if series is None or not had:
            return None, None
        means = (series * fk[None, :]).mean(axis=1)
        lo, hi = np.percentile(means, [2.5, 97.5])
        return float(lo), float(hi)

    attributes = []
    for row, (a, start, count) in zip(rows, res["spans"]):
        levels = []
        for lv, old in enumerate(row["levels"]):
            column = util[:, start + lv]
            lo, hi = interval(None if draws is None else draws["util"][:, ok, start + lv], old["lo"] is not None)
            levels.append({**old, **_stats(column), "lo": lo, "hi": hi, "beta": float(column.mean()),
                           "se": None if lo is None else (hi - lo) / 3.92})
        attributes.append({**row, "levels": levels})
    out_price = out_asc = None
    if price is not None:
        p = np.array(res["price"], dtype=float)[ok] * fk
        lo, hi = interval(None if draws is None or draws["price"] is None else draws["price"][:, ok], price["lo"] is not None)
        out_price = {**price, **_stats(p), "lo": lo, "hi": hi}
    if asc is not None:
        c = np.array(res["asc"], dtype=float)[ok] * fk
        lo, hi = interval(None if draws is None or draws["asc"] is None else draws["asc"][:, ok], asc["lo"] is not None)
        out_asc = {**asc, **_stats(c), "lo": lo, "hi": hi}
    price_levels = _price_levels(item, attrs, out_price, pa)
    return {"attributes": attributes, "price": out_price, "asc": out_asc, "price_levels": price_levels,
            "charts": charts({"attributes": attributes, "price_levels": price_levels}),
            "persons": int(ok.sum()), "left_out": int((~ok).sum()), "intervals": draws is not None}


def _price_levels(item, attrs, price, pa):
    """The linear price as bars like in the aggregate model (cbc_analysis.estimate): mean coefficient x (price - mean
    price of the levels), whisker from the posterior interval of the mean (Ralf, 2026-10-22: HB showed one attribute
    fewer than the logit)."""
    if price is None or item.type != "conjoint" or pa is None:
        return None
    numbers = item.kind.level_numbers(item, pa)
    if not numbers:
        return None
    centre = sum(numbers) / len(numbers)
    se = None if price["lo"] is None else (price["hi"] - price["lo"]) / 3.92
    return {"label": attrs[pa]["label"], "levels": [
        {"label": label, "beta": price["mean"] * (p - centre), "se": None if se is None else abs(p - centre) * se}
        for label, p in zip(attrs[pa]["level_labels"], numbers)]}


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


def _interaction_rows(item, res, attrs, rows, price):
    """The taken-over interactions for the HB block: per pair the table of interaction values (mean and 95 % interval)
    and of the combination utilities (mean part-worths of both levels plus the interaction)."""
    out = []
    if not res.get("interactions"):
        return out
    by_attr = {a: [lv["mean"] for lv in row["levels"]] for row, (a, _, _) in zip(rows, res["spans"])}
    pa = item.kind.price_attribute(item) if item.type == "conjoint" else None
    numbers = item.kind.level_numbers(item, pa) if pa is not None else None
    centre = (res.get("coding") or {}).get("price_center") or 0.0
    if pa is not None and numbers and price is not None:
        by_attr[pa] = [price["mean"] * (x - centre) for x in numbers]
    for row in res["interactions"]:
        if row["a"] >= len(attrs) or row["b"] >= len(attrs) or not row.get("cells"):
            continue
        A, B = attrs[row["a"]], attrs[row["b"]]
        ua, ub = by_attr.get(row["a"]), by_attr.get(row["b"])
        cells = row["cells"]
        comb = ([[ua[i] + ub[j] + cells[i][j] for j in range(len(cells[i]))] for i in range(len(cells))]
                if ua is not None and ub is not None and len(ua) == len(cells) and len(ub) == len(cells[0]) else None)
        out.append({"a": row["a"], "b": row["b"], "label": f"{A['label']} × {B['label']}", "a_label": A["label"],
                    "b_label": B["label"], "a_levels": A["level_labels"][:len(cells)],
                    "b_levels": B["level_labels"][:len(cells[0])], "cells": cells, "lo": row["cells_lo"],
                    "hi": row["cells_hi"], "comb": comb})
    return out


def _quartiles(column):
    """[Q1, median, Q3] of the importances of the people (shares 0 … 1): the spread the figure draws, since importances
    are bounded and often skewed, where ± SD would run past 0 or 100 % (Ralf, 2026-10-26)."""
    column = np.asarray(column, dtype=float)
    column = column[np.isfinite(column)]
    return [float(q) for q in np.percentile(column, [25, 50, 75])] if len(column) else None


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
                     "importance_q": None if a in loose else _quartiles(imp[:, j]),
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
                 "importance_q": _quartiles(imp[:, j]) if res.get("has_price_importance") else None,
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
    price_levels = _price_levels(item, attrs, price, pa)
    return {"persons": n, "type": res["type"], "attributes": rows, "price": price, "asc": asc,
            "interactions": _interaction_rows(item, res, attrs, rows, price),
            "price_levels": price_levels,
            # the same part-worths rescaled per person (SCALE_KEY), for the switch above the table; importances,
            # simulator and holdouts keep the raw values
            "diffs": _diffs(item, res, rows, price, asc, pa, attrs),
            "charts": charts({"attributes": rows, "price_levels": price_levels}),
            "rlh": {"mean": float(rlh.mean()), "median": float(np.median(rlh)), "chance": float(chance.mean()),
                    "low": int(low.sum()), "low_share": float(low.mean()),
                    "chart": rlh_chart(rlh, float(RLH_FACTOR * chance.mean()), float(chance.mean())),
                    "limit": float(RLH_FACTOR * chance.mean())},
            "settings": res["settings"], "acceptance": acc, "target": hb.TARGET, "converged": res["converged"],
            "geweke_ll": res["geweke_ll"], "geweke_mu_flagged": res["geweke_mu_flagged"],
            **{x: res.get(x) for x in hb.GEWEKE_STORED},
            "ll_halves": res["ll_halves"], "kept": res["kept"], "sets": res["sets"], "seconds": res["seconds"],
            "created": to_local(datetime.fromisoformat(res["created"])), "pool": res.get("pool") or {},
            "warnings": warnings, "rlh_factor": RLH_FACTOR,
            # spread between persons (H-3): None for an estimate from before, whose stored diagonal mixes the scales of
            # effects-coded parameters, the constant and the standardised price
            "sigma_mean": res.get("sigma_util"), "sigma_asc": res.get("sigma_asc"),
            "sigma_price_sd": None if res.get("sigma_price") is None else math.sqrt(res["sigma_price"]),
            "convergence": hb_convergence.view(res), "convergence_warning": hb_convergence.warning(res)}
