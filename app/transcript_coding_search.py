"""Interviews kodieren: searching the material (Ralf 2026-10-29).

A search field in the view "Interviews" of an analysis looks for words in all transcripts the actor sees there
(app/transcript_coding.py, interviews): upper and lower case alike, on request whole words only, several terms with
ODER (also "OR", "|" or a comma); a term of several words matches with any spaces and line breaks between them. Each
hit shows its interview, its place ("AN-01, Pos. 12, 00:14:32", app/transcript_coding_view.py, spot) and the sentence
it stands in with one sentence before and after, the hit marked; 20 per page.

From a hit one jumps to its place in the coding view (the passage is marked there, the list of categories opens) or
ticks several and codes them at once with "Treffer kodieren als …": a category, the sentence or the paragraph around
each hit as the passage, a preview before anything is stored, and "Rückgängig" afterwards. Coding goes through
transcript_coding_view.add, so rights, the widening to whole words and the limits are the same as when marking by
hand; a hit already coded with that category by the same person is left out.

A lexical search finds words, not meanings: every hit is to be read before it is coded (Kuckartz & Rädiker, 2022),
which the fold "Warum Treffer einzeln prüfen?" says."""
import re

from flask import abort, flash, redirect, render_template, request, url_for

from app.extensions import db
from app.lang import de_en
from app.models import CodingCategory, Response
from app.transcript_coding import _in, bp, has_transcript, interviews, live, signed_in_or_coder, tree
from app.transcript_coding_view import add, codings, paragraphs, remove, snap, spot, transcript

PER_PAGE = 20
MAX_HITS = 2000
MAX_CODE = 200  # hits coded in one step
_SPLIT = re.compile(r"\s+(?:ODER|OR)\s+|\s*[|,;]\s*")
_END = re.compile(r"[.!?…](?=\s|$)")
_MARK = re.compile(r"\s*#\d{1,2}:\d{2}:\d{2}(?:-\d+)?#\s*$")
_SPEAKER = re.compile(r"[A-ZÄÖÜ][\w-]{0,15}:\s+")  # "B: ", "I: ", "Anna: " at the start of a turn


def _squash(part):
    """Runs of spaces and line breaks as one space, as they stand (a space before or after the hit stays)."""
    return re.sub(r"\s+", " ", part)


def terms(q):
    """The terms of a query: separated by ODER, OR, |, comma or semicolon; quotes around a term are dropped."""
    out = []
    for part in _SPLIT.split(str(q or "")[:500]):
        part = part.strip().strip("\"„“”'").strip()
        if part and part.casefold() not in {t.casefold() for t in out}:
            out.append(part)
    return out[:20]


def pattern(found_terms, words=False):
    if not found_terms:
        return None
    alt = "|".join(r"\s+".join(re.escape(w) for w in t.split()) for t in sorted(found_terms, key=len, reverse=True))
    if words:
        alt = rf"(?<!\w)(?:{alt})(?!\w)"
    return re.compile(alt, re.IGNORECASE)


def _paragraph(paras, start, n):
    for p in paras:
        if p[0] <= start <= p[1]:
            return p
    return [0, n]


def sentence(text, start, end, paras=None):
    """(start, end) of the sentence the place stands in, within its paragraph."""
    p0, p1 = _paragraph(paras if paras is not None else paragraphs(text), start, len(text))
    s0 = p0
    for m in _END.finditer(text, p0, start):
        s0 = m.end()
    m = _END.search(text, end, p1)
    s1 = m.end() if m else p1
    while s0 < s1 and text[s0].isspace():
        s0 += 1
    return s0, s1


def around(text, start, end, paras=None):
    """(start, end) of the context: the sentence with one before and one after, within the paragraph."""
    paras = paras if paras is not None else paragraphs(text)
    p0, p1 = _paragraph(paras, start, len(text))
    s0, s1 = sentence(text, start, end, paras)
    if s0 > p0:
        s0 = sentence(text, max(p0, s0 - 2), max(p0, s0 - 2), paras)[0]
    if s1 < p1:
        s1 = sentence(text, min(p1, s1 + 1), min(p1, s1 + 1), paras)[1]
    return s0, s1


def unit(text, start, end, kind, paras=None):
    """The passage to code around a hit: its sentence or its paragraph, without a trailing time mark and without the
    speaker's label at its start."""
    paras = paras if paras is not None else paragraphs(text)
    s0, s1 = sentence(text, start, end, paras) if kind == "sentence" else tuple(_paragraph(paras, start, len(text)))
    m = _MARK.search(text, s0, s1)
    if m and m.end() == s1:
        s1 = m.start()
    m = _SPEAKER.match(text, s0, s1)
    if m and m.end() <= start:  # the speaker's label is no part of what they said
        s0 = m.end()
    return s0, max(s1, end)


def hits(project, who, q, words=False):
    """[{rid, label, start, end, where, before, match, after}] in the order of the interviews and the text."""
    rx = pattern(terms(q), words)
    out = []
    if rx is None:
        return out
    for row in interviews(project, who):
        if not has_transcript(row):
            continue
        text, label = transcript(row), (row.interview or {}).get("label", str(row.id))
        paras = paragraphs(text)
        for m in rx.finditer(text):
            c0, c1 = around(text, m.start(), m.end(), paras)
            out.append({"rid": row.id, "label": label, "start": m.start(), "end": m.end(),
                        "where": spot(label, text, m.start(), paras), "before": _squash(text[c0:m.start()]),
                        "match": text[m.start():m.end()], "after": _squash(text[m.end():c1]),
                        "key": f"{row.id}:{m.start()}:{m.end()}"})
            if len(out) >= MAX_HITS:
                return out
    return out


def results(project, who, args):
    """The search of the view "Interviews": query, options, the hits of one page and the categories to code with."""
    q = str(args.get("q") or "").strip()
    words = bool(args.get("words"))
    found = hits(project, who, q, words) if q else []
    try:
        page = max(1, int(args.get("p") or 1))
    except ValueError:
        page = 1
    pages = max(1, -(-len(found) // PER_PAGE))
    page = min(page, pages)
    mains, candidates = tree(project)
    cats = [(c.id, getattr(c, "path", c.name)) for m, subs in mains for c in [m] + subs] + [(c.id, c.name) for c in candidates]
    return {"q": q, "words": words, "terms": terms(q), "total": len(found), "capped": len(found) >= MAX_HITS,
            "hits": found[(page - 1) * PER_PAGE:page * PER_PAGE], "page": page, "pages": pages,
            "interviews": len({h["rid"] for h in found}), "categories": cats}


# --- coding hits at once -------------------------------------------------------------------------------------------


def _chosen(project, who, keys, kind):
    """[(row, start, end, quote, where)] of the ticked hits, as passages of the chosen unit; one per passage."""
    rows = {r.id: r for r in interviews(project, who) if has_transcript(r)}
    out, seen = [], set()
    for key in keys[:MAX_CODE]:
        try:
            rid, start, end = (int(x) for x in str(key).split(":"))
        except ValueError:
            continue
        row = rows.get(rid)
        if row is None:
            continue
        text = transcript(row)
        if not 0 <= start < end <= len(text):
            continue
        paras = paragraphs(text)
        s0, s1 = unit(text, start, end, kind, paras)
        span = snap(text, s0, s1)
        if span is None or (rid, span) in seen:
            continue
        seen.add((rid, span))
        label = (row.interview or {}).get("label", str(row.id))
        out.append((row, span[0], span[1], text[span[0]:span[1]], spot(label, text, span[0], paras)))
    return out


def _back(survey, project, q="", words=False):
    return url_for("transcript_coding.project", code=survey.code, pid=project.id, q=q or None, words="1" if words else None) + "#suche"


@bp.route("/<code>/interviews/coding/<int:pid>/search/code", methods=["POST"])
@signed_in_or_coder
def code_hits(code, pid):
    """"Treffer kodieren als …": first a preview of the passages, then they are stored."""
    survey, project, who = _in(code, pid)
    q, words = request.form.get("q", ""), bool(request.form.get("words"))
    kind = "paragraph" if request.form.get("unit") == "paragraph" else "sentence"
    try:
        cat_id = int(request.form.get("cat") or 0)
    except ValueError:
        cat_id = 0
    cat = db.session.get(CodingCategory, cat_id) if cat_id else None
    if cat is None or cat.project_id != project.id or cat.removed_at is not None or cat.merged_into is not None:
        flash(de_en("Bitte eine Kategorie wählen.", "Please choose a category."), "error")
        return redirect(_back(survey, project, q, words))
    keys = request.form.getlist("hit")
    if not keys:
        flash(de_en("Bitte mindestens einen Treffer ankreuzen.", "Please tick at least one hit."), "error")
        return redirect(_back(survey, project, q, words))
    chosen = _chosen(project, who, keys, kind)
    if request.form.get("action") != "save":
        tree(project)
        return render_template("transcript_coding/search_preview.html", survey=survey, project=project, who=who, q=q,
                               words=words, kind=kind, cat=cat, path=getattr(cat, "path", cat.name), chosen=chosen,
                               keys=keys)
    made, skipped = [], 0
    for row, start, end, _quote, _where in chosen:
        coding, problem = add(project, who, row, start, end, cat.id)
        if coding is None:
            skipped += 1
        else:
            made.append(f"{row.id}:{coding['id']}")
    return render_template("transcript_coding/search_done.html", survey=survey, project=project, who=who, q=q, words=words,
                           cat=cat, made=made, skipped=skipped)


@bp.route("/<code>/interviews/coding/<int:pid>/search/undo", methods=["POST"])
@signed_in_or_coder
def undo_hits(code, pid):
    """"Rückgängig" after coding hits: the passages just made go again (only one's own)."""
    survey, project, who = _in(code, pid)
    rows = {r.id: r for r in interviews(project, who)}
    n = 0
    for key in request.form.getlist("made")[:MAX_CODE]:
        rid, _sep, cid = str(key).partition(":")
        row = rows.get(int(rid)) if rid.isdigit() else None
        if row is None:
            continue
        found = next((c for c in codings(row, project.id) if c.get("id") == cid), None)
        if found is None or found.get("by") != who.key:
            continue
        remove(project, who, row, cid)
        n += 1
    flash(de_en("{n} Stellen wieder entfernt.", "{n} passages removed again.", n=n), "success")
    return redirect(_back(survey, project, request.form.get("q", ""), bool(request.form.get("words"))))
