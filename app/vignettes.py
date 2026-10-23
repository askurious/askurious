"""Vignettes (factorial survey): a loop whose rows are not typed but come from factors, levels and an experimental
design (app/doe.py), split into decks per person. Table or factors is the choice at the top of the loop page
(to_factors / to_table); the flow offers one card "Schleife" for both.

A vignette loop is a loop node of the flow (app/loops.py) with "source": "factors":
  {"t": "loop", "source": "factors", "id": "l..", "name": "Vignetten 1", "code": "vig1",
   "attributes": [{"id", "code", "levels": [{"id", "hint"}]}],          the factors (as conjoint attributes)
   "prohibitions": [{"a", "la", "b", "lb"}],                              level pairs that never meet
   "texts": {lang: {"attributes": {id: label}, "levels": {id: label}, "level_defs": {id: text}}},
   "plan": {"method": "full" | "doptimal", "per_person": 10, "decks": 12,
            "sig": "...", "profiles": [[level index per factor], ...], "decks_rows": [[row id, ...], ...],
            "stats": {...}},
   "columns": [...], "rows": [...],                                       generated from the plan (materialize)
   "order": "random", "children": [block nodes]}

The rows are written from the plan, one per profile: a column per factor (its level in each language), "<code>_def"
for the definitions and "<code>_hint" (internal, never shown) for the internal hints. Everything that works on
loop rows therefore works unchanged: placeholders, answers stored per row ("item#row"), images per combination,
export, dummy data, page times. A person gets one deck: the deck shown least often to real participants so far
(the row counts of app/balance.py; the rows of a deck belong to no other deck), its rows in random order.

The plan is current while its signature matches the structure (number of levels, prohibitions, method, vignettes
per person, decks); labels and translations change only the rows' texts.

The editor is editor.vignettes_node; the loop page (editor.loop_node) sends a vignette loop there."""
import copy
import hashlib
import json
import random
import re
import secrets

from app.items.attributes import AttributeLevels
from app.items.base import new_id as _nid
from app.lang import de_en

NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,40}\Z")  # placeholder names (app/loops.py)
UMLAUT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"})
FIELDS = ("source", "attributes", "prohibitions", "texts", "plan")
RESERVED = {"lang", "survey", "pid", "branch"}


def is_vignettes(node):
    return bool(node) and node.get("t") == "loop" and node.get("source") == "factors"


def new_node(n):
    """The fields of a vignette loop without factors yet (to_factors takes them over)."""
    from app.lang import de_en

    return {"t": "loop", "id": _nid("l"), "source": "factors", "name": de_en("Vignetten {n}", "Vignettes {n}", n=n), "code": f"vig{n}",
            "attributes": [], "prohibitions": [], "texts": {},
            "plan": {"method": "doptimal", "per_person": 10, "decks": 1},
            "columns": [], "rows": [], "mode": "all", "k": 1, "order": "random", "balanced": True, "children": []}



def _renamed(node, root, prefix, other):
    """A default name ("Schleife 2") follows the source ("Vignetten 1"); a name of the researcher's own stays."""
    if not re.fullmatch(rf"{other} \d+", node.get("name") or ""):
        return node.get("name")
    n = sum(1 for m in _loops(root) if m is not node and re.fullmatch(rf"{prefix} \d+", m.get("name") or ""))
    return f"{prefix} {n + 1}"


def _loops(root):
    from app import flow

    return [n for n, _, _ in flow.walk(root) if n["t"] == "loop"]


def _factor_columns(node):
    """The columns of a table that become factors: the ticked ones of a "Ausschnitt nach Versuchsplan", else every
    visible column; never the definitions ("<code>_def") and internal hints ("<code>_hint") of another column."""
    from app import loop_plan

    columns = node.get("columns") or []
    names = {c["name"] for c in columns}
    extra = {f"{n}_def" for n in names} | {f"{n}_hint" for n in names}
    if node.get("mode") == "plan" and loop_plan.settings(node)["cols"]:
        picked = set(loop_plan.settings(node)["cols"])
        return [c for c in columns if c["id"] in picked and c["name"] not in extra]
    return [c for c in columns if not c.get("hidden") and c["name"] not in extra]


def from_table(survey, node):
    """What to_factors makes of the table: (factor names, "decks" | "full" | "new"). "decks": a current
    "Ausschnitt nach Versuchsplan" becomes the plan as it is; "full": the rows are every combination once, the plan
    is all of them; "new": the plan is made afterwards ("Versuchsplan erzeugen")."""
    from app import loop_plan

    cols = _factor_columns(node)
    if not cols or not node.get("rows"):
        return [], "new"
    if node.get("mode") == "plan" and loop_plan.current(survey, node):
        return [c["name"] for c in cols], "decks"
    probe = {**node, loop_plan.KEY: {**loop_plan.settings(node), "cols": [c["id"] for c in cols]}}
    _, levels, candidates = loop_plan.factor_space(survey, probe)
    total = 1
    for lv in levels:
        total *= len(lv)
    full = (len(candidates) == len(node["rows"]) == total and len({tuple(p) for _, p in candidates}) == total
            and all(len(lv) > 1 for lv in levels))
    return [c["name"] for c in cols], "full" if full else "new"


def to_factors(survey, node, root):
    """Table to factors (the choice at the top of the loop page): the factor columns (_factor_columns) become factors,
    their values levels (labels in every language, "<code>_def" as definitions, "<code>_hint" as internal hints, each
    from the first row with the value). The plan as from_table says; with "decks" the rows keep their ids, so
    answers stay with them. Other columns drop out (undo brings them back); the export name and blocks stay."""
    from app import doe, loop_plan
    from app.items.base import new_id

    name = _renamed(node, root, "Vignetten", "Schleife")
    _, outcome = from_table(survey, node)
    cols = _factor_columns(node)
    subset = loop_plan.settings(node)
    by_name = {c["name"]: c for c in node.get("columns") or []}
    langs = list(survey.languages or [survey.default_language])
    probe = {**node, loop_plan.KEY: {**subset, "cols": [c["id"] for c in cols]}}
    _, levels, candidates = loop_plan.factor_space(survey, probe) if cols else ([], [], [])
    rows = {r["id"]: r for r in node.get("rows") or []}
    fresh = new_node(1)
    attrs, texts = [], {lang: {"attributes": {}, "levels": {}, "level_defs": {}} for lang in langs}
    for j, c in enumerate(cols):
        aid, lv_ids = new_id(), [new_id() for _ in levels[j]]
        hint_col, def_col = by_name.get(f"{c['name']}_hint"), by_name.get(f"{c['name']}_def")
        level_list = []
        for li, lid in enumerate(lv_ids):
            row = rows[next(r for r, p in candidates if p[j] == li)]
            values = row.get("values") or {}
            hint = (values.get(hint_col["id"]) or {}).get(survey.default_language, "") if hint_col else ""
            level_list.append({"id": lid, "hint": hint})
            for lang in langs:
                if (values.get(c["id"]) or {}).get(lang):
                    texts[lang]["levels"][lid] = values[c["id"]][lang]
                if def_col and (values.get(def_col["id"]) or {}).get(lang):
                    texts[lang]["level_defs"][lid] = values[def_col["id"]][lang]
        attrs.append({"id": aid, "code": c["name"], "levels": level_list})
        for lang in langs:
            texts[lang]["attributes"][aid] = c["name"]
    for key in FIELDS:
        node[key] = fresh[key]
    node.update(attributes=attrs, texts=texts, mode="all")
    if outcome == "decks":
        decks = [[r for r in d if r in rows] for d in subset["decks_rows"]]
        profile = dict(candidates)
        ordered = [profile[r] for d in decks for r in d]
        node["plan"] = {"method": "doptimal", "per_person": len(decks[0]), "decks": len(decks)}
        size = len(decks[0])
        node["plan"].update(ids=[r for d in decks for r in d], profiles=ordered, decks_rows=decks,
                            stats=doe.stats(ordered, level_counts(node),
                                            [list(range(t * size, (t + 1) * size)) for t in range(len(decks))]))
        node["plan"]["sig"] = signature(survey, node)
        materialize(survey, node)
    elif outcome == "full":
        total = len(candidates)
        per_person = subset["per_person"] if total % subset["per_person"] == 0 and subset["per_person"] <= total else total
        node["plan"] = {"method": "full", "per_person": per_person, "decks": total // per_person}
        generate(survey, node)
    else:
        node.update(columns=[], rows=[])
    for key in (loop_plan.KEY, "level_cols"):
        node.pop(key, None)
    node["name"] = name


def to_table(survey, node, root):
    """Factors to table: the rows written from the plan stay as a table to edit (texts in every language, the
    internal hints as an internal column). Several decks stay as a fixed selection per person (app/loop_plan.py),
    so everyone keeps getting one deck; a plan that is not current leaves only the columns."""
    from app import loop_plan

    plan = node.get("plan") or {}
    decks = [d for d in plan.get("decks_rows") or [] if d] if plan_current(survey, node) else []
    factor_cols = [c["id"] for c in node.get("columns") or [] if c["id"].startswith("f")]
    name = _renamed(node, root, "Schleife", "Vignetten")
    for key in FIELDS:
        node.pop(key, None)
    node.update(name=name, mode="all", k=1)
    if len(decks) > 1 and factor_cols:
        node["mode"] = "plan"
        node[loop_plan.KEY] = {"cols": factor_cols, "per_person": len(decks[0]), "decks": len(decks),
                               "decks_rows": decks, "stats": plan.get("stats") or {}}
        node[loop_plan.KEY]["sig"] = loop_plan.signature(survey, node)

class Factors(AttributeLevels):
    """The factors of a vignette loop in the shape the conjoint helpers expect of an item (config, t, text,
    survey), so that their form parser and templates serve vignettes too."""

    def __init__(self, survey, node):
        self.survey = survey
        self.node = node
        self.config = {"attributes": node.get("attributes") or [], "prohibitions": node.get("prohibitions") or []}
        self.texts = node.get("texts") or {}

    def t(self, lang=None):
        default = self.survey.default_language
        base = copy.deepcopy(self.texts.get(default, {}))
        if lang and lang != default:
            for k, v in self.texts.get(lang, {}).items():
                if isinstance(v, dict):
                    base[k] = {**base.get(k, {}), **{kk: vv for kk, vv in v.items() if vv}}
        return base

    def text(self, key, lang=None):
        return self.t(lang).get(key, {})

    def has_real_answers(self, item):
        return False

    def long_columns(self, item):
        return []


def factors(survey, node):
    return Factors(survey, node)


def _slug(text):
    s = re.sub(r"[^A-Za-z0-9_]+", "_", str(text or "").translate(UMLAUT)).strip("_").lower()
    if not s or not s[0].isalpha():
        s = "f_" + s if s else "f"
    return s[:30]


def _codes(attributes, labels):
    """Placeholder names: the typed one if valid, else one made from the label; unique, never "_def"/"_hint"
    endings of another factor and never a built-in placeholder."""
    taken = set()
    for n, a in enumerate(attributes, start=1):
        code = (a.get("code") or "").strip()
        if not NAME_RE.match(code) or re.fullmatch(r"A\d+", code):
            code = _slug(labels.get(a["id"]) or f"faktor{n}")
        base, k = code, 2
        while code.lower() in taken or code.lower() in RESERVED or code.endswith(("_def", "_hint")):
            code = f"{base}{k}"
            k += 1
        taken.add(code.lower())
        a["code"] = code


def update_from_form(survey, node, form, lang):
    """Save the editor: factors, levels, texts, prohibitions, plan settings, name and code. Structure only in the
    draft and in the default language, texts in every language (a running survey: only other languages)."""
    from app.items.base import CODE_RE

    structural = survey.is_editable and lang == survey.default_language
    if not survey.is_editable and lang == survey.default_language:
        return
    f = factors(survey, node)
    config = {"attributes": copy.deepcopy(f.config["attributes"]), "prohibitions": copy.deepcopy(f.config["prohibitions"])}
    texts = copy.deepcopy(node.get("texts") or {})
    lt = texts.setdefault(lang, {})
    f.update_attributes(f, form, lang, structural, config, lt)
    for key in ("explain_intro", "attr_desc"):
        lt.pop(key, None)
    ticked = set(form.getlist("attr_between"))
    for a in config["attributes"]:
        a.pop("visual", None)
        a.pop("price_attr", None)
        if structural:
            if a["id"] in ticked:
                a["between"] = True
            else:
                a.pop("between", None)
    if structural:
        _codes(config["attributes"], lt.get("attributes", {}))
        node["attributes"], node["prohibitions"] = config["attributes"], config["prohibitions"]
        plan = dict(node.get("plan") or {})
        plan["method"] = "full" if form.get("method") == "full" else "doptimal"
        plan["per_person"] = _int(form.get("per_person"), plan.get("per_person", 10), 1, 500)
        plan["decks"] = _int(form.get("decks"), plan.get("decks", 1), 1, 500)
        known = {a["id"] for a in config["attributes"]}
        chosen = []
        for value in form.getlist("interaction"):
            a, _, b = value.partition("|")
            if a in known and b in known and a != b and sorted((a, b)) not in chosen:
                chosen.append(sorted((a, b)))
        plan["interactions"] = chosen
        node["plan"] = plan
        name = form.get("name", "").strip()[:100]
        node["name"] = name or node.get("name") or "Vignetten"
        code = form.get("code", "").strip()
        if CODE_RE.match(code):
            node["code"] = code
        node["order"] = "fixed" if form.get("order") == "fixed" else "random"
    node["texts"] = texts
    materialize(survey, node)


def _int(value, default, lo, hi):
    value = str(value or "").strip()
    return max(lo, min(hi, int(value))) if value.isascii() and value.isdigit() else default


# --- plan ---------------------------------------------------------------------------------


def level_counts(node):
    return [len(a.get("levels", [])) for a in node.get("attributes") or []]


def signature(survey, node):
    plan = node.get("plan") or {}
    shape = [level_counts(node), factors(survey, node).prohibitions(factors(survey, node)), plan.get("method"),
             plan.get("per_person"), plan.get("decks") if plan.get("method") != "full" else None]
    if pairs(node):  # appended only when there are any: plans made before keep their signature
        shape.append(pairs(node))
    if between(node):
        shape.append(["between", between(node)])
    return hashlib.sha1(json.dumps(shape).encode()).hexdigest()[:12]


def pairs(node):
    """The two-way interactions the plan is made for, as pairs of factor indices (from plan["interactions"], pairs of
    factor ids); only pairs of factors that still exist."""
    index = {a["id"]: n for n, a in enumerate(node.get("attributes") or [])}
    out = []
    for p in (node.get("plan") or {}).get("interactions") or []:
        if isinstance(p, (list, tuple)) and len(p) == 2 and p[0] in index and p[1] in index and p[0] != p[1]:
            pair = tuple(sorted((index[p[0]], index[p[1]])))
            if pair not in out:
                out.append(pair)
    return sorted(out)


def plan_current(survey, node):
    plan = node.get("plan") or {}
    return bool(plan.get("profiles")) and plan.get("sig") == signature(survey, node)


def universe_size(node):
    from app import doe

    return doe.universe(level_counts(node)) if node.get("attributes") else 0


def allowed_count(survey, node):
    from app import conjoint_design

    levels = level_counts(node)
    if not levels or any(n < 1 for n in levels):
        return 0
    return conjoint_design.n_allowed_profiles(levels, factors(survey, node).prohibitions(factors(survey, node)))


def full_options(survey, node):
    """Vignettes per person a complete plan allows: the divisors of the allowed combinations (equal decks)."""
    from app import doe

    n = allowed_count(survey, node)
    if not n or n > doe.FULL_MAX:
        return []
    return [d for d in range(1, n + 1) if n % d == 0]


def problems(survey, node):
    """What keeps a plan from being made (texts for the editor), empty when it can be."""
    out = []
    attrs = node.get("attributes") or []
    if not attrs:
        out.append(de_en("Mindestens einen Faktor anlegen.", "Add at least one factor."))
    for a in attrs:
        if len(a.get("levels", [])) < 2:
            out.append(de_en("Faktor {code}: mindestens zwei Stufen.", "Factor {code}: at least two levels.",
                             code=a['code']))
    return out


def has_real_answers(survey, node):
    """True if real participants answered a question of the loop: then its plan stays as it is (the answers are
    stored per row of the plan)."""
    return any(it.kind.has_real_answers(it) for it in _answer_items(survey, node))


def generate(survey, node, seed=None, budget=None):
    """Make the plan from the current settings; raises ValueError with a text for the researcher. budget
    (conjoint_design.Budget): the searches stop with the best plan so far once it is over (budget.cut)."""
    from app import doe

    found = problems(survey, node)
    if found:
        raise ValueError(" ".join(found))
    levels = level_counts(node)
    f = factors(survey, node)
    forbidden = f.prohibitions(f)
    from app import conjoint_design

    if forbidden and conjoint_design.coupled(levels, forbidden):
        raise ValueError(de_en("Die verbotenen Kombinationen koppeln Stufen zweier Faktoren so eng, dass sich ihre Wirkungen "
                               "nicht trennen lassen. Bitte weniger Kombinationen verbieten.", "The prohibitions tie "
                               "levels of two factors so closely that their effects cannot be told apart. Please prohibit "
                               "fewer combinations."))
    plan = dict(node.get("plan") or {})
    per_person = plan.get("per_person", 10)
    if plan.get("method") != "full" and per_person * plan.get("decks", 1) > doe.N_MAX:  # code review 2026-10-15, B2
        raise ValueError(de_en("{n} Vignetten ({k} je Person in {d} Decks) sind zu viele für einen Versuchsplan "
                               "(höchstens {most}). Bitte weniger Decks oder weniger Vignetten je Person wählen.",
                               "{n} vignettes ({k} per person in {d} decks) are too many for an experimental design (at "
                               "most {most}). Please choose fewer decks or fewer vignettes per person.",
                               n=per_person * plan.get("decks", 1), k=per_person, d=plan.get("decks", 1), most=doe.N_MAX))
    rng = random.Random(seed)
    if between(node):  # factors that stay the same for a person: a split design (_split_plan)
        profiles, decks, n_decks = _split_plan(node, levels, forbidden, plan, rng, budget)
    elif plan.get("method") == "full":
        profiles = doe.full_factorial(levels, forbidden)
        if len(profiles) > doe.FULL_MAX:
            raise ValueError(de_en("{n} erlaubte Kombinationen sind zu viele für alle (höchstens {most}). Bitte "
                                   "„D-optimaler Ausschnitt“ wählen.", "{n} allowed combinations are too many for all "
                                   "(at most {most}). Please choose “D-optimal subset”.", n=len(profiles),
                                   most=doe.FULL_MAX))
        if per_person > len(profiles) or len(profiles) % per_person:
            raise ValueError(de_en("{n} Kombinationen lassen sich nicht in gleich große Decks zu {k} Vignetten teilen. "
                                   "Bitte eine der angebotenen Zahlen wählen.", "{n} combinations cannot be split into "
                                   "equal decks of {k} vignettes. Please choose one of the numbers offered.",
                                   n=len(profiles), k=per_person))
        n_decks = len(profiles) // per_person
    else:
        n_decks = plan.get("decks", 1)
        profiles = doe.d_optimal(levels, per_person * n_decks, forbidden, seed=rng.randrange(1 << 30), pairs=pairs(node),
                                 budget=budget)
    if not between(node):
        decks = doe.blocks(profiles, levels, n_decks, seed=rng.randrange(1 << 30), pairs=pairs(node), budget=budget)
    ordered = [profiles[i] for deck in decks for i in deck]
    sig = signature(survey, node)
    # a random part of fixed length: a plan made again (after "Zur Bearbeitung öffnen") must not give its new
    # profiles the row ids of the old ones, or stored answers "<item>#<row>" would be read with the new levels
    # (code review 2026-10-15, C3)
    salt = secrets.token_hex(3)
    ids = [f"p{sig[:4]}{salt}{n}" for n in range(1, len(ordered) + 1)]
    size = len(ordered) // n_decks
    plan.update(sig=sig, profiles=ordered, decks_rows=[ids[t * size:(t + 1) * size] for t in range(n_decks)],
                ids=ids, stats=doe.stats(ordered, levels, [list(range(t * size, (t + 1) * size)) for t in range(n_decks)],
                                         pairs(node), between(node)))
    if plan.get("method") == "full":
        plan["decks"] = n_decks
    node["plan"] = plan
    materialize(survey, node)
    return plan


def plan_stats(node):
    """The figures of the stored plan. A split plan stored before the audit of 2026-10-05 (W-3) counted the factors
    between persons in the efficiency within the decks (always 0 there): such figures are computed anew."""
    from app import doe

    plan = node.get("plan") or {}
    stats = plan.get("stats") or {}
    b_idx = between(node)
    rows = plan.get("decks_rows") or []
    if not b_idx or stats.get("deck_within") or not rows or not plan.get("profiles"):
        return stats
    size = len(rows[0])
    try:
        return doe.stats(plan["profiles"], level_counts(node),
                         [list(range(t * size, (t + 1) * size)) for t in range(len(rows))], pairs(node), b_idx)
    except (IndexError, ValueError):  # a plan that no longer fits its factors: not current anyway
        return stats


def between(node):
    """Indices of the factors that vary only between persons: every vignette of a deck has the same level."""
    return [n for n, a in enumerate(node.get("attributes") or []) if a.get("between")]


def _split_plan(node, levels, forbidden, plan, rng, budget=None):
    """A plan whose decks each hold one combination of the between-person factors: per combination the same number
    of decks, the vignettes within them from a plan of the other factors (all their combinations or a D-optimal
    sample, blocked into decks). Returns (profiles, decks as index lists, number of decks)."""
    import itertools

    from app import doe

    b_idx = between(node)
    w_idx = [n for n in range(len(levels)) if n not in b_idx]
    if not w_idx:
        raise ValueError(de_en("Mindestens ein Faktor muss innerhalb der Personen variieren.",
                               "At least one factor has to vary within people."))
    if any(a in b_idx or b in b_idx for (a, _), (b, _) in forbidden):
        raise ValueError(de_en("Verbotene Kombinationen mit Faktoren, die nur zwischen Personen variieren, sind nicht möglich.",
                               "Prohibitions with factors that vary only between people are not possible."))
    combos = list(itertools.product(*[range(levels[n]) for n in b_idx]))
    w_levels = [levels[n] for n in w_idx]
    w_pos = {n: k for k, n in enumerate(w_idx)}
    w_forbidden = [((w_pos[a], la), (w_pos[b], lb)) for (a, la), (b, lb) in forbidden]
    w_pairs = [(w_pos[a], w_pos[b]) for a, b in pairs(node) if a in w_pos and b in w_pos]
    per_person = plan.get("per_person", 10)
    if plan.get("method") == "full":
        within = doe.full_factorial(w_levels, w_forbidden)
        if per_person > len(within) or len(within) % per_person:
            raise ValueError(de_en("Die {n} Kombinationen der übrigen Faktoren lassen sich nicht in gleich große Decks zu "
                                   "{k} Vignetten teilen.", "The {n} combinations of the other factors cannot be split "
                                   "into equal decks of {k} vignettes.", n=len(within), k=per_person))
        per_combo = len(within) // per_person
    else:
        n_decks = plan.get("decks", 1)
        if n_decks % len(combos):
            raise ValueError(de_en("Die Faktoren zwischen Personen haben {n} Kombinationen: Die Zahl der Decks muss ein "
                                   "Vielfaches von {n} sein.", "The factors between people have {n} combinations: the "
                                   "number of decks has to be a multiple of {n}.", n=len(combos)))
        per_combo = n_decks // len(combos)
    profiles, decks = [], []
    for combo in combos:
        if plan.get("method") != "full":
            within = doe.d_optimal(w_levels, per_person * per_combo, w_forbidden, seed=rng.randrange(1 << 30), pairs=w_pairs,
                                   budget=budget)
        w_decks = doe.blocks(within, w_levels, per_combo, seed=rng.randrange(1 << 30), pairs=w_pairs, budget=budget)
        for deck in w_decks:
            start = len(profiles)
            for i in deck:
                row = [0] * len(levels)
                for k, n in enumerate(w_idx):
                    row[n] = within[i][k]
                for k, n in enumerate(b_idx):
                    row[n] = combo[k]
                profiles.append(row)
            decks.append(list(range(start, len(profiles))))
    return profiles, decks, len(decks)


def materialize(survey, node):
    """Write the loop's columns and rows from the plan and the texts (no rows while the plan is not current)."""
    f = factors(survey, node)
    attrs = node.get("attributes") or []
    langs = list(survey.languages or [survey.default_language])
    per_lang = {lang: f.t(lang) for lang in langs}
    default = per_lang.get(survey.default_language) or f.t()
    columns = []
    for a in attrs:
        columns.append({"id": f"f{a['id']}", "name": a["code"], "hidden": False})
        if any((default.get("level_defs") or {}).get(l["id"]) for l in a.get("levels", [])):
            columns.append({"id": f"d{a['id']}", "name": f"{a['code']}_def", "hidden": False})
        if any((l.get("hint") or "").strip() for l in a.get("levels", [])):
            columns.append({"id": f"h{a['id']}", "name": f"{a['code']}_hint", "hidden": True})
    rows = []
    plan = node.get("plan") or {}
    if plan_current(survey, node):
        for rid, profile in zip(plan.get("ids") or [], plan.get("profiles") or []):
            values = {}
            for a, li in zip(attrs, profile):
                level = a["levels"][li]
                values[f"f{a['id']}"] = {lang: (t.get("levels") or {}).get(level["id"], "") for lang, t in per_lang.items()}
                values[f"d{a['id']}"] = {lang: (t.get("level_defs") or {}).get(level["id"], "") for lang, t in per_lang.items()}
                values[f"h{a['id']}"] = {survey.default_language: level.get("hint", "")}
            known = {c["id"] for c in columns}
            rows.append({"id": rid, "values": {k: {lg: v for lg, v in cell.items() if v} for k, cell in values.items()
                                               if k in known}})
    node["columns"], node["rows"] = columns, rows
    node["mode"] = "all"


def deck_of(node, row_id):
    for n, deck in enumerate((node.get("plan") or {}).get("decks_rows") or [], start=1):
        if row_id in deck:
            return n
    return None


def choose_rows(survey, node, rng, counts):
    """The rows of one person: the least shown deck (ties at random), in random or plan order."""
    return pick_deck((node.get("plan") or {}).get("decks_rows") or [], {r["id"] for r in node.get("rows") or []},
                     counts, rng, node.get("order", "random"))


def pick_deck(decks, known, counts, rng, order="random"):
    """The deck shown least often to real participants so far (the count of its first row: a row belongs to one
    deck only), ties at random; its rows in random order or as planned. Also for table subsets (app/loop_plan.py).

    The decks are shuffled and the first of the least shown taken (as balance.least_used): the draw uses the same
    random numbers however many decks are tied. The first page draws with all counts, its first "Next" with only
    those of the picked deck (balance.keep), where it is the only one left; a choice among the tied ones would have
    used other random numbers then, and every later draw (order in the deck, statements, other loops) would differ
    from what the first page showed."""
    decks = [[r for r in d if r in known] for d in decks]
    decks = [d for d in decks if d]
    if not decks:
        return []
    rng.shuffle(decks)
    picked = list(min(decks, key=lambda d: counts.get(d[0], 0)))
    if order == "random":
        rng.shuffle(picked)
    return picked


def check(survey, node, label, first):
    """Findings of the pre-launch check for one vignette loop (the loop check adds the general ones)."""
    out = []
    if not node.get("attributes"):
        out.append(("error", f"{label}: noch keine Faktoren.", None, first))
        return out
    for text in problems(survey, node):
        out.append(("error", f"{label}: {text}", None, first))
    if not plan_current(survey, node):
        out.append(("error", f"{label}: Versuchsplan noch nicht erzeugt oder veraltet (nach einer Änderung an Faktoren, "
                             "Stufen, verbotenen Kombinationen oder Deckgröße neu erzeugen).", None, first))
    return out


# --- translations ---------------------------------------------------------------------------


def translation_get(survey, node, lang):
    """The texts of the factors in `lang` for the translation page: {"a": {factor: name}, "l": {level: label},
    "d": {level: definition}}."""
    t = (node.get("texts") or {}).get(lang, {})
    out = {"a": {}, "l": {}, "d": {}}
    for a in node.get("attributes") or []:
        out["a"][a["id"]] = (t.get("attributes") or {}).get(a["id"], "")
        for l in a.get("levels", []):
            out["l"][l["id"]] = (t.get("levels") or {}).get(l["id"], "")
            out["d"][l["id"]] = (t.get("level_defs") or {}).get(l["id"], "")
    return out


def translation_put(survey, node, lang, values):
    texts = copy.deepcopy(node.get("texts") or {})
    t = texts.setdefault(lang, {})
    for kind, bucket in (("a", "attributes"), ("l", "levels"), ("d", "level_defs")):
        for xid, text in ((values or {}).get(kind) or {}).items():
            t.setdefault(bucket, {})[xid] = text
    node["texts"] = texts
    materialize(survey, node)


# --- preview --------------------------------------------------------------------------------


def preview(survey, node, blocks, n, lang=None):
    """The texts of the loop's questions with the levels of vignette n put in: [(item code, text)], for the
    section "Vorschau" (the longest text of each element that uses a placeholder)."""
    from app.items.base import placeholders

    lang = lang or survey.default_language
    rows = node.get("rows") or []
    if not rows:
        return []
    row = rows[(n - 1) % len(rows)]
    values = {c["name"]: ((row.get("values") or {}).get(c["id"]) or {}).get(lang)
              or ((row.get("values") or {}).get(c["id"]) or {}).get(survey.default_language, "")
              for c in node.get("columns") or [] if not c.get("hidden")}
    names = set(values)
    out = []
    for block in blocks:
        for item in block.items:
            texts = [s for s in _strings(item.t(lang)) if placeholders(s) & names]
            if texts:
                text = max(texts, key=len)
                out.append((item.code, re.sub(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*(\|[^}]*)?\}\}",
                                              lambda m: values.get(m.group(1), m.group(0)), text)))
    return out


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def deck_table(survey, node, lang=None):
    """[(deck number, [(vignette number, [level label per factor])])] for the preview."""
    f = factors(survey, node)
    t = f.t(lang)
    attrs = node.get("attributes") or []
    plan = node.get("plan") or {}
    by_id = dict(zip(plan.get("ids") or [], plan.get("profiles") or []))
    number = {rid: n for n, rid in enumerate(plan.get("ids") or [], start=1)}
    out = []
    for d, deck in enumerate(plan.get("decks_rows") or [], start=1):
        out.append((d, [(number[rid], [(t.get("levels") or {}).get(a["levels"][li]["id"], "?") for a, li in zip(attrs, by_id[rid])])
                        for rid in deck if rid in by_id]))
    return out


# --- export and analysis --------------------------------------------------------------------

SHROUT_FLEISS = ("Shrout, P. E., & Fleiss, J. L. (1979). Intraclass correlations: Uses in assessing rater reliability. "
                 "Psychological Bulletin, 86(2), 420–428. https://doi.org/10.1037/0033-2909.86.2.420")
DONNER = ("Donner, A. (1986). A review of inference procedures for the intraclass correlation coefficient in the one-way "
          "random effects model. International Statistical Review, 54(1), 67–82. https://doi.org/10.2307/1403259")
CAMERON_MILLER = ("Cameron, A. C., & Miller, D. L. (2015). A practitioner's guide to cluster-robust inference. Journal of "
                  "Human Resources, 50(2), 317–372. https://doi.org/10.3368/jhr.50.2.317")


def nodes(survey):
    from app import flow

    return [n for n, _, _ in flow.walk(flow.tree(survey)) if is_vignettes(n)]


def _answer_items(survey, node):
    from app import loops

    return [it for b in loops.blocks_in(survey, node) for it in b.items if it.kind.collects_answer]


def long_columns(survey, node):
    cols = ["deck", "position", "vignette"]
    for a in node.get("attributes") or []:
        cols += [a["code"], f"{a['code']}_label"]
    for it in _answer_items(survey, node):
        cols += [c for c, _ in it.kind.export_columns(it)]
    return cols


def long_rows(survey, node, response):
    """One row per vignette the person was shown: deck, position, vignette number, the level (number from 1 and
    label in the default language) of every factor, then the answers to the questions of the loop."""
    plan = node.get("plan") or {}
    shown = ((response.layout or {}).get("loops") or {}).get(node["id"]) or []
    by_id = dict(zip(plan.get("ids") or [], plan.get("profiles") or []))
    number = {rid: n for n, rid in enumerate(plan.get("ids") or [], start=1)}
    t = factors(survey, node).t()
    attrs = node.get("attributes") or []
    items = _answer_items(survey, node)
    answers = response.answers or {}
    for pos, rid in enumerate(shown, start=1):
        profile = by_id.get(rid)
        if profile is None:
            continue
        row = [deck_of(node, rid), pos, number.get(rid)]
        for a, li in zip(attrs, profile):
            row += [li + 1, (t.get("levels") or {}).get(a["levels"][li]["id"], "")]
        for it in items:
            answer = answers.get(f"{it.id}#{rid}")
            # with the values that need the participation itself (sides of an assignment, a voice file), as the
            # wide export (export.rows)
            values = {**it.kind.export_values(it, answer), **it.kind.export_response_values(it, answer, response)}
            row += [values.get(c) for c, _ in it.kind.export_columns(it)]
        yield row


def _number(v):
    if isinstance(v, bool) or v is None or v == "":
        return None
    try:
        x = float(str(v).replace(",", "."))
    except ValueError:
        return None
    return x if x == x and abs(x) != float("inf") else None


def analysis(survey, table):
    """Per vignette loop and numeric question: the mean per level of each factor with a 95 % interval and a
    regression of the answer on all factors (first level as reference) with standard errors clustered by person
    (CR1; Cameron & Miller, 2015), plus the intraclass correlation (one-way ANOVA estimator). From the rows of
    export.rows() (same pool as the results page): the answer of row n is in "<code>_<n>", the rows a person saw in
    "<loop>_order". Only questions measured on a scale (metric or ordinal, study_plan._level): the answer codes
    of a nominal question are no numbers to average; those are listed in "skipped". Codes that count as missing
    ("weiß nicht", "keine Angabe") are left out as in hypotheses._values (code review 2026-10-15, C1/C2)."""
    import numpy as np

    from app import study_plan

    out = []
    for node in nodes(survey):
        if not plan_current(survey, node):
            continue
        rows = node.get("rows") or []
        rid_of = {n: r["id"] for n, r in enumerate(rows, start=1)}
        plan = node.get("plan") or {}
        by_id = dict(zip(plan.get("ids") or [], plan.get("profiles") or []))
        attrs = node.get("attributes") or []
        t = factors(survey, node).t()
        entry = {"name": node.get("name") or node.get("code"), "code": node.get("code"), "questions": [], "skipped": []}
        for it in _answer_items(survey, node):
            cols = [c for c, _ in it.kind.export_columns(it)]
            if len(cols) != 1:
                continue
            if study_plan._level(it) not in ("metric", "ordinal"):
                entry["skipped"].append(it.code)
                continue
            gone = {str(m) for m in it.kind.missing_codes(it, cols[0])}
            obs = []  # (person, profile, y)
            for r in table:
                order = str(r.get(f"{node['code']}_order") or "")
                for part in order.split("|"):
                    if not part.isdigit() or int(part) not in rid_of:
                        continue
                    raw = r.get(f"{cols[0]}_{part}")
                    y = None if raw is None or _code(raw) in gone else _number(raw)
                    profile = by_id.get(rid_of[int(part)])
                    if y is not None and profile is not None:
                        obs.append((r.get("resp_id"), profile, y))
            persons = {p for p, _, _ in obs}
            if len(obs) < 10 or len(persons) < 3:
                if obs:
                    entry["questions"].append({"code": it.code, "question": it.text("question"), "n": len(obs),
                                               "persons": len(persons), "too_few": True})
                continue
            entry["questions"].append(_fit(it, obs, attrs, t, np, pairs(node)))
        if entry["questions"] or entry["skipped"]:
            out.append(entry)
    return out


def _code(v):
    """An answer as the text of its code ("-98" for -98.0), to compare with missing_codes."""
    return str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)


def _fit(item, obs, attrs, t, np, inter=()):
    from app import group_tests, intervals

    y = np.array([o[2] for o in obs], dtype=float)
    groups = [o[0] for o in obs]
    G = len(set(groups))
    levels = [[(t.get("levels") or {}).get(l["id"], "?") for l in a["levels"]] for a in attrs]
    by_level = []
    for j, a in enumerate(attrs):
        rows = []
        for li, label in enumerate(levels[j]):
            picked = [(g, v) for g, o, v in zip(groups, obs, y) if o[1][j] == li]
            if not picked:
                rows.append({"label": label, "n": 0, "mean": None, "ci": None})
                continue
            ys = np.array([v for _, v in picked])
            m = float(ys.mean())
            sums = {}  # the standard error of the mean, clustered by person
            for g, v in picked:
                sums[g] = sums.get(g, 0.0) + (v - m)
            g_n = len(sums)
            ci = None
            if g_n > 1:
                se = float(np.sqrt(sum(s * s for s in sums.values()) * g_n / (g_n - 1)) / len(ys))
                q = intervals.t_quantile(0.975, g_n - 1)
                ci = (m - q * se, m + q * se)
            rows.append({"label": label, "n": len(picked), "mean": m, "ci": ci})
        by_level.append({"code": a["code"], "label": (t.get("attributes") or {}).get(a["id"], a["code"]), "levels": rows})
    # regression with dummies, the first level of each factor as reference
    names, cols = ["(Konstante)"], [np.ones(len(obs))]
    for j, a in enumerate(attrs):
        for li in range(1, len(a["levels"])):
            names.append(f"{a['code']}: {levels[j][li]}")
            cols.append(np.array([1.0 if o[1][j] == li else 0.0 for o in obs]))
    for ja, jb in inter:  # the interactions the plan was made for: products of the dummies
        for la in range(1, len(attrs[ja]["levels"])):
            for lb in range(1, len(attrs[jb]["levels"])):
                names.append(f"{attrs[ja]['code']}: {levels[ja][la]} × {attrs[jb]['code']}: {levels[jb][lb]}")
                cols.append(np.array([1.0 if o[1][ja] == la and o[1][jb] == lb else 0.0 for o in obs]))
    X = np.column_stack(cols)
    N, K = X.shape
    coefs, r2 = None, None
    if N > K and G > 1 and np.linalg.matrix_rank(X) == K:
        XtX_inv = np.linalg.inv(X.T @ X)
        b = XtX_inv @ X.T @ y
        u = y - X @ b
        meat = np.zeros((K, K))
        idx = {}
        for n, g in enumerate(groups):
            idx.setdefault(g, []).append(n)
        for members in idx.values():
            s = X[members].T @ u[members]
            meat += np.outer(s, s)
        V = XtX_inv @ meat @ XtX_inv * (G / (G - 1)) * ((N - 1) / (N - K))
        se = np.sqrt(np.clip(np.diag(V), 0, None))
        q = intervals.t_quantile(0.975, G - 1)
        coefs = []
        for name, est, s in zip(names, b, se):
            tv = float(est / s) if s > 0 else None
            coefs.append({"name": name, "b": float(est), "se": float(s),
                          "ci": (float(est - q * s), float(est + q * s)),
                          "p": group_tests.t_sf2(tv, G - 1) if tv is not None else None})
        ss_tot = float(((y - y.mean()) ** 2).sum())
        r2 = 1 - float((u ** 2).sum()) / ss_tot if ss_tot > 0 else None
    return {"code": item.code, "question": item.text("question"), "n": N, "persons": G, "by_level": by_level,
            "coefs": coefs, "r2": r2, "icc": _icc(groups, y, np)}


def _icc(groups, y, np):
    """ICC(1) from a one-way ANOVA with unequal group sizes: (MSB - MSW) / (MSB + (n0 - 1) MSW)."""
    idx = {}
    for g, v in zip(groups, y):
        idx.setdefault(g, []).append(float(v))
    k, N = len(idx), len(y)
    if k < 2 or N <= k:
        return None
    grand = float(np.mean(y))
    ssb = sum(len(v) * (float(np.mean(v)) - grand) ** 2 for v in idx.values())
    ssw = sum(float(((np.array(v) - np.mean(v)) ** 2).sum()) for v in idx.values())
    msb, msw = ssb / (k - 1), ssw / (N - k)
    n0 = (N - sum(len(v) ** 2 for v in idx.values()) / N) / (k - 1)
    denom = msb + (n0 - 1) * msw
    return float((msb - msw) / denom) if denom > 0 else None


# --- documentation ----------------------------------------------------------------------------


def describe(survey, node):
    """What codebook and method section say about a vignette loop: factors, plan, decks, prohibitions, AI marks."""
    from app import doe

    f = factors(survey, node)
    attrs = f.attributes(f)
    plan = node.get("plan") or {}
    stats = plan_stats(node)
    decks = plan.get("decks_rows") or []
    return {"name": node.get("name") or node.get("code"), "code": node.get("code"),
            "factors": [{"code": a["code"], "label": a["label"], "levels": [l for l in a["level_labels"]], "ai": bool(a.get("ai")),
                         "ai_levels": sum(1 for l in a.get("levels", []) if l.get("ai"))} for a in attrs],
            "method": plan.get("method"), "current": plan_current(survey, node),
            "universe": doe.universe(level_counts(node)) if attrs else 0, "allowed": allowed_count(survey, node),
            "prohibitions": len(node.get("prohibitions") or []), "n": len(plan.get("profiles") or []),
            "decks": len(decks), "per_person": len(decks[0]) if decks else 0,
            "d_eff": stats.get("d_eff"), "deck_eff": stats.get("deck_eff"), "deck_within": bool(stats.get("deck_within")),
            "interactions": [f"{attrs[a]['label']} × {attrs[b]['label']}" for a, b in pairs(node)],
            "between": [attrs[n]["label"] for n in between(node)],
            "order": node.get("order", "random")}


def codebook_note(survey, node, lang=None):
    """The note of a vignette loop in the codebook; lang: the reader's language (app.items.base.reader). The
    names and levels of the factors stay as the researchers wrote them."""
    from app.items.base import reader

    d = describe(survey, node)
    if reader(lang) == "en":
        return _codebook_note_en(d)
    parts = ["Vignetten: " + "; ".join(
        f"{x['code']} = {x['label']} ({', '.join(x['levels'])})" for x in d["factors"])]
    if d["current"]:
        parts.append(("alle Kombinationen" if d["method"] == "full" else "D-optimaler Ausschnitt")
                     + f": {d['n']} Vignetten von {d['allowed']} erlaubten, D-Effizienz {_de(d['d_eff'])}"
                     + (f", in den Decks {_de(d['deck_eff'])}" if d["deck_eff"] is not None else "")
                     + (" (nur Faktoren innerhalb der Personen)" if d["deck_within"] and d["deck_eff"] is not None else ""))
        parts.append(f"{d['decks']} {'Deck' if d['decks'] == 1 else 'Decks'} zu {d['per_person']}, je Person das bis "
                     "dahin seltenste Deck" + ("; zufällige Reihenfolge" if d["order"] == "random" else ""))
    if d["prohibitions"]:
        parts.append(f"{d['prohibitions']} ausgeschlossene Stufenpaare")
    if d["interactions"]:
        parts.append("Plan auch für die Wechselwirkungen " + ", ".join(d["interactions"]))
    if d["between"]:
        parts.append("nur zwischen Personen (gleich in allen Vignetten eines Decks): " + ", ".join(d["between"]))
    marked = [x["code"] for x in d["factors"] if x["ai"]]
    levels = sum(x["ai_levels"] for x in d["factors"])
    if marked or levels:
        parts.append("KI-generiert: " + ", ".join(([f"Faktoren {', '.join(marked)}"] if marked else [])
                                                  + ([f"{levels} {'Stufe' if levels == 1 else 'Stufen'}"] if levels else []))
                     + " (von einer KI vorgeschlagen, von den Forschenden ausgewählt)")
    parts.append("Langformat: vignettes_" + str(d["code"]) + "_long.csv")
    return "; ".join(parts)


def _codebook_note_en(d):
    """codebook_note() in English (decimal point)."""
    def num(x):
        return "–" if x is None else f"{x:.1f}"

    parts = ["Vignettes: " + "; ".join(
        f"{x['code']} = {x['label']} ({', '.join(x['levels'])})" for x in d["factors"])]
    if d["current"]:
        parts.append(("all combinations" if d["method"] == "full" else "D-optimal subset")
                     + f": {d['n']} vignettes of {d['allowed']} allowed, D-efficiency {num(d['d_eff'])}"
                     + (f", in the decks {num(d['deck_eff'])}" if d["deck_eff"] is not None else "")
                     + (" (only factors within people)" if d["deck_within"] and d["deck_eff"] is not None else ""))
        parts.append(f"{d['decks']} {'deck' if d['decks'] == 1 else 'decks'} of {d['per_person']}, each person gets the "
                     "deck used least so far" + ("; random order" if d["order"] == "random" else ""))
    if d["prohibitions"]:
        parts.append(f"{d['prohibitions']} excluded pairs of levels")
    if d["interactions"]:
        parts.append("plan also for the interactions " + ", ".join(d["interactions"]))
    if d["between"]:
        parts.append("only between people (the same in all vignettes of a deck): " + ", ".join(d["between"]))
    marked = [x["code"] for x in d["factors"] if x["ai"]]
    levels = sum(x["ai_levels"] for x in d["factors"])
    if marked or levels:
        parts.append("AI-generated: " + ", ".join(([f"factors {', '.join(marked)}"] if marked else [])
                                                  + ([f"{levels} {'level' if levels == 1 else 'levels'}"] if levels else []))
                     + " (suggested by an AI, chosen by the researchers)")
    parts.append("long format: vignettes_" + str(d["code"]) + "_long.csv")
    return "; ".join(parts)


def methods_text(survey, node):
    """German sentences for the method section."""
    d = describe(survey, node)
    if not d["factors"] or not d["current"]:
        return ""
    factor_list = ", ".join(f"{x['label']} ({len(x['levels'])} Stufen)" for x in d["factors"])
    out = [f"In einem faktoriellen Survey (Vignetten „{d['name']}“) variierten {len(d['factors'])} "
           f"{'Faktor' if len(d['factors']) == 1 else 'Faktoren'}: {factor_list}."]
    if d["method"] == "full":
        out.append(f"Alle {d['n']} erlaubten Kombinationen wurden verwendet")
    else:
        out.append(f"Aus den {d['universe']} möglichen Kombinationen"
                   + (f" ({d['allowed']} nach dem Ausschluss von {d['prohibitions']} unplausiblen Stufenpaaren)"
                      if d["prohibitions"] else "")
                   + f" wurde ein D-optimaler Ausschnitt von {d['n']} Vignetten gezogen"
                   + (f", der neben den Haupteffekten die Wechselwirkungen {', '.join(d['interactions'])} schätzbar macht"
                      if d["interactions"] else "")
                   + f" (D-Effizienz {_de(d['d_eff'])}; Kuhfeld et al., 1994)")
    out[-1] += (f" und per D-effizienter Blockbildung auf {d['decks']} Decks zu je {d['per_person']} Vignetten verteilt "
                f"(D-Effizienz in den Decks {_de(d['deck_eff'])}"
                + (", berechnet über die Faktoren innerhalb der Personen" if d["deck_within"] else "")
                + "; Atzmüller & Steiner, 2010)." if d["decks"] > 1 else ".")
    if d["between"]:
        out.append(f"{', '.join(d['between'])} {'variierte' if len(d['between']) == 1 else 'variierten'} nur zwischen "
                   "den Personen: Alle Vignetten eines Decks hatten dieselbe Stufe, die übrigen Faktoren wurden je "
                   "Kombination dieser Stufen in gleich viele Decks geplant.")
    out.append("Jede Person erhielt " + ("das bis dahin am seltensten vergebene Deck" if d["decks"] > 1 else "alle Vignetten")
               + (", die Vignetten in zufälliger Reihenfolge." if d["order"] == "random" else "."))
    if any(x["ai"] or x["ai_levels"] for x in d["factors"]):
        out.append("Ein Teil der Faktoren oder Stufen wurde von einer KI vorgeschlagen und von den Forschenden ausgewählt.")
    return " ".join(out)


def methods_text_en(survey, node):
    """The same facts in English, for the AI that writes the method section (methods.facts_text)."""
    d = describe(survey, node)
    if not d["factors"] or not d["current"]:
        return ""
    return (f"Factorial survey (vignettes \"{d['name']}\"): {len(d['factors'])} factors ("
            + ", ".join(f"{x['label']}: {len(x['levels'])} levels" for x in d["factors"]) + "); "
            + ("all allowed combinations" if d["method"] == "full" else
               f"D-optimal sample of {d['n']} of {d['allowed']} allowed combinations ({d['prohibitions']} implausible level "
               f"pairs excluded), D-efficiency {d['d_eff']}")
            + (f"; D-efficient blocking into {d['decks']} decks of {d['per_person']} (within-deck D-efficiency {d['deck_eff']}"
               + (", over the within-person factors" if d["deck_within"] else "") + "), "
               "each person got the least used deck" if d["decks"] > 1 else "")
            + ("; the design also estimates the interactions " + ", ".join(d["interactions"]) if d["interactions"] else "")
            + ("; between-person factors (same level in all vignettes of a deck): " + ", ".join(d["between"]) if d["between"] else "")
            + ("; vignette order random" if d["order"] == "random" else "")
            + ("; some factors or levels were suggested by an AI and chosen by the researchers"
               if any(x["ai"] or x["ai_levels"] for x in d["factors"]) else "")
            + ". Sources: Kuhfeld et al. (1994); Atzmüller & Steiner (2010); Auspurg & Hinz (2015).")


def _de(x, places=1):
    return "–" if x is None else f"{x:.{places}f}".replace(".", ",")
