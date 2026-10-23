"""Structural equation models of a survey (for everyone since 2026-10-11; concept strukturgleichungsmodelle-konzept.md,
Ralf 2026-10-06: all as recommended, PLS from the start): the model comes from the study plan, the numbers from
app/sem.py, the results page shows them in the block "Strukturgleichungsmodell" (surveys/_sem.html).

The model (E3): every hypothesis of the plan with the method "Strukturgleichungsmodell" is a path from what explains to
what is explained; a hypothesis of the kind "Vermittlung" is two paths over its mediator plus the direct path, and its
indirect effect. Since the drawing in the study plan (modell-zeichnen-konzept.md, F4, Ralf 2026-10-11) a hypothesis
of the kind "Einfluss" is a path and one of the kind "Zusammenhang" a covariance without a direction (between
residuals where one of the two is explained in the model); before that "Zusammenhang" was a path too. A hypothesis
of the kind "Moderation" (stage 2, F2) says that w changes the strength of the path x -> y: with a construct or a
number as w an interaction term x × w joins the model beside x -> y and w -> y (product indicators, double mean
centred matched pairs: Marsh et al., 2004; Lin et al., 2010; PLS in two stages), judged by its path; with two groups
as w (covariance-based) the model without w is estimated in both groups with equal loadings and the path x -> y
compared (Wald test of the difference). Festlegungen: x and w of an interaction are not explained by other paths,
an instrument with subscales cannot moderate or be moderated yet. What a hypothesis names enters as
  a construct       a Likert matrix or semantic differential with at least two scored statements, measured as
                    metric (app/scales.groups): its statements are the indicators. An instrument with subscales
                    enters with one construct per subscale; a hypothesis that names it gets one path per subscale.
  an observed one   a single question with numbers (metric or ordinal), or two groups coded 0/1 (a choice with two
                    options, a randomisation with two conditions: the first one 0).
Without such hypotheses the block shows the measurement model of all scales (confirmatory factor analysis, E1),
every construct correlated with every other: from two constructs, or one with at least three statements.

Data: the export rows of the results page (the data pool without what quality assurance marks), unweighted like
alpha and omega, complete cases over all columns of the model (E5). Festlegungen von Askurious (E7): estimated from
MIN_N complete cases; covariance-based also with at least PER_PARAMETER cases per free parameter, PLS with ten
times the largest number of paths pointing at one construct (Hair et al., 2022); below SMALL_N a hint. A statement
with fewer than MIN_POINTS answer points: a hint that ML treats it as continuous (Rhemtulla et al., 2012, E4).
Verdicts (as app/hypotheses.py): a direct path two-sided at .05 with the direction of the hypothesis; an indirect
effect by joint significance (MacKinnon et al., 2002): its p is the larger p of its two paths, with the direction of
the product (audit 2026-10-06, SEM-1, Ralf's decision 2); the bootstrap interval (Preacher & Hayes, 2008) is
reported beside it. Both p enter Holm's correction (hypotheses.with_holm). PLS paths only with the bootstrap. A model
that is not admissible (Heywood case, constructs correlated beyond 1, not identified) gives no verdict."""
import hashlib
import math
import json
import os
import threading

from app import brand
from app import sem
from app import sem_means
from app import sem_ordinal
from app import efa  # the exploratory factor analysis of the scales (results page), as S_EFA in its templates
from app.lang import de_en, english

MIN_N = 100
SMALL_N = 200
PER_PARAMETER = 5
PLS_TIMES = 10
MIN_POINTS = 5
BOOT_DRAWS = 2000
METHODS = {"cb": "kovarianzbasiert (Maximum Likelihood)", "pls": "varianzbasiert (PLS)"}
METHODS_EN = {"cb": "covariance-based (maximum likelihood)", "pls": "variance-based (PLS)"}


def methods():
    return METHODS_EN if english() else METHODS


DIRECTED = ("mediation", "effect", "moderation")  # kinds that only the model tests (study_plan.SEM_KINDS)
TIMES = "×"  # the name of an interaction term: "x×w", of a product indicator "a×b"
SINGLE = "°"  # the column of a single question that enters as a construct with a fixed reliability: "alter°"


def wanted(h):
    """Does this hypothesis of the plan belong to the model?"""
    return bool(h) and h.get("kind") not in ("open", "descriptive") and (h.get("analysis") == "sem" or h.get("kind") in DIRECTED)


def in_use(survey):
    from app import study_plan

    return survey is not None and bool(survey.study_plan) and any(
        wanted(h) for h in study_plan.plan_of(survey)["hypotheses"])


def method_of(survey):
    from app import study_plan

    return study_plan.plan_of(survey).get("sem_method") or "cb"


def _short(text, limit=60):
    text = " ".join((text or "").split())
    return (text[:limit - 2].rstrip() + " …") if len(text) > limit else text


def _question(item):
    from app.items.base import plain

    return _short(plain(item.text("question", item.survey.default_language) or ""))


def _statement_labels(item):
    labels = {}
    for code, label in item.kind.export_columns(item):
        labels[code] = _short(label.split(" [")[0], 70)
    return labels


def variable(survey, code):
    """How a variable of the plan enters the model: {"code", "label", "kind": "latent" | "observed", "parts":
    [(node name, label, [(column, statement text, answer points)])] (latent), "coding": None | (code of 0, code
    of 1), "groups" and "dummies" [(node, label, value)] (more than two groups: one 0/1 column per group against the
    first, stage 3)}; or {"code", "error": key} when it cannot."""
    from app import scales, study_plan
    from app.items.base import plain

    def many(values, shown, condition):
        """More than two groups (modell-zeichnen-konzept.md, F7 b): dummies against the first group."""
        names = [plain(shown.get(v, v)) for v in values]
        return {"code": code, "label": label, "kind": "observed", "groups": list(values), "group_labels": names,
                "dummies": [(f"{code}:{v}", f"{code} · {n}", v) for v, n in zip(values[1:], names[1:])],
                "coded": de_en("Gruppen gegen „{a}“", "groups against “{a}”", a=names[0]),
                **({"condition": True} if condition else {"item": thing})}

    thing = study_plan._item(survey, code)
    if thing is None:
        return {"code": code, "error": "missing"}
    if isinstance(thing, dict):  # a randomisation: its conditions
        children = thing.get("children") or []
        if len(children) < 2 or (thing.get("k") or 1) != 1:
            return {"code": code, "error": "groups"}
        from app import flow

        names = [flow.child_label(survey, c) for c in children]  # as the export row names the condition shown
        label = thing.get("name") or code
        if len(names) > 2:
            return many(names, {}, True)
        return {"code": code, "label": label, "kind": "observed", "coding": tuple(names), "condition": True,
                "coded": de_en("0 = „{a}“, 1 = „{b}“", "0 = “{a}”, 1 = “{b}”", a=names[0], b=names[1])}
    from app import flow

    if thing.block_id in flow.loop_blocks(survey):  # one answer per pass, no column of its own (audit 2026-10-12, SEM-1)
        return {"code": code, "error": "loop"}
    groups = scales.groups(thing)
    if groups:
        labels = _statement_labels(thing)
        parts, single = [], []
        for sub, codes in groups:
            name = f"{code}:{sub}" if sub else code
            title = f"{code} · {sub}" if sub else code
            if len(codes) < 2:  # a subscale of one statement is no construct (audit 2026-10-12, SEM-8)
                single.append(title)
                continue
            parts.append((name, title, [(c, labels.get(c, c), _points(thing, c)) for c in codes]))
        out = {"code": code, "label": _question(thing), "kind": "latent", "parts": parts, "item": thing}
        if single:  # a hypothesis cannot name it; the measurement model keeps its other subscales (measurement())
            out.update(error="single", single=single)
        return out
    level = study_plan._level(thing)
    label = _question(thing)
    if level in ("metric", "ordinal"):
        return {"code": code, "label": label, "kind": "observed", "coding": None, "item": thing}
    if level == "nominal":
        options = [o for o in (thing.config or {}).get("options") or [] if not o.get("missing")]
        shown = {}
        try:
            shown = {str(o["code"]): o["label"] for o in thing.kind.options(thing, thing.survey.default_language)}
        except (AttributeError, KeyError, TypeError):
            pass
        codes = tuple(str(o.get("code")) for o in options)
        if len(options) == 2:
            return {"code": code, "label": label, "kind": "observed", "coding": codes, "item": thing,
                    "coded": de_en("0 = „{a}“, 1 = „{b}“", "0 = “{a}”, 1 = “{b}”",
                                   a=plain(shown.get(codes[0], codes[0])), b=plain(shown.get(codes[1], codes[1])))}
        if len(options) > 2:
            return many(codes, shown, False)
        return {"code": code, "error": "groups"}
    return {"code": code, "error": "level"}


def _points(item, code):
    from app import scales

    found = scales.item_range(item, code)
    if not found:
        return None
    lo, hi = found
    return int(round(hi - lo)) + 1


def _nodes(info):
    """The node names of a variable: one per subscale of a construct (or its factor of the second order), one per
    group but the first of a variable with more than two groups (dummies, stage 3), else the code of an observed
    one."""
    if info.get("higher"):  # the instrument as one factor over its subscales (SEM extensions 2026-10-17, point 8)
        return [info["higher"]]
    if info["kind"] == "latent":
        return [name for name, _, _ in info["parts"]]
    if info.get("dummies"):
        return [name for name, _, _ in info["dummies"]]
    return [info["code"]]


def build(survey, every=False):
    """every: every hypothesis with a predictor and an outcome, not only those for the model (the dummy answers
    drawn from the plan, app/dummy_plan.py). The model of the survey: {"mode": "structural" | "measurement", "spec", "vars": {code: info}, "nodes":
    {node: (code, label)}, "hyps": {number: {"paths": [(a, b)], "indirect": [(x, m, y)], "errors": [...]}},
    "notes": [...]}, or None when there is nothing to estimate."""
    from app import study_plan

    plan = study_plan.plan_of(survey)
    hyps = [(n, h) for n, h in enumerate(plan["hypotheses"], 1)
            if wanted(h) or (every and not study_plan.untested(h) and h.get("iv") and h.get("dv"))]
    infos, nodes, out_hyps = {}, {}, {}
    latent, observed, paths, indirect, cov = {}, [], [], [], []
    higher = {}  # factors of the second order (point 8): {instrument: [its subscales]}
    second = set(plan["sem"]["second"]) if method_of(survey) != "pls" else set()
    products, groups, legs = {}, {}, {}
    # roles a variable with more than two groups cannot take (stage 3): what is explained, what mediates
    explained = {"mediation": (1, 2), "moderation": (2,), "correlation": ()}
    # the moderations last: whether x and w are explained by a path is known only once the other paths are there
    hyps = [p for p in hyps if p[1].get("kind") != "moderation"] + [p for p in hyps if p[1].get("kind") == "moderation"]
    by_groups = method_of(survey) != "pls"  # two groups as moderator: compared in two groups (covariance-based)

    def use(code):
        if code not in infos:
            infos[code] = variable(survey, code) if code else {"code": "", "error": "missing"}
            info = infos[code]
            if "error" not in info:
                if info["kind"] == "latent":
                    for name, title, items in info["parts"]:
                        latent[name] = [c for c, _, _ in items]
                        nodes[name] = (code, title)
                    if code in second and len(info["parts"]) >= 2:
                        info["higher"] = code
                        higher[code] = [name for name, _, _ in info["parts"]]
                        nodes[code] = (code, code)
                elif info.get("dummies"):  # more than two groups: one 0/1 column per group but the first (stage 3)
                    for name, title, _ in info["dummies"]:
                        observed.append(name)
                        nodes[name] = (code, title)
                else:
                    observed.append(code)
                    nodes[code] = (code, code)
        return infos[code]

    def link(a_nodes, b_nodes, into):
        for a in a_nodes:
            for b in b_nodes:
                if (a, b) not in paths:
                    paths.append((a, b))
                into.append((a, b))

    for n, h in hyps:
        entry = {"paths": [], "indirect": [], "cov": [], "errors": [], "kind": h.get("kind")}
        codes = ([h.get("iv"), h.get("mv"), h.get("dv")] if h.get("kind") == "mediation" else
                 [h.get("iv"), h.get("wv"), h.get("dv")] if h.get("kind") == "moderation" else [h.get("iv"), h.get("dv")])
        found = [use(c) for c in codes]
        bad = [f for f in found if "error" in f]
        if len(set(c for c in codes if c)) < len(codes) or not all(codes):
            entry["errors"].append("roles")
        elif bad:
            entry["errors"] += [(f["code"], f["error"]) for f in bad]
        elif [f for k, f in enumerate(found) if f.get("dummies") and k in explained.get(h.get("kind"), (1,))]:
            entry["errors"] += [(f["code"], "groups_role") for k, f in enumerate(found)
                                if f.get("dummies") and k in explained.get(h.get("kind"), (1,))]
        elif h.get("kind") == "mediation":
            x, m, y = (_nodes(f) for f in found)
            if len(x) == len(m) == len(y) == 1:  # its legs, for a moderated mediation (stage 3)
                legs[(h.get("iv"), h.get("mv"))] = {"stage": "a", "x": x[0], "m": m[0], "y": y[0], "number": n}
                legs[(h.get("mv"), h.get("dv"))] = {"stage": "b", "x": x[0], "m": m[0], "y": y[0], "number": n}
            link(x, m, entry["paths"])
            link(m, y, entry["paths"])
            link(x, y, [])  # the direct effect: in the model, reported beside the indirect one
            entry["direct"] = [(a, b) for a in x for b in y]
            for a in x:
                for b in m:
                    for c in y:
                        indirect.append((a, b, c))
                        entry["indirect"].append((a, b, c))
        elif h.get("kind") == "moderation" and any(f.get("higher") for f in found):
            entry["errors"].append("higher_moderation")
        elif h.get("kind") == "moderation":
            _moderation(n, entry, found, paths, latent, observed, nodes, products, groups, link, by_groups, legs)
        elif h.get("kind") == "correlation" and h.get("analysis") == "sem":  # without a direction (F4)
            for a in _nodes(found[0]):
                for b in _nodes(found[1]):
                    if (a, b) not in cov and (b, a) not in cov:
                        cov.append((a, b))
                    entry["cov"].append((a, b))
        else:
            link(_nodes(found[0]), _nodes(found[1]), entry["paths"])
        out_hyps[n] = entry
    # control variables (SEM extensions 2026-10-17, point 6): a path onto every variable the hypotheses explain,
    # without a hypothesis of their own and outside Holm's correction
    opts = study_plan.plan_of(survey)["sem"]
    controls, control_errors = {}, []
    explained_nodes = list(dict.fromkeys(b for _, b in paths))
    for code in opts["controls"] if explained_nodes else ():
        info = use(code)
        if "error" in info:
            control_errors.append((code, info["error"]))
            continue
        mine = _nodes(info)
        if set(mine) & set(explained_nodes):  # explained in the model itself: no control
            control_errors.append((code, "control_explained"))
            continue
        for c in mine:
            controls[c] = code
            for b in explained_nodes:
                if (c, b) not in paths and b not in products:
                    paths.append((c, b))
    # a relation without a direction between two that a path already links: the model cannot tell them apart
    clash = [(a, b) for a, b in cov if (a, b) in paths or (b, a) in paths]
    if clash:
        cov = [pair for pair in cov if pair not in clash]
        for entry in out_hyps.values():
            if any(pair in clash for pair in entry["cov"]):
                entry["errors"].append("both")
                entry["cov"] = []
    mode = "structural"
    if not paths and not cov and not groups:
        mode = "measurement"
        latent, observed, nodes = {}, [], {}
        skipped = []
        for block in survey.blocks:
            for item in block.items:
                info = variable(survey, item.code) if item.kind.result_kind == "scale" else None
                if info and info.get("error") == "single":  # the subscales of one statement stay out, with a note
                    skipped += info.pop("single")
                    info.pop("error")
                if info and info.get("kind") == "latent" and info["parts"]:
                    infos[item.code] = info
                    for name, title, items in info["parts"]:
                        latent[name] = [c for c, _, _ in items]
                        nodes[name] = (item.code, title)
        # one construct is identified from three statements; two-statement ones need a partner
        if not latent or (len(latent) == 1 and len(next(iter(latent.values()))) < 3):
            return None
    used = {a for p in paths + cov for a in p}
    if mode == "structural":
        # constructs only a failed hypothesis named stay out
        grouped = {v for g in groups.values() for v in g["x"] + g["y"]}
        higher = {h: parts for h, parts in higher.items() if h in used or h in grouped}
        lowers = {p for parts in higher.values() for p in parts}
        latent = {k: v for k, v in latent.items() if k in used or k in grouped or k in lowers}
        observed = [o for o in observed if o in used or o in grouped
                    or any(o == g["w"] or o in g["w_nodes"] for g in groups.values())]
        nodes = {k: v for k, v in nodes.items() if k in latent or k in observed or k in higher}
    spec = {"latent": latent, "observed": observed, "paths": paths, "indirect": list(dict.fromkeys(indirect))}
    if higher and mode == "structural":
        spec["higher"] = higher
    if cov:
        spec["cov"] = cov
    if products:
        spec["products"] = products
    modmed = [e["modmed"] for e in out_hyps.values() if e.get("modmed")]
    if modmed:
        spec["modmed"] = modmed
    single = {}
    if mode == "structural" and method_of(survey) != "pls":  # PLS has no measurement error to take out
        in_products = {v for part in products.values() for v in (part["x"], part["w"])}
        for code, rel in opts["single"].items():
            info = infos.get(code) or {}
            if info.get("kind") == "observed" and not info.get("coding") and not info.get("dummies")                     and code in observed and code not in in_products:
                single[code] = rel
    if method_of(survey) != "pls" and opts["ordinal"] and not products and mode == "structural":
        spec["ordinal"] = True  # ordered categories with WLSMV (point 3); complete cases, so no FIML beside it
    elif method_of(survey) != "pls" and opts["missing"] == "fiml" and not products and mode == "structural":
        spec["missing"] = "fiml"  # cases with gaps by full information ML (point 2); interactions need complete cases
    if method_of(survey) == "pls":  # scales whose statements form the construct (mode B, point 9)
        formative = [n for code in opts["formative"] if (infos.get(code) or {}).get("kind") == "latent"
                     for n in _nodes(infos[code]) if n in latent]
        if formative:
            spec["formative"] = formative
    if mode == "measurement":
        try:
            spec = sem.check(spec)
        except sem.SemError as e:
            return {"mode": mode, "error": e.reason, "hyps": out_hyps, "vars": infos, "nodes": nodes, "skipped": skipped}
        return {"mode": mode, "spec": spec, "vars": infos, "nodes": nodes, "names": nodes, "hyps": out_hyps,
                "skipped": skipped}
    # the main model: without what only the moderations by two groups name (they have models of their own)
    used_lowers = {p for h, parts in higher.items() if h in used for p in parts}
    main = {**spec, "latent": {k: v for k, v in latent.items() if k in used or k in used_lowers},
            "observed": [o for o in observed if o in used],
            **({"higher": {h: parts for h, parts in higher.items() if h in used}} if higher else {})}
    try:
        main = sem.check(_single_items(main, single))
        group_models = {n: {**g, "spec": sem.check(_single_items(_group_spec(spec, g), single))}
                        for n, g in groups.items()}
    except sem.SemError as e:
        return {"mode": mode, "error": e.reason, "hyps": out_hyps, "vars": infos, "nodes": nodes}
    shown = set(main["latent"]) | set(main["observed"]) | set(main.get("higher") or {})
    # "nodes": what the diagram of the main model draws; "names": every variable, for the moderations by groups
    out = {"mode": mode, "spec": main, "vars": infos, "nodes": {k: v for k, v in nodes.items() if k in shown},
           "names": nodes, "hyps": out_hyps, "controls": controls, "control_errors": control_errors}
    if group_models:
        out["groups"] = group_models
    return out


def scale_candidates(survey):
    """[(variable name, label)] of the scales in the model of the plan: what can be formative with PLS (point 9)."""
    model = build(survey) if survey is not None and survey.id else None
    if not model or model.get("mode") != "structural":
        return []
    return [(code, f"{code} · {info.get('label') or code}") for code, info in model["vars"].items()
            if "error" not in info and info.get("kind") == "latent"]


INVARIANCE_MAX_GROUPS = 10  # a variable with more groups is no grouping for the test of invariance


def invariance_candidates(survey):
    """[(key, label)] of what can split the measurement model into groups (point 4): the language of the survey when
    it has several, randomisations and choice questions with two to INVARIANCE_MAX_GROUPS answers."""
    from app import study_plan

    out = []
    if len(survey.languages or []) > 1:
        out.append((study_plan.LANG_GROUPS, de_en("Sprache der Befragung", "Language of the survey")))
    for code, label in study_plan.measures(survey):
        info = variable(survey, code)
        if "error" in info or info.get("kind") != "observed":
            continue
        groups = info.get("groups") or list(info.get("coding") or ())
        if 2 <= len(groups) <= INVARIANCE_MAX_GROUPS:
            out.append((code, label))
    return out


def _invariance(survey, model, table):
    """The test of measurement invariance over the groups the plan names (SEM extensions 2026-10-17, point 4): the
    reflective constructs of the model (all scales without hypotheses), correlated, in each group with at least
    GROUP_MIN complete cases; covariance-based whatever the method of the model. {"by", "labels", "n", "levels",
    "highest", "constructs", "dropped"} or {"error"} or None when the plan names no grouping."""
    found = _invariance_input(survey, model, table)
    return invariance_numbers(found) if found and "input" in found else found


def _invariance_input(survey, model, table):
    """The part of _invariance that reads the survey: what is shown beside the levels, and under "input" the rows of
    each group with the columns and the model (numbers only, for invariance_numbers in the job "sem_fit")."""
    from app import study_plan

    key = study_plan.plan_of(survey)["sem"]["invariance"]
    if not key or not model or model.get("error"):
        return None
    spec = model["spec"]
    gone = set(spec.get("products") or {}) | set(spec.get("single") or {}) | set(spec.get("formative") or ())
    latent = {lv: items for lv, items in spec["latent"].items() if lv not in gone}
    if not latent:
        return {"error": "constructs"}
    mspec = {"latent": latent}
    if key == study_plan.LANG_GROUPS:
        labels = list(survey.languages or [])
        groups = [labels.index(r.get("lang")) if r.get("lang") in labels else None for r in table]
        by = de_en("Sprache der Befragung", "language of the survey")
        from app.surveys import LANGUAGE_NAMES

        labels = [LANGUAGE_NAMES.get(code, code) for code in labels]
    else:
        info = variable(survey, key)
        if "error" in info:
            return {"error": "groups"}
        labels = _group_labels(survey, info, key)
        groups = _group_index(survey, info, table)
        by = key
    rows, cols, _, kept = matrix(survey, model, table, spec=mspec, with_ids=True, positions=True)
    by_group = {}
    for row, k in zip(rows, kept):
        g = groups[k]
        if g is not None:
            by_group.setdefault(g, []).append(row)
    used = sorted(g for g, members in by_group.items() if len(members) >= GROUP_MIN)
    dropped = [(labels[g], len(by_group[g])) for g in sorted(by_group) if g not in used]
    out = {"by": by, "labels": [labels[g] for g in used], "n": [len(by_group[g]) for g in used], "dropped": dropped,
           "constructs": [(model["nodes"].get(lv, (lv, lv))[1]) for lv in latent]}
    if len(used) < 2:
        out["error"] = "few_groups"
        return out
    out["input"] = {"groups": [by_group[g] for g in used], "cols": cols, "latent": latent,
                    "names": {lv: list(model["nodes"].get(lv, (lv, lv))) for lv in latent}}
    return out


def invariance_numbers(found):
    """The levels of measurement invariance from _invariance_input's "input" (numbers only)."""
    inp = found.pop("input")
    out = dict(found)
    mspec = {"latent": inp["latent"]}
    try:
        res = sem_means.invariance(inp["groups"], inp["cols"], mspec)
    except (sem.SemError, ValueError, ArithmeticError):
        out["error"] = "estimate"
        return out
    out["levels"] = _plain(res["levels"])
    out["highest"] = res["highest"]
    names = inp["names"]
    scalar = next((r["fit"] for r in res["levels"] if r["level"] == "scalar"), None)
    out["means"] = [{"label": names.get(lv, (lv, lv))[1], "rows": [{"group": out["labels"][g + 1], **m}
                                                                   for g, m in enumerate(found_lv)]}
                    for lv, found_lv in ((scalar or {}).get("latent_means") or {}).items()]
    return out


def alternative_spec(survey, model, spec):
    """The competing model of the plan (point 11): the model without the paths of the hypotheses and the direct effects
    of the mediations it names; (spec, [labels of what is left out]) or None."""
    from app import study_plan

    plan = study_plan.plan_of(survey)
    keys = plan["sem"]["alternative"]
    if not keys:
        return None
    numbers = {h.get("id"): n for n, h in enumerate(plan["hypotheses"], 1)}
    drop, labels = set(), []
    for key in keys:
        n = numbers.get(key[2:])
        entry = (model.get("hyps") or {}).get(n) or {}
        found = [tuple(p) for p in (entry.get("paths") if key[0] == "h" else entry.get("direct")) or []]
        if found:
            drop |= set(found)
            labels.append(de_en("H{n}", "H{n}", n=n) if key[0] == "h" else de_en("direkter Effekt in H{n}", "direct effect in H{n}", n=n))
    if not drop:
        return None
    paths = [p for p in spec["paths"] if tuple(p) not in drop]
    alt = {**spec, "paths": paths,
           "indirect": [t for t in spec["indirect"] if (t[0], t[1]) in paths and (t[1], t[2]) in paths]}
    alt.pop("modmed", None)
    return sem.check(alt), labels


def _compare(survey, model, spec, rows, cols, res, method, wts):
    """The main model against the competing one (point 11), covariance-based: both fits, the scaled difference test
    (Satorra & Bentler, 2001; FIML: with the Yuan-Bentler factors) and the differences of AIC and BIC (competing
    minus main: positive speaks for the main model). None without a competing model."""
    found = alternative_spec(survey, model, spec)
    return None if found is None else compare_numbers(found[0], found[1], spec, rows, cols, res, method, wts)


def compare_numbers(alt_spec, labels, spec, rows, cols, res, method, wts):
    """_compare with the competing model given (numbers only)."""
    if method == "pls":
        return {"labels": labels, "error": "pls"}
    if spec.get("ordinal"):
        return {"labels": labels, "error": "ordinal"}
    if spec.get("missing") == "fiml":
        alt = sem_means.fit_fiml(rows, cols, alt_spec)
    else:
        alt = sem.fit_ml(rows, cols, alt_spec, robust=True, weights=wts)
    if not alt.get("converged"):
        return {"labels": labels, "error": "converge"}
    n = res["n"]
    diff = sem_means.scaled_difference(alt, res)
    dq = res["q"] - alt["q"]
    d_chi2 = alt["chi2"] - res["chi2"]
    return {"labels": labels, "main": _plain({k: res.get(k) for k in ("chi2", "df", "cfi", "rmsea", "srmr")}),
            "alt": _plain({k: alt.get(k) for k in ("chi2", "df", "cfi", "rmsea", "srmr")}),
            "diff": _plain(diff), "d_aic": d_chi2 - 2 * dq, "d_bic": d_chi2 - dq * math.log(n),
            "admissible": bool(alt.get("admissible"))}


def subscale_candidates(survey):
    """[(variable name, label)] of the instruments with subscales in the model of the plan: what can be one factor of
    the second order (point 8)."""
    model = build(survey) if survey is not None and survey.id else None
    if not model or model.get("mode") != "structural":
        return []
    return [(code, f"{code} · {info.get('label') or code}") for code, info in model["vars"].items()
            if "error" not in info and info.get("kind") == "latent" and len(info.get("parts") or []) >= 2]


def alternative_candidates(survey):
    """[(key, label)] of what a competing model can leave out (point 11): the paths of each hypothesis of the model
    and the direct effect of each mediation."""
    from app import study_plan

    model = build(survey) if survey is not None and survey.id else None
    if not model or model.get("mode") != "structural":
        return []
    out = []
    for n, h in enumerate(study_plan.plan_of(survey)["hypotheses"], 1):
        entry = (model.get("hyps") or {}).get(n) or {}
        if entry.get("paths") and not entry.get("errors"):
            out.append((f"h:{h.get('id')}", de_en("H{n} (ihre Pfade auf 0)", "H{n} (its paths at 0)", n=n)))
        if entry.get("direct") and not entry.get("errors"):
            out.append((f"d:{h.get('id')}", de_en("direkter Effekt in H{n} (vollständige Vermittlung)",
                                                    "direct effect in H{n} (full mediation)", n=n)))
    return out


def single_candidates(survey):
    """[(variable name, label)] of the single questions with numbers in the model of the plan: what can enter as a
    construct with a fixed reliability (point 7)."""
    model = build(survey) if survey is not None and survey.id else None
    if not model or model.get("mode") != "structural":
        return []
    return [(code, f"{code} · {info.get('label') or code}") for code, info in model["vars"].items()
            if "error" not in info and info.get("kind") == "observed" and not info.get("coding")
            and not info.get("dummies") and not info.get("condition")]


def _single_items(spec, single):
    """The model with each single question of `single` ({name: reliability}) as a construct of one indicator (the
    column name°) whose error variance is fixed at 1 - reliability (SEM extensions 2026-10-17, point 7)."""
    found = {code: rel for code, rel in single.items() if code in spec["observed"]}
    if not found:
        return spec
    return {**spec, "latent": {**spec["latent"], **{code: [code + SINGLE] for code in found}},
            "observed": [o for o in spec["observed"] if o not in found],
            "single": {**(spec.get("single") or {}), **found}}


def _moderation(n, entry, found, paths, latent, observed, nodes, products, groups, link, by_groups, legs):
    """One hypothesis "Moderation": w changes the path x -> y (see the module text). Fills entry and the model. A
    construct with subscales, or a variable with more than two groups, takes part with each of its nodes (stage 3);
    a path that is a leg of a mediation makes a moderated mediation (legs: {(x, y): {"stage", "x", "m", "y"}})."""
    x_info, w_info, y_info = found
    xs, ws, ys = _nodes(x_info), _nodes(w_info), _nodes(y_info)
    leg = legs.get((x_info["code"], y_info["code"]))
    if by_groups and (w_info.get("coding") or w_info.get("dummies")) and not leg:
        # groups: the same model in each, the paths x -> y compared; in the model of all, x -> y over all groups
        entry["groups"] = {"w": w_info["code"], "w_nodes": ws, "x": xs, "y": ys}
        link(xs, ys, [])
        groups[n] = entry["groups"]
        return
    entry["moderations"] = []
    for x in xs:
        for w in ws:
            # one term per pair of variables: w × x moderating another path is the same product (else singular)
            name = next((k for k, v in products.items() if {v["x"], v["w"]} == {x, w}), f"{x}{TIMES}{w}")
            if name in products:
                link([x], ys, [])
                link([w], ys, [])
                link([name], ys, entry["paths"])
                for y in ys:
                    entry["moderations"].append({"x": x, "w": w, "y": y, "xw": name})
                continue
            xi, wi = latent.get(x), latent.get(w)
            if xi and wi:  # matched pairs in the order of the statements (Marsh et al., 2004)
                pairs, kind = list(zip(xi, wi)), "ll"
            elif xi:
                pairs, kind = [(a, w) for a in xi], "lo"
            elif wi:
                pairs, kind = [(x, b) for b in wi], "lo"
            else:
                pairs, kind = [(x, w)], "oo"
            if kind == "oo":
                if name not in observed:
                    observed.append(name)
            else:
                latent[name] = [f"{a}{TIMES}{b}" for a, b in pairs]
            products[name] = {"x": x, "w": w, "kind": kind, "pairs": [list(p) for p in pairs]}
            nodes[name] = (nodes[x][0], f"{nodes[x][1]} {TIMES} {nodes[w][1]}")
            link([x], ys, [])
            link([w], ys, [])
            link([name], ys, entry["paths"])
            for y in ys:
                entry["moderations"].append({"x": x, "w": w, "y": y, "xw": name})
    if len(entry["moderations"]) == 1:
        entry["moderation"] = entry["moderations"][0]
        if leg:  # moderated mediation (Hayes, 2015): the index a3·b (first stage) or a·b3 (second stage)
            mod = entry["moderation"]
            entry["modmed"] = (leg["stage"], leg["x"], leg["m"], leg["y"], mod["xw"])
            entry["mediation"] = leg["number"]


def _group_spec(spec, g):
    """The model of a moderation by groups: the model without the moderator w (and what only it names), with the
    paths x -> y."""
    gone = set(g["w_nodes"]) | {g["w"]}
    # an interaction term with w (w also moderating a leg of a mediation, or as x of another moderation) goes too,
    # with its paths: its product columns are not built without w (audit 2026-10-12, SEM-2)
    gone |= {k for k, v in (spec.get("products") or {}).items() if gone & {v["x"], v["w"]}}
    paths = [p for p in spec["paths"] if not gone & set(p)]
    for x in g["x"]:
        for y in g["y"]:
            if (x, y) not in paths:
                paths.append((x, y))
    cov = [c for c in spec.get("cov", ()) if not gone & set(c)]
    used = {a for p in paths + [tuple(c) for c in cov] for a in p}
    products = {k: v for k, v in (spec.get("products") or {}).items() if not gone & {v["x"], v["w"]} and k in used}
    out = {"latent": {k: v for k, v in spec["latent"].items() if k in used},
           "observed": [o for o in spec["observed"] if o in used and o not in gone], "paths": paths,
           "indirect": [t for t in spec["indirect"] if not gone & set(t) and (t[0], t[1]) in paths and (t[1], t[2]) in paths]}
    if cov:
        out["cov"] = cov
    if products:
        out["products"] = products
    return sem.check(out)


def _observed_values(survey, info, table):
    from app import hypotheses

    if info.get("coding"):
        zero, one = info["coding"]
        if "item" not in info:  # a randomisation: the condition by its name
            vals = [r.get(info["code"]) for r in table]
            return [0.0 if str(v) == zero else 1.0 if str(v) == one else None for v in vals]
        codes = hypotheses._codes(survey, info["code"], table)
        return [0.0 if c == zero else 1.0 if c == one else None for c in codes]
    values, _ = hypotheses._values(survey, info["code"], table)
    return [v if isinstance(v, float) else None for v in values]


def _group_index(survey, info, table):
    """The group of each row of a variable with groups (0, 1, ...), None where it is missing."""
    from app import hypotheses

    values = info.get("groups") or list(info.get("coding") or ())
    if info.get("condition"):  # a randomisation: the condition by its name
        found = [r.get(info["code"]) for r in table]
    else:
        found = hypotheses._codes(survey, info["code"], table)
    return [values.index(str(v)) if v is not None and str(v) in values else None for v in found]


def matrix(survey, model, table, spec=None, extra=(), with_ids=False, positions=False, complete=True):
    """(rows of complete cases as lists, the columns, number of rows in the table). spec: another model than the
    main one (a moderation by groups); extra: variables appended to each row (their group index, 0, 1, ...). The
    columns of an interaction term are the products of the indicators of x and w, each centred over the complete
    cases, the product centred again (double mean centring, Lin et al., 2010); a variable with more than two groups
    gives one 0/1 column per group but the first (stage 3)."""
    from app import scales

    spec = spec or model["spec"]
    cols = sem.columns(spec)
    products = {f"{a}{TIMES}{b}": (a, b) for part in (spec.get("products") or {}).values() for a, b in part["pairs"]}
    want = [c for c in cols if c not in products] + [c for c in extra if c not in cols]
    found = {}
    for code, info in model["vars"].items():
        if "error" in info:
            continue
        if info["kind"] == "latent":
            item = info["item"]
            codes = [c for _, _, items in info["parts"] for c, _, _ in items if c in want]
            if not codes:
                continue
            hidden = scales._missing(item, codes)
            rows = [scales.values(item, codes, r, hidden=hidden) for r in table]
            for k, c in enumerate(codes):
                found[c] = [row[k] for row in rows]
            continue
        if code in extra:
            found[code] = [float(g) if g is not None else None for g in _group_index(survey, info, table)]
        if info.get("dummies"):
            index = None
            for k, (node, _, _) in enumerate(info["dummies"], 1):
                if node in want:
                    index = index if index is not None else _group_index(survey, info, table)
                    found[node] = [None if g is None else 1.0 if g == k else 0.0 for g in index]
        elif code in want and code not in found:
            found[code] = _observed_values(survey, info, table)
        if code + SINGLE in want:  # a single question as a construct: its own values (point 7)
            found[code + SINGLE] = found.get(code) or _observed_values(survey, info, table)
    data, kept = [], []
    for k in range(len(table)):
        row = [found[c][k] for c in want]
        # complete cases; for FIML (point 2) every case with at least one value, gaps as None
        if all(v is not None for v in row) if complete else any(v is not None for v in row):
            data.append(row)
            kept.append(k)
    at = {c: k for k, c in enumerate(want)}
    if products and data:
        import numpy as np

        x = np.asarray(data, dtype=float)
        centred = x - x.mean(axis=0)
        made = {}
        for name, (a, b) in products.items():
            v = centred[:, at[a]] * centred[:, at[b]]
            made[name] = (v - v.mean()).tolist()
        data = [[made[c][k] if c in products else row[at[c]] for c in cols] + [row[at[c]] for c in extra]
                for k, row in enumerate(data)]
    elif extra:
        data = [[row[at[c]] for c in cols] + [row[at[c]] for c in extra] for row in data]
    if with_ids:  # the id of each kept row, as the data export names the participation (positions: their index)
        return data, cols, len(table), (kept if positions else [table[k].get("resp_id") for k in kept])
    return data, cols, len(table)


HIGH_R = 0.9  # predictors of one target correlated at least this much are marked in the diagnosis


def diagnosis(rows, cols, spec, names):
    """What helps when the estimate has no solution or collapsed (Ralf, 2026-10-07): the highest correlations between
    the predictors of each target (scores: the mean of a construct's statements), α and the number of statements of
    each construct. {"pairs": [(a, b, r)] highest first, "scales": [(name, k, alpha)]}."""
    import numpy as np

    if not rows:
        return {"pairs": [], "scales": []}
    x = np.asarray(rows, dtype=float)
    index = {c: k for k, c in enumerate(cols)}
    score = {}
    for v, items in (spec.get("latent") or {}).items():
        idx = [index[i] for i in items if i in index]
        if idx:
            score[v] = x[:, idx].mean(axis=1)
    for v in spec.get("observed") or []:
        if v in index:
            score[v] = x[:, index[v]]
    label = (lambda v: (names.get(v) or (v, v))[1])
    seen, pairs = set(), []
    for target in {b for _, b in spec.get("paths") or []}:
        preds = [a for a, b in spec["paths"] if b == target and a in score]
        for i, a in enumerate(preds):
            for b in preds[i + 1:]:
                key = tuple(sorted((a, b)))
                if key in seen:
                    continue
                seen.add(key)
                sa, sb = score[a], score[b]
                if np.std(sa) == 0 or np.std(sb) == 0:
                    continue
                pairs.append((label(a), label(b), float(np.corrcoef(sa, sb)[0, 1])))
    pairs.sort(key=lambda p: -abs(p[2]))
    scales = []
    for v, items in (spec.get("latent") or {}).items():
        idx = [index[i] for i in items if i in index]
        k = len(idx)
        alpha = None
        if k >= 2:
            sub = x[:, idx]
            total = sub.sum(axis=1).var(ddof=1)
            alpha = float(k / (k - 1) * (1 - sub.var(axis=0, ddof=1).sum() / total)) if total > 0 else None
        scales.append((label(v), k, alpha))
    return {"pairs": pairs[:5], "scales": scales}


def data_key(rows, cols, spec, method, weights=None):
    """What a bootstrap result belongs to: the data, the model and the method, and the version of the bootstrap
    (2: estimated in units of the SD, inadmissible draws counted; audit 2026-10-06, SEM-2 and SEM-7; 3: with the
    intervals of HTMT and of formative weights, SEM extensions 2026-10-17)."""
    body = json.dumps([rows, cols, spec, method, 3] + ([weights] if weights else []), sort_keys=True, default=list)
    return hashlib.sha256(body.encode()).hexdigest()[:24]


def free_parameters(spec):
    cols = sem.columns(spec)
    return len(cols) * (len(cols) + 1) // 2 - sem.model_df(spec)


def estimate(survey, table, method=None, weighted=False):
    """The model of the survey estimated on the export rows `table` (the results page's pool): a dict for the page
    and the hypotheses, None when the survey has no model. weighted: with the weights of the survey (app/weights.py)
    when the page shows weighted results (SEM extensions 2026-10-17, point 5): covariance-based ML only, on the
    participations that have a weight. Here in one go; the results page runs numbers() as the job "sem_fit"
    (app/sem_fit_job.py; Ralf 2026-10-17, thread "Rechenknecht")."""
    out, job = prepare(survey, table, method, weighted)
    return out if job is None else finish(out, numbers(job))


def prepare(survey, table, method=None, weighted=False):
    """The part of estimate() that reads the survey: (out, job). out: what the page shows besides the numbers (final
    when job is None: no model, an error of the plan, too few cases). job: what numbers() estimates, plain numbers
    and the model without participation numbers (the ids of the construct scores stay in out)."""
    model = build(survey)
    if model is None:
        return None, None
    method = method or method_of(survey)
    from app import study_plan

    out = {"mode": model["mode"], "method": method, "hyps": model["hyps"], "nodes": model["nodes"],
           "names": model.get("names") or model["nodes"], "notes": [], "error": model.get("error"),
           # the drawing of the study plan: the diagram keeps its arrangement (modell-zeichnen-konzept.md, F5)
           "layout": study_plan.plan_of(survey)["layout"]["nodes"],
           # subscales of one statement the measurement model leaves out (audit 2026-10-12, SEM-8)
           "skipped": model.get("skipped") or [],
           # control variables and single questions with a fixed reliability (SEM extensions 2026-10-17, 6 and 7)
           "controls": model.get("controls") or {}, "control_errors": model.get("control_errors") or [],
           "single": dict((model.get("spec") or {}).get("single") or {})}
    out["labels"] = {code: info.get("label") or code for code, info in model["vars"].items() if "error" not in info}
    out["coded"] = {code: info["coded"] for code, info in model["vars"].items() if info.get("coded")}
    # randomisations: drawn as "Zuteilung: …" with a dashed frame, not as a question (Ralf, 2026-10-07)
    out["conditions"] = [code for code, info in model["vars"].items() if info.get("condition")]
    out["statements"] = {c: text for info in model["vars"].values() if info.get("kind") == "latent"
                         for _, _, items in info["parts"] for c, text, _ in items}
    if out["error"]:
        return out, None
    spec = model["spec"]
    out["spec"] = spec
    fiml = spec.get("missing") == "fiml" and method != "pls"
    rows, cols, n_all, ids = matrix(survey, model, table, with_ids=True, complete=not fiml)
    n = len(rows)
    if fiml:
        out["missing"] = "fiml"
        out["n_complete"] = sum(1 for r in rows if all(v is not None for v in r))
    wts = None
    if weighted and method != "pls" and not fiml and not spec.get("ordinal"):
        from app import weights as weights_mod

        found = weights_mod.compute(survey, table)
        if found:
            pairs = [(r, found["weights"].get(i)) for r, i in zip(rows, ids)]
            pairs = [(r, w) for r, w in pairs if w]
            rows, wts = [r for r, _ in pairs], [w for _, w in pairs]
            ids = [i for i in ids if found["weights"].get(i)]
            n = len(rows)
            out["weighted"] = True
            out["n_eff"] = weights_mod.n_eff(wts)
    elif weighted:
        out["unweighted"] = True  # PLS, FIML and WLSMV: unweighted, the page says so
    out.update(n=n, n_all=n_all)
    groups = {number: _group_input(survey, model, table, g) for number, g in (model.get("groups") or {}).items()}
    points = [p for info in model["vars"].values() if info.get("kind") == "latent"
              for _, _, items in info["parts"] for c, _, p in items if c in cols and p is not None]
    out["few_points"] = method == "cb" and any(p < MIN_POINTS for p in points)
    q = free_parameters(spec)
    arrows = max([sum(1 for _, b in spec["paths"] if b == v) for v in model["nodes"]] or [0])
    need = max(MIN_N, PER_PARAMETER * q) if method == "cb" else max(MIN_N, PLS_TIMES * arrows)
    out["need"] = need
    if n < need:
        if groups:  # moderations by two groups: a model in both groups each (F2), small as the data are
            out["group_fits"] = {number: _group_back(_group_numbers(g)) for number, g in groups.items()}
        out["error"] = "few"
        return out, None
    out["small"] = n < SMALL_N
    try:
        alternative = alternative_spec(survey, model, spec)
    except Exception:  # numpy's LinAlgError and the like: only the comparison's part of the page says so
        alternative = "error"
    try:
        invariance = _invariance_input(survey, model, table)
    except Exception:
        invariance = {"error": "estimate"}
    out["_ids"] = ids  # the participations of the construct scores: they stay here, the job gets numbers only
    out["_binary"] = _binary(survey, model, spec, rows, cols)
    out["boot_key"] = data_key(rows, cols, spec, method, wts)
    out["cols"] = cols
    out["_rows"] = rows  # for the bootstrap; kept on the server with the results, never shown
    out["_weights"] = wts
    job = {"rows": rows, "cols": cols, "spec": spec, "method": method, "weights": wts, "fiml": fiml,
           "mode": model["mode"], "nodes": model["nodes"], "groups": groups,
           "alternative": alternative if alternative is None or alternative == "error" else list(alternative),
           "invariance": invariance}
    return out, job


def _respec(spec):
    """A model back from JSON: its pairs and triples as tuples again (sem.py compares them with tuples)."""
    spec = dict(spec)
    for k in ("paths", "indirect", "cov", "modmed"):
        if k in spec:
            spec[k] = [tuple(p) for p in spec[k]]
    return spec


def numbers(job, progress=None):
    """The estimates of prepare()'s job: numbers only, no survey and no database (it runs in the estimation process
    or on the Rechenknecht too). {"error", "diag", "fit", "predict", "scores", "compare", "invariance", "group_fits",
    "ordinal"} as far as reached."""
    spec, rows, cols, method, wts = _respec(job["spec"]), job["rows"], job["cols"], job["method"], job["weights"]
    nodes = job["nodes"]
    found = {"group_fits": {}}
    steps = 4 + len(job["groups"] or {})
    done = [0]

    def step():
        done[0] += 1
        if progress:
            progress(min(1.0, done[0] / steps))

    for number, g in (job["groups"] or {}).items():
        found["group_fits"][number] = _group_numbers(g)
        step()
    try:
        if method == "pls":
            res = sem.fit_pls(rows, cols, spec)
        elif spec.get("ordinal"):
            if spec.get("single") or spec.get("formative") or not sem_ordinal.delta_ok(spec):
                return {**found, "error": "ordinal_model"}
            if not sem_ordinal.ordinal_columns(rows):
                return {**found, "error": "ordinal_metric"}
            res = sem_ordinal.fit_wlsmv(rows, cols, spec)
            found["ordinal"] = True
        elif job["fiml"]:
            res = sem_means.fit_fiml(rows, cols, spec)
        else:
            res = sem.fit_ml(rows, cols, spec, robust=True, weights=wts)
    except (sem.SemError, ValueError, ArithmeticError) as e:
        return {**found, "error": getattr(e, "reason", "estimate")}
    except Exception:  # numpy's LinAlgError and the like: no result, the page says so
        return {**found, "error": "estimate"}
    step()
    if not res.get("converged"):
        return {**found, "error": "converge", "diag": diagnosis(rows, cols, spec, nodes)}
    # a collapsed estimate (paths of 10^40, R² below 0, a negative χ²) is no result at all: no tables, no fit line,
    # only why (Ralf, 2026-10-07; mostly random or test data without relations, or two scales that are the same)
    if "extreme" in (res.get("problems") or []) or (res.get("chi2") is not None and res["chi2"] < -1e-6):
        return {**found, "error": "extreme", "diag": diagnosis(rows, cols, spec, nodes)}
    found["fit"] = _plain(res)
    if method == "pls" and job["mode"] == "structural":
        # the prediction of the indicators (PLSpredict, point 13) and the construct scores per participation (17)
        try:
            found["predict"] = _plain(sem.pls_predict(rows, cols, spec))
        except (sem.SemError, ValueError, ArithmeticError):
            found["predict"] = None
        try:
            scored = sem.fit_pls(rows, cols, spec, light=True, scores=True)
            names = [v for v in scored["scores"] if v not in (spec.get("products") or {})]
            found["scores"] = {"names": names,
                               "values": [[float(scored["scores"][v][k]) for v in names] for k in range(len(rows))]}
        except (sem.SemError, ValueError, ArithmeticError):
            found["scores"] = None
    step()
    try:  # the competing model planned in advance (point 11): its failure hits only its own part of the page
        alternative = job["alternative"]
        if alternative == "error":
            raise ValueError("alternative")
        found["compare"] = None if alternative is None else compare_numbers(
            _respec(alternative[0]), alternative[1], spec, rows, cols, res, method, wts)
    except Exception:  # numpy's LinAlgError and the like
        found["compare"] = {"error": "estimate"}
    step()
    invariance = job["invariance"]
    try:  # the test of measurement invariance (point 4): its failure hits only its own part of the page
        found["invariance"] = invariance_numbers(dict(invariance)) if invariance and "input" in invariance             else invariance
    except Exception:  # numpy's LinAlgError and the like
        found["invariance"] = {"error": "estimate"}
    step()
    return found


def finish(out, found):
    """The page's model from prepare()'s out and numbers()' found (also after JSON: from the job)."""
    out = dict(out)
    ids, binary = out.pop("_ids", None), out.pop("_binary", None)
    if found.get("group_fits"):
        out["group_fits"] = {_group_key(k): _group_back(v) for k, v in found["group_fits"].items()}
    if found.get("ordinal"):
        out["ordinal"] = True
    if found.get("error"):
        for k in ("boot_key", "cols", "_rows", "_weights"):
            out.pop(k, None)
        out["error"] = found["error"]
        if "diag" in found:
            out["diag"] = {"pairs": [tuple(p) for p in found["diag"]["pairs"]],
                           "scales": [tuple(x) for x in found["diag"]["scales"]]}
        return out
    out["fit"] = found["fit"]
    out["binary"] = binary
    if "predict" in found:
        out["predict"] = found["predict"]
    if "scores" in found:
        out["scores"] = None if found["scores"] is None else {"ids": ids, **found["scores"]}
    out["compare"] = found.get("compare")
    out["invariance"] = found.get("invariance")
    return out


def _group_key(k):
    return int(k) if isinstance(k, str) and k.lstrip("-").isdigit() else k


GROUP_MIN = 50  # complete cases each group of a moderation by two groups needs (Festlegung von Askurious: half of MIN_N)


def _group_labels(survey, info, code):
    """The names of the groups of a variable with groups, as the plan shows them."""
    labels = list(info.get("group_labels") or info.get("coding") or ("0", "1"))
    if info.get("coding") and info.get("item") is not None:  # the answers' names
        from app import hypotheses

        shown = hypotheses._labels(survey, code)
        labels = [shown.get(v, v) for v in labels]
    return labels


def _binary(survey, model, spec, rows, cols):
    """{variable: {"mean", "labels"}} of the variables with two groups (coded 0/1) in an interaction term: the
    conditional effects are taken in each group, not at ±1 SD (Hayes, 2015; audit 2026-10-12, decision 8). mean: the
    share of 1 among the complete cases (the column is centred around it in the product)."""
    out = {}
    for part in (spec.get("products") or {}).values():
        for v in (part["x"], part["w"]):
            info = model["vars"].get(v) or {}
            if v in out or not info.get("coding") or v not in cols or not rows:
                continue
            k = cols.index(v)
            out[v] = {"mean": sum(r[k] for r in rows) / len(rows), "labels": _group_labels(survey, info, v)}
    return out


def _group_fit(survey, model, table, g):
    """The model of a moderation by groups estimated in each group with equal loadings (sem.fit_ml_groups):
    {"n": [per group], "labels": [group names], "pairs": [(x, y)], "paths": {(x, y): [β per group]}, "diff": {(x,
    y): {"est", "se", "z", "p"}} (the second group minus the first, two groups), "joint": {(x, y): {"chi2", "df", "p"}}
    (the same path in every group), "admissible"} or {"error": key}."""
    return _group_back(_group_numbers(_group_input(survey, model, table, g)))


def _group_input(survey, model, table, g):
    """The part of _group_fit that reads the survey: the rows of each group (numbers only) and the names."""
    spec = g["spec"]
    try:  # a failure here hits only this hypothesis, not the page (audit 2026-10-12, SEM-2)
        rows, cols, _ = matrix(survey, model, table, spec=spec, extra=[g["w"]])
    except Exception:
        return {"out": {"error": "estimate", "n": [], "labels": [], "x": g["x"], "y": g["y"], "w": g["w"], "pairs": []}}
    labels = _group_labels(survey, model["vars"].get(g["w"]) or {}, g["w"])
    xs = [[r[:-1] for r in rows if r[-1] == float(v)] for v in range(len(labels))]
    pairs = [[x, y] for x in g["x"] for y in g["y"]]
    return {"out": {"n": [len(x) for x in xs], "labels": labels, "x": g["x"], "y": g["y"], "w": g["w"],
                    "pairs": pairs}, "xs": xs, "cols": cols, "spec": spec}


def _group_numbers(g):
    """The estimate of one moderation by groups from _group_input (numbers only); its pairs as "x>y" keys (JSON)."""
    out = dict(g["out"])
    if out.get("error"):
        return out
    if min(out["n"]) < GROUP_MIN:
        out["error"] = "few_groups"
        return out
    pairs = [tuple(p) for p in out["pairs"]]
    try:
        res = sem.fit_ml_groups(g["xs"], g["cols"], _respec(g["spec"]))
    except (sem.SemError, ValueError, ArithmeticError) as e:
        out["error"] = getattr(e, "reason", "estimate")
        return out
    except Exception:  # numpy's LinAlgError and the like
        out["error"] = "estimate"
        return out
    if not res.get("converged"):
        out["error"] = "converge"
        return out
    out["paths"] = {f"{x}>{y}": [float(grp["paths"][(x, y)]) for grp in res["groups"]] for x, y in pairs}
    out["diff"] = {f"{x}>{y}": _plain(res["diff"][(x, y)]) for x, y in pairs}
    out["joint"] = {f"{x}>{y}": _plain(res["joint"][(x, y)]) for x, y in pairs}
    out["admissible"] = bool(res["admissible"])
    out["fit"] = _plain({"chi2": res["chi2"], "df": res["df"], "p": res["p"]})
    return out


def _group_back(out):
    """_group_numbers' result as the page reads it: the pairs as tuples, the values per pair keyed by them."""
    out = dict(out)
    out["pairs"] = [tuple(p) for p in out.get("pairs") or []]
    for k in ("paths", "diff", "joint"):
        if k in out:
            out[k] = {tuple(key.split(">", 1)): v for key, v in out[k].items()}
    return out


# --- the bootstrap (estimation process) -------------------------------------------------------------------------

def _paths(survey, app=None):
    from flask import current_app

    folder = os.path.join((app or current_app).config["UPLOAD_FOLDER"], "hb", survey.uid)  # removed with the survey
    return os.path.join(folder, "sem.json"), os.path.join(folder, "sem.run.json")


def fit_paths(survey, app=None):
    """Result and marker of the job "sem_fit" (the estimate of the results page, app/sem_fit_job.py)."""
    from flask import current_app

    folder = os.path.join((app or current_app).config["UPLOAD_FOLDER"], "hb", survey.uid)  # removed with the survey
    return os.path.join(folder, "sem_fit.json"), os.path.join(folder, "sem_fit.run.json")


def fit_state(survey, key):
    """{"state": "none" | "running" | "error", "progress", "queued", "message"} of the job "sem_fit" for the
    fingerprint key; a run for another one (the weighted page, an older plan) counts as running, queued before it."""
    from app import jobs

    paths = fit_paths(survey)
    marker = jobs.read(paths)
    if marker and marker.get("state") == "running":
        marker = jobs.settle(paths, marker) or marker
    if not marker:
        return {"state": "none"}
    if marker.get("state") == "running":
        mine = marker.get("key") == key
        return {"state": "running", "progress": (marker.get("progress") or 0.0) if mine else 0.0,
                "queued": bool(marker.get("queued")) or not mine}
    if marker.get("key") == key and marker.get("state") in ("error", "aborted"):
        return {"state": "error", "message": marker.get("message", "")}
    return {"state": "none"}


BOOT_UNSURE = 0.05  # Festlegung von Askurious (audit 2026-10-06, SEM-7): from this share of draws without a solution or
# with an inadmissible one the page calls the interval unsure


def unsure(stored):
    """Do so many draws of a stored bootstrap fail or end inadmissible that its intervals are unsure?"""
    draws = stored.get("draws") or 0
    return bool(draws) and ((stored.get("failed") or 0) + (stored.get("inadmissible") or 0)) / draws >= BOOT_UNSURE


def boot_state(survey, result):
    """{"state": "none" | "running" | "error" | "done", "progress", "queued", "result": {"paths": {(a, b): {"ci",
    "se"}}, "indirect": {(x, m, y): ...}, "cov", "modmed", "draws", "failed"} | None} of the bootstrap that belongs to
    this estimate (its boot_key); a stored one of other data counts as none."""
    from app import jobs, maxdiff_hb

    if not result or not result.get("boot_key"):
        return None
    paths = _paths(survey)
    marker = jobs.read(paths)  # also on the Rechenknecht (C7: was not followed there)
    stored = maxdiff_hb._read(paths[0])
    if marker and marker.get("key") == result["boot_key"]:
        if marker.get("state") == "running":
            marker = jobs.settle(paths, marker) or marker
        if marker.get("state") == "running":
            return {"state": "running", "progress": marker.get("progress") or 0.0, "queued": bool(marker.get("queued"))}
        if marker.get("state") in ("error", "aborted"):
            return {"state": "error", "message": marker.get("message", "")}
    if stored and stored.get("key") == result["boot_key"]:
        return {"state": "done", "result": {
            "draws": stored["draws"], "failed": stored["failed"], "created": stored.get("created"),
            "inadmissible": stored.get("inadmissible"), "unsure": unsure(stored),
            "paths": {(a, b): {"ci": (lo, hi), "se": se} for a, b, lo, hi, se in stored["paths"] if lo is not None},
            "indirect": {(x, m, y): {"ci": (lo, hi), "se": se} for x, m, y, lo, hi, se in stored["indirect"]
                         if lo is not None},
            "cov": {(a, b): {"ci": (lo, hi), "se": se} for a, b, lo, hi, se in stored.get("cov") or [] if lo is not None},
            "modmed": {tuple(t): {"ci": (lo, hi), "se": se} for *t, lo, hi, se in stored.get("modmed") or []
                       if lo is not None},
            # formative weights (point 9) and HTMT with its one-sided 95 % bound (point 13)
            "weights": {(lv, i): {"ci": (lo, hi), "se": se} for lv, i, lo, hi, se in stored.get("weights") or []
                        if lo is not None},
            "htmt": {(a, b): {"ci": (lo, hi), "upper95": up} for a, b, lo, hi, up in stored.get("htmt") or []
                     if lo is not None}}}
    return {"state": "none"}


def start_bootstrap(survey, result, pool, background=True):
    """Start the bootstrap of the estimate: "started", "running", "current" or "nodata"."""
    from flask import current_app

    from app import jobs

    if not result or not result.get("_rows") or not result.get("spec"):
        return "nodata"
    state = boot_state(survey, result)
    if state and state["state"] == "done":
        return "current"
    spec = result["spec"]
    inp = {"key": result["boot_key"], "rows": result["_rows"], "cols": result["cols"], "method": result["method"],
           "draws": BOOT_DRAWS, "seed": 1, **({"weights": result["_weights"]} if result.get("_weights") else {}),
           "spec": {"latent": spec["latent"], "observed": spec["observed"], "paths": [list(p) for p in spec["paths"]],
                    "indirect": [list(t) for t in spec["indirect"]], "cov": [list(p) for p in spec.get("cov", ())],
                    **({"products": spec["products"]} if spec.get("products") else {}),
                    **({"modmed": [list(t) for t in spec["modmed"]]} if spec.get("modmed") else {}),
                    **({"single": spec["single"]} if spec.get("single") else {}),
                    **({"formative": spec["formative"]} if spec.get("formative") else {}),
                    **({"missing": "fiml"} if spec.get("missing") == "fiml" else {}),
                    **({"ordinal": True} if spec.get("ordinal") else {}),
                    **({"higher": spec["higher"]} if spec.get("higher") else {})}}
    app = current_app._get_current_object()
    paths = _paths(survey, app)
    if not jobs.start(app, "sem", paths, inp, pool, background, name=f"sem-{survey.id}"):
        return "running"
    return "started"


def joint_test(result, pairs, boot=None):
    """The test of several paths of one hypothesis together (subscales, more than two groups, stage 3):
    covariance-based the joint Wald test (χ², df), PLS Bonferroni over the paths' bootstrap p (Festlegung von
    Askurious). {"p", "text"} or None."""
    fit = result.get("fit") or {}
    if result.get("method") == "pls":
        ps = [(path_row(result, a, b, boot) or {}).get("p") for a, b in pairs]
        if any(p is None for p in ps):
            return None
        p = min(1.0, min(ps) * len(ps))
        return {"p": p, "text": "Bonferroni"}
    found = sem.wald(fit, pairs)
    if found is None:
        return None
    chi2, df, p = found
    return {"p": p, "chi2": chi2, "df": df}


def modmed_index(result, entry, boot=None):
    """The index of moderated mediation of a moderation on a leg of a mediation (Hayes, 2015): the product of the
    standardised interaction path and the other leg, with the bootstrap's interval and p; the conditional indirect
    effects where w lies one SD below and above its mean, or in each group of a 0/1 moderator (simple_slopes). {"value",
    "ci", "p", "conditional": [(key, label, effect)]} or None."""
    t = entry.get("modmed")
    if not t:
        return None
    stage, x, m, y, xw = t
    first = path_row(result, xw, m if stage == "a" else y, boot)
    other = path_row(result, m, y, boot) if stage == "a" else path_row(result, x, m, boot)
    leg = path_row(result, x, m, boot) if stage == "a" else path_row(result, m, y, boot)
    if not first or not other or not leg:
        return None
    out = {"value": first["std"] * other["std"], "ci": None, "p": None, "stage": stage}
    found = (boot or {}).get("modmed", {}).get(tuple(t))
    if found:
        out["ci"] = found["ci"]
        out["p"] = sem._normal_p(out["value"] / found["se"]) if found.get("se") else None
    # the conditional indirect effects: the slope of the moderated path where w is set, times the other path
    slopes = simple_slopes(result, entry["moderation"], boot)
    if slopes:
        out["conditional"] = [(key, label, v * other["std"]) for key, label, v in slopes]
    return out


def _plain(res):
    """The estimate with plain floats (numpy scalars out), tuple keys kept (the store pickles)."""
    def conv(v):
        if isinstance(v, dict):
            return {k: conv(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return type(v)(conv(x) for x in v)
        if hasattr(v, "item"):
            return v.item()
        return v

    return conv(res)


# --- planning (concept E10) -------------------------------------------------------------------------------------

def _planned(survey):
    """(spec, {(a, b): planned beta}, {number: entry}, the model) of the model the plan describes, None without one.
    The expected effect of a hypothesis is the standardised beta of its path(s); a mediation's value counts for both
    of its paths (the field says "β je Pfad", audit 2026-10-12, SEM-6); paths without a value are planned as 0."""
    from app import study_plan

    model = build(survey)
    if model is None or model.get("error") or model.get("mode") != "structural":
        return None
    betas = {}
    for n, h in enumerate(study_plan.plan_of(survey)["hypotheses"], 1):
        entry = model["hyps"].get(n)
        if entry and h.get("effect") is not None and not entry.get("groups"):
            for pair in entry.get("paths") or []:
                betas.setdefault(tuple(pair), float(h["effect"]))
    return model["spec"], betas, model["hyps"], model


def _number(survey, h):
    from app import study_plan

    return next((n for n, x in enumerate(study_plan.plan_of(survey)["hypotheses"], 1) if x.get("id") == h.get("id")), None)


def planned_n(survey, h, power=0.8, alpha=0.05):
    """The N the path(s) of hypothesis h need in the planned model (Satorra & Saris, 1985; for a mediation the joint
    significance of both paths, MacKinnon et al., 2002), with standardised loadings sem.PLAN_LOADING (Festlegung von
    Askurious). A moderation by groups in the model of the groups with the same loadings (audit 2026-10-12, decision
    9): the path x -> y spread evenly over Δβ around its planned value (two groups: −Δβ/2 and +Δβ/2), the groups of
    equal size, tested together with G − 1 df (sem.groups_n). None when there is no model or the betas are not
    possible together."""
    found = _planned(survey)
    if found is None:
        return None
    spec, betas, hyps, model = found
    number = _number(survey, h)
    entry = hyps.get(number) if number else None
    if entry and entry.get("groups"):
        g = (model.get("groups") or {}).get(number)
        if g is None or h.get("effect") is None:
            return None
        info = model["vars"].get(g["w"]) or {}
        k = max(2, len(info.get("groups") or info.get("coding") or ()))
        delta = float(h["effect"])
        pairs = [(x, y) for x in g["x"] for y in g["y"]]
        gspec = g["spec"]
        own = {p: b for p, b in betas.items() if p in gspec["paths"]}
        import numpy as np

        key = json.dumps(["groups", gspec, sorted(own.items()), pairs, k, delta, power, alpha], sort_keys=True, default=list)

        def compute():
            ns = []
            for pair in pairs:
                centre = own.get(pair, 0.0)
                values = [centre - delta / 2 + delta * i / (k - 1) for i in range(k)]
                try:
                    ns.append(sem.groups_n(gspec, own, pair, values, power, alpha))
                except (sem.SemError, ValueError, ArithmeticError, np.linalg.LinAlgError):
                    ns.append(None)
            return None if not ns or any(n is None for n in ns) else max(ns)

        return _cached(key, compute)
    if not entry or not entry.get("paths"):
        return None
    key = json.dumps([spec, sorted(betas.items()), entry["paths"], h.get("kind"), power, alpha], sort_keys=True, default=list)
    return _planned_n(key, spec, betas, entry["paths"], h.get("kind") == "mediation", power, alpha)


def moderation_groups(survey, h):
    """The number of groups when hypothesis h is a moderation by groups (the model compared in each group, not an
    interaction term), else 0. Read from the model (audit 2026-10-12, SEM-5): a moderator with groups on a leg of a
    mediation is an interaction term, and with PLS always."""
    if not h or h.get("kind") != "moderation" or not h.get("wv"):
        return 0
    found = _planned(survey)
    if found is None:
        return 0
    _, _, hyps, model = found
    number = _number(survey, h)
    if not number or not (hyps.get(number) or {}).get("groups"):
        return 0
    info = model["vars"].get(hyps[number]["groups"]["w"]) or {}
    return max(2, len(info.get("groups") or info.get("coding") or ()))


def moderation_by_groups(survey, h):
    """Is hypothesis h a moderation by groups (compared in the groups, not as an interaction term)?"""
    return moderation_groups(survey, h) > 0


_CACHE = {}
_CACHE_SIZE = 256
_CACHE_LOCK = threading.Lock()


def _cached(key, compute):
    """compute() kept under key in the bounded process cache. Returns the local value: another thread may clear the
    cache between storing and reading (code review 2026-10-15, L6); the computing itself runs outside the lock."""
    with _CACHE_LOCK:
        if key in _CACHE:
            return _CACHE[key]
    found = compute()
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_SIZE:
            _CACHE.clear()
        _CACHE[key] = found
    return found


def _planned_n(key, spec, betas, paths, mediation, power, alpha):
    def compute():
        try:
            if mediation:
                return sem.path_n(spec, betas, [tuple(p) for p in paths], power, alpha) if len(paths) == 2 else None
            ns = [sem.path_n(spec, betas, [tuple(p)], power, alpha) for p in paths]
            return None if any(n is None for n in ns) else max(ns)
        except (sem.SemError, ValueError, ArithmeticError):
            return None

    return _cached(key, compute)


POWER_REPS = 500  # data sets of the simulation (SEM extensions 2026-10-17, point 16)


def _power_paths(survey, app=None):
    from flask import current_app

    folder = os.path.join((app or current_app).config["UPLOAD_FOLDER"], "hb", survey.uid)
    return os.path.join(folder, "sem_power.json"), os.path.join(folder, "sem_power.run.json")


def power_n(survey):
    """The N the simulation draws: the target of the plan, else the largest N of the power analyses, else None."""
    from app import study_plan

    target = study_plan.plan_of(survey)["sample"]["target"]
    if target:
        return int(target)
    found = study_plan.power_summary(survey)
    return int(found["n"]) if found and found.get("n") else None


def power_input(survey):
    """What the simulation needs (point 16): the planned model, its expected effects and the hypotheses it judges;
    None without a covariance-based model, an N or an expected effect."""
    from app import study_plan

    if method_of(survey) == "pls":
        return None
    found = _planned(survey)
    n = power_n(survey)
    if found is None or not n:
        return None
    spec, betas, hyps, model = found
    plan = study_plan.plan_of(survey)["hypotheses"]
    judged = []
    for number, entry in sorted(hyps.items()):
        h = plan[number - 1] if number - 1 < len(plan) else {}
        if entry.get("errors") or entry.get("groups") or h.get("effect") is None:
            continue
        if h.get("kind") == "mediation":
            pairs = [p for t in entry.get("indirect") or [] for p in ((t[0], t[1]), (t[1], t[2]))][:2]
        elif entry.get("moderations"):
            pairs = [(m["xw"], m["y"]) for m in entry["moderations"]]
        else:
            pairs = entry.get("paths") or []
        judged.append({"number": number, "kind": h.get("kind"), "paths": [list(p) for p in pairs],
                       "cov": [list(c) for c in entry.get("cov") or []][:1], "direction": h.get("direction") or ""})
    if not judged:
        return None
    body = {"spec": {k: (v if not isinstance(v, list) else [list(x) if isinstance(x, tuple) else x for x in v])
                     for k, v in spec.items()},
            "betas": [[a, b, v] for (a, b), v in sorted(betas.items())], "hyps": judged, "n": n, "reps": POWER_REPS,
            "seed": 1}
    body["key"] = hashlib.sha256(json.dumps(body, sort_keys=True, default=list).encode()).hexdigest()[:24]
    return body


def power_state(survey):
    """{"state": "none" | "running" | "error" | "done", "progress", "result": {"n", "reps", "failed", "power": {number:
    share}}} of the simulation that belongs to the plan as it is now; None when there is nothing to simulate."""
    from app import jobs, maxdiff_hb

    inp = power_input(survey)
    if inp is None:
        return None
    paths = _power_paths(survey)
    marker = jobs.read(paths)
    if marker and marker.get("key") == inp["key"]:
        if marker.get("state") == "running":
            marker = jobs.settle(paths, marker) or marker
        if marker.get("state") == "running":
            return {"state": "running", "progress": marker.get("progress") or 0.0, "n": inp["n"]}
        if marker.get("state") in ("error", "aborted"):
            return {"state": "error", "n": inp["n"]}
    stored = maxdiff_hb._read(paths[0])
    if stored and stored.get("key") == inp["key"] and stored.get("power"):
        return {"state": "done", "n": inp["n"], "result": {"n": stored["n"], "reps": stored["reps"],
                                                           "failed": stored["failed"],
                                                           "power": {int(k): v for k, v in stored["power"]}}}
    return {"state": "none", "n": inp["n"]}


def start_power(survey, background=True):
    """Start the simulation: "started", "running", "current" or "nothing"."""
    from flask import current_app

    from app import jobs

    inp = power_input(survey)
    if inp is None:
        return "nothing"
    state = power_state(survey)
    if state and state["state"] == "done":
        return "current"
    app = current_app._get_current_object()
    if not jobs.start(app, "sem_power", _power_paths(survey, app), inp, {}, background, name=f"sem-power-{survey.id}"):
        return "running"
    return "started"


def rmsea_plan(survey, power=0.8):
    """The test of the whole covariance-based model (MacCallum et al., 1996: close fit, RMSEA .05 against .08):
    {"df", "n"}; None for PLS or without a model."""
    if method_of(survey) == "pls":
        return None
    found = _planned(survey)
    if found is None:
        return None
    try:
        df = sem.model_df(found[0])
    except sem.SemError:
        return None
    if df <= 0:
        return {"df": df, "n": None}
    return {"df": df, "n": sem.rmsea_n(df, power)}


# --- the hypotheses ---------------------------------------------------------------------------------------------

def _num(v, lang, d=2):
    from app.hypotheses import _de_lang

    return _de_lang(v, d, lang)


def _p(p, lang):
    from app.hypotheses import _p_lang

    return _p_lang(p, lang)


def _ci(lo, hi, lang, level="95"):
    if lang == "en":
        return f"{level}% CI [{_num(lo, lang)}, {_num(hi, lang)}]"
    return f"{level}-%-KI [{_num(lo, lang)}; {_num(hi, lang)}]"


def fit_sentence(fit, lang="de"):
    """The fit of a covariance-based model in APA style."""
    if not fit or fit.get("method") != "ml" or not fit.get("df"):
        return ""
    parts = [f"χ²({fit['df']}) = {_num(fit['chi2'], lang)}, {_p(fit['p'], lang)}"]
    if fit.get("estimator") == "wlsmv":  # the mean- and variance-adjusted test and indices (point 3)
        name = "WLSMV-χ²" if lang == "de" else "WLSMV χ²"
        parts = [f"{name}({fit['df']}) = {_num(fit['scaled']['chi2'], lang)}, {_p(fit['scaled']['p'], lang)}"]
    elif fit.get("scaled") and fit["scaled"].get("chi2") is not None:
        name = "Satorra-Bentler χ²" if lang == "en" else "Satorra-Bentler-χ²"
        parts.append(f"{name}({fit['df']}) = {_num(fit['scaled']['chi2'], lang)}, {_p(fit['scaled']['p'], lang)}")
    if fit.get("cfi") is not None:
        parts.append(f"CFI = {_num(fit['cfi'], lang)}")
    if fit.get("tli") is not None:
        parts.append(f"TLI = {_num(fit['tli'], lang)}")
    if fit.get("rmsea") is not None:
        lo, hi = fit["rmsea_ci"]
        parts.append(f"RMSEA = {_num(fit['rmsea'], lang, 3)}, " + _ci(lo, hi, lang, "90").replace(
            _num(lo, lang), _num(lo, lang, 3)).replace(_num(hi, lang), _num(hi, lang, 3)))
    parts.append(f"SRMR = {_num(fit['srmr'], lang, 3)}")
    rob = fit.get("robust_fit") or {}
    if rob.get("cfi") is not None and rob.get("rmsea") is not None:  # beside the normal-theory values (Brosseau-Liard)
        lo, hi = rob["rmsea_ci"]
        word = "robust" if lang == "en" else "robuster"
        tail = (", " + _ci(lo, hi, lang, "90").replace(_num(lo, lang), _num(lo, lang, 3)).replace(_num(hi, lang), _num(hi, lang, 3))
                if lo is not None else "")
        parts.append(f"{word} CFI = {_num(rob['cfi'], lang)}"
                     + (f", {word} TLI = {_num(rob['tli'], lang)}" if rob.get("tli") is not None else "")
                     + f", {word} RMSEA = {_num(rob['rmsea'], lang, 3)}{tail}")
    return ", ".join(parts)


def _verdict(p, value, direction):
    from app.hypotheses import _verdict as verdict

    return verdict(p, value, direction)


def path_row(result, a, b, boot=None):
    """The row of a path in the estimate, with the bootstrap's interval and p where there is one."""
    for row in (result.get("fit") or {}).get("paths") or []:
        if row["from"] == a and row["to"] == b:
            row = dict(row)
            found = (boot or {}).get("paths", {}).get((a, b))
            if found:
                row["boot_ci"] = found["ci"]
                if row.get("p") is None and found.get("se"):  # PLS: a normal p from the bootstrap error
                    row["p"] = sem._normal_p(row["std"] / found["se"]) if found["se"] > 0 else None
                    row["ci"] = found["ci"]
            return row
    return None


def cov_row(result, a, b, boot=None):
    """The row of a pair without a direction (F4), with the bootstrap's interval and p where there is one."""
    for row in (result.get("fit") or {}).get("covariances") or []:
        if {row["a"], row["b"]} == {a, b}:
            row = dict(row)
            found = (boot or {}).get("cov", {}).get((a, b)) or (boot or {}).get("cov", {}).get((b, a))
            if found:
                row["boot_ci"] = found["ci"]
                if row.get("p") is None and found.get("se"):  # PLS: a normal p from the bootstrap error
                    row["p"] = sem._normal_p(row["std"] / found["se"]) if found["se"] > 0 else None
                    row["ci"] = found["ci"]
            row["residual"] = bool({a, b} & sem.endogenous(result.get("spec") or {"paths": []}))
            return row
    return None


def indirect_row(result, x, m, y, boot=None):
    for row in (result.get("fit") or {}).get("indirect") or []:
        if (row["x"], row["m"], row["y"]) == (x, m, y):
            row = dict(row)
            found = (boot or {}).get("indirect", {}).get((x, m, y))
            row["boot_ci"] = found["ci"] if found else None
            return row
    return None


def apply(hyps, result, plan_hyps, boot=None, lang="de"):
    """Fill the results per hypothesis (app/hypotheses.evaluate) of the hypotheses that belong to the model from
    the estimate; the others stay as they are."""
    if not hyps or result is None:
        return hyps
    fit = result.get("fit") or {}
    admissible = fit.get("admissible", False)
    for n, (out, h) in enumerate(zip(hyps, plan_hyps), 1):
        if not wanted(h):
            continue
        entry = (result.get("hyps") or {}).get(n) or {}
        out.update(analysis="sem", verdict="offen", sentence="", notes=[], groups=[], sem_paths=[])
        out.pop("sensitivity", None)
        if entry.get("errors"):
            out["notes"].append(_error_note(entry["errors"]))
            continue
        if result.get("error"):
            out["notes"].append(de_en("Kein Ergebnis: Das Strukturgleichungsmodell ließ sich nicht schätzen (siehe unten).",
                                      "No result: the structural equation model could not be estimated (see below)."))
            continue
        out["n"] = result.get("n")
        names = result.get("nodes") or {}
        rows = []
        for a, b in entry.get("paths") or []:
            row = path_row(result, a, b, boot)
            if row:
                rows.append({"from": names.get(a, (a, a))[1], "to": names.get(b, (b, b))[1], **row})
        if h.get("kind") == "mediation":
            ind = [indirect_row(result, *t, boot=boot) for t in entry.get("indirect") or []]
            ind = [r for r in ind if r]
            out["sem_indirect"] = [{"path": " → ".join(names.get(v, (v, v))[1] for v in (r["x"], r["m"], r["y"])), **r}
                                   for r in ind]
            out["sem_paths"] = rows
            if len(ind) == 1:
                r = ind[0]
                out["effect"] = ("β", r["std"])
                legs = [path_row(result, r["x"], r["m"], boot), path_row(result, r["m"], r["y"], boot)]
                ps = [leg.get("p") if leg else None for leg in legs]
                if admissible and all(p is not None for p in ps):
                    # joint significance (MacKinnon et al., 2002): the larger p of the two paths decides and enters
                    # Holm's correction (audit 2026-10-06, SEM-1, decision 2); the bootstrap interval is reported
                    p = max(ps)
                    out["p"] = p
                    out["verdict"] = _verdict(p, r["std"], h.get("direction", ""))
                    sentence = f"{'indirect effect' if lang == 'en' else 'indirekter Effekt'} β = {_num(r['std'], lang)}"
                    if r.get("boot_ci"):
                        lo, hi = r["boot_ci"]
                        sentence += ", " + _ci(lo, hi, lang) + (" (bootstrap)" if lang == "en" else " (Bootstrap)")
                    sentence += (", joint significance of both paths (larger p): " if lang == "en"
                                 else ", gemeinsame Signifikanz beider Pfade (größeres p): ") + _p(p, lang)
                    out["sentence"] = sentence
                    if not r.get("boot_ci"):
                        out["notes"].append(de_en("Das Bootstrap-Intervall des indirekten Effekts fehlt noch (unten "
                                                  "„Bootstrap rechnen“).",
                                                  "The bootstrap interval of the indirect effect is still missing (below "
                                                  "“Run bootstrap”)."))
                elif admissible:
                    out["notes"].append(de_en("PLS prüft Pfade nur mit dem Bootstrap (unten „Bootstrap rechnen“).",
                                              "PLS tests paths only with the bootstrap (below “Run bootstrap”)."))
                out["notes"].append(de_en("Urteil über die Vermittlung: gemeinsame Signifikanz, beide Pfade (x → m und "
                                          "m → y) mit p < ,05; ihr größeres p zählt (MacKinnon et al., 2002).",
                                          "Verdict on the mediation: joint significance, both paths (x → m and m → y) "
                                          "with p < .05; the larger of their p counts (MacKinnon et al., 2002)."))
            else:
                out["notes"].append(de_en("Mehrere Subskalen: je Subskala ein indirekter Effekt, Urteile im Modell unten.",
                                          "Several subscales: one indirect effect per subscale, verdicts in the model below."))
        elif h.get("kind") == "moderation":
            _moderation_result(n, out, entry, result, h, boot, lang, admissible)
        elif entry.get("cov"):  # a relation without a direction (F4): the (residual) correlation
            found = [cov_row(result, a, b, boot) for a, b in entry["cov"]]
            found = [r for r in found if r]
            out["sem_cov"] = found
            if len(found) == 1:
                r = found[0]
                out["effect"] = ("r", r["std"])
                if r.get("p") is not None and admissible:
                    out["p"] = r["p"]
                    out["verdict"] = _verdict(r["p"], r["std"], h.get("direction", ""))
                    lo, hi = r["ci"]
                    out["sentence"] = f"r = {_num(r['std'], lang)}, {_ci(lo, hi, lang)}, {_p(r['p'], lang)}"
                elif result.get("method") == "pls":
                    out["notes"].append(de_en("PLS prüft Zusammenhänge nur mit dem Bootstrap (unten „Bootstrap rechnen“).",
                                              "PLS tests relationships only with the bootstrap (below “Run bootstrap”)."))
                if r.get("residual") and result.get("method") != "pls":
                    out["notes"].append(de_en("Zusammenhang ohne Richtung; weil eine der beiden Größen im Modell erklärt "
                                              "wird, ist r die Korrelation der Residuen (was die Pfade nicht erklären).",
                                              "Relationship without a direction; since one of the two is explained in the "
                                              "model, r is the correlation of the residuals (what the paths do not "
                                              "explain)."))
                elif result.get("method") == "pls":
                    out["notes"].append(de_en("PLS: Korrelation der Konstruktwerte.", "PLS: correlation of the construct scores."))
            elif found:
                out["notes"].append(de_en("Instrument mit Subskalen: je Subskala ein Zusammenhang, Werte im Modell unten.",
                                          "Instrument with subscales: one relationship per subscale, values in the model below."))
        else:
            out["sem_paths"] = rows
            if len(rows) == 1:
                r = rows[0]
                out["effect"] = ("β", r["std"])
                if r.get("p") is not None and admissible:
                    out["p"] = r["p"]  # enters Holm's correction (audit 2026-10-06, SEM-1)
                    out["verdict"] = _verdict(r["p"], r["std"], h.get("direction", ""))
                    lo, hi = r["ci"]
                    z = f", z = {_num(r['z'], lang)}" if r.get("z") is not None else ""
                    out["sentence"] = f"β = {_num(r['std'], lang)}, {_ci(lo, hi, lang)}{z}, {_p(r['p'], lang)}"
                elif result.get("method") == "pls":
                    out["notes"].append(de_en("PLS prüft Pfade nur mit dem Bootstrap (unten „Bootstrap rechnen“).",
                                              "PLS tests paths only with the bootstrap (below “Run bootstrap”)."))
            elif rows:  # subscales or more than two groups: the paths tested together (stage 3)
                test = joint_test(result, entry.get("paths") or [], boot)
                if test and admissible:
                    out["p"] = test["p"]
                    out["verdict"] = _verdict(test["p"], 1.0, "")
                    out["sentence"] = ("; ".join(f"β({r['from']}) = {_num(r['std'], lang)}" for r in rows) + "; "
                                       + (f"χ²({test['df']}) = {_num(test['chi2'], lang)}, {_p(test['p'], lang)}"
                                          if "chi2" in test else f"{_p(test['p'], lang)} (Bonferroni)"))
                out["notes"].append(de_en("{k} Pfade (Subskalen oder Gruppen gegen die erste) gemeinsam geprüft{how}.",
                                          "{k} paths (subscales or groups against the first) tested together{how}.",
                                          k=len(rows), how=de_en(" (Bonferroni)", " (Bonferroni)")
                                          if result.get("method") == "pls" else de_en(" (Wald-Test)", " (Wald test)")))
        if not admissible and fit:
            out["notes"].append(de_en("Die Lösung ist nicht zulässig (siehe Modell unten): kein Urteil.",
                                      "The solution is not admissible (see the model below): no verdict."))
        out["beta"] = True  # "Strukturgleichungsmodell, β standardisiert" beside the variables (2026-10-17)
    return hyps


def _slope_points(result, mod):
    """Where the slopes are taken, [(key, label, shift of w from its mean in the units of the data)]: one SD below and
    above the mean of w (model-implied SD), or for a moderator with two groups coded 0/1 at w = 0 and w = 1 (Hayes,
    2015; audit 2026-10-12, decision 8). key: "low"/"high" or "g0"/"g1"."""
    binary = (result.get("binary") or {}).get(mod["w"])
    if binary:
        return [(f"g{g}", binary["labels"][g], g - binary["mean"]) for g in (0, 1)]
    sd = ((result.get("fit") or {}).get("latent_sd") or {}).get(mod["w"])
    if sd is None:
        return None
    return [("low", "−1 SD", -sd), ("high", "+1 SD", sd)]


def simple_slopes(result, mod, boot=None):
    """The standardised slopes of x -> y where w lies one SD below and above its mean, or in each group of a 0/1
    moderator (_slope_points): [(key, label, β)] or None. From the model-implied SDs (audit 2026-10-12, SEM-4 and
    decision 8): the unstandardised slope b_x + b_xw · shift, standardised with SD(x) / SD(y), written with the
    standardised paths as β_x + β_xw · shift · SD(x) / SD(x·w). Before, SD(x·w) was taken as SD(x)·SD(w)·√(1 + r²)
    (bivariate normal); the model estimates that variance freely."""
    main = path_row(result, mod["x"], mod["y"], boot)
    inter = path_row(result, mod["xw"], mod["y"], boot)
    sd = (result.get("fit") or {}).get("latent_sd") or {}
    points = _slope_points(result, mod)
    if not main or not inter or not points or not sd.get(mod["x"]) or not sd.get(mod["xw"]):
        return None
    per_unit = inter["std"] * sd[mod["x"]] / sd[mod["xw"]]
    return [(key, label, main["std"] + shift * per_unit) for key, label, shift in points]


def _slope_label(key, label, w):
    """The name of a slope in the page's language: "w low (−1 SD)" or "w: group"."""
    if key == "low":
        return de_en("{w} niedrig (−1 SD)", "{w} low (−1 SD)", w=w)
    if key == "high":
        return de_en("{w} hoch (+1 SD)", "{w} high (+1 SD)", w=w)
    return de_en("{w} = „{g}“", "{w} = “{g}”", w=w, g=label)


def _slope_sentence(slopes, lang):
    return ", ".join(f"{_num(v, lang)} ({label})" for _, label, v in slopes)


def _moderation_result(number, out, entry, result, h, boot, lang, admissible):
    """The result of a hypothesis "Moderation" (F2): the interaction term's path (several: tested together, stage 3),
    the comparison of the path between groups, or the index of a moderated mediation (stage 3)."""
    names = result.get("names") or result.get("nodes") or {}

    def label(v):
        return names.get(v, (v, v))[1]

    if entry.get("groups"):
        g = (result.get("group_fits") or {}).get(number) or {"error": "estimate"}
        out["sem_groups"] = g
        labels = g.get("labels") or ["0", "1"]
        if g.get("error") == "few_groups":
            out["notes"].append(de_en("Zu wenige vollständige Teilnahmen je Gruppe: {counts}, nötig je {need} "
                                      "(Festlegung von Askurious).",
                                      "Too few complete participations per group: {counts}, {need} needed in each "
                                      "(set by Askurious).",
                                      counts=", ".join(f"{n} ({a})" for n, a in zip(g["n"], labels)), need=GROUP_MIN))
            return
        if g.get("error"):
            out["notes"].append(de_en("Kein Ergebnis: ", "No result: ") + error_text({"error": g["error"]}))
            return
        out["n"] = sum(g["n"])
        pairs = g["pairs"]
        two = len(labels) == 2 and len(pairs) == 1
        if two:
            diff = g["diff"][pairs[0]]
            out["effect"] = ("Δb", diff["est"])
            p, value = diff["p"], diff["est"]
        else:  # several groups or paths: one joint test (the same paths in all groups)
            tests = [g["joint"][pair] for pair in pairs]
            ps = [t["p"] for t in tests if t.get("p") is not None]
            p, value = (min(1.0, min(ps) * len(ps)) if ps else None), None
        if p is not None and g.get("admissible"):
            out["p"] = p
            out["verdict"] = _verdict(p, value if value is not None else 1.0, h.get("direction", "") if two else "")
        elif not g.get("admissible"):
            out["notes"].append(de_en("Die Lösung in den Gruppen ist nicht zulässig: kein Urteil.",
                                      "The solution in the groups is not admissible: no verdict."))
        betas = "; ".join(f"β({a}) = {_num(v, lang)}" for a, v in zip(labels, g["paths"][pairs[0]]))
        if two:
            diff = g["diff"][pairs[0]]
            out["sentence"] = (f"{betas}; Δb = {_num(diff['est'], lang)}, "
                               f"z = {_num(diff['z'], lang) if diff.get('z') is not None else '–'}, {_p(diff['p'], lang)}")
        else:
            t = g["joint"][pairs[0]]
            out["sentence"] = (f"{betas}; χ²({t['df']}) = {_num(t['chi2'], lang) if t.get('chi2') is not None else '–'}, "
                               f"{_p(p, lang)}" if len(pairs) == 1 else f"{betas}; {_p(p, lang)} (Bonferroni)")
        out["notes"].append(de_en("Moderation durch Gruppen: dasselbe Modell in jeder Gruppe mit gleichen Ladungen, "
                                  "verglichen wird der Pfad {x} → {y} (Wald-Test{more}).",
                                  "Moderation by groups: the same model in every group with equal loadings, the path "
                                  "{x} → {y} is compared (Wald test{more}).",
                                  x=", ".join(label(v) for v in g["x"]), y=", ".join(label(v) for v in g["y"]),
                                  more=(de_en("; positiv heißt: Der Pfad ist in „{b}“ größer als in „{a}“",
                                              "; positive means the path is larger in “{b}” than in “{a}”", a=labels[0],
                                              b=labels[1]) if two else
                                        de_en(", gleicher Pfad in allen {k} Gruppen", ", the same path in all {k} groups",
                                              k=len(labels)))))
        return
    mods = entry.get("moderations") or []
    rows = [(m, path_row(result, m["xw"], m["y"], boot)) for m in mods]
    rows = [(m, r) for m, r in rows if r]
    out["sem_paths"] = [{"from": label(m["xw"]), "to": label(m["y"]), **r} for m, r in rows]
    if not rows:
        return
    if entry.get("modmed"):  # moderated mediation (Hayes, 2015)
        idx = modmed_index(result, entry, boot)
        out["sem_modmed"] = idx
        if idx:
            out["effect"] = ("ab", idx["value"])
            if idx.get("p") is not None and admissible:
                out["p"] = idx["p"]
                out["verdict"] = _verdict(idx["p"], idx["value"], h.get("direction", ""))
            sentence = (f"{'index of moderated mediation' if lang == 'en' else 'Index der moderierten Vermittlung'} = "
                        f"{_num(idx['value'], lang)}")
            if idx.get("ci"):
                lo, hi = idx["ci"]
                sentence += ", " + _ci(lo, hi, lang) + (" (bootstrap)" if lang == "en" else " (Bootstrap)") + ", " + _p(idx["p"], lang)
            if idx.get("conditional"):
                sentence += (("; conditional indirect effects " if lang == "en" else "; bedingte indirekte Effekte ")
                             + _slope_sentence(idx["conditional"], lang))
            out["sentence"] = sentence
            if not idx.get("ci"):
                out["notes"].append(de_en("Das Urteil über die moderierte Vermittlung braucht den Bootstrap (unten "
                                          "„Bootstrap rechnen“).", "The verdict on the moderated mediation needs the "
                                          "bootstrap (below “Run bootstrap”)."))
        out["notes"].append(de_en("Moderierte Vermittlung: Urteil über den Index der moderierten Vermittlung (Produkt "
                                  "des Wechselwirkungspfads und des anderen Pfads der Vermittlung) mit seinem "
                                  "Bootstrap-Intervall (Hayes, 2015).", "Moderated mediation: verdict on the index of "
                                  "moderated mediation (product of the interaction path and the other path of the "
                                  "mediation) with its bootstrap interval (Hayes, 2015)."))
        return
    if len(rows) == 1:
        mod, row = rows[0]
        out["effect"] = ("β", row["std"])
        slopes = simple_slopes(result, mod, boot)
        out["sem_slopes"] = slopes
        main = path_row(result, mod["x"], mod["y"], boot)
        if main and main["std"] < 0 and h.get("direction") in ("pos", "neg"):  # the sign, not the strength (SEM-11)
            out["notes"].append(de_en("Der Pfad {x} → {y} ist negativ (β = {b}): Eine positive Wechselwirkung macht ihn "
                                      "dem Betrag nach schwächer, eine negative stärker. Das Urteil prüft das Vorzeichen "
                                      "der Wechselwirkung.", "The path {x} → {y} is negative (β = {b}): a positive "
                                      "interaction makes it weaker in size, a negative one stronger. The verdict tests "
                                      "the sign of the interaction.", x=label(mod["x"]), y=label(mod["y"]),
                                      b=_num(main["std"], lang)))
        if row.get("p") is not None and admissible:
            out["p"] = row["p"]
            out["verdict"] = _verdict(row["p"], row["std"], h.get("direction", ""))
            lo, hi = row["ci"]
            z = f", z = {_num(row['z'], lang)}" if row.get("z") is not None else ""
            out["sentence"] = f"β = {_num(row['std'], lang)}, {_ci(lo, hi, lang)}{z}, {_p(row['p'], lang)}"
            if slopes:
                out["sentence"] += (("; simple slopes β = " if lang == "en" else "; einfache Steigungen β = ")
                                    + _slope_sentence(slopes, lang))
            jn = jn_text(jn_region(result, mod), label(mod["w"]))
            if jn:  # Johnson-Neyman (Hayes, 2022): where the path is significant
                out["notes"].append(de_en("Johnson-Neyman: ", "Johnson-Neyman: ") + jn)
        elif result.get("method") == "pls":
            out["notes"].append(de_en("PLS prüft Pfade nur mit dem Bootstrap (unten „Bootstrap rechnen“).",
                                      "PLS tests paths only with the bootstrap (below “Run bootstrap”)."))
    else:  # several interaction paths (subscales, groups): tested together
        test = joint_test(result, [(m["xw"], m["y"]) for m in mods], boot)
        if test and admissible:
            out["p"] = test["p"]
            out["verdict"] = _verdict(test["p"], 1.0, "")
            out["sentence"] = (f"χ²({test['df']}) = {_num(test['chi2'], lang)}, {_p(test['p'], lang)}" if "chi2" in test
                               else f"{_p(test['p'], lang)} (Bonferroni)")
        out["notes"].append(de_en("{k} Wechselwirkungspfade (Subskalen oder Gruppen) gemeinsam geprüft{how}; die einzelnen "
                                  "Pfade unter „Moderationen“.", "{k} interaction paths (subscales or groups) tested "
                                  "together{how}; the single paths under “Moderations”.", k=len(rows),
                                  how=de_en(" (Bonferroni)", " (Bonferroni)") if result.get("method") == "pls" else
                                  de_en(" (Wald-Test)", " (Wald test)")))
    if result.get("method") == "pls":
        out["notes"].append(de_en("Wechselwirkung zweistufig: Produkt der standardisierten Konstruktwerte (Hair et al., 2022).",
                                  "Interaction in two stages: product of the standardised construct scores (Hair et al., 2022)."))
    else:
        mod = rows[0][0]
        out["notes"].append(de_en("Wechselwirkung über Produktindikatoren, doppelt zentriert (Marsh et al., 2004; Lin et "
                                  "al., 2010); einfache Steigungen von {x} → {y}, wo {w} eine SD unter und über dem "
                                  "Mittel liegt.", "Interaction through product indicators, double mean centred (Marsh "
                                  "et al., 2004; Lin et al., 2010); simple slopes of {x} → {y} where {w} lies one SD below "
                                  "and above its mean.", x=label(mod["x"]), y=label(mod["y"]), w=label(mod["w"])))


ERRORS = {
    "missing": ("fehlt im Fragebogen", "is missing in the questionnaire"),
    "groups": ("hat weniger als zwei Gruppen", "has fewer than two groups"),
    "groups_role": ("hat mehr als zwei Gruppen; so eine Größe erklärt im Modell (je Gruppe ein Pfad gegen die erste), "
                    "wird aber nicht selbst erklärt und vermittelt nicht",
                    "has more than two groups; such a variable explains in the model (one path per group against the "
                    "first) but is not explained itself and does not mediate"),
    "level": ("ist weder Skala noch Zahl", "is neither a scale nor a number"),
    "loop": ("liegt in einer Schleife (je Durchgang eine Antwort); Skalen in Schleifen bleiben draußen",
             "is in a loop (one answer per pass); scales in loops stay out"),
    "single": ("hat eine Subskala mit nur einer Aussage; ein Konstrukt braucht mindestens zwei",
               "has a subscale with only one statement; a construct needs at least two"),
    "higher_moderation": ("", ""),
    "control_explained": ("wird im Modell selbst erklärt und kann deshalb keine Kontrollvariable sein",
                          "is explained in the model itself and so cannot be a control variable"),
}


def control_notes(result):
    """The lines under the diagram about the settings of the model: the control variables, single questions with a
    fixed reliability and control variables that cannot enter (SEM extensions 2026-10-17, points 6 and 7)."""
    out = []
    names = result.get("names") or result.get("nodes") or {}
    codes = list(dict.fromkeys((result.get("controls") or {}).values()))
    if codes:
        out.append(de_en("Kontrolliert für {c}: je ein Pfad auf jede erklärte Größe, ohne Hypothese und ohne Korrektur "
                         "nach Holm; nicht im Diagramm, die Pfade in der Tabelle.",
                         "Controlled for {c}: one path onto every explained variable, without a hypothesis and outside "
                         "Holm's correction; not in the diagram, the paths in the table.", c=", ".join(codes)))
    single = result.get("single") or {}
    if single:
        shown = ", ".join(f"{(names.get(c) or (c, c))[0]} ({_num(r, 'en' if english() else 'de')})" for c, r in single.items())
        out.append(de_en("Einzelfragen mit angenommener Reliabilität: {c}; ihr Messfehler (1 − Reliabilität ihrer Varianz) "
                         "ist festgelegt, so werden ihre Pfade nicht durch Messfehler verkleinert.",
                         "Single questions with an assumed reliability: {c}; their measurement error (1 − reliability of "
                         "their variance) is fixed, so their paths are not shrunk by measurement error.", c=shown))
    for code, key in result.get("control_errors") or []:
        de, en = ERRORS.get(key, ERRORS["level"])
        out.append(de_en("Kontrollvariable „{code}“ nicht im Modell: {why}.",
                         "Control variable “{code}” not in the model: {why}.", code=code, why=en if english() else de))
    return out


def _error_note(errors):
    parts = []
    for e in errors:
        if e == "roles":
            parts.append(de_en("Einfluss-, Ziel- und (bei Vermittlung) Mittlergröße müssen verschiedene Fragen sein und "
                               "gewählt sein", "predictor, outcome and (for mediation) mediator must be chosen and be "
                               "different questions"))
        elif e == "parts":
            parts.append(de_en("eine Moderation mit einem Instrument mit Subskalen kommt später",
                               "a moderation with an instrument with subscales comes later"))
        elif e == "explained":
            parts.append(de_en("Einflussgröße und Moderator dürfen im Modell nicht selbst erklärt werden (kein Pfeil "
                               "auf sie); das kommt später", "predictor and moderator must not be explained in the model "
                               "themselves (no arrow onto them); that comes later"))
        elif e == "higher_moderation":
            parts.append(de_en("eine Moderation mit einem Faktor zweiter Ordnung kommt später",
                               "a moderation with a second-order factor comes later"))
        elif e == "both":
            parts.append(de_en("zwischen denselben beiden Größen steht schon ein Pfad; Zusammenhang ohne Richtung und "
                               "Pfad zugleich lassen sich nicht schätzen", "a path already links the same two "
                               "variables; a relationship without a direction and a path at once cannot be estimated"))
        else:
            code, key = e
            de, en = ERRORS.get(key, ERRORS["level"])
            parts.append(de_en("„{code}“ {why}", "“{code}” {why}", code=code, why=en if english() else de))
    return de_en("Nicht im Modell: {why}.", "Not in the model: {why}.", why="; ".join(parts))


# --- the tables of the block -------------------------------------------------------------------------------------

# orientation marks of the measurement model, not verdicts: AVE (Fornell & Larcker, 1981), composite reliability
# (Hair et al., 2022), HTMT (Henseler et al., 2015, the stricter of their two bounds) and the fit indices (Hu &
# Bentler, 1999)
AVE_MIN, CR_MIN, HTMT_MAX = 0.5, 0.7, 0.85
CFI_MIN, RMSEA_MAX, SRMR_MAX = 0.95, 0.06, 0.08


def path_table(result, boot=None):
    """[{"from", "to", "hyps": [numbers], "std", "ci", "p", "boot_ci", "sig"}] of every path of the model, the
    hypotheses that named it first; the interval of PLS and of an indirect effect from the bootstrap."""
    names = result.get("nodes") or {}
    by_path = {}
    for n, entry in sorted((result.get("hyps") or {}).items()):
        for pair in entry.get("paths") or []:
            by_path.setdefault(tuple(pair), []).append(n)
        for pair in entry.get("direct") or []:
            by_path.setdefault(tuple(pair), [])
    rows = []
    for a, b in (result.get("spec") or {}).get("paths") or []:
        row = path_row(result, a, b, boot)
        if row is None:
            continue
        fit = result.get("fit") or {}
        rows.append({"from": names.get(a, (a, a))[1], "to": names.get(b, (b, b))[1], "hyps": by_path.get((a, b), []),
                     "std": row["std"], "ci": row.get("ci"), "p": row.get("p"), "boot_ci": row.get("boot_ci"),
                     "sig": row.get("p") is not None and row["p"] < 0.05,
                     # PLS (point 13): the effect size f² and the collinearity of the predictors of the target
                     "f2": (fit.get("f2") or {}).get((a, b)), "vif": (fit.get("vif") or {}).get((a, b)),
                     "control": a in (result.get("controls") or {}) and not by_path.get((a, b))})
    # the relations without a direction of the hypotheses (F4), as "A ↔ B" with their (residual) correlation
    by_cov = {}
    for n, entry in sorted((result.get("hyps") or {}).items()):
        for pair in entry.get("cov") or []:
            by_cov.setdefault(tuple(pair), []).append(n)
    for a, b in (result.get("spec") or {}).get("cov") or []:
        row = cov_row(result, a, b, boot)
        if row is None:
            continue
        rows.append({"from": names.get(a, (a, a))[1], "to": names.get(b, (b, b))[1], "hyps": by_cov.get((a, b), []),
                     "std": row["std"], "ci": row.get("ci"), "p": row.get("p"), "boot_ci": row.get("boot_ci"),
                     "sig": row.get("p") is not None and row["p"] < 0.05, "cov": True, "residual": row["residual"]})
    return rows


def moderation_table(result, boot=None):
    """[{"hyps", "label", "kind": "product" | "groups" | "modmed", "std" | "diff", "ci", "p", "lines": [(label,
    slope)], "plot": {"lines": [...]}, "error"}] of the moderations (F2, stage 3): each interaction term with its
    simple slopes, the path in each group with their difference or joint test, the index of a moderated mediation."""
    names = result.get("names") or result.get("nodes") or {}

    def label(v):
        return names.get(v, (v, v))[1]

    rows = []
    for n, entry in sorted((result.get("hyps") or {}).items()):
        grp = entry.get("groups")
        for mod in entry.get("moderations") or []:
            row = path_row(result, mod["xw"], mod["y"], boot)
            if row is None:
                continue
            slopes = simple_slopes(result, mod, boot)
            lines = [(_slope_label(key, text, label(mod["w"])), v) for key, text, v in slopes or []]
            out = {"hyps": [n], "kind": "product", "label": f"{label(mod['x'])} → {label(mod['y'])}",
                   "by": label(mod["w"]), "std": row["std"], "ci": row.get("ci"), "p": row.get("p"),
                   "boot_ci": row.get("boot_ci"), "lines": lines, "plot": _slope_plot(lines),
                   "jn": jn_text(jn_region(result, mod), label(mod["w"]))}
            if entry.get("modmed"):
                idx = modmed_index(result, entry, boot)
                if idx:
                    out["modmed"] = idx
                    if idx.get("conditional"):
                        out["mm_lines"] = [(_slope_label(key, text, label(mod["w"])), v) for key, text, v in idx["conditional"]]
            rows.append(out)
        if grp:
            g = (result.get("group_fits") or {}).get(n) or {"error": "estimate"}
            for pair in g.get("pairs") or [(grp["x"][0], grp["y"][0])]:
                out = {"hyps": [n], "kind": "groups", "label": f"{label(pair[0])} → {label(pair[1])}",
                       "by": label(grp["w"])}
                if g.get("error"):
                    out["error"] = (de_en("zu wenige vollständige Teilnahmen je Gruppe ({counts}, nötig je {need})",
                                          "too few complete participations per group ({counts}, {need} needed in each)",
                                          counts=", ".join(str(k) for k in g["n"]), need=GROUP_MIN)
                                    if g["error"] == "few_groups" else error_text({"error": g["error"]}))
                else:
                    lines = list(zip(g["labels"], g["paths"][pair]))
                    diff, joint = g["diff"][pair], g["joint"][pair]
                    out.update(diff=diff["est"] if len(lines) == 2 else None, se=diff["se"], z=diff["z"],
                               p=diff["p"] if len(lines) == 2 else joint["p"], chi2=joint.get("chi2"), df=joint.get("df"),
                               n=g["n"], lines=lines, plot=_slope_plot(lines))
                rows.append(out)
                if g.get("error"):
                    break
    return rows


def _slope_plot(lines, width=220, height=120):
    """Lines of the slopes from x = −1 SD to +1 SD in a small picture (y in SD of the outcome, centred)."""
    if not lines:
        return None
    top = max(0.5, max(abs(s) for _, s in lines) * 1.15)
    pad = 10

    def y(v):
        return round(height / 2 - v / top * (height / 2 - pad), 1)

    return {"width": width, "height": height,
            "lines": [{"label": text, "x1": pad, "y1": y(-s), "x2": width - pad, "y2": y(s), "slope": s}
                      for text, s in lines]}


def indirect_table(result, boot=None):
    names = result.get("nodes") or {}
    rows = []
    for n, entry in sorted((result.get("hyps") or {}).items()):
        for t in entry.get("indirect") or []:
            row = indirect_row(result, *t, boot=boot)
            if row:
                rows.append({"hyps": [n], "path": " → ".join(names.get(v, (v, v))[1] for v in t), "std": row["std"],
                             "delta_ci": row.get("ci") if result.get("method") != "pls" else None,
                             "boot_ci": row.get("boot_ci")})
    return rows


VIF_MAX = 5.0  # collinearity: from a VIF of 5 critical (Hair et al., 2022); below 3 ideal
F2_STEPS = (0.02, 0.15, 0.35)  # small, medium, large (Cohen, 1988)


def f2_word(v):
    """The size of an f² in words (Cohen, 1988)."""
    if v is None:
        return ""
    if v >= F2_STEPS[2]:
        return de_en("groß", "large")
    if v >= F2_STEPS[1]:
        return de_en("mittel", "medium")
    if v >= F2_STEPS[0]:
        return de_en("klein", "small")
    return de_en("kaum", "negligible")


def r2_table(result):
    """[{"label", "r2", "r2_adj"}] of the explained variables (PLS: with the adjusted R², point 13)."""
    fit = result.get("fit") or {}
    names = result.get("nodes") or {}
    adj = fit.get("r2_adj") or {}
    return [{"label": names.get(v, (v, v))[1], "r2": r2, "r2_adj": adj.get(v)} for v, r2 in (fit.get("r2") or {}).items()
            if v in names]


def formative_table(result, boot=None):
    """[{"label", "items": [{"code", "text", "weight", "loading", "vif", "ci", "p"}]}] of the formative constructs
    (PLS mode B, point 9): weights with their bootstrap interval and p, loadings, the VIF of each indicator."""
    fit = result.get("fit") or {}
    names = result.get("nodes") or {}
    statements = result.get("statements") or {}
    found = (boot or {}).get("weights") or {}
    out = []
    for lv, vifs in (fit.get("formative") or {}).items():
        items = []
        for r, vif in zip((fit.get("loadings") or {}).get(lv) or [], vifs):
            b = found.get((lv, r["item"]))
            p = sem._normal_p(r["weight"] / b["se"]) if b and b.get("se") else None
            items.append({"code": r["item"], "text": statements.get(r["item"], ""), "weight": r["weight"],
                          "loading": r["std"], "vif": vif, "ci": b["ci"] if b else None, "p": p,
                          "high_vif": vif is not None and vif >= VIF_MAX})
        out.append({"label": names.get(lv, (lv, lv))[1], "items": items})
    return out


def htmt_interval(result, boot=None):
    """{(label a, label b): upper bound} of the one-sided 95 % bootstrap interval of each HTMT (Franke & Sarstedt,
    2019): below 0.85 the two constructs are told apart (point 13). Empty without a bootstrap."""
    names = result.get("nodes") or {}
    return {(names.get(a, (a, a))[1], names.get(b, (b, b))[1]): v["upper95"]
            for (a, b), v in ((boot or {}).get("htmt") or {}).items() if v.get("upper95") is not None}


PREDICT_WORDS = {"high": ("hoch", "high"), "medium": ("mittel", "medium"), "low": ("gering", "low"),
                 "none": ("keine", "none")}


def predict_table(result):
    """PLSpredict for the page (point 13): {"items": [...], "constructs": [(label, word)], "folds"} or None."""
    found = result.get("predict")
    if not found:
        return None
    names = result.get("nodes") or {}
    statements = result.get("statements") or {}
    words = {k: (v[1] if english() else v[0]) for k, v in PREDICT_WORDS.items()}
    return {"items": [{**i, "text": statements.get(i["item"], ""), "label": names.get(i["construct"], (i["construct"],) * 2)[1],
                       "better": i["rmse"] < i["rmse_lm"]} for i in found["items"]],
            "constructs": [(names.get(v, (v, v))[1], words[w]) for v, w in found["constructs"].items()],
            "folds": found.get("folds")}


def measurement_table(result):
    """[{"name", "label", "items": [{"code", "text", "std"}], "ave", "cr", "htmt_max", "flags"}] and the HTMT
    pairs [(a, b, value)]."""
    fit = result.get("fit") or {}
    quality = fit.get("quality") or {}
    names = result.get("nodes") or {}
    statements = result.get("statements") or {}
    htmt = quality.get("htmt") or {}
    out = []
    products = (result.get("spec") or {}).get("products") or {}
    htmt = {pair: v for pair, v in htmt.items() if not set(pair) & set(products)}
    # an interaction term, a single question, a formative construct (its own table): not here
    single = set((result.get("spec") or {}).get("single") or {}) | set((result.get("spec") or {}).get("formative") or ())
    for lv, rows in (fit.get("loadings") or {}).items():
        if lv in products or lv in single:
            continue
        mine = [v for (a, b), v in htmt.items() if lv in (a, b) and v is not None]
        ave, cr = quality.get("ave", {}).get(lv), quality.get("cr", {}).get(lv)
        flags = []
        if ave is not None and ave < AVE_MIN:
            flags.append("ave")
        if cr is not None and cr < CR_MIN:
            flags.append("cr")
        if mine and max(mine) > HTMT_MAX:
            flags.append("htmt")
        out.append({"name": lv, "label": names.get(lv, (lv, lv))[1],
                    "items": [{"code": r["item"], "text": statements.get(r["item"], ""), "std": r["std"],
                               "weight": r.get("weight")} for r in rows],
                    "ave": ave, "cr": cr, "htmt_max": max(mine) if mine else None, "flags": flags})
    for h, rows in (fit.get("higher") or {}).items():  # second order (point 8): the subscales as its indicators
        out.append({"name": h, "label": de_en("{h} (zweite Ordnung)", "{h} (second order)", h=names.get(h, (h, h))[1]),
                    "items": [{"code": names.get(r["item"], (r["item"], r["item"]))[1], "text": "", "std": r["std"]}
                              for r in rows], "ave": None, "cr": None, "htmt_max": None, "flags": []})
    pairs = [(names.get(a, (a, a))[1], names.get(b, (b, b))[1], v) for (a, b), v in htmt.items()]
    return out, pairs


def weak_measurement(table):
    """The constructs of measurement_table whose measurement does not carry the paths (UI/UX audit 2026-10-09, X54):
    CR under CR_MIN or a statement with a negative loading (often a statement not reversed).
    [{"label", "cr", "low": bool, "negative": [codes]}], empty if every construct holds."""
    out = []
    for c in table:
        negative = [i["code"] for i in c["items"] if i.get("std") is not None and i["std"] < 0]
        if "cr" in c["flags"] or negative:
            out.append({"label": c["label"], "cr": c["cr"], "low": "cr" in c["flags"], "negative": negative})
    return out


def fit_marks(fit):
    """{"cfi": True/False, ...}: whether each index meets the orientation of Hu & Bentler (1999)."""
    if not fit or fit.get("method") != "ml" or not fit.get("df"):
        return {}
    rob = fit.get("robust_fit") or {}
    return {"cfi": fit.get("cfi") is not None and fit["cfi"] >= CFI_MIN,
            "tli": fit.get("tli") is not None and fit["tli"] >= CFI_MIN,
            "rmsea": fit.get("rmsea") is not None and fit["rmsea"] <= RMSEA_MAX,
            "srmr": fit.get("srmr") is not None and fit["srmr"] <= SRMR_MAX,
            "cfi_r": rob.get("cfi") is not None and rob["cfi"] >= CFI_MIN,
            "tli_r": rob.get("tli") is not None and rob["tli"] >= CFI_MIN,
            "rmsea_r": rob.get("rmsea") is not None and rob["rmsea"] <= RMSEA_MAX}


def residual_table(result):
    """The largest residual correlations of the covariance-based model (sem.RESIDUAL_TOP), [{"a", "b", "text_a",
    "text_b", "value", "large"}] with the statements' texts; large: above sem.RESIDUAL_MARK. Empty for PLS."""
    fit = result.get("fit") or {}
    if fit.get("method") != "ml":
        return []
    statements = result.get("statements") or {}
    products = (result.get("spec") or {}).get("products") or {}
    made = {i for items in (result.get("spec") or {}).get("latent", {}).values() for i in items if TIMES in i}
    out = []
    for a, b, v in fit.get("residuals") or []:
        if a in made or b in made or a in products or b in products:  # product indicators are not questions
            continue
        out.append({"a": a, "b": b, "text_a": statements.get(a, ""), "text_b": statements.get(b, ""), "value": v,
                    "large": round(abs(v), 2) > sem.RESIDUAL_MARK})  # as shown: −0,10 is not above 0,10
    return out[:5]


def jn_region(result, mod):
    """The Johnson-Neyman region of a moderation by a number or construct (covariance-based): where w lies (in SD of
    w from its mean, model-implied SD) the path x -> y is significant at .05. {"kind", "bounds": [SD, ...]} (see
    sem.slope_region) or None (PLS, a 0/1 moderator, no errors)."""
    fit = result.get("fit") or {}
    if fit.get("method") != "ml" or (result.get("binary") or {}).get(mod["w"]):
        return None
    sd = (fit.get("latent_sd") or {}).get(mod["w"])
    pairs = [(mod["x"], mod["y"]), (mod["xw"], mod["y"])]
    v = sem.path_cov(fit, pairs)
    est = {(r["from"], r["to"]): r["est"] for r in fit.get("paths") or []}
    if v is None or not sd or any(p not in est for p in pairs):
        return None
    found = sem.slope_region(est[pairs[0]], est[pairs[1]], v[0, 0], v[0, 1], v[1, 1])
    return {"kind": found["kind"], "bounds": [b / sd for b in found["bounds"]]}


JN_RANGE = 3.0  # Johnson-Neyman bounds beyond ±3 SD of the moderator are named as such: hardly anyone lies there


def jn_text(region, w):
    """The Johnson-Neyman region in words for the page."""
    if not region:
        return ""

    def at(v):
        shown = f"{v:+.2f}".replace("-", "−")
        if not english():
            shown = shown.replace(".", ",")
        return shown + (de_en(" (außerhalb ±3 SD)", " (beyond ±3 SD)") if abs(v) > JN_RANGE else "")

    kind, b = region["kind"], region["bounds"]
    if kind == "all":
        return de_en("Der Pfad ist für jeden Wert von {w} signifikant.", "The path is significant for every value of {w}.", w=w)
    if kind == "none":
        return de_en("Der Pfad ist für keinen Wert von {w} signifikant.", "The path is significant for no value of {w}.", w=w)
    if kind == "outside":
        return de_en("Der Pfad ist signifikant, wo {w} unter {a} SD oder über {b} SD liegt.",
                     "The path is significant where {w} lies below {a} SD or above {b} SD.", w=w, a=at(b[0]), b=at(b[1]))
    if kind == "inside":
        return de_en("Der Pfad ist signifikant, wo {w} zwischen {a} SD und {b} SD liegt.",
                     "The path is significant where {w} lies between {a} SD and {b} SD.", w=w, a=at(b[0]), b=at(b[1]))
    if kind == "above":
        return de_en("Der Pfad ist signifikant, wo {w} über {a} SD liegt.", "The path is significant where {w} lies above {a} SD.",
                     w=w, a=at(b[0]))
    return de_en("Der Pfad ist signifikant, wo {w} unter {a} SD liegt.", "The path is significant where {w} lies below {a} SD.",
                 w=w, a=at(b[0]))


def error_text(result):
    """Why there is no estimate, in words."""
    reason = result.get("error")
    if reason == "few":
        if result.get("method") == "pls":
            return de_en("Zu wenige vollständige Teilnahmen: {n} von mindestens {need} (Festlegung von Askurious: "
                         "mindestens {min}, und zehnmal so viele wie die meisten Pfade, die auf ein Konstrukt zeigen).",
                         "Too few complete participations: {n} of at least {need} (set by Askurious: at least {min}, "
                         "and ten times the largest number of paths pointing at one construct).",
                         n=result.get("n", 0), need=result.get("need"), min=MIN_N)
        return de_en("Zu wenige vollständige Teilnahmen: {n} von mindestens {need} (Festlegung von Askurious: "
                     "mindestens {min}, und {k} je frei geschätztem Parameter).",
                     "Too few complete participations: {n} of at least {need} (set by Askurious: at least {min}, and "
                     "{k} per freely estimated parameter).",
                     n=result.get("n", 0), need=result.get("need"), min=MIN_N, k=PER_PARAMETER)
    texts = {
        "covpath": ("Zwischen zwei Größen stehen ein Pfad und ein Zusammenhang ohne Richtung zugleich; das lässt sich "
                    "nicht schätzen.", "Two variables are linked by a path and by a relationship without a direction "
                    "at once; that cannot be estimated."),
        "converge": ("Die Schätzung hat keine Lösung gefunden (keine Konvergenz). Oft hilft ein Konstrukt mit mehr "
                     "Aussagen oder ein einfacheres Modell.", "The estimation found no solution (no convergence). A "
                     "construct with more statements or a simpler model often helps."),
        "cyclic": ("Die Hypothesen bilden einen Kreis (A wirkt auf B und B auf A); das Modell schätzt nur Wirkungen "
                   "in eine Richtung.", "The hypotheses form a circle (A affects B and B affects A); the model only "
                   "estimates effects in one direction."),
        "singular": ("Zwei Spalten des Modells hängen genau voneinander ab (z. B. dieselbe Aussage zweimal).",
                     "Two columns of the model depend exactly on each other (e.g. the same statement twice)."),
        "constant": ("Eine Aussage oder Frage des Modells streut nicht: Alle antworten gleich.",
                     "A statement or question of the model does not vary: everybody answers the same."),
        "indicators": ("Ein Konstrukt braucht mindestens zwei Aussagen.", "A construct needs at least two statements."),
        "ordinal_metric": ("Für geordnete Kategorien (WLSMV) darf jede Frage des Modells höchstens zehn verschiedene "
                           "Antworten haben; hier hat eine mehr (z. B. ein Alter in Jahren). Im Studienplan „als Zahlen "
                           "(ML)“ wählen oder die Frage aus dem Modell nehmen.",
                           "For ordered categories (WLSMV) every question of the model may have at most ten different "
                           "answers; one here has more (e.g. an age in years). Choose “as numbers (ML)” in the research "
                           "plan or take the question out of the model."),
        "ordinal_model": ("Geordnete Kategorien (WLSMV) gehen nicht mit Einzelfragen mit Reliabilität, formativen "
                          "Konstrukten oder einer einzelnen Frage, die erklärt wird und selbst weiter wirkt (Mittler).",
                          "Ordered categories (WLSMV) do not go with single questions with a reliability, formative "
                          "constructs or a single question that is explained and acts further itself (mediator)."),
        "extreme": ("Die Schätzung ist gekippt: Sie liefert unmögliche Werte (standardisierte Pfade weit über 1, ein R² "
                    "unter 0 oder ein negatives χ²). Meist sind die Antworten ohne Zusammenhänge, etwa Test- oder "
                    "Zufallsdaten, oder zwei Einflussgrößen messen fast dasselbe. Mit echten Daten oder einem "
                    "einfacheren Modell neu rechnen.",
                    "The estimate has collapsed: it gives impossible values (standardised paths far above 1, an R² "
                    "below 0 or a negative χ²). Usually the answers have no relations, e.g. test or random data, or "
                    "two predictors measure almost the same. Compute again with real data or a simpler model."),
    }
    de, en = texts.get(reason, ("Das Modell ließ sich nicht schätzen.", "The model could not be estimated."))
    return en if english() else de


# --- the model as an R script (lavaan, seminr) -------------------------------------------------------------------

def _r_name(text):
    import re

    name = re.sub(r"[^A-Za-z0-9_]", "_", text)
    return name if name[:1].isalpha() else "v_" + name


_R_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def _r_str(text):
    r"""A text as an R string literal in double quotes: backslash and quote escaped, line breaks and tabs written as
    \n \r \t, other control characters left out. Answer codes and group names come from the editor or an
    imported survey file; put in raw, `2", system("…"), "x` ran as R code in the researcher's R session
    (audit 2026-10-13, DA-1)."""
    out = []
    for ch in str(text):
        if ch in _R_ESCAPES:
            out.append(_R_ESCAPES[ch])
        elif ch >= " " and ch != "\x7f":
            out.append(ch)
    return '"' + "".join(out) + '"'


def _r_comment(text):
    """A text for an R comment (also inside the single-quoted lavaan model): one line, without control characters,
    quotes or backslashes that would end the comment or the model string (audit 2026-10-13, DA-1)."""
    text = " ".join(str(text).split())
    return "".join(ch for ch in text if ch >= " " and ch not in "\x7f'\"\\`")


def _r_unique(keys, make, used=()):
    """{key: R name} with every name once: names that only differ in characters R does not take ("Größe" and
    "Grüße" both give "Gr__e") get a number (audit 2026-10-12, SEM-10 c). used: names already taken."""
    out, taken = {}, set(used)
    for key in keys:
        base = name = make(key)
        k = 2
        while name in taken:
            name = f"{base}_{k}"
            k += 1
        taken.add(name)
        out[key] = name
    return out


def _r_lines(spec, names, mark=None, number=None):
    """The lavaan lines of a model: measurement, regressions with labels p1, p2, ... (the paths `mark` of a
    moderation by groups with one label per group instead: {(x, y): [labels]}), the free covariances of the exogenous
    variables, the covariances of relations without a direction. ({"lines", "labels"}.)"""
    out = []
    for lv, items in spec["latent"].items():
        out.append(f"  {names[lv]} =~ " + " + ".join(_r_name(i) for i in items))
    for h, parts in (spec.get("higher") or {}).items():  # second order (point 8)
        out.append(f"  {names[h]} =~ " + " + ".join(names[p] for p in parts))
    labels, k = {}, 0
    for target in dict.fromkeys(b for _, b in spec["paths"]):
        terms = []
        for a, b in spec["paths"]:
            if b == target:
                if mark and (a, b) in mark:
                    terms.append(f"c({', '.join(mark[(a, b)])})*{names[a]}")
                    continue
                k += 1
                labels[(a, b)] = f"p{k}"
                terms.append(f"p{k}*{names[a]}" if mark is None else names[a])
        out.append(f"  {names[target]} ~ " + " + ".join(terms))
    # the exogenous variables covary freely, as in the app; written out, with fixed.x = FALSE also between observed
    # ones and between a construct and an observed one (audit 2026-10-06, SEM-6)
    endo = sem.endogenous(spec) | sem.lower(spec)
    exo = [v for v in list(spec["latent"]) + list(spec.get("higher") or {}) + spec["observed"] if v not in endo]
    for i in range(len(exo)):
        for j in range(i + 1, len(exo)):
            out.append(f"  {names[exo[i]]} ~~ {names[exo[j]]}")
    # relations without a direction (F4): between exogenous ones written above, else between the residuals
    for a, b in spec.get("cov", ()):
        if a in endo or b in endo:
            out.append(f"  {names[a]} ~~ {names[b]}")
    return {"lines": out, "labels": labels}


def _r_columns(spec, observed):
    """The data columns of a model in R (indicators and observed variables), without the columns of interaction
    terms, which are built from them."""
    products = spec.get("products") or {}
    out = [_r_name(i) for lv, items in spec["latent"].items() if lv not in products for i in items]
    out += [observed.get(o) or _r_name(o) for o in spec["observed"] if o not in products]
    return list(dict.fromkeys(out))


def _r_invariance(survey, model, names, observed, en):
    """R lines for the test of measurement invariance (point 4): the measurement model in the groups, level by level
    (group.equal), compared with the scaled difference test, and the fit of each level."""
    from app import study_plan

    key = study_plan.plan_of(survey)["sem"]["invariance"]
    spec = model["spec"]
    gone = set(spec.get("products") or {}) | set(spec.get("single") or {}) | set(spec.get("formative") or ())
    latent = {lv: items for lv, items in spec["latent"].items() if lv not in gone}
    if not key or not latent:
        return []
    column = "lang" if key == study_plan.LANG_GROUPS else _r_name(key)
    items = [_r_name(i) for its in latent.values() for i in its]
    lines = ["", "# " + ("measurement invariance across the groups of the plan (configural, metric, scalar, strict); "
                         "groups with fewer than 50 complete cases are left out on the results page" if en else
                         "Messinvarianz über die Gruppen des Plans (konfigural, metrisch, skalar, strikt); Gruppen unter "
                         "50 vollständigen Fällen lässt die Ergebnisseite weg"),
             "messung <- '", *[f"  {names[lv]} =~ " + " + ".join(_r_name(i) for i in its) for lv, its in latent.items()], "'",
             f"daten_g <- daten[complete.cases(daten[, c({', '.join(_r_str(c) for c in items + [column])})]), ]",
             f"daten_g <- daten_g[daten_g[[{_r_str(column)}]] %in% names(which(table(daten_g[[{_r_str(column)}]]) >= 50)), ]"]
    steps = [("konfigural", "configural", "NULL"), ("metrisch", "metric", 'c("loadings")'),
             ("skalar", "scalar", 'c("loadings", "intercepts")'),
             ("strikt", "strict", 'c("loadings", "intercepts", "residuals")')]
    for de, eng, equal in steps:
        lines.append(f'fit_{eng} <- cfa(messung, data = daten_g, group = {_r_str(column)}, estimator = "MLM"'
                     + (f", group.equal = {equal}" if equal != "NULL" else "") + ")")
    lines += ['lavTestLRT(fit_configural, fit_metric, fit_scalar, fit_strict, method = "satorra.bentler.2001")',
              'sapply(list(konfigural = fit_configural, metrisch = fit_metric, skalar = fit_scalar, strikt = fit_strict), '
              'fitMeasures, c("chisq", "df", "cfi", "rmsea", "srmr", "cfi.robust", "rmsea.robust"))',
              "# " + ("latent means (scalar model): the rows ~1 of the constructs" if en else
                      "latente Mittelwerte (skalares Modell): die Zeilen ~1 der Konstrukte"),
              'subset(parameterEstimates(fit_scalar), op == "~1" & lhs %in% c('
              + ", ".join(_r_str(names[lv]) for lv in latent) + "))"]
    return lines


def _r_single(spec, model, frame, en):
    """R lines that fix the error variance of each single question of the model at (1 - reliability) of its variance
    (divided by N, as the ML sample covariance), appended to the model string (SEM extensions 2026-10-17, 7)."""
    out = []
    for code, rel in (spec.get("single") or {}).items():
        col = _r_name(code + SINGLE)
        out.append(f'{model} <- paste0({model}, "\\n  {col} ~~ ", (1 - {rel:g}) * var({frame}${col}) * '
                   f'(nrow({frame}) - 1) / nrow({frame}), "*{col}")  # '
                   + (f"reliability {rel:.2f} assumed" if en else f"Reliabilität {rel:.2f} angenommen".replace(".", ",")))
    return out


def _r_complete(frame, source, columns, en, rows=None):
    """R lines that keep in `frame` the rows `rows` of `source` that are complete in the columns of a model
    (listwise, as the app: the products are centred over these cases; audit 2026-10-12, SEM-9 and SEM-3)."""
    quoted = ", ".join(_r_str(c) for c in columns)
    out = ["# " + ("the complete cases of the columns of the model (listwise), as on the results page" if en else
                   "die vollständigen Fälle der Spalten des Modells (listenweise), wie auf der Ergebnisseite")]
    if rows:
        out.append(f"{frame} <- {source}[{rows}, ]")
        source = frame
    out.append(f"{frame} <- {source}[complete.cases({source}[, c({quoted})]), ]")
    return out


def _r_products(spec, observed, en, frame="daten"):
    """R lines that build the columns of the interaction terms in the data frame `frame` as the app does (double mean
    centred products, Lin et al., 2010; in R with semTools::indProd), over the complete cases of the model, which
    `frame` holds (_r_complete)."""
    products = spec.get("products") or {}
    if not products:
        return []
    out = ["library(semTools)", "# " + ("interaction terms (moderation): products of the centred indicators, centred "
                                        "again, over the complete cases of the model, as in the app" if en else
                                        "Wechselwirkungen (Moderation): Produkte der zentrierten Indikatoren, erneut "
                                        "zentriert, über die vollständigen Fälle des Modells, wie in der App")]

    def col(code):
        return observed.get(code) or _r_name(code)

    for name, part in products.items():
        pairs = part["pairs"]
        if part["kind"] == "oo":
            a, b, c = col(pairs[0][0]), col(pairs[0][1]), col(name)
            out += [f"{frame}${c} <- ({frame}${a} - mean({frame}${a})) * ({frame}${b} - mean({frame}${b}))",
                    f"{frame}${c} <- {frame}${c} - mean({frame}${c})"]
            continue
        first, second = [p[0] for p in pairs], [p[1] for p in pairs]
        match = part["kind"] == "ll"
        var1 = list(dict.fromkeys(first))
        var2 = list(dict.fromkeys(second))
        quote = (lambda codes: ", ".join(_r_str(col(c)) for c in codes))
        made = ", ".join(_r_str(_r_name(f"{a}{TIMES}{b}")) for a, b in pairs)
        out.append(f"{frame} <- indProd({frame}, var1 = c({quote(var1)}), var2 = c({quote(var2)}), match = "
                   f"{'TRUE' if match else 'FALSE'}, meanC = TRUE, residualC = FALSE, doubleMC = TRUE, namesProd = c({made}))")
    return out + [""]


def _r_conditional(model, names, labels, observed, en):
    """R lines with the simple slopes of each interaction term and the conditional indirect effects of a moderated
    mediation, standardised from the model-implied SDs as on the results page (audit 2026-10-12, SEM-4, SEM-10 a,
    decision 8): w one SD below and above its mean, a 0/1 moderator at 0 and 1."""
    out = []
    for n, entry in sorted((model.get("hyps") or {}).items()):
        mods = entry.get("moderations") or []
        if len(mods) != 1 or entry.get("errors"):
            continue
        mod = mods[0]
        main, inter = labels.get((mod["x"], mod["y"])), labels.get((mod["xw"], mod["y"]))
        if not main or not inter:
            continue
        w = names[mod["w"]]
        if (model["vars"].get(mod["w"]) or {}).get("coding"):
            at, where = f"c(0, 1) - mean(daten_m${observed.get(mod['w']) or _r_name(mod['w'])})", "w = 0, w = 1"
        else:
            at, where = f'c(-1, 1) * sd_["{w}"]', "−1 SD, +1 SD"
        out += ["", f"# H{n}: " + (f"simple slopes ({where}), standardised with the model-implied SDs (cov.all)" if en
                                   else f"einfache Steigungen ({where}), standardisiert mit den modellimplizierten SDs "
                                   "(cov.all)"),
                'sd_ <- sqrt(diag(lavInspect(fit, "cov.all")))', "co <- coef(fit)", f"stellen <- {at}",
                f'(co["{main}"] + co["{inter}"] * stellen) * sd_["{names[mod["x"]]}"] / sd_["{names[mod["y"]]}"]']
        t = entry.get("modmed")
        if t:
            stage, x, m, y, _ = t
            other = labels.get((m, y)) if stage == "a" else labels.get((x, m))
            if other:  # the moderated path times the other one, in SD of y per SD of x
                out += ["# " + ("conditional indirect effects (standardised)" if en else
                                "bedingte indirekte Effekte (standardisiert)"),
                        f'(co["{main}"] + co["{inter}"] * stellen) * co["{other}"] * sd_["{names[x]}"] / sd_["{names[y]}"]']
    return out


def lavaan_syntax(survey):
    """The lines of the main model in lavaan syntax, as the R script writes them (SEM extensions 2026-10-17, point
    18): to paste into the SEM modules of JASP and jamovi (SEMLj) or into R. None without a model."""
    script = r_script(survey, "daten.rds", "en")
    if not script:
        return None
    lines = script.split("\n")
    try:
        start = lines.index("modell <- '") + 1
        end = lines.index("'", start)
    except ValueError:
        return None
    return "\n".join(line.strip() for line in lines[start:end])


def r_script(survey, rds_name, lang="de"):
    """An R script that reads the .rds export and fits the model of the plan with lavaan (and, for PLS, seminr), so
    that the numbers can be checked and taken further (concept E11). None when the survey has no model."""
    model = build(survey)
    if model is None or model.get("error"):
        return None
    spec = model["spec"]
    en = lang == "en"
    group_models = model.get("groups") or {}
    every_spec = [spec] + [g["spec"] for g in group_models.values()]
    latent = _r_unique(dict.fromkeys(lv for s in every_spec for lv in list(s["latent"]) + list(s.get("higher") or {})),
                       lambda lv: "F_" + _r_name(lv))
    products = {k: v for s in every_spec for k, v in (s.get("products") or {}).items()}
    dummy_of = {node: (code, value) for code, info in model["vars"].items() for node, _, value in info.get("dummies") or ()}
    codes = list(dict.fromkeys(o for s in every_spec for o in s["observed"]))
    # columns of the export keep their names; what the script makes (products, dummies, 0/1 codings) gets a name of
    # its own that no other column has
    made = [c for c in codes if c in products or c in dummy_of or (model["vars"].get(c) or {}).get("coding")]
    observed = {c: _r_name(c) for c in codes if c not in made}
    taken = set(observed.values()) | {_r_name(i) for s in every_spec for items in s["latent"].values() for i in items}
    observed.update(_r_unique(made, lambda c: _r_name(c) + ("_01" if c not in products and c not in dummy_of else ""),
                              used=taken))
    recode = []
    for code in made:
        if code in products:  # an interaction of two numbers: its column is built below
            continue
        name = observed[code]
        if code in dummy_of:  # one group of a variable with more than two: 1 for it, 0 for the others (stage 3)
            source, value = dummy_of[code]
            column = _r_name(source)
            recode.append(f'daten${name} <- ifelse(is.na(daten${column}), NA, as.numeric(as.character(daten${column}) == {_r_str(value)}))')
            continue
        info = model["vars"][code]
        zero, one = info["coding"]
        column = _r_name(code)
        recode.append(f'daten${name} <- ifelse(as.character(daten${column}) == {_r_str(one)}, 1, '
                      f'ifelse(as.character(daten${column}) == {_r_str(zero)}, 0, NA))  # {_r_comment(info.get("coded", ""))}')
    names = {**latent, **observed}
    lines = [f"# {'Structural equation model of the survey' if en else 'Strukturgleichungsmodell der Befragung'} "
             f"„{_r_comment(survey.title)}“ ({_r_comment(brand.name())})",
             "# " + ("Data: the R export (.rds) of the same data pool; missing values are NA, cases with gaps in the "
                     "columns of a model are left out (listwise), as on the results page." if en else
                     "Daten: der R-Export (.rds) desselben Datenpools; fehlende Werte sind NA, Fälle mit Lücken in den "
                     "Spalten eines Modells fallen weg (listenweise), wie auf der Ergebnisseite."),
             '# install.packages(c("lavaan", "seminr"))', "# lavaan: " + _r_comment(sem.ROSSEEL), "",
             "library(lavaan)", f'daten <- readRDS({_r_str(rds_name)})',
             "# " + ("numbers without their value labels (haven_labelled), as lavaan and seminr expect them" if en else
                     "Zahlen ohne Wertelabels (haven_labelled), wie lavaan und seminr sie erwarten"),
             "daten[] <- lapply(daten, function(x) if (is.numeric(x)) as.numeric(x) else x)", ""]
    # a single question with a fixed reliability (point 7): its own column for the construct of one indicator
    singles = {code: rel for s_ in every_spec for code, rel in (s_.get("single") or {}).items()}
    for code in singles:
        recode.append(f"daten${_r_name(code + SINGLE)} <- daten${_r_name(code)}  # "
                      + ("single question as a construct" if en else "Einzelfrage als Konstrukt"))
    lines += recode + ([""] if recode else [])
    if spec.get("missing") == "fiml":  # point 2: every case with at least one value in the model
        quoted = ", ".join(_r_str(c) for c in _r_columns(spec, observed))
        lines += ["# " + ("every case with at least one value in the columns of the model: full information ML "
                          "(missing = \"ml\"), as on the results page" if en else
                          "jeder Fall mit mindestens einem Wert in den Spalten des Modells: Full Information ML "
                          "(missing = \"ml\"), wie auf der Ergebnisseite"),
                  f"daten_m <- daten[rowSums(!is.na(daten[, c({quoted})])) > 0, ]", ""]
    else:
        lines += _r_complete("daten_m", "daten", _r_columns(spec, observed), en) + [""]
    lines += _r_products(spec, observed, en, "daten_m")
    found = _r_lines(spec, names)
    model_lines, labels = found["lines"], found["labels"]
    for n, (x, m, y) in enumerate(spec["indirect"], 1):
        model_lines.append(f"  ind{n} := {labels[(x, m)]}*{labels[(m, y)]}  # {_r_comment(f'{x} -> {m} -> {y}')}")
    for n, (stage, x, m, y, xw) in enumerate(spec.get("modmed") or (), 1):
        # the index of moderated mediation (Hayes, 2015): a3·b or a·b3 (audit 2026-10-12, SEM-10 a)
        inter, other = (labels[(xw, m)], labels[(m, y)]) if stage == "a" else (labels[(xw, y)], labels[(x, m)])
        model_lines.append(f"  imm{n} := {inter}*{other}  # {_r_comment(f'{xw}: {x} -> {m} -> {y}')}")
    lines += ["modell <- '", *model_lines, "'", *_r_single(spec, "modell", "daten_m", en), "",
              "# " + ("as on the results page: ML, robust errors and Satorra-Bentler test (MLM); fixed.x = FALSE: "
                      "variances and covariances of observed exogenous variables are estimated, as in the app" if en else
                      "wie auf der Ergebnisseite: ML, robuste Standardfehler und Satorra-Bentler-Test (MLM); fixed.x = "
                      "FALSE: Varianzen und Kovarianzen beobachteter exogener Größen werden geschätzt, wie in der App"),
              "# " + ("CFI, TLI and RMSEA: lavaan's cfi, tli, rmsea are the normal-theory values the page shows" if en else
                      "CFI, TLI und RMSEA: lavaans cfi, tli, rmsea sind die normaltheoretischen Werte der Seite"),
              ('fit <- sem(modell, data = daten_m, ordered = TRUE, estimator = "WLSMV")' if spec.get("ordinal") else
               'fit <- sem(modell, data = daten_m, estimator = "MLR", missing = "ml", fixed.x = FALSE)'
               if spec.get("missing") == "fiml" else 'fit <- sem(modell, data = daten_m, estimator = "MLM", fixed.x = FALSE)'),
              'summary(fit, fit.measures = TRUE, standardized = TRUE, rsquare = TRUE)',
              'fitMeasures(fit, c("chisq", "df", "pvalue", "chisq.scaled", "pvalue.scaled", "cfi", "tli", "rmsea", '
              '"rmsea.ci.lower", "rmsea.ci.upper", "srmr", "cfi.robust", "tli.robust", "rmsea.robust"))',
              "# " + ("the largest residual correlations, as the diagnosis on the results page" if en else
                      "die größten Residualkorrelationen, wie die Diagnose auf der Ergebnisseite"),
              'residuals(fit, type = "cor")$cov',
              "# " + ("modification indices only to understand the misfit; freeing paths after them fits the model to "
                      "chance (MacCallum et al., 1992)" if en else
                      "Modifikationsindizes nur, um den Misfit zu verstehen; Pfade danach freizugeben passt das Modell an "
                      "den Zufall an (MacCallum et al., 1992)"),
              "# modindices(fit, sort. = TRUE, maximum.number = 10)"]
    lines += _r_conditional(model, names, labels, observed, en)
    if spec["indirect"] or spec.get("modmed"):
        # the interval of the standardised indirect effect, as the page bootstraps it (audit 2026-10-06, SEM-5)
        lines += ["", "# " + ("indirect effects and indices of moderated mediation (rows :=): percentile bootstrap of "
                              "the standardised values (std.all), as on the results page; ML without the robust "
                              "correction, a draw without a solution is NA and left out. Other random draws than the "
                              "app's: the bounds differ a little." if en else
                              "indirekte Effekte und Indizes der moderierten Vermittlung (Zeilen :=): Perzentil-Bootstrap "
                              "der standardisierten Werte (std.all), wie auf der Ergebnisseite; ML ohne robuste "
                              "Korrektur, eine Ziehung ohne Lösung ist NA und fällt weg. Andere Zufallsziehungen als in "
                              "der App: Die Grenzen weichen etwas ab."),
                  "set.seed(1)",
                  ('fit_ml <- sem(modell, data = daten_m, ordered = TRUE, estimator = "WLSMV", se = "none", '
                   'test = "none")' if spec.get("ordinal") else
                   'fit_ml <- sem(modell, data = daten_m, missing = "ml", fixed.x = FALSE)' if spec.get("missing") == "fiml"
                   else 'fit_ml <- sem(modell, data = daten_m, fixed.x = FALSE)'),
                  "std <- standardizedSolution(fit_ml)",
                  f"boot_std <- bootstrapLavaan(fit_ml, R = {BOOT_DRAWS}, FUN = function(f) standardizedSolution(f, "
                  "se = FALSE, zstat = FALSE, pvalue = FALSE, ci = FALSE)$est.std)",
                  'ind <- std$op == ":="',
                  "data.frame(" + ("effect" if en else "effekt") + " = std$lhs[ind], beta = std$est.std[ind], "
                  "t(apply(boot_std[, ind, drop = FALSE], 2, quantile, probs = c(0.025, 0.975), na.rm = TRUE)))"]
    for number, g in group_models.items():
        # a moderation by groups: the model without the moderator in each group, loadings equal, x -> y compared
        gspec = g["spec"]
        info = model["vars"].get(g["w"]) or {}
        levels = list(info.get("groups") or info.get("coding") or ("0", "1"))
        column = _r_name(g["w"])
        pairs = [(x, y) for x in g["x"] for y in g["y"]]
        mark = {}
        for j, pair in enumerate(pairs, 1):
            tag = f"{number}" if len(pairs) == 1 else f"{number}_{j}"
            mark[pair] = ([f"a{tag}", f"b{tag}"] if len(levels) == 2 else [f"g{k}_{tag}" for k in range(1, len(levels) + 1)])
        found = _r_lines(gspec, names, mark=mark, number=number)
        if len(levels) == 2:
            tests = [f"  d{tag[1:]} := {b} - {a}" for tag, (a, b) in ((lab[0], lab) for lab in mark.values())]
            ask = f'subset(parameterEstimates(fit_h{number}), label %in% c({", ".join(chr(34) + t + chr(34) for lab in mark.values() for t in lab + ["d" + lab[0][1:]])}))'
        else:
            tests = []
            ask = "lavTestWald(fit_h{n}, constraints = \"{c}\")".format(
                n=number, c="; ".join(f"{lab[0]} == {other}" for lab in mark.values() for other in lab[1:]))
        quoted = ", ".join(_r_str(v) for v in levels)
        frame = f"daten_h{number}"
        lines += ["", f"# H{number}: " + ("moderation by groups: the same model in each group with equal loadings, the "
                                         "path compared (Wald test)" if en else
                                         "Moderation durch Gruppen: dasselbe Modell in jeder Gruppe mit gleichen "
                                         "Ladungen, der Pfad verglichen (Wald-Test)"),
                  f"modell_h{number} <- '", *found["lines"], *tests, "'",
                  *_r_complete(frame, "daten", _r_columns(gspec, observed), en,
                               rows=f"as.character(daten${column}) %in% c({quoted})"),
                  *_r_single(gspec, f"modell_h{number}", frame, en),
                  *_r_products(gspec, observed, en, frame),
                  f'fit_h{number} <- sem(modell_h{number}, data = {frame}, group = {_r_str(column)}, '
                  f'group.label = c({quoted}), group.equal = "loadings", estimator = "MLM", fixed.x = FALSE)',
                  ask]
    lines += _r_invariance(survey, model, names, observed, en)
    if getattr(survey, "sampling", None) is not None:  # a diary study: entries nested in persons (point 10)
        import re

        # without the labels (they would make the levels equal) and the defined effects (":=", ind1 = p1*p2)
        bare = [re.sub(r"\bp\d+\*", "", line) for line in found["lines"] if ":=" not in line]
        lines += ["", "# " + ("Diary study: the entries are nested in persons. A two-level model (lavaan, cluster = "
                              "\"person\"): the same paths within persons (level 1) and between them (level 2). Level 2 "
                              "needs enough persons (rule of thumb: 50 or more); not on the results page." if en else
                              "Tagebuchstudie: Die Einträge sind in Personen geschachtelt. Ein Zweiebenen-Modell (lavaan, "
                              "cluster = \"person\"): dieselben Pfade innerhalb der Personen (Ebene 1) und zwischen ihnen "
                              "(Ebene 2). Ebene 2 braucht genug Personen (Faustregel: 50 und mehr); nicht auf der "
                              "Ergebnisseite."),
                  "modell_2e <- '", "level: 1", *bare, "level: 2", *bare, "'",
                  'fit_2e <- sem(modell_2e, data = daten_m, cluster = "person")',
                  "summary(fit_2e, fit.measures = TRUE, standardized = TRUE)"]
    if method_of(survey) == "pls":
        lines += ["", "# " + ("the same model with PLS-SEM (seminr)" if en else "dasselbe Modell mit PLS-SEM (seminr)")]
        if not spec["paths"]:  # the measurement model alone: seminr needs a structural model (SEM-10 b)
            lines.append("# " + ("seminr estimates no model without paths; the measurement model of all scales is the "
                                 "confirmatory factor analysis above (lavaan). The app's PLS weights without paths: "
                                 "factor weighting, every construct with every other." if en else
                                 "seminr schätzt kein Modell ohne Pfade; das Messmodell aller Skalen rechnet oben lavaan "
                                 "(konfirmatorische Faktorenanalyse). Die PLS-Gewichte der App ohne Pfade: "
                                 "Faktorgewichtung, jedes Konstrukt mit jedem anderen."))
            return "\n".join(lines) + "\n"
        lines += ["library(seminr)", "messung <- constructs("]
        formative = set(spec.get("formative") or ())  # mode B (point 9)
        parts = [f'  composite("{latent[lv]}", c({", ".join(chr(34) + _r_name(i) + chr(34) for i in items)})'
                 + (", weights = mode_B)" if lv in formative else ")")
                 for lv, items in spec["latent"].items() if lv not in products]
        parts += [f'  composite("{observed[o]}", single_item("{observed[o]}"))' for o in spec["observed"]
                  if o not in products]
        # interactions in two stages, as in the app (Hair et al., 2022)
        for name, part in (spec.get("products") or {}).items():
            names[name] = f"{names[part['x']]}*{names[part['w']]}"
            parts.append(f'  interaction_term(iv = "{names[part["x"]]}", moderator = "{names[part["w"]]}", '
                         'method = two_stage)')
        lines += [",\n".join(parts), ")", "struktur <- relationships("]
        if spec.get("cov"):
            lines.append("# " + ("relations without a direction are not part of a PLS model; the app reports the "
                                 "correlation of the construct scores" if en else
                                 "Zusammenhänge ohne Richtung kennt ein PLS-Modell nicht; die App berichtet die "
                                 "Korrelation der Konstruktwerte"))
        lines += [",\n".join(f'  paths(from = "{names[a]}", to = "{names[b]}")' for a, b in spec["paths"]), ")",
                  "# " + ("the complete cases of the model (daten_m): seminr would otherwise replace missing values "
                          "by the mean (audit 2026-10-12, SEM-3)" if en else
                          "die vollständigen Fälle des Modells (daten_m): seminr ersetzte fehlende Werte sonst durch den "
                          "Mittelwert (Audit 2026-10-12, SEM-3)"),
                  "pls <- estimate_pls(data = daten_m, measurement_model = messung, structural_model = struktur)",
                  "summary(pls)", f"summary(bootstrap_model(pls, nboot = {BOOT_DRAWS}, seed = 1))",
                  "# " + ("f², adjusted R² and VIF as on the results page: summary(pls)$fSquare, $paths, $vif_antecedents"
                          if en else "f², korrigiertes R² und VIF wie auf der Ergebnisseite: summary(pls)$fSquare, "
                          "$paths, $vif_antecedents"),
                  "# " + ("PLSpredict (10 folds, earliest antecedents; other random folds than the app's)" if en else
                          "PLSpredict (10 Teile, früheste Einflussgrößen; andere Zufallsteilung als in der App)"),
                  "summary(predict_pls(pls, technique = predict_EA, noFolds = 10))"]
    return "\n".join(lines) + "\n"


# --- the diagram --------------------------------------------------------------------------------------------------

def _layers(names, paths):
    """{column: [names top to bottom]}, laid out so that as few paths as possible cross (Ralf, 2026-10-07):
    the column of each variable is the longest path leading to it, then a variable is moved right up to the column
    before its nearest target (a source that only points at the third column stands in the second, so its path is
    a straight line and no arc); the order within the columns is the one with the fewest crossings, found column by
    column (every order of a column up to seven, swaps of neighbours beyond) until nothing improves."""
    edges = [(a, b) for a, b in paths if a in names and b in names and a != b]
    depth = {v: 0 for v in names}
    for _ in range(len(names)):
        for a, b in edges:
            depth[b] = max(depth[b], depth[a] + 1)
    for _ in range(len(names)):
        for v in names:
            targets = [depth[b] for a, b in edges if a == v]
            if targets:
                depth[v] = max(depth[v], min(targets) - 1)
    columns = {}
    for v in names:
        columns.setdefault(depth[v], []).append(v)
    if not edges:
        return columns
    columns = {c: columns[c] for c in sorted(columns)}

    def cost(cols):
        row = {v: k for members in cols.values() for k, v in enumerate(members)}
        straight = [(row[a], row[b], depth[a]) for a, b in edges if depth[b] - depth[a] == 1]
        n = sum(1 for i, (a1, b1, c1) in enumerate(straight) for a2, b2, c2 in straight[i + 1:]
                if c1 == c2 and (a1 - a2) * (b1 - b2) < 0)
        # an arc leaves its source to the right and goes over the top: it crosses the paths of the variables above
        # its source and lands on the top of its target through whatever stands above it
        for a, b in edges:
            if depth[b] - depth[a] > 1:
                above = set(cols[depth[a]][:row[a]])
                n += sum(1 for x, y in edges if x in above) + 2 * row[b]
        # ties: a variable near the middle of the rows it is linked to
        pull = sum(abs(row[a] - row[b]) for a, b in edges) * 0.001
        return n + pull

    from itertools import permutations

    best = cost(columns)
    for _ in range(6):
        improved = False
        for c, members in columns.items():
            if len(members) < 2:
                continue
            if len(members) <= 7:
                orders = permutations(members)
            else:
                orders = (members[:k] + [members[k + 1], members[k]] + members[k + 2:] for k in range(len(members) - 1))
            for order in orders:
                trial = {**columns, c: list(order)}
                score = cost(trial)
                if score < best - 1e-9:
                    best, columns, improved = score, trial, True
        if not improved:
            break
    return columns


NODE_W, NODE_H = 200, 64  # a variable in the diagram and on the drawing of the study plan (static/model.js)


def _rim(cx, cy, tx, ty, latent, w=NODE_W, h=NODE_H):
    """The point on the outline of a node (ellipse or box around cx, cy) towards tx, ty."""
    import math

    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return cx, cy
    rx, ry = w / 2, h / 2
    if latent:
        t = 1 / math.sqrt((dx / rx) ** 2 + (dy / ry) ** 2)
    else:
        t = min(rx / abs(dx) if dx else math.inf, ry / abs(dy) if dy else math.inf)
    return cx + t * dx, cy + t * dy


def _crosses(pos, a, b, x1, y1, cx, cy, x2, y2):
    """Does the (quadratic) line pass through a node other than a and b?"""
    for v, (x, y) in pos.items():
        if v in (a, b):
            continue
        for i in range(1, 20):
            t = i / 20
            px = (1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t * t * x2
            py = (1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t * t * y2
            if x - 6 < px < x + NODE_W + 6 and y - 6 < py < y + NODE_H + 6:
                return True
    return False


def _line(pos, latent, a, b, gap=6, bow=0.0):
    """An edge from node a to node b (top-left corners in pos): {"d", "lx", "ly"}; bow > 0 bends it to its left
    (a relation without a direction, F4), gap leaves room for the arrow head. Where a straight line would cross
    another node it bends around it, as on the drawing of the study plan (static/model.js)."""
    if bow:
        return _shape(pos, latent, a, b, gap, bow)
    for tried in (0.0, 48.0, -48.0, 96.0, -96.0, 150.0, -150.0):
        found = _shape(pos, latent, a, b, gap, tried)
        if not _crosses(pos, a, b, *found["_q"]):
            return found
    return _shape(pos, latent, a, b, gap, 0.0)


def _shape(pos, latent, a, b, gap, bow):
    import math

    (xa, ya), (xb, yb) = pos[a], pos[b]
    ca, cb = (xa + NODE_W / 2, ya + NODE_H / 2), (xb + NODE_W / 2, yb + NODE_H / 2)
    dist = math.hypot(cb[0] - ca[0], cb[1] - ca[1]) or 1.0
    nx, ny = -(cb[1] - ca[1]) / dist, (cb[0] - ca[0]) / dist
    mx, my = (ca[0] + cb[0]) / 2 + nx * bow, (ca[1] + cb[1]) / 2 + ny * bow
    x1, y1 = _rim(*ca, mx, my, a in latent)
    x2, y2 = _rim(*cb, mx, my, b in latent)
    ux, uy = (x2 - mx, y2 - my) if bow else (x2 - x1, y2 - y1)
    norm = math.hypot(ux, uy) or 1.0
    x2, y2 = x2 - gap * ux / norm, y2 - gap * uy / norm
    if bow:
        vx, vy = x1 - mx, y1 - my
        norm = math.hypot(vx, vy) or 1.0
        x1, y1 = x1 - gap * vx / norm, y1 - gap * vy / norm
        cx, cy = 2 * mx - (x1 + x2) / 2, 2 * my - (y1 + y2) / 2
        return {"d": f"M{x1:.1f} {y1:.1f} Q{cx:.1f} {cy:.1f} {x2:.1f} {y2:.1f}", "lx": mx, "ly": my - 6,
                "_q": (x1, y1, cx, cy, x2, y2)}
    return {"d": f"M{x1:.1f} {y1:.1f} L{x2:.1f} {y2:.1f}", "lx": (x1 + x2) / 2, "ly": (y1 + y2) / 2 - 6,
            "_q": (x1, y1, (x1 + x2) / 2, (y1 + y2) / 2, x2, y2)}


def _drawn(result, names, w_node, h_node):
    """The positions (top-left corners) from the drawing of the study plan (modell-zeichnen-konzept.md, F5:
    the researchers' arrangement on the results page): every node whose variable has a place there; the subscales
    of one instrument below each other around it. None when a node has no place."""
    layout = result.get("layout") or {}
    if not names or not all(result["nodes"][v][0] in layout for v in names):
        return None
    pos = {}
    for v in names:
        code = result["nodes"][v][0]
        parts = [u for u in names if result["nodes"][u][0] == code]
        cx, cy = layout[code]
        off = (parts.index(v) - (len(parts) - 1) / 2) * (h_node + 14)
        pos[v] = (cx - w_node / 2, cy + off - h_node / 2)
    return pos


LABEL_W, LABEL_H = 44, 16  # the room a value on an arrow takes ("0,42*")


def _free_labels(pos, edges):
    """Move the value of an arrow off the variables and off the values placed before it (Ralf, 2026-10-17: they
    could lie on a box): its own place if free, else along its line (t from the middle outwards), else just beside
    it. static/sem_drag.js (semFreeLabels) does the same, so moving a variable keeps the picture of the server."""
    taken = []

    def hits(x, y):
        for vx, vy in pos.values():
            if vx - 4 < x + LABEL_W / 2 and x - LABEL_W / 2 < vx + NODE_W + 4 and vy - 4 < y + 4 and y - 12 < vy + NODE_H + 4:
                return True
        return any(abs(tx - x) < LABEL_W + 4 and abs(ty - y) < LABEL_H + 2 for tx, ty in taken)

    for e in edges:
        q = e.get("_q")
        if q is None or e.get("lx") is None:
            continue
        if not hits(e["lx"], e["ly"]):
            taken.append((e["lx"], e["ly"]))
            continue
        x1, y1, cx, cy, x2, y2 = q
        dx, dy = x2 - x1, y2 - y1
        norm = (dx * dx + dy * dy) ** 0.5 or 1.0
        found = None
        for off in (0.0, 18.0, -18.0, 32.0, -32.0):
            for t in (0.5, 0.4, 0.6, 0.32, 0.68, 0.25, 0.75):
                u = 1 - t
                x = u * u * x1 + 2 * u * t * cx + t * t * x2 - off * dy / norm
                y = u * u * y1 + 2 * u * t * cy + t * t * y2 - 6 + off * dx / norm
                if not hits(x, y):
                    found = (x, y)
                    break
            if found:
                break
        if found:
            e["lx"], e["ly"] = found
        taken.append((e["lx"], e["ly"]))


def _name_lines(text, size=24):
    """A name on two lines instead of cut (Ralf, 2026-10-17), as static/model.js nameLines: broken at a space where
    it can be, the second line cut with "…" only when that is still too long."""
    if len(text) <= size:
        return [text]
    cut = text.rfind(" ", 0, size + 1)
    if cut < size / 2:
        cut = size
    rest = text[cut:].strip()
    return [text[:cut].strip(), rest[:size - 1] + "…" if len(rest) > size else rest]


MEASURE_IW, MEASURE_IH, MEASURE_GAP, MEASURE_RISE = 38, 20, 6, 34  # a statement's box below its construct


def _indicators(node, rows, statements):
    """The statements of a construct as boxes in a row below it (above run the arcs of the paths that skip a
    column), each with its standardised loading (Ralf, 2026-10-17: the measurement model in the picture, shown on
    request): [{x, y, w, h, value, title, d}]."""
    count = len(rows)
    if not count:
        return []
    width = count * MEASURE_IW + (count - 1) * MEASURE_GAP
    cx, cy = node["x"] + node["w"] / 2, node["y"] + node["h"] / 2
    top = node["y"] + node["h"] + MEASURE_RISE
    out = []
    for k, r in enumerate(rows):
        x = cx - width / 2 + k * (MEASURE_IW + MEASURE_GAP)
        bx = x + MEASURE_IW / 2
        x1, y1 = _rim(cx, cy, bx, top, node["latent"])
        text = statements.get(r["item"], "") or r["item"]
        out.append({"x": x, "y": top, "w": MEASURE_IW, "h": MEASURE_IH, "value": r.get("std"), "code": r["item"],
                    "title": f"{k + 1}: {text}", "d": f"M{x1:.1f} {y1:.1f} L{bx:.1f} {top - 4:.1f}"})
    return out


def diagram(result, boot=None):
    """Positions for the path diagram (surveys/_sem.html): constructs as ellipses, observed variables as boxes, in
    columns by the length of the longest path leading to them; the measurement model as one column of constructs.
    Where the study plan holds a drawing of the model, its arrangement (F5). {"width", "height", "nodes": [{name,
    label, sub, x, y, w, h, latent}], "edges": [{d (SVG path), value, sig, lx, ly, cov}]}"""
    fit = result.get("fit") or {}
    spec = result.get("spec") or {}
    products = spec.get("products") or {}
    # an interaction term is no box of its own: an arrow from the moderator onto the path it changes (F2)
    controls = result.get("controls") or {}
    # control variables are not drawn: their paths onto every explained variable would cover the picture; the line
    # under it names them and the table lists their paths (SEM extensions 2026-10-17, point 6)
    lowers = sem.lower(spec)  # the subscales under a factor of the second order: in the measurement model
    names = [v for v in result.get("nodes") or {} if v not in products and v not in controls and v not in lowers]
    if not names:
        return None
    spec = {**spec, "paths": [p for p in spec.get("paths") or [] if p[0] not in controls]}
    columns = _layers(names, spec.get("paths") or [])
    if result.get("mode") == "measurement":  # no paths: the constructs in columns of at most four
        columns = {k // 4: names[k:k + 4] for k in range(0, len(names), 4)}
    w_node, h_node, gap_x, gap_y = NODE_W, NODE_H, 110, 28
    drawn = _drawn(result, names, w_node, h_node) if result.get("mode") != "measurement" else None
    ncol = max(columns) + 1
    nrow = max(len(v) for v in columns.values())
    col_of = {v: c for c, members in columns.items() for v in members}
    # a path that skips a column is drawn as an arc above the constructs between, one level higher each
    skips = [] if drawn else [(a, b) for a, b in spec.get("paths") or []
                              if a in col_of and b in col_of and col_of[b] - col_of[a] > 1]
    # the shorter arc inside, the longer one above it: nested arcs do not cross
    skips.sort(key=lambda e: (col_of[e[1]] - col_of[e[0]], col_of[e[0]], columns[col_of[e[0]]].index(e[0])))
    arc = 46
    above = arc * len(skips) + (10 if skips else 0)
    width = ncol * w_node + (ncol - 1) * gap_x + 40
    height = nrow * h_node + (nrow - 1) * gap_y + 40 + above
    pos = {}
    for c, members in columns.items():
        total = len(members) * h_node + (len(members) - 1) * gap_y
        top = above + (height - above - total) / 2
        for k, v in enumerate(members):
            pos[v] = (20 + c * (w_node + gap_x), top + k * (h_node + gap_y))
    if drawn:  # the drawing's places, moved so that the picture starts with a margin of 30 (room for the labels)
        left, top_drawn = min(x for x, _ in drawn.values()), min(y for _, y in drawn.values())
        pos = {v: (x - left + 30, y - top_drawn + 30) for v, (x, y) in drawn.items()}
        width = max(x for x, _ in pos.values()) + w_node + 30
        height = max(y for _, y in pos.values()) + h_node + 30
    labels = result.get("labels") or {}
    # an inadmissible solution gives no values to draw (its paths can be 10^40, its R² below 0): the boxes and arrows
    # alone, the warning above says why (Ralf, 2026-10-07)
    shown = fit.get("admissible", True) is not False
    r2 = (fit.get("r2") or {}) if shown else {}
    rel = (fit.get("quality") or {}).get("cr") or {}
    latent = (set(spec.get("latent") or {}) | set(spec.get("higher") or {})) - set(spec.get("single") or {})  # a single question stays a box
    conditions = set(result.get("conditions") or [])
    from app.items.base import plain

    nodes = []
    for v in names:
        x, y = pos[v]
        code, title = result["nodes"][v]
        # the name in the node: the subscale (the construct) or the question, the code on the second line (UI/UX
        # audit 2026-10-07, W70); "Aussagen" as in the legend under the diagram (W69)
        named = title.split(" · ", 1)[1] if " · " in title else plain(labels.get(code, "") or "").strip()
        sub = [code if len(code) <= 18 else code[:17] + "…"]
        if v in r2:
            sub.append(f"R² = {_num(r2[v], 'en' if english() else 'de')}")
        count = (de_en("{k} Subskalen", "{k} subscales", k=len(spec["higher"][v])) if v in (spec.get("higher") or {}) else
                 de_en("{k} Aussagen", "{k} statements", k=len(spec["latent"][v])) if v in latent else "")
        if code in conditions:  # a randomisation, not a question: says so in the node
            named = de_en("Zuteilung: {name}", "Assignment: {name}", name=named or title)
        nodes.append({"name": v, "code": code, "label": named or title, "lines": _name_lines(named or title),
                      "question": labels.get(code, ""), "sub": " · ".join(sub),
                      "count": count,
                      "x": x, "y": y, "w": w_node, "h": h_node, "latent": v in latent, "cr": rel.get(v),
                      "condition": code in conditions})
    edges = []
    for a, b in spec.get("paths") or []:
        row = path_row(result, a, b, boot)
        if row is None or a not in pos or b not in pos:
            continue
        (xa, ya), (xb, yb) = pos[a], pos[b]
        p = row.get("p")
        edge = {"value": row["std"] if shown else None, "sig": shown and p is not None and p < 0.05, "known": p is not None}
        if drawn:
            edge.update(_line(pos, latent, a, b))
        elif (a, b) in skips:  # over the top: from the top of one (from its right side when constructs stand above it
            # in its column, not across them) to the upper side of the other; the labels sit at different points of
            # their arcs, so arcs that span the same columns do not write over each other (Ralf, 2026-10-07)
            level = skips.index((a, b)) + 1
            if columns[col_of[a]][0] == a:
                x1, y1 = xa + 0.7 * w_node, ya + 2
            else:
                x1, y1 = xa + w_node, ya + h_node / 2
            x2, y2 = xb + 0.3 * w_node, yb + 2
            # the highest point clears every construct of the columns it spans, one level higher per arc
            clear = min(pos[v][1] for c in range(col_of[a], col_of[b] + 1) for v in columns[c]) - 16 - arc * (level - 1)
            cx, cy = (x1 + x2) / 2, 2 * clear - (y1 + y2) / 2
            t = 0.5 if len(skips) == 1 else 0.17  # near its own source: the sources lie in different rows
            edge.update(d=f"M{x1:.1f} {y1:.1f} Q{cx:.1f} {cy:.1f} {x2:.1f} {y2:.1f}", _q=(x1, y1, cx, cy, x2, y2),
                        lx=(1 - t) ** 2 * x1 + 2 * (1 - t) * t * cx + t * t * x2,
                        ly=(1 - t) ** 2 * y1 + 2 * (1 - t) * t * cy + t * t * y2 - 4)
            tm = min(1.0, max(0.0, (y1 - cy) / (y1 - 2 * cy + y2))) if y1 - 2 * cy + y2 else 0.5  # its highest point
            edge["apex"] = (1 - tm) ** 2 * y1 + 2 * (1 - tm) * tm * cy + tm * tm * y2
        else:
            x1, y1 = xa + w_node, ya + h_node / 2
            x2, y2 = xb - 6, yb + h_node / 2
            if xb <= xa:  # same column (the measurement model has no paths): from the bottom
                x1, y1, x2, y2 = xa + w_node / 2, ya + h_node, xb + w_node / 2, yb - 6
            edge.update(d=f"M{x1:.1f} {y1:.1f} L{x2:.1f} {y2:.1f}", lx=(x1 + x2) / 2, ly=(y1 + y2) / 2 - 6,
                        _q=(x1, y1, (x1 + x2) / 2, (y1 + y2) / 2, x2, y2))
        edge["pair"] = (a, b)
        edge.update(kind="path", a=a, b=b)
        edges.append(edge)
    # moderations (F2): from the moderator to the middle of the path x -> y it changes
    for name, part in products.items():
        y = next((b for a, b in spec.get("paths") or [] if a == name), None)
        base = next((e for e in edges if e.get("pair") == (part["x"], y)), None)
        if y is None or base is None or part["w"] not in pos:
            continue
        row = path_row(result, name, y, boot)
        if row is None:
            continue
        # a little before the middle of the path, so that its value stays free
        x0, y0, qx, qy, xe, ye = base["_q"]
        mx = 0.36 * x0 + 0.48 * qx + 0.16 * xe
        my = 0.36 * y0 + 0.48 * qy + 0.16 * ye
        wx, wy = pos[part["w"]][0] + NODE_W / 2, pos[part["w"]][1] + NODE_H / 2
        x1, y1 = _rim(wx, wy, mx, my, part["w"] in latent)
        import math

        dist = math.hypot(mx - x1, my - y1) or 1.0
        x2, y2 = mx - 5 * (mx - x1) / dist, my - 5 * (my - y1) / dist
        p = row.get("p")
        edges.append({"value": row["std"] if shown else None, "sig": shown and p is not None and p < 0.05,
                      "known": p is not None, "moderation": True, "kind": "mod", "a": part["w"], "b": y, "x": part["x"],
                      "d": f"M{x1:.1f} {y1:.1f} L{x2:.1f} {y2:.1f}", "_q": (x1, y1, (x1 + x2) / 2, (y1 + y2) / 2, x2, y2),
                      "lx": x1 + 0.55 * (x2 - x1) + 14, "ly": y1 + 0.55 * (y2 - y1)})
    # relations without a direction (F4): a bent line with a head at both ends
    for a, b in spec.get("cov") or []:
        row = cov_row(result, a, b, boot)
        if row is None or a not in pos or b not in pos:
            continue
        p = row.get("p")
        edge = {"value": row["std"] if shown else None, "sig": shown and p is not None and p < 0.05,
                "known": p is not None, "cov": True, "kind": "cov", "a": a, "b": b}
        edge.update(_line(pos, latent, a, b, bow=36))
        edges.append(edge)
    _free_labels(pos, edges)
    # the measurement model of each construct, drawn only on request (the button above the diagram, sem_drag.js)
    loadings = (fit.get("loadings") or {}) if shown else {}
    statements = result.get("statements") or {}
    for n in nodes:
        n["indicators"] = _indicators(n, loadings.get(n["name"]) or [], statements) if n["latent"] else []
    # the space reserved above for the arcs is more than their tops need: the picture starts at the highest arc or
    # label (viewBox from "top"), not with an empty band
    tops = [y for _, y in pos.values()] + [e["ly"] - 14 for e in edges] + [e["apex"] for e in edges if "apex" in e]
    top = max(0.0, min(tops) - 20)
    # for moving the variables on the page (static/sem_drag.js): the drawing's places are these positions plus shift
    # (the centre of a variable in the study plan), without a drawing the automatic arrangement is taken over
    shift = None
    if drawn:
        shift = [round(left - 30), round(top_drawn - 30)]
    return {"width": width, "height": height - top, "top": top, "nodes": nodes, "edges": edges,
            "movable": result.get("mode") != "measurement", "shift": shift}


def drag_data(d):
    """What static/sem_drag.js reads to move the variables of the diagram (d from diagram()): the nodes in the order
    of the page with their positions, the edges with what they join; shift turns a position into a place of the
    drawing of the study plan (None: the automatic arrangement, taken over at the first move)."""
    return {"shift": d.get("shift"), "w": NODE_W, "h": NODE_H,
            "nodes": [{"name": n["name"], "code": n["code"], "x": round(n["x"], 1), "y": round(n["y"], 1),
                       "latent": bool(n["latent"]), "label": n["label"]} for n in d["nodes"]],
            "edges": [{"kind": e.get("kind"), "a": e.get("a"), "b": e.get("b"), "x": e.get("x")} for e in d["edges"]]}


def sources(result):
    """[(heading, [citations])] for the one fold of the results page."""
    if not result or result.get("error") == "few" and not result.get("fit"):
        return []
    out = []
    if result.get("method") == "pls":
        out.append((de_en("Strukturgleichungsmodell (PLS)", "Structural equation model (PLS)"), list(sem.PLS_SOURCES)))
    else:
        out.append((de_en("Strukturgleichungsmodell (kovarianzbasiert)", "Structural equation model (covariance-based)"),
                    list(sem.CB_SOURCES)))
    out.append((de_en("Strukturgleichungsmodell: Messmodell (AVE, HTMT)", "Structural equation model: measurement model (AVE, HTMT)"),
                list(sem.QUALITY_SOURCES)))
    if any(e.get("indirect") for e in (result.get("hyps") or {}).values()):
        out.append((de_en("Strukturgleichungsmodell: indirekte Effekte", "Structural equation model: indirect effects"),
                    [sem.MACKINNON, sem.PREACHER, sem.SOBEL]))
    hyps = (result.get("hyps") or {}).values()
    if any(e.get("moderation") or e.get("groups") for e in hyps):
        found = [sem.MARSH, sem.LIN] if any(e.get("moderation") for e in hyps) else []
        if any(e.get("groups") for e in hyps):
            found.append(sem.MEREDITH)
        if result.get("method") == "pls":
            found.append(sem.HAIR)
        if any(e.get("modmed") for e in hyps) or result.get("binary"):  # moderated mediation, effects per group
            found.append(sem.HAYES)
        if any(e.get("moderation") for e in hyps) and result.get("method") != "pls":  # the region of significance
            found.append(sem.JOHNSON_NEYMAN)
        out.append((de_en("Strukturgleichungsmodell: Moderation", "Structural equation model: moderation"), found))
    out.append((de_en("Strukturgleichungsmodell: Stichprobengröße", "Structural equation model: sample size"), [sem.WOLF]))
    # what the SEM extensions of 2026-10-17 cite on the page, each only where it is shown
    fit = result.get("fit") or {}
    if result.get("method") == "pls" and fit:
        out.append((de_en("Strukturgleichungsmodell (PLS): f², Vorhersagekraft, Diskriminanz",
                          "Structural equation model (PLS): f², predictive power, discriminant validity"),
                    [sem.COHEN_1988, sem.SHMUELI, sem.FRANKE_SARSTEDT]))
    elif fit.get("quality", {}).get("htmt"):
        out.append((de_en("Strukturgleichungsmodell: HTMT mit Bootstrap-Grenze", "Structural equation model: HTMT with bootstrap bound"),
                    [sem.FRANKE_SARSTEDT]))
    if result.get("single"):
        out.append((de_en("Strukturgleichungsmodell: Einzelfragen mit Reliabilität", "Structural equation model: single questions with a reliability"),
                    [sem.PETRESCU]))
    if result.get("missing") == "fiml":
        out.append((de_en("Strukturgleichungsmodell: fehlende Werte (FIML, MLR)", "Structural equation model: missing values (FIML, MLR)"),
                    list(sem_means.FIML_SOURCES)))
    if result.get("ordinal"):
        out.append((de_en("Strukturgleichungsmodell: geordnete Kategorien (WLSMV)", "Structural equation model: ordered categories (WLSMV)"),
                    list(sem_ordinal.SOURCES)))
    if result.get("weighted"):
        out.append((de_en("Strukturgleichungsmodell: Gewichte", "Structural equation model: weights"), [sem.ASPAROUHOV_2005]))
    if result.get("invariance") and not (result.get("invariance") or {}).get("error"):
        out.append((de_en("Strukturgleichungsmodell: Messinvarianz", "Structural equation model: measurement invariance"),
                    list(sem_means.SOURCES)))
    if result.get("compare") and not (result.get("compare") or {}).get("error"):
        out.append((de_en("Strukturgleichungsmodell: Konkurrenzmodell", "Structural equation model: competing model"),
                    [sem_means.SATORRA_BENTLER_2001, sem.BURNHAM]))
    return out
