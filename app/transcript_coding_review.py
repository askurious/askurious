"""Interviews kodieren, stage 3: looking at the category system together and tidying it up (app/transcript_coding.py;
concept "Interviews kodieren", all as recommended, Ralf 2026-10-27).

Overviews, all over the interviews the actor sees in the analysis: the category system as a table (passages,
interviews, who coded, the mutual check) or as a map (a tree drawn as SVG, also to download), the passages of one
category, categories × interviews (also as CSV), the whole history.

"Unscharf": two categories whose borders blur, found three ways: passages coded with both (overlapping by at least
half of the shorter one), passages the mutual check put into the other one, and names alike. Each pair offers
"Stellen ansehen", "Abgrenzung schreiben" and "Zusammenführen".

Merging takes effect at once (`merge`): the source category points to the target (merged_into), the target keeps the
source's name as a former name, and the codings keep the id they were made with, so that nothing in the interviews is
rewritten (transcript_coding.resolver). Everyone whose own passages are affected finds the change on top of the
category system ("Änderungen betreffen Ihre Stellen", no banner) and answers it: "Passt" (for themselves), or "Meine
Stellen herauslösen", which puts their own passages back into a category of their own with the old name and leaves the
others merged. The same two answers follow a rename ("Alten Namen behalten"). Whoever merged, or the team, takes a merge
back. Every change has a name, a time and a reason and can be commented.

The mutual check ("gegenseitige Probe"): a coder gets passages others coded, without their category, and chooses one;
per category the share of the same choice is shown ("Probe"). The checks belong to the interview
(Response.interview["checks"]), like the codings. Which passages: "zufällig" (the default) from all interviews the
actor sees, or "nach Zuteilung" (Ralf 2026-11-05): the team gives a person interviews and category blocks (a main
category with everything below it), CodingProject.settings["check_plan"]; then only who has a row checks, only there,
interview by interview from the top of the text down, still choosing from all categories.

The coding guide (Kodierleitfaden, Word): research question, per category definition, delimitation, anchor example,
former names and number of passages (Kuckartz & Rädiker, 2022; Mayring, 2022)."""
import csv
import difflib
import io
import math
import random
from xml.sax.saxutils import escape

from flask import Response as HttpResponse
from flask import abort, flash, redirect, render_template, request, send_file, url_for

from app import autosave, heavy
from app.extensions import db
from app.lang import de_en
from app.models import CodingCategory, CodingEvent, Response, utcnow
from app.transcript_coding import (_add, _event, _in, _key, bp, interviews, live, numbers, resolver, signed_in_or_coder,
                                   tree)
from app.transcript_coding_view import (anchor_spots, clean_quote, codings, example_spots, has_transcript, paragraphs,
                                        placed, spotter, transcript)

KUCKARTZ = ("Kuckartz, U., & Rädiker, S. (2022). Qualitative Inhaltsanalyse: Methoden, Praxis, Computerunterstützung "
            "(5. Aufl.). Beltz Juventa.")
MAYRING = "Mayring, P. (2022). Qualitative Inhaltsanalyse: Grundlagen und Techniken (13. Aufl.). Beltz."
OVERLAP = 0.5  # share of the shorter passage two codings have to share to count as the same passage
PROBE_LOW = 0.7  # below this share of agreement (with at least PROBE_MIN checks) a category counts as "unscharf"
PROBE_MIN = 3
CONTEXT = 220  # characters before and after a passage in the mutual check
ANSWERS = ("fits", "split_off", "comment", "undo")


# --- what was coded --------------------------------------------------------------------------------------------------


def all_codings(project, who):
    """[(interview, coding with its category now as "cat" and the one it was made with as "orig")] over the interviews
    the actor sees; passages no longer to be placed are left out."""
    out = []
    for row in interviews(project, who):
        if not has_transcript(row):
            continue
        ok, _lost = placed(row, project, who)
        out.extend((row, c) for c in ok)
    return out


def stats(project, who):
    """{category id: {"n": passages, "interviews": set of ids, "coders": set of names, "own": passages coded with it
    itself}}; every category counts what stands below it too, on every level."""
    from app.transcript_coding import chain

    cats = {c.id: c for c in live(project)}
    out = {cid: {"n": 0, "interviews": set(), "coders": set(), "own": 0} for cid in cats}
    for row, c in all_codings(project, who):
        cat = cats.get(c["cat"])
        if cat is not None:
            out[cat.id]["own"] += 1
        for cid in ({cat.id} | {a.id for a in chain(cat, cats)} if cat is not None else set()):
            if cid in out:
                out[cid]["n"] += 1
                out[cid]["interviews"].add(row.id)
                out[cid]["coders"].add(c.get("by_name") or "")
    return out


def checks(project, who):
    """[(coding, check)] of the mutual check over the interviews the actor sees: the category the coding counts for
    now and the one the check chose, both resolved."""
    resolve = resolver(project)
    out = []
    for row in interviews(project, who):
        by_id = {c.get("id"): c for c in codings(row, project.id)}
        for chk in (row.interview or {}).get("checks") or []:
            if not isinstance(chk, dict) or chk.get("project") != project.id or chk.get("coding") not in by_id:
                continue
            coding = by_id[chk["coding"]]
            out.append(({**coding, "cat": resolve(coding.get("cat"))}, {**chk, "cat": resolve(chk.get("cat"))}))
    return out


def probe(project, who):
    """{category id: (agreeing checks, all checks)} by the category of the checked passage."""
    out = {}
    for coding, chk in checks(project, who):
        cid = coding.get("cat")
        if cid is None:
            continue
        same, n = out.get(cid, (0, 0))
        out[cid] = (same + (chk.get("cat") == cid), n + 1)
    return out


def _overlapping(items):
    """The pairs (i, j), i < j, of passages in `items` that share at least one character, in the order the nested loop
    over all pairs would meet them (the order the pairs of fuzzy() are first counted in, which breaks ties of its
    sorting). Sorted by start and swept: only passages still open are compared, O(n log n + overlaps) instead of every
    pair (1 500 codings of an interview were 9 s per call; performance audit 2026-11-11)."""
    order = sorted(range(len(items)), key=lambda k: items[k]["start"])
    found, open_ = [], []
    for k in order:
        start = items[k]["start"]
        if items[k]["end"] <= start:
            continue  # an empty passage shares no character with any
        open_ = [o for o in open_ if items[o]["end"] > start]
        found.extend((min(o, k), max(o, k)) for o in open_)
        open_.append(k)
    found.sort()
    return found


def fuzzy(project, who):
    """The pairs of categories whose borders blur: [{"a", "b", "overlap", "checks", "names"}], strongest first."""
    cats = {c.id: c for c in live(project)}
    pairs = {}

    def pair(a, b):
        key = tuple(sorted((a, b)))
        return pairs.setdefault(key, {"a": cats[key[0]], "b": cats[key[1]], "overlap": 0, "checks": 0, "names": False})

    per_row = {}
    for row, c in all_codings(project, who):
        per_row.setdefault(row.id, []).append(c)
    for items in per_row.values():
        for i, j in _overlapping(items):
            x, y = items[i], items[j]
            if x["cat"] == y["cat"] or x["cat"] not in cats or y["cat"] not in cats:
                continue
            shared = min(x["end"], y["end"]) - max(x["start"], y["start"])
            shorter = min(x["end"] - x["start"], y["end"] - y["start"])
            if shorter > 0 and shared >= OVERLAP * shorter:
                pair(x["cat"], y["cat"])["overlap"] += 1
    for coding, chk in checks(project, who):
        if coding.get("cat") in cats and chk.get("cat") in cats and coding["cat"] != chk["cat"]:
            pair(coding["cat"], chk["cat"])["checks"] += 1
    subs = [c for c in cats.values() if c.kind == CodingCategory.SUB]
    for i, x in enumerate(subs):
        for y in subs[i + 1:]:
            if difflib.SequenceMatcher(None, _key(x.name), _key(y.name)).ratio() >= 0.75:
                pair(x.id, y.id)["names"] = True
    return sorted(pairs.values(), key=lambda p: -(p["overlap"] * 2 + p["checks"] * 2 + p["names"]))


def blurred(project, who, fuzzy_pairs=None, probes=None):
    """The categories marked "unscharf": in a pair with two shared passages or a disagreeing check, or a low check."""
    probes = probe(project, who) if probes is None else probes
    out = {cid for cid, (same, n) in probes.items() if n >= PROBE_MIN and same / n < PROBE_LOW}
    for p in fuzzy(project, who) if fuzzy_pairs is None else fuzzy_pairs:
        if p["overlap"] >= 2 or p["checks"]:
            out |= {p["a"].id, p["b"].id}
    return out


# --- merging and the answers to it -----------------------------------------------------------------------------------


def may_merge(project, source, target, who):
    """A subcategory into another one: every member (it takes effect at once; the others answer); a main category or a
    candidate: the team."""
    if source.id == target.id or source.project_id != project.id or target.project_id != project.id:
        return False
    from app.transcript_coding import subtree

    if target.id in subtree(project, source):  # not into something below itself
        return False
    if source.kind == CodingCategory.SUB and target.kind == CodingCategory.SUB:
        from app.transcript_coding import guarded, own_or_team

        # setting "only team and author": both categories have to be one's own (else it is proposed)
        return not guarded(project) or (own_or_team(source, who) and own_or_team(target, who))
    return who.team and target.kind == CodingCategory.MAIN and source.kind in (CodingCategory.MAIN, CodingCategory.CANDIDATE)


def may_propose_merge(project, source, target, who):
    """Setting `guarded`: two subcategories of which one is someone else's are merged by proposing it."""
    from app.transcript_coding import guarded, subtree

    return (guarded(project) and source.kind == CodingCategory.SUB and target.kind == CodingCategory.SUB
            and source.id != target.id and target.id not in subtree(project, source) and not may_merge(project, source, target, who))


def own_codings(project, key, cat_ids):
    """[(interview, coding)] of the actor `key` whose category as made is one of `cat_ids` (all interviews)."""
    from app import interview_data
    from app.models import Survey

    survey = db.session.get(Survey, project.survey_id)
    return [(r, c) for r in interview_data.interviews(survey) for c in codings(r, project.id)
            if c.get("by") == key and c.get("cat") in cat_ids]


def affected(project, cat_ids, but=None):
    """{actor key: [name, number of passages]} whose own passages carry one of `cat_ids` as made."""
    from app import interview_data
    from app.models import Survey

    survey = db.session.get(Survey, project.survey_id)
    out = {}
    for r in interview_data.interviews(survey):
        for c in codings(r, project.id):
            if c.get("cat") in cat_ids and c.get("by") and c.get("by") != but:
                out.setdefault(c["by"], [c.get("by_name") or "", 0])[1] += 1
    return out


def _merged_ids(project, cat_id):
    """The category and all merged into it, as made."""
    rows = list(db.session.scalars(db.select(CodingCategory).filter_by(project_id=project.id)))
    ids, grew = {cat_id}, True
    while grew:
        grew = False
        for c in rows:
            if c.merged_into in ids and c.id not in ids:
                ids.add(c.id)
                grew = True
    return ids


UNIFY = ("name", "definition", "delimitation", "anchor", "former_names")


def merge(project, source, target, who, reason, values=None):
    """Merge `source` into `target` at once; returns a problem or None. `values` (the dialog "Zusammenführen und
    vereinheitlichen", Ralf 2026-10-28): the name, definition and delimitation the merged category keeps, and which
    anchor example ("target" or "source"); old names go into "früher auch". Everything before is kept in the history,
    so that taking it back restores both exactly."""
    if not may_merge(project, source, target, who):
        return de_en("Diese beiden lassen sich so nicht zusammenführen.", "These two cannot be merged like this.")
    if target.removed_at is not None or target.merged_into is not None or source.merged_into is not None:
        abort(404)
    reason = " ".join(str(reason or "").split())[:1000]
    if not reason:
        return de_en("Bitte kurz begründen, warum die beiden zusammengehören.", "Please say briefly why the two belong together.")
    from app.transcript_coding import MAX_DEPTH, height, level_of

    kids = [c for c in live(project) if c.parent_id == source.id]
    if kids and level_of(target, {c.id: c for c in live(project)}) + height(project, source) - 1 > MAX_DEPTH:
        return de_en("Dann stünde etwas tiefer als {n} Ebenen.", "Something would then stand deeper than {n} levels.", n=MAX_DEPTH)
    moved = []
    for c in kids:  # what stood below it goes along under the target, on every level
        c.parent_id = target.id
        moved.append(c.id)
    from app.transcript_coding import NAME_MAX, _clean, _key, _text

    before = {k: (list(getattr(target, k)) if isinstance(getattr(target, k), list) else
                  dict(getattr(target, k)) if isinstance(getattr(target, k), dict) else getattr(target, k)) for k in UNIFY}
    values = values or {}
    name = _clean(values.get("name"), NAME_MAX) or target.name
    if _key(name) != _key(target.name):
        siblings = [c for c in live(project) if c.id not in (source.id, target.id) and c.parent_id == target.parent_id
                    and (c.kind == CodingCategory.SUB) == (target.kind == CodingCategory.SUB)]
        if any(_key(c.name) == _key(name) for c in siblings):
            return de_en("„{name}“ gibt es dort schon.", "“{name}” already exists there.", name=name)
    ids = _merged_ids(project, source.id)
    source.merged_into = target.id
    old_names = [target.name] if name != target.name else []
    target.former_names = [n for n in dict.fromkeys((target.former_names or []) + old_names + [source.name]
                                                     + list(source.former_names or [])) if n != name] or None
    target.name = name
    if "definition" in values:
        target.definition = _text(values.get("definition"))
    if "delimitation" in values:
        target.delimitation = _text(values.get("delimitation"))
    if values.get("anchor") == "source" and source.anchor:
        target.anchor = source.anchor
    elif not target.anchor and source.anchor:
        target.anchor = source.anchor
    target.updated_at = source.updated_at = utcnow()
    unified = any(before[k] != getattr(target, k) for k in ("name", "definition", "delimitation"))
    _event(project, who, "merged", target, {"source": source.id, "source_name": source.name, "target": target.id,
                                            "target_name": before["name"], "moved": moved, "before": before,
                                            "unified": unified, "after_name": target.name,
                                            "affected": affected(project, ids, but=who.key)}, reason=reason)
    return None


def _answered(project, event_id, key):
    return db.session.scalar(db.select(CodingEvent.id).filter_by(project_id=project.id, reply_to=event_id, actor_key=key)
                             .where(CodingEvent.kind.in_(("fits", "split_off"))).limit(1)) is not None


def notices(project, who):
    """The merges, renames and moves by others that carry own passages of the actor, and own passages others moved to
    another category, that the actor has not answered: [(event, own passages)]."""
    out = []
    for e in db.session.scalars(db.select(CodingEvent).filter_by(project_id=project.id)
                                .where(CodingEvent.kind.in_(("merged", "changed", "moved", "recoded"))).order_by(CodingEvent.id.desc()).limit(200)):
        data = e.data or {}
        if e.actor_key == who.key or data.get("undone") or _answered(project, e.id, who.key):
            continue
        if e.kind == "recoded":  # a passage of the actor moved to another category by someone of the team
            if data.get("author") == who.key:
                out.append((e, 1))
            continue
        if e.kind == "merged":
            ids = _merged_ids(project, data.get("source"))
            if db.session.get(CodingCategory, data.get("source")).merged_into != data.get("target"):
                continue  # taken back meanwhile
        elif e.kind == "moved":
            ids = set(data.get("ids") or [e.category_id])
        else:
            before, after = (data.get("before") or {}).get("name"), (data.get("after") or {}).get("name")
            if not before or before == after:
                continue
            ids = {e.category_id}
        n = len(own_codings(project, who.key, ids))
        if n:
            out.append((e, n))
    return out


def split_off(project, event, who):
    """"Meine Stellen herauslösen" / "Alten Namen behalten": the actor's own passages go into a category of their own
    with the old name; everyone else's stay. Returns the new category or a problem text."""
    data = event.data or {}
    if event.kind == "merged":
        old = db.session.get(CodingCategory, data.get("source"))
        ids = _merged_ids(project, old.id)
        name, definition, delimitation, parent = old.name, old.definition, old.delimitation, old.parent_id
        kind = old.kind if old.kind != CodingCategory.MAIN else CodingCategory.SUB
    elif event.kind == "moved":  # the own passages into a category of the old name at the old place
        cat = db.session.get(CodingCategory, event.category_id)
        ids = set(data.get("ids") or [cat.id])
        name, definition, delimitation = cat.name, cat.definition, cat.delimitation
        parent = (data.get("before") or {}).get("parent")
        kind = CodingCategory.SUB if parent else (CodingCategory.MAIN if who.team else CodingCategory.CANDIDATE)
    else:
        cat = db.session.get(CodingCategory, event.category_id)
        before = data.get("before") or {}
        ids = {cat.id}
        name, definition, delimitation = before.get("name") or cat.name, before.get("definition") or cat.definition, before.get("delimitation") or cat.delimitation
        parent, kind = cat.parent_id, cat.kind if cat.kind != CodingCategory.MAIN else CodingCategory.SUB
    mine = own_codings(project, who.key, ids)
    if not mine:
        return de_en("Keine eigenen Stellen darin.", "None of your passages are in it.")
    taken = {_key(c.name) for c in live(project) if c.parent_id == parent}
    base, n = name, 2
    while _key(name) in taken:
        name, n = f"{base} ({n})", n + 1
    parent_cat = db.session.get(CodingCategory, parent) if parent else None
    if kind == CodingCategory.SUB and (parent_cat is None or parent_cat.merged_into or parent_cat.removed_at):
        kind, parent_cat = CodingCategory.CANDIDATE, None
    new = _add(project, who, kind, name[:120], definition, delimitation, parent=parent_cat)
    rows = {}
    for row, c in mine:
        rows.setdefault(row.id, (row, set()))[1].add(c["id"])
    from app.transcript_coding_view import _store

    for row, coding_ids in rows.values():
        _store(row, lambda items, coding_ids=coding_ids: [{**c, "cat": new.id} if c.get("id") in coding_ids else c for c in items])
    _event(project, who, "split_off", new, {"moved": len(mine), "from": event.category_id}, reply_to=event.id)
    return new


def changed_since(project, event):
    """Whether the category of `event` was changed, moved, merged or removed after it (proposals and answers aside)."""
    if not event.category_id:
        return False
    later = db.session.scalar(db.select(CodingEvent.id).where(
        CodingEvent.project_id == project.id, CodingEvent.category_id == event.category_id, CodingEvent.id > event.id,
        CodingEvent.kind.in_(("changed", "moved", "merged", "removed", "ordered", "undone", "unmerged"))).limit(1))
    return later is not None


def may_take_back_move(project, event, who):
    """"Zurücknehmen" of a move in the history: the team, the category's author and whoever moved it (Ralf
    2026-10-29), while it is not taken back yet."""
    if event.kind != "moved" or (event.data or {}).get("undone"):
        return False
    cat = db.session.get(CodingCategory, event.category_id) if event.category_id else None
    if cat is None or cat.removed_at is not None or cat.merged_into is not None:
        return False
    from app.transcript_coding import own_or_team

    return own_or_team(cat, who) or event.actor_key == who.key


def undo_move(project, event, who, force=False):
    """Put a moved category back where it stood; with `force` also when it was changed since (the page asks first)."""
    if not may_take_back_move(project, event, who):
        abort(403)
    if changed_since(project, event) and not force:
        return de_en("Die Kategorie wurde seither weiter geändert; bitte bestätigen.", "The category was changed since; please confirm.")
    from app.transcript_coding import MAX_DEPTH, _event, height, level_of, live, subtree, utcnow

    cat = db.session.get(CodingCategory, event.category_id)
    before = (event.data or {}).get("before") or {}
    back = before.get("parent")
    cats = {c.id: c for c in live(project)}
    if back is not None and (back not in cats or back in subtree(project, cat)
                             or level_of(cats[back], cats) + height(project, cat) > MAX_DEPTH):
        return de_en("Der alte Platz ist nicht mehr frei.", "The old place is no longer free.")
    cat.parent_id, cat.kind = back, before.get("kind") or cat.kind
    cat.updated_at = utcnow()
    event.data = {**(event.data or {}), "undone": True}
    _event(project, who, "undone", cat, {"after": {"parent": cat.parent_id, "kind": cat.kind, "name": cat.name}}, reply_to=event.id)
    return None


def undo_merge(project, event, who):
    data = event.data or {}
    if event.kind != "merged" or data.get("undone"):
        abort(404)
    if not (who.team or event.actor_key == who.key):
        abort(403)
    source = db.session.get(CodingCategory, data.get("source"))
    target = db.session.get(CodingCategory, data.get("target"))
    if source is None or target is None or source.merged_into != target.id:
        return de_en("Das lässt sich nicht mehr zurücknehmen.", "This can no longer be taken back.")
    source.merged_into = None
    if data.get("before"):  # merged and harmonised: the target exactly as it was
        for k, v in data["before"].items():
            setattr(target, k, v)
    else:
        target.former_names = [n for n in (target.former_names or []) if n != source.name] or None
    for cid in data.get("moved") or []:
        c = db.session.get(CodingCategory, cid)
        if c is not None and c.parent_id == target.id:
            c.parent_id = source.id
    event.data = {**data, "undone": True}
    _event(project, who, "unmerged", target, {"source": source.id, "source_name": source.name}, reply_to=event.id)
    return None


# --- the mutual check --------------------------------------------------------------------------------------------------


CHECK_RANDOM, CHECK_PLAN = "random", "plan"
CANDIDATES = "cand"  # the block of the candidates for new main categories


def check_mode(project):
    """"random" (the default, as before 2026-11-05) or "plan": only who has a row of the plan checks, and only there."""
    return CHECK_PLAN if (project.settings or {}).get("check_mode") == CHECK_PLAN else CHECK_RANDOM


def check_plan(project):
    """{actor key: {"name", "interviews": [interview ids] or None (all), "blocks": [main category ids, "cand"] or
    None (all)}}: who checks which interviews and which category blocks (the team, Ralf 2026-11-05)."""
    plan = (project.settings or {}).get("check_plan") or {}
    return {k: v for k, v in plan.items() if isinstance(v, dict)}


def blocks(project):
    """[(block id, name)] in the order of the category system: the main categories, then the candidates."""
    mains, candidates = tree(project)
    out = [(m.id, m.name) for m, _subs in mains]
    if candidates:
        out.append((CANDIDATES, de_en("Kandidaten", "Candidates")))
    return out


def block_of(project):
    """category id -> its block: the main category it stands under (or itself), CANDIDATES for a candidate."""
    mains, candidates = tree(project)
    out = {}
    for main, subs in mains:
        out[main.id] = main.id
        out.update({s.id: main.id for s in subs})
    out.update({c.id: CANDIDATES for c in candidates})
    return out


def _scope(project, entry):
    """(interview ids or None, a test of a coding for the blocks or None) of a row of the plan."""
    if entry is None:
        return None, None
    wanted = None if entry.get("interviews") is None else set(entry["interviews"])
    if entry.get("blocks") is None:
        return wanted, None
    kinds, resolve, block = set(entry["blocks"]), resolver(project), block_of(project)
    return wanted, lambda c: block.get(resolve(c.get("cat"))) in kinds


def _check_pool(project, key, rows, entry=None):
    """[(interview, coding)] of passages others coded that `key` has not checked yet, in the interviews `rows` (and,
    with a row of the plan, only its interviews and blocks), in the order of the interviews and the text."""
    from app.transcript_coding_double import assignment, finished

    wanted, in_blocks = _scope(project, entry)
    pool = []
    for row in rows:
        if wanted is not None and row.id not in wanted:
            continue
        dbl = assignment(project, row.id)
        if not has_transcript(row) or (dbl is not None and not finished(dbl)):
            continue  # nobody checks passages of a double coding before it is finished
        done = {c.get("coding") for c in (row.interview or {}).get("checks") or []
                if isinstance(c, dict) and c.get("by") == key and c.get("project") == project.id}
        ok, _lost = placed(row, project)
        for c in sorted(ok, key=lambda c: (c.get("start") or 0, c.get("end") or 0)):
            if c.get("by") == key or c.get("id") in done or (in_blocks is not None and not in_blocks(c)):
                continue
            pool.append((row, c))
    return pool


def _done_in(project, key, rows, entry):
    """How many checks `key` made in the interviews and blocks of its row of the plan."""
    wanted, in_blocks = _scope(project, entry)
    n = 0
    for row in rows:
        if wanted is not None and row.id not in wanted:
            continue
        by_id = {c.get("id"): c for c in codings(row, project.id)}
        for chk in (row.interview or {}).get("checks") or []:
            if not isinstance(chk, dict) or chk.get("by") != key or chk.get("project") != project.id:
                continue
            coding = by_id.get(chk.get("coding"))
            if coding is not None and (in_blocks is None or in_blocks(coding)):
                n += 1
    return n


def my_plan(project, who):
    """The row of the plan of `who` while the check goes by the plan, else None."""
    return check_plan(project).get(who.key) if check_mode(project) == CHECK_PLAN else None


def check_progress(project, who):
    """(checked, all) of the actor's row of the plan, or None."""
    entry = my_plan(project, who)
    if entry is None:
        return None
    rows = interviews(project, who)
    done = _done_in(project, who.key, rows, entry)
    return done, done + len(_check_pool(project, who.key, rows, entry))


def plan_labels(project, survey, entry):
    """(interview labels or None for all, block names or None for all) of a row of the plan."""
    from app import interview_data

    labels = None
    if entry.get("interviews") is not None:
        names = {r.id: (r.interview or {}).get("label") or f"#{r.id}" for r in interview_data.interviews(survey, source="real")}
        labels = [names[i] for i in entry["interviews"] if i in names]
    found = None
    if entry.get("blocks") is not None:
        names = dict(blocks(project))
        found = [names[b] for b in entry["blocks"] if b in names]
    return labels, found


def plan_rows(project, survey):
    """The plan as the team sees it: [{"key", "name", "interviews", "blocks", "labels", "block_names", "done",
    "total"}], by name. The state counts over all interviews of the row (the team sees all)."""
    from app import interview_data

    rows = list(interview_data.interviews(survey, source="real"))
    out = []
    for key, entry in check_plan(project).items():
        labels, block_names = plan_labels(project, survey, entry)
        done = _done_in(project, key, rows, entry)
        out.append({"key": key, "name": entry.get("name") or key, "interviews": entry.get("interviews"),
                    "blocks": entry.get("blocks"), "labels": labels, "block_names": block_names,
                    "done": done, "total": done + len(_check_pool(project, key, rows, entry))})
    return sorted(out, key=lambda r: r["name"].casefold())


def set_check_mode(project, mode):
    project.settings = {**(project.settings or {}), "check_mode": CHECK_PLAN if mode == CHECK_PLAN else CHECK_RANDOM}
    project.updated_at = utcnow()


def set_plan_row(project, survey, key, rids, block_ids):
    """Put the row of `key` into the plan: `rids` and `block_ids` the ticked ones, None for all (also those added
    later). Unknown interviews and blocks are left out. Returns False if nothing is left to check."""
    from app import interview_data
    from app.transcript_coding_double import assignable

    db.session.refresh(project, with_for_update=True)  # one writer of the plan at a time
    known = dict(assignable(project, survey))
    if key not in known:
        abort(400)
    if rids is not None:
        real = {r.id for r in interview_data.interviews(survey, source="real")}
        rids = [r for r in dict.fromkeys(rids) if r in real]
    if block_ids is not None:
        ids = {b for b, _n in blocks(project)}
        block_ids = [b for b in dict.fromkeys(block_ids) if b in ids]
    if rids == [] or block_ids == []:
        return False
    plan = check_plan(project)
    plan[key] = {"name": known[key][:200], "interviews": rids, "blocks": block_ids}
    project.settings = {**(project.settings or {}), "check_plan": plan}
    project.updated_at = utcnow()
    return True


def remove_plan_row(project, key):
    db.session.refresh(project, with_for_update=True)
    plan = check_plan(project)
    plan.pop(key, None)
    project.settings = {**(project.settings or {}), "check_plan": plan}
    project.updated_at = utcnow()


def next_check(project, who):
    """A passage someone else coded and the actor has not checked yet: (interview, coding) or None. By the plan only
    from the actor's interviews and blocks, in the order of the interviews and the text, and nothing without a row;
    else at random from all interviews the actor sees."""
    if check_mode(project) == CHECK_PLAN:
        entry = check_plan(project).get(who.key)
        if entry is None:
            return None
        pool = _check_pool(project, who.key, interviews(project, who), entry)
        return pool[0] if pool else None
    pool = _check_pool(project, who.key, interviews(project, who))
    if not pool:
        return None
    return random.Random(f"{who.key}:{len(pool)}").choice(pool)


def record_check(project, who, row, coding_id, cat_id):
    if coding_id not in {c.get("id") for c in codings(row, project.id)}:
        abort(404)
    cat = db.session.get(CodingCategory, cat_id) if isinstance(cat_id, int) else None
    if cat is None or cat.project_id != project.id or cat.removed_at is not None or cat.merged_into is not None:
        abort(400)
    entry = {"coding": coding_id, "project": project.id, "cat": cat.id, "by": who.key, "by_name": who.name[:200],
             "at": utcnow().isoformat(timespec="seconds")}

    from app import interview_audio

    def work():
        db.session.refresh(row)
        meta = dict(row.interview or {})
        meta["checks"] = [c for c in (meta.get("checks") or []) if not (isinstance(c, dict) and c.get("coding") == coding_id
                                                                         and c.get("by") == who.key)] + [entry]
        row.interview = meta

    interview_audio.retried(work)
    return entry


# --- the map --------------------------------------------------------------------------------------------------------


def _wrap(text, room, size=13, bold=False):
    """The lines of a name in the diagram, at most `room` pixels each (Ralf 2026-11-20: names were cut off after a
    fixed number of characters). Measured with Noto Sans, which runs a little wider than the Arial of the drawing, so
    a line never runs out of its room; a word longer than a line is split with a hyphen."""
    from reportlab.pdfbase import pdfmetrics

    from app import pdf_fonts

    face = pdf_fonts.font(bold=bold)

    def fits(s):
        return pdfmetrics.stringWidth(s, face, size) <= room

    lines, line = [], ""
    for word in " ".join(str(text or "").split()).split(" "):
        while word and not fits(word):  # wider than a line: as much as fits, the rest on the next line
            if line:
                lines.append(line)
                line = ""
            cut = max([i for i in range(1, len(word)) if fits(word[:i] + "-")] or [1])
            lines.append(word[:cut] + "-")
            word = word[cut:]
        if not word:
            continue
        if fits(f"{line} {word}" if line else word):
            line = f"{line} {word}" if line else word
        else:
            lines.append(line)
            line = word
    if line or not lines:
        lines.append(line)
    return lines


def _lines(x, y, lines, step, attrs):
    """One <text> per line, the first on y (app/svg_png.py and the Word guide draw a <text> as one line)."""
    return "".join(f'<text x="{x}" y="{y + i * step:g}"{attrs}>{escape(t)}</text>' for i, t in enumerate(lines))


MAP_SWITCHES = ("counts", "pairs")  # what the diagram may leave out, each on unless switched off (Ralf 2026-11-20)


def map_options(args):
    """{"counts": bool, "pairs": bool, "by": key} from the address of the page or of a download. The page's form sends
    "o=1" and a ticked box as "1"; without "o" (an old link, the Word guide) everything is shown. "by": the
    characteristic of the interviews the diagram is coloured by (map_groupers), "" for the colours of the categories."""
    by = str(args.get("by") or "")[:20]
    if not args.get("o"):
        return {**{k: True for k in MAP_SWITCHES}, "by": by}
    return {**{k: args.get(k) == "1" for k in MAP_SWITCHES}, "by": by}


def map_query(opts):
    """The address part of map_options for the downloads: nothing while all is shown in the colours of the categories."""
    out = {} if all(opts[k] for k in MAP_SWITCHES) else {"o": "1", **{k: "1" for k in MAP_SWITCHES if opts[k]}}
    return {**out, "by": opts["by"]} if opts.get("by") else out


# the colours of the groups when the diagram is coloured by a characteristic (Ralf 2026-11-20, E8): Okabe & Ito (2008),
# told apart also with red-green colour blindness, in the order of the answers; "keine Angabe" grey
GROUP_COLOURS = ("#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#8A4B14", "#1F6B2A")
NO_ANSWER_COLOUR = "#9A978F"


def _tint(colour, share):
    """`colour` mixed with white, as a solid colour (app/svg_png.py draws no transparency)."""
    c = (colour or "").lstrip("#")
    if len(c) != 6:
        return "#ffffff"
    try:
        rgb = [int(c[i:i + 2], 16) for i in (0, 2, 4)]
    except ValueError:
        return "#ffffff"
    return "#" + "".join(f"{round(255 - (255 - v) * share):02x}" for v in rgb)


def map_groupers(survey):
    """What the diagram can be coloured by (Ralf 2026-11-20, E2): the single choice questions and the federal state of
    the Kurzfragebogen and the Postskript, the interview form and who interviewed. [{"key", "label", "part",
    "order": answers in their order, "of": function interview -> answer text}]."""
    from app import interview
    from app.interview_results import answer_text
    from app.items.base import plain
    from app.lang import english

    lang = survey.default_language
    out = []
    for part, part_name in (("short", de_en("Kurzfragebogen", "Short questionnaire")), ("post", de_en("Postskript", "Postscript"))):
        form = interview.shown_form(survey, part)
        for item in (form.items if form is not None else []):
            if item.type not in ("single_choice", "federal_state"):
                continue
            text = " ".join(plain(item.text("question", lang)).split())
            label = (text[:60].rstrip() + "…" if len(text) > 61 else text) or item.code
            try:
                order = [o["label"] for o in item.kind.options(item, lang)] if item.type == "single_choice" else []
            except Exception:  # an option list it cannot read: the answers then stand in the order of the alphabet
                order = []
            out.append({"key": f"i{item.id}", "label": label, "part": part_name, "order": order,
                        "of": lambda r, item=item: answer_text(item, (r.answers or {}).get(str(item.id)), lang)})
    modes = interview.MODES_EN if english() else interview.MODES
    meta = de_en("Interview", "Interview")
    out.append({"key": "form", "label": de_en("Interviewform", "Interview mode"), "part": meta, "order": list(modes.values()),
                "of": lambda r: modes.get((r.interview or {}).get("mode"), "")})
    out.append({"key": "von", "label": de_en("interviewt von", "interviewed by"), "part": meta, "order": [],
                "of": lambda r: (r.interview or {}).get("by_name", "")})
    return out


def map_grouping(project, who, survey, key):
    """The groups of the interviews by the characteristic `key` and, per category, the passages from each group (with
    what stands below it, as stats() counts): {"label", "part", "groups": [(answer, colour)], "of": {category id:
    {answer: passages}}}, or None for an unknown key. Only the interviews the actor sees count (Ralf 2026-11-20)."""
    from app.transcript_coding import chain

    grouper = next((g for g in map_groupers(survey) if g["key"] == key), None)
    if grouper is None:
        return None
    missing = de_en("keine Angabe", "no answer")
    label = {r.id: (grouper["of"](r) or missing) for r in interviews(project, who) if has_transcript(r)}
    order = grouper["order"]
    names = sorted(set(label.values()), key=lambda n: (n == missing, order.index(n) if n in order else len(order), n.lower()))
    colours, i = {}, 0
    for n in names:
        if n == missing:
            colours[n] = NO_ANSWER_COLOUR
        else:
            colours[n] = GROUP_COLOURS[i % len(GROUP_COLOURS)]
            i += 1
    cats = {c.id: c for c in live(project)}
    of = {}
    for row, c in all_codings(project, who):
        cat = cats.get(c["cat"])
        if cat is None:
            continue
        g = label.get(row.id, missing)
        for cid in {cat.id} | {a.id for a in chain(cat, cats)}:
            here = of.setdefault(cid, {})
            here[g] = here.get(g, 0) + 1
    return {"label": grouper["label"], "part": grouper["part"], "groups": [(n, colours[n]) for n in names], "of": of}


def _groups_text(groups, n_groups):
    """"nur Klinik", "Klinik und Altenheim", "keine Stelle": what a row's colour says, for the tooltip and screen readers."""
    if not groups:
        return de_en("keine Stelle", "no passage")
    if len(groups) == 1:
        return de_en("nur {g}", "only {g}", g=groups[0]) if n_groups > 1 else groups[0]
    return de_en("{a} und {b}", "{a} and {b}", a=", ".join(groups[:-1]), b=groups[-1])


def _dot(cx, cy, colours, r=6):
    """The dot of a row: one colour, or split into equal wedges, one per group (closed paths of straight lines, which
    app/svg_png.py fills too)."""
    if len(colours) == 1:
        return f'<circle cx="{cx}" cy="{cy:g}" r="{r}" fill="{escape(colours[0])}"/>'
    out, n, steps = [], len(colours), 24
    for i, colour in enumerate(colours):
        a0, a1 = -math.pi / 2 + 2 * math.pi * i / n, -math.pi / 2 + 2 * math.pi * (i + 1) / n
        k = max(2, steps // n)
        pts = " ".join(f"L{cx + r * math.cos(a0 + (a1 - a0) * j / k):.2f} {cy + r * math.sin(a0 + (a1 - a0) * j / k):.2f}"
                       for j in range(k + 1))
        out.append(f'<path d="M{cx} {cy:g} {pts} Z" fill="{escape(colour)}"/>')
    return "".join(out)


def map_svg(project, who, survey, interactive=False, counts=True, pairs=True, by=""):
    """The category system as a tree: the research question, then one column per level; every subcategory with a
    bar of its passages (with what stands below it); blurring pairs joined by a dashed line. Plain SVG with its own
    colours, to download as it is. The drawing grows to the right with the levels and scrolls in its own box.
    All lines go under the rows, every row on a white ground of its own, and they leave a row to the right of its
    number, so no line covers a name, a bar or a number (Ralf 2026-11-10). Long names wrap within their row instead
    of being cut off; only such a row grows, by one text line per further line (Ralf 2026-11-20). `counts` False leaves
    out bars and numbers (the names then take their room), `pairs` False the dashed lines (Ralf 2026-11-20).
    Colours (Ralf 2026-11-20): each subcategory's dot and a tint of its row in its colour (its own or that of the
    nearest category above with one), a main category's frame in its colour. `by` colours by a characteristic of the
    interviews instead (map_groupers): a row only one group has passages in is tinted in that group's colour, a row
    of several groups stays white with its dot split into their colours, the bar is split by the passages of each
    group, a main category whose passages all come from one group is framed in its colour; a legend names the groups."""
    from app.transcript_coding import colour_of

    mains, candidates = tree(project)
    grouping = map_grouping(project, who, survey, by) if by else None
    every = {c.id: c for m, ss in mains for c in [m] + ss}  # for the colour of what stands above
    st = stats(project, who)
    pairs = [p for p in fuzzy(project, who) if p["overlap"] or p["checks"]] if pairs else []
    subs = [s for _m, ss in mains for s in ss]
    biggest = max([st[s.id]["n"] for s in subs] + [1])
    depth = max([s.level for s in subs] + [1])
    row_h, x0, col, box, step = 34, 20, 310, 250, 15
    end = 268  # a subcategory's row: dot, name, bar and number end here; its lines start right of it
    main_ids = {m.id for m, _ss in mains}
    # a main category's name runs from its left edge to its number, a subcategory's from its dot to its bar
    names = {c.id: _wrap(c.name, (196 if c.id in main_ids else 146) + (0 if counts else 30 if c.id in main_ids else 96),
                         bold=c.id in main_ids)
             for c in [m for m, _ss in mains] + subs}
    below = {}
    for s in subs:
        below.setdefault(s.parent_id, []).append(s)
    pos, y = {}, 20

    def tall(cat):
        return row_h + (len(names[cat.id]) - 1) * step

    def shift(cat, by):
        pos[cat.id] += by
        for k in below.get(cat.id, []):
            shift(k, by)

    def place(cat):  # leaves one row each, a category in the middle of what stands below it
        nonlocal y
        kids = below.get(cat.id, [])
        if not kids:
            pos[cat.id] = y + tall(cat) / 2 - 3
            y += tall(cat)
            return pos[cat.id]
        start = y
        ys = [place(k) for k in kids]
        pos[cat.id] = (ys[0] + ys[-1]) / 2
        gap = tall(cat) - (y - start)
        if gap > 0:  # a long name over few rows: they move down, so that it does not reach into its neighbours
            for k in kids:
                shift(k, gap / 2)
            pos[cat.id] += gap / 2
            y = start + tall(cat)
        return pos[cat.id]

    for main, _ss in mains:
        place(main)
        y += 14
    head = _wrap(project.name, 226, bold=True)
    question = _wrap(project.question or project.name, 226, size=11)
    root_h = 24 + len(head) * step + len(question) * 13
    width = x0 + 250 + 50 + depth * col + 140
    rest = (_wrap(de_en("Kandidaten: ", "Candidates: ") + ", ".join(c.name for c in candidates), width - 2 * x0, size=11)
            if candidates else [])
    levels = {s.id: s.level for s in subs}
    pairs = [p for p in pairs if p["a"].id in levels and p["b"].id in levels]  # only these get a line
    # a legend under the drawing for what is shown of bars and lines, so a download explains itself (Ralf 2026-11-20)
    room = width - 2 * x0 - 34
    bar_text = de_en("Balken und Zahl: Stellen der Kategorie mit allem darunter; der längste Balken hat die meisten "
                     "Stellen.", "Bar and number: passages of the category with everything below it; the longest bar has "
                     "the most passages.")
    if grouping is not None:
        bar_text += de_en(" Die Farben teilen den Balken nach den Gruppen.", " The colours split the bar by the groups.")
    legend = ([("bar", _wrap(bar_text, room, size=11), None)] if counts and subs else []) + (
        [("pair", _wrap(de_en("Gestrichelt: unscharfes Paar, also zwei Unterkategorien, mit denen dieselben Stellen kodiert "
                              "sind, oder mit einer Probe, die die eine statt der anderen wählt.", "Dashed: blurring pair, two "
                              "subcategories that code the same passages, or with a check that chose one instead of the "
                              "other."), room, size=11), None)] if pairs else [])
    n_groups = len(grouping["groups"]) if grouping else 0
    if grouping is not None:  # the groups first, so that the colours are explained at once (E9)
        groups = grouping["groups"]
        entries = [("head", _wrap(de_en("Farben nach „{label}“ ({part}); getönte Zeile: Stellen nur aus dieser Gruppe.",
                                        "Colours by “{label}” ({part}); tinted row: passages only from this group.",
                                        label=grouping["label"], part=grouping["part"]), room + 34, size=11, bold=True), None)]
        entries += [("group", _wrap(_groups_text([g], n_groups), room, size=11), [colour]) for g, colour in groups]
        if n_groups > 1:
            several = (de_en("Stellen aus beiden Gruppen: Zeile weiß, Punkt geteilt", "passages from both groups: row white, "
                             "dot split") if n_groups == 2 else
                       de_en("Stellen aus mehreren Gruppen: Zeile weiß, Punkt geteilt in deren Farben", "passages from "
                             "several groups: row white, dot split into their colours"))
            entries.append(("group", _wrap(several, room, size=11), [c for _g, c in groups[:3]]))
        if any(s.id not in grouping["of"] for s in subs):
            entries.append(("group", _wrap(de_en("keine Stelle", "no passage"), room, size=11), []))
        if not groups:
            entries.append(("note", _wrap(de_en("Noch keine Interviews mit Transkript.", "No interviews with a transcript yet."),
                                          room + 34, size=11), None))
        legend = entries + legend
    legend_h = sum(6 + 13 * len(t) + (2 if k == "group" else 0) for k, t, _x in legend) + (10 if legend else 0)
    # the box of the research question in the middle of the tree, not of tree and legend, so that a long legend never
    # runs into it (Ralf 2026-11-20)
    tree_h = max(y, root_h + 40)
    height = max(tree_h + 30 + max(len(rest) - 1, 0) * 13 + legend_h, 120)
    mid = (tree_h + 6) / 2 if legend else height / 2
    top = mid - root_h / 2
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{int(height)}" viewBox="0 0 {width} {int(height)}" '
             f'font-family="Arial, sans-serif" font-size="13" role="img" aria-label="{escape(de_en("Kategoriensystem als Diagramm", "Category system as a diagram"))}">',
             '<rect width="100%" height="100%" fill="#ffffff"/>',
             ('<g class="tc-map-root" data-tc-drop-main>' if interactive else "<g>"),
             f'<rect x="{x0}" y="{top:g}" width="250" height="{root_h}" rx="10" fill="#fbf3ef" stroke="#a8401c" stroke-width="2"/>',
             _lines(x0 + 12, top + 22, head, step, ' font-weight="bold" fill="#1c1c1a"'),
             _lines(x0 + 12, top + 22 + len(head) * step + 2, question, 13, ' fill="#5f5e59" font-size="11"'), "</g>"]

    def x_of(level):
        return x0 + 250 + 50 + (level - 1) * col

    from flask import url_for

    from app.transcript_coding import may_move, may_propose_move

    def node(cat, note=""):  # on the page: a node to drag where the person may move it (app/static/transcript_coding.js)
        if not interactive:  # `note`: what its colour says by groups, also in a download
            return f"<g><title>{escape(cat.path + ': ' + note)}</title>" if note else "<g>"
        grab = (f' data-tc-grab="{cat.id}" data-tc-move-url="{escape(url_for("transcript_coding.move_category", code=survey.code, pid=project.id, cid=cat.id))}"'
                if may_move(project, cat, who) or may_propose_move(project, cat, who) else "")
        # a click (without dragging) opens the dialog with its passages (Ralf 2026-11-18), also with Enter
        show = escape(url_for("transcript_coding.category_passages_json", code=survey.code, pid=project.id, cid=cat.id))
        return (f'<g class="tc-map-node" data-tc-cat="{cat.id}" data-tc-anc-of="{" ".join(str(a) for a in cat.anc)}"{grab} '
                f'data-tc-passages="{show}" tabindex="0" role="button">'
                f'<title>{escape(cat.path + (": " + note if note else ""))}</title>')

    lines, nodes = [], []  # lines first, the rows on top of them

    def link(xa, ya, xb, yb):
        lines.append(f'<path d="M{xa} {ya} C {xb - 25} {ya}, {xb - 25} {yb}, {xb} {yb}" fill="none" stroke="#cfcdc5" stroke-width="1.5"/>')

    group_colour = dict(grouping["groups"]) if grouping else {}

    def groups_of(cat):  # the groups with passages in it, in the order of the legend
        here = grouping["of"].get(cat.id, {})
        return [g for g, _c in grouping["groups"] if here.get(g)]

    for main, ss in mains:
        my, x1 = pos[main.id], x_of(1)
        extra = (len(names[main.id]) - 1) * step
        link(x0 + 250, mid, x1, my)
        if grouping is not None:
            gs = groups_of(main)
            frame = (group_colour[gs[0]], 2) if len(gs) == 1 and n_groups > 1 else ("#706e68", 1)
            nodes.append(node(main, _groups_text(gs, n_groups)))
        else:
            frame = (main.colour, 2) if main.colour else ("#706e68", 1)
            nodes.append(node(main))
        nodes.append(f'<rect x="{x1}" y="{my - 15 - extra / 2:g}" width="{box}" height="{30 + extra}" rx="8" fill="#f8f8f5" '
                     f'stroke="{escape(frame[0])}" stroke-width="{frame[1]}"/>')
        nodes.append(_lines(x1 + 10, my + 5 - extra / 2, names[main.id], step, ' font-weight="bold" fill="#1c1c1a"'))
        if counts:
            nodes.append(f'<text x="{x1 + 240}" y="{my + 5}" text-anchor="end" fill="#5f5e59">{st[main.id]["n"]}</text>')
        nodes.append("</g>")
        for s in ss:
            sy, xs = pos[s.id], x_of(s.level)
            extra = (len(names[s.id]) - 1) * step
            above = pos.get(s.parent_id, my)
            link(x_of(s.level - 1) + (box if s.parent_id == main.id else end), above, xs - 4, sy)
            gs = []
            if grouping is not None:
                gs = groups_of(s)
                ground = _tint(group_colour[gs[0]], 0.18) if len(gs) == 1 and n_groups > 1 else "#ffffff"
                dot = (_dot(xs + 6, sy, [group_colour[g] for g in gs]) if gs else
                       f'<circle cx="{xs + 6}" cy="{sy:g}" r="5" fill="#ffffff" stroke="{NO_ANSWER_COLOUR}"/>')
                nodes.append(node(s, _groups_text(gs, n_groups)))
            else:
                colour = colour_of(project, s, every) or "#2957A4"
                ground, dot = _tint(colour, 0.12), f'<circle cx="{xs + 6}" cy="{sy}" r="5" fill="{escape(colour)}"/>'
                nodes.append(node(s))
            # the row's ground, so that lines passing behind it never cover it; on the page also the whole row to grab
            # and to drop onto, not only the small dot
            hit = ' class="tc-hit"' if interactive else ""
            nodes.append(f'<rect{hit} x="{xs - 4}" y="{sy - 14 - extra / 2:g}" width="{end + 2}" height="{28 + extra}" rx="6" fill="{ground}" stroke="none"/>')
            nodes.append(dot)
            nodes.append(_lines(xs + 18, sy + 4 - extra / 2, names[s.id], step, ' fill="#1c1c1a"'))
            bar = int(60 * st[s.id]["n"] / biggest)
            if counts:
                nodes.append(f'<rect x="{xs + 170}" y="{sy - 5}" width="60" height="10" rx="2" fill="#efeee9"/>')
                if bar and grouping is not None:  # split by the passages of each group, the widths summed up
                    here, done, left = grouping["of"].get(s.id, {}), 0, 0
                    total = sum(here.get(g, 0) for g in gs) or 1
                    for g in gs:
                        done += here[g]
                        right = round(bar * done / total)
                        if right > left:
                            nodes.append(f'<rect x="{xs + 170 + left}" y="{sy - 5}" width="{right - left}" height="10" fill="{escape(group_colour[g])}"/>')
                        left = right
                elif bar:
                    nodes.append(f'<rect x="{xs + 170}" y="{sy - 5}" width="{bar}" height="10" rx="2" fill="#378ccf"/>')
                nodes.append(f'<text x="{xs + 262}" y="{sy + 4}" text-anchor="end" fill="#5f5e59">{st[s.id]["n"]}</text>')
            nodes.append("</g>")
    for p in pairs:
        a, b = pos.get(p["a"].id), pos.get(p["b"].id)
        if a is None or b is None or p["a"].id not in levels or p["b"].id not in levels:
            continue
        xa, xb = x_of(levels[p["a"].id]) + end + 4, x_of(levels[p["b"].id]) + end + 4
        bend = max(xa, xb) + min(col - end - 10, 8 + abs(a - b) / 4)
        lines.append(f'<path d="M{xa} {a} C {bend} {a}, {bend} {b}, {xb} {b}" fill="none" stroke="#b35c00" '
                     f'stroke-width="1.5" stroke-dasharray="6 5"/>')
    parts.extend(lines + nodes)
    if rest:
        parts.append(_lines(x0, height - 12 - legend_h - (len(rest) - 1) * 13, rest, 13, ' fill="#5f5e59" font-size="11"'))
    ly = height - legend_h + 10
    for kind, text, colours in legend:
        if kind in ("head", "note"):
            parts.append(_lines(x0, ly, text, 13, ' fill="#1c1c1a" font-size="11"' + (' font-weight="bold"' if kind == "head" else "")))
            ly += 6 + 13 * len(text)
            continue
        if kind == "bar" and grouping is not None and grouping["groups"]:
            first, second = (grouping["groups"] * 2)[:2]
            parts.append(f'<rect x="{x0}" y="{ly - 9:g}" width="24" height="10" rx="2" fill="#efeee9"/>'
                         f'<rect x="{x0}" y="{ly - 9:g}" width="10" height="10" fill="{escape(first[1])}"/>'
                         f'<rect x="{x0 + 10}" y="{ly - 9:g}" width="7" height="10" fill="{escape(second[1])}"/>')
        elif kind == "bar":
            parts.append(f'<rect x="{x0}" y="{ly - 9:g}" width="24" height="10" rx="2" fill="#efeee9"/>'
                         f'<rect x="{x0}" y="{ly - 9:g}" width="16" height="10" rx="2" fill="#378ccf"/>')
        elif kind == "group":
            ground = _tint(colours[0], 0.18) if len(colours) == 1 and n_groups > 1 else "#ffffff"
            parts.append(f'<rect x="{x0}" y="{ly - 12:g}" width="24" height="16" rx="4" fill="{ground}" stroke="#cfcdc5"/>')
            parts.append(_dot(x0 + 12, ly - 4, colours) if colours else
                         f'<circle cx="{x0 + 12}" cy="{ly - 4:g}" r="5" fill="#ffffff" stroke="{NO_ANSWER_COLOUR}"/>')
        else:
            parts.append(f'<line x1="{x0}" y1="{ly - 4:g}" x2="{x0 + 24}" y2="{ly - 4:g}" stroke="#b35c00" stroke-width="1.5" stroke-dasharray="6 5"/>')
        parts.append(_lines(x0 + 34, ly, text, 13, ' fill="#5f5e59" font-size="11"'))
        ly += 6 + 13 * len(text) + (2 if kind == "group" else 0)
    parts.append("</svg>")
    return "".join(parts)


# --- the coding guide ------------------------------------------------------------------------------------------------


def _guide_diagram(doc, project, who, survey):
    """The diagram of the category system in the coding guide (Ralf 2026-10-29): as wide as the text, never taller
    than the page; one more than 2.2 times as wide as high on a page of its own across, then back upright."""
    from PIL import Image
    from docx.enum.section import WD_ORIENT, WD_SECTION
    from docx.shared import Cm

    mains, candidates = tree(project)
    if not mains and not candidates:
        return
    png = map_png(project, who, survey, scale=2.5)
    w, h = Image.open(io.BytesIO(png)).size
    across = w / max(h, 1) > 2.2

    def turn(section):
        section.orientation = WD_ORIENT.LANDSCAPE if section.orientation == WD_ORIENT.PORTRAIT else WD_ORIENT.PORTRAIT
        section.page_width, section.page_height = section.page_height, section.page_width

    if across:
        turn(doc.add_section(WD_SECTION.NEW_PAGE))
    section = doc.sections[-1]
    room_w = section.page_width - section.left_margin - section.right_margin
    room_h = section.page_height - section.top_margin - section.bottom_margin - Cm(3)
    width = min(room_w, int(room_h * w / max(h, 1)))
    p = doc.add_paragraph()
    p.add_run(de_en("Kategoriensystem", "Category system")).bold = True
    doc.add_picture(io.BytesIO(png), width=width)
    if across:
        turn(doc.add_section(WD_SECTION.NEW_PAGE))


def guide_docx(project, survey, who):
    from app.interview_docx import _document, _head, _note
    from app.lang import english

    ui = "en" if english() else "de"
    doc = _document()
    _head(doc, survey, de_en("Kodierleitfaden: {name}", "Coding guide: {name}", name=project.name), ui)
    if project.question:
        p = doc.add_paragraph()
        p.add_run(de_en("Forschungsfrage: ", "Research question: ")).bold = True
        p.add_run(project.question)
    _guide_diagram(doc, project, who, survey)
    st = stats(project, who)
    mains, candidates = tree(project)
    from app.transcript_coding_ai import taken_from_ai

    from_ai = taken_from_ai(project, interviews(project, who))
    spots = anchor_spots(project, who)  # "(B03, Pos. 12, 00:14:32)": where the anchor stands; "" where `who` does not see it
    more = example_spots(project, all_codings(project, who))  # up to two further examples

    def entry(cat, level, number=""):
        doc.add_heading(f"{number} {cat.name}".strip(), level=min(level, 9))
        lines = [(de_en("Definition", "Definition"), cat.definition), (de_en("Abgrenzung", "Delimitation"), cat.delimitation),
                 (de_en("Ankerbeispiel", "Anchor example"), (f"„{clean_quote((cat.anchor or {}).get('quote'))}“"
                                                             + (f" ({spots[cat.id]})" if spots.get(cat.id) else "")
                                                             if (cat.anchor or {}).get("quote") else "")),
                 (de_en("Weitere Beispiele", "Further examples"), "; ".join(f"„{q}“ ({w})" for q, w in more.get(cat.id, []))),
                 (de_en("Früher auch", "Formerly also"), ", ".join(cat.former_names or [])),
                 (de_en("Stellen", "Passages"), str(st.get(cat.id, {}).get("n", 0))
                  + (de_en(", davon {n} aus KI-Vorschlägen übernommen", ", of them {n} accepted from AI suggestions", n=from_ai[cat.id])
                     if from_ai.get(cat.id) else ""))]
        for label, text in lines:
            if text:
                p = doc.add_paragraph()
                p.add_run(label + ": ").bold = True
                p.add_run(text)
        p = doc.add_paragraph()
        p.add_run(de_en("angelegt von {who}", "created by {who}", who=cat.author_name or "–")).italic = True

    number = numbers(mains)  # 1, 1.1, 1.1.1 … down every level
    for main, subs in mains:
        entry(main, 1, number[main.id])
        for s in subs:
            entry(s, s.level, number[s.id])
    for c in candidates:
        entry(c, 2)
    from app import transcript_coding_double as double

    agree = double.agreement(project, interviews(project, who))
    if agree["_pairs"]:
        doc.add_heading(de_en("Übereinstimmung", "Agreement"), level=1)
        doc.add_paragraph(de_en("{n} abgeschlossene Doppelkodierungen; Wortebene mit Cohens κ (Cohen, 1960; Lesart nach Landis "
                                "& Koch, 1977), Stellenebene bei mindestens {t} % Überlappung.",
                                "{n} finished double codings; word level with Cohen's κ (Cohen, 1960; reading after Landis & "
                                "Koch, 1977), passage level at an overlap of at least {t}%.", n=agree["_pairs"], t=round(double.OVERLAP * 100)))
        names = {c.id: c.name for c in live(project)}
        sums = [(f"sum:{cid}", de_en("{name} mit allem darunter", "{name} with all below it", name=names[cid]))
                for cid in names if f"sum:{cid}" in agree]
        for key, label in [("all", de_en("Alle Kategorien", "All categories"))] + list(names.items()) + sums:
            f = agree[key]
            if not f["enough"]:
                continue
            kappa = f"{f['kappa']:.2f} ({double.reading(f['kappa'])})" if f["kappa"] is not None else "–"
            doc.add_paragraph(f"{label}: " + de_en("Wörter {p} %, κ = {k}; Stellen {m} von {n}", "words {p}%, κ = {k}; passages {m} of {n}",
                                                   p=round(100 * f["p_o"], 1), k=kappa, m=f["passages"][0], n=f["passages"][1]))
    _note(doc, de_en("Inhaltlich strukturierende qualitative Inhaltsanalyse nach ", "Content-structuring qualitative content analysis after ")
          + KUCKARTZ + " " + MAYRING)
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


# --- the pages -------------------------------------------------------------------------------------------------------


def _back(survey, project, anchor=""):
    from app.transcript_coding import _project_url

    return redirect(_project_url(survey, project, anchor))


def _context(survey, project, who):
    names = {c.id: c.name for c in db.session.scalars(db.select(CodingCategory).filter_by(project_id=project.id))}
    from app.transcript_coding import event_text

    return dict(survey=survey, project=project, who=who, event_text=lambda e: event_text(e, names),
                move_back=lambda e: may_take_back_move(project, e, who), changed_since=lambda e: changed_since(project, e))


@bp.route("/<code>/interviews/coding/<int:pid>/overview")
@signed_in_or_coder
@heavy.limited  # a place among the heavy evaluations (performance audit 2026-11-11)
def overview(code, pid):
    from app.transcript_coding import history

    survey, project, who = _in(code, pid)
    if request.args.get("view") == "map":  # the map moved to the view "Kategoriensystem" (Ralf 2026-10-28)
        return redirect(url_for("transcript_coding.project", code=survey.code, pid=project.id, view="categories", show="diagram"))
    from app import transcript_coding_memos

    mains, candidates = tree(project)
    pairs = fuzzy(project, who)
    probes = probe(project, who)
    from app import transcript_coding_overlap

    # overlaps of categories (app/transcript_coding_overlap.py): level and "im selben Absatz" in the address, a cell too
    ov = transcript_coding_overlap.table(project, all_codings(project, who), level=request.args.get("ov", "main"),
                                         paragraph=request.args.get("ov_para") == "1")
    cell = None
    a, b = request.args.get("ov_a", type=int), request.args.get("ov_b", type=int)
    names = {c.id: c for c in ov["cats"]}
    if a in names and b in names and a != b:
        cell = {"a": names[a], "b": names[b], "pairs": ov["pairs"].get((a, b), [])}
    memo_rows = transcript_coding_memos.memo_list(project, who, request.args)
    all_memos = transcript_coding_memos.visible(project, who)
    from app.transcript_coding import may_export, numbers

    return render_template("transcript_coding/overview.html", mains=mains, candidates=candidates, stats=stats(project, who),
                           nums=numbers(mains), may_export=may_export(project, who),
                           memo_rows=memo_rows, memos=[transcript_coding_memos.as_json(m, who) for m in all_memos],
                           memo_kind=transcript_coding_memos.kind_name,
                           probes=probes, pairs=pairs[:12], blurred=blurred(project, who, pairs, probes), ov=ov, ov_cell=cell,
                           history=history(project, 8),
                           notices=notices(project, who), n_interviews=len(interviews(project, who)),
                           **_context(survey, project, who))


def map_pdf(project, who, survey, **opts):
    """The diagram as a PDF page with reportlab, A4 across when the diagram is wide, scaled to fit. The picture is the
    PNG of app/svg_png.py at print resolution: the vector way of the results report (results_pdf.svg_drawing) redraws
    figures in its own grey APA style and would lose the colours of the categories and the boxes."""
    from PIL import Image as PILImage
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Image, SimpleDocTemplate, Spacer

    from app import pdf_fonts

    png = map_png(project, who, survey, scale=3.0, **opts)
    w, h = PILImage.open(io.BytesIO(png)).size
    size = landscape(A4) if w > h else A4
    margin = 15 * mm
    room_w, room_h = size[0] - 2 * margin, size[1] - 2 * margin - 22 * mm
    factor = min(room_w / w, room_h / h)
    pdf_fonts.register()
    sample = getSampleStyleSheet()
    styles = {"Heading2": ParagraphStyle("h2", parent=sample["Heading2"], fontName=pdf_fonts.BOLD),
              "Normal": ParagraphStyle("n", parent=sample["Normal"], fontName=pdf_fonts.REGULAR)}
    buf = io.BytesIO()
    title = de_en("Kategoriensystem: {name}", "Category system: {name}", name=project.name)
    doc = SimpleDocTemplate(buf, pagesize=size, leftMargin=margin, rightMargin=margin, topMargin=margin, bottomMargin=margin,
                            title=title)
    head = [pdf_fonts.paragraph(pdf_fonts.markup(title), styles["Heading2"])]
    if project.question:
        head.append(pdf_fonts.paragraph(pdf_fonts.markup(project.question), styles["Normal"]))
    doc.build(head + [Spacer(1, 4 * mm), Image(io.BytesIO(png), width=w * factor, height=h * factor)])
    return buf.getvalue()


def map_png(project, who, survey, scale=2.0, **opts):
    """The diagram as PNG (app/svg_png.py, Pillow; no outside renderer)."""
    from app import svg_png

    return svg_png.render(map_svg(project, who, survey, **opts), scale=scale)


@bp.route("/<code>/interviews/coding/<int:pid>/map.png")
@signed_in_or_coder
@heavy.limited  # a place among the heavy evaluations (performance audit 2026-11-11)
def map_png_file(code, pid):
    survey, project, who = _in(code, pid)
    return HttpResponse(map_png(project, who, survey, **map_options(request.args)), mimetype="image/png",
                        headers={"Content-Disposition": f'attachment; filename="kategoriensystem-{survey.code}-{project.id}.png"'})


@bp.route("/<code>/interviews/coding/<int:pid>/map.pdf")
@signed_in_or_coder
@heavy.limited  # a place among the heavy evaluations (performance audit 2026-11-11)
def map_pdf_file(code, pid):
    survey, project, who = _in(code, pid)
    return HttpResponse(map_pdf(project, who, survey, **map_options(request.args)), mimetype="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="kategoriensystem-{survey.code}-{project.id}.pdf"'})


@bp.route("/<code>/interviews/coding/<int:pid>/map.svg")
@signed_in_or_coder
@heavy.limited  # a place among the heavy evaluations (performance audit 2026-11-11)
def map_file(code, pid):
    survey, project, who = _in(code, pid)
    return HttpResponse(map_svg(project, who, survey, **map_options(request.args)), mimetype="image/svg+xml",
                        headers={"Content-Disposition": f'attachment; filename="kategoriensystem-{survey.code}-{project.id}.svg"'})


@bp.route("/<code>/interviews/coding/<int:pid>/cases")
@signed_in_or_coder
def cases(code, pid):
    """The thematic case summaries of this analysis for whoever works in it (app/transcript_coding_cases.py): the
    interviews the reader sees with a transcript, the main categories as columns."""
    from app import transcript_coding_cases as tcc
    from app.interview_summaries import author_line, summaries

    survey, project, who = _in(code, pid)
    rows = [r for r in interviews(project, who) if has_transcript(r)]
    mains = [m for m, _subs in tree(project)[0]]
    key = lambda m: f"c{project.id}:{m.id}"  # noqa: E731
    writable = {r.id: all(tcc.writer(survey, r, key(m)) is not None for m in mains[:1]) for r in rows}
    return render_template("transcript_coding/cases.html", survey=survey, project=project, who=who, rows=rows, mains=mains,
                           cells=tcc.cells(project, rows, who), draft=tcc.draft, summaries=summaries,
                           author_line=author_line, writable=lambda r: writable.get(r.id, False))


@bp.route("/<code>/interviews/coding/<int:pid>/passages")
@signed_in_or_coder
def passages(code, pid):
    from app.transcript_coding import in_order, menu_groups

    survey, project, who = _in(code, pid)
    mains, candidates = tree(project)
    cats = in_order(mains, candidates)  # the first category of the system is the one shown first
    wanted = request.args.getlist("cat", type=int) or ([cats[0].id] if cats else [])
    rows = [(r, c) for r, c in all_codings(project, who) if c["cat"] in wanted]
    at = spotter()
    where = {c["id"]: at(r, c["start"]) for r, c in rows}
    return render_template("transcript_coding/passages.html", groups=menu_groups(mains, candidates), wanted=wanted, rows=rows, where=where,
                           names={c.id: c.name for c in cats}, **_context(survey, project, who))


PASSAGE_SIZES = (10, 20, 50)  # rows per page of the dialog of a category in the diagram


def category_passages(project, who, cat_id):
    """[(interview, coding)] of the category and of everything below it, on every level (as the diagram counts it,
    `stats`), over the interviews the actor sees; in the order of the category system, then interview and place."""
    from app.transcript_coding import chain

    mains, _candidates = tree(project)
    order = {c.id: i for i, c in enumerate(c for m, ss in mains for c in [m] + ss)}
    cats = {c.id: c for c in live(project)}
    wanted = {cid for cid, c in cats.items() if cid == cat_id or cat_id in {a.id for a in chain(c, cats)}}
    rows = [(r, c) for r, c in all_codings(project, who) if c["cat"] in wanted]
    rows.sort(key=lambda rc: (order.get(rc[1]["cat"], len(order)), (rc[0].interview or {}).get("label", ""), rc[0].id,
                              rc[1].get("start") or 0))
    return rows


@bp.route("/<code>/interviews/coding/<int:pid>/categories/<int:cid>/passages.json")
@signed_in_or_coder
def category_passages_json(code, pid, cid):
    """The passages of one category for its dialog in the diagram (Ralf 2026-11-18), loaded only on a click and a page
    at a time: the definition of the clicked category (shown fixed above the list), quote, interview and place, the subcategory below the clicked one it was coded with, who coded and a
    link to the place in the transcript."""
    from flask import jsonify

    survey, project, who = _in(code, pid)
    cats = {c.id: c for c in live(project)}
    if cid not in cats:
        abort(404)
    tree(project)  # gives each category its path
    per = request.args.get("per", type=int)
    per = per if per in PASSAGE_SIZES else PASSAGE_SIZES[0]
    rows = category_passages(project, who, cid)
    pages = max(1, -(-len(rows) // per))
    page = min(max(request.args.get("page", 1, type=int) or 1, 1), pages)
    shown = rows[(page - 1) * per:page * per]
    at = spotter()
    cat = cats[cid]
    top = getattr(cat, "path", cat.name)

    def below(c):  # the path below the clicked category, empty for itself
        path = getattr(cats.get(c["cat"]), "path", "")
        return path[len(top) + 3:] if c["cat"] != cid and path.startswith(top + " › ") else ""

    return jsonify(name=top, definition=cat.definition or "", total=len(rows), page=page, pages=pages, per=per, sizes=list(PASSAGE_SIZES),
                   rows=[{"quote": clean_quote(c.get("quote")), "where": at(r, c["start"]), "by": c.get("by_name") or "",
                          "sub": below(c),
                          "url": url_for("transcript_coding.page", code=survey.code, pid=project.id, rid=r.id,
                                         at=f"{c['start']}-{c['end']}")} for r, c in shown])


def _matrix(project, who):
    mains, candidates = tree(project)
    cats = [s for _m, subs in mains for s in subs] + list(candidates)  # in the order of the category system
    rows = [r for r in interviews(project, who) if has_transcript(r)]
    cells = {}
    for r, c in all_codings(project, who):
        cells[(c["cat"], r.id)] = cells.get((c["cat"], r.id), 0) + 1
    return cats, rows, cells


@bp.route("/<code>/interviews/coding/<int:pid>/matrix")
@signed_in_or_coder
def matrix(code, pid):
    survey, project, who = _in(code, pid)
    cats, rows, cells = _matrix(project, who)
    if request.args.get("format") == "csv":
        from app.export import safe_text

        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow([safe_text(x) for x in [de_en("Kategorie", "category")] + [(r.interview or {}).get("label", "") for r in rows]])
        for cat in cats:
            w.writerow([safe_text(cat.name)] + [cells.get((cat.id, r.id), 0) for r in rows])
        return send_file(io.BytesIO(("﻿" + buf.getvalue()).encode("utf-8")), as_attachment=True, mimetype="text/csv",
                         download_name=f"kategorien-interviews-{survey.code}-{project.id}.csv")
    mains, candidates = tree(project)
    return render_template("transcript_coding/matrix.html", cats=cats, rows=rows, cells=cells, mains=mains,
                           candidates=candidates, nums=numbers(mains), **_context(survey, project, who))


@bp.route("/<code>/interviews/coding/<int:pid>/history")
@signed_in_or_coder
def full_history(code, pid):
    from app.transcript_coding import history

    survey, project, who = _in(code, pid)
    return render_template("transcript_coding/history.html", history=history(project, 1000), **_context(survey, project, who))


@bp.route("/<code>/interviews/coding/<int:pid>/merge", methods=["POST"])
@signed_in_or_coder
def merge_categories(code, pid):
    survey, project, who = _in(code, pid)
    source = db.session.get(CodingCategory, request.form.get("source", type=int) or 0)
    target = db.session.get(CodingCategory, request.form.get("target", type=int) or 0)
    if source is None or target is None or source.removed_at or target.removed_at:
        abort(404)
    values = {k: request.form.get(k) for k in ("name", "definition", "delimitation", "anchor") if k in request.form}
    names = (source.name, target.name)  # as they were, for the message
    if may_propose_merge(project, source, target, who):  # setting "only team and author" (Ralf 2026-10-29)
        from app.transcript_coding import propose_merge

        problem = propose_merge(project, source, target, who, request.form.get("reason"), values)
        if problem:
            db.session.rollback()
            flash(problem, "error")
        else:
            db.session.commit()
            flash(de_en("Vorschlag gesendet: „{a}“ mit „{b}“ zusammenführen. Team und Autor entscheiden.",
                        "Proposal sent: merge “{a}” with “{b}”. The team and the author decide.", a=names[0], b=names[1]), "success")
        return redirect(request.form.get("back") if (request.form.get("back") or "").startswith("/") and not (request.form.get("back") or "").startswith("//")
                        else url_for("transcript_coding.overview", code=survey.code, pid=project.id))
    problem = merge(project, source, target, who, request.form.get("reason"), values)
    if problem:
        db.session.rollback()
        flash(problem, "error")
    else:
        db.session.commit()
        flash(de_en("„{a}“ ist mit „{b}“ zusammengeführt. Wer Stellen darin hat, sieht das und kann seine herauslösen.",
                    "“{a}” is merged with “{b}”. Whoever has passages in it sees this and can take theirs out.",
                    a=names[0], b=names[1])
              + (de_en(" Neuer Name: „{c}“.", " New name: “{c}”.", c=target.name) if target.name != names[1] else ""), "success")
    return redirect(request.form.get("back") if (request.form.get("back") or "").startswith("/") and not (request.form.get("back") or "").startswith("//")
                    else url_for("transcript_coding.overview", code=survey.code, pid=project.id))


@bp.route("/<code>/interviews/coding/<int:pid>/events/<int:eid>/<answer>", methods=["POST"])
@signed_in_or_coder
def answer(code, pid, eid, answer):
    survey, project, who = _in(code, pid)
    event = db.session.get(CodingEvent, eid)
    if event is None or event.project_id != project.id or answer not in ANSWERS:
        abort(404)
    if answer == "comment":
        text = " ".join(request.form.get("text", "").split())[:1000]
        if text:
            _event(project, who, "comment", db.session.get(CodingCategory, event.category_id) if event.category_id else None,
                   reason=text, reply_to=event.id)
            db.session.commit()
        return redirect(url_for("transcript_coding.overview", code=survey.code, pid=project.id) + "#verlauf")
    if answer == "undo":
        if event.kind == "moved":  # a move, taken back from the history (Ralf 2026-10-29)
            problem = undo_move(project, event, who, force=bool(request.form.get("force")))
            done = de_en("Verschieben zurückgenommen.", "Move taken back.")
        else:
            problem = undo_merge(project, event, who)
            done = de_en("Zusammenführen zurückgenommen.", "Merge taken back.")
        if problem:
            db.session.rollback()
            flash(problem, "error")
        else:
            db.session.commit()
            flash(done, "success")
        return redirect(url_for("transcript_coding.overview", code=survey.code, pid=project.id))
    if event.kind not in ("merged", "changed", "moved", "recoded") or _answered(project, event.id, who.key):
        abort(404)
    if event.kind == "recoded" and answer == "split_off":  # "Zurück nach …": the own passage back where it was
        from app.transcript_coding_view import recode_back

        _moved, problem = recode_back(project, who, event)
        if problem:
            flash(problem, "error")
        else:
            _event(project, who, "split_off", db.session.get(CodingCategory, event.category_id), {"moved": 1}, reply_to=event.id)
            db.session.commit()
            flash(de_en("Die Stelle steht wieder in „{name}“.", "The passage is back in “{name}”.",
                        name=(event.data or {}).get("from_name", "")), "success")
        return redirect(request.referrer if request.referrer and request.referrer.startswith(request.host_url)
                        else url_for("transcript_coding.overview", code=survey.code, pid=project.id))
    if answer == "fits":
        _event(project, who, "fits", db.session.get(CodingCategory, event.category_id), reply_to=event.id)
        db.session.commit()
        flash(de_en("Bestätigt.", "Confirmed."), "success")
    else:
        result = split_off(project, event, who)
        if isinstance(result, str):
            db.session.rollback()
            flash(result, "error")
        else:
            db.session.commit()
            flash(de_en("Ihre Stellen stehen jetzt in „{name}“.", "Your passages are now in “{name}”.", name=result.name), "success")
    return redirect(request.referrer if request.referrer and request.referrer.startswith(request.host_url)
                    else url_for("transcript_coding.overview", code=survey.code, pid=project.id))


@bp.route("/<code>/interviews/coding/<int:pid>/check", methods=["GET", "POST"])
@signed_in_or_coder
def check(code, pid):
    """The mutual check: a passage someone else coded, without its category; the actor chooses one."""
    survey, project, who = _in(code, pid)
    if request.method == "POST":
        row = db.session.get(Response, request.form.get("rid", type=int) or 0)
        if row is None or row.id not in {r.id for r in interviews(project, who)}:
            abort(404)
        if check_mode(project) == CHECK_PLAN and check_plan(project).get(who.key) is None:
            abort(404)  # by the plan only who has a row checks
        record_check(project, who, row, request.form.get("coding", ""), request.form.get("cat", type=int))
        return redirect(url_for("transcript_coding.check", code=survey.code, pid=project.id))
    found = next_check(project, who)
    context = None
    if found is not None:
        row, coding = found
        text = transcript(row)
        context = {"before": text[max(0, coding["start"] - CONTEXT):coding["start"]], "quote": text[coding["start"]:coding["end"]],
                   "after": text[coding["end"]:coding["end"] + CONTEXT], "row": row, "coding": coding}
    done = sum(1 for _c, chk in checks(project, who) if chk.get("by") == who.key)
    mains, candidates = tree(project)
    entry = my_plan(project, who)
    mine = None
    if entry is not None:
        labels, block_names = plan_labels(project, survey, entry)
        checked, total = check_progress(project, who)
        mine = {"labels": labels, "blocks": block_names, "done": checked, "total": total}
    from app.transcript_coding import branches

    return render_template("transcript_coding/check.html", item=context, mains=mains, candidates=candidates, done=done,
                           nums=numbers(mains), rows={m.id: branches(subs) for m, subs in mains},
                           by_plan=check_mode(project) == CHECK_PLAN, mine=mine, **_context(survey, project, who))


def _back_to_plan(survey, project):
    return redirect(url_for("transcript_coding.project", code=survey.code, pid=project.id, view="settings") + "#probe")


@bp.route("/<code>/interviews/coding/<int:pid>/check/mode", methods=["POST"])
@signed_in_or_coder
@autosave.aware
def check_mode_save(code, pid):
    """Zufällig or nach Zuteilung (the team)."""
    survey, project, _who = _in(code, pid, team_only=True)
    set_check_mode(project, request.form.get("mode"))
    db.session.commit()
    flash(de_en("Gespeichert.", "Saved."), "success")
    return _back_to_plan(survey, project)


def _ticked(name, every, convert):
    """The ticked values of a list of boxes, None when every one is ticked ("alle", also those added later)."""
    values = []
    for raw in request.form.getlist(name):
        try:
            values.append(convert(raw))
        except ValueError:
            continue
    return None if every and set(every) <= set(values) else values


def _block_id(raw):
    return CANDIDATES if raw == CANDIDATES else int(raw)


@bp.route("/<code>/interviews/coding/<int:pid>/check/plan", methods=["POST"])
@signed_in_or_coder
def check_plan_save(code, pid):
    """A row of the plan: who checks which interviews and blocks; "copy" takes over those of another row."""
    from app import interview_data

    survey, project, _who = _in(code, pid, team_only=True)
    key = request.form.get("who", "")
    other = check_plan(project).get(request.form.get("copy", ""))
    if other is not None:
        rids, block_ids = other.get("interviews"), other.get("blocks")
    else:
        every = [r.id for r in interview_data.interviews(survey, source="real")]
        rids = _ticked("rid", every, int)
        block_ids = _ticked("block", [b for b, _n in blocks(project)], _block_id)
    if not set_plan_row(project, survey, key, rids, block_ids):
        db.session.rollback()
        flash(de_en("Bitte mindestens ein Interview und einen Kategorieblock wählen.",
                    "Please choose at least one interview and one category block."), "error")
        return _back_to_plan(survey, project)
    db.session.commit()
    flash(de_en("Probe zugeteilt.", "Check assigned."), "success")
    return _back_to_plan(survey, project)


@bp.route("/<code>/interviews/coding/<int:pid>/check/plan/remove", methods=["POST"])
@signed_in_or_coder
def check_plan_remove(code, pid):
    survey, project, _who = _in(code, pid, team_only=True)
    remove_plan_row(project, request.form.get("who", ""))
    db.session.commit()
    flash(de_en("Zuteilung entfernt.", "Assignment removed."), "success")
    return _back_to_plan(survey, project)


@bp.route("/<code>/interviews/coding/<int:pid>/kodierleitfaden.docx")
@signed_in_or_coder
@heavy.limited  # a place among the heavy evaluations (performance audit 2026-11-11)
def coding_guide(code, pid):
    survey, project, who = _in(code, pid)
    return send_file(io.BytesIO(guide_docx(project, survey, who)), as_attachment=True,
                     mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                     download_name=f"kodierleitfaden-{survey.code}-{project.id}.docx")

