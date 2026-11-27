"""Pages below the tab Ergebnisse: one page per analysis (restructuring of the results part, stage 1).

A question of the conjoint family (CBC, ACBC, ACA, ratings-based conjoint, MaxDiff, paired comparison) has its whole
analysis on a page of its own: importance and part-worths, the market simulator, the segments, the model quality, the
details and the downloads. The results page keeps a short summary per question and lists the analyses in its section
"Analysen". The numbers are those of the results page (results.compute, the kept results under their fingerprint);
only where they are shown changed.
"""
from flask import abort, current_app, render_template, request, url_for
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


# --- the models with a page of their own (stage 2): structural equation model, panel models, vignettes ------------

def model_rows(survey, data, sem=None, sem_job=None, longitudinal=None):
    """The lines of the section "Analysen" for the structural equation model, the models over the waves of a panel
    and the vignettes, as far as the survey has them; each with its state as badges."""
    out = []
    args = pool_args()
    if sem or sem_job:
        from app import sem_versions

        badges = []
        if sem_job and not sem:
            badges.append((de_en("läuft", "running"), ""))
        elif sem.get("error"):
            badges.append((de_en("ohne Lösung", "no solution"), "badge-pending"))
        else:
            head = sem_versions.page(survey) if sem.get("fit") and not sem.get("weighted") else None
            if head and head.get("current"):
                badges.append((de_en("Version {k} · aktuell", "Version {k} · current", k=head["current"].number),
                               "badge-approved"))
            else:
                badges.append((de_en("geschätzt", "estimated"), "badge-approved"))
        measurement = bool(sem and sem.get("mode") == "measurement")
        out.append({"key": "sem", "method": de_en("Messmodell aller Skalen", "Measurement model of all scales")
                    if measurement else de_en("Strukturgleichungsmodell", "Structural equation model"),
                    "question": "", "hint": "", "badges": badges,
                    "url": url_for("surveys.results_model", survey_id=survey.id,
                                   **({"gewichtet": args["gewichtet"]} if "gewichtet" in args else {}))})
    if longitudinal:
        result, job = longitudinal.get("result"), longitudinal.get("job") or {}
        if longitudinal.get("missing"):
            badge = (de_en("Leserecht fehlt", "read access missing"), "badge-pending")
        elif not result and job.get("state") == "running":
            badge = (de_en("läuft", "running"), "")
        elif not result:
            badge = (de_en("noch nicht geschätzt", "not estimated yet"), "")
        elif result.get("error"):
            badge = (de_en("ohne Lösung", "no solution"), "badge-pending")
        else:
            badge = (de_en("geschätzt", "estimated"), "badge-approved")
        out.append({"key": "panel", "method": de_en("Längsschnitt über die Wellen", "Longitudinal analysis over the waves"),
                    "question": "", "hint": " · ".join((result or {}).get("waves") or []), "badges": [badge],
                    "url": url_for("surveys.results_longitudinal", survey_id=survey.id)})
    vignettes = (data or {}).get("vignettes") or []
    if vignettes:
        n = sum(len(v.get("questions") or []) for v in vignettes)
        out.append({"key": "vignettes", "method": de_en("Vignetten", "Vignettes"),
                    "question": ", ".join(str(v.get("name") or v.get("code")) for v in vignettes), "hint": "",
                    "badges": [(de_en("1 Frage ausgewertet", "1 question analysed") if n == 1
                                else de_en("{n} Fragen ausgewertet", "{n} questions analysed", n=n), "")],
                    "url": url_for("surveys.results_vignettes", survey_id=survey.id, **args)})
    return out


def _page_pool(survey):
    """(f, key, kept, first) of a page below "Ergebnisse": the pool and view of the address as on the results page."""
    from app import results_store, weights

    first = not any(key in request.args for key in ("source", "all", "drop"))
    f = {**data_pool(survey, drop=True), "drop": request.args.get("markierte") != "1", "speed_real": True}
    f["weighted"] = request.args.get("gewichtet") == "1" and weights.active(survey)
    key = results_store.fingerprint(survey, f) if first else None
    kept = results_store.load(survey, key, f) if first else None
    return f, key, kept, first


@bp.route("/<sid:survey_id>/results/model")
@login_required
def results_model(survey_id):
    """The page of the structural equation model (or the measurement model of all scales) below "Ergebnisse": the
    model as the results page keeps and judges it (results._sem_result, the saved pool without what quality
    assurance marks, weighted with "gewichtet"); estimated in the background, the page shows the bar."""
    from flask import g

    from app import results as res, sem_model, sem_versions
    from app.surveys import results as page

    survey = get_survey(survey_id)
    if survey.sampling is not None:  # a diary study has no model over its entries (results._sem_result)
        abort(404)
    sem, f = page._sem_of_page(survey)
    job = g.get("sem_job")
    if sem is None and not job:
        abort(404)
    boot = None
    if sem is not None:
        boot = sem_model.boot_state(survey, sem)
        if not f.get("weighted") and not sem.get("error"):
            try:  # keeping a version never costs the page (as on the results page)
                sem_versions.record(survey, sem, (boot or {}).get("result"))
            except Exception:
                db.session.rollback()
                current_app.logger.exception("version of the model of survey %s not kept", survey.id)
    cited = sem_model.sources(sem)
    measurement = bool(sem and sem.get("mode") == "measurement")
    return render_template("surveys/analysis_model.html", survey=survey, what="sem", sem=sem, sem_boot=boot, sem_job=job,
                           f=f, cited=cited, notes=res.footnotes(cited),
                           title=de_en("Messmodell aller Skalen", "Measurement model of all scales") if measurement
                           else de_en("Strukturgleichungsmodell", "Structural equation model"),
                           back_args={"gewichtet": "1"} if f.get("weighted") else {})


@bp.route("/<sid:survey_id>/results/longitudinal")
@login_required
def results_longitudinal(survey_id):
    """The page of the models over the waves of a panel below "Ergebnisse" (app/panel_models.py): kept ones only,
    computed on their button."""
    from app import panel_models, results as res

    survey = get_survey(survey_id)
    longitudinal = panel_models.page(current_user, survey)
    if not longitudinal:
        abort(404)
    cited = panel_models.sources(longitudinal)
    return render_template("surveys/analysis_model.html", survey=survey, what="panel", longitudinal=longitudinal,
                           cited=cited, notes=res.footnotes(cited), f=None, back_args={},
                           title=de_en("Längsschnitt über die Wellen", "Longitudinal analysis over the waves"))


@bp.route("/<sid:survey_id>/results/vignettes")
@login_required
def results_vignettes(survey_id):
    """The page of the vignettes below "Ergebnisse" (app/vignettes.py): the effect of the factors per loop and
    question, from the results of the pool and view of the address, kept as the results page keeps them."""
    from app import progress, results as res, results_store
    from app.surveys.results import PROGRESS_COOKIE

    survey = get_survey(survey_id)
    f, key, kept, first = _page_pool(survey)
    if kept is not None and res.refresh_estimates(kept[0], survey):
        kept = None
    if kept is None and not request.args.get("go") and current_app.config.get("RESULTS_WAIT_PAGE", True):
        return render_template("surveys/results_wait.html", survey=survey, f=f, first=first, by=None)
    if kept is None and not heavy.hold():
        return heavy.busy()
    token = request.cookies.get(PROGRESS_COOKIE)
    try:
        if kept is not None:
            data = kept[0]
        else:
            data = res.compute(survey, f["sources"], f["all"], f["drop"], f["speeder"], f["speed_real"],
                               progress=progress.reporter(token, current_user.id), weighted=f["weighted"])
            if first:
                results_store.save(survey, key, data, f, basis=key)
        if not data.get("vignettes"):
            abort(404)
        cited = res.with_sampling_note(res.page_sources(data))
        return render_template("surveys/analysis_model.html", survey=survey, what="vignettes", r=data, f=f,
                               cited=cited, notes=res.footnotes(cited), back_args=pool_args(),
                               title=de_en("Vignetten", "Vignettes"))
    finally:
        progress.clear(token, current_user.id)
