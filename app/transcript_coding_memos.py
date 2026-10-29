"""Interviews kodieren: memos (Kuckartz & Rädiker, 2022; Ralf 2026-10-29).

A memo is a note of whoever works in an analysis, at one of three places (CodingMemo):

- at a coded passage ("passage"): what struck one about this passage, a doubt about the category;
- at a category ("category"): thoughts on its definition and borders while the category system grows;
- at an interview ("interview"): a case summary in one's own words, the context of the conversation.

Who sees and writes them is who codes (app/transcript_coding.py): the team all, members and interviewer links with
the right to code at the interviews they see; categories are seen by everybody of the analysis. In double coding an
interview is blind until everyone is done (app/transcript_coding_double.py): memos of others at it and its passages
stay hidden like their passages. The author and the team change or delete a memo; a deleted one stays for a day
(deleted_at) so that "Rückgängig" brings it back, then it goes.

The memos go with the analysis, the interview (erasure, retention), the coded passage and the account of their
author. They are in the .qdpx (REFI-QDA Note with NoteRef at the code, the text source or the selection) and on a
sheet of their own in the Excel file of the category system (app/transcript_coding_export.py). The overview of an
analysis lists them, filtered and paged (memo_list)."""
from datetime import timedelta

from flask import abort, flash, jsonify, redirect, request, url_for

from app.extensions import db
from app.lang import de_en, english
from app.models import CodingCategory, CodingMemo, Response, utcnow
from app.timeutil import fmt
from app.transcript_coding import _in, bp, interviews, signed_in_or_coder, tree
from app.transcript_coding_view import codings, paragraphs, placed, spot, transcript

TEXT_MAX = 4000
KEEP_DELETED = timedelta(days=1)
KINDS = (CodingMemo.PASSAGE, CodingMemo.CATEGORY, CodingMemo.INTERVIEW)
PER_PAGE = (10, 20, 50)


def kind_name(kind):
    return {CodingMemo.PASSAGE: de_en("Stelle", "Passage"), CodingMemo.CATEGORY: de_en("Kategorie", "Category"),
            CodingMemo.INTERVIEW: de_en("Interview", "Interview")}.get(kind, kind)


def purge():
    """Memos deleted more than a day ago go for good."""
    db.session.execute(db.delete(CodingMemo).where(CodingMemo.deleted_at.is_not(None),
                                                   CodingMemo.deleted_at < utcnow() - KEEP_DELETED))


def may_change(who, memo):
    return who.team or (memo.author_key and memo.author_key == who.key)


def _rows(project, who):
    return {r.id: r for r in interviews(project, who)}


def _blind(project, who, rid):
    from app.transcript_coding_double import blind_for

    return blind_for(project, who, rid)


def visible(project, who, rows=None, kind=None, rid=None, include_deleted=False):
    """The memos of the analysis `who` may see now, oldest first."""
    from app.transcript_coding_double import visible as visible_codings

    rows = _rows(project, who) if rows is None else rows
    query = db.select(CodingMemo).where(CodingMemo.project_id == project.id)
    if not include_deleted:
        query = query.where(CodingMemo.deleted_at.is_(None))
    if kind:
        query = query.where(CodingMemo.kind == kind)
    if rid is not None:
        query = query.where(CodingMemo.response_id == rid)
    seen = {}  # response id: ids of the codings the actor sees there
    out = []
    for memo in db.session.scalars(query.order_by(CodingMemo.created_at, CodingMemo.id)):
        if memo.kind == CodingMemo.CATEGORY:
            out.append(memo)
            continue
        row = rows.get(memo.response_id)
        if row is None:
            continue  # an interview this actor does not see
        if memo.author_key != who.key and _blind(project, who, row.id):
            continue  # double coding: blind until everyone is done
        if memo.kind == CodingMemo.PASSAGE:
            if row.id not in seen:
                seen[row.id] = {c.get("id") for c in visible_codings(project, who, row, codings(row, project.id))}
            if memo.coding_id not in seen[row.id]:
                continue
        out.append(memo)
    return out


def counts(memos):
    """{"passage:<coding id>" | "category:<id>" | "interview:<response id>": number of memos}."""
    out = {}
    for m in memos:
        key = f"{m.kind}:{m.coding_id if m.kind == CodingMemo.PASSAGE else m.category_id if m.kind == CodingMemo.CATEGORY else m.response_id}"
        out[key] = out.get(key, 0) + 1
    return out


def as_json(memo, who):
    return {"id": memo.id, "kind": memo.kind, "rid": memo.response_id, "coding": memo.coding_id, "cat": memo.category_id,
            "text": memo.text, "by": memo.author_name, "at": fmt(memo.updated_at, lang="en" if english() else "de"),
            "edited": memo.updated_at > memo.created_at, "mine": bool(may_change(who, memo))}


def _clean(text):
    text = str(text or "").replace("\r\n", "\n").strip()
    return text[:TEXT_MAX]


def create(project, who, kind, text, rid=None, coding_id=None, cat_id=None):
    """A new memo; returns (memo, problem)."""
    text = _clean(text)
    if not text:
        return None, de_en("Bitte einen Text eingeben.", "Please enter a text.")
    if kind not in KINDS:
        abort(400)
    memo = CodingMemo(project_id=project.id, kind=kind, text=text, author_key=who.key, author_id=who.user_id,
                      author_name=(who.name or "")[:200])
    if kind == CodingMemo.CATEGORY:
        cat = db.session.get(CodingCategory, cat_id) if isinstance(cat_id, int) else None
        if cat is None or cat.project_id != project.id or cat.removed_at is not None:
            abort(404)
        memo.category_id = cat.id
    else:
        rows = _rows(project, who)
        row = rows.get(rid) if isinstance(rid, int) else None
        if row is None:
            abort(404)
        memo.response_id = row.id
        if kind == CodingMemo.PASSAGE:
            from app.transcript_coding_double import visible as visible_codings

            found = [c for c in visible_codings(project, who, row, codings(row, project.id)) if c.get("id") == coding_id]
            if not found:
                abort(404)
            memo.coding_id = str(coding_id)[:24]
    db.session.add(memo)
    db.session.commit()
    return memo, None


def forget_coding(project, row, coding_id):
    """A coded passage removed: its memos go with it."""
    db.session.execute(db.delete(CodingMemo).where(CodingMemo.project_id == project.id, CodingMemo.response_id == row.id,
                                                   CodingMemo.coding_id == str(coding_id)))


def _memo(project, who, mid, include_deleted=False):
    memo = db.session.get(CodingMemo, mid)
    if memo is None or memo.project_id != project.id:
        abort(404)
    if memo.id not in {m.id for m in visible(project, who, include_deleted=include_deleted)}:
        abort(404)
    if not may_change(who, memo):
        abort(403)
    return memo


# --- what a memo is about, for lists and exports -----------------------------------------------------------------


def targets(project, memos):
    """{memo id: {"category", "number", "interview", "where", "quote", "rid"}} for lists and exports; the place of a
    passage as in the Excel export ("B03, Pos. 12, 00:14:32")."""
    from app.transcript_coding import numbers, resolver

    mains, candidates = tree(project)
    number = numbers(mains)
    names = {c.id: getattr(c, "path", c.name) for m, subs in mains for c in [m] + subs}
    names.update({c.id: c.name for c in candidates})
    resolve = resolver(project)
    rows = {}
    out = {}
    for memo in memos:
        entry = {"category": "", "number": "", "interview": "", "where": "", "quote": "", "rid": memo.response_id}
        if memo.kind == CodingMemo.CATEGORY:
            cid = resolve(memo.category_id) or memo.category_id
            entry.update(category=names.get(cid, ""), number=number.get(cid, ""), cat=cid)
        else:
            if memo.response_id not in rows:
                rows[memo.response_id] = db.session.get(Response, memo.response_id)
            row = rows[memo.response_id]
            label = (row.interview or {}).get("label", str(row.id)) if row is not None else ""
            entry["interview"] = label
            if memo.kind == CodingMemo.PASSAGE and row is not None:
                c = next((c for c in placed(row, project)[0] if c.get("id") == memo.coding_id), None)
                if c is not None:
                    text = transcript(row)
                    entry.update(where=spot(label, text, c["start"], paragraphs(text)), quote=" ".join(c.get("quote", "").split()),
                                 category=names.get(c["cat"], ""), number=number.get(c["cat"], ""), cat=c["cat"])
            elif row is not None:
                entry["where"] = label
        out[memo.id] = entry
    return out


def memo_list(project, who, args):
    """The memos for the overview: filtered by kind, category and interview, sorted and paged (10, 20 or 50)."""
    memos = visible(project, who)
    about = targets(project, memos)
    kind = args.get("mk") if args.get("mk") in KINDS else ""
    try:
        cat = int(args.get("mc") or 0)
        rid = int(args.get("mi") or 0)
        page = max(1, int(args.get("mp") or 1))
        per = int(args.get("mn") or 20)
    except ValueError:
        cat = rid = 0
        page, per = 1, 20
    per = per if per in PER_PAGE else 20
    sort = args.get("ms") if args.get("ms") in ("new", "old", "kind", "author") else "new"
    rows = [m for m in memos if (not kind or m.kind == kind) and (not cat or about[m.id].get("cat") == cat)
            and (not rid or m.response_id == rid)]
    if sort == "new":
        rows.sort(key=lambda m: (m.updated_at, m.id), reverse=True)
    elif sort == "old":
        rows.sort(key=lambda m: (m.created_at, m.id))
    elif sort == "kind":
        rows.sort(key=lambda m: (KINDS.index(m.kind), about[m.id]["number"], about[m.id]["interview"], m.id))
    else:
        rows.sort(key=lambda m: (m.author_name.casefold(), m.id))
    pages = max(1, -(-len(rows) // per))
    page = min(page, pages)
    shown = rows[(page - 1) * per:page * per]
    return {"rows": [(m, about[m.id], may_change(who, m)) for m in shown], "total": len(rows), "all": len(memos),
            "page": page, "pages": pages, "per": per, "sort": sort, "kind": kind, "cat": cat, "rid": rid,
            "interviews": sorted({(m.response_id, about[m.id]["interview"]) for m in memos if m.response_id},
                                 key=lambda r: r[1].casefold()),
            "categories": [(c.id, getattr(c, "path", c.name)) for m, subs in tree(project)[0] for c in [m] + subs]}


# --- routes --------------------------------------------------------------------------------------------------------


def _payload():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else request.form


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _answer(ok, survey, project, memo=None, who=None, error="", status=200):
    if request.is_json or request.headers.get("X-Requested-With") == "fetch":
        if not ok:
            return jsonify(ok=False, error=error), status
        return jsonify(ok=True, memo=as_json(memo, who) if memo is not None else None)
    if not ok:
        flash(error, "error")
    back = request.form.get("back") or url_for("transcript_coding.overview", code=survey.code, pid=project.id)
    if not back.startswith("/"):
        back = url_for("transcript_coding.overview", code=survey.code, pid=project.id)
    return redirect(back)


@bp.route("/<code>/interviews/coding/<int:pid>/memos", methods=["POST"])
@signed_in_or_coder
def add_memo(code, pid):
    survey, project, who = _in(code, pid)
    data = _payload()
    purge()
    memo, problem = create(project, who, str(data.get("kind") or ""), data.get("text"), rid=_int(data.get("rid")),
                           coding_id=data.get("coding") or None, cat_id=_int(data.get("cat")))
    if problem:
        return _answer(False, survey, project, error=problem, status=400)
    return _answer(True, survey, project, memo, who)


@bp.route("/<code>/interviews/coding/<int:pid>/memos/<int:mid>", methods=["POST"])
@signed_in_or_coder
def edit_memo(code, pid, mid):
    survey, project, who = _in(code, pid)
    memo = _memo(project, who, mid)
    text = _clean(_payload().get("text"))
    if not text:
        return _answer(False, survey, project, error=de_en("Bitte einen Text eingeben.", "Please enter a text."), status=400)
    if text != memo.text:
        memo.text, memo.updated_at = text, utcnow()
        db.session.commit()
    return _answer(True, survey, project, memo, who)


@bp.route("/<code>/interviews/coding/<int:pid>/memos/<int:mid>/delete", methods=["POST"])
@signed_in_or_coder
def delete_memo(code, pid, mid):
    survey, project, who = _in(code, pid)
    memo = _memo(project, who, mid)
    memo.deleted_at = utcnow()
    purge()
    db.session.commit()
    return _answer(True, survey, project, memo, who)


@bp.route("/<code>/interviews/coding/<int:pid>/memos/<int:mid>/restore", methods=["POST"])
@signed_in_or_coder
def restore_memo(code, pid, mid):
    """"Rückgängig" after deleting: the memo comes back while its day has not passed."""
    survey, project, who = _in(code, pid)
    memo = _memo(project, who, mid, include_deleted=True)
    memo.deleted_at = None
    db.session.commit()
    return _answer(True, survey, project, memo, who)
