"""What balanced assignment and the live comparison need to know about the participations so far, kept per
server process for a few seconds.

Balanced randomizers, loops that show k of n rows and the design versions of choice tasks give the next person
what was shown least. That used to read the layout of every participation of the survey, twice per new
participant (first page and first submit): the cost of a start grew with the success of the survey, and anyone
could trigger it without limit by opening the link. The live comparison read all answers on every page view.

Now one pass over the participations fills a small table of counts, which is kept for BALANCE_SECONDS. A
participation stored by this process is added to the table at once (`note`), so within a process the counts
stay exact; what other processes store arrives with the next pass. Assignments were never atomic (two people
starting at the same moment got the same condition before, too); over a survey the balance evens out.
"""
import copy
import time
from contextlib import contextmanager

from flask import current_app, g

from app.extensions import db


def _store():
    return current_app.extensions.setdefault("balance", {})


def _ttl():
    return current_app.config.get("BALANCE_SECONDS", 15)


def cached(key, load):
    """`load()`, or its result from at most BALANCE_SECONDS ago (0: always fresh)."""
    ttl = _ttl()
    store = _store()
    entry = store.get(key)
    now = time.monotonic()
    if entry is not None and ttl and now - entry[0] < ttl:
        return entry[1]
    value = load()
    if ttl:
        store[key] = (now, value)
        if len(store) > 500:  # surveys long closed: drop what ran out
            for k in [k for k, (at, _) in store.items() if now - at >= ttl]:
                del store[k]
    return value


def _add(table, layout, step=1):
    """Count what `layout` got (step -1: take it away again, never below 0)."""
    from app import flow

    layout = layout or {}

    def count(row, key):
        row[key] = max(row.get(key, 0) + step, 0)

    # a randomizer behind a branch condition counts only for those who reached it: the balance evens out among them
    for rid, children in flow.reached_conditions(layout).items():
        row = table["rand"].setdefault(rid, {})
        for child in children or []:
            count(row, child)
    for nid, rows in (layout.get("loops") or {}).items():
        row = table["loop"].setdefault(nid, {})
        for rid in rows or []:
            count(row, rid)
    for item_id, state in (layout.get("cj") or {}).items():
        version = (state or {}).get("version")
        if isinstance(version, int):
            count(table["version"].setdefault(str(item_id), {}), version)


# The parts of a layout the counts read (_add, flow.reached_conditions): read from the database without the rest
# (the pages and orders of a long loop are most of a layout; code review 2026-10-07, T4)
COUNTED_PARTS = ("conditions", "rand_in", "reached", "loops", "cj")


class _Frozen(dict):
    """The counts a draw reads (frozen): read only when the draw asks for them, so a survey without balanced
    randomizers, counted loops or design versions reads nothing (code review 2026-10-07, T4)."""

    def __init__(self):
        super().__init__(rand={}, loop={}, version={})
        self.read = False


def counts(survey):
    """How often real participations got what: {"rand": {randomizer id: {child id: n}}, "loop": {loop id:
    {row id: n}}, "version": {item id: {design version: n}}}. While a layout is drawn under `frozen`, the
    counts it was drawn from."""
    held = getattr(g, "balance_frozen", {}).get(survey.id)
    if held is not None:
        if isinstance(held, _Frozen) and not held.read:  # the first question of the draw: the counts of now
            held.update(copy.deepcopy(_current(survey)))
            held.read = True
        return held
    return _current(survey)


def _current(survey):
    from app.models import Response

    def load():
        table = {"rand": {}, "loop": {}, "version": {}}
        parts = [Response.layout[part] for part in COUNTED_PARTS]
        for found in db.session.execute(db.select(*parts).filter_by(survey_id=survey.id, is_preview=False)):
            _add(table, {part: value for part, value in zip(COUNTED_PARTS, found) if value is not None})
        return table

    return cached(("counts", survey.id), load)


def note(survey, layout):
    """A real participation with this layout was just stored: count it in the table this process keeps."""
    entry = _store().get(("counts", survey.id))
    if entry is not None:
        _add(entry[1], layout)


def unnote(survey, layout):
    """A real participation with this layout was just removed (declined): taken out of the table this process
    keeps, instead of reading every participation again; the pools of the live comparison stay, a declined
    participation is never completed (code review 2026-10-07, T4, T5)."""
    entry = _store().get(("counts", survey.id))
    if entry is not None:
        _add(entry[1], layout, step=-1)


def note_reached(survey_id, drawn, rids):
    """A stored real participation just reached these randomizers behind a branch condition (runtime.reach): they
    count from now on, in the table this process keeps as well."""
    entry = _store().get(("counts", survey_id))
    if entry is not None:
        for rid in rids:
            row = entry[1]["rand"].setdefault(rid, {})
            for child in drawn.get(rid) or []:
                row[child] = row.get(child, 0) + 1


@contextmanager
def frozen(survey, kept=None):
    """Draws a layout from fixed counts: those `kept` from an earlier draw (see `keep`), or else the current
    ones, held for the whole draw. Yields the table used.

    The first page is drawn when the link is opened, but stored only on the first "Next" (participate.py); in
    between other people start, and the same seed with other counts would store another condition than the one
    shown. So the counts of the first draw travel along in the session and the draw is repeated with them."""
    table = _restored(kept) if kept is not None else _Frozen()
    held = g.setdefault("balance_frozen", {})
    before = held.get(survey.id)
    held[survey.id] = table
    try:
        yield table
    finally:
        if before is None:
            held.pop(survey.id, None)
        else:
            held[survey.id] = before


def keep(table, layout, survey=None):
    """The part of `table` the drawing of `layout` read (its randomizers, loops and design versions), small
    enough for the session cookie and with string keys (JSON).

    A loop keeps only the counts of the rows it picked, and one count for all others (above the highest of the
    picked ones): the same draw again (loops.choose_rows sorts the shuffled rows by their counts and takes the
    first k), without a count per row of the table (600 rows made the cookie larger than browsers keep). With
    `survey`, loops whose draw reads no counts (all rows, or not balanced) keep nothing."""
    layout = layout or {}
    out = {}
    for kind, key in (("rand", "conditions"), ("version", "cj")):
        rows = {str(i): {str(c): n for c, n in table[kind][str(i)].items()}
                for i in layout.get(key) or {} if table[kind].get(str(i))}
        if rows:
            out[kind] = rows
    counted = _counted_loops(survey) if survey is not None else None
    kept_loops = {}
    for nid, picked in (layout.get("loops") or {}).items():
        row = table["loop"].get(str(nid))
        if not row or (counted is not None and str(nid) not in counted):
            continue
        mine = {str(r): row.get(r, 0) for r in picked or []}
        kept_loops[str(nid)] = [mine, max(mine.values(), default=0) + 1]
    if kept_loops:
        out["loop"] = kept_loops
    return out


def _counted_loops(survey):
    """Ids of the loops whose draw reads the counts: balanced, k of more than k rows, or one row per level of
    levels with more than one row."""
    from app import flow, loops

    out = set()
    for node, _, _ in flow.walk(flow.tree(survey)):
        if node["t"] == "loop":
            cfg = loops.config_node(node)
            if cfg["balanced"] and cfg["mode"] == "k" and min(cfg["k"], len(cfg["rows"])) < len(cfg["rows"]):
                out.add(str(node["id"]))
            elif (cfg["balanced"] and cfg["mode"] == "level" and node.get("source") != "factors"
                  and loops.shown_rows(survey, cfg) < len(cfg["rows"])):
                out.add(str(node["id"]))  # one row per level: the least shown variant of each level
            elif len((node.get("subset" if node.get("mode") == "plan" else "plan") or {}).get("decks_rows") or []) > 1:
                out.add(str(node["id"]))  # vignettes: the least shown deck (app/vignettes.py)
    return out


class _Rest(dict):
    """The kept counts of a loop: those of the picked rows; every other row has the count `rest`."""

    def __init__(self, picked, rest):
        super().__init__(picked)
        self.rest = rest

    def get(self, key, default=None):
        return super().get(key, self.rest)


def _restored(kept):
    table = {"rand": {}, "loop": {}, "version": {}}
    if not isinstance(kept, dict):
        return table
    for kind in table:
        for i, row in (kept.get(kind) or {}).items():
            if kind == "loop" and isinstance(row, list) and len(row) == 2 and isinstance(row[0], dict) and isinstance(row[1], int):
                # keep(): the picked rows and one count for the rest
                table[kind][i] = _Rest({c: n for c, n in row[0].items() if isinstance(n, int)}, row[1])
            elif isinstance(row, dict):
                # design versions are numbers; JSON made them strings
                table[kind][i] = {int(c) if kind == "version" else c: n for c, n in row.items()
                                  if isinstance(n, int) and (kind != "version" or str(c).isdigit())}
    return table


def forget(survey_id):
    """Counts and pools of this survey are read afresh (a participation was removed or got a new layout)."""
    store = _store()
    for key in [k for k in store if k[1] == survey_id]:
        del store[key]


def least_used(candidates, used, k, rng):
    """k of the candidates, those used least so far; ties in random order. The same choice as "the combination
    of k with the smallest sum", without listing the combinations (12 of 24 are 2.7 million)."""
    order = list(candidates)
    rng.shuffle(order)
    return sorted(order, key=lambda c: used.get(c, 0))[:k]


def marked(survey, sources=("real",), fresh=False):
    """Ids of the completed participations quality assurance marks (export.marked_ids): the speeder rule rests on
    the median of all completion times, the other flags on the whole pool. Kept as long as what they rest on is
    the same (_marked_basis), not for a few seconds: computing them reads every completed participation, and the
    live comparison asked for that every BALANCE_SECONDS (code review 2026-10-07, T5)."""
    from app import export

    if fresh or not _ttl():  # BALANCE_SECONDS 0: always what is there now
        return export.marked_ids(survey, sources)
    key = ("marked", survey.id, tuple(sources))
    basis = _marked_basis(survey, sources)
    store = _store()
    entry = store.get(key)
    if entry is not None and entry[1][0] == basis:
        return entry[1][1]
    found = export.marked_ids(survey, sources)
    store[key] = (time.monotonic(), (basis, found))
    return found


def _marked_basis(survey, sources):
    """What the marked participations rest on, in one small query: the completed participations of the pool (how
    many, the newest, and their row versions, which every change of a stored participation raises), the marking
    rule, the speeder rule and the questions (their attention checks)."""
    import hashlib
    import json

    from app import export, quality
    from app.models import Response

    r = Response
    done = db.session.execute(db.select(db.func.count(r.id), db.func.max(r.id), db.func.sum(r.version)).filter(
        r.survey_id == survey.id, r.source.in_(sources), r.status == r.COMPLETED)).one()
    items = [[i.id, i.type, i.code, i.config] for b in survey.blocks for i in b.items]
    basis = [list(done), survey.qversion, quality.rules(survey), export.threshold(survey), export.speeder_rule(survey),
             export.rsi_limit(survey), items]
    return hashlib.sha256(json.dumps(basis, sort_keys=True, default=str).encode()).hexdigest()


def answers(survey, key, preview=False):
    """[(participation id, its answer stored under `key`)] of completed participations (real; in the preview
    also test and dummy), for the live comparison. Only the one answer is kept, not the whole participation.
    What quality assurance marks is left out, as on the results page. After "Neuer Durchgang" only the participations
    begun in the current run count (lifecycle.run_since); the others stay in the data."""
    from app import lifecycle
    from app.models import Response

    sources = ("real", "test", "dummy") if preview else ("real",)
    since = lifecycle.run_since(survey)

    def load():
        query = db.select(Response.id, Response.answers).filter(
            Response.survey_id == survey.id, Response.status == Response.COMPLETED, Response.source.in_(sources))
        if since is not None:
            query = query.filter(Response.started_at >= since)
        if not preview:
            query = query.filter(Response.is_preview.is_(False))
        drop = marked(survey, sources, fresh=preview)
        return [(rid, (given or {}).get(key)) for rid, given in db.session.execute(query.order_by(Response.id))
                if rid not in drop]

    if preview:  # researchers trying things out: always what is there now
        return load()
    return cached(("answers", survey.id, key, since), load)


def derived(key, lists, build):
    """build(), computed once for these lists of answers (answers(); the very lists kept, not equal ones): what a
    live comparison works out of the sample, e.g. the scale means of the scale profile, which took one second per
    page with 2000 participations (performance audit 2026-10-09, N-5). Kept as long as answers() hands out the same
    lists, so it is exactly what build() gives on them; lists read anew (after BALANCE_SECONDS, or in the preview)
    compute it again. key: what else build() depends on (the element, its sources, the pass of a loop)."""
    if not _ttl():
        return build()
    store = _store()
    entry = store.get(("derived",) + tuple(key))
    if entry is not None and len(entry[1][0]) == len(lists) and all(a is b for a, b in zip(entry[1][0], lists)):
        return entry[1][1]
    value = build()
    store[("derived",) + tuple(key)] = (time.monotonic(), (tuple(lists), value))
    return value
