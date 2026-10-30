"""Interviews kodieren: the pages of an analysis bring themselves up to date (Ralf 2026-10-30, "alle 5 Sekunden").

The category system (list and diagram), the overview and the coding view ask `changes` every few seconds
(transcript_coding_live.js, while the window is visible) whether what they show has changed since they were drawn.
The answer is a version of what this actor sees: a hash over the categories, the history, the memos and the codings
the actor may see (blind double coding stays blind: someone else's passages in a blind interview do not change it).
Nothing changed: 204 without a body. Otherwise the new version; the coding view gets its data with it, the other
pages fetch themselves anew and swap the parts marked data-tc-live in place.

Working the version out reads every interview of the analysis, so it is kept per process for a short while under a
cheap stamp (the analysis' updated_at, which storing a coding also sets, the last event, the categories, memos and
members). Anything the stamp does not see, e.g. a corrected transcript, shows after VERSION_TTL at the latest."""
import hashlib
import json
import time

from flask import jsonify, make_response, request

from app.extensions import db
from app.models import CodingCategory, CodingEvent, CodingMember, CodingMemo
from app.transcript_coding import _in, bp, interviews, live, signed_in_or_coder

POLL_SECONDS = 5  # how often the pages ask (handed to the script, data-tc-every)
VERSION_TTL = 60  # seconds a version is kept under the same stamp
_seen = {}  # (project id, created_at, actor key, row id, stamp) -> (version, monotonic time)
_SEEN_MAX = 2000


def _stamp(project):
    """What changes cheaply with nearly every change of the analysis (one query per table)."""
    events = db.session.scalar(db.select(db.func.max(CodingEvent.id)).where(CodingEvent.project_id == project.id))
    cats = db.session.execute(db.select(db.func.count(), db.func.max(CodingCategory.updated_at))
                              .where(CodingCategory.project_id == project.id)).one()
    memos = db.session.execute(db.select(db.func.count(), db.func.max(CodingMemo.updated_at), db.func.max(CodingMemo.deleted_at))
                               .where(CodingMemo.project_id == project.id)).one()
    members = db.session.execute(db.select(db.func.count(), db.func.max(CodingMember.id))
                                 .where(CodingMember.project_id == project.id)).one()
    return str((project.updated_at, events, tuple(cats), tuple(memos), tuple(members)))


def _sha(value):
    return hashlib.sha1(str(value).encode("utf-8")).hexdigest()


def _digest(project, who, row=None):
    """A hash over what `who` sees of the analysis (and of the interview `row` in the coding view)."""
    from app import transcript_coding_ai, transcript_coding_double, transcript_coding_memos
    from app.transcript_coding_view import codings

    rows = interviews(project, who)
    seen = []
    for r in rows:
        meta = r.interview or {}
        text = (meta.get("transcript") or {}).get("text") or ""
        items = transcript_coding_double.visible(project, who, r, codings(r, project.id))
        seen.append([r.id, meta.get("label"), bool((meta.get("transcript") or {}).get("checked")), _sha(text),
                     [[c.get(k) for k in ("id", "cat", "start", "end", "by", "accepted_by")] for c in items],
                     transcript_coding_double.assignment(project, r.id)])
    memos = [[m.id, str(m.updated_at), _sha(m.text)]
             for m in transcript_coding_memos.visible(project, who, rows={r.id: r for r in rows})]
    cats = [[c.id, c.parent_id, c.kind, c.position, c.name, c.definition, c.delimitation, c.colour, c.anchor, c.examples,
             c.former_names, str(c.updated_at)] for c in live(project)]
    events = db.session.scalar(db.select(db.func.max(CodingEvent.id)).where(CodingEvent.project_id == project.id))
    members = db.session.execute(db.select(db.func.count(), db.func.max(CodingMember.id))
                                 .where(CodingMember.project_id == project.id)).one()
    state = [project.name, project.question, project.visibility, project.mains_fixed, project.ai_allowed, project.settings,
             cats, events, list(members), memos, seen]
    if row is not None:
        state.append([[s.get(k) for k in ("id", "cat", "start", "end", "accepted", "discarded")]
                      for s in transcript_coding_ai.suggestions(row, project.id)])
    return _sha(json.dumps(state, default=str, sort_keys=True))[:16]


def version(project, who, row=None):
    """The version of what `who` sees now (see the module's text)."""
    key = (project.id, str(project.created_at), who.key, getattr(row, "id", None), _stamp(project))
    now = time.monotonic()
    hit = _seen.get(key)
    if hit is not None and now - hit[1] < VERSION_TTL:
        return hit[0]
    found = _digest(project, who, row)
    if len(_seen) >= _SEEN_MAX:
        _seen.clear()
    _seen[key] = (found, now)
    return found


@bp.app_template_global("tc_live_marker")
def marker(survey, project, who, row=None):
    """The data the pages hand transcript_coding_live.js: where to ask, how often, and the version drawn."""
    from flask import url_for
    from markupsafe import Markup, escape

    url = url_for("transcript_coding.changes", code=survey.code, pid=project.id, **({"rid": row.id} if row is not None else {}))
    return Markup(f'<div hidden data-tc-poll="{escape(url)}" data-tc-every="{POLL_SECONDS}" '
                  f'data-tc-version="{escape(version(project, who, row))}"></div>')


@bp.route("/<code>/interviews/coding/<int:pid>/changes")
@signed_in_or_coder
def changes(code, pid):
    """`v` the version the page shows; 204 when it is still the same, else {"v": new} (with `rid`, the coding view:
    and "data", what the page draws). The same checks as the pages: the analysis, the interview, blind coding."""
    from app.transcript_coding_view import _row, page_data

    survey, project, who = _in(code, pid)
    rid = request.args.get("rid", type=int)
    row = _row(project, who, rid) if rid is not None else None
    now = version(project, who, row)
    if request.args.get("v") == now:
        response = make_response("", 204)
    else:
        response = jsonify(v=now, **({"data": page_data(project, who, row)} if row is not None else {}))
    response.headers["Cache-Control"] = "no-store"
    return response
