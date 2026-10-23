"""Estimations on the results page: hierarchical Bayes, latent classes, omega and the market simulator."""
from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.lang import de_en
from app.surveys.base import bp, data_pool, get_survey


@bp.route("/<sid:survey_id>/results/hb/<int:item_id>", methods=["POST"])
@login_required
def results_hb(survey_id, item_id):
    """Start the hierarchical Bayes estimation of a MaxDiff, CBC or ACBC item for the data pool of the
    results page (app/maxdiff_hb.py, app/conjoint_hb.py): in a background thread, the page shows the
    status and later the result. With "longer": burn-in and iterations of the stored result doubled
    (app/hb_convergence.py), for a chain that did not pass the convergence rule."""
    from app import export, hb, hb_convergence, maxdiff_hb
    from app.models import Item

    survey = get_survey(survey_id)
    item = db.session.get(Item, item_id)
    hb_model = export.hb_module(item) if item is not None else None
    if hb_model is None or item.block.survey_id != survey.id:
        abort(404)
    pool = {**data_pool(survey, request.form), "drop": True}  # as the results page: never with marked participations
    config = None
    if request.form.get("longer"):
        stored = hb_model.load(item) or {}
        if stored.get("settings"):
            config = hb_convergence.longer_settings(stored["settings"], maxdiff_hb.settings())
    pool = maxdiff_hb.stamp(survey, pool)  # the fingerprint of the data first, then the table (V1)
    state = hb_model.start(item, hb_model.pool_table(survey, pool), pool,
                           background=not current_app.config.get("HB_SYNC"), config=config)
    if state == "started":
        message = de_en("HB-Schätzung gestartet; sie läuft im Hintergrund, die Seite zeigt den Stand.",
                        "HB estimation started; it runs in the background, the page shows how far it is.")
    elif state == "running":
        message = de_en("Für diese Frage läuft bereits eine HB-Schätzung.", "An HB estimation is already running for this question.")
    elif state == "current":
        message = de_en("Die gespeicherte HB-Schätzung passt schon zu diesen Daten.", "The saved HB estimate already fits these data.")
    elif state == "nodata":
        message = (de_en("Für eine HB-Schätzung fehlen Daten (mindestens {n} Personen mit beantworteten Sets).",
                         "An HB estimation lacks data (at least {n} people with answered sets).", n=hb.MIN_PERSONS)
                   if item.type == "maxdiff" else
                   de_en("Für eine HB-Schätzung fehlen Daten (mindestens {n} Personen mit beantworteten Paaren).",
                         "An HB estimation lacks data (at least {n} people with answered pairs).", n=hb.MIN_PERSONS)
                   if item.type == "paired" else
                   de_en("Für eine HB-Schätzung fehlen Daten (mindestens {n} Personen mit beantworteten Aufgaben).",
                         "An HB estimation lacks data (at least {n} people with answered tasks).", n=hb.MIN_PERSONS))
    else:  # "disconnected"
        message = de_en("Die Aussagen zerfallen in Gruppen, die nie gemeinsam in einem Set vorkamen: Zwischen den "
                        "Gruppen gibt es keinen gemeinsamen Maßstab, deshalb keine HB-Schätzung.",
                        "The statements fall into groups that never appeared together in a set: there is no common scale "
                        "between the groups, so no HB estimation.")
    if request.headers.get("X-Requested-With") == "fetch":
        # static/hb.js: the page stays, the script fetches the HB block of the question anew (hb_part)
        from flask import jsonify

        return jsonify(state=state, message=message, ok=state in ("started", "current"))
    flash(message, "success" if state in ("started", "current") else "error")
    args = {"source": pool["sources"], **({"all": "1"} if pool["all"] else {}),
            **({"drop": "1"} if pool["drop"] else {})}
    return redirect(url_for("surveys.results", survey_id=survey.id, **args) + f"#hb-{item.id}")


@bp.route("/<sid:survey_id>/results/hb/<int:item_id>/status")
@login_required
def results_hb_status(survey_id, item_id):
    """How far the running estimation of an item is, for the bar on the results page:
    {"state": "running", "progress": 0..1}; any other state (done, error, aborted) lets the page reload."""
    from flask import jsonify

    from app import export
    from app.models import Item

    survey = get_survey(survey_id)
    item = db.session.get(Item, item_id)
    hb_model = export.hb_module(item) if item is not None else None
    if hb_model is None or item.block.survey_id != survey.id:
        abort(404)
    marker = hb_model.running(item)
    response = (jsonify(state=marker["state"], progress=marker.get("progress") or 0.0, queued=bool(marker.get("queued")))
                if marker else jsonify(state="done"))
    response.headers["Cache-Control"] = "no-store"
    return response


def _lc_item(survey_id, item_id, need=None):
    """The survey and a CBC or MaxDiff question of it, for its segments (app/segments.py)."""
    from app import segments
    from app.models import Item

    survey = get_survey(survey_id, need=need) if need else get_survey(survey_id)
    item = db.session.get(Item, item_id)
    if item is None or item.block.survey_id != survey.id or item.type not in segments.TYPES:
        abort(404)
    return survey, item


def _lc_back(survey, item, form, message=None, error=False):
    """From static/segments.js: {"message", "error"}, the script then fetches the segments of the question anew
    (lc_part). Otherwise back to them on the results page with the pool the page named (the forms carry it only when
    its address had one: naming the saved pool in the address would compute the whole page again)."""
    from flask import jsonify

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(message=message, error=error)
    if message:
        flash(message, "error" if error else "success")
    args = {k: form.getlist(k) for k in ("source", "all", "drop", "gewichtet") if form.get(k)}
    return redirect(url_for("surveys.results", survey_id=survey.id, **args) + f"#lc-{item.id}")


def _lc_part(survey, item_id, key, kept, run, f):
    """The segments of one question alone, for static/segments.js: from the kept results, with what changed since
    (a run started or ended, another solution or other questions to describe them) brought up to date in place and
    kept again. Without kept results the whole page is computed here, while the page shows a bar where the
    segments are; a run of a live vote answers 409 (the script loads the page)."""
    from flask import Response as FlaskResponse, make_response

    from app import compare, results as res, results_store, segments

    if run:
        return FlaskResponse("", status=409)
    if kept is not None:
        data = kept[0]
    else:
        data = res.compute(survey, f["sources"], f["all"], f["drop"], f["speeder"], f["speed_real"], weighted=f["weighted"])
        if key:
            results_store.save(survey, key, data, f, basis=key)
    entry = next((e for e in data.get("items") or [] if e.get("item") is not None and e["item"].id == item_id), None)
    kind = next((k for k in ("cbc", "acbc", "maxdiff", "cva") if entry and (entry.get(k) or {}).get("lc")), None)
    if kind is None:
        abort(404)
    item = entry["item"]
    lang = survey.default_language
    view = segments.revisit(item, entry[kind]["lc"], lang)
    if view.get("cov_ids") is not None and segments.covariate_ids(item) != view.get("cov_ids"):
        # other covariates: the answers decide whether a stored estimate fits (a new key), so the view anew
        table = compare.table_of(survey, f)
        view = segments.view(item, segments.maxdiff_hb.pool_table(survey, {**f, "drop": True}), lang)
        if view.get("result"):
            segments.enrich(item, view, data["items"], table, data.get("weighting"))
    elif view.get("unknown") and view.get("result"):
        segments.enrich(item, view, data["items"], compare.table_of(survey, f), data.get("weighting"))
        view["unknown"] = False
    elif view.get("unknown"):
        view["unknown"] = False
    entry[kind]["lc"] = view
    if kept is not None and key:
        results_store.save(survey, key, data, f, basis=key)
    response = make_response(render_template("surveys/_segments_part.html", survey=survey, e=entry, l=view, f=f,
                                              notes=res.footnotes(res.page_sources(data))))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/<sid:survey_id>/results/lc/<int:item_id>", methods=["POST"])
@login_required
def results_lc(survey_id, item_id):
    """Start the latent class estimation (segments) of a CBC or MaxDiff question for the data pool of the results
    page, in a background thread like HB (app/segments.py); an interrupted run for the same answers resumes."""
    from app import latent_class, segments

    survey, item = _lc_item(survey_id, item_id)
    pool = {**data_pool(survey, request.form), "drop": True}  # as the results page: never with marked participations
    config = None
    if request.form.get("more"):  # "mit mehr Starts": for a solution found by fewer than two of the kept runs
        config = {**segments.settings(), "starts": latent_class.STARTS * 2}  # 100, the top of Masyn's (2013) range
    pool = segments.maxdiff_hb.stamp(survey, pool)  # the fingerprint of the data first, then the table (V1)
    state = segments.start(item, segments.maxdiff_hb.pool_table(survey, pool), pool,
                           background=not current_app.config.get("LC_SYNC"), config=config)
    if state == "started":
        message = de_en("Segmentschätzung gestartet; sie läuft im Hintergrund, die Seite zeigt den Stand.",
                        "Segment estimation started; it runs in the background, the page shows how far it is.")
    elif state == "running":
        message = de_en("Für diese Frage läuft bereits eine Segmentschätzung.", "A segment estimation is already running for this question.")
    elif state == "current":
        message = de_en("Die gespeicherte Segmentschätzung passt schon zu diesen Daten.", "The saved segment estimate already fits these data.")
    elif item.type == "maxdiff":  # "nodata"
        message = de_en("Für Segmente fehlen Daten (mindestens {n} Personen mit beantworteten Sets).",
                        "Segments lack data (at least {n} people with answered sets).", n=latent_class.MIN_PERSONS)
    else:
        message = de_en("Für Segmente fehlen Daten (mindestens {n} Personen mit beantworteten Aufgaben).",
                        "Segments lack data (at least {n} people with answered tasks).", n=latent_class.MIN_PERSONS)
    return _lc_back(survey, item, request.form, message, error=state not in ("started", "current"))


@bp.route("/<sid:survey_id>/results/lc/<int:item_id>/status")
@login_required
def results_lc_status(survey_id, item_id):
    """How far the running segment estimation is, for the bar on the results page (as results_hb_status)."""
    from flask import jsonify

    from app import segments

    survey, item = _lc_item(survey_id, item_id)
    marker = segments.running(item)
    response = (jsonify(state=marker["state"], progress=marker.get("progress") or 0.0, queued=bool(marker.get("queued")))
                if marker else jsonify(state="done"))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/<sid:survey_id>/results/lc/<int:item_id>/choose", methods=["POST"])
@login_required
def results_lc_choose(survey_id, item_id):
    """Which solution the page shows (and the export and the simulator use) and the names of its segments. Like the
    scenarios of the simulator no change of the questionnaire: allowed in every state, with its own undo."""
    from app import segments

    survey, item = _lc_item(survey_id, item_id, need="editor")
    result = segments.load(item)
    kmax = max((s["K"] for s in (result or {}).get("solutions") or []), default=0)
    before = segments.covariate_ids(item)
    changed = segments.choose(item, request.form, kmax)
    if changed:
        db.session.commit()
    if changed and segments.covariate_ids(item) != before:
        # other covariates: another model, estimated anew at once (in the background, as the button does)
        pool = segments.maxdiff_hb.stamp(survey, {**data_pool(survey, request.form), "drop": True})
        state = segments.start(item, segments.maxdiff_hb.pool_table(survey, pool), pool,
                               background=not current_app.config.get("LC_SYNC"))
        return _lc_back(survey, item, request.form, de_en("Kovariaten übernommen; die Segmente werden neu geschätzt.",
                                                          "Covariates applied; the segments are estimated anew.")
                        if state in ("started", "running", "current") else de_en("Kovariaten übernommen.", "Covariates applied."))
    return _lc_back(survey, item, request.form, de_en("Übernommen.", "Applied.") if changed else None)


@bp.route("/<sid:survey_id>/results/lc/<int:item_id>/undo", methods=["POST"])
@login_required
def results_lc_undo(survey_id, item_id):
    from app import segments

    survey, item = _lc_item(survey_id, item_id, need="editor")
    done = segments.undo_last(item)
    if done:
        db.session.commit()
    return _lc_back(survey, item, request.form, de_en("Letzte Änderung zurückgenommen.", "Last change undone.") if done else None)


def _omega_part(survey, key, kept, run, f):
    """The omega intervals of the results page, for static/omega.js: kept ones under the fingerprint of the results,
    else drawn now and kept (results.omega_intervals). With kept results only the scales that wait for them; without
    (another pool, or the results changed since the page was made) every scale of the survey. A run of a live vote
    answers 409 (its page draws them at once)."""
    from flask import Response as FlaskResponse, make_response

    from app import results as res, results_store

    if run:
        return FlaskResponse("", status=409)
    if key is None:
        key = results_store.fingerprint(survey, f)
    found, marker = res.omega_part_state(survey, f, key, res.omega_waiting(kept[0]) if kept is not None else None)
    if marker is not None:  # the bootstrap runs as a job of its own (app/omega_job.py): the script asks again
        from flask import jsonify

        waiting = jsonify(state=marker.get("state"), progress=marker.get("progress") or 0.0,
                          queued=bool(marker.get("queued")))
        waiting.status_code = 202 if marker.get("state") == "running" else 500
        waiting.headers["Cache-Control"] = "no-store"
        return waiting
    items = {i.id: i for b in survey.blocks for i in b.items}
    slots, note = [], False
    for ref, one in found.items():
        item_id, _, name = ref.partition(":")
        item = items.get(int(item_id))
        if item is None:
            continue
        figures = {"omega_ci": one.get("ci"), "omega_failed": one.get("failed")}
        slots.append((f"s:{ref}", figures))
        if not one.get("pooled"):  # the overview lists the scales outside loops
            slots.append((f"o:{item.code}:{name}", figures))
            note = note or bool(one.get("failed"))
    response = make_response(render_template("surveys/_omega_part.html", slots=slots, note=note))
    response.headers["Cache-Control"] = "no-store"
    return response


def _hb_part(survey, item_id, kept, run, f):
    """The HB block of one question alone, for static/hb.js: from the kept results (their HB views brought up to
    date by results.refresh_estimates), else this question alone computed. A run of a live vote answers 409 (the
    script loads the page)."""
    from flask import Response as FlaskResponse, make_response

    from app import results as res

    if run:
        return FlaskResponse("", status=409)
    if kept is not None:
        data = kept[0]
    else:
        data = res.compute(survey, f["sources"], f["all"], f["drop"], f["speeder"], f["speed_real"], only={item_id},
                           weighted=f["weighted"])
    entry = next((e for e in data.get("items") or [] if e.get("item") is not None and e["item"].id == item_id), None)
    kind = next((k for k in ("maxdiff", "cbc", "acbc", "paired", "cva") if entry and (entry.get(k) or {}).get("hb")), None)
    if kind is None:
        abort(404)
    response = make_response(render_template("surveys/_hb_part.html", survey=survey, e=entry, kind=kind, f=f,
                                              notes=res.footnotes(res.page_sources(data))))
    response.headers["Cache-Control"] = "no-store"
    return response


def _sim_part(survey, item_id, kept, run, f):
    from flask import Response as FlaskResponse, make_response

    from app import results as res, simulator

    if run:
        return FlaskResponse("", status=409)
    if kept is not None:
        data = kept[0]
    else:
        # nothing kept for this pool (another pool, or the kept results are out of date): this one question alone,
        # never the whole page; the page itself keeps computing it as before (Ralf, 2026-10-05: "Varianten
        # durchrechnen" loaded the page again and closed the simulator)
        data = res.compute(survey, f["sources"], f["all"], f["drop"], f["speeder"], f["speed_real"], only={item_id},
                           weighted=f["weighted"])
    entry = next((e for e in data.get("items") or [] if e.get("item") is not None and e["item"].id == item_id), None)
    part = simulator._part(entry) if entry else None
    if part is None or entry["item"].type not in simulator.TYPES:
        abort(404)
    item = entry["item"]
    s = simulator.view(item, part, request.args, survey.default_language,
                       data.get("weighting") if f["weighted"] else None, survey.can(current_user, "editor"))
    if s is None:
        abort(404)
    response = make_response(render_template("surveys/_simulator_part.html", survey=survey, s=s,
                                              notes=res.footnotes(simulator.sources({item.id: s}))))
    response.headers["Cache-Control"] = "no-store"
    return response


def _sim_item(survey_id, item_id):
    """The survey (role "Bearbeiten") and a CBC, ACBC or MaxDiff question of it, for the scenarios of the simulator."""
    from app.models import Item

    survey = get_survey(survey_id, need="editor")
    item = db.session.get(Item, item_id)
    if item is None or item.block.survey_id != survey.id or item.type not in ("conjoint", "acbc", "maxdiff"):
        abort(404)
    return survey, item


def _sim_back(survey, item, message=None, error=False, **extra):
    """Back to the simulator of the question on the results page, with the page's own parameters (pool, weighting,
    grouping) as the form sent them. From the script (static/simulator.js): {"message", "error", "load": the
    scenario to show (saved: sim_load) or null (the one on the page)} instead, it fetches the fold again."""
    from urllib.parse import parse_qsl

    from flask import jsonify

    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(message=message, error=error, load=extra.get("sim_load"))
    if message:
        flash(message, "error" if error else "success")
    keep = [(k, v) for k, v in parse_qsl(request.form.get("back", ""))
            if k in ("source", "all", "drop", "gewichtet", "gruppen")]
    args = {}
    for k, v in keep:
        args.setdefault(k, []).append(v)
    return redirect(url_for("surveys.results", survey_id=survey.id, **args, sim=item.id, **extra) + f"#sim-{item.id}")


@bp.route("/<sid:survey_id>/results/sim/<int:item_id>/save", methods=["POST"])
@login_required
def results_sim_save(survey_id, item_id):
    """Save the scenario of the market simulator under a name (app/simulator.py). Scenarios do not change the
    questionnaire: allowed in every state of the survey, taken back with the simulator's own undo (results_sim_undo),
    not with the arrows of the editor (they would put the whole question back and could change the key of its HB
    estimate)."""
    from app import simulator

    survey, item = _sim_item(survey_id, item_id)
    index, problem = simulator.save(item, request.form)
    if problem:
        return _sim_back(survey, item, problem, error=True)
    name = simulator.saved(item)[index]["name"]
    db.session.commit()
    return _sim_back(survey, item, de_en("Szenario „{name}“ gespeichert.", "Scenario “{name}” saved.", name=name), sim_load=index)


@bp.route("/<sid:survey_id>/results/sim/<int:item_id>/delete", methods=["POST"])
@login_required
def results_sim_delete(survey_id, item_id):
    from app import simulator

    survey, item = _sim_item(survey_id, item_id)
    name = simulator.delete(item, request.form.get("sim_delete", type=int))
    if name is not None:
        db.session.commit()
    return _sim_back(survey, item, de_en("Szenario „{name}“ gelöscht.", "Scenario “{name}” deleted.", name=name)
                     if name is not None else None)


@bp.route("/<sid:survey_id>/results/sim/<int:item_id>/undo", methods=["POST"])
@login_required
def results_sim_undo(survey_id, item_id):
    """The simulator's own undo: the scenarios as before their last change, in every state of the survey; again:
    the change is redone."""
    from app import simulator

    survey, item = _sim_item(survey_id, item_id)
    done = simulator.undo_last(item)
    if done:
        db.session.commit()
    return _sim_back(survey, item, de_en("Letzte Änderung der Szenarien zurückgenommen.", "Last change of the scenarios undone.")
                     if done else None)
