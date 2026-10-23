"""D-optimal subset of a loop table: the loop keeps its typed table, the
researcher ticks the columns that are factors, and Askurious picks the rows that together make the best plan for a
main-effects model, split into decks (app/doe.py). Rows not picked stay in the table and are never shown.

For tables whose rows cannot be made from factors: each row has its own image or wording. A table that is a
loop that switches to factors keeps its factor columns as factors and a current plan as it is (app/vignettes.to_factors).

Stored in the loop node: "mode": "plan" and
  "subset": {"cols": [column ids], "per_person": 6, "decks": 4,
             "sig": "...", "decks_rows": [[row id, ...], ...], "stats": {...}}
Levels are the distinct values of a factor column in the default language, in the order of the table."""
import hashlib
import json
import random

KEY = "subset"


def active(node):
    return bool(node) and node.get("t") == "loop" and node.get("mode") == "plan" and node.get("source") != "factors"


def settings(node):
    return {"cols": [], "per_person": 6, "decks": 1, **(node.get(KEY) or {})}


def factor_space(survey, node):
    """(columns, levels per column [labels], [(row id, profile)]) of the ticked columns; rows with an empty cell in a
    factor column are no candidates."""
    from app import loops

    cfg = settings(node)
    by_id = {c["id"]: c for c in node.get("columns") or []}
    cols = [by_id[c] for c in cfg["cols"] if c in by_id]
    default = survey.default_language
    levels = [[] for _ in cols]
    candidates = []
    for row in node.get("rows") or []:
        profile = []
        for j, c in enumerate(cols):
            v = loops.value(node, row, c, default, default).strip()
            if not v:
                profile = None
                break
            if v not in levels[j]:
                levels[j].append(v)
            profile.append(levels[j].index(v))
        if profile is not None:
            candidates.append((row["id"], profile))
    return cols, levels, candidates


def signature(survey, node):
    cfg = settings(node)
    cols, levels, candidates = factor_space(survey, node)
    shape = [[c["id"] for c in cols], levels, candidates, cfg["per_person"], cfg["decks"]]
    return hashlib.sha1(json.dumps(shape).encode()).hexdigest()[:12]


def current(survey, node):
    cfg = settings(node)
    return bool(cfg.get("decks_rows")) and cfg.get("sig") == signature(survey, node)


def problems(survey, node):
    from app.lang import de_en

    cols, levels, candidates = factor_space(survey, node)
    out = []
    if not cols:
        out.append(de_en("Mindestens eine Spalte als Faktor ankreuzen.", "Tick at least one column as a factor."))
    for c, lv in zip(cols, levels):
        if len(lv) < 2:
            out.append(de_en("Spalte {name}: Braucht mindestens zwei verschiedene Werte.",
                             "Column {name}: needs at least two different values.", name=c["name"]))
    return out


def generate(survey, node, seed=None):
    from app import doe

    found = problems(survey, node)
    if found:
        raise ValueError(" ".join(found))
    cfg = settings(node)
    cols, levels, candidates = factor_space(survey, node)
    counts = [len(lv) for lv in levels]
    n = cfg["per_person"] * cfg["decks"]
    rng = random.Random(seed)
    picked = doe.d_optimal_rows([p for _, p in candidates], counts, n, seed=rng.randrange(1 << 30))
    profiles = [candidates[i][1] for i in picked]
    decks = doe.blocks(profiles, counts, cfg["decks"], seed=rng.randrange(1 << 30))
    ids = [candidates[picked[i]][0] for deck in decks for i in deck]
    ordered = [profiles[i] for deck in decks for i in deck]
    size = cfg["per_person"]
    cfg.update(sig=signature(survey, node), decks_rows=[ids[t * size:(t + 1) * size] for t in range(cfg["decks"])],
               stats=doe.stats(ordered, counts, [list(range(t * size, (t + 1) * size)) for t in range(cfg["decks"])]))
    node[KEY] = cfg
    return cfg


def in_plan(node):
    return {r for d in settings(node).get("decks_rows") or [] for r in d}


def choose_rows(survey, node, rng, counts):
    from app import vignettes

    if not current(survey, node):
        return []
    return vignettes.pick_deck(settings(node)["decks_rows"], {r["id"] for r in node.get("rows") or []}, counts, rng,
                               node.get("order", "random"))
