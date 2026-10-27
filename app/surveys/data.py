"""Tab Daten: the data page, survey file export, publications, Zenodo, dummy data, CSV/R bodies."""
from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app import heavy, report_jobs
from app.extensions import db
from app.lang import de_en
from app.surveys.base import bp, data_pool, get_survey, pool_counts


def _copyable(survey_id):
    """The survey for its survey file: from "Bearbeiten" on, as duplicating (access.may_copy_out); "Ansehen" only for
    an example survey seen as one. Until 2026-10-18 every role (audit 2026-10-18, AUTHZ-05)."""
    from app import access
    from app.models import Survey

    found = db.session.get(Survey, survey_id)
    return get_survey(survey_id, need="viewer" if found is not None and access.showcase_only(current_user, found)
                      else "editor")


@bp.route("/<sid:survey_id>/export/survey.json")
@login_required
@heavy.limited
def export_file(survey_id):
    """The survey as a file (app/transfer.py): questionnaire and settings, nothing collected and nobody named.
    Whoever may duplicate it (_copyable)."""
    from flask import Response as FlaskResponse

    from app import transfer

    survey = _copyable(survey_id)
    wanted = request.args.getlist("files")  # the form sends "0" and, ticked, "1"; a plain link: with files
    try:
        body = transfer.dumps(survey, with_files=not wanted or "1" in wanted)
    except transfer.ExportTooLarge as exc:
        flash(str(exc), "warning")
        return redirect(url_for("surveys.export_zip", survey_id=survey.id, files=wanted))
    return FlaskResponse(body, mimetype="application/json; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{transfer.file_name(survey)}"', "Cache-Control": "no-store"})


@bp.route("/<sid:survey_id>/export/survey.zip")
@login_required
@heavy.limited
def export_zip(survey_id):
    """The same as a ZIP: befragung.json and the images as files of their own (app/survey_files.py)."""
    from flask import send_file

    from app import survey_files

    survey = _copyable(survey_id)
    wanted = request.args.getlist("files")
    try:
        body = survey_files.write([survey], with_files=not wanted or "1" in wanted)
    except survey_files.transfer.ExportTooLarge as exc:
        abort(413, description=str(exc))
    response = send_file(body, mimetype="application/zip",
                         as_attachment=True, download_name=survey_files.file_name([survey]))
    response.headers["Cache-Control"] = "no-store"
    return response


# --- export and end texts ----------------------------------------------------------


@bp.route("/<sid:survey_id>/data")
@login_required
def data(survey_id):
    return _data_page(get_survey(survey_id))


def _data_page(survey, erase_found=None, new_token=None):
    """The Export page. erase_found: what a request for deletion found, waiting to be confirmed (erase_request);
    new_token: a read token just made, shown this once (data_access.create)."""
    from app import data_access
    from app import contact as contact_mod
    from app.models import Response as R

    tally = survey.tally()
    counts = {k: sum(n for (source, _, status), n in tally.items() if source == "real" and status == v) for k, v in
              (("completed", R.COMPLETED), ("active", R.ACTIVE), ("screenout", R.SCREENOUT), ("quotafull", R.QUOTAFULL),
               ("excluded", R.EXCLUDED))}
    counts["declined"] = survey.declined_count or 0
    by_source = pool_counts(survey)
    from app import zenodo_auth

    zen = {t: {"available": zenodo_auth.available(t), "connected": zenodo_auth.connection(current_user, t) is not None}
           for t in ("sandbox", "zenodo")}
    from app import snapshots, timeline
    from app.export import used_constructs

    from app import export, standard_vars

    from app import erasure

    pool = data_pool(survey)
    from app import waves

    from app.live.base import is_slide

    # a live vote with presentation slides offers them with the presenter's pen notes as PDF (app/live/ink.py)
    live_slides = survey.live is not None and any(is_slide(i) for b in survey.blocks for i in b.items)
    return render_template("surveys/data.html", survey=survey, live_slides=live_slides,
                           waves_view=waves.view(current_user, survey) if waves.of(survey) else None,
                           waves_report=waves.page_report(current_user, survey), waves_sources=waves.SOURCES, citations=__import__("app.citations", fromlist=["listing"]), counts=counts, by_source=by_source, contact=contact_mod,
                           erased=erasure.summary(survey), erase_found=erase_found, erase_ways=erasure.ways(),
                           comment_shots=__import__("app.comments", fromlist=["any_shots"]).any_shots(survey),
                           keeping=__import__("app.retention", fromlist=["view"]).view(survey),
                           pool=pool, open_cols=sorted(export.open_columns(survey)),
                           scale_cols=[c for found in export.scale_defs(survey).values() for c, *_ in found],
                           standard_vars=standard_vars.available(survey),
                           data_tokens=data_access.listing(survey, current_user), new_token=new_token,
                           data_urls=data_access.addresses(survey) if data_access.offered(survey) else {},
                           token_days=(data_access.DEFAULT_DAYS, data_access.MAX_DAYS),
                           zen=zen, used_constructs=used_constructs(survey), used_linked=used_constructs(survey, linked=True), tl=timeline.build(survey),
                           prereg=__import__("app.prereg", fromlist=["listing"]).listing(survey),
                           prereg_compare=_prereg_compare(survey),
                           prereg_stamps=__import__("app.prereg", fromlist=["stamp_time"]).stamp_times(survey),
                           seal_info=__import__("app.seal", fromlist=["summary"]).summary(survey) if survey.seal else None,
                           snapshots=snapshots.listing(survey), snapshot_extras={
                               n: snapshots.extra_files(survey, n) for n, _, _ in snapshots.listing(survey)},
                           snapshot_files={n: {f for f in snapshots.FILES if snapshots.path(survey, n, f)}
                                           for n, _, _ in snapshots.listing(survey)})


def _prereg_compare(survey):
    """(name, count, [sentence]) of the newest pre-registration against the survey as it runs now, or None (no
    pre-registration of the current version, or one without a survey file); E10 B."""
    from app import prereg

    newest = next(iter(prereg.listing(survey)), None)
    if newest is None or newest[1] != (survey.qversion or 1):
        return None
    found = prereg.compare(survey, newest[0])
    return (newest[0], *found) if found else None


@bp.route("/<sid:survey_id>/publications", methods=["POST"])
@login_required
def publications(survey_id):
    """A publication (article or data set, by DOI) for the survey: it counts for every toolbox construct in it and is
    listed on the constructs' pages (app/citations.py)."""
    from app import citations

    # "Bearbeiten" enters and removes them, as the panel shows (the POST default would ask for "admin"; Abgleich
    # 2026-10-15, side finding on roles)
    survey = get_survey(survey_id, need="editor")
    back = redirect(url_for("surveys.data", survey_id=survey.id) + "#publications")
    doi = citations.doi_of(request.form.get("doi"))
    if doi is None:
        flash(de_en("Die DOI sieht nicht richtig aus (z. B. 10.1037/0022-3514.84.4.822).",
                    "The DOI does not look right (e.g. 10.1037/0022-3514.84.4.822)."), "error")
        return back
    if request.form.get("action") == "remove":
        citations.remove_for_survey(survey, doi)
        db.session.commit()
        flash(de_en("Veröffentlichung entfernt.", "Publication removed."), "success")
        return back
    try:
        n = citations.add_for_survey(survey, doi, current_user)
    except ValueError as e:
        flash(str(e), "error")
        return back
    db.session.commit()
    if n == 1:
        flash(de_en("Eingetragen: Steht jetzt bei dem Konstrukt dieser Befragung unter „Verwendet in“.",
                    "Added: now listed with the construct of this survey under “Used in”."), "success")
    else:
        flash(de_en("Eingetragen: Steht jetzt bei den {n} Konstrukten dieser Befragung unter „Verwendet in“.",
                    "Added: now listed with the {n} constructs of this survey under “Used in”.", n=n), "success")
    return back


@bp.route("/<sid:survey_id>/zenodo", methods=["POST"])
@login_required
def zenodo_upload(survey_id):
    from app import zenodo

    survey = get_survey(survey_id)
    back = redirect(url_for("surveys.data", survey_id=survey.id) + "#zenodo")
    consent = survey.consent_item
    if consent is not None and not consent.config.get("open_data") and not request.form.get("confirm_no_open_data"):
        flash(de_en("Laut Einwilligung werden die Daten nicht veröffentlicht. Bitte ausdrücklich bestätigen, dass eine "
                    "Veröffentlichung trotzdem zulässig ist.",
                    "According to the consent, the data are not published. Please confirm explicitly that publishing "
                    "is allowed nonetheless."), "error")
        return back
    from app import zenodo_auth

    target = request.form.get("target", "zenodo")
    if request.form.get("action") != "refresh":
        try:
            from app import asset_rights

            zenodo.license_id(survey)  # before access_token, which may refresh OAuth through an external POST
            asset_rights.require_public(survey)
        except (zenodo.ZenodoError, asset_rights.RightsError) as e:
            flash(str(e), "error")
            return back
    # only through "Mit Zenodo verbinden" (OAuth): no operator token, no personal tokens (Philipp, 2026-10-04)
    token = zenodo_auth.access_token(current_user, target) if zenodo_auth.available(target) else None
    if not token:
        flash(de_en("Bitte zuerst unter „Konto › Verbindungen“ „Mit Zenodo verbinden“.", "Please first “Connect with Zenodo” under “Account › Connections”."), "error")
        return back
    try:
        client = zenodo.Client(token, target)
        if request.form.get("action") == "refresh":  # published on Zenodo by the researchers? (Askurious never publishes)
            survey.zenodo = zenodo.refresh(survey, client)
            flash(de_en("Auf Zenodo veröffentlicht. DOI: {doi}", "Published on Zenodo. DOI: {doi}", doi=survey.zenodo.get('doi'))
                  if survey.zenodo["state"] == "published"
                  else de_en("Auf Zenodo noch ein Entwurf.", "Still a draft on Zenodo."), "success")
        else:
            survey.zenodo = zenodo.upload(survey, client, open_answers=bool(request.form.get("include_open_answers")))
            flash(de_en("Entwurf auf Zenodo angelegt bzw. aktualisiert. Bitte dort prüfen und dort veröffentlichen; erst dann "
                        "wird die DOI vergeben.",
                        "Draft created or updated on Zenodo. Please check and publish it there; only then is the DOI "
                        "assigned."), "success")
    except zenodo.ZenodoError as e:
        flash(str(e), "error")
        return back
    db.session.commit()
    return back


@bp.route("/<sid:survey_id>/dummy", methods=["POST"])
@login_required
def make_dummy(survey_id):
    """Create dummy participations. The page asks for them in small portions (Accept: application/json) and
    shows the progress in between; without JavaScript the form creates all of them in one request."""
    from flask import jsonify

    from app import design, dummy
    from app.models import Response as R

    survey = get_survey(survey_id)
    portions = request.accept_mimetypes.best == "application/json"
    errors = design.blocking(design.check(survey))
    if errors:
        # the error that stops them, not "the questionnaire" (UI/UX audit 2026-10-09, X60: it may be the research plan)
        message = de_en("Dummy-Daten brauchen eine Startprüfung ohne Fehler: {error}",
                        "Dummy data need a pre-launch check without errors: {error}", error=errors[0].message)
        if len(errors) > 1:
            message += de_en(" (und {n} weitere)", " (and {n} more)", n=len(errors) - 1)
        if portions:
            return jsonify(error=message), 400
        flash(message, "error")
        return redirect(url_for("surveys.field", survey_id=survey.id) + "#testing")
    n = max(1, min(request.form.get("n", 50, type=int) or 50, 500))
    # numbered on from the dummies already there, so the portions of one run do not all start at 1
    there = db.session.scalar(db.select(db.func.count()).select_from(R)
                              .where(R.survey_id == survey.id, R.source == "dummy"))
    first = there if portions else 0
    # at most DUMMY_MAX in all: a loop of requests filled the shared database without end (audit 2026-10-22, D-05)
    most = int(current_app.config.get("DUMMY_MAX") or 0)
    if most:
        n = min(n, most - there)
        if n <= 0:
            message = de_en("Diese Befragung hat schon {n} Dummy-Teilnahmen, mehr gehen nicht. Löschen Sie sie, um neue "
                            "zu erzeugen.", "This survey already has {n} dummy participations; no more are possible. "
                            "Delete them to create new ones.", n=there)
            if portions:
                return jsonify(error=message), 400
            flash(message, "error")
            return redirect(url_for("surveys.field", survey_id=survey.id) + "#testing")
    # a diary study: dummy persons with entries over their days (app/dummy.py), at most 50 at a time
    created = dummy.generate_diary(survey, min(n, 50), first=first) if survey.sampling is not None \
        else dummy.generate(survey, n, first=first)
    if portions:
        return jsonify(created=created)
    flash(de_en("{n} Dummy-Teilnahmen erzeugt. Im Export sind sie an der Spalte „source“ erkennbar.",
                "{n} dummy participations created. In the export they can be recognised by the column “source”.", n=created),
          "success")
    return redirect(url_for("surveys.field", survey_id=survey.id) + "#testing")


@bp.route("/<sid:survey_id>/dummy/delete", methods=["POST"])
@login_required
def delete_dummy(survey_id):
    from app.models import Response as R

    survey = get_survey(survey_id)
    kind = request.form.get("source")
    if kind in ("dummy", "test"):
        from app.models import keep_floor_before_bulk_delete

        keep_floor_before_bulk_delete(R, R.survey_id == survey.id, R.source == kind)  # no id given twice (B1)
        db.session.execute(db.delete(R).where(R.survey_id == survey.id, R.source == kind))
        from app.models import Enrolment

        # the persons of a diary study go with their entries (dummy data or test persons); their invitation codes
        # let go of them, and SQLite gives none of their ids to a new person (audit 2026-10-23, S-01)
        from app import diary

        persons = db.select(Enrolment.id).where(Enrolment.survey_id == survey.id, Enrolment.source == kind)
        diary.detach_invites(survey.id, persons)
        keep_floor_before_bulk_delete(Enrolment, Enrolment.survey_id == survey.id, Enrolment.source == kind)
        db.session.execute(db.delete(Enrolment).where(Enrolment.survey_id == survey.id, Enrolment.source == kind))
        db.session.commit()
        from app import results_store

        results_store.forget(survey)  # kept results may rest on what was just deleted: gone with it
        flash(de_en("Dummy-Daten gelöscht.", "Dummy data deleted.") if kind == "dummy"
              else de_en("Testdurchläufe gelöscht.", "Test runs deleted."), "success")
    return redirect(url_for("surveys.field", survey_id=survey.id) + "#testing")


def with_scales():
    """Does the export ask for the scale scores (tick on the export page, app/scales.py)?"""
    return request.args.get("scales") == "1"


def with_standard():
    """Does the export ask for the KonsortSWD standard variables x_... (tick on the export page, app/standard_vars.py)?"""
    return request.args.get("standard") == "1"


def csv_body(survey, kind, pool, sep, scales, standard=False):
    """The data set ("data") or the codebook ("codebook") as CSV for a data pool: the button of the export page and
    a read token (app/data_access.py) get the same file."""
    from app import export

    sources = pool["sources"]
    if kind == "data":  # the speeder median comes from the chosen data pool, the threshold from the survey
        return export.to_csv(survey, include_incomplete=pool["all"], separator=sep, sources=sources,
                             speed_sources=sources, drop_speeders=pool["drop"], scales=scales, standard=standard)
    return export.codebook_csv(survey, separator=sep, speed_sources=sources, drop_speeders=pool["drop"], scales=scales,
                               standard=standard)


def rds_body(survey, pool, scales, standard=False):
    """The data as an R file (.rds) for a data pool: the button of the export page and a read token."""
    from app import export

    return export.r_rds(survey, include_incomplete=pool["all"], sources=pool["sources"],
                        speed_sources=pool["sources"], drop_speeders=pool["drop"], scales=scales, standard=standard)


def _codebook_ready(survey, kind=None, **_):
    """The codebook reads no participation worth a process of its own (performance audit 2026-10-18, PERF-03: 3.1 s
    there instead of 0.14 s in the request)."""
    return kind == "codebook"


@bp.route("/<sid:survey_id>/export/<kind>.csv")
@login_required
@report_jobs.background(ready=_codebook_ready)
@heavy.limited
def export_csv(survey_id, kind):
    from flask import Response as FlaskResponse

    from app import export

    survey = get_survey(survey_id)
    if kind not in ("data", "codebook"):
        abort(404)
    sep = "semicolon" if request.args.get("sep") == "semicolon" else "comma"
    pool = data_pool(survey)
    if kind == "data":
        from flask import send_file

        if request.args.get("answers") == "labels":  # the answers as their labels (user test 2026-10-27, 12)
            body = export.labelled_csv_file(survey, include_incomplete=pool["all"], separator=sep, sources=pool["sources"],
                                            speed_sources=pool["sources"], drop_speeders=pool["drop"], scales=with_scales(),
                                            standard=with_standard())
        else:
            body = export.csv_file(survey, include_incomplete=pool["all"], separator=sep, sources=pool["sources"],
                                   speed_sources=pool["sources"], drop_speeders=pool["drop"], scales=with_scales(),
                                   standard=with_standard())
        # "text/csv": the response adds "; charset=utf-8" itself, given twice it stood twice (audit 2026-10-18, INJ-6)
        response = send_file(body, mimetype="text/csv", conditional=False, download_name=export.filename(survey, kind))
        response.headers["Content-Disposition"] = f'attachment; filename="{export.filename(survey, kind)}"'
        response.headers["Cache-Control"] = "no-store"
        response.call_on_close(body.close)
        return response
    body = csv_body(survey, kind, pool, sep, with_scales(), with_standard())
    return FlaskResponse(body, mimetype="text/csv; charset=utf-8", headers={
        "Content-Disposition": f'attachment; filename="{export.filename(survey, kind)}"',
        "Cache-Control": "no-store",
    })


def _counts_basis(survey, pool):
    """The data pool the counts are kept under, and its fingerprint (app/results_store.py): the same for both choices
    of the marked ones, which only decide what "included" says."""
    from app import results_store

    basis = {"sources": list(pool["sources"]), "all": bool(pool["all"]), "drop": True, "speeder": pool["speeder"],
             "speed_real": True}
    return basis, results_store.fingerprint(survey, basis)


def _kept_counts(survey, pool):
    from app import results_store

    basis, key = _counts_basis(survey, pool)
    kept = results_store.load(survey, key, basis, name="counts")
    return kept[0] if kept is not None else None


def _export_counts(survey, pool):
    """{total, marked, included} of the export page's selection. Kept with the results (results_store, "counts"), under
    the fingerprint of the data and of what marks: the page asks on every load and every change of the form, and
    each count read every participation and built the quality context anew, 1.9 s at 10 000 (performance audit
    2026-10-18, PERF-01). Computed only when something changed, in one of the places for heavy evaluations."""
    from app import export, results_store

    basis, key = _counts_basis(survey, pool)
    kept = results_store.load(survey, key, basis, name="counts")
    if kept is not None:
        counts = kept[0]
    else:
        if not heavy.hold():
            abort(heavy.busy())
        responses = export._responses(survey, pool["all"], pool["sources"])
        kept_rows = export.long_pool(survey, pool["all"], pool["sources"], drop_speeders=True, responses=responses)
        counts = {"total": len(responses), "marked": len(responses) - len(kept_rows)}
        results_store.save(survey, key, counts, basis, name="counts", basis=key)
    return {"total": counts["total"], "marked": counts["marked"],
            "included": counts["total"] - counts["marked"] if pool["drop"] else counts["total"]}


def _counts_ready(survey, **_):
    """Counts kept already: answered in the request at once (PERF-01), else from the threshold in a process of its
    own, which the page polls (app.js)."""
    return _kept_counts(survey, data_pool(survey)) is not None


@bp.route("/<sid:survey_id>/data-counts")
@login_required
@report_jobs.background(ready=_counts_ready)
def export_counts(survey_id):
    from flask import jsonify

    survey = get_survey(survey_id)
    response = jsonify(_export_counts(survey, data_pool(survey)))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/<sid:survey_id>/data-selection.json")
@login_required
@report_jobs.background(ready=_counts_ready)
def data_selection(survey_id):
    from flask import jsonify
    from app.models import utcnow

    survey = get_survey(survey_id)
    pool = data_pool(survey)
    response = jsonify({"format": 1, "survey": survey.uid, "qversion": survey.qversion,
                        "generated_at": utcnow().isoformat(), "sources": pool["sources"],
                        "include_incomplete": pool["all"], "exclude_marked": pool["drop"],
                        "speeder_threshold": pool["speeder"], "weighted": False,
                        "counts": _export_counts(survey, pool),
                        "scope": "answer files; long formats may have multiple rows per participation"})
    response.headers["Content-Disposition"] = 'attachment; filename="data-selection.json"'
    response.headers["Cache-Control"] = "no-store"
    return response
