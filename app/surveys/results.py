"""Tab Ergebnisse: the results page, SEM layout and bootstrap, methods section, scale table, classes."""
from urllib.parse import urlencode

from flask import abort, current_app, flash, g, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import autosave, heavy, report_jobs, undo
from app.extensions import db
from app.lang import de_en
from app.models import utcnow
from app.surveys.base import bp, data_pool, get_survey, pool_counts, pool_frozen, pool_unfinished, pool_frozen_note, stored_pool
from app.surveys.estimates import _hb_part, _lc_part, _omega_part, _sim_part
from app.surveys.field import _board_tests, _diary_scales, _diary_time
from app.timeutil import fmt, to_local


def _hyp_key(survey, key, lang):
    """The key the evaluated hypotheses of the study plan are kept under (results page): the fingerprint of the
    results, the plan, the version of what a result holds (results kept before the sensitivity, PA2, are made anew)
    and the language of the page, in which the sentences are built (UI/UX audit 2026-10-07, W106, W107)."""
    import hashlib
    import json as json_mod

    from app import hypotheses

    plan_key = hashlib.sha256(json_mod.dumps([_plan_basis(survey), hypotheses.VERSION, lang], sort_keys=True)
                              .encode()).hexdigest()[:16]
    return f"{key}:{plan_key}" if key else None


def _kept_page(survey, **_):
    """Whether the results page can be shown from what is kept, without reading the participations again: the survey's
    own data pool, no run of a live vote, no grouping or filter, kept results and (with hypotheses in the study plan)
    kept hypotheses. Then the page answers in the request also from REPORT_JOB_THRESHOLD participations on, instead
    of a process of its own that took 3.8 to 4.3 s for what the request shows in 0.3 s (performance audit 2026-10-18,
    PERF-03)."""
    from app import lang as ui_lang, results as res, results_store, weights

    if any(key in request.args for key in ("source", "all", "drop")) or survey.sampling is not None:
        return False
    if any(request.args.get(name) for name in ("durchgang", "nur_var", "gruppen")):
        return False
    f = {**data_pool(survey, drop=True), "drop": request.args.get("markierte") != "1", "speed_real": True}
    f["weighted"] = request.args.get("gewichtet") == "1" and weights.active(survey)
    key = results_store.fingerprint(survey, f)
    kept = results_store.load(survey, key, f)
    if kept is None or res.refresh_estimates(kept[0], survey):
        return False
    if survey.study_plan and survey.study_plan.get("hypotheses"):
        return results_store.load(survey, _hyp_key(survey, key, ui_lang.current()), f, name="hypotheses") is not None
    return True


@bp.route("/<sid:survey_id>/results")
@login_required
@report_jobs.background(ready=_kept_page)
def results(survey_id):
    """Descriptive results (work in progress) for the survey's data pool. They are computed when what they
    rest on has changed since the last computation (new participations in the pool, pool or quality assurance
    changed, the questionnaire changed, see app/results_store.py) and shown from the kept ones otherwise."""
    from app import progress, results as res, results_store

    survey = get_survey(survey_id)  # access is checked on every request, kept results or not
    first = not any(key in request.args for key in ("source", "all", "drop"))  # the survey's saved data pool
    # quality assurance: the results leave out what it marks, whatever the address says about the pool ("drop" is for
    # the downloads, see _pool.html). The page offers one view with them ("Markierte einbeziehen", markierte=1), to
    # see whether the suspect participations carry a result (concept ki-agenten-konzept.md, E9)
    with_marked = request.args.get("markierte") == "1"
    f = {**data_pool(survey, drop=True), "drop": not with_marked, "speed_real": True}
    from app import weights

    # weighted results (app/weights.py): chosen in the address, only offered with a weighting set (page "Quoten")
    f["weighted"] = request.args.get("gewichtet") == "1" and weights.active(survey)
    # a live vote: the results of one run (live concept, L8), by its number (durchgang_id in the
    # export); a name from an older address still works
    run = request.args.get("durchgang", "") if survey.live is not None else ""
    if not survey.can(current_user, "editor") and (run or request.args.get("nur_var")):
        # Filtering aggregates permits reconstruction of individual answers (AZ-1).
        abort(403)
    f["weighted"] = f["weighted"] and not run  # one run of a live vote: unweighted
    keep = first and not run  # one kept computation per survey: its own pool
    key = results_store.fingerprint(survey, f) if keep else None
    kept = results_store.load(survey, key, f) if keep else None
    # the omega intervals alone (static/omega.js): drawn once per state of the data and kept (results.fill_omega)
    if request.args.get("omega_part") and request.headers.get("X-Requested-With") == "fetch":
        return _omega_part(survey, key, kept, run, f)
    # the segments of one question alone (static/segments.js): before refresh_estimates, which would drop the kept
    # results for a new choice of segments; the part brings them up to date itself
    lc_part = request.args.get("lc_part", type=int)
    if lc_part is not None and request.headers.get("X-Requested-With") == "fetch":
        return _lc_part(survey, lc_part, key, kept, run, f)
    # an HB estimation that starts, runs or ends: only its views are read anew (results.refresh_estimates)
    if kept is not None and res.refresh_estimates(kept[0], survey):
        kept = None
    # the HB block of one question alone (static/hb.js): after starting an estimation and when it ends, the page
    # is not loaded again; its views were just read anew (refresh_estimates)
    hb_part = request.args.get("hb_part", type=int)
    if hb_part is not None and request.headers.get("X-Requested-With") == "fetch":
        return _hb_part(survey, hb_part, kept, run, f)
    # "Nach Gruppen aufteilen" (app/compare.py): kept apart, under the fingerprint plus the grouping question
    from app import compare

    # the fold of the market simulator alone (static/simulator.js): from the kept results, the page is not loaded and
    # nothing computed again; without kept results the script loads the whole page
    part = request.args.get("sim_part", type=int)
    if part is not None and request.headers.get("X-Requested-With") == "fetch":
        return _sim_part(survey, part, kept, run, f)
    # "Nur zeigen" (concept gruppen-aufteilen-konzept.md, section 6): the page for one or more groups of a group
    # variable only, as a view: computed on the spot and not kept, the exports stay unfiltered
    only = compare.chosen(survey, request.args.get("nur_var")) if request.args.get("nur_var") else None
    only_codes = [c for c in dict.fromkeys(request.args.getlist("nur")) if c][:groupvars_max()] if only is not None else []
    if (only is not None and not only_codes) or run:
        only = None
    if only is not None:
        keep, key, kept = False, None, None
    by = compare.chosen(survey, request.args.get("gruppen"))
    by_key = f"{key}:{by.stamp}" if keep and by is not None else None
    if by_key and survey.sampling is not None:  # a diary study: by person, not kept before (code review 2026-10-15, C4)
        by_key += ":persons"
    # in the file of the pool (weighted, with the marked ones), so the views do not push each other out (R5)
    cmp_kept = results_store.load(survey, by_key, f, name="compare") if by_key else None
    if (kept is None or (by is not None and cmp_kept is None)) and not run and only is None and not request.args.get("go")             and current_app.config.get("RESULTS_WAIT_PAGE", True):
        # there is something to compute: the page opens at once, with the progress bar, and asks for the results
        # by itself (go=1), instead of the browser waiting on the old page until they are computed
        return render_template("surveys/results_wait.html", survey=survey, f=f, first=first, by=by)
    # computed anew: one of the places for heavy evaluations of this process (app/heavy.py, P9); also the run of a live
    # vote, which draws its omega intervals at once (code review 2026-10-15, A5)
    if kept is None and not heavy.hold():
        return heavy.busy()
    # while the results are computed a bar shows how far: the page names its token in a cookie (app/progress.py)
    token = request.cookies.get(PROGRESS_COOKIE)
    try:
        table = None
        if kept is not None:
            data, computed_at = kept
        elif run:  # live votes are small: computed on the spot, not kept
            from app import export

            table = export.rows(survey, f["all"], f["sources"], f["speeder"], f["speed_real"], speed_sources=f["sources"],
                                with_hb=False, with_quality=False).keep(
                lambda r: str(r.get("durchgang_id")) == run if run.isdigit() else r.get("durchgang") == run)
            data = res.compute(survey, f["sources"], f["all"], f["drop"], f["speeder"], f["speed_real"], table=table,
                               weighted=f["weighted"], omega_ci=True)  # nothing kept: the intervals at once
            computed_at = utcnow()
        else:
            # read once for the results, the comparison, the hypotheses and the structural equation model (code
            # review 2026-10-07, R3: without a grouping the hypotheses and the model read the pool again)
            report = progress.reporter(token, current_user.id) or (lambda share, text: None)
            table = compare.table_of(survey, {**f, "drop": False}, progress=lambda n, total: report(
                0.5 * n / total, de_en("Antworten lesen: Teilnahme {n} von {total}", "Reading answers: participation {n} of {total}",
                                       n=n, total=total)))
            if only is not None:
                full_table = table
                only.prepare(table)
                only_codes = [c for c in only_codes if c in {g for g, _ in only.groups}]
                if only_codes:
                    table = table.keep(lambda r: only.member(r) in only_codes)
                    if f["weighted"]:  # the group keeps the weights of the whole pool (audit 2026-10-06, E-2)
                        weights.domain(survey, full_table, table)
                else:  # none of its groups: no filter
                    only = None
            data = res.compute(survey, f["sources"], f["all"], f["drop"], f["speeder"], f["speed_real"],
                               progress=progress.reporter(token, current_user.id), table=table, weighted=f["weighted"],
                               omega_ci=only is not None)  # a filtered view is not kept: the intervals at once
            if only is not None:
                _without_estimates(data)
            computed_at = utcnow()
            if keep:
                results_store.save(survey, key, data, f, basis=key)
        if keep and res.omega_waiting(data):  # the omega intervals, if drawn for this state already (omega_part)
            drawn = results_store.load(survey, key, f, name="omega")
            if drawn is not None:
                res.apply_omega(data, drawn[0])
        cmp = None
        if by is not None:
            if cmp_kept is not None:
                cmp = dict(cmp_kept[0])
                if by.experiment and not cmp.get("blocked"):
                    from app import groupvars

                    cmp["assignment"] = groupvars.assignment_check(by, survey, f["sources"])
            else:
                if table is None:
                    table = compare.table_of(survey, {**f, "drop": False})
                rows, cmp_weights = table, None
                if f["weighted"]:
                    # the weights of the results above (results.compute): only the weighted participations, so the
                    # marked ones stay out here too, also with "Markierte einbeziehen" (audit 2026-10-05, I-1)
                    found = weights.compute(survey, table)
                    if found is not None:
                        rows = table.keep(lambda r: r.get("resp_id") in found["weights"])
                        cmp_weights = [found["weights"][r["resp_id"]] for r in rows]
                if f["drop"] and cmp_weights is None:  # the weighted rows are without the marked already
                    rows = rows.keep(lambda r: r.get("qc_marked") != 1)
                diary_study = survey.sampling is not None
                if diary_study and cmp_weights is None:
                    # a diary study: every person counts once in every part, as the results above (results.compute);
                    # with weights there are shares and means but no test, which would take entries as independent
                    # cases (code review 2026-10-15, C4)
                    from app import diary

                    person = diary.person_weights(rows, getattr(rows, "numbered", {}) or {})
                    cmp_weights = [person.get(r.get("resp_id"), 1.0) for r in rows]
                cmp = compare.compute(survey, by, data, rows, cmp_weights, sources=f["sources"], diary=diary_study)
                if by_key:
                    results_store.save(survey, by_key, cmp, f, name="compare", basis=key)
        # results per hypothesis of the study plan (app/hypotheses.py): kept with the results, under their
        # fingerprint and the plan, so a changed plan is evaluated anew
        from app import lang as ui_lang

        hyps, hyp_lang = None, ui_lang.current()
        hyp_pool = None  # under "Nur zeigen": the participations the hypotheses are tested on (W61)
        if not run and survey.study_plan and survey.study_plan.get("hypotheses"):
            from app import hypotheses

            hyp_key = _hyp_key(survey, key, hyp_lang)
            hyp_kept = results_store.load(survey, hyp_key, f, name="hypotheses") if hyp_key else None
            if hyp_kept is not None:
                hyps = hyp_kept[0]
            else:
                # the hypotheses are tested on the whole data pool, also under "Nur zeigen": a filter on the condition
                # would leave one group of the test
                if only is None and table is None:  # the results were kept: read once for the hypotheses and the model
                    table = compare.table_of(survey, {**f, "drop": False})
                rows = full_table if only is not None else table
                if f["drop"]:
                    rows = rows.keep(lambda r: r.get("qc_marked") != 1)
                # uncorrected: Holm's correction comes after the structural equation model below (hypotheses.with_holm)
                hyps = hypotheses.evaluate(survey, rows, hyp_lang, correct=False)
                if only is not None:
                    hyp_pool = len(rows)
                if hyp_key:
                    results_store.save(survey, hyp_key, hyps, f, name="hypotheses", basis=key)
        # the structural equation model (app/sem_model.py; for everyone since 2026-10-11): from the hypotheses of the
        # plan, or the measurement model of all scales; kept like the hypotheses, on the whole data pool
        sem_result = sem_boot = None
        from app import sem_model

        # not in a diary study: its entries are no independent cases; the scales come as two-level models with the
        # course of the study (_diary_scales) and the paths as multilevel models of the hypotheses (app/diary_model.py)
        if not run and survey.sampling is None:
            sem_result = _sem_result(survey, f, key, lambda: full_table if only is not None else table, wait=False)
            if sem_result is not None:
                from app import study_plan as plan_mod

                sem_boot = sem_model.boot_state(survey, sem_result)
                if hyps:  # the sentences in the language of the page like the other hypotheses
                    sem_model.apply(hyps, sem_result, plan_mod.plan_of(survey)["hypotheses"],
                                    (sem_boot or {}).get("result"), lang=hyp_lang)
        from app import panel_models

        # the models over the waves of a panel (app/panel_models.py): kept ones only, computed on their button; they
        # judge the hypotheses "Wirkung über die Zeit" before Holm's correction
        longitudinal = None if run or only is not None else panel_models.page(current_user, survey)
        if hyps:
            from app import hypotheses as hyp_mod
            from app import study_plan as plan_mod

            if any(h.get("kind") == "lagged" for h in plan_mod.plan_of(survey)["hypotheses"]):
                panel_models.apply(hyps, plan_mod.plan_of(survey)["hypotheses"], longitudinal, lang=hyp_lang)
            hyp_mod.with_holm(survey, hyps, hyp_lang)
        if survey.live is not None:  # a live vote: its word clouds beside the lists, as moderated on the screen
            from app import live

            live.result_clouds(survey, data, viewer=not survey.can(current_user, "editor"), run_id=run or None)
        if not survey.can(current_user, "editor"):
            # "Ansehen" reads aggregated results; answers of single participations need "Bearbeiten", as the data export
            res.for_viewers(data)
        from app import quotas

        runs, asked, chosen = [], [], ""
        if survey.live is not None:
            from app import live

            st = live.state(survey)
            every = st["runs"] + [st["run"]]
            names = [r["name"] for r in every]
            # runs named alike before names were made unique: told apart by their start
            from datetime import datetime

            runs = [(str(r["id"]), r["name"] + (f" ({fmt(datetime.fromisoformat(r['started']))})"
                                                if names.count(r["name"]) > 1 and r.get("started") else "")) for r in every]
            # the questions from the audience, under the results (of the chosen run: by its name, as they are kept)
            chosen = next((r["name"] for r in every if str(r["id"]) == run), run) if run else ""
            asked = live.audience_questions(survey, chosen, shown_only=not survey.can(current_user, "editor"))
        from app import simulator

        # the market simulator (CBC) and "Auswahl aus Aussagen" (MaxDiff): computed from the scenario in the address;
        # the scenarios the page opens with are kept under the fingerprint of the results (simulator.page)
        opening = None
        if keep:
            opened = results_store.load(survey, key, f, name="sim")
            opening = dict(opened[0]) if opened is not None else {}
        marks = {k: v[0] for k, v in (opening or {}).items()}
        sims = {} if run or only is not None else simulator.page(data, request.args, f["weighted"], survey.can(current_user, "editor"),
                                             kept=opening)
        if opening is not None and {k: v[0] for k, v in opening.items()} != marks:
            results_store.save(survey, key, opening, f, name="sim", basis=key)
        from app import hypotheses as hypotheses_mod

        from app import panel as panel_mod

        shown_cmp = panel_mod.named_compare(survey, cmp)  # names instead of codes for links per person, at display (X56)
        cited = res.with_sampling_note(res.page_sources(data) + simulator.sources(sims) + compare.sources(cmp)
                                       + hypotheses_mod.sources(hyps) + sem_model.sources(sem_result)
                                       + panel_models.sources(longitudinal))
        # a diary study (app/diary.py): its modules over the days and hours, and ICC(1)
        diary_time = _diary_time(survey, f, key if keep else None, table) \
            if survey.sampling is not None and only is None and not run else None
        diary_scales = _diary_scales(survey, f, key if keep else None, table) \
            if survey.sampling is not None and only is None and not run else None
        if diary_scales or any((h or {}).get("analysis") == "mixed" for h in hyps or ()):
            from app import mixed, sem_twolevel

            cited = cited + [(de_en("Mehrebenenmodelle der Tagebuchstudie", "Multilevel models of the diary survey"),
                              list(mixed.SOURCES) + (list(sem_twolevel.SOURCES) if diary_scales else []))]
        return render_template("surveys/results.html", survey=survey, r=data, f=f, pool=f, first=first, runs=runs, run=run, asked=asked, run_name=chosen,
                               clear_subgroup_url=request.path + ("?" + urlencode([(k, v) for k, vs in request.args.lists() if k not in ("nur_var", "nur") for v in vs]) if any(k not in ("nur_var", "nur") for k in request.args) else ""),
                               diary_time=diary_time, diary_scales=diary_scales, sources_twolevel=list(__import__("app.sem_twolevel", fromlist=["SOURCES"]).SOURCES), shrout=__import__("app.diary", fromlist=["SHROUT"]).SHROUT,
                               bolger=__import__("app.diary", fromlist=["BOLGER"]).BOLGER,
                               board_tests=_board_tests(survey, f),
                               basis=stored_pool(survey), pool_frozen=pool_frozen(survey), pool_frozen_note=pool_frozen_note(),
                               cited=cited, notes=res.footnotes(cited),
                               sims=sims, weighting_on=weights.active(survey),
                               cmp=shown_cmp, cmp_min=compare.MIN_GROUP, group_choices=compare.choices(survey), hyps=hyps,
                               cmp_overview=compare.overview(shown_cmp, data.get("items")),
                               sem=sem_result, sem_boot=sem_boot, sem_job=g.get("sem_job"),
                               longitudinal=longitudinal,
                               only=panel_mod.named_var(survey, only), only_codes=only_codes,
                               group_vars=panel_mod.named_vars(survey, compare.group_vars(survey)),
                               hyp_pool=hyp_pool,
                               computed_at=computed_at, from_store=kept is not None,
                               quota_report=quotas.report(survey, marked=data.get("quota_marked"), shown=True) if survey.quotas else [],
                               by_source=pool_counts(survey), unfinished=pool_unfinished(survey))
    finally:
        progress.clear(token, current_user.id)


def _sem_result(survey, f, key, table=None, wait=True):
    """The structural equation model of the results page (app/sem_model.py): kept like
    the results per hypothesis, under the fingerprint of the data pool and the study plan; computed on the whole
    pool without what quality assurance marks. table: a function giving the rows read already, or None. None when
    the survey has no model, or while it is computed: with wait=False (the page) the estimate runs as the job
    "sem_fit" in the estimation process or on the Rechenknecht (Ralf 2026-10-17, thread "Rechenknecht"), its state in
    g.sem_job; the downloads (wait=True) compute in the request what is not kept yet."""
    import hashlib
    import json as json_mod

    from app import results_store, sem_model

    if survey.sampling is not None:
        # a diary study: its entries are no independent cases, so no model over them, also not through the routes of
        # the bootstrap and the downloads (audit 2026-10-17, M1; the page itself already leaves it out)
        return None
    plan_key =hashlib.sha256(json_mod.dumps(_plan_basis(survey), sort_keys=True).encode()).hexdigest()[:16]
    sem_key = f"{key}:{plan_key}" if key else None
    kept = results_store.load(survey, sem_key, f, name="sem") if sem_key else None
    background = current_app.config.get("SEM_BACKGROUND") and not current_app.config.get("HB_SYNC")
    if kept is None and sem_key and not wait and background:
        found, state = _sem_job(survey, f, key, sem_key, table)
        if found is None:
            g.sem_job = state
            return None
        kept = (found, None)
    if kept is not None:
        # the arrangement of the diagram is no part of the estimate: always the one saved now (results_sem_layout)
        from app import study_plan

        found = {**kept[0], "layout": study_plan.plan_of(survey)["layout"]["nodes"]} if not kept[0].get("none") else kept[0]
    else:
        found = sem_model.estimate(survey, _sem_rows(survey, f, table), weighted=bool(f.get("weighted"))) or {"none": True}
        if sem_key:
            results_store.save(survey, sem_key, found, f, name="sem", basis=key)
    return None if found.get("none") else found


def _sem_rows(survey, f, table=None):
    from app import compare

    rows = (table() if table else None) or compare.table_of(survey, {**f, "drop": False})
    if f["drop"]:
        rows = rows.keep(lambda r: r.get("qc_marked") != 1)
    return rows


def _sem_job(survey, f, key, sem_key, table):
    """(the model, None) once the job "sem_fit" of this fingerprint has ended (taken into the kept results) or when
    there is nothing to estimate; else (None, its state) after starting it. The part read from the survey waits in
    the kept results ("sem_prep"), so participation numbers never go to the job."""
    from app import jobs, maxdiff_hb, results_store, sem_fit_job, sem_model

    paths = sem_model.fit_paths(survey)
    stored = maxdiff_hb._read(paths[0])
    if stored and stored.get("key") == sem_key:
        prep = results_store.load(survey, sem_key, f, name="sem_prep")
        if prep is not None:
            found = sem_model.finish(prep[0], sem_fit_job.unpack(stored["found"]))
            results_store.save(survey, sem_key, found, f, name="sem", basis=key)
            maxdiff_hb._remove(paths[0])
            return found, None
    state = sem_model.fit_state(survey, sem_key)
    if state["state"] == "running":
        return None, {**state, "key": sem_key}
    out, job = sem_model.prepare(survey, _sem_rows(survey, f, table), weighted=bool(f.get("weighted")))
    if job is None or state["state"] == "error":  # nothing to estimate, or the job broke: here as before
        found = (out if job is None else sem_model.finish(out, sem_model.numbers(job))) or {"none": True}
        results_store.save(survey, sem_key, found, f, name="sem", basis=key)
        return found, None
    results_store.save(survey, sem_key, out, f, name="sem_prep", basis=key)
    pool = {k: f.get(k) for k in ("sources", "all", "drop", "speeder")}
    app = current_app._get_current_object()
    started = jobs.start(app, "sem_fit", sem_model.fit_paths(survey, app), {"key": sem_key, "job": sem_fit_job.pack(job)},
                         pool, True, name=f"sem-fit-{survey.id}")
    return None, {"state": "running", "progress": 0.0, "queued": not started, "key": sem_key}


def _plan_basis(survey):
    """The study plan as the kept hypotheses and the model are computed from it: without the arrangement of the drawing
    (layout), which only says where the variables stand, so moving one computes nothing anew."""
    return {k: v for k, v in (survey.study_plan or {}).items() if k != "layout"}


@bp.route("/<sid:survey_id>/results/sem-layout", methods=["GET", "POST"])
@login_required
def results_sem_layout(survey_id):
    """The variables of the path diagram moved on the results page (static/sem_drag.js): the places go into the
    drawing of the study plan, the one arrangement both show (modell-zeichnen-konzept.md, F5); "Bearbeiten" and
    "Admin" only. A step for "Rückgängig"; JSON with the arrows, the page is not loaded anew. GET: the places saved
    now (after ↶ or ↷ on the page)."""
    from flask import jsonify

    from app import study_plan

    survey = get_survey(survey_id, need="editor")
    if request.method == "GET":
        response = jsonify(nodes=study_plan.plan_of(survey)["layout"]["nodes"])
        response.headers["Cache-Control"] = "no-store"
        return response
    sent = request.get_json(silent=True) or {}
    moved = study_plan.layout_of({"nodes": sent.get("nodes")})["nodes"]
    if not moved:
        return jsonify(error=de_en("Die Reihenfolge kam nicht an. Bitte die Seite neu laden und noch einmal ziehen.",
                                   "The order did not arrive. Please reload the page and drag again.")), 400
    plan = survey.study_plan if isinstance(survey.study_plan, dict) else {}
    places = {**study_plan.plan_of(survey)["layout"]["nodes"], **moved}
    step = undo.begin(survey, layout=True)
    survey.study_plan = {**plan, "layout": study_plan.layout_of({"nodes": places})}
    name = str(sent.get("name") or "")[:80]
    made = step.done(undo.both("Pfaddiagramm: {name} verschoben", "Path diagram: {name} moved", name=name) if name
                     else undo.both("Pfaddiagramm angeordnet", "Path diagram arranged"), page="results")
    db.session.commit()
    return jsonify(status="saved", at=to_local(utcnow()).strftime("%H:%M"), step=made.id if made else None,
                   **undo_tops(survey))


def undo_tops(survey):
    """The labels of ↶ and ↷ and the steps they would take (static/sem_drag.js takes back its own steps without
    loading the page; another step on top goes the usual way)."""
    steps = undo.history(survey, current_user)
    top, nxt = (steps["undo"] or [None])[0], (steps["redo"] or [None])[0]
    return {"undo": undo.label(top) if top else "", "redo": undo.label(nxt) if nxt else "",
            "undo_id": top.id if top else None, "redo_id": nxt.id if nxt else None}


def _sem_of_page(survey):
    """The model as the results page shows it with the survey's saved pool (for the bootstrap's start and state)."""
    from app import results_store

    from app import weights

    # weighted as the page that asks (SEM extensions 2026-10-17, point 5: its forms and links carry "gewichtet")
    weighted = request.values.get("gewichtet") == "1" and weights.active(survey)
    f = {**data_pool(survey, drop=True), "drop": True, "speed_real": True, "weighted": weighted}
    return _sem_result(survey, f, results_store.fingerprint(survey, f), wait=False), f


@bp.route("/<sid:survey_id>/results/sem-fit/status")
@login_required
def results_sem_fit_status(survey_id):
    """How far the estimate of the model is (job "sem_fit"), for the bar of the block (static/app.js,
    data-sem-running); the fingerprint comes from the page that shows the bar."""
    from flask import jsonify

    from app import sem_model

    survey = get_survey(survey_id)
    state = sem_model.fit_state(survey, request.args.get("key", ""))
    response = jsonify(state=state["state"], progress=state.get("progress") or 0.0, queued=bool(state.get("queued")))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/<sid:survey_id>/results/sem-bootstrap", methods=["POST"])
@login_required
def results_sem_bootstrap(survey_id):
    """Start the bootstrap of the structural equation model (intervals of the indirect effects, PLS tests) in the
    background, like HB (app/sem_model.start_bootstrap)."""
    from app import sem_model

    survey = get_survey(survey_id)
    result, f = _sem_of_page(survey)
    pool = {k: f[k] for k in ("sources", "all", "drop", "speeder")}
    # "final": the final version with BOOT_DRAWS_FINAL draws for a report (formative check 2026-10-26, E5)
    draws = sem_model.BOOT_DRAWS_FINAL if request.form.get("draws") == "final" else sem_model.BOOT_DRAWS
    state = sem_model.start_bootstrap(survey, result, pool, background=not current_app.config.get("HB_SYNC"),
                                      draws=draws)
    if state == "started":
        flash(de_en("Bootstrap gestartet; er läuft im Hintergrund, die Seite zeigt den Stand.",
                    "Bootstrap started; it runs in the background, the page shows how far it is."), "success")
    elif state == "running":
        flash(de_en("Der Bootstrap läuft bereits.", "The bootstrap is already running."), "info")
    elif state == "nodata":
        flash(de_en("Für den Bootstrap fehlt ein geschätztes Modell.", "The bootstrap needs an estimated model."), "error")
    return redirect(url_for("surveys.results", survey_id=survey.id, **({"gewichtet": "1"} if f.get("weighted") else {}))
                    + "#sem")


@bp.route("/<sid:survey_id>/results/panel", methods=["POST"])
@login_required
@heavy.limited
def results_panel(survey_id):
    """"Längsschnitt rechnen" of the block over the waves (app/panel_models.py): links the waves here and estimates the
    models as a job in the estimation process or on the Rechenknecht (Ralf 2026-10-17, thread "Rechenknecht"); the
    page takes the result over under the fingerprints of all waves. Written to the access log, since it reads the
    linked answers of every wave like the merged export."""
    from app import audit, panel_models

    survey = get_survey(survey_id)
    found = panel_models.page(current_user, survey)
    if found is None or found.get("missing"):
        flash(de_en("Der Längsschnitt braucht mindestens zwei verknüpfbare Wellen, die Sie lesen dürfen, und eine gewählte "
                    "Größe (Seite „Studienplan“, Abschnitt „Wellen“).",
                    "The longitudinal analysis needs at least two linkable waves you may read and a chosen variable (page "
                    "“Study plan”, section “Waves”)."), "error")
        return redirect(url_for("surveys.results", survey_id=survey.id) + "#panel")
    panel_models.start(current_user, survey, background=not current_app.config.get("HB_SYNC"))
    rows, _ = panel_models.usable(current_user, survey)
    audit.done(detail="waves=" + "+".join(str(r["survey"].id) for r in rows))
    return redirect(url_for("surveys.results", survey_id=survey.id) + "#panel")


@bp.route("/<sid:survey_id>/results/panel/status")
@login_required
def results_panel_status(survey_id):
    """How far the job of the block over the waves is, for the bar of the block (static/app.js, data-sem-running)."""
    from flask import jsonify

    from app import panel_models

    survey = get_survey(survey_id)
    state = panel_models.state(survey, panel_models.key(current_user, survey))
    response = jsonify(state=state["state"], progress=state.get("progress") or 0.0, queued=bool(state.get("queued")))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/<sid:survey_id>/results/panel.docx")
@login_required
@heavy.limited
def results_panel_docx(survey_id):
    """The kept block over the waves as tables for a manuscript (app/panel_docx.py), German or English."""
    from flask import Response as FlaskResponse

    from app import export, panel_docx, panel_models

    survey = get_survey(survey_id)
    found = panel_models.page(current_user, survey)
    result = (found or {}).get("result")
    if not result or result.get("error"):
        abort(404)
    lang = "en" if request.args.get("lang") == "en" else "de"
    name = f"laengsschnitt-{export.slug(survey.title)}-{lang}.docx"
    return FlaskResponse(panel_docx.build(result, lang),
                         mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                         headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@bp.route("/<sid:survey_id>/results/sem.docx")
@login_required
@heavy.limited
def results_sem_docx(survey_id):
    """The structural equation model of the results page as tables for a manuscript (Word, APA style;
    app/sem_docx.py, SEM extensions 2026-10-17, point 15), German or English (?lang=en)."""
    from flask import Response as FlaskResponse

    from app import export, sem_model

    survey = get_survey(survey_id)
    try:
        from app import sem_docx
    except ImportError:  # python-docx is missing on a server whose vendor/ was built before it was needed
        current_app.logger.exception("Word export without python-docx")
        flash(de_en("Der Word-Export geht auf diesem Server gerade nicht. Bitte über „Rückmeldung“ (oben rechts) melden; "
                    "CSV und die anderen Formate gehen.",
                    "The Word export does not work on this server at the moment. Please report it via “Feedback” "
                    "(top right); CSV and the other formats work."), "error")
        return redirect(url_for("surveys.results", survey_id=survey.id) + "#sem")
    result, _ = _sem_of_page(survey)
    if not result or result.get("error") or not result.get("fit"):
        abort(404)
    boot = (sem_model.boot_state(survey, result) or {}).get("result")
    lang = "en" if request.args.get("lang") == "en" else "de"
    return FlaskResponse(sem_docx.build(survey, result, boot, lang), headers={
        "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Content-Disposition": f'attachment; filename="{export.filename(survey, "sem-" + lang)[:-4]}.docx"',
        "Cache-Control": "no-store",
    })


@bp.route("/<sid:survey_id>/export/sem-scores.csv")
@login_required
def export_sem_scores(survey_id):
    """The construct scores of the PLS model of the results page per participation (SEM extensions 2026-10-17, point
    17): resp_id and one column per construct, standardised (mean 0, SD 1) as PLS estimates them; only the complete
    cases of the model. Response data: "Bearbeiten" and up, like the other data exports."""
    import csv
    import io

    from flask import Response as FlaskResponse

    from app import export

    survey = get_survey(survey_id)
    result, _ = _sem_of_page(survey)
    scores = (result or {}).get("scores")
    if not scores:
        abort(404)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(["resp_id"] + [v.replace(":", "_") for v in scores["names"]])  # "bfi:Offenheit": bfi_Offenheit
    for rid, values in zip(scores["ids"], scores["values"]):
        writer.writerow([rid] + [f"{v:.6f}" for v in values])
    return FlaskResponse(buf.getvalue(), mimetype="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{export.filename(survey, "pls-scores")[:-4]}.csv"',
        "Cache-Control": "no-store",
    })


@bp.route("/<sid:survey_id>/results/sem-bootstrap/status")
@login_required
def results_sem_status(survey_id):
    """How far the bootstrap is, for the bar of the block (static/app.js)."""
    from flask import jsonify

    from app import sem_model

    survey = get_survey(survey_id)
    result, _ = _sem_of_page(survey)
    state = sem_model.boot_state(survey, result) or {"state": "none"}
    response = jsonify(state=state["state"], progress=state.get("progress") or 0.0, queued=bool(state.get("queued")))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/<sid:survey_id>/export/methods-section.<fmt>")
@login_required
@report_jobs.background
@heavy.limited
def export_methods(survey_id, fmt):
    """A draft of the methods section (app/methods_text.py): sample, instruments, exclusions, analysis plan and the
    results per hypothesis, from the saved pool without what quality assurance marks; Word or Markdown, German or
    English (?lang=en)."""
    from flask import Response as FlaskResponse

    from app import compare, methods, methods_text, quality, results as res, results_store, study_plan

    if fmt not in ("docx", "md"):
        abort(404)
    survey = get_survey(survey_id)
    lang = "en" if request.args.get("lang") == "en" else "de"
    f = {**stored_pool(survey, drop=True), "speed_real": True}
    key = results_store.fingerprint(survey, f)
    kept = results_store.load(survey, key)
    if kept is not None:
        data = kept[0]
    else:  # computed and kept, as the results page and the scale table keep it
        data = res.compute(survey, f["sources"], f["all"], f["drop"], f["speeder"], f["speed_real"])
        results_store.save(survey, key, data, f, basis=key)
    # the completed participations of the pool: the kept facts of the page "Methodenteil" (methods.facts, the same
    # pool) give the sentences on choice experiments and Standort and the numbers of the exclusions; the table and
    # quality.summary were built anew on every download before (audit 2026-10-10, Q-Rest: 3 to 6 s, 448 MB). A pool
    # "also unfinished ones" has no such facts: built as before
    facts = methods.facts(survey, f["speeder"], f["sources"]) if not f["all"] else None
    table = None
    if facts is None or study_plan.plan_of(survey)["hypotheses"]:  # the tests of the hypotheses need the rows
        table = compare.table_of(survey, {**f, "drop": False})
        if f["drop"]:
            table = table.keep(lambda r: r.get("qc_marked") != 1)
    if facts is not None:
        summary = {**facts["exclusions"], "rules": quality.rules(survey)}
    else:
        summary = quality.summary(survey, f["sources"], f["all"])
    counts = pool_counts(survey)
    # the structural equation model as the results page keeps it (the same pool and fingerprint), estimated and kept
    # only when the page has not done it yet (code review 2026-10-15, A10)
    parts = methods_text.sections(survey, data, table, summary, lang, n=sum(counts.get(s, 0) for s in f["sources"]),
                                  facts=facts, sem=lambda: _sem_result(survey, {**f, "weighted": False}, key, lambda: table))
    name = f"methodenteil-{survey.code}" + ("-en" if lang == "en" else "")
    if fmt == "docx":
        try:
            body = methods_text.docx(survey, parts, lang)
        except ImportError:  # python-docx missing on the server: the same text as Markdown
            return redirect(url_for("surveys.export_methods", survey_id=survey.id, fmt="md", lang=lang))
        return FlaskResponse(body, headers={"Content-Disposition": f'attachment; filename="{name}.docx"'},
                             mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    return FlaskResponse(methods_text.markdown(survey, parts, lang), mimetype="text/markdown; charset=utf-8",
                         headers={"Content-Disposition": f'attachment; filename="{name}.md"'})


@bp.route("/<sid:survey_id>/results/scales.docx")
@login_required
@report_jobs.background
@heavy.limited
def results_table(survey_id):
    """The scales of the results page as a table for a manuscript (Word, APA style; app/manuscript_tables.py): the
    same numbers as the page, from the kept results when they are current. Like the methods section a heavy
    evaluation, in a process of its own for large surveys; the omega intervals come from their job as on the page
    (results.omega_part_state): while it runs, the page says so instead of drawing them here (code review 2026-10-15,
    A4)."""
    from flask import Response as FlaskResponse

    from app import export, results as res, results_store

    survey = get_survey(survey_id)
    try:
        from app import manuscript_tables
    except ImportError:  # python-docx is missing on a server whose vendor/ was built before it was needed
        current_app.logger.exception("Word export without python-docx")
        flash(de_en("Der Word-Export geht auf diesem Server gerade nicht. Bitte über „Rückmeldung“ (oben rechts) melden; "
                    "CSV und die anderen Formate gehen.",
                    "The Word export does not work on this server at the moment. Please report it via “Feedback” "
                    "(top right); CSV and the other formats work."), "error")
        return redirect(url_for("surveys.results", survey_id=survey.id))
    f = {**stored_pool(survey, drop=True), "speed_real": True}  # as the page: the saved pool, without what QC marks
    keep = True
    key = results_store.fingerprint(survey, f)
    kept = results_store.load(survey, key)
    # the saved scenarios of the market simulator use the HB estimates as they are now (results.refresh_estimates)
    if kept is not None and res.refresh_estimates(kept[0], survey):
        kept = None
    if kept is not None:
        data = kept[0]
    else:
        data = res.compute(survey, f["sources"], f["all"], f["drop"], f["speeder"], f["speed_real"])
        if keep:
            results_store.save(survey, key, data, f, basis=key)
    wanted = res.omega_waiting(data)  # the table needs the omega intervals: kept ones, else from their job
    if wanted:
        found, marker = res.omega_part_state(survey, f, key, wanted)
        if marker is not None:
            if marker.get("state") == "running":
                flash(de_en("Die Konfidenzintervalle von ω werden gerade berechnet (Ergebnisseite). Bitte laden Sie die "
                            "Tabelle in einer Minute noch einmal herunter.",
                            "The confidence intervals of ω are being computed (results page). Please download the "
                            "table again in a minute."), "info")
            else:
                flash(de_en("Die Konfidenzintervalle von ω konnten nicht berechnet werden. Bitte laden Sie die Tabelle "
                            "noch einmal herunter.",
                            "The confidence intervals of ω could not be computed. Please download the table again."),
                      "error")
            return redirect(url_for("surveys.results", survey_id=survey.id))
        res.apply_omega(data, found)
    lang = "en" if request.args.get("lang") == "en" else "de"
    return FlaskResponse(manuscript_tables.build(survey, data, lang), headers={
        "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "Content-Disposition": f'attachment; filename="{export.filename(survey, "skalen-" + lang)[:-4]}.docx"',
        "Cache-Control": "no-store",
    })


def groupvars_max():
    from app import groupvars

    return groupvars.MAX_GROUPS


def _without_estimates(data):
    """A filtered view of the results ("Nur zeigen"): the HB estimates and segments are estimated on the whole data
    pool and shown there only; here a start would estimate on the filtered rows and replace the stored estimate."""
    for e in data.get("items") or []:
        for kind in ("maxdiff", "cbc", "acbc", "paired", "cva"):
            part = e.get(kind)
            if part:
                part["hb"], part["lc"] = None, None
                part["filtered"] = True


@bp.route("/<sid:survey_id>/results/classes", methods=["POST"])
@login_required
@autosave.aware
def results_bins(survey_id):
    """The bounds of the classes a number is split into on the results page (app/groupvars.py, role "Bearbeiten"):
    saved per survey, so the page shows the same classes again; empty: the bounds from the data (or the age bands)."""
    from app import groupvars, units

    survey = get_survey(survey_id)
    item_id = request.form.get("item", type=int)
    item = next((i for i in groupvars.number_items(survey) if i.id == item_id), None)
    if item is None:
        abort(404)
    bounds = groupvars.clean_bounds(request.form.get("grenzen", ""), known=groupvars.saved_bounds(survey, item))
    for note in units.entry_notes():  # "1,000" in German: read as 1, said after saving (Mathe-Audit 2026-10-06, Z-4)
        flash(note, "warning")
    step = undo.begin(survey)
    bins = {k: v for k, v in ((survey.data_pool or {}).get("bins") or {}).items() if k != str(item.id)}
    if bounds:
        bins[str(item.id)] = bounds
    survey.data_pool = {**(survey.data_pool or {}), "bins": bins}
    step.done(undo.both("Klassen geändert", "Classes changed"), page="quality", node="pool", merge=autosave.merge("bins"))
    db.session.commit()
    return redirect(url_for("surveys.results", survey_id=survey.id, gruppen=f"klassen:{item.id}") + "#groups")


PROGRESS_COOKIE = "results_progress"


@bp.route("/<sid:survey_id>/results/progress")
@login_required
def results_progress(survey_id):
    """How far the results page is that this browser is waiting for: {"share": 0..1, "text": ...}, or {}
    before the computation has started and after it has ended."""
    from flask import jsonify

    from app import progress

    get_survey(survey_id)
    response = jsonify(progress.read(request.cookies.get(PROGRESS_COOKIE), current_user.id))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/<sid:survey_id>/methods", methods=["GET", "POST"])
@login_required
@heavy.limited
def methods(survey_id):
    """Method section: the facts of the data pool of the results page, the study plan, Word (German, English) and,
    on POST, the AI rewording it (facts only, no answers)."""
    from app import ai
    from app import methods as m

    survey = get_survey(survey_id)
    # the data pool of the results page (UI/UX audit 2026-10-05, V29): one methods section, the same numbers as there
    f = m.facts(survey, sources=stored_pool(survey)["sources"])
    text, lang, length = None, request.form.get("lang", "de"), request.values.get("length", "medium")
    if request.method == "POST":
        try:
            text = m.ai_text(f, lang, length)
        except ai.AIError as e:
            flash(de_en("{e} Unten steht ein deutscher Entwurf ohne KI.", "{e} Below is a German draft without AI.", e=e), "error")
            text, lang = m.draft(f), "draft"
    return render_template("surveys/methods.html", survey=survey, f=f, text=text, lang=lang, length=length,
                           lengths=m.lengths(), to_local=to_local,
                           choice_en=[m._choice_en(c) + "." for c in f.get("choice") or []],
                           reliability_sources=m.reliability_sources(f),
                           choice_sources=m.choice_sources(f.get("choice") or []) + m.task_sources(f.get("tasks") or []))
