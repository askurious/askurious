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
(Response.interview["checks"]), like the codings.

The coding guide (Kodierleitfaden, Word): research question, per category definition, delimitation, anchor example,
former names and number of passages (Kuckartz & Rädiker, 2022; Mayring, 2022)."""
import csv
import difflib
import io
import random
from xml.sax.saxutils import escape

from flask import Response as HttpResponse
from flask import abort, flash, redirect, render_template, request, send_file, url_for

from app.extensions import db
from app.lang import de_en
from app.models import CodingCategory, CodingEvent, Response, utcnow
from app.transcript_coding import _add, _event, _in, _key, bp, interviews, live, resolver, signed_in_or_coder, tree
from app.transcript_coding_view import codings, has_transcript, placed, transcript

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
        for i, x in enumerate(items):
            for y in items[i + 1:]:
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
        return True
    return who.team and target.kind == CodingCategory.MAIN and source.kind in (CodingCategory.MAIN, CodingCategory.CANDIDATE)


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
    """The merges and renames by others that carry own passages of the actor and that the actor has not answered:
    [(event, own passages)]."""
    out = []
    for e in db.session.scalars(db.select(CodingEvent).filter_by(project_id=project.id)
                                .where(CodingEvent.kind.in_(("merged", "changed", "moved"))).order_by(CodingEvent.id.desc()).limit(200)):
        data = e.data or {}
        if e.actor_key == who.key or data.get("undone") or _answered(project, e.id, who.key):
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


def next_check(project, who):
    """A passage someone else coded and the actor has not checked yet: (interview, coding) or None."""
    from app.transcript_coding_double import assignment, finished

    pool = []
    for row in interviews(project, who):
        entry = assignment(project, row.id)
        if not has_transcript(row) or (entry is not None and not finished(entry)):
            continue  # nobody checks passages of a double coding before it is finished
        done = {c.get("coding") for c in (row.interview or {}).get("checks") or []
                if isinstance(c, dict) and c.get("by") == who.key and c.get("project") == project.id}
        ok, _lost = placed(row, project)
        pool.extend((row, c) for c in ok if c.get("by") != who.key and c.get("id") not in done)
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


def map_svg(project, who, survey, interactive=False):
    """The category system as a tree: the research question, then one column per level; every subcategory with a
    bar of its passages (with what stands below it); blurring pairs joined by a dashed line. Plain SVG with its own
    colours, to download as it is. The drawing grows to the right with the levels and scrolls in its own box."""
    mains, candidates = tree(project)
    st = stats(project, who)
    pairs = [p for p in fuzzy(project, who) if p["overlap"] or p["checks"]]
    subs = [s for _m, ss in mains for s in ss]
    biggest = max([st[s.id]["n"] for s in subs] + [1])
    depth = max([s.level for s in subs] + [1])
    row_h, x0, col, box = 34, 20, 290, 250
    below = {}
    for s in subs:
        below.setdefault(s.parent_id, []).append(s)
    pos, y = {}, 20

    def place(cat):  # leaves one row each, a category in the middle of what stands below it
        nonlocal y
        kids = below.get(cat.id, [])
        if not kids:
            pos[cat.id] = y + 14
            y += row_h
            return pos[cat.id]
        ys = [place(k) for k in kids]
        pos[cat.id] = (ys[0] + ys[-1]) / 2
        return pos[cat.id]

    for main, _ss in mains:
        place(main)
        y += 14
    width = x0 + 250 + 50 + depth * col + 140
    height = max(y + 30, 120)
    mid = height / 2
    title = escape(project.question or project.name)[:90]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{int(height)}" viewBox="0 0 {width} {int(height)}" '
             f'font-family="Arial, sans-serif" font-size="13" role="img" aria-label="{escape(de_en("Kategoriensystem als Diagramm", "Category system as a diagram"))}">',
             '<rect width="100%" height="100%" fill="#ffffff"/>',
             ('<g class="tc-map-root" data-tc-drop-main>' if interactive else "<g>"),
             f'<rect x="{x0}" y="{mid - 26}" width="250" height="52" rx="10" fill="#fbf3ef" stroke="#a8401c" stroke-width="2"/>',
             f'<text x="{x0 + 12}" y="{mid - 4}" font-weight="bold" fill="#1c1c1a">{escape(project.name)[:34]}</text>',
             f'<text x="{x0 + 12}" y="{mid + 14}" fill="#5f5e59" font-size="11">{title[:42]}</text>', "</g>"]

    def x_of(level):
        return x0 + 250 + 50 + (level - 1) * col

    from flask import url_for

    from app.transcript_coding import may_move

    def node(cat):  # on the page: a node to drag where the person may move it (app/static/transcript_coding.js)
        if not interactive:
            return "<g>"
        grab = (f' data-tc-grab="{cat.id}" data-tc-move-url="{escape(url_for("transcript_coding.move_category", code=survey.code, pid=project.id, cid=cat.id))}"'
                if may_move(project, cat, who) else "")
        return (f'<g class="tc-map-node" data-tc-cat="{cat.id}" data-tc-anc-of="{" ".join(str(a) for a in cat.anc)}"{grab}>'
                f'<title>{escape(cat.path)}</title>')

    def link(xa, ya, xb, yb):
        parts.append(f'<path d="M{xa} {ya} C {xb - 25} {ya}, {xb - 25} {yb}, {xb} {yb}" fill="none" stroke="#cfcdc5" stroke-width="1.5"/>')

    for main, ss in mains:
        my, x1 = pos[main.id], x_of(1)
        link(x0 + 250, mid, x1, my)
        parts.append(node(main))
        parts.append(f'<rect x="{x1}" y="{my - 15}" width="{box}" height="30" rx="8" fill="#f8f8f5" stroke="#706e68"/>')
        parts.append(f'<text x="{x1 + 10}" y="{my + 5}" font-weight="bold" fill="#1c1c1a">{escape(main.name)[:30]}</text>')
        parts.append(f'<text x="{x1 + 240}" y="{my + 5}" text-anchor="end" fill="#5f5e59">{st[main.id]["n"]}</text>')
        parts.append("</g>")
        for s in ss:
            sy, xs = pos[s.id], x_of(s.level)
            above = pos.get(s.parent_id, my)
            link(x_of(s.level - 1) + box, above, xs, sy)
            colour = s.colour or main.colour or "#2957A4"
            parts.append(node(s))
            if interactive:  # a whole row to grab and to drop onto, not only the small dot
                parts.append(f'<rect class="tc-hit" x="{xs - 4}" y="{sy - 14}" width="{box + 20}" height="28" rx="6" fill="#ffffff" fill-opacity="0.01" stroke="none"/>')
            parts.append(f'<circle cx="{xs + 6}" cy="{sy}" r="5" fill="{escape(colour)}"/>')
            parts.append(f'<text x="{xs + 18}" y="{sy + 4}" fill="#1c1c1a">{escape(s.name)[:22]}</text>')
            bar = int(60 * st[s.id]["n"] / biggest)
            parts.append(f'<rect x="{xs + 170}" y="{sy - 5}" width="60" height="10" rx="2" fill="#efeee9"/>')
            if bar:
                parts.append(f'<rect x="{xs + 170}" y="{sy - 5}" width="{bar}" height="10" rx="2" fill="#378ccf"/>')
            parts.append(f'<text x="{xs + 262}" y="{sy + 4}" text-anchor="end" fill="#5f5e59">{st[s.id]["n"]}</text>')
            parts.append("</g>")
    levels = {s.id: s.level for s in subs}
    for p in pairs:
        a, b = pos.get(p["a"].id), pos.get(p["b"].id)
        if a is None or b is None or p["a"].id not in levels or p["b"].id not in levels:
            continue
        xa, xb = x_of(levels[p["a"].id]) + 150, x_of(levels[p["b"].id]) + 150
        bend = max(xa, xb) + min(120, abs(a - b) / 3)
        parts.append(f'<path d="M{xa} {a} C {bend} {a}, {bend} {b}, {xb} {b}" fill="none" stroke="#b35c00" '
                     f'stroke-width="1.5" stroke-dasharray="6 5"/>')
    if candidates:
        parts.append(f'<text x="{x0}" y="{height - 12}" fill="#5f5e59" font-size="11">'
                     f'{escape(de_en("Kandidaten: ", "Candidates: ") + ", ".join(c.name for c in candidates))[:140]}</text>')
    parts.append("</svg>")
    return "".join(parts)


# --- the coding guide ------------------------------------------------------------------------------------------------


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
    st = stats(project, who)
    mains, candidates = tree(project)
    from app.transcript_coding_ai import taken_from_ai

    from_ai = taken_from_ai(project, interviews(project, who))

    def entry(cat, level, number=""):
        doc.add_heading(f"{number} {cat.name}".strip(), level=min(level, 9))
        lines = [(de_en("Definition", "Definition"), cat.definition), (de_en("Abgrenzung", "Delimitation"), cat.delimitation),
                 (de_en("Ankerbeispiel", "Anchor example"), (f"„{(cat.anchor or {}).get('quote')}“ ({(cat.anchor or {}).get('label', '')})"
                                                             if (cat.anchor or {}).get("quote") else "")),
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

    numbers = {}
    for n, (main, subs) in enumerate(mains, 1):  # numbered 1, 1.1, 1.1.1 … down every level
        numbers[main.id] = str(n)
        entry(main, 1, numbers[main.id])
        count = {}
        for s in subs:
            count[s.parent_id] = count.get(s.parent_id, 0) + 1
            numbers[s.id] = f"{numbers.get(s.parent_id, numbers[main.id])}.{count[s.parent_id]}"
            entry(s, s.level, numbers[s.id])
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


def _paths(project):
    """{category id: its path "A › B › C"} for naming the category above one."""
    tree(project)
    return {c.id: getattr(c, "path", c.name) for c in live(project)}


def _back(survey, project, anchor=""):
    from app.transcript_coding import _project_url

    return redirect(_project_url(survey, project, anchor))


def _context(survey, project, who):
    names = {c.id: c.name for c in db.session.scalars(db.select(CodingCategory).filter_by(project_id=project.id))}
    from app.transcript_coding import event_text

    return dict(survey=survey, project=project, who=who, event_text=lambda e: event_text(e, names))


@bp.route("/<code>/interviews/coding/<int:pid>/overview")
@signed_in_or_coder
def overview(code, pid):
    from app.transcript_coding import history

    survey, project, who = _in(code, pid)
    if request.args.get("view") == "map":  # the map moved to the view "Kategoriensystem" (Ralf 2026-10-28)
        return redirect(url_for("transcript_coding.project", code=survey.code, pid=project.id, view="categories", show="diagram"))
    mains, candidates = tree(project)
    pairs = fuzzy(project, who)
    probes = probe(project, who)
    return render_template("transcript_coding/overview.html", mains=mains, candidates=candidates, stats=stats(project, who),
                           probes=probes, pairs=pairs[:12], blurred=blurred(project, who, pairs, probes),
                           history=history(project, 8),
                           notices=notices(project, who), n_interviews=len(interviews(project, who)),
                           **_context(survey, project, who))


@bp.route("/<code>/interviews/coding/<int:pid>/map.svg")
@signed_in_or_coder
def map_file(code, pid):
    survey, project, who = _in(code, pid)
    return HttpResponse(map_svg(project, who, survey), mimetype="image/svg+xml",
                        headers={"Content-Disposition": f'attachment; filename="kategoriensystem-{survey.code}-{project.id}.svg"'})


@bp.route("/<code>/interviews/coding/<int:pid>/passages")
@signed_in_or_coder
def passages(code, pid):
    survey, project, who = _in(code, pid)
    cats = live(project)
    wanted = request.args.getlist("cat", type=int) or ([cats[0].id] if cats else [])
    rows = [(r, c) for r, c in all_codings(project, who) if c["cat"] in wanted]
    return render_template("transcript_coding/passages.html", cats=cats, wanted=wanted, rows=rows,
                           names={c.id: c.name for c in cats}, **_context(survey, project, who))


def _matrix(project, who):
    cats = [c for c in live(project) if c.kind != CodingCategory.MAIN]
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
    return render_template("transcript_coding/matrix.html", cats=cats, rows=rows, cells=cells,
                           parents=_paths(project), **_context(survey, project, who))


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
        problem = undo_merge(project, event, who)
        if problem:
            flash(problem, "error")
        else:
            db.session.commit()
            flash(de_en("Zusammenführen zurückgenommen.", "Merge taken back."), "success")
        return redirect(url_for("transcript_coding.overview", code=survey.code, pid=project.id))
    if event.kind not in ("merged", "changed", "moved") or _answered(project, event.id, who.key):
        abort(404)
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
    return render_template("transcript_coding/check.html", item=context, mains=mains, candidates=candidates, done=done,
                           **_context(survey, project, who))


@bp.route("/<code>/interviews/coding/<int:pid>/kodierleitfaden.docx")
@signed_in_or_coder
def coding_guide(code, pid):
    survey, project, who = _in(code, pid)
    return send_file(io.BytesIO(guide_docx(project, survey, who)), as_attachment=True,
                     mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                     download_name=f"kodierleitfaden-{survey.code}-{project.id}.docx")

