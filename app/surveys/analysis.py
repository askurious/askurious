"""Pages below the tab Ergebnisse: one page per analysis (restructuring of the results part, stage 1).

A question of the conjoint family (CBC, ACBC, ACA, ratings-based conjoint, MaxDiff, paired comparison) has its whole
analysis on a page of its own: importance and part-worths, the market simulator, the segments, the model quality, the
details and the downloads. The results page keeps a short summary per question and lists the analyses in its section
"Analysen". The numbers are those of the results page (results.compute, the kept results under their fingerprint);
only where they are shown changed.
"""
from flask import abort, current_app, render_template, request
from flask_login import current_user, login_required

from app import heavy
from app.extensions import db
from app.lang import de_en
from app.models import utcnow
from app.surveys.base import analysis_url, bp, data_pool, get_survey
from app.surveys.estimates import _hb_part, _lc_part, _sim_part

# the part of an entry of results.compute per kind of question, and the type of its item
KINDS = ("cbc", "acbc", "aca", "cva", "maxdiff", "paired")
ITEM_TYPES = {"conjoint": "cbc", "acbc": "acbc", "aca": "aca", "cva": "cva", "maxdiff": "maxdiff", "paired": "paired"}
# the arguments of the results page that say what the numbers rest on: they go along to the page of an analysis
POOL_ARGS = ("source", "all", "drop", "markierte", "gewichtet")


def kind_of(entry):
    """"cbc", "acbc", ... for an entry of results.compute with an analysis of the conjoint family, else None."""
    return next((k for k in KINDS if entry and entry.get(k)), None)


def method_name(kind):
    """How the procedure is called in the head of its page and in the list "Analysen" (docs/glossar.md)."""
    return {"maxdiff": "MaxDiff", "paired": de_en("Paarvergleich", "Paired comparison")}.get(kind, "Conjoint")


def pool_args(values=None):
    """The arguments of an address that name the data pool and the view, as lists (for url_for)."""
    values = request.args if values is None else values
    return {k: values.getlist(k) for k in POOL_ARGS if values.get(k)}


def rows(survey, data):
    """The lines of the section "Analysen" of the results page: one per question of the conjoint family, with the
    state of its HB estimation and the number of its segments."""
    out = []
    for entry in (data or {}).get("items") or []:
        kind = kind_of(entry)
        item = entry.get("item")
        if kind is None or item is None:
            continue
        part = entry[kind]
        hb, lc = part.get("hb") or None, part.get("lc") or None
        state = None
        if hb:
            state = "running" if hb.get("state") == "running" else \
                "current" if hb.get("state") == "current" and hb.get("result") else \
                "stale" if hb.get("state") == "stale" else "none"
        segments = (lc.get("result") or {}).get("K") if lc and lc.get("state") == "current" and lc.get("result") else None
        out.append({"item": item, "kind": kind, "method": method_name(kind), "label": item.kind.label,
                    "question": entry.get("question"), "hb": state, "segments": segments,
                    "lc_running": bool(lc and lc.get("state") == "running"),
                    "url": analysis_url(survey, item.id, **pool_args())})
    return out


@bp.route("/<sid:survey_id>/results/analysis/<int:item_id>")
@login_required
def results_analysis(survey_id, item_id):
    """The page of one analysis of the conjoint family, below "Ergebnisse". The data pool and the view are those of
    the results page (the same arguments in the address); the results come from the kept ones when they are current,
    else they are computed and kept as the results page does. The parts the scripts fetch (static/hb.js, segments.js,
    simulator.js: hb_part, lc_part, sim_part) are answered here too, so an estimation touches this page only."""
    from app import progress, results as res, results_store, simulator, weights
    from app.models import Item
    from app.surveys.results import PROGRESS_COOKIE

    survey = get_survey(survey_id)  # access as on the results page
    item = db.session.get(Item, item_id)
    if item is None or item.block.survey_id != survey.id or item.type not in ITEM_TYPES:
        abort(404)
    first = not any(key in request.args for key in ("source", "all", "drop"))  # the survey's saved data pool
    with_marked = request.args.get("markierte") == "1"
    f = {**data_pool(survey, drop=True), "drop": not with_marked, "speed_real": True}
    f["weighted"] = request.args.get("gewichtet") == "1" and weights.active(survey)
    keep = first
    key = results_store.fingerprint(survey, f) if keep else None
    kept = results_store.load(survey, key, f) if keep else None
    fetched = request.headers.get("X-Requested-With") == "fetch"
    # the parts in the order of the results page: the segments before refresh_estimates (they bring themselves up to date)
    lc_part = request.args.get("lc_part", type=int)
    if lc_part is not None and fetched:
        return _lc_part(survey, lc_part, key, kept, "", f)
    if kept is not None and res.refresh_estimates(kept[0], survey):
        kept = None
    hb_part = request.args.get("hb_part", type=int)
    if hb_part is not None and fetched:
        return _hb_part(survey, hb_part, kept, "", f)
    sim_part = request.args.get("sim_part", type=int)
    if sim_part is not None and fetched:
        return _sim_part(survey, sim_part, kept, "", f)
    if kept is None and not request.args.get("go") and current_app.config.get("RESULTS_WAIT_PAGE", True):
        # something to compute: the page with the bar opens at once and asks again by itself (go=1)
        return render_template("surveys/results_wait.html", survey=survey, f=f, first=first, by=None)
    if kept is None and not heavy.hold():
        return heavy.busy()
    token = request.cookies.get(PROGRESS_COOKIE)
    try:
        if kept is not None:
            data, computed_at = kept
        else:
            # the saved pool: everything, kept for the results page too; another pool: this question alone
            data = res.compute(survey, f["sources"], f["all"], f["drop"], f["speeder"], f["speed_real"],
                               progress=progress.reporter(token, current_user.id), weighted=f["weighted"],
                               **({} if keep else {"only": {item.id}}))
            computed_at = utcnow()
            if keep:
                results_store.save(survey, key, data, f, basis=key)
        entry = next((e for e in data.get("items") or [] if e.get("item") is not None and e["item"].id == item.id), None)
        kind = kind_of(entry)
        if kind is None:
            abort(404)
        editor = survey.can(current_user, "editor")
        if not editor:
            res.for_viewers(data)
        one = {**data, "items": [entry]}
        # the scenario the simulator opens with is kept under the fingerprint of the results, as on the results page
        opening = None
        if keep:
            opened = results_store.load(survey, key, f, name="sim")
            opening = dict(opened[0]) if opened is not None else {}
        marks = {k: v[0] for k, v in (opening or {}).items()}
        sims = simulator.page(one, request.args, f["weighted"], editor, kept=opening)
        if opening is not None and {k: v[0] for k, v in opening.items()} != marks:
            results_store.save(survey, key, opening, f, name="sim", basis=key)
        cited = res.with_sampling_note(res.page_sources(one) + simulator.sources(sims))
        return render_template("surveys/analysis.html", survey=survey, r=data, e=entry, kind=kind, item=item,
                               method=method_name(kind), f=f, pool=f, first=first, sims=sims,
                               cited=cited, notes=res.footnotes(cited), weighting_on=weights.active(survey),
                               computed_at=computed_at, part_url=request.path,
                               back_args=pool_args())
    finally:
        progress.clear(token, current_user.id)
