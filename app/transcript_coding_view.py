"""Interviews kodieren, stage 2: the coding view of one interview in one analysis (app/transcript_coding.py; concept
"Interviews kodieren", all as recommended, Ralf 2026-10-27).

Whoever works in an analysis (team, member, coding link, interviewer with the tick) and sees an interview there opens
its transcript, marks a passage and assigns it a category: from a list that filters while typing, with the keys 1 to 9
for the categories used last, or as a new subcategory made on the spot. A marked passage is widened to whole words.

A coding is part of the interview (Response.interview["codings"]), so it goes with a deletion request, the period of
the guide and the guide itself:

    {"id", "project", "cat", "start", "end", "quote", "by" (actor key), "by_name", "at", "origin": "manual"}

Re-finding after a correction of the transcript (`place`): `start`/`end` hold while the text there is still `quote`;
otherwise the same wording is looked for (the occurrence nearest the old place, then the same words with other spaces
and line breaks between them). A passage whose wording is gone is "nicht mehr zuzuordnen": it shows in a list of its
own, nothing is moved by guesswork, and the person who coded it (or the team) removes it or marks it anew.

The anchor example of a category is one of its coded passages (CodingCategory.anchor = {"interview", "coding",
"quote"}), chosen by whoever may change the category. It goes when its interview or its coding goes (`forget`)."""
import re
import secrets

from flask import abort, jsonify, render_template, request, url_for

from app.extensions import db
from app.lang import de_en
from app.models import CodingCategory, Response, utcnow
from app.transcript_coding import (_in, bp, create, has_transcript, interviews, live, may_edit, signed_in_or_coder,
                                   similar, tree)

MAX_PER_INTERVIEW = 3000  # codings of one interview over all analyses
QUOTE_MAX = 2000
ORIGIN = "manual"


# --- the passages --------------------------------------------------------------------------------------------------


def _word(ch):
    return ch.isalnum() or ch in "-'’_"


def snap(text, start, end):
    """The passage widened to whole words and trimmed of spaces at its ends: (start, end) or None if nothing is left."""
    n = len(text)
    start, end = max(0, min(int(start), n)), max(0, min(int(end), n))
    if end < start:
        start, end = end, start
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    if start >= end:
        return None
    while start > 0 and _word(text[start - 1]) and _word(text[start]):
        start -= 1
    while end < n and _word(text[end]) and _word(text[end - 1]):
        end += 1
    return start, end


def _loose(quote):
    """The words of `quote` with any whitespace between them, as a pattern."""
    words = quote.split()
    return re.compile(r"\s+".join(re.escape(w) for w in words)) if words else None


def place(text, coding):
    """(start, end) of the coding in the current transcript, or None when its wording is gone."""
    quote = coding.get("quote") or ""
    start, end = coding.get("start"), coding.get("end")
    if not quote:
        return None
    if isinstance(start, int) and isinstance(end, int) and text[start:end] == quote:
        return start, end
    old = start if isinstance(start, int) else 0
    found = [m.start() for m in re.finditer(re.escape(quote), text)]
    if found:
        best = min(found, key=lambda s: abs(s - old))
        return best, best + len(quote)
    pattern = _loose(quote)
    if pattern is not None:
        matches = list(pattern.finditer(text))
        if matches:
            best = min(matches, key=lambda m: abs(m.start() - old))
            return best.start(), best.end()
    return None


def transcript(row):
    return ((row.interview or {}).get("transcript") or {}).get("text") or ""


def codings(row, project_id=None):
    out = [c for c in ((row.interview or {}).get("codings") or []) if isinstance(c, dict)]
    return [c for c in out if project_id is None or c.get("project") == project_id]


def placed(row, project):
    """The codings of `project` in this interview as the page shows them: ({...coding, "start", "end"} placed,
    [codings no longer to be placed])."""
    from app.transcript_coding import resolver

    text = transcript(row)
    ok, lost = [], []
    resolve = resolver(project)
    for c in codings(row, project.id):
        cat = resolve(c.get("cat"))
        if cat is None:
            continue  # a category removed
        c = {**c, "cat": cat, "orig": c.get("cat")}  # merged ones count for the category they were merged into
        where = place(text, c)
        if where is None:
            lost.append(c)
        else:
            ok.append({**c, "start": where[0], "end": where[1]})
    return sorted(ok, key=lambda c: (c["start"], c["end"])), lost


def may_change(project, who, coding):
    return who.team or coding.get("by") == who.key


def _row(project, who, rid):
    row = db.session.get(Response, rid)
    if row is None or row.survey_id != project.survey_id or row.interview is None:
        abort(404)
    if row.id not in {r.id for r in interviews(project, who)} or not has_transcript(row):
        abort(404)
    return row


def _store(row, change):
    """change(list of codings) -> new list, stored with a retry when the interview was saved meanwhile."""
    from app import interview_audio

    def work():
        db.session.refresh(row)
        meta = dict(row.interview or {})
        meta["codings"] = change(list(meta.get("codings") or []))
        row.interview = meta
        return meta["codings"]

    return interview_audio.retried(work)


def add(project, who, row, start, end, cat_id):
    """Code a passage; returns (coding, problem)."""
    cat = db.session.get(CodingCategory, cat_id) if isinstance(cat_id, int) else None
    if cat is None or cat.project_id != project.id or cat.removed_at is not None or cat.merged_into is not None:
        return None, de_en("Bitte eine Kategorie wählen.", "Please choose a category.")
    text = transcript(row)
    try:
        span = snap(text, start, end)
    except (TypeError, ValueError):
        span = None
    if span is None:
        return None, de_en("Bitte eine Textstelle markieren.", "Please mark a passage.")
    quote = text[span[0]:span[1]]
    if len(quote) > QUOTE_MAX:
        return None, de_en("Die Stelle ist zu lang (höchstens {n} Zeichen).", "The passage is too long (at most {n} characters).", n=QUOTE_MAX)
    coding = {"id": secrets.token_urlsafe(8), "project": project.id, "cat": cat.id, "start": span[0], "end": span[1],
              "quote": quote, "by": who.key, "by_name": who.name[:200], "at": utcnow().isoformat(timespec="seconds"),
              "origin": ORIGIN}
    problem = []

    def change(items):
        if any(c.get("project") == project.id and c.get("cat") == cat.id and c.get("start") == span[0] and c.get("end") == span[1]
               for c in items):
            problem.append(de_en("Diese Stelle hat diese Kategorie schon.", "This passage already has this category."))
            return items
        if len(items) >= MAX_PER_INTERVIEW:
            problem.append(de_en("Höchstens {n} kodierte Stellen je Interview.", "At most {n} coded passages per interview.", n=MAX_PER_INTERVIEW))
            return items
        return items + [coding]

    _store(row, change)
    return (None, problem[0]) if problem else (coding, None)


def remove(project, who, row, coding_id):
    """Remove a coding: its author or the team. The category's anchor goes with it."""
    found = next((c for c in codings(row, project.id) if c.get("id") == coding_id), None)
    if found is None:
        abort(404)
    if not may_change(project, who, found):
        abort(403)
    _store(row, lambda items: [c for c in items if c.get("id") != coding_id])
    for cat in live(project):
        if (cat.anchor or {}).get("coding") == coding_id:
            cat.anchor = None
    db.session.commit()


def set_anchor(project, who, row, coding_id):
    """Make a coded passage the anchor example of its category (whoever may change the category)."""
    found = next((c for c in codings(row, project.id) if c.get("id") == coding_id), None)
    if found is None:
        abort(404)
    from app.transcript_coding import resolver

    cat = db.session.get(CodingCategory, resolver(project)(found.get("cat")) or 0)
    if cat is None or cat.project_id != project.id:
        abort(404)
    if not may_edit(project, cat, who):
        return de_en("Das Ankerbeispiel wählt, wer die Kategorie ändern darf.", "The anchor example is chosen by whoever may change the category.")
    cat.anchor = {"interview": row.id, "coding": coding_id, "quote": found.get("quote", "")[:QUOTE_MAX],
                  "label": (row.interview or {}).get("label", "")}
    cat.updated_at = utcnow()
    db.session.commit()
    return None


def counts(project, who):
    """{category id: coded passages} over the interviews the actor sees."""
    from app.transcript_coding import resolver

    out, resolve = {}, resolver(project)
    for row in interviews(project, who):
        for c in codings(row, project.id):
            cat = resolve(c.get("cat"))
            out[cat] = out.get(cat, 0) + 1
    return out


def forget(responses):
    """Before interviews are deleted (erasure, retention): anchor examples quoting them go."""
    ids = {r.id for r in responses}
    survey_ids = {r.survey_id for r in responses}
    if not ids:
        return
    from app.models import CodingProject

    projects = list(db.session.scalars(db.select(CodingProject.id).where(CodingProject.survey_id.in_(survey_ids))))
    if not projects:
        return
    for cat in db.session.scalars(db.select(CodingCategory).where(CodingCategory.project_id.in_(projects),
                                                                   CodingCategory.anchor.is_not(None))):
        if (cat.anchor or {}).get("interview") in ids:
            cat.anchor = None


def forget_survey_anchors(survey_id):
    """All anchor examples of a guide's analyses (retention.purge deletes every interview at once)."""
    from app.models import CodingProject

    projects = list(db.session.scalars(db.select(CodingProject.id).filter_by(survey_id=survey_id)))
    if projects:
        db.session.execute(db.update(CodingCategory).where(CodingCategory.project_id.in_(projects))
                           .values(anchor=None).execution_options(synchronize_session=False))


# --- the pages -----------------------------------------------------------------------------------------------------


def _categories(project, who):
    """The categories for the page's list: main categories with their subcategories, then candidates."""
    mains, candidates = tree(project)
    n = counts(project, who)
    out = []
    for main, subs in mains:
        out.append({"id": main.id, "name": main.name, "kind": "main", "parent": "", "colour": main.colour or "",
                    "n": n.get(main.id, 0), "definition": main.definition[:300]})
        for s in subs:
            out.append({"id": s.id, "name": s.name, "kind": "sub", "parent": main.name, "parent_id": main.id,
                        "colour": s.colour or main.colour or "", "n": n.get(s.id, 0), "definition": s.definition[:300]})
    for c in candidates:
        out.append({"id": c.id, "name": c.name, "kind": "candidate", "parent": "", "colour": c.colour or "",
                    "n": n.get(c.id, 0), "definition": c.definition[:300]})
    return out


def _json_coding(c, project, who):
    return {**{k: c.get(k) for k in ("id", "cat", "start", "end", "quote", "by_name", "at", "origin", "accepted_by")},
            "mine": may_change(project, who, c)}


@bp.route("/<code>/interviews/coding/<int:pid>/i/<int:rid>")
@signed_in_or_coder
def page(code, pid, rid):
    from app import transcript_coding_ai, transcript_coding_review

    survey, project, who = _in(code, pid)
    row = _row(project, who, rid)
    ok, lost = placed(row, project)
    rows = [r for r in interviews(project, who) if has_transcript(r)]
    ids = [r.id for r in rows]
    at = ids.index(row.id) if row.id in ids else -1
    nxt = rows[at + 1] if 0 <= at < len(rows) - 1 else None
    prev = rows[at - 1] if at > 0 else None
    cats = _categories(project, who)
    names = {c["id"]: c["name"] for c in cats}
    return render_template("transcript_coding/code.html", survey=survey, project=project, who=who, row=row,
                           text=transcript(row), placed=[_json_coding(c, project, who) for c in ok],
                           lost=[{**_json_coding(c, project, who), "name": names.get(c.get("cat"), "")} for c in lost],
                           categories=cats, nxt=nxt, prev=prev, anchors={c.id: (c.anchor or {}).get("coding") for c in live(project)},
                           editable={c.id: may_edit(project, c, who) for c in live(project)},
                           may_main=who.team or not project.mains_fixed,
                           notices=transcript_coding_review.notices(project, who),
                           ai_state=transcript_coding_ai.state(project, who),
                           suggestions=transcript_coding_ai.open_suggestions(row, project),
                           sections=len(transcript_coding_ai.sections(transcript(row))))


def _payload():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else request.form


@bp.route("/<code>/interviews/coding/<int:pid>/i/<int:rid>/codings", methods=["POST"])
@signed_in_or_coder
def code_passage(code, pid, rid):
    survey, project, who = _in(code, pid)
    row = _row(project, who, rid)
    data = _payload()
    try:
        start, end, cat = int(data.get("start")), int(data.get("end")), int(data.get("cat"))
    except (TypeError, ValueError):
        return jsonify(ok=False, error=de_en("Bitte eine Textstelle markieren.", "Please mark a passage.")), 400
    coding, problem = add(project, who, row, start, end, cat)
    if problem:
        return jsonify(ok=False, error=problem), 400
    return jsonify(ok=True, coding=_json_coding(coding, project, who))


@bp.route("/<code>/interviews/coding/<int:pid>/i/<int:rid>/codings/<cid>/delete", methods=["POST"])
@signed_in_or_coder
def uncode(code, pid, rid, cid):
    survey, project, who = _in(code, pid)
    row = _row(project, who, rid)
    remove(project, who, row, cid)
    return jsonify(ok=True)


@bp.route("/<code>/interviews/coding/<int:pid>/i/<int:rid>/codings/<cid>/anchor", methods=["POST"])
@signed_in_or_coder
def anchor(code, pid, rid, cid):
    survey, project, who = _in(code, pid)
    row = _row(project, who, rid)
    problem = set_anchor(project, who, row, cid)
    if problem:
        return jsonify(ok=False, error=problem), 403
    return jsonify(ok=True)


@bp.route("/<code>/interviews/coding/<int:pid>/categories/quick", methods=["POST"])
@signed_in_or_coder
def quick_category(code, pid):
    """A new category from the coding view (JSON): the similar ones first, as in the dialog of the analysis."""
    survey, project, who = _in(code, pid)
    data = _payload()
    name = str(data.get("name") or "")
    alike = similar(project, name)
    if alike and not data.get("anyway"):
        return jsonify(ok=False, similar=[{"id": c.id, "name": c.name} for c, _s in alike],
                       error=de_en("Es gibt schon ähnliche Kategorien.", "There are similar categories already."))
    try:
        parent = int(data.get("parent")) if data.get("parent") not in (None, "") else None
    except (TypeError, ValueError):
        parent = None
    cat, problem = create(project, who, str(data.get("kind") or CodingCategory.SUB), name, str(data.get("definition") or ""),
                          str(data.get("delimitation") or ""), parent)
    if problem:
        db.session.rollback()
        return jsonify(ok=False, error=problem), 400
    db.session.commit()
    parent_cat = db.session.get(CodingCategory, cat.parent_id) if cat.parent_id else None
    return jsonify(ok=True, category={"id": cat.id, "name": cat.name, "kind": cat.kind,
                                      "parent": parent_cat.name if parent_cat else "", "parent_id": cat.parent_id,
                                      "colour": cat.colour or "", "n": 0, "definition": cat.definition[:300]})


def interview_url(survey, project, row):
    return url_for("transcript_coding.page", code=survey.code, pid=project.id, rid=row.id)
