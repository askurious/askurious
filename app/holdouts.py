"""Holdout tasks of CBC and MaxDiff (concept wahlmodelle-punkt6-konzept.md, stage B, W5-W7, W13).

Fixed tasks the researcher puts together in the editor ("Holdout-Aufgaben" below the design), the same for every
person. They look like the other tasks, come after task 3 and at the end (AFTER, W6), at most MAX of them (W5),
and are locked like the design once the survey has started (W7). Their answers are stored under keys of their
own ("h0", "h1", ...; the tasks have "t0", ...) and exported as <code>_ho1 ... (MaxDiff: _ho1_best, _ho1_worst);
they never enter the counts, the aggregate logit or HB, so they test how well those predict choices they were
not fitted to. Not for ACBC: its concepts are adaptive (W13). The ratings-based conjoint (app/items/cva.py) takes the
CBC functions with one offer per holdout (two in its pairs) and scores them in app/cva_analysis.py holdout_view.

config (CBC):     holdouts [{id, alts: [[level id per attribute], ...]}], one list per alternative of the study
config (MaxDiff): holdouts [{id, statements: [statement id, ...]}], as many statements as a set has

Warm-up tasks (concept aufwaermaufgaben, Ralf 2026-11-23, E1-E7 as recommended) are built the same way: fixed tasks
the same for everyone, at most WARMUP_MAX of them, before the first task of the design ("warmups" in the config,
answers "w0", "w1", export columns <code>_wu1 ..., MaxDiff _wu1_best/_wu1_worst, timing pages _wu1). They look like
every other task and count in "task x of y", but they never enter counts, logit, HB, segments or the simulator.
The functions below take `kind` = "holdouts" (default) or "warmups"; KINDS holds the names per kind.

Results (only with a current HB result): per holdout the hit rate (share of persons whose highest predicted utility,
from their posterior means, is the alternative they chose; ties count in equal parts) with its Wilson interval and
the chance rate 1 / alternatives; the mean absolute error between the predicted shares of preference (logit rule
per person and kept draw, averaged over the draws and then over the persons, as the market simulator: audit
2026-10-05, W-1) and the observed choice shares over the alternatives; both also for the aggregate logit, on the same
persons (Orme, 2009). MaxDiff: the choice "most important" of the holdout set.
"""
import numpy as np

from app import intervals
from app.lang import de_en
from app.choice_predict import (draw_shares, draw_utilities, first_choice, hb_models, pooled_model, shares,
                                task_utilities)

MAX = 3      # holdouts per study (W5)
AFTER = 3    # the first holdout comes after this task, the others at the end (W6)
ORME = ("Orme, B. K. (2009). Fine-tuning CBC and adaptive CBC questionnaires (Sawtooth Software Research Paper "
        "Series). Sawtooth Software. https://content.sawtoothsoftware.com/assets/cf6f1953-db50-4174-8672-06bc04a0d5ef")
# warm-up tasks: the estimates from the first two tasks were the least like those of the whole interview, which "may
# support" warm-up tasks, though all tasks agreed well (pp. 10-11)
JOHNSON_ORME = ("Johnson, R. M., & Orme, B. K. (1996). How many questions should you ask in choice-based conjoint studies? "
                "(Sawtooth Software Research Paper Series). Sawtooth Software. "
                "https://content.sawtoothsoftware.com/assets/a24654f4-0553-4484-9c90-46a5899e8d57")
TYPES = ("conjoint", "maxdiff")
WARMUP_MAX = 2  # warm-up tasks per study (E3)
# per kind: most, answer key prefix, page meta key, form field prefix
KINDS = {"holdouts": {"max": MAX, "answer": "h", "meta": "holdout", "form": "ho"},
         "warmups": {"max": WARMUP_MAX, "answer": "w", "meta": "warmup", "form": "wu"}}


def page_order(n_tasks, n_holdouts, n_warmups=0):
    """[("w", warm-up), ("t", task) or ("h", holdout)] in the order of the pages: the warm-up tasks first, the first
    holdout after task AFTER of the design (when there are more tasks than that), the others after the last task."""
    tasks = [("t", t) for t in range(n_tasks)]
    hold = [("h", h) for h in range(n_holdouts)]
    warm = [("w", w) for w in range(n_warmups)]
    if not hold:
        return warm + tasks
    if n_tasks > AFTER:
        return warm + tasks[:AFTER] + hold[:1] + tasks[AFTER:] + hold[1:]
    return warm + tasks + hold


def stored(item, kind="holdouts"):
    if item.type == "cva" and (item.config or {}).get("form") == "ranking":
        return []  # the ranking puts all offers on one page: no holdouts or warm-ups beside it
    return [h for h in (item.config or {}).get(kind) or [] if isinstance(h, dict)][:KINDS[kind]["max"]]


def page_key(meta):
    """The answer key of a page: "w<n>" (warm-up), "h<n>" (holdout) or "t<task>"."""
    meta = meta or {}
    if "warmup" in meta:
        return f"w{meta['warmup']}"
    if "holdout" in meta:
        return f"h{meta['holdout']}"
    return f"t{meta.get('task', 0)}"


def page_kind(meta):
    """"warmups", "holdouts" or None (a task of the design) for a page."""
    meta = meta or {}
    return "warmups" if "warmup" in meta else ("holdouts" if "holdout" in meta else None)


def pages(order, active_holdouts, active_warmups, extra=None):
    """Page metas from page_order, leaving out incomplete holdouts and warm-ups; "n": the number the page shows
    ("Aufgabe n von ..."). extra: keys every task page gets (e.g. {"v": version})."""
    out = []
    for k, x in order:
        if (k == "h" and x not in active_holdouts) or (k == "w" and x not in active_warmups):
            continue
        n = len(out) + 1
        if k == "t":
            out.append({"task": x, **(extra or {}), "n": n})
        else:
            out.append({"holdout" if k == "h" else "warmup": x, "n": n})
    return out


def timing(order, active_holdouts, active_warmups, unit=("Aufgabe", "Holdout-Aufgabe", "Aufwärmaufgabe")):
    """[(column suffix, label, paradata key)] of the pages in page_order, as timing_pages wants them. unit: the
    German labels of a task, a holdout and a warm-up (MaxDiff: "Set", "Holdout-Set", "Aufwärm-Set")."""
    out = []
    for k, x in order:
        if k == "t":
            out.append((f"_t{x + 1}", f"{unit[0]} {x + 1}", f".t{x}"))
        elif k == "h" and x in active_holdouts:
            out.append((f"_ho{x + 1}", f"{unit[1]} {x + 1}", f".h{x}"))
        elif k == "w" and x in active_warmups:
            out.append((f"_wu{x + 1}", f"{unit[2]} {x + 1}", f".w{x}"))
    return out


def alternatives(item, config=None):
    """Offers per holdout task: the options of a CBC task; the ratings-based conjoint (app/items/cva.py) one offer to
    rate, or two in its pairs."""
    config = item.config if config is None else config
    if item.type == "cva":
        return 2 if config.get("form") == "pairs" else 1
    return int(config.get("alternatives", 2))


# --- CBC -------------------------------------------------------------------------------------------------

def cbc_profiles(item, h, kind="holdouts"):
    """Level indices (per attribute, config order) of each alternative of holdout (or warm-up) h; None if one is
    incomplete (a level removed since, or an alternative added)."""
    rows = stored(item, kind)
    if h >= len(rows):
        return None
    attrs = item.config.get("attributes") or []
    alts = rows[h].get("alts") or []
    n = alternatives(item)
    if len(alts) < n or not attrs:
        return None
    out = []
    for alt in alts[:n]:
        chosen = set(alt or [])
        profile = []
        for a in attrs:
            hit = [k for k, lv in enumerate(a.get("levels") or []) if lv["id"] in chosen]
            if len(hit) != 1:
                return None
            profile.append(hit[0])
        out.append(profile)
    return out


def cbc_active(item, kind="holdouts"):
    """[(holdout index, profiles)] of the complete holdouts (or warm-ups)."""
    out = []
    for h in range(len(stored(item, kind))):
        profiles = cbc_profiles(item, h, kind)
        if profiles is not None:
            out.append((h, profiles))
    return out


def cbc_from_form(item, config, form, kind="holdouts"):
    """The holdouts of the editor form: "ho_count" and per holdout, alternative and attribute a choice of level
    ("ho<h>_<j>_<attribute id>" = level id); warm-ups the same with "wu". config: the item's settings as this form
    leaves them."""
    f = KINDS[kind]["form"]
    count = max(0, min(KINDS[kind]["max"], _int(form.get(f"{f}_count"))))
    attrs = config.get("attributes") or []
    old = stored(item, kind)
    out = []
    for h in range(count):
        alts = []
        for j in range(alternatives(item, config)):
            alt = []
            for a in attrs:
                lid = form.get(f"{f}{h}_{j}_{a['id']}", "")
                if lid in {lv["id"] for lv in a.get("levels") or []}:
                    alt.append(lid)
            alts.append(alt)
        out.append({"id": old[h]["id"] if h < len(old) and old[h].get("id") else _new_id(), "alts": alts})
    return out


def cbc_level_choice(item, h, j, attr_index, kind="holdouts"):
    """The level index shown as chosen in the editor for holdout h, alternative j: the stored one, else a rotation
    (alternative j gets level j of every attribute, holdout h shifts it by h) so new alternatives differ. Warm-ups
    rotate the other way round (from the last level), so they differ from the holdouts (E4)."""
    a = item.config["attributes"][attr_index]
    rows = stored(item, kind)
    if h < len(rows):
        alts = rows[h].get("alts") or []
        if j < len(alts):
            for k, lv in enumerate(a.get("levels") or []):
                if lv["id"] in (alts[j] or []):
                    return k
    n = len(a.get("levels") or []) or 1
    if kind == "warmups":
        return (n - 1 - (j + h)) % n
    return (j + h) % n


def cbc_problems(item, kind="holdouts"):
    """Holdouts (or warm-ups) that cannot be shown (block the start)."""
    if kind == "warmups":
        return [de_en("Aufwärmaufgabe {n}: für jede Option je Merkmal eine Stufe wählen",
                      "Warm-up task {n}: choose a level per attribute for every option", n=h + 1)
                for h in range(len(stored(item, kind))) if cbc_profiles(item, h, kind) is None]
    return [de_en("Holdout-Aufgabe {n}: für jede Option je Merkmal eine Stufe wählen",
                  "Holdout task {n}: choose a level per attribute for every option", n=h + 1)
            for h in range(len(stored(item))) if cbc_profiles(item, h) is None]


def cbc_warnings(item, kind="holdouts"):
    """Identical alternatives and, where it is clear, dominated ones: the same levels except a higher price."""
    warm = kind == "warmups"
    rows = cbc_active(item, kind)
    kind = item.kind
    pa = kind.price_attribute(item)
    numbers = kind.level_numbers(item, pa) if pa is not None else None
    out = []
    for h, profiles in rows:
        for i in range(len(profiles)):
            for j in range(i + 1, len(profiles)):
                p, q = profiles[i], profiles[j]
                if p == q and warm:
                    out.append(de_en("Aufwärmaufgabe {n}: Option {i} und {j} sind gleich.",
                                     "Warm-up task {n}: options {i} and {j} are the same.", n=h + 1, i=i + 1, j=j + 1))
                elif p == q:
                    out.append(de_en("Holdout-Aufgabe {n}: Option {i} und {j} sind gleich.",
                                     "Holdout task {n}: options {i} and {j} are the same.", n=h + 1, i=i + 1, j=j + 1))
                elif numbers and all(x == y for k, (x, y) in enumerate(zip(p, q)) if k != pa) \
                        and numbers[p[pa]] != numbers[q[pa]]:
                    worse, better = (i, j) if numbers[p[pa]] > numbers[q[pa]] else (j, i)
                    if warm:
                        out.append(de_en("Aufwärmaufgabe {n}: Option {w} ist gleich wie Option {b}, nur teurer; die "
                                         "Wahl ist zu leicht, um das Abwägen zu üben.",
                                         "Warm-up task {n}: option {w} is the same as option {b}, only more expensive; "
                                         "the choice is too easy to practise weighing up.",
                                         n=h + 1, w=worse + 1, b=better + 1))
                        continue
                    out.append(de_en("Holdout-Aufgabe {n}: Option {w} ist gleich wie Option {b}, nur teurer; sie wird "
                                     "kaum gewählt und sagt wenig über die Vorhersage.",
                                     "Holdout task {n}: option {w} is the same as option {b}, only more expensive; it "
                                     "is hardly chosen and says little about the prediction.",
                                     n=h + 1, w=worse + 1, b=better + 1))
    return out


# --- MaxDiff ---------------------------------------------------------------------------------------------

def maxdiff_set(item, h, kind="holdouts"):
    """Statement indices of holdout (or warm-up) set h; None if incomplete (a statement removed, a repeated one, a
    size that no longer fits)."""
    rows = stored(item, kind)
    if h >= len(rows):
        return None
    index = {s["id"]: n for n, s in enumerate(item.config.get("statements") or [])}
    ids = rows[h].get("statements") or []
    k = int(item.config.get("set_size", 4))
    found = [index.get(sid) for sid in ids[:k]]
    if len(found) < k or None in found or len(set(found)) < k:
        return None
    return found


def maxdiff_active(item, kind="holdouts"):
    out = []
    for h in range(len(stored(item, kind))):
        s = maxdiff_set(item, h, kind)
        if s is not None:
            out.append((h, s))
    return out


def maxdiff_from_form(item, config, form, kind="holdouts"):
    """The holdout sets of the editor form: "ho_count" and per set and place a statement ("ho<h>_<p>" = its id);
    warm-up sets the same with "wu"."""
    f = KINDS[kind]["form"]
    count = max(0, min(KINDS[kind]["max"], _int(form.get(f"{f}_count"))))
    known = {s["id"] for s in config.get("statements") or []}
    old = stored(item, kind)
    out = []
    for h in range(count):
        ids = [form.get(f"{f}{h}_{p}", "") for p in range(int(config.get("set_size", 4)))]
        out.append({"id": old[h]["id"] if h < len(old) and old[h].get("id") else _new_id(),
                    "statements": [sid for sid in ids if sid in known]})
    return out


def maxdiff_choice(item, h, p, kind="holdouts"):
    """Statement index shown as chosen at place p of holdout set h: the stored one, else a rotation (warm-ups from
    the last statement backwards, so they differ from the holdouts)."""
    rows = stored(item, kind)
    statements = item.config.get("statements") or []
    if h < len(rows):
        ids = rows[h].get("statements") or []
        if p < len(ids):
            for n, s in enumerate(statements):
                if s["id"] == ids[p]:
                    return n
    k = int(item.config.get("set_size", 4))
    n = len(statements) or 1
    if kind == "warmups":
        return (n - 1 - (h * k + p)) % n
    return (h * k + p) % n


def maxdiff_problems(item, kind="holdouts"):
    if kind == "warmups":
        return [de_en("Aufwärm-Set {n}: {k} verschiedene Aussagen wählen", "Warm-up set {n}: choose {k} different statements",
                      n=h + 1, k=item.config.get('set_size', 4))
                for h in range(len(stored(item, kind))) if maxdiff_set(item, h, kind) is None]
    return [de_en("Holdout-Set {n}: {k} verschiedene Aussagen wählen", "Holdout set {n}: choose {k} different statements",
                  n=h + 1, k=item.config.get('set_size', 4))
            for h in range(len(stored(item))) if maxdiff_set(item, h) is None]


# --- import of a survey file -----------------------------------------------------------------------------

def import_fit(config, key, kind="holdouts"):
    """Holdouts (or warm-ups) of a file kept only if they name what exists (level ids of the attributes, statement
    ids); None otherwise. key: "alts" (CBC) or "statements" (MaxDiff)."""
    rows = config.get(kind)
    if not rows:
        return []
    if key == "alts":
        known = {lv["id"] for a in config.get("attributes") or [] for lv in a.get("levels") or []}
        ok = all(all(isinstance(alt, list) and set(alt) <= known for alt in r.get("alts") or []) for r in rows)
    else:
        known = {s["id"] for s in config.get("statements") or []}
        ok = all(set(r.get("statements") or []) <= known for r in rows)
    return rows if ok else None


# --- results page ----------------------------------------------------------------------------------------

def score(chosen, utilities, person_shares=None):
    """chosen: index of the chosen alternative per person; utilities: per person the predicted utilities of the
    alternatives; person_shares: per person the predicted shares of preference (HB: averaged over the kept draws),
    else the logit rule on `utilities`. Hit rate (first choice hits, ties in equal parts) with Wilson interval, and
    the mean absolute error between predicted shares of preference (mean over persons) and observed shares, over the
    alternatives."""
    n = len(chosen)
    if not n:
        return None
    J = len(utilities[0])
    hits = sum(first_choice(u)[c] for c, u in zip(chosen, utilities))
    pred = [0.0] * J
    for row in (person_shares if person_shares is not None else [shares(u) for u in utilities]):
        for j, s in enumerate(row):
            pred[j] += float(s) / n
    obs = [sum(1 for c in chosen if c == j) / n for j in range(J)]
    ci = intervals.wilson(hits, n)
    return {"n": n, "hits": hits, "hit": hits / n, "lo": ci[0] if ci else None, "hi": ci[1] if ci else None,
            "mae": sum(abs(p - o) for p, o in zip(pred, obs)) / J, "pred": pred, "obs": obs}


def _int(x):
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return 0


def _new_id():
    from app.items.base import new_id

    return new_id("h")


def _answers(table, column):
    """{pid: whole number} of one export column."""
    out = {}
    for r in table:
        v = _int(r.get(column)) if r.get(column) not in (None, "") else None
        if v is not None and r.get("pid"):
            out[r["pid"]] = v
    return out


def cbc_view(item, table, entry):
    """Hit rates and share errors of the CBC holdouts, or None (no holdouts, no current HB result)."""
    from app import conjoint_hb

    active = cbc_active(item)
    hb = (entry or {}).get("hb") or {}
    if not active or hb.get("state") != "current":
        return None
    result = conjoint_hb.load(item)
    if not result:
        return None
    kind = item.kind
    with_none = bool(item.config.get("none"))
    pa = kind.price_attribute(item)
    numbers = kind.level_numbers(item, pa) if pa is not None else None
    people = hb_models(result)
    params = conjoint_hb.draw_parameters(result)
    index = {pid: n for n, pid in enumerate(result["pids"])}
    # one centre per model (audit data storage and calculations 2026-10-21, D6): the HB coding's own, and for the
    # aggregate model the mean price level only if that model has a price
    hb_center = (result.get("coding") or {}).get("price_center") or 0.0
    pooled = None
    if (entry or {}).get("model"):
        pooled_center = (sum(numbers) / len(numbers) if numbers and (entry["model"].get("price") or {}).get("beta")
                         is not None else 0.0)
        pooled = pooled_model(entry["model"], [a["code"] for a in item.config["attributes"]], pooled_center)
    J = int(item.config["alternatives"])
    rows = []
    for h, profiles in active:
        given = _answers(table, f"{item.code}_ho{h + 1}")
        pids = [pid for pid in result["pids"] if pid in given]
        prices = [numbers[p[pa]] for p in profiles] if numbers and result.get("price") is not None else None
        chosen = [J if given[pid] == 0 else given[pid] - 1 for pid in pids]
        keep = [k for k, c in enumerate(chosen) if 0 <= c < J + with_none]
        pids, chosen = [pids[k] for k in keep], [chosen[k] for k in keep]
        hb_utils = [task_utilities(people[pid], profiles, prices, with_none) for pid in pids]
        P, _ = draw_shares(draw_utilities(params, result["spans"], profiles, prices, with_none, hb_center)) if pids \
            else (None, None)
        person_shares = P[[index[pid] for pid in pids]] if pids else None
        row = {"h": h + 1, "alts": J + with_none, "chance": 1 / (J + with_none),
               "hb": score(chosen, hb_utils, person_shares), "pooled": None}
        if pooled is not None and pids:
            pooled_prices = [numbers[p[pa]] for p in profiles] if numbers and pooled["price"] is not None else None
            u = task_utilities(pooled, profiles, pooled_prices, with_none)
            row["pooled"] = score(chosen, [u] * len(chosen))
        rows.append(row)
    return {"rows": rows, "none": with_none, "kind": "cbc"}


def maxdiff_view(item, table, entry):
    """As cbc_view for the MaxDiff holdout sets: the choice "most important"."""
    from app import maxdiff_hb

    active = maxdiff_active(item)
    hb = (entry or {}).get("hb") or {}
    if not active or hb.get("state") != "current":
        return None
    result = maxdiff_hb.load(item)
    if not result:
        return None
    col = {i: j for j, i in enumerate(result["est"])}
    mnl = (entry or {}).get("mnl") or {}
    pooled_u = {r["n"] - 1: r["u"] for r in mnl.get("rows") or []} if mnl and not mnl.get("disconnected") else None
    index = {pid: n for n, pid in enumerate(result["pids"])}
    draws = maxdiff_hb.draw_utilities(result)                  # draws x persons x estimated statements
    rows = []
    for h, s in active:
        given = _answers(table, f"{item.code}_ho{h + 1}_best")
        pids = [pid for pid in result["pids"] if pid in given and given[pid] - 1 in s]
        chosen = [s.index(given[pid] - 1) for pid in pids]
        # a statement never shown in the tasks has no utility: it counts as average (0, the utilities are centred)
        utils = [[result["util"][index[pid]][col[i]] if i in col else 0.0 for i in s] for pid in pids]
        person_shares = None
        if pids:
            U = np.stack([draws[:, :, col[i]] if i in col else np.zeros(draws.shape[:2]) for i in s], axis=2)
            person_shares = draw_shares(U[:, [index[pid] for pid in pids], :])[0]
        row = {"h": h + 1, "alts": len(s), "chance": 1 / len(s), "hb": score(chosen, utils, person_shares),
               "pooled": None}
        if pooled_u is not None and pids:
            row["pooled"] = score(chosen, [[pooled_u.get(i, 0.0) for i in s]] * len(chosen))
        rows.append(row)
    return {"rows": rows, "none": False, "kind": "maxdiff"}
