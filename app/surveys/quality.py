"""Tab Qualitätskontrolle: quality rules and the data pool."""
from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import autosave, report_jobs, undo
from app.extensions import db
from app.lang import de_en
from app.surveys.base import bp, get_survey, pool_counts, pool_frozen, pool_frozen_note, stored_pool


def _kept_page(survey, **_):
    """Whether the page can answer in the request from REPORT_JOB_THRESHOLD participations on: a save (POST) always,
    the page when what it computes from the participations is kept (quality.kept: in this process or on the server).
    Otherwise the first computation runs in a report process and the page waits for it, as the results do
    (performance audit 2026-10-18, PERF rest: 4.6 s at 5000, 8.4 s at 10 000 participations in the request)."""
    from app import quality as patterns

    return request.method != "GET" or patterns.kept(survey, stored_pool(survey))


@bp.route("/<sid:survey_id>/quality", methods=["GET", "POST"])
@login_required
@report_jobs.background(ready=_kept_page)
@autosave.aware
def quality(survey_id):
    """Quality assurance: which participations the results rest on. The data pool (sources, also unfinished
    ones) and the speeder threshold (Leiner 2019) are stored with the survey: the results use both and leave
    speeders out, every export uses the threshold, and the export page offers the pool first (whether
    speeders are left out of a file is chosen there). POST (role "Bearbeiten") saves them."""
    from app import export

    survey = get_survey(survey_id)
    if request.method == "POST":
        form = request.form
        step = undo.begin(survey)  # only in a draft (app/undo.py); in the field nothing is taken back
        # the pool is set on the page "Datenerhebung" (update_pool); a form that still sends it is heard
        if "source" in form and not _save_pool(survey, form):
            return redirect(url_for("surveys.quality", survey_id=survey.id))
        if "speeder" in form:  # no slider before the first completed participation
            survey.speeder_pct = export.speeder_pct(form.get("speeder"))
        if form.get("speeder_rule") in ("duration", "rsi") and survey.page_timing:  # the index needs page times
            survey.speeder_rule = form.get("speeder_rule")
        if "speeder_rsi" in form:
            survey.speeder_rsi = export.rsi_tenths(form.get("speeder_rsi"))
        from app import quality as patterns

        survey.quality_rules = patterns.rules_from_form(form)
        what = autosave.what()
        step.done(undo.both("Qualitätssicherung: {what} geändert", "Quality assurance: {what} changed", what=what) if what
                  else undo.both("Qualitätssicherung geändert", "Quality assurance changed"), page="quality",
                  merge=autosave.merge("quality"))
        db.session.commit()
        rule = (export.page_rule_text(survey) if export.speeder_rule(survey) == "rsi"
                else de_en("{pct} % der Median-Zeit", "{pct}% of the median time", pct=survey.speeder_pct))
        flash(de_en("Qualitätssicherung gespeichert: Speeder-Schwelle ({rule}) und Markierung gelten für die Ergebnisse; "
                    "Schwelle und Markierung auch für jeden Export.",
                    "Quality assurance saved: speeder threshold ({rule}) and flags apply to the results; threshold and "
                    "flags also to every export.", rule=rule), "success")
        return redirect(url_for("surveys.quality", survey_id=survey.id))
    pool = stored_pool(survey)
    from app import quality as patterns

    page = patterns.page(survey, pool)
    return render_template("surveys/quality.html", survey=survey, pool=pool, by_source=pool_counts(survey), basis=stored_pool(survey),
                           weighting_on=__import__("app.weights", fromlist=["active"]).active(survey),
                           pool_frozen=pool_frozen(survey), pool_frozen_note=pool_frozen_note(),
                           # kept per process while nothing changed, else read once for all three (Q-Rest)
                           speed=page["speed"], tempo_pages=page["tempo_pages"], patterns=page["patterns"],
                           # blocks loaded at once: page times measured in the browser (app/blockload.py, decision 4a)
                           block_times=__import__("app.blockload", fromlist=["quality_counts"]).quality_counts(
                               survey, pool["sources"]) if getattr(survey, "block_load", False) else None,
                           geo_compare=__import__("app.geoip", fromlist=["comparison"]).comparison(survey, pool["sources"], pool["all"]),
                           location_compare=__import__("app.items.geolocation", fromlist=["comparison"]).comparison(
                               survey, pool["sources"], pool["all"]),
                           # a diary study: the entries sent later, with the server's time of arrival (M1)
                           late=__import__("app.diary", fromlist=["late_entries"]).late_entries(survey, pool["sources"])
                           if survey.sampling is not None and survey.can(current_user, "editor") else None)


def _save_pool(survey, form):
    """Store the data pool a form sends (sources, also unfinished ones); False, with a message and nothing
    stored, when a public dashboard link fixes it and the form would change it."""
    from app import export

    sent = {"sources": [x for x in export.SOURCES if x in form.getlist("source")] or ["real"], "all": form.get("all") == "1"}
    if pool_frozen(survey):
        # what a public dashboard link shows is the admins' decision (boards.may_change): the page sends no
        # pool then (its boxes are disabled), and one that would change it is refused, with nothing saved
        old = stored_pool(survey)
        if "source" in form and sent != {"sources": old["sources"], "all": old["all"]}:
            flash(pool_frozen_note(), "error")
            return False
        return True
    survey.data_pool = {**(survey.data_pool or {}), **sent}  # the bounds of classes ("bins") stay
    return True


@bp.route("/<sid:survey_id>/pool", methods=["POST"])
@login_required
@autosave.aware
def update_pool(survey_id):
    """The data pool, in the box "Grundlage der Auswertung" of Qualitätssicherung and Ergebnisse (role "Bearbeiten"):
    which participations quality assurance and results rest on, and the first choice on the export page."""
    survey = get_survey(survey_id)
    pages = (url_for("surveys.quality", survey_id=survey.id), url_for("surveys.results", survey_id=survey.id))
    back = (request.form.get("back") if request.form.get("back") in pages else pages[0]) + "#pool"
    step = undo.begin(survey)
    if not _save_pool(survey, request.form):
        return redirect(back)
    step.done(undo.both("Datenpool geändert", "Data pool changed"), page="quality", node="pool", merge=autosave.merge("pool"))
    db.session.commit()
    flash(de_en("Datenpool gespeichert: Gilt für Qualitätssicherung und Ergebnisse und ist die Vorauswahl beim Export.",
                "Data pool saved: applies to quality assurance and results and is the preselection for the export."), "success")
    return redirect(back)
